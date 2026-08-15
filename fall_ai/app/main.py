#!/usr/bin/env python3
"""Fall AI addon entry point."""
import argparse
import logging
import os
import time

from camera import CameraWorker
from cleanup import SnapshotCleaner
from config import load_config
from ha_client import HAClient
from model import PoseModel
from mqtt_client import MqttPublisher
from notify import Notifier

LOG = logging.getLogger("fall_ai")

# Home Assistant's real config directory, mounted read/write via the
# 'homeassistant_config:rw' map entry in config.yaml. Snapshots are written
# under its www/ folder so both the HA frontend (/local/...) and the
# zalo_bot integration (which runs inside HA Core and sees this same
# folder as /config/www/...) can read them.
HA_CONFIG_ROOT = "/homeassistant"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/config/config.yaml")
    args = ap.parse_args()

    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    g = cfg["global"]
    notif_cfg = cfg["notifications"]
    mqtt_cfg = cfg["mqtt"]
    www_subdir = notif_cfg.get("www_subdir", "fall_ai")
    snapshot_dir = os.path.join(HA_CONFIG_ROOT, "www", www_subdir)

    LOG.info("Fall AI starting")
    LOG.info("Configured cameras: %d", len(cfg["cameras"]))
    LOG.info(
        "CPU threads=%s inference_fps=%s image_size=%s",
        g.get("cpu_threads", 2),
        g.get("inference_fps", 3),
        g.get("image_size", 416),
    )

    ha = HAClient()

    if not cfg["cameras"]:
        LOG.warning("No cameras configured. Add cameras to /config/config.yaml.")
        while True:
            time.sleep(60)

    model = PoseModel(g.get("image_size", 416), g.get("cpu_threads", 2))

    notifier = None
    if notif_cfg.get("enabled", True):
        if not os.path.isdir(HA_CONFIG_ROOT):
            LOG.error(
                "%s is not mounted. Add 'homeassistant_config:rw' to this "
                "addon's 'map:' in config.yaml and restart, otherwise "
                "mobile/Zalo image notifications cannot find your Home "
                "Assistant www/ folder.",
                HA_CONFIG_ROOT,
            )
        try:
            os.makedirs(snapshot_dir, exist_ok=True)
        except Exception:
            LOG.exception("Could not create snapshot folder %s", snapshot_dir)
        notifier = Notifier(ha, notif_cfg, www_subdir=www_subdir)
        LOG.info(
            "Notifications enabled: mobile=%s zalo=%s -> %s",
            notif_cfg.get("mobile", {}).get("enabled", True),
            notif_cfg.get("zalo", {}).get("enabled", False),
            snapshot_dir,
        )

    mqtt_pub = None
    if mqtt_cfg.get("enabled", False):
        mqtt_pub = MqttPublisher(mqtt_cfg, ha, cfg["cameras"])
        mqtt_pub.start()

    cleaner = SnapshotCleaner(snapshot_dir, g.get("snapshot_retention_days", 7))
    cleaner.start()

    workers = []
    for cam in cfg["cameras"]:
        if not cam.get("enabled", True):
            LOG.info("Camera %s disabled, skipping", cam.get("id"))
            continue
        worker = CameraWorker(cam, g, model, ha, notifier=notifier, mqtt_pub=mqtt_pub,
                               snapshot_dir=snapshot_dir)
        workers.append(worker)
        worker.start()
        LOG.info("Camera %s enabled; motion_entity=%s", cam.get("id"), cam.get("motion_entity"))

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        LOG.info("Shutting down...")
        for w in workers:
            w.stop()
        cleaner.stop()
        if mqtt_pub:
            mqtt_pub.stop()


if __name__ == "__main__":
    main()
