#!/usr/bin/env python3
import argparse
import json
import logging
import math
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np
import onnxruntime as ort
import paho.mqtt.client as mqtt
import psutil
import requests
import yaml

MODEL_URL = "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo11n-pose.onnx"
MODEL_PATH = Path("/config/models/yolo11n-pose.onnx")
DEFAULT_CONFIG = {
    "cameras": [],
    "global": {
        "inference_fps": 3,
        "alert_fps": 5,
        "image_size": 416,
        "person_confidence": 0.45,
        "motion_threshold": 8.0,
        "motion_pixels": 250,
        "confirmation_seconds": 8,
        "cooldown_seconds": 60,
        "fall_score_threshold": 0.72,
        "cpu_threads": 2,
        "snapshot_on_event": True,
        "max_people": 2,
        "track_timeout_seconds": 1.5,
    },
}

LOG = logging.getLogger("fall_ai")
KPTS = {
    "nose": 0, "left_eye": 1, "right_eye": 2, "left_ear": 3, "right_ear": 4,
    "left_shoulder": 5, "right_shoulder": 6, "left_elbow": 7, "right_elbow": 8,
    "left_wrist": 9, "right_wrist": 10, "left_hip": 11, "right_hip": 12,
    "left_knee": 13, "right_knee": 14, "left_ankle": 15, "right_ankle": 16,
}

def deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out

def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(v)))

def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))

def download_model():
    if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 5_000_000:
        return
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MODEL_PATH.with_suffix(".tmp")
    LOG.info("Downloading YOLO pose model...")
    with requests.get(MODEL_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", "0"))
        got = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        LOG.info("Model %.0f%%", got * 100 / total)
    tmp.replace(MODEL_PATH)
    LOG.info("Model ready: %.1f MB", MODEL_PATH.stat().st_size / 1048576)

class PoseModel:
    def __init__(self, image_size=416, threads=2):
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, int(threads))
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(MODEL_PATH),
            sess_options=so,
            providers=["CPUExecutionProvider"],
        )
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        self.model_h = int(shape[2]) if isinstance(shape[2], int) else image_size
        self.model_w = int(shape[3]) if isinstance(shape[3], int) else image_size
        self.image_size = image_size
        LOG.info("YOLO input=%s providers=%s", shape, self.session.get_providers())

    def infer(self, frame, conf=0.45, max_people=2):
        h0, w0 = frame.shape[:2]
        scale = min(self.model_w / w0, self.model_h / h0)
        nw, nh = int(round(w0 * scale)), int(round(h0 * scale))
        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((self.model_h, self.model_w, 3), 114, dtype=np.uint8)
        dx = (self.model_w - nw) // 2
        dy = (self.model_h - nh) // 2
        canvas[dy:dy+nh, dx:dx+nw] = resized
        img = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))[None, ...]

        raw = self.session.run(None, {self.input_name: img})[0]
        arr = np.asarray(raw)
        if arr.ndim == 3:
            arr = arr[0]
        # YOLO11 pose ONNX: [56, 8400] or [8400, 56]
        if arr.shape[0] < arr.shape[1]:
            arr = arr.T
        # columns: cx,cy,w,h,cls, 17*(x,y,v)
        if arr.shape[1] < 56:
            LOG.warning("Unexpected pose output shape: %s", arr.shape)
            return []
        detections = []
        for row in arr:
            score = float(row[4])
            if score < conf:
                continue
            cx, cy, bw, bh = map(float, row[:4])
            x1 = (cx - bw / 2 - dx) / scale
            y1 = (cy - bh / 2 - dy) / scale
            x2 = (cx + bw / 2 - dx) / scale
            y2 = (cy + bh / 2 - dy) / scale
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w0 - 1, x2), min(h0 - 1, y2)
            if x2 <= x1 or y2 <= y1:
                continue
            kp = np.zeros((17, 3), dtype=np.float32)
            vals = row[5:56].reshape(17, 3)
            for i, (x, y, v) in enumerate(vals):
                kp[i] = [(x - dx) / scale, (y - dy) / scale, v]
            detections.append({
                "score": score,
                "box": (x1, y1, x2, y2),
                "keypoints": kp,
            })
        # Keep highest-confidence persons; model only has person class.
        detections.sort(key=lambda d: d["score"], reverse=True)
        return detections[:max_people]

class TrackState:
    def __init__(self):
        self.history = deque(maxlen=30)
        self.last_seen = 0.0
        self.fall_score = 0.0
        self.candidate_since = None
        self.alerted = False

    def update(self, pose, now):
        self.last_seen = now
        self.history.append((now, pose))
        if len(self.history) > 1:
            return self._score()
        return 0.0

    def _score(self):
        if len(self.history) < 5:
            return 0.0
        t0, p0 = self.history[-min(15, len(self.history))]
        t1, p1 = self.history[-1]
        dt = max(0.1, t1 - t0)
        score = fall_features(self.history)
        return score

def center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

def pose_features(pose):
    box = pose["box"]
    x1, y1, x2, y2 = box
    bw, bh = max(1, x2-x1), max(1, y2-y1)
    kp = pose["keypoints"]
    valid = kp[:,2] > 0.25

    def pt(i):
        return kp[i, :2] if kp[i,2] > 0.25 else None

    ls, rs = pt(5), pt(6)
    lh, rh = pt(11), pt(12)
    la, ra = pt(15), pt(16)
    nose = pt(0)

    shoulder = np.mean([p for p in (ls, rs) if p is not None], axis=0) if any(p is not None for p in (ls,rs)) else None
    hip = np.mean([p for p in (lh, rh) if p is not None], axis=0) if any(p is not None for p in (lh,rh)) else None
    ankle = np.mean([p for p in (la, ra) if p is not None], axis=0) if any(p is not None for p in (la,ra)) else None

    if shoulder is not None and hip is not None:
        body_angle = abs(math.degrees(math.atan2(float(hip[1]-shoulder[1]), float(hip[0]-shoulder[0]))))
        body_angle = min(body_angle, 180-body_angle)
    else:
        body_angle = None

    if shoulder is not None and ankle is not None:
        torso_to_ankle = float(np.linalg.norm(ankle - shoulder)) / max(bh, 1)
    else:
        torso_to_ankle = None

    return {
        "cx": (x1+x2)/2, "cy": (y1+y2)/2,
        "bw": bw, "bh": bh, "aspect": bw/bh,
        "body_angle": body_angle,
        "hip_y": float(hip[1]) if hip is not None else None,
        "shoulder_y": float(shoulder[1]) if shoulder is not None else None,
        "ankle_y": float(ankle[1]) if ankle is not None else None,
        "head_y": float(nose[1]) if nose is not None else None,
        "visibility": float(np.mean(kp[:,2][valid])) if np.any(valid) else 0.0,
    }

def is_lying_state(pose):
    f = pose_features(pose)
    horizontal_box = f["aspect"] > 1.15
    horizontal_body = f["body_angle"] is not None and f["body_angle"] < 55
    return bool(horizontal_box or horizontal_body)

def fall_features(history):
    feats = [pose_features(p) for _, p in history]
    cur = feats[-1]
    old = feats[max(0, len(feats)-min(12, len(feats)))]
    recent = feats[-5:]

    score = 0.0

    # Horizontal/lying shape.
    if cur["aspect"] > 1.35:
        score += 0.26
    elif cur["aspect"] > 1.15:
        score += 0.14

    # Body angle close to horizontal.
    if cur["body_angle"] is not None:
        if cur["body_angle"] < 35:
            score += 0.24
        elif cur["body_angle"] < 55:
            score += 0.10

    # Fast downward movement of center/hip relative to box height.
    dy = cur["cy"] - old["cy"]
    norm_dy = dy / max(old["bh"], 1)
    if norm_dy > 0.65:
        score += 0.24
    elif norm_dy > 0.35:
        score += 0.13

    # Sudden increase in width/height ratio.
    ratio_change = cur["aspect"] / max(0.2, old["aspect"])
    if ratio_change > 1.7:
        score += 0.16
    elif ratio_change > 1.35:
        score += 0.08

    # If current state is stable and horizontal for several frames, increase.
    horiz = sum(1 for f in recent if f["aspect"] > 1.15 or (f["body_angle"] is not None and f["body_angle"] < 55))
    if horiz >= 4:
        score += 0.16

    # Penalize obvious standing/upright state.
    if cur["aspect"] < 0.75 and (cur["body_angle"] is None or cur["body_angle"] > 60):
        score -= 0.35

    return clamp(score)

class CameraWorker:
    def __init__(self, cfg, global_cfg, model, mqtt_client, root_status):
        self.cfg = cfg
        self.g = global_cfg
        self.model = model
        self.mqtt = mqtt_client
        self.root_status = root_status
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.rtsp = cfg["rtsp"]
        self.running = True
        self.track = TrackState()
        self.last_frame = None
        self.prev_small = None
        self.last_infer = 0.0
        self.last_motion = 0.0
        self.alert_until = 0.0
        self.last_event = None
        self.thread = threading.Thread(target=self.run, name=f"cam-{self.id}", daemon=True)

    def topic(self, suffix):
        return f"{self.g.get('mqtt_prefix','fall_ai')}/{self.id}/{suffix}"

    def publish(self, suffix, payload, retain=False):
        try:
            self.mqtt.publish(self.topic(suffix), payload, qos=0, retain=retain)
        except Exception as e:
            LOG.warning("[%s] MQTT publish failed: %s", self.id, e)

    def discovery(self):
        base = self.g.get("mqtt_prefix","fall_ai")
        dev = {
            "identifiers": [f"fall_ai_{self.id}"],
            "name": f"Fall AI - {self.name}",
            "manufacturer": "Thuy",
            "model": "YOLO Pose Fall AI",
        }
        cfg = {
            "name": "Fall detected",
            "unique_id": f"fall_ai_{self.id}_fall",
            "state_topic": self.topic("fall"),
            "payload_on": "ON",
            "payload_off": "OFF",
            "device": dev,
            "availability_topic": self.topic("status"),
            "payload_available": "online",
            "payload_not_available": "offline",
            "icon": "mdi:human-falling",
        }
        self.mqtt.publish(
            f"homeassistant/binary_sensor/{self.id}/fall/config",
            json.dumps(cfg), qos=0, retain=True
        )
        for key, name, unit in [
            ("confidence", "Fall confidence", "%"),
            ("cpu", "CPU", "%"),
        ]:
            scfg = {
                "name": name,
                "unique_id": f"fall_ai_{self.id}_{key}",
                "state_topic": self.topic(key),
                "device": dev,
                "availability_topic": self.topic("status"),
                "unit_of_measurement": unit,
                "state_class": "measurement",
            }
            self.mqtt.publish(
                f"homeassistant/sensor/{self.id}/{key}/config",
                json.dumps(scfg), qos=0, retain=True
            )

    def start(self):
        self.discovery()
        self.publish("status", "online", retain=True)
        self.publish("fall", "OFF", retain=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def is_motion(self, frame):
        small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5,5), 0)
        if self.prev_small is None:
            self.prev_small = gray
            return True
        diff = cv2.absdiff(self.prev_small, gray)
        self.prev_small = gray
        changed = int(np.sum(diff > self.g["motion_threshold"]))
        return changed >= int(self.g["motion_pixels"])

    def maybe_snapshot(self, frame, event):
        if not self.g.get("snapshot_on_event", True):
            return None
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path("/media/fall_ai") / f"{self.id}_{ts}.jpg"
        try:
            cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
            self.publish("snapshot", str(path))
            return str(path)
        except Exception as e:
            LOG.warning("[%s] snapshot failed: %s", self.id, e)
            return None

    def run(self):
        cap = None
        reconnect = 1
        while self.running:
            try:
                if cap is None or not cap.isOpened():
                    LOG.info("[%s] Connecting RTSP", self.id)
                    cap = cv2.VideoCapture(self.rtsp, cv2.CAP_FFMPEG)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    if not cap.isOpened():
                        LOG.warning("[%s] RTSP connect failed; retry in %ss", self.id, reconnect)
                        time.sleep(reconnect)
                        reconnect = min(30, reconnect * 2)
                        continue
                    reconnect = 1

                ok, frame = cap.read()
                if not ok or frame is None:
                    LOG.warning("[%s] RTSP frame read failed", self.id)
                    cap.release()
                    cap = None
                    time.sleep(1)
                    continue

                self.last_frame = frame
                self.root_status[self.id] = {
                    "name": self.name,
                    "last_frame": time.time(),
                    "last_motion": self.last_motion,
                    "last_event": self.last_event,
                    "fall_score": self.track.fall_score,
                }

                motion = self.is_motion(frame)
                now = time.time()
                if motion:
                    self.last_motion = now

                # After motion, keep inference alive for a short tail so a person
                # who becomes still immediately after falling is not missed.
                active = (now - self.last_motion) < 12.0
                if not active:
                    time.sleep(0.02)
                    continue

                fps = self.g["alert_fps"] if self.track.fall_score > 0.55 else self.g["inference_fps"]
                interval = 1.0 / max(1, fps)
                if now - self.last_infer < interval:
                    continue
                self.last_infer = now

                # Cool down after an alert, but still keep a lightweight status.
                if now < self.alert_until:
                    continue

                cpu = psutil.cpu_percent(interval=None)
                self.publish("cpu", f"{cpu:.1f}")

                dets = self.model.infer(
                    frame,
                    conf=self.g["person_confidence"],
                    max_people=int(self.g["max_people"]),
                )

                if not dets:
                    # If no person, slowly forget candidate.
                    self.track.fall_score *= 0.9
                    continue

                pose = dets[0]
                score = self.track.update(pose, now)
                self.track.fall_score = score
                self.publish("confidence", f"{score*100:.1f}")

                # Two-stage confirmation:
                # 1) detect a likely fall transition (impact score)
                # 2) keep a candidate alive while the person remains in a
                #    lying/horizontal state for the configured confirmation time.
                lying = is_lying_state(pose)
                if score >= self.g["fall_score_threshold"]:
                    if self.track.candidate_since is None:
                        self.track.candidate_since = now
                elif self.track.candidate_since is not None:
                    if lying and (now - self.track.candidate_since) <= (self.g["confirmation_seconds"] + 6):
                        pass
                    elif now - self.track.candidate_since > (self.g["confirmation_seconds"] + 6):
                        self.track.candidate_since = None

                if self.track.candidate_since is not None:
                    age = now - self.track.candidate_since
                    if age >= self.g["confirmation_seconds"] and lying:
                        self.confirm_fall(frame, max(score, self.g["fall_score_threshold"]), now)

            except Exception as e:
                LOG.exception("[%s] worker error: %s", self.id, e)
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                time.sleep(2)

        if cap is not None:
            cap.release()

    def confirm_fall(self, frame, score, now):
        if self.track.alerted and now < self.alert_until:
            return
        self.track.alerted = True
        self.alert_until = now + self.g["cooldown_seconds"]
        self.track.candidate_since = None

        event = {
            "event": "fall",
            "camera_id": self.id,
            "camera_name": self.name,
            "confidence": round(score, 3),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        snapshot = self.maybe_snapshot(frame, event)
        if snapshot:
            event["snapshot"] = snapshot

        self.last_event = event
        self.publish("fall", "ON", retain=True)
        self.publish("event", json.dumps(event, ensure_ascii=False), retain=False)
        LOG.warning("FALL CONFIRMED camera=%s score=%.2f", self.name, score)

        # Auto-clear binary sensor after 15 seconds; event itself remains in MQTT.
        def clear():
            time.sleep(15)
            self.publish("fall", "OFF", retain=True)
            self.track.alerted = False
        threading.Thread(target=clear, daemon=True).start()

class WebServer(threading.Thread):
    def __init__(self, status):
        super().__init__(daemon=True)
        self.status = status

    def run(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        status = self.status
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return
            def do_GET(self):
                if self.path in ("/", "/api/status"):
                    body = json.dumps({
                        "service": "fall_ai",
                        "cpu": psutil.cpu_percent(interval=None),
                        "cameras": status,
                    }, ensure_ascii=False, default=str).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()
        try:
            HTTPServer(("0.0.0.0", 8099), Handler).serve_forever()
        except Exception as e:
            LOG.warning("Web server stopped: %s", e)

def get_mqtt_from_env():
    # Values are populated by run.sh through bashio in the future-compatible way.
    host = os.getenv("MQTT_HOST", "core-mosquitto")
    port = int(os.getenv("MQTT_PORT", "1883"))
    user = os.getenv("MQTT_USER", "")
    password = os.getenv("MQTT_PASSWORD", "")
    return host, port, user, password

def mqtt_connect():
    host, port, user, password = get_mqtt_from_env()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fall_ai")
    if user:
        client.username_pw_set(user, password)
    LOG.info("Connecting MQTT %s:%s", host, port)
    client.connect(host, port, keepalive=30)
    client.loop_start()
    return client

def load_config(path):
    if not Path(path).exists():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        example = Path("/app/config.example.yaml")
        if example.exists():
            Path(path).write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            Path(path).write_text(
                yaml.safe_dump(DEFAULT_CONFIG, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        raise SystemExit(
            f"Created {path}. Add RTSP cameras, save, then restart Fall AI."
        )
    with open(path, "r", encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    cfg = deep_merge(DEFAULT_CONFIG, user)
    valid = []
    for c in cfg.get("cameras", []):
        if c.get("enabled", True) and c.get("id") and c.get("rtsp"):
            valid.append(c)
    cfg["cameras"] = valid
    return cfg

def lower_process_priority():
    try:
        os.nice(10)
    except Exception:
        pass
    # Never try to claim the full host CPU. ONNX Runtime is already limited
    # to cpu_threads; this additionally lowers scheduling priority.
    try:
        if hasattr(os, "sched_setaffinity"):
            cpus = list(os.sched_getaffinity(0))
            if len(cpus) > 4:
                os.sched_setaffinity(0, set(cpus[:4]))
    except Exception:
        pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/config/config.yaml")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    lower_process_priority()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cfg = load_config(args.config)
    if not cfg["cameras"]:
        LOG.error("No enabled cameras in %s", args.config)
        raise SystemExit(2)

    download_model()
    g = cfg["global"]
    model = PoseModel(g["image_size"], g["cpu_threads"])
    mqttc = mqtt_connect()
    status = {}
    WebServer(status).start()

    workers = []
    for cam_cfg in cfg["cameras"]:
        w = CameraWorker(cam_cfg, g, model, mqttc, status)
        w.start()
        workers.append(w)

    LOG.info("Fall AI started with %d camera(s)", len(workers))
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        pass
    finally:
        for w in workers:
            w.stop()
        mqttc.loop_stop()
        mqttc.disconnect()

if __name__ == "__main__":
    main()
