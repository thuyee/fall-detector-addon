#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
import math
import os
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import requests
import yaml
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn
from ultralytics import YOLO

LOG = logging.getLogger("fall_ai")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
HA_BASE = "http://supervisor/core/api"
CONFIG = {}
STATUS = {}
STATUS_LOCK = threading.Lock()
STOP = threading.Event()

app = FastAPI(title="Fall AI")


def ha_headers():
    return {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json",
    }


def ha_state(entity_id: str) -> str | None:
    if not SUPERVISOR_TOKEN or not entity_id:
        return None
    try:
        r = requests.get(
            f"{HA_BASE}/states/{entity_id}",
            headers=ha_headers(),
            timeout=3,
        )
        if r.ok:
            return r.json().get("state")
        LOG.warning("HA state request failed %s: HTTP %s", entity_id, r.status_code)
    except Exception as e:
        LOG.warning("HA state request error for %s: %s", entity_id, e)
    return None


def fire_ha_event(event_name: str, data: dict):
    if not SUPERVISOR_TOKEN:
        LOG.error("SUPERVISOR_TOKEN is missing; cannot fire HA event")
        return
    try:
        r = requests.post(
            f"{HA_BASE}/events/{event_name}",
            headers=ha_headers(),
            json=data,
            timeout=5,
        )
        if not r.ok:
            LOG.error("HA event failed: HTTP %s %s", r.status_code, r.text[:300])
    except Exception as e:
        LOG.error("HA event error: %s", e)


def save_snapshot(frame, camera_id):
    if not CONFIG["global"].get("save_snapshot", True):
        return None
    outdir = Path("/media/fall_ai")
    outdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = outdir / f"fall_{camera_id}_{ts}.jpg"
    try:
        cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        return f"/media/fall_ai/{path.name}"
    except Exception as e:
        LOG.warning("Snapshot failed: %s", e)
        return None


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def point(kpts, idx):
    if idx >= len(kpts):
        return None
    x, y, c = kpts[idx]
    if c < 0.25:
        return None
    return float(x), float(y)


def distance(a, b):
    if not a or not b:
        return 0.0
    return math.hypot(a[0] - b[0], a[1] - b[1])


def pose_features(kpts, box):
    # COCO pose indices:
    # 0 nose, 5/6 shoulders, 11/12 hips, 15/16 ankles.
    ls, rs = point(kpts, 5), point(kpts, 6)
    lh, rh = point(kpts, 11), point(kpts, 12)
    nose = point(kpts, 0)

    valid = [point(kpts, i) for i in range(len(kpts))]
    valid = [p for p in valid if p]
    if len(valid) < 5:
        return None

    x1, y1, x2, y2 = box
    bw = max(1.0, x2 - x1)
    bh = max(1.0, y2 - y1)
    aspect = bw / bh

    shoulder = None
    hip = None
    if ls and rs:
        shoulder = ((ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2)
    if lh and rh:
        hip = ((lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2)

    torso_angle = None
    if shoulder and hip:
        dx = hip[0] - shoulder[0]
        dy = hip[1] - shoulder[1]
        torso_angle = abs(math.degrees(math.atan2(dx, -dy)))
        torso_angle = min(torso_angle, 180 - torso_angle)

    center = (
        (box[0] + box[2]) / 2,
        (box[1] + box[3]) / 2,
    )
    if hip:
        center = hip

    # Horizontal body cue: wide bbox + near-horizontal torso.
    horizontal = clamp((aspect - 1.10) / 1.20)
    if torso_angle is not None:
        horizontal = max(horizontal, clamp((torso_angle - 35.0) / 55.0))

    return {
        "center": center,
        "height": bh,
        "width": bw,
        "aspect": aspect,
        "torso_angle": torso_angle,
        "horizontal": horizontal,
        "nose": nose,
        "hip": hip,
    }


class CameraWorker:
    def __init__(self, cfg, global_cfg, model):
        self.cfg = cfg
        self.g = global_cfg
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.rtsp = cfg["rtsp"]
        self.motion_entity = cfg.get("motion_entity", "")
        self.model = model
        self.last_motion = 0.0
        self.last_alert = 0.0
        self.history = deque(maxlen=30)
        self.running = False
        self.cap = None
        self.last_frame = None
        self.last_infer = 0.0
        self.infer_period = 1.0 / max(1, float(self.g.get("inference_fps", 3)))
        self.motion_poll = 0.0

    def set_status(self, **kwargs):
        with STATUS_LOCK:
            STATUS.setdefault(self.id, {})
            STATUS[self.id].update(kwargs)

    def motion_active(self):
        now = time.monotonic()
        if self.motion_entity and now - self.motion_poll > 1.0:
            state = ha_state(self.motion_entity)
            self._motion_state = state
            self.motion_poll = now
            if state == "on":
                self.last_motion = now
        if getattr(self, "_motion_state", None) == "on":
            return True
        return (now - self.last_motion) <= float(self.g.get("post_motion_seconds", 10))

    def open(self):
        if self.cap and self.cap.isOpened():
            return True
        LOG.info("[%s] Opening RTSP", self.id)
        self.cap = cv2.VideoCapture(self.rtsp, cv2.CAP_FFMPEG)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.cap.isOpened():
            LOG.error("[%s] Unable to open RTSP", self.id)
            self.set_status(state="rtsp_error")
            return False
        self.set_status(state="streaming")
        return True

    def close(self):
        if self.cap:
            self.cap.release()
        self.cap = None
        self.set_status(state="idle")

    def score_fall(self, feat):
        now = time.monotonic()
        self.history.append((now, feat))
        if len(self.history) < 5:
            return 0.0

        recent = list(self.history)
        old_t, old_f = recent[max(0, len(recent) - 10)]
        dt = max(0.1, now - old_t)

        dy = feat["center"][1] - old_f["center"][1]
        drop = clamp(dy / max(20.0, feat["height"] * 0.75))

        speed = clamp(abs(dy) / dt / 220.0)
        horizontal = feat["horizontal"]

        # Body becoming short in image height after a downward transition.
        low_body = clamp((1.0 - feat["height"] / 230.0))

        score = (
            0.38 * horizontal
            + 0.30 * drop
            + 0.17 * speed
            + 0.15 * low_body
        )

        # Strong temporal cue: previous pose was more upright.
        upright_before = old_f["horizontal"] < 0.35
        if upright_before and drop > 0.25 and horizontal > 0.45:
            score += 0.12

        return clamp(score)

    def infer(self, frame):
        h, w = frame.shape[:2]
        results = self.model.predict(
            source=frame,
            imgsz=int(self.g.get("frame_width", 640)),
            conf=float(self.g.get("person_confidence", 0.45)),
            classes=[0],
            verbose=False,
            max_det=int(self.g.get("max_people", 2)),
        )

        best = 0.0
        best_feat = None
        best_box = None
        if not results:
            return 0.0, None, None

        result = results[0]
        if result.boxes is None or len(result.boxes) == 0:
            return 0.0, None, None

        boxes = result.boxes.xyxy.cpu().numpy()
        if result.keypoints is None:
            return 0.0, None, None
        kps = result.keypoints.data.cpu().numpy()

        for i in range(min(len(boxes), len(kps))):
            feat = pose_features(kps[i], boxes[i])
            if not feat:
                continue
            score = self.score_fall(feat)
            if score > best:
                best, best_feat, best_box = score, feat, boxes[i]

        return best, best_feat, best_box

    def emit_fall(self, frame, score, feat):
        now = time.monotonic()
        if now - self.last_alert < float(self.g.get("cooldown_seconds", 60)):
            return

        self.last_alert = now
        snapshot = save_snapshot(frame, self.id)
        payload = {
            "camera_id": self.id,
            "camera_name": self.name,
            "confidence": round(float(score), 3),
            "score": round(float(score), 3),
            "snapshot": snapshot,
            "timestamp": datetime.now().isoformat(),
        }
        LOG.warning(
            "[%s] FALL CONFIRMED score=%.2f snapshot=%s",
            self.id, score, snapshot
        )
        fire_ha_event(
            CONFIG.get("home_assistant", {}).get("event_name", "fall_ai_event"),
            payload,
        )
        self.set_status(
            state="fall_confirmed",
            last_fall=datetime.now().isoformat(),
            last_score=round(float(score), 3),
            snapshot=snapshot,
        )

    def run(self):
        self.running = True
        self.set_status(state="idle", name=self.name)
        while not STOP.is_set():
            try:
                if not self.motion_active():
                    self.close()
                    time.sleep(0.25)
                    continue

                if not self.open():
                    time.sleep(2)
                    continue

                ok, frame = self.cap.read()
                if not ok or frame is None:
                    LOG.warning("[%s] RTSP frame read failed", self.id)
                    self.close()
                    time.sleep(1)
                    continue

                self.last_frame = frame
                now = time.monotonic()
                if now - self.last_infer < self.infer_period:
                    continue
                self.last_infer = now

                # Downscale before inference to keep CPU load bounded.
                fw = int(self.g.get("frame_width", 640))
                fh = int(self.g.get("frame_height", 360))
                frame_small = cv2.resize(frame, (fw, fh))

                score, feat, box = self.infer(frame_small)
                threshold = float(self.g.get("fall_score_threshold", 0.72))
                self.set_status(
                    state="inference",
                    last_score=round(float(score), 3),
                    motion=True,
                )

                if score >= threshold and feat:
                    # Require the cue to remain present briefly, reducing one-frame false positives.
                    confirm = float(self.g.get("confirmation_seconds", 2.5))
                    start = time.monotonic()
                    confirmed = True
                    while time.monotonic() - start < confirm and not STOP.is_set():
                        time.sleep(0.15)
                        if not self.motion_active():
                            # We still allow post-motion observation.
                            pass
                    # Re-score from the retained temporal history.
                    if self.history and self.history[-1][1]["horizontal"] < 0.35:
                        confirmed = False
                    if confirmed:
                        self.emit_fall(frame_small, score, feat)

            except Exception:
                LOG.exception("[%s] worker error", self.id)
                self.close()
                time.sleep(2)

        self.close()


@app.get("/")
def root():
    return {"service": "fall_ai", "version": "0.1.1"}


@app.get("/api/status")
def api_status():
    with STATUS_LOCK:
        return JSONResponse(STATUS)


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main():
    global CONFIG
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    CONFIG = load_config(args.config)
    global_cfg = CONFIG.setdefault("global", {})
    cameras = CONFIG.get("cameras", [])

    threads = int(global_cfg.get("cpu_threads", 2))
    os.environ["OMP_NUM_THREADS"] = str(threads)
    os.environ["MKL_NUM_THREADS"] = str(threads)

    model_name = global_cfg.get("model", "yolo11n-pose.pt")
    LOG.info("Loading pose model: %s", model_name)
    model = YOLO(model_name)

    workers = []
    for c in cameras:
        if not c.get("enabled", True):
            continue
        required = ["id", "rtsp"]
        missing = [k for k in required if not c.get(k)]
        if missing:
            LOG.error("Camera skipped; missing: %s", missing)
            continue
        worker = CameraWorker(c, global_cfg, model)
        workers.append(worker)
        threading.Thread(
            target=worker.run,
            name=f"fall-{worker.id}",
            daemon=True,
        ).start()

    LOG.info("Fall AI started with %d camera(s)", len(workers))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8099,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
