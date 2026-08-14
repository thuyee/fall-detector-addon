#!/bin/sh
set -eu

CONFIG="/config/config.yaml"
MODEL_SRC="/opt/models/yolo11n-pose.onnx"
MODEL_DST="/config/models/yolo11n-pose.onnx"

mkdir -p /config/models

# Always replace the runtime model with the bundled Opset-21 model.
# This also removes an old Opset-22 model left by v0.1.1.
if [ -f "$MODEL_SRC" ]; then
  cp -f "$MODEL_SRC" "$MODEL_DST"
fi

if [ ! -f "$CONFIG" ]; then
  cat > "$CONFIG" <<'EOF'
cameras: []
global:
  inference_fps: 3
  alert_fps: 5
  image_size: 416
  person_confidence: 0.45
  motion_threshold: 8.0
  motion_pixels: 250
  confirmation_seconds: 8
  cooldown_seconds: 60
  fall_score_threshold: 0.72
  cpu_threads: 2
  snapshot_on_event: true
  max_people: 2
  track_timeout_seconds: 1.5
  mqtt_prefix: fall_ai
EOF
  echo "[Fall AI] Created /config/config.yaml. Add cameras, save, then restart."
fi

exec python3 /app/main.py --config "$CONFIG"
