"""Per-camera worker thread: RTSP capture, motion gating, pose inference,
temporal fall confirmation, snapshot + notification dispatch.
"""
import logging
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from model import decode_pose, pose_features
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
            timeout=float(g.get("track_timeout_seconds", 2.0)),
            iou_threshold=float(g.get("track_iou_threshold", 0.25)),
            center_threshold=float(g.get("track_center_threshold", 0.20)),
        )
        # Per-track temporal state. A short skeleton history is much more
        # informative than a single "lying" frame: a real fall normally has
        # standing -> rapid rotation -> low/lying posture.
        self.track_history = {}
        # Candidate state is kept through short pose/keypoint glitches.
        # A slow fall may cross the slow-fall transition angle well before
        # YOLO reports a fully lying posture.
        self.fall_candidate_since = {}
        self.fall_stable_since = {}
        self.fall_candidate_bad_since = {}
        # Which path (fast/slow) armed the current candidate. Used to gate
        # slow-fall confirmation behind an explicit posture + stillness
        # check (see slow_fall_posture_threshold / slow_fall_stationary_movement).
        self.fall_candidate_mode = {}
        # Track continuously-lying duration independent of the transition
        # candidate above. Covers the case where a track never had an
        # upright reference to compare against - e.g. the RTSP link
        # glitched, HA motion flapped, or the person was already occluded
        # while falling and only reappears as a "new" track once already on
        # the floor. Without this, such a person could lie on the floor
        # indefinitely and never generate an alert, because
        # _fall_transition() requires seeing them upright first.
        self.fall_unknown_since = {}
        self.last_debug_log = {}

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
    def _state_for(self, tid):
        maxlen = max(8, int(float(self.g.get("history_seconds", 3.0)) *
                            float(self.g.get("inference_fps", 3)) + 3))
        state = self.track_history.get(tid)
        if state is None:
            state = {"history": deque(maxlen=maxlen)}
            self.track_history[tid] = state
        elif state["history"].maxlen != maxlen:
            state["history"] = deque(state["history"], maxlen=maxlen)
        return state

    @staticmethod
    def _recent_reference(history, now, seconds):
        ref = None
        cutoff = now - seconds
        for item in history:
            if item["t"] >= cutoff and item["t"] < now:
                ref = item
        return ref

    def _fall_transition(self, history, feat, now):
        """Detect the transition into a fall using short-term geometry.

        The detector deliberately requires a pre-fall upright state. This
        prevents a person who was already lying down from generating an
        alert merely because motion restarted.
        """
        if not feat.get("valid"):
            return False, 0.0, {}

        current = feat["lying_score"]
        angle = feat["angle"]
        # Independent score cores for fast and slow falls.
        # Keep the legacy fall_score_threshold as a fallback for fast-fall.
        fast_threshold = float(self.g.get(
            "fast_fall_score_threshold",
            self.g.get("fall_score_threshold", 0.72)
        ))
        slow_threshold = float(self.g.get("slow_fall_score_threshold", 0.30))
        min_lie_angle = float(self.g.get("fall_lie_angle", 38.0))
        min_angle_drop = float(self.g.get("fall_min_angle_drop", 20.0))
        min_angular_velocity = float(self.g.get("fall_min_angular_velocity", 8.0))
        min_hip_drop = float(self.g.get("fall_min_hip_drop", 0.035))
        min_aspect_gain = float(self.g.get("fall_min_aspect_gain", 0.25))
        transition_window = float(self.g.get("transition_window_seconds", 4.0))
        slow_window = float(self.g.get("slow_fall_transition_seconds", 6.0))

        ref = self._recent_reference(history, now, transition_window)
        if ref is None:
            return False, 0.0, {}

        # Fast path baseline uses the shorter transition window. The slow path
        # below can look further back because a gradual stand-to-floor movement
        # may take several seconds.
        upright_ref = max(
            (x for x in history if x["t"] >= now - transition_window
             and x["t"] < now),
            key=lambda x: x["f"]["upright_score"],
            default=ref,
        )
        slow_upright_ref = max(
            (x for x in history if x["t"] >= now - slow_window
             and x["t"] < now),
            key=lambda x: (x["f"]["upright_score"], x["f"]["angle"]),
            default=upright_ref,
        )

        dt = max(0.05, now - upright_ref["t"])
        angle_drop = max(0.0, upright_ref["f"]["angle"] - angle)
        angular_velocity = angle_drop / dt
        hip_drop = max(0.0, feat["center_y"] - upright_ref["f"]["center_y"])
        aspect_gain = feat["aspect"] - upright_ref["f"]["aspect"]

        upright_seen = upright_ref["f"]["upright_score"] >= float(
            self.g.get("prefall_upright_score", 0.55)
        ) or upright_ref["f"]["angle"] >= float(self.g.get("prefall_upright_angle", 55.0))

        # Two independent dynamic cues are required in addition to the final
        # lying posture. This is the key false-positive reduction.
        cues = 0
        if angle_drop >= min_angle_drop:
            cues += 1
        if angular_velocity >= min_angular_velocity:
            cues += 1
        if hip_drop >= min_hip_drop:
            cues += 1
        if aspect_gain >= min_aspect_gain:
            cues += 1

        angle_score = np.clip(angle_drop / 55.0, 0, 1)
        velocity_score = np.clip(angular_velocity / 55.0, 0, 1)
        hip_score = np.clip(hip_drop / 0.14, 0, 1)
        aspect_score = np.clip(aspect_gain / 1.0, 0, 1)

        transition_score = float(
            0.30 * current
            + 0.25 * angle_score
            + 0.18 * velocity_score
            + 0.15 * hip_score
            + 0.12 * aspect_score
        )

        min_cues = int(self.g.get("fall_min_cues", 2))
        # When the composite score is already very high, don't let a single
        # missing cue (e.g. hip/ankle occluded by furniture) block an
        # otherwise obvious fast fall. Recall > precision here.
        high_confidence_score = float(self.g.get("fall_high_confidence_score", 0.85))

        # Normal/fast path keeps the stricter score, but the two-cue
        # requirement can be bypassed when the score alone is already
        # high-confidence (see fall_high_confidence_score above).
        fast_pass = (
            upright_seen
            and transition_score >= fast_threshold
            and current >= fast_threshold
            and angle <= min_lie_angle
            and angle_drop >= min_angle_drop
            and (cues >= min_cues or transition_score >= high_confidence_score)
        )

        # Slow-fall path: mirror the proven fall-streak idea used by the
        # reference detector: once a person who was upright reaches a torso
        # angle around 45 degrees, keep the candidate alive while the person
        # remains nearly stationary. Do NOT require a high angular velocity or
        # a large instantaneous angle drop; those are precisely what gradual
        # falls can miss. The final stable-lie confirmation in process_people()
        # still has to hold for confirmation_seconds before an alert is sent.
        slow_posture_threshold = float(self.g.get("slow_fall_posture_threshold", 0.45))
        slow_angle_limit = float(self.g.get("slow_fall_angle", 50.0))
        slow_upright_angle = float(self.g.get("slow_fall_upright_angle", 55.0))
        slow_stationary_seconds = float(self.g.get("slow_fall_stationary_seconds", 2.2))
        slow_stationary_movement = float(self.g.get("slow_fall_stationary_movement", 0.055))

        slow_cutoff = now - slow_stationary_seconds
        slow_recent = [x for x in history if x["t"] >= slow_cutoff and x["t"] <= now]
        slow_movement = 0.0
        if len(slow_recent) >= 2:
            slow_movement = sum(
                float(np.hypot(
                    b["f"].get("center_x", 0.0) - a["f"].get("center_x", 0.0),
                    b["f"].get("center_y", 0.0) - a["f"].get("center_y", 0.0),
                ))
                for a, b in zip(slow_recent, slow_recent[1:])
            )

        slow_upright_seen = (
            slow_upright_ref["f"]["upright_score"] >= float(self.g.get("prefall_upright_score", 0.55))
            or slow_upright_ref["f"]["angle"] >= slow_upright_angle
        )
        slow_angle_drop = max(0.0, slow_upright_ref["f"]["angle"] - angle)
        slow_geometry_cue = (
            hip_drop >= min_hip_drop
            or aspect_gain >= min_aspect_gain
            or slow_angle_drop >= 10.0
        )

        # Do not require the person to be stationary BEFORE creating the
        # candidate. The reference detector starts its fall streak as soon as
        # the torso crosses ~45 degrees, then confirms that the person remains
        # still. Here that confirmation is represented by the existing
        # stable-lie window in process_people().
        # IMPORTANT: do not require a high lying_score at the moment the
        # slow-fall candidate starts.  During a gradual fall the torso can
        # cross the 45-degree transition point while the bbox/aspect and
        # keypoint spread still look only partly horizontal.  Requiring
        # lying_score here was causing slow falls to be rejected before the
        # confirmation stage ever started.
        #
        # slow_fall_posture_threshold and slow_fall_stationary_movement are
        # NOT checked here on purpose (same reasoning as above). They are a
        # hard gate applied later, in process_people(), right before a
        # slow-fall candidate is actually allowed to confirm_fall(). Keeping
        # them out of candidate creation preserves early/lenient candidate
        # arming; enforcing them only at the final confirm step still means
        # a slow-fall alert can never fire unless both are satisfied.
        slow_pass = (
            slow_upright_seen
            and transition_score >= slow_threshold
            and angle <= slow_angle_limit
            and slow_angle_drop >= 10.0
            and slow_geometry_cue
        )

        passed = fast_pass or slow_pass
        details = {
            "angle": angle,
            "angle_drop": angle_drop,
            "angular_velocity": angular_velocity,
            "hip_drop": hip_drop,
            "aspect_gain": aspect_gain,
            "lying_score": current,
            "cues": cues,
            "upright_seen": upright_seen,
            "slow_angle_drop": slow_angle_drop,
            "slow_movement": slow_movement,
            "mode": "fast" if fast_pass else ("slow" if slow_pass else "none"),
            "fast_threshold": fast_threshold,
            "slow_threshold": slow_threshold,
        }
        return passed, transition_score, details

    def process_people(self, people):
        now = time.monotonic()
        confirm = float(self.g.get("confirmation_seconds", 1.8))
        stable_lie_threshold = float(self.g.get("stable_lie_threshold", 0.45))
        stable_angle = float(self.g.get("stable_lie_angle", 48.0))
        cid = self.cam.get("id", "camera")

        track_ids = self.tracker.update(people, now)
        active_ids = set(track_ids)

        for person, tid in zip(people, track_ids):
            feat = pose_features(person)
            if not feat.get("valid"):
                continue

            state = self._state_for(tid)
            history = state["history"]
            history.append({"t": now, "f": feat})

            transition, transition_score, details = self._fall_transition(history, feat, now)

            lying_now = (
                feat["angle"] <= stable_angle
                or feat["lying_score"] >= stable_lie_threshold
            )

            if transition:
                # Keep the candidate alive. In particular, a slow fall can
                # trigger at ~45 degrees and need several inference frames
                # before it reaches the final lying posture.
                self.fall_candidate_since.setdefault(tid, now)
                self.fall_candidate_bad_since.pop(tid, None)
                self.fall_candidate_mode[tid] = details.get("mode")
                LOG.info(
                    "%s: fall transition track=%s mode=%s score=%.2f angle=%.1f drop=%.1f vel=%.1f hip=%.3f cues=%d",
                    cid, tid, details.get("mode", "?"), transition_score, details["angle"], details["angle_drop"],
                    details["angular_velocity"], details["hip_drop"], details["cues"],
                )

            candidate_since = self.fall_candidate_since.get(tid)
            if candidate_since is not None:
                # v0.4.7 intentionally favors recall over precision. Once a
                # strong fall transition has been detected, accept a somewhat
                # imperfect final pose. A single YOLO/keypoint estimate should
                # not prevent an otherwise obvious fall from being confirmed.
                max_candidate = float(self.g.get("slow_fall_transition_seconds", 8.0))

                if lying_now:
                    # Start the actual stable-lying confirmation only when
                    # the person reaches the final posture. The transition
                    # candidate may remain alive for the whole slow-fall
                    # window before this point.
                    self.fall_stable_since.setdefault(tid, now)
                    self.fall_candidate_bad_since.pop(tid, None)
                    if now - self.fall_stable_since[tid] >= confirm:
                        # Slow-fall candidates get one extra hard gate here:
                        # the person must have reached a genuinely horizontal
                        # posture (not just an ambiguous mid-range score) AND
                        # be nearly motionless over the last
                        # slow_fall_stationary_seconds. Either check failing
                        # means this is NOT reported as a fall yet (e.g. a
                        # bend/crouch/pick-something-up passing through the
                        # lying-adjacent posture while still moving). The
                        # fast path (sudden collapse) is unaffected.
                        mode = self.fall_candidate_mode.get(tid)
                        gate_ok = True
                        if mode == "slow":
                            slow_posture_threshold = float(
                                self.g.get("slow_fall_posture_threshold", 0.45)
                            )
                            slow_stationary_movement = float(
                                self.g.get("slow_fall_stationary_movement", 0.055)
                            )
                            posture_ok = feat["lying_score"] >= slow_posture_threshold
                            stationary_ok = details.get(
                                "slow_movement", 0.0
                            ) <= slow_stationary_movement
                            gate_ok = posture_ok and stationary_ok
                            if not gate_ok:
                                LOG.info(
                                    "%s: slow-fall gate not yet met track=%s lying=%.2f "
                                    "(need>=%.2f) move=%.3f (need<=%.3f)",
                                    cid, tid, feat["lying_score"], slow_posture_threshold,
                                    details.get("slow_movement", 0.0), slow_stationary_movement,
                                )

                        if gate_ok:
                            self.confirm_fall(max(feat["lying_score"], transition_score))
                            self.fall_candidate_since.pop(tid, None)
                            self.fall_stable_since.pop(tid, None)
                            self.fall_candidate_bad_since.pop(tid, None)
                            self.fall_candidate_mode.pop(tid, None)
                            self.fall_unknown_since.pop(tid, None)
                        elif now - candidate_since > max_candidate:
                            # Gate never satisfied (person keeps shifting
                            # slightly, e.g. in pain) - do not stay armed
                            # forever, but this only affects when we give up
                            # waiting, never whether the gate itself is
                            # required.
                            self.fall_candidate_since.pop(tid, None)
                            self.fall_stable_since.pop(tid, None)
                            self.fall_candidate_bad_since.pop(tid, None)
                            self.fall_candidate_mode.pop(tid, None)
                else:
                    self.fall_stable_since.pop(tid, None)
                    # Do not cancel immediately on one bad pose frame. Allow
                    # the slow-fall transition window to complete, but expire
                    # the candidate if the person never reaches a lying
                    # posture. This prevents a normal sit/crouch from staying
                    # armed indefinitely.
                    if now - candidate_since > max_candidate:
                        self.fall_candidate_since.pop(tid, None)
                        self.fall_candidate_bad_since.pop(tid, None)
                        self.fall_candidate_mode.pop(tid, None)

            # -- unknown-onset lying (no upright reference available) ------
            # Runs independently of the transition candidate above. Covers:
            # a track that was lost mid-fall and reappears as a "new" id
            # while already on the floor, a person who was already down
            # when the camera/motion gate first picked them up, or any case
            # where _fall_transition() never found an upright reference.
            # This intentionally trades some precision for recall: a long,
            # uninterrupted lying posture with no known prior standing state
            # is still worth an alert.
            if bool(self.g.get("unknown_onset_enabled", True)):
                unknown_seconds = float(self.g.get("unknown_onset_seconds", 10.0))
                _raw_lie = self.g.get("unknown_onset_lying_threshold")
                unknown_lie_threshold = (
                    float(_raw_lie) if _raw_lie is not None else stable_lie_threshold
                )
                _raw_angle = self.g.get("unknown_onset_angle")
                unknown_angle = (
                    float(_raw_angle) if _raw_angle is not None else stable_angle
                )
                strongly_lying = (
                    feat["angle"] <= unknown_angle
                    or feat["lying_score"] >= unknown_lie_threshold
                )
                if strongly_lying:
                    since = self.fall_unknown_since.setdefault(tid, now)
                    if now - since >= unknown_seconds:
                        LOG.warning(
                            "%s: unknown-onset lying confirmed track=%s lying=%.2f "
                            "angle=%.1f duration=%.1fs (no prior upright reference)",
                            cid, tid, feat["lying_score"], feat["angle"], now - since,
                        )
                        self.confirm_fall(feat["lying_score"])
                        self.fall_unknown_since.pop(tid, None)
                        self.fall_candidate_since.pop(tid, None)
                        self.fall_stable_since.pop(tid, None)
                        self.fall_candidate_bad_since.pop(tid, None)
                        self.fall_candidate_mode.pop(tid, None)
                else:
                    self.fall_unknown_since.pop(tid, None)

        for stale in list(self.track_history.keys()):
            if stale not in active_ids:
                self.track_history.pop(stale, None)
                self.fall_candidate_since.pop(stale, None)
                self.fall_stable_since.pop(stale, None)
                self.fall_candidate_bad_since.pop(stale, None)
                self.fall_candidate_mode.pop(stale, None)
                self.fall_unknown_since.pop(stale, None)

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
        # Freeze the exact frame available at the instant FALL CONFIRMED is
        # entered. Save it before firing HA events or sending notifications,
        # so network/service latency cannot delay or change the capture.
        confirm_frame = self.last_frame.copy() if self.last_frame is not None else None
        if bool(self.g.get("snapshot_on_event", True)) and confirm_frame is not None:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            snapshot_filename = f"{cid}_{stamp}.jpg"
            try:
                self.snapshot_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(self.snapshot_dir / snapshot_filename), confirm_frame)
            except Exception:
                LOG.exception("%s: snapshot failed", cid)
                snapshot_filename = ""

        LOG.warning(
            "%s: FALL CONFIRMED score=%.2f snapshot=%s",
            cid, score, snapshot_filename or "NONE"
        )

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
        consecutive_grab_failures = 0
        # HEVC/H.264 decoders need a few packets (SPS/PPS/keyframe) before
        # grab() can succeed. Right after connecting, a handful of failed
        # grabs is normal, not a dead connection — only reconnect once
        # failures persist well beyond that warm-up window.
        GRAB_FAIL_LIMIT = 30  # ~3s of retries at the 0.1s sleep below

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
                consecutive_grab_failures = 0

                # Prime the decoder with one real read so grab()-only mode
                # (used below when motion is off) has a reference frame to
                # work from; a bare grab() right after opening can fail
                # transiently before any frame has been decoded yet.
                ok, frame = cap.read()
                if ok:
                    self.last_frame = frame
                else:
                    LOG.warning("%s: initial frame read failed, will retry", cid)
                continue

            gate = self._motion_gate_cheap()

            if gate is False:
                # Motion entity says nothing is happening: drain the RTSP
                # buffer without paying the JPEG/H264/H265 decode cost.
                ok = cap.grab()
                if not ok:
                    consecutive_grab_failures += 1
                    if consecutive_grab_failures >= GRAB_FAIL_LIMIT:
                        LOG.warning(
                            "%s: RTSP grab failed %d times in a row; reconnecting",
                            cid, consecutive_grab_failures,
                        )
                        cap.release()
                        cap = None
                        consecutive_grab_failures = 0
                        time.sleep(2)
                else:
                    consecutive_grab_failures = 0
                time.sleep(0.05)
                continue

            ok, frame = cap.read()
            if not ok:
                LOG.warning("%s: RTSP frame read failed; reconnecting", cid)
                cap.release()
                cap = None
                time.sleep(2)
                continue

            consecutive_grab_failures = 0
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
