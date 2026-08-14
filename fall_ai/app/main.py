#!/usr/bin/env python3
import argparse
import logging
import os
import time
import threading
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
    cfg.setdefault("global", {})
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
        # onnx is needed only for validation; import here so the addon fails clearly.
        try:
            check_opset(self.path)
        except ImportError:
            LOG.warning("Python package 'onnx' is not installed; skipping explicit opset check")
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, int(threads))
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        LOG.info("Loading YOLO11n-pose model...")
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
        return self.session.run(None, {self.input: img})


class HAMotion:
    """Reads a Home Assistant binary_sensor through the Supervisor API."""
    def __init__(self, entity):
        self.entity = entity
        self.state = None
        self.last_check = 0.0
        self.lock = threading.Lock()

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

    def should_infer(self, frame):
        # Preferred: Home Assistant's camera motion entity.
        entity = self.cam.get("motion_entity")
        if entity:
            on = self.ha_motion.is_on()
            if on != self.last_motion_log:
                LOG.info("%s: HA motion %s=%s", self.cam.get("id"), entity, on)
                self.last_motion_log = on
            return on

        # Fallback: local image motion.
        motion, self.last_motion_frame = image_motion(
            self.last_motion_frame,
            frame,
            self.g.get("motion_threshold", 8.0),
            self.g.get("motion_pixels", 250),
        )
        return motion

    def run(self):
        cid = self.cam.get("id", "camera")
        rtsp = self.cam.get("rtsp")
        if not rtsp:
            LOG.error("%s: missing rtsp", cid)
            return

        cap = None
        interval = 1.0 / max(0.1, float(self.g.get("inference_fps", 3)))

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

            if not self.should_infer(frame):
                time.sleep(0.05)
                continue

            now = time.monotonic()
            if now - self.last_infer < interval:
                continue

            self.last_infer = now
            try:
                outputs = self.model.infer(frame)
                # The current build validates the model and performs pose inference.
                # Fall scoring can be refined after verifying the camera stream.
                _ = outputs
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

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    g = cfg.get("global") or {}

    LOG.info("Fall AI v0.1.2 starting")
    LOG.info("Configured cameras: %d", len(cfg["cameras"]))
    LOG.info(
        "CPU threads: %s, inference FPS: %s",
        g.get("cpu_threads", 2),
        g.get("inference_fps", 3),
    )

    model = PoseModel(g.get("image_size", 416), g.get("cpu_threads", 2))

    workers = []
    for cam in cfg["cameras"]:
        if not isinstance(cam, dict):
            continue
        if cam.get("enabled", True):
            w = CameraWorker(cam, g, model)
            workers.append(w)
            w.start()
            LOG.info(
                "Camera %s enabled; motion_entity=%s",
                cam.get("id"),
                cam.get("motion_entity"),
            )

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
