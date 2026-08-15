"""Small multi-person tracker using IoU + normalized center distance."""
import math


class Tracker:
    def __init__(self, timeout=1.5, iou_threshold=0.3, center_threshold=0.20):
        self.timeout = float(timeout)
        self.iou_threshold = float(iou_threshold)
        self.center_threshold = float(center_threshold)
        self.tracks = {}
        self._next_id = 1

    @staticmethod
    def _iou(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        ax1, ay1, ax2, ay2 = ax - aw / 2, ay - ah / 2, ax + aw / 2, ay + ah / 2
        bx1, by1, bx2, by2 = bx - bw / 2, by - bh / 2, bx + bw / 2, by + bh / 2
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        union = aw * ah + bw * bh - inter
        return inter / union if union > 0 else 0.0

    @staticmethod
    def _center_distance(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        dx = ax - bx
        dy = ay - by
        scale = max(0.05, (ah + bh) / 2.0)
        return math.hypot(dx, dy) / scale

    def update(self, detections, now):
        stale = [tid for tid, t in self.tracks.items()
                 if now - t["last_seen"] > self.timeout]
        for tid in stale:
            del self.tracks[tid]

        candidates = []
        for di, det in enumerate(detections):
            for tid, t in self.tracks.items():
                if t.get("used"):
                    continue
                iou = self._iou(det["bbox"], t["bbox"])
                dist = self._center_distance(det["bbox"], t["bbox"])
                # Either a decent overlap OR a close predicted centre can
                # preserve identity while a person changes from standing to
                # horizontal (where IoU can temporarily fall).
                if iou >= self.iou_threshold or dist <= self.center_threshold:
                    cost = (1.0 - iou) + 0.35 * min(dist, 2.0)
                    candidates.append((cost, di, tid))

        candidates.sort(key=lambda x: x[0])
        assigned_dets = set()
        assigned_tracks = set()
        result = [None] * len(detections)

        for _, di, tid in candidates:
            if di in assigned_dets or tid in assigned_tracks:
                continue
            result[di] = tid
            assigned_dets.add(di)
            assigned_tracks.add(tid)

        for di, det in enumerate(detections):
            tid = result[di]
            if tid is None:
                tid = self._next_id
                self._next_id += 1
                result[di] = tid
            self.tracks[tid] = {
                "bbox": det["bbox"],
                "last_seen": now,
            }

        for t in self.tracks.values():
            t.pop("used", None)

        return result
