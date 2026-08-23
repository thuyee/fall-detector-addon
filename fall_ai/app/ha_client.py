"""Thin client for Home Assistant's Supervisor-proxied Core API."""
import logging
import os
import time

import requests

LOG = logging.getLogger("fall_ai.ha_client")

SUPERVISOR = "http://supervisor"


class HAClient:
    def __init__(self, base=SUPERVISOR, token=None, timeout=5, retries=2, retry_delay=1.0):
        self.base = base
        self.token = token or os.environ.get("SUPERVISOR_TOKEN", "")
        self.timeout = timeout
        # Fall alerts are rare and matter a lot; a single transient network
        # hiccup should not silently swallow an already-confirmed fall. Retry
        # a small, bounded number of times before giving up.
        self.retries = max(0, int(retries))
        self.retry_delay = max(0.0, float(retry_delay))
        self._config_cache = None
        self._config_cache_at = 0.0

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _post_with_retry(self, url, payload, what):
        last_exc = None
        for attempt in range(self.retries + 1):
            try:
                r = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout)
                r.raise_for_status()
                if attempt > 0:
                    LOG.info("%s succeeded on retry %d/%d", what, attempt, self.retries)
                return True
            except Exception as exc:
                last_exc = exc
                if attempt < self.retries:
                    LOG.warning(
                        "%s failed (attempt %d/%d): %s - retrying in %.1fs",
                        what, attempt + 1, self.retries + 1, exc, self.retry_delay,
                    )
                    time.sleep(self.retry_delay)
        LOG.error("%s failed after %d attempt(s): %s", what, self.retries + 1, last_exc)
        return False

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
        url = f"{self.base}/core/api/events/{event_type}"
        return self._post_with_retry(url, data, f"fire_event({event_type})")

    def call_service(self, domain, service, payload):
        """service may be given as 'notify.foo' or just 'foo'."""
        if not self.token:
            LOG.warning("No SUPERVISOR_TOKEN; cannot call service %s.%s", domain, service)
            return False
        if "." in service:
            service = service.split(".", 1)[1]
        url = f"{self.base}/core/api/services/{domain}/{service}"
        return self._post_with_retry(url, payload, f"call_service({domain}.{service})")

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

