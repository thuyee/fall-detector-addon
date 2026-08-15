"""Background thread that deletes snapshots older than the configured
retention period, so the addon's persistent storage doesn't grow forever.
"""
import logging
import threading
import time
from pathlib import Path

LOG = logging.getLogger("fall_ai.cleanup")


class SnapshotCleaner(threading.Thread):
    def __init__(self, directory, retention_days, interval_seconds=3600):
        super().__init__(daemon=True, name="snapshot-cleanup")
        self.directory = Path(directory)
        self.retention_seconds = max(1, float(retention_days)) * 86400
        self.interval = interval_seconds
        self.stop_event = threading.Event()

    def run(self):
        while not self.stop_event.is_set():
            self._sweep()
            self.stop_event.wait(self.interval)

    def _sweep(self):
        if not self.directory.exists():
            return
        cutoff = time.time() - self.retention_seconds
        removed = 0
        try:
            for f in self.directory.glob("*.jpg"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
                except OSError:
                    continue
        except Exception:
            LOG.exception("Snapshot cleanup sweep failed")
        if removed:
            LOG.info("Snapshot cleanup: removed %d old file(s)", removed)

    def stop(self):
        self.stop_event.set()
