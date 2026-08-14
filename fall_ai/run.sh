#!/usr/bin/env bash
set -euo pipefail

OPTIONS="/data/options.json"
APP_CONFIG="/config/config.yaml"

mkdir -p /config/models /config/snapshots /media/fall_ai

# Home Assistant app service API. The Supervisor exposes the MQTT service
# credentials to apps that declare: services: [mqtt:need].
if [[ -n "${SUPERVISOR_TOKEN:-}" ]]; then
  MQTT_JSON="$(curl -fsS -H "Authorization: Bearer ${SUPERVISOR_TOKEN}"     http://supervisor/services/mqtt || true)"
  if [[ -n "${MQTT_JSON}" ]]; then
    export MQTT_HOST="$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("host",""))' <<<"${MQTT_JSON}")"
    export MQTT_PORT="$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("port","1883"))' <<<"${MQTT_JSON}")"
    export MQTT_USER="$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("username",""))' <<<"${MQTT_JSON}")"
    export MQTT_PASSWORD="$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("password",""))' <<<"${MQTT_JSON}")"
  fi
fi

LOG_LEVEL="$(python3 - <<'PY'
import json
p="/data/options.json"
try:
    d=json.load(open(p))
    print(d.get("log_level","info"))
except Exception:
    print("info")
PY
)"

echo "[Fall AI] Starting. log_level=${LOG_LEVEL}"
exec python3 /app/main.py --config "${APP_CONFIG}" --log-level "${LOG_LEVEL}"
