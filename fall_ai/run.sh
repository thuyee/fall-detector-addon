#!/bin/sh
set -eu

CONFIG="/config/config.yaml"
MODEL_SRC="/opt/models/yolo11n-pose.onnx"
MODEL_DST="/config/models/yolo11n-pose.onnx"

mkdir -p /config/models
mkdir -p /config/snapshots

# Always replace any old runtime model, including an older/incompatible opset.
cp -f "$MODEL_SRC" "$MODEL_DST"

if [ ! -f "$CONFIG" ]; then
  cat > "$CONFIG" <<'YAML'
cameras:
  - id: phong_khach
    name: "Phòng khách"
    rtsp: "rtsp://admin:YOUR_PASSWORD@192.168.0.107:554"
    motion_entity: "binary_sensor.c6n_e17394610_motion"
    enabled: true

  - id: san
    name: "Sân"
    rtsp: "rtsp://admin:YOUR_PASSWORD@192.168.0.122:554"
    motion_entity: "binary_sensor.h6c_bf2378127_motion"
    enabled: true

global:
  inference_fps: 3
  image_size: 416
  person_confidence: 0.45
  motion_threshold: 8.0
  motion_pixels: 250
  confirmation_seconds: 8
  cooldown_seconds: 60
  fall_score_threshold: 0.72
  cpu_threads: 2
  snapshot_on_event: true
  snapshot_retention_days: 7
  max_people: 2
  track_timeout_seconds: 1.5
  track_iou_threshold: 0.3

notifications:
  enabled: true
  message: "⚠️ Phát hiện té ngã tại {camera_name} lúc {time}"
  base_url: ""
  snapshot_port: 8099
  mobile:
    enabled: true
    services:
      - notify.mobile_app_dien_thoai_cua_a
  zalo:
    enabled: false
    service: notify.zalo_bot

mqtt:
  enabled: false
  prefix: fall_ai
  discovery_prefix: homeassistant
YAML
  echo "[Fall AI] Created /config/config.yaml. Edit RTSP passwords and notification targets, save, then restart."
fi

exec python3 /app/main.py --config "$CONFIG"
