# Changelog

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
