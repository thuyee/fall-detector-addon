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
