"""Tiny static file server that exposes /config/snapshots over HTTP.

Home Assistant mobile-app (and most notify integrations, including a
Zalo bot) attach images by URL, not by local container path. The addon's
own /config directory is not reachable from the HA frontend/companion
app, so we serve the snapshot folder ourselves on a dedicated port and
build notification image URLs against it.
"""
import http.server
import logging
import socketserver
import threading

LOG = logging.getLogger("fall_ai.snapshot_server")


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        LOG.debug("snapshot_server: " + fmt, *args)


class SnapshotServer(threading.Thread):
    def __init__(self, directory, port=8099):
        super().__init__(daemon=True)
        self.directory = directory
        self.port = int(port)
        self._httpd = None

    def run(self):
        handler = lambda *a, **kw: _Handler(*a, directory=self.directory, **kw)
        try:
            self._httpd = socketserver.ThreadingTCPServer(("0.0.0.0", self.port), handler)
            self._httpd.daemon_threads = True
            LOG.info("Snapshot HTTP server listening on :%d (serving %s)", self.port, self.directory)
            self._httpd.serve_forever()
        except Exception:
            LOG.exception("Snapshot HTTP server failed to start on port %d", self.port)

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
