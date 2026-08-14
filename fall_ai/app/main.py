#!/usr/bin/env python3
import argparse
import json
import logging
import math
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
import requests
import yaml

LOG = logging.getLogger("fall_ai")
SUPERVISOR = "http://supervisor"
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise ValueError("Config root must be a YAML mapping")
    cams = cfg.get("cameras")
    if cams is None:
        cams = []
    if not isinstance(cams, list):
        raise ValueError("cameras must be a list")
    cfg["cameras"] = cams
    cfg["global"] = cfg.get("global") or {}
    return cfg


def check_opset(path):
    import onnx
    model = onnx.load(path, load_external_data=False)
    versions = [x.version for x in model.opset_import if x.domain in ("", "ai.onnx")]
    if not versions:
        raise RuntimeError("Model has no ai.onnx opset")
    opset = max(versions)
    LOG.info("YOLO ONNX opset=%s", opset)
    if opset > 21:
        raise RuntimeError(
            f"Wrong YOLO model: Opset {opset}. This addon requires Opset <= 21."
        )


class PoseModel:
    def __init__(self, size=416, threads=2):
        self.size = int(size)
        self.path = "/config/models/yolo11n-pose.onnx"
        if not os.path.exists(self.path):
            raise FileNotFoundError(self.path)
        check_opset(self.path)
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, int(threads))
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            self.path,
            sess_options=so,
            providers=["CPUExecutionProvider"],
        )
        self.input = self.session.get_inputs()[0].name
        LOG.info("ONNX model loaded: %s", self.path)
        LOG.info("ONNX providers: %s", self.session.get_providers())

    def infer(self, frame):
        img = cv2.resize(frame, (self.size, self.size))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))[None, ...]
        outputs = self.session.run(None, {self.input: img})
        return outputs[0]


class HAMotion:
    def __init__(self, entity):
        self.entity = entity
        self.state = None
        self.last_check = 0.0

    def update(self):
        if not self.entity or not TOKEN:
            return None
        now = time.monotonic()
        if now - self.last_check < 0.5:
            return self.state
        self.last_check = now
        try:
            r = requests.get(
                f"{SUPERVISOR}/core/api/states/{self.entity}",
                headers={"Authorization": f"Bearer {TOKEN}"},
                timeout=2,
            )
            r.raise_for_status()
            self.state = r.json().get("state")
        except Exception as exc:
            LOG.warning("HA motion read failed for %s: %s", self.entity, exc)
        return self.state

    def is_on(self):
        return self.update() == "on"


def decode_pose(output, image_size, conf_threshold, max_people):
    arr = np.asarray(output)
    if arr.ndim == 3:
        arr = arr[0]
    if arr.shape[0] < arr.shape[1]:
        arr = arr.T

    if arr.shape[1] < 56:
        return []

    people = []
    for row in arr:
        conf = float(row[4])
        if conf < conf_threshold:
            continue

        x, y, w, h = map(float, row[:4])
        k = np.asarray(row[5:56], dtype=np.float32).reshape(17, 3)

        # Coordinates are in the exported model's input space.
        k[:, 0] = np.clip(k[:, 0] / image_size, 0, 1)
        k[:, 1] = np.clip(k[:, 1] / image_size, 0, 1)

        bw = max(1e-6, w / image_size)
        bh = max(1e-6, h / image_size)
        people.append({
            "confidence": conf,
            "bbox": (x / image_size, y / image_size, bw, bh),
            "keypoints": k,
        })

    people.sort(key=lambda p: p["confidence"], reverse=True)
    return people[:max_people]


def pose_fall_score(person):
    """
    Heuristic score:
      - horizontal body/bounding box
      - torso approaching horizontal
      - shoulders/hips/ankles spread horizontally
      - sufficient visible keypoints

    Returns 0..1. It is deliberately conservative and is combined with
    temporal transition logic in CameraWorker.
    """
    k = person["keypoints"]
    vis = k[:, 2]
    good = vis > 0.35
    if int(np.count_nonzero(good)) < 6:
        return 0.0

    # COCO keypoints: 5/6 shoulders, 11/12 hips, 15/16 ankles.
    def mid(a, b):
        if good[a] and good[b]:
            return (k[a, :2] + k[b, :2]) / 2.0
        return None

    shoulder = mid(5, 6)
    hip = mid(11, 12)

    _, _, bw, bh = person["bbox"]
    aspect = bw / max(0.01, bh)
    aspect_score = np.clip((aspect - 1.05) / 1.5, 0, 1)

    torso_score = 0.0
    if shoulder is not None and hip is not None:
        dx = float(hip[0] - shoulder[0])
        dy = float(hip[1] - shoulder[1])
        angle_from_horizontal = abs(math.degrees(math.atan2(dy, dx)))
        angle_from_horizontal = min(angle_from_horizontal, 180 - angle_from_horizontal)
        torso_score = np.clip(1.0 - angle_from_horizontal / 75.0, 0, 1)

    visible_x = k[good, 0]
    visible_y = k[good, 1]
    pose_w = float(np.max(visible_x) - np.min(visible_x))
    pose_h = float(np.max(visible_y) - np.min(visible_y))
    spread_score = np.clip((pose_w / max(0.02, pose_h) - 1.1) / 2.0, 0, 1)

    return float(
        0.40 * aspect_score
        + 0.35 * torso_score
        + 0.25 * spread_score
    )


def image_motion(prev, frame, threshold, pixels):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (320, 180))
    if prev is None:
        return False, gray
    diff = cv2.absdiff(prev, gray)
    _, mask = cv2.threshold(diff, float(threshold), 255, cv2.THRESH_BINARY)
    count = int(np.count_nonzero(mask))
    return count >= int(pixels), gray


class CameraWorker(threading.Thread):
    def __init__(self, cam, g, model):
        super().__init__(daemon=True)
        self.cam = cam
        self.g = g
        self.model = model
        self.stop_event = threading.Event()
        self.last_motion_frame = None
        self.last_infer = 0.0
        self.ha_motion = HAMotion(cam.get("motion_entity", ""))
        self.last_motion_log = None
        self.last_scores = {}
        self.fall_candidate_since = {}
        self.last_alert = 0.0
        self.last_frame = None
        self.last_upright = {}

    def should_infer(self, frame):
        entity = self.cam.get("motion_entity")
        if entity:
            on = self.ha_motion.is_on()
            if on != self.last_motion_log:
                LOG.info(
                    "%s: HA motion %s=%s",
                    self.cam.get("id"), entity, on
                )
                self.last_motion_log = on
            return on

        motion, self.last_motion_frame = image_motion(
            self.last_motion_frame,
            frame,
            self.g.get("motion_threshold", 8.0),
            self.g.get("motion_pixels", 250),
        )
        return motion

    def send_event(self, score, snapshot):
        now = time.time()
        cooldown = float(self.g.get("cooldown_seconds", 60))
        if now - self.last_alert < cooldown:
            return

        cid = self.cam.get("id", "camera")
        data = {
            "camera_id": cid,
            "camera_name": self.cam.get("name", cid),
            "score": round(float(score), 3),
            "snapshot": snapshot or "",
            "timestamp": now,
        }

        if snapshot:
            try:
                Path(snapshot).parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(snapshot, self.last_frame)
            except Exception:
                LOG.exception("%s: snapshot failed", cid)

        try:
            r = requests.post(
                f"{SUPERVISOR}/core/api/events/fall_ai_event",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json=data,
                timeout=3,
            )
            r.raise_for_status()
            LOG.warning("%s: FALL CONFIRMED score=%.2f", cid, score)
            self.last_alert = now
        except Exception as exc:
            LOG.error("%s: failed to send HA event: %s", cid, exc)

    def process_people(self, people):
        now = time.monotonic()
        threshold = float(self.g.get("fall_score_threshold", 0.72))
        confirm = float(self.g.get("confirmation_seconds", 8))
        cid = self.cam.get("id", "camera")

        for idx, person in enumerate(people):
            score = pose_fall_score(person)
            prev = self.last_scores.get(idx, 0.0)

            # A strong jump toward horizontal is treated as the fall transition.
            transition = score >= threshold and (prev < threshold or score - prev > 0.20)

            if score < threshold:
                self.fall_candidate_since.pop(idx, None)
                self.last_upright[idx] = score < 0.45
            elif transition or idx in self.fall_candidate_since:
                self.fall_candidate_since.setdefault(idx, now)

                if now - self.fall_candidate_since[idx] >= confirm:
                    snap = ""
                    if bool(self.g.get("snapshot_on_event", True)):
                        stamp = time.strftime("%Y%m%d-%H%M%S")
                        snap = f"/config/snapshots/{cid}_{stamp}.jpg"
                    self.send_event(score, snap)
                    self.fall_candidate_since.pop(idx, None)

            self.last_scores[idx] = score

        # Keep state bounded.
        if len(self.last_scores) > 8:
            self.last_scores = dict(list(self.last_scores.items())[-4:])

    def run(self):
        cid = self.cam.get("id", "camera")
        rtsp = self.cam.get("rtsp")
        if not rtsp:
            LOG.error("%s: missing rtsp", cid)
            return

        interval = 1.0 / max(0.1, float(self.g.get("inference_fps", 3)))
        cap = None

        while not self.stop_event.is_set():
            if cap is None or not cap.isOpened():
                LOG.info("%s: connecting RTSP...", cid)
                cap = cv2.VideoCapture(rtsp, cv2.CAP_FFMPEG)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if not cap.isOpened():
                    LOG.error("%s: cannot open RTSP; retry in 5s", cid)
                    if cap:
                        cap.release()
                    cap = None
                    time.sleep(5)
                    continue
                LOG.info("%s: RTSP connected", cid)

            ok, frame = cap.read()
            if not ok:
                LOG.warning("%s: RTSP frame read failed; reconnecting", cid)
                cap.release()
                cap = None
                time.sleep(2)
                continue

            self.last_frame = frame

            if not self.should_infer(frame):
                time.sleep(0.05)
                continue

            now = time.monotonic()
            if now - self.last_infer < interval:
                continue
            self.last_infer = now

            try:
                output = self.model.infer(frame)
                people = decode_pose(
                    output,
                    self.model.size,
                    float(self.g.get("person_confidence", 0.45)),
                    int(self.g.get("max_people", 2)),
                )
                self.process_people(people)
            except Exception:
                LOG.exception("%s: inference failed", cid)

        if cap:
            cap.release()

    def stop(self):
        self.stop_event.set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/config/config.yaml")
    args = ap.parse_args()

    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    g = cfg["global"]

    LOG.info("Fall AI v0.1.4 starting")
    LOG.info("Configured cameras: %d", len(cfg["cameras"]))
    LOG.info(
        "CPU threads=%s inference_fps=%s image_size=%s",
        g.get("cpu_threads", 2),
        g.get("inference_fps", 3),
        g.get("image_size", 416),
    )

    if not cfg["cameras"]:
        LOG.warning("No cameras configured. Add cameras to /config/config.yaml.")
        while True:
            time.sleep(60)

    model = PoseModel(
        g.get("image_size", 416),
        g.get("cpu_threads", 2),
    )

    workers = []
    for cam in cfg["cameras"]:
        if not isinstance(cam, dict) or not cam.get("enabled", True):
            continue
        worker = CameraWorker(cam, g, model)
        workers.append(worker)
        worker.start()
        LOG.info(
            "Camera %s enabled; motion_entity=%s",
            cam.get("id"),
            cam.get("motion_entity"),
        )

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
