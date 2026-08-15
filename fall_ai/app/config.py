"""Load and validate the persistent /config/config.yaml."""
import logging

import yaml

LOG = logging.getLogger("fall_ai.config")

DEFAULT_GLOBAL = {
    "inference_fps": 3,
    "image_size": 416,
    "person_confidence": 0.45,
    "motion_threshold": 8.0,
    "motion_pixels": 250,
    "confirmation_seconds": 8,
    "cooldown_seconds": 60,
    "fall_score_threshold": 0.72,
    "cpu_threads": 2,
    "snapshot_on_event": True,
    "snapshot_retention_days": 7,
    "max_people": 2,
    "track_timeout_seconds": 1.5,
    "track_iou_threshold": 0.3,
}

DEFAULT_NOTIFICATIONS = {
    "enabled": True,
    "message": "\u26a0\ufe0f Ph\u00e1t hi\u1ec7n t\u00e9 ng\u00e3 t\u1ea1i {camera_name} l\u00fac {time}",
    "base_url": "",
    "snapshot_port": 8099,
    "mobile": {"enabled": True, "services": []},
    "zalo": {"enabled": False, "service": ""},
}

DEFAULT_MQTT = {
    "enabled": False,
    "prefix": "fall_ai",
    "discovery_prefix": "homeassistant",
}


def _merge_defaults(user, defaults):
    out = dict(defaults)
    for k, v in (user or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_defaults(v, out[k])
        else:
            out[k] = v
    return out


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise ValueError("Config root must be a YAML mapping")

    raw_cams = cfg.get("cameras")
    if raw_cams is None:
        raw_cams = []
    if not isinstance(raw_cams, list):
        raise ValueError("cameras must be a list")

    cameras = []
    seen_ids = set()
    for i, cam in enumerate(raw_cams):
        if not isinstance(cam, dict):
            LOG.warning("cameras[%d]: not a mapping, skipped", i)
            continue
        cid = cam.get("id")
        rtsp = cam.get("rtsp")
        if not cid:
            LOG.warning("cameras[%d]: missing 'id', skipped", i)
            continue
        if not rtsp:
            LOG.warning("cameras[%d] (%s): missing 'rtsp', skipped", i, cid)
            continue
        if cid in seen_ids:
            LOG.warning("cameras[%d]: duplicate id '%s', skipped", i, cid)
            continue
        seen_ids.add(cid)
        cam.setdefault("name", cid)
        cam.setdefault("enabled", True)
        cameras.append(cam)

    cfg["cameras"] = cameras
    cfg["global"] = _merge_defaults(cfg.get("global"), DEFAULT_GLOBAL)
    cfg["notifications"] = _merge_defaults(cfg.get("notifications"), DEFAULT_NOTIFICATIONS)
    cfg["mqtt"] = _merge_defaults(cfg.get("mqtt"), DEFAULT_MQTT)
    return cfg
