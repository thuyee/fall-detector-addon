"""Sends fall alerts to Home Assistant mobile-app notify targets and Zalo.

Design: the addon calls HA's notify.* services directly via the Supervisor
Core API (no automation needed on the HA side). Images are attached by
URL, pointing at the addon's own snapshot HTTP server (see snapshot_server.py).
"""
import logging
import time
from urllib.parse import urlparse

LOG = logging.getLogger("fall_ai.notify")


def _guess_host(url):
    if not url:
        return None
    try:
        return urlparse(url).hostname
    except Exception:
        return None


class Notifier:
    def __init__(self, ha_client, cfg):
        self.ha = ha_client
        self.cfg = cfg or {}

    def _base_url(self):
        override = (self.cfg.get("base_url") or "").strip()
        if override:
            return override.rstrip("/")

        port = self.cfg.get("snapshot_port", 8099)
        core_cfg = self.ha.get_core_config()
        host = (
            _guess_host(core_cfg.get("internal_url"))
            or _guess_host(core_cfg.get("external_url"))
        )
        if not host:
            LOG.warning(
                "Cannot auto-detect Home Assistant host for snapshot URLs; "
                "set notifications.base_url in config.yaml explicitly."
            )
            return None
        return f"http://{host}:{port}"

    def image_url(self, filename):
        if not filename:
            return None
        base = self._base_url()
        if not base:
            return None
        return f"{base}/{filename}"

    def notify_fall(self, camera_name, score, snapshot_filename, timestamp):
        if not self.cfg.get("enabled", True):
            return

        t_str = time.strftime("%H:%M:%S %d/%m/%Y", time.localtime(timestamp))
        try:
            message = self.cfg.get(
                "message", "\u26a0\ufe0f Ph\u00e1t hi\u1ec7n t\u00e9 ng\u00e3 t\u1ea1i {camera_name} l\u00fac {time}"
            ).format(camera_name=camera_name, time=t_str, score=round(float(score), 2))
        except Exception:
            message = f"\u26a0\ufe0f Ph\u00e1t hi\u1ec7n t\u00e9 ng\u00e3 t\u1ea1i {camera_name} l\u00fac {t_str}"

        image_url = self.image_url(snapshot_filename)

        mobile_cfg = self.cfg.get("mobile", {})
        if mobile_cfg.get("enabled", True):
            services = mobile_cfg.get("services") or []
            if not services:
                LOG.debug("No mobile notify services configured; skipping mobile push")
            for service in services:
                data = {}
                if image_url:
                    data["image"] = image_url
                payload = {"title": "Fall AI", "message": message, "data": data}
                ok = self.ha.call_service("notify", service, payload)
                if ok:
                    LOG.info("Mobile notification sent via %s", service)

        zalo_cfg = self.cfg.get("zalo", {})
        if zalo_cfg.get("enabled", False):
            service = zalo_cfg.get("service") or ""
            if not service:
                LOG.warning("notifications.zalo.enabled=true but no service configured")
            else:
                data = {}
                if image_url:
                    # Most HA notify-style Zalo integrations accept either
                    # 'image' (mobile-app convention) or 'photo'/'attachment';
                    # send the common ones so it works regardless of which
                    # integration exposes the notify.zalo_bot service.
                    data["image"] = image_url
                    data["photo"] = image_url
                payload = {"message": message, "data": data}
                ok = self.ha.call_service("notify", service, payload)
                if ok:
                    LOG.info("Zalo notification sent via %s", service)
