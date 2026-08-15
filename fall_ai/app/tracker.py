"""Very small IoU-based tracker.

The previous implementation kept fall state keyed by the *index* of a
detection in the per-frame, confidence-sorted list. That index is not a
stable identity: if a second person appears/disappears, or two people
swap relative confidence between frames, state (like "how long has this
person been in a fall pose") silently jumps to the wrong person.

This tracker assigns a stable integer track_id to each detection by
matching bounding boxes across frames with IoU, so temporal logic in
CameraWorker can be keyed by track_id instead of list position.
"""


class Tracker:
    def __init__(self, timeout=1.5, iou_threshold=0.3):
        self.timeout = float(timeout)
        self.iou_threshold = float(iou_threshold)
        self.tracks = {}  # track_id -> {"bbox": (x,y,w,h), "last_seen": t}
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

    def update(self, detections, now):
        """detections: list of dicts with a 'bbox' key.
        Returns: list of track_ids, same length/order as detections.
        """
        # Drop tracks that haven't been seen recently.
        stale = [tid for tid, t in self.tracks.items() if now - t["last_seen"] > self.timeout]
        for tid in stale:
            del self.tracks[tid]

        used = set()
        assigned = []
        for det in detections:
            best_tid, best_iou = None, 0.0
            for tid, t in self.tracks.items():
                if tid in used:
                    continue
                v = self._iou(det["bbox"], t["bbox"])
                if v > best_iou:
                    best_iou, best_tid = v, tid

            if best_tid is not None and best_iou >= self.iou_threshold:
                tid = best_tid
            else:
                tid = self._next_id
                self._next_id += 1

            self.tracks[tid] = {"bbox": det["bbox"], "last_seen": now}
            used.add(tid)
            assigned.append(tid)

        return assigned
