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
from snapshot_server import SnapshotServer

LOG = logging.getLogger("fall_ai")

SNAPSHOT_DIR = "/config/snapshots"


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

    notifier = Notifier(ha, notif_cfg) if notif_cfg.get("enabled", True) else None
    if notifier:
        LOG.info(
            "Notifications enabled: mobile=%s zalo=%s",
            notif_cfg.get("mobile", {}).get("enabled", True),
            notif_cfg.get("zalo", {}).get("enabled", False),
        )

    snapshot_server = None
    if notif_cfg.get("enabled", True):
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        snapshot_server = SnapshotServer(SNAPSHOT_DIR, notif_cfg.get("snapshot_port", 8099))
        snapshot_server.start()

    mqtt_pub = None
    if mqtt_cfg.get("enabled", False):
        mqtt_pub = MqttPublisher(mqtt_cfg, ha, cfg["cameras"])
        mqtt_pub.start()

    cleaner = SnapshotCleaner(SNAPSHOT_DIR, g.get("snapshot_retention_days", 7))
    cleaner.start()

    workers = []
    for cam in cfg["cameras"]:
        if not cam.get("enabled", True):
            LOG.info("Camera %s disabled, skipping", cam.get("id"))
            continue
        worker = CameraWorker(cam, g, model, ha, notifier=notifier, mqtt_pub=mqtt_pub,
                               snapshot_dir=SNAPSHOT_DIR)
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
        if snapshot_server:
            snapshot_server.stop()
        if mqtt_pub:
            mqtt_pub.stop()


if __name__ == "__main__":
    main()
