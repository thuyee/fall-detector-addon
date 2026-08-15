# Changelog

## 0.2.0
- Rebuilt the addon into modular files (model, camera, tracker, notify,
  mqtt_client, snapshot_server, cleanup, config, ha_client) instead of a
  single main.py.
- **Fixed tracking bug**: fall-candidate state was previously keyed by a
  detection's position in the per-frame confidence-sorted list, which is not
  a stable identity. Replaced with a small IoU-based tracker so state stays
  attached to the correct person across frames, including with multiple
  people in view.
- **CPU optimization**: when the configured motion entity is `off`, the
  addon now calls `cap.grab()` instead of `cap.read()`, skipping frame
  decode entirely instead of decoding every RTSP frame just to discard it.
- Added input validation for `cameras` entries (missing id/rtsp, duplicate
  ids are now skipped with a clear log message instead of causing confusing
  failures later).
- Wired up `track_timeout_seconds` and `mqtt_prefix`, which were present in
  the config schema but previously unused by the code.
- **New: automatic notifications.** On a confirmed fall, the addon now
  saves a snapshot, serves it over a small built-in HTTP server (port
  8099), and directly calls Home Assistant `notify.*` services for mobile
  app push notifications and for a Zalo bot integration — no user-written
  automation required. Still fires the `fall_ai_event` HA event as before
  for anyone who wants their own automation too.
- **New: optional MQTT integration.** `mqtt.enabled: true` publishes a
  per-camera `binary_sensor` via Home Assistant MQTT discovery.
- **New: snapshot retention.** Old snapshots are cleaned up automatically
  (`global.snapshot_retention_days`, default 7).

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
