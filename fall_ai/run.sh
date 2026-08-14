#!/usr/bin/env bash
set -e

CONFIG_FILE="/config/$(python3 - <<'PY'
import json, os
p="/data/options.json"
try:
    print(json.load(open(p)).get("config_file","fall_config.yaml"))
except Exception:
    print("fall_config.yaml")
PY
)"

mkdir -p /config /data/ultralytics /media/fall_ai

if [ ! -f "$CONFIG_FILE" ]; then
    echo "[INFO] Creating default Fall AI configuration: $CONFIG_FILE"
    cp /app/fall_config.example.yaml "$CONFIG_FILE"
fi

echo "[INFO] Using configuration: $CONFIG_FILE"
exec python3 /app/app/main.py --config "$CONFIG_FILE"
