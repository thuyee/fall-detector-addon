"""Sends fall alerts to Home Assistant mobile-app notify targets and to
the zalo_bot custom integration.

Both delivery paths need the snapshot to be readable from *inside Home
Assistant Core's own filesystem view* (not the addon's isolated /config):

- Mobile app notify: the companion app resolves a relative image path like
  "/local/<file>.jpg" against your configured HA server URL, so no host
  guessing is needed.
- zalo_bot.send_image: takes a literal filesystem path (`image_path`) that
  is read by the zalo_bot integration running inside HA Core's container,
  which sees the same physical folder at "/config/www/...".

For both to work, the addon writes snapshots into Home Assistant's real
`www/` folder (mounted into the addon via `homeassistant_config:rw`, see
DOCS.md), not into the addon's own private config folder.
"""
import logging
import time

LOG = logging.getLogger("fall_ai.notify")


class Notifier:
    def __init__(self, ha_client, cfg, www_subdir="fall_ai"):
        self.ha = ha_client
        self.cfg = cfg or {}
        self.www_subdir = www_subdir

    def _format_message(self, camera_name, score, timestamp):
        t_str = time.strftime("%H:%M:%S %d/%m/%Y", time.localtime(timestamp))
        template = self.cfg.get(
            "message", "\u26a0\ufe0f Ph\u00e1t hi\u1ec7n t\u00e9 ng\u00e3 t\u1ea1i {camera_name} l\u00fac {time}"
        )
        try:
            return template.format(camera_name=camera_name, time=t_str, score=round(float(score), 2))
        except Exception:
            return f"\u26a0\ufe0f Ph\u00e1t hi\u1ec7n t\u00e9 ng\u00e3 t\u1ea1i {camera_name} l\u00fac {t_str}"

    # -- paths -----------------------------------------------------------
    def local_url(self, filename):
        """Relative URL for HA mobile-app notify (resolved against the
        server URL by the companion app itself)."""
        if not filename:
            return None
        return f"/local/{self.www_subdir}/{filename}"

    def ha_core_path(self, filename):
        """Absolute filesystem path as seen from *inside HA Core's own
        container* (used by zalo_bot.send_image's image_path field)."""
        if not filename:
            return None
        return f"/config/www/{self.www_subdir}/{filename}"

    # -- dispatch ----------------------------------------------------------
    def notify_fall(self, camera_name, score, snapshot_filename, timestamp):
        if not self.cfg.get("enabled", True):
            return

        message = self._format_message(camera_name, score, timestamp)

        self._notify_mobile(message, snapshot_filename)
        self._notify_zalo(message, snapshot_filename)

    def _notify_mobile(self, message, snapshot_filename):
        mobile_cfg = self.cfg.get("mobile", {})
        if not mobile_cfg.get("enabled", True):
            return
        services = mobile_cfg.get("services") or []
        if not services:
            LOG.debug("No mobile notify services configured; skipping mobile push")
            return

        base_url = (mobile_cfg.get("base_url") or "").strip()
        if base_url:
            image = f"{base_url.rstrip('/')}/local/{self.www_subdir}/{snapshot_filename}" if snapshot_filename else None
        else:
            image = self.local_url(snapshot_filename)

        for service in services:
            data = {}
            if image:
                data["image"] = image
            payload = {"title": "Fall AI", "message": message, "data": data}
            if self.ha.call_service("notify", service, payload):
                LOG.info("Mobile notification sent via %s", service)

    def _notify_zalo(self, message, snapshot_filename):
        zalo_cfg = self.cfg.get("zalo", {})
        if not zalo_cfg.get("enabled", False):
            return

        thread_id = zalo_cfg.get("thread_id") or ""
        account = zalo_cfg.get("account_selection") or ""
        if not thread_id or not account:
            LOG.warning(
                "notifications.zalo.enabled=true but thread_id/account_selection "
                "is not set; skipping Zalo notification"
            )
            return

        msg_type = zalo_cfg.get("msg_type", "1")
        ttl = zalo_cfg.get("ttl", 0)

        if zalo_cfg.get("send_text", True):
            payload = {
                "type": msg_type,
                "ttl": ttl,
                "thread_id": thread_id,
                "account_selection": account,
                "message": message,
            }
            if self.ha.call_service("zalo_bot", "send_message", payload):
                LOG.info("Zalo text alert sent (thread_id=%s)", thread_id)

        if zalo_cfg.get("send_image", True) and snapshot_filename:
            image_path = self.ha_core_path(snapshot_filename)
            payload = {
                "type": msg_type,
                "ttl": ttl,
                "thread_id": thread_id,
                "account_selection": account,
                "image_path": image_path,
            }
            if self.ha.call_service("zalo_bot", "send_image", payload):
                LOG.info("Zalo snapshot sent (thread_id=%s, path=%s)", thread_id, image_path)
