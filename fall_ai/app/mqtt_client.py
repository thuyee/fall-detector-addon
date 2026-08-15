"""Optional MQTT integration: publishes a per-camera 'fall detected'
binary_sensor via Home Assistant MQTT discovery.

Disabled by default (mqtt.enabled: false in config.yaml). When enabled,
broker credentials are auto-discovered from the Supervisor-managed MQTT
add-on if present; otherwise host/port/user/password can be set explicitly
under the 'mqtt' section of config.yaml.
"""
import json
import logging

LOG = logging.getLogger("fall_ai.mqtt")

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - optional dependency
    mqtt = None


class MqttPublisher:
    def __init__(self, cfg, ha_client, cameras):
        self.cfg = cfg or {}
        self.ha = ha_client
        self.cameras = cameras
        self.client = None
        self.prefix = self.cfg.get("prefix", "fall_ai")
        self.discovery_prefix = self.cfg.get("discovery_prefix", "homeassistant")
        self._connected = False

    def start(self):
        if not self.cfg.get("enabled", False):
            return
        if mqtt is None:
            LOG.error("mqtt.enabled=true but paho-mqtt is not installed")
            return

        host = self.cfg.get("host")
        port = int(self.cfg.get("port", 1883))
        user = self.cfg.get("username")
        password = self.cfg.get("password")

        if not host:
            svc = self.ha.get_addon_mqtt_service()
            if svc:
                host = svc.get("host")
                port = int(svc.get("port", 1883))
                user = svc.get("username")
                password = svc.get("password")

        if not host:
            LOG.error(
                "mqtt.enabled=true but no broker found. Install the Mosquitto "
                "add-on, or set mqtt.host/port/username/password explicitly."
            )
            return

        self.client = mqtt.Client()
        if user:
            self.client.username_pw_set(user, password)

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                self._connected = True
                LOG.info("MQTT connected to %s:%s", host, port)
                self._publish_discovery()
            else:
                LOG.error("MQTT connect failed, rc=%s", rc)

        self.client.on_connect = on_connect
        try:
            self.client.connect(host, port, keepalive=60)
            self.client.loop_start()
        except Exception:
            LOG.exception("MQTT connection failed")

    def _topic(self, cam_id, suffix):
        return f"{self.prefix}/{cam_id}/{suffix}"

    def _publish_discovery(self):
        for cam in self.cameras:
            cid = cam.get("id")
            name = cam.get("name", cid)
            state_topic = self._topic(cid, "fall")
            unique_id = f"fall_ai_{cid}"
            discovery_topic = f"{self.discovery_prefix}/binary_sensor/{unique_id}/config"
            payload = {
                "name": f"Fall AI {name}",
                "unique_id": unique_id,
                "state_topic": state_topic,
                "payload_on": "ON",
                "payload_off": "OFF",
                "device_class": "safety",
                "device": {
                    "identifiers": ["fall_ai_addon"],
                    "name": "Fall AI",
                    "manufacturer": "Fall AI Addon",
                },
            }
            self.client.publish(discovery_topic, json.dumps(payload), retain=True)
            self.client.publish(state_topic, "OFF", retain=True)

    def publish_fall(self, cam_id, snapshot_url=None):
        if not self._connected or not self.client:
            return
        self.client.publish(self._topic(cam_id, "fall"), "ON", retain=True)
        if snapshot_url:
            self.client.publish(self._topic(cam_id, "snapshot_url"), snapshot_url, retain=True)

    def publish_clear(self, cam_id):
        if not self._connected or not self.client:
            return
        self.client.publish(self._topic(cam_id, "fall"), "OFF", retain=True)

    def stop(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
