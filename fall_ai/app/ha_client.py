"""Thin client for Home Assistant's Supervisor-proxied Core API."""
import logging
import os
import time

import requests

LOG = logging.getLogger("fall_ai.ha_client")

SUPERVISOR = "http://supervisor"


class HAClient:
    def __init__(self, base=SUPERVISOR, token=None, timeout=5):
        self.base = base
        self.token = token or os.environ.get("SUPERVISOR_TOKEN", "")
        self.timeout = timeout
        self._config_cache = None
        self._config_cache_at = 0.0

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def get_state(self, entity_id):
        if not entity_id or not self.token:
            return None
        try:
            r = requests.get(
                f"{self.base}/core/api/states/{entity_id}",
                headers=self._headers(),
                timeout=2,
            )
            r.raise_for_status()
            return r.json().get("state")
        except Exception as exc:
            LOG.warning("get_state(%s) failed: %s", entity_id, exc)
            return None

    def get_core_config(self, max_age=300):
        """Cached fetch of /core/api/config (has external_url/internal_url)."""
        now = time.monotonic()
        if self._config_cache is not None and now - self._config_cache_at < max_age:
            return self._config_cache
        try:
            r = requests.get(
                f"{self.base}/core/api/config",
                headers=self._headers(),
                timeout=self.timeout,
            )
            r.raise_for_status()
            self._config_cache = r.json()
            self._config_cache_at = now
        except Exception:
            LOG.exception("Failed to fetch /core/api/config")
            self._config_cache = self._config_cache or {}
        return self._config_cache

    def fire_event(self, event_type, data):
        if not self.token:
            LOG.warning("No SUPERVISOR_TOKEN; cannot fire event %s", event_type)
            return False
        try:
            r = requests.post(
                f"{self.base}/core/api/events/{event_type}",
                headers=self._headers(),
                json=data,
                timeout=self.timeout,
            )
            r.raise_for_status()
            return True
        except Exception as exc:
            LOG.error("fire_event(%s) failed: %s", event_type, exc)
            return False

    def call_service(self, domain, service, payload):
        """service may be given as 'notify.foo' or just 'foo'."""
        if not self.token:
            LOG.warning("No SUPERVISOR_TOKEN; cannot call service %s.%s", domain, service)
            return False
        if "." in service:
            service = service.split(".", 1)[1]
        url = f"{self.base}/core/api/services/{domain}/{service}"
        try:
            r = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout)
            r.raise_for_status()
            return True
        except Exception as exc:
            LOG.error("call_service(%s.%s) failed: %s", domain, service, exc)
            return False

    def get_addon_mqtt_service(self):
        """Ask Supervisor for the HA-managed MQTT broker credentials, if any."""
        if not self.token:
            return None
        try:
            r = requests.get(
                f"{self.base}/services/mqtt",
                headers=self._headers(),
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json().get("data") or {}
            if not data.get("host"):
                return None
            return data
        except Exception as exc:
            LOG.info("No Supervisor-managed MQTT service available: %s", exc)
            return None
