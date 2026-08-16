# v0.4.14

- **Bugfix: `fall_candidate_bad_since` was dead code — one noisy frame could
  silently reset an otherwise-real fall's confirmation progress to zero.**
  The comment right above the reset line even said "do not cancel
  immediately on one bad pose frame", but the code did exactly that:
  `fall_stable_since` (the 1.8s confirmation timer) and `fall_unknown_since`
  (the unknown-onset timer) were popped on the very first frame where the
  pose estimate ticked a degree past the lying threshold — common on
  low-res/low-fps CPU pose estimation. A real fall with a single jittery
  frame in the middle would never accumulate enough continuous "lying" time
  to confirm. Now tolerates up to `lying_grace_seconds` (default 0.6s) of
  continuous bad frames before actually resetting either timer.

# v0.4.14

- **Fixed: fake `angle_drop` from occlusion-based `angle=45.0` fallback.**
  `pose_features()` in `model.py` falls back to a fixed `angle = 45.0`
  placeholder whenever a person's shoulder pair AND hip pair aren't both
  visible (occlusion, side-on framing, poor lighting) - this is not a real
  measurement. Previously this fallback silently poisoned every downstream
  calculation that compared angles: `angle_drop`/`angular_velocity` could
  report a large fabricated "rotation" purely because a track's keypoints
  flipped between visible/occluded with zero real posture change (seen in
  the field as `angle=45.0` frozen across many frames with `drop=44.x` on a
  person who never fell), and the `stable_lie_threshold`/`unknown_onset`
  angle checks (`angle <= X`) were trivially always true at exactly 45.0
  regardless of real posture.
  - `pose_features()` now returns `angle_valid: bool`. When false,
    `lying_score`/`upright_score` are recomputed from aspect + keypoint
    spread only (torso/angle term dropped and weights renormalized), so
    they remain a genuine, if noisier, signal instead of embedding the
    placeholder.
  - Every `angle <= threshold` comparison in `camera.py`
    (fast-fall final posture, slow-fall final posture, the stable-lie
    check, and the unknown-onset check) now falls back to the
    angle-independent `lying_score` when `angle_valid` is false, instead of
    trivially evaluating against the placeholder.
  - `angle_drop`/`angular_velocity`/`slow_angle_drop` are now `0.0` (no
    signal) whenever either endpoint of the comparison has an invalid
    angle, instead of a fabricated delta.
  - To avoid losing recall from this stricter angle handling, the fast-fall
    and slow-fall hard "did the torso move" requirements substitute the
    hip-drop / aspect-gain cues when angle isn't available, so a genuine
    fall that ends with the torso occluded (e.g. behind furniture) still
    confirms - verified in testing to still confirm in ~3.6s for a fast
    fall landing with the torso occluded, vs. never confirming before this
    fix in that same scenario.

# v0.4.13

- Version bump only (packaging), no functional change from v0.4.12.

# v0.4.12

- **New: unknown-onset lying fallback.** Previously, a fall could only be
  confirmed if the detector had seen the person upright first
  (`_fall_transition` requires an upright reference). This meant a track
  that lost identity mid-fall (RTSP glitch, HA motion sensor flapping,
  occlusion) and reappeared as a "new" track while already on the floor
  could lie there indefinitely with zero alert. Now, any track showing a
  sustained strongly-lying posture for `unknown_onset_seconds` (default
  10s) confirms a fall even with no upright reference at all. Toggle with
  `unknown_onset_enabled` (default true); tune `unknown_onset_seconds`,
  `unknown_onset_lying_threshold`, `unknown_onset_angle` per camera if a
  room legitimately has people lying down on purpose (bed/sofa) and this
  needs to be less sensitive there.
- **Fast-fall cue requirement relaxed for high-confidence scores.** Fast
  falls previously required 2 of 4 dynamic cues (angle drop, angular
  velocity, hip drop, aspect gain) in addition to the score threshold. A
  single occluded keypoint (e.g. legs behind furniture) could drop this to
  1 cue and reject an otherwise obvious fall. Now, `cues >= fall_min_cues`
  (still 2 by default) OR `transition_score >= fall_high_confidence_score`
  (0.85 default) passes - an unambiguous score alone is enough.
- **HA API calls now retry.** `ha_client.fire_event` / `call_service`
  (used for the `fall_ai_event` event and both mobile-app + Zalo
  notifications) previously gave up after a single failed request. A
  one-off network hiccup right when a fall is confirmed meant the alert
  could silently vanish. Both now retry up to `HAClient.retries` times
  (default 2, 1s apart) before giving up. `get_state` (motion polling,
  called every 0.5s) is intentionally left without retry - a miss there
  self-corrects on the next poll.

# v0.4.11

- `slow_fall_posture_threshold` and `slow_fall_stationary_movement` were read
  from config but never actually used to gate anything (dead code). Wired
  them in as a **hard requirement** for the slow-fall path only: a slow-fall
  candidate can now only `confirm_fall()` once the final posture's
  `lying_score >= slow_fall_posture_threshold` AND the person's
  center-of-mass movement over the last `slow_fall_stationary_seconds` is
  `<= slow_fall_stationary_movement`. If either check fails, it is not
  reported as a fall (candidate keeps waiting, up to the existing
  `slow_fall_transition_seconds` cap, then expires).
  - This gate only applies to candidates armed via the slow-fall path
    (gradual stand-to-floor transitions). The fast-fall path (sudden
    collapse) is unaffected.
  - Candidate *creation* for slow falls is intentionally left as lenient as
    before (see the v0.4.6/0.4.7 notes below) — only the final confirmation
    step is gated, so early slow-fall detection is not delayed or rejected.

# v0.4.10

- Mobile notification now defaults to `notify.notify` when no explicit service list is configured.
- Snapshot is frozen and written immediately when FALL CONFIRMED is entered, before HA/Zalo calls.
- Added automatic fall-state clear (default 300s) and a Home Assistant MQTT manual-clear button.
- Manual clear publishes OFF immediately; set `unsafe_auto_clear_seconds: 0` to disable auto-clear.

## 0.4.7
- Prioritize fall-detection recall over strict false-positive rejection.
- Relaxed final lying confirmation to tolerate imperfect YOLO pose/keypoint estimates.
- Slow-fall transition angle relaxed to 50 degrees.
- Stable confirmation relaxed to 1.8 seconds, 48 degree angle, or 0.45 lying score.
- Extended slow-fall candidate lifetime to 8 seconds.

## 0.4.6\n- Keep fall candidates alive through gradual upright-to-lying transitions.\n- Stable-lying confirmation starts only after the final lying posture is reached.\n- Tolerate transient pose/keypoint glitches without immediately cancelling the candidate.\n\n## 0.4.3
- Added a dedicated slow-fall streak path inspired by the supplied V12.5 SmartSense detector.
- Slow falls no longer depend on high angular velocity or a large instantaneous angle drop.
- Slow-fall detection uses an upright-to-torso-angle transition around 45 degrees plus a stationary confirmation window.
- Extended the slow transition lookback to 6 seconds while retaining the existing final stable-lying confirmation.
- Added configurable slow-fall parameters for tuning false positives/false negatives.

## 0.4.2
- Added a slow-fall transition path for gradual stand-to-floor movements.
- Extended pose history/transition window so slow falls can accumulate evidence over several seconds.
- Relaxed angular velocity, angle-drop, hip-drop and aspect-ratio cues for gradual falls.
- Slow-fall candidates still require the existing stable lying confirmation before an alert is sent.

# Changelog

## 0.4.0
- Reworked fall detection around temporal pose transitions instead of single-frame lying posture.
- Added torso-angle change, angular velocity, hip/center-of-mass drop, bbox aspect-ratio change and stable lying confirmation.
- Added short per-person pose history keyed by tracker ID.
- Improved tracker matching with IoU + normalized center distance to preserve identity through a stand-to-floor transition.
- Kept HA motion gating, MQTT, snapshots and Zalo/mobile notifications compatible with v0.3.1.


## 0.3.1
- **Fixed a reconnect-loop bug** introduced by the 0.2.0 `cap.grab()` CPU
  optimization: right after opening an RTSP connection (especially for
  H.265/HEVC streams), the very first `grab()` can transiently fail before
  the decoder has a reference frame yet — the addon was treating a single
  failed grab as a dead connection and reconnecting forever, so the camera
  never actually settled into a working state.
  - Now does one real `read()` immediately after connecting to prime the
    decoder before ever calling grab()-only mode.
  - A failed `grab()` no longer triggers an immediate reconnect; it only
    reconnects after ~30 consecutive failures (a few seconds), tolerating
    the normal decoder warm-up.

## 0.3.0
- **Fixed Zalo integration to match your actual `zalo_bot` custom
  integration.** Previous 0.2.0 assumed a generic `notify.*` service for
  Zalo; it actually exposes `zalo_bot.send_message` and
  `zalo_bot.send_image` with `thread_id` / `account_selection` / `type` /
  `ttl` / `image_path` fields, matching your existing automations. The
  addon now calls both directly with the confirmed-fall message and
  snapshot.
- Replaced the addon's own ad-hoc HTTP snapshot server (port 8099, host
  auto-guessing) with writing snapshots into Home Assistant's real `www/`
  folder via a new `homeassistant_config:rw` map entry. This is what
  `zalo_bot.send_image`'s `image_path` needs (a path inside HA Core's own
  filesystem, not the addon's isolated one) and it also makes mobile-app
  image attachments simpler and more reliable (`/local/...` relative path,
  no host/IP guessing).
- `notifications.zalo` config fields changed: `service` replaced with
  `thread_id`, `account_selection`, `msg_type`, `ttl`, `send_text`,
  `send_image`.
- Removed `notifications.base_url` / `notifications.snapshot_port` (no
  longer needed); `notifications.mobile.base_url` remains as an optional
  override if you'd rather send a fully-qualified image URL.

## 0.2.0
- Rebuilt the addon into modular files (model, camera, tracker, notify,
  mqtt_client, cleanup, config, ha_client) instead of a single main.py.
- **Fixed tracking bug**: fall-candidate state was previously keyed by a
  detection's position in the per-frame confidence-sorted list, which is
  not a stable identity. Replaced with a small IoU-based tracker so state
  stays attached to the correct person across frames, including with
  multiple people in view.
- **CPU optimization**: when the configured motion entity is `off`, the
  addon now calls `cap.grab()` instead of `cap.read()`, skipping frame
  decode entirely instead of decoding every RTSP frame just to discard it.
- Added input validation for `cameras` entries (missing id/rtsp, duplicate
  ids are now skipped with a clear log message instead of causing
  confusing failures later).
- Wired up `track_timeout_seconds` and `mqtt_prefix`, which were present
  in the config schema but previously unused by the code.
- New: automatic notifications on confirmed fall, including a snapshot.
- New: optional MQTT integration (`mqtt.enabled: true`) publishing a
  per-camera `binary_sensor` via Home Assistant MQTT discovery.
- New: automatic snapshot retention/cleanup.

## 0.1.4
- Fixed Docker build for Supervisor 2026.04+.
- Removed dependency on externally supplied BUILD_FROM.
- Uses Python 3.12 slim base.
- Bundles YOLO11n-pose exported with ONNX Opset 21.
- Replaces stale runtime model on every start.
- Includes multi-camera RTSP + Home Assistant motion gating.
- Adds temporal pose-based fall scoring and HA event/snapshot output.

## 0.1.3
- Attempted migration to Opset 21.
