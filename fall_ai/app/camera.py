"""Per-camera worker thread: RTSP capture, motion gating, pose inference,
temporal fall confirmation, snapshot + notification dispatch.
"""
import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from model import decode_pose, pose_fall_score
from tracker import Tracker

LOG = logging.getLogger("fall_ai.camera")


class HAMotion:
    """Cached read of a HA binary_sensor motion entity's state."""

    def __init__(self, ha_client, entity):
        self.ha = ha_client
        self.entity = entity
        self.state = None
        self.last_check = 0.0

    def update(self):
        if not self.entity:
            return None
        now = time.monotonic()
        if now - self.last_check < 0.5:
            return self.state
        self.last_check = now
        self.state = self.ha.get_state(self.entity)
        return self.state

    def is_on(self):
        return self.update() == "on"


def image_motion(prev_gray, frame, threshold, pixels):
    """Fallback motion detector used only when no motion_entity is configured."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (320, 180))
    if prev_gray is None:
        return False, gray
    diff = cv2.absdiff(prev_gray, gray)
    _, mask = cv2.threshold(diff, float(threshold), 255, cv2.THRESH_BINARY)
    count = int(np.count_nonzero(mask))
    return count >= int(pixels), gray


class CameraWorker(threading.Thread):
    def __init__(self, cam, g, model, ha_client, notifier=None, mqtt_pub=None,
                 snapshot_dir="/config/snapshots"):
        super().__init__(daemon=True, name=f"cam-{cam.get('id', '?')}")
        self.cam = cam
        self.g = g
        self.model = model
        self.ha = ha_client
        self.notifier = notifier
        self.mqtt_pub = mqtt_pub
        self.snapshot_dir = Path(snapshot_dir)

        self.stop_event = threading.Event()
        self.ha_motion = HAMotion(ha_client, cam.get("motion_entity", ""))
        self.uses_motion_entity = bool(cam.get("motion_entity"))

        self.last_motion_gray = None
        self.last_motion_log = None
        self.last_infer = 0.0
        self.last_frame = None
        self.last_alert = 0.0

        self.tracker = Tracker(
            timeout=float(g.get("track_timeout_seconds", 1.5)),
            iou_threshold=float(g.get("track_iou_threshold", 0.3)),
        )
        # Per track_id temporal state.
        self.last_scores = {}
        self.fall_candidate_since = {}

    # -- motion gating -----------------------------------------------
    def _motion_gate_cheap(self):
        """Returns True/False if motion state can be determined WITHOUT
        decoding a frame (i.e. via the HA entity). Returns None if a
        decoded frame is required (image-diff fallback)."""
        if self.uses_motion_entity:
            on = self.ha_motion.is_on()
            if on != self.last_motion_log:
                LOG.info("%s: HA motion %s=%s", self.cam.get("id"), self.cam.get("motion_entity"), on)
                self.last_motion_log = on
            return bool(on)
        return None

    # -- fall confirmation --------------------------------------------
    def process_people(self, people):
        now = time.monotonic()
        threshold = float(self.g.get("fall_score_threshold", 0.72))
        confirm = float(self.g.get("confirmation_seconds", 8))
        cid = self.cam.get("id", "camera")

        track_ids = self.tracker.update(people, now)
        active_ids = set(track_ids)

        for person, tid in zip(people, track_ids):
            score = pose_fall_score(person)
            prev = self.last_scores.get(tid, 0.0)

            # A strong jump toward horizontal is treated as the fall transition.
            transition = score >= threshold and (prev < threshold or score - prev > 0.20)

            if score < threshold:
                self.fall_candidate_since.pop(tid, None)
            elif transition or tid in self.fall_candidate_since:
                self.fall_candidate_since.setdefault(tid, now)

                if now - self.fall_candidate_since[tid] >= confirm:
                    self.confirm_fall(score)
                    self.fall_candidate_since.pop(tid, None)

            self.last_scores[tid] = score

        # Drop temporal state for tracks that are no longer active.
        for stale in list(self.last_scores.keys()):
            if stale not in active_ids:
                self.last_scores.pop(stale, None)
                self.fall_candidate_since.pop(stale, None)

    # -- alert dispatch --------------------------------------------------
    def confirm_fall(self, score):
        now = time.time()
        cooldown = float(self.g.get("cooldown_seconds", 60))
        if now - self.last_alert < cooldown:
            return
        self.last_alert = now

        cid = self.cam.get("id", "camera")
        cam_name = self.cam.get("name", cid)

        snapshot_filename = ""
        if bool(self.g.get("snapshot_on_event", True)) and self.last_frame is not None:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            snapshot_filename = f"{cid}_{stamp}.jpg"
            try:
                self.snapshot_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(self.snapshot_dir / snapshot_filename), self.last_frame)
            except Exception:
                LOG.exception("%s: snapshot failed", cid)
                snapshot_filename = ""

        LOG.warning("%s: FALL CONFIRMED score=%.2f", cid, score)

        # 1) HA event, for anyone who wants to build their own automation too.
        self.ha.fire_event("fall_ai_event", {
            "camera_id": cid,
            "camera_name": cam_name,
            "score": round(float(score), 3),
            "snapshot": snapshot_filename,
            "timestamp": now,
        })

        # 2) Direct push notification (mobile app + Zalo), with the snapshot attached.
        if self.notifier:
            try:
                self.notifier.notify_fall(cam_name, score, snapshot_filename, now)
            except Exception:
                LOG.exception("%s: notification dispatch failed", cid)

        # 3) Optional MQTT state.
        if self.mqtt_pub:
            try:
                url = None
                if snapshot_filename and self.notifier:
                    url = self.notifier.local_url(snapshot_filename)
                self.mqtt_pub.publish_fall(cid, url)
            except Exception:
                LOG.exception("%s: mqtt publish failed", cid)

    # -- main loop ---------------------------------------------------
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

            gate = self._motion_gate_cheap()

            if gate is False:
                # Motion entity says nothing is happening: drain the RTSP
                # buffer without paying the JPEG/H264 decode cost.
                ok = cap.grab()
                if not ok:
                    LOG.warning("%s: RTSP grab failed; reconnecting", cid)
                    cap.release()
                    cap = None
                    time.sleep(2)
                time.sleep(0.05)
                continue

            ok, frame = cap.read()
            if not ok:
                LOG.warning("%s: RTSP frame read failed; reconnecting", cid)
                cap.release()
                cap = None
                time.sleep(2)
                continue

            self.last_frame = frame

            if gate is None:
                # No motion entity configured: decide via image differencing.
                motion, self.last_motion_gray = image_motion(
                    self.last_motion_gray,
                    frame,
                    self.g.get("motion_threshold", 8.0),
                    self.g.get("motion_pixels", 250),
                )
                if not motion:
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
