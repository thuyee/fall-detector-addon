"""YOLO11n-pose ONNX model wrapper and fall-scoring heuristic."""
import logging
import math
import os

import cv2
import numpy as np
import onnx
import onnxruntime as ort

LOG = logging.getLogger("fall_ai.model")

MODEL_PATH = "/config/models/yolo11n-pose.onnx"
MAX_SUPPORTED_OPSET = 21


def check_opset(path):
    model = onnx.load(path, load_external_data=False)
    versions = [x.version for x in model.opset_import if x.domain in ("", "ai.onnx")]
    if not versions:
        raise RuntimeError("Model has no ai.onnx opset")
    opset = max(versions)
    LOG.info("YOLO ONNX opset=%s", opset)
    if opset > MAX_SUPPORTED_OPSET:
        raise RuntimeError(
            f"Wrong YOLO model: Opset {opset}. This addon requires Opset <= {MAX_SUPPORTED_OPSET}."
        )


class PoseModel:
    def __init__(self, size=416, threads=2, path=MODEL_PATH):
        self.size = int(size)
        self.path = path
        if not os.path.exists(self.path):
            raise FileNotFoundError(self.path)
        check_opset(self.path)
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, int(threads))
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            self.path,
            sess_options=so,
            providers=["CPUExecutionProvider"],
        )
        self.input_name = self.session.get_inputs()[0].name
        LOG.info("ONNX model loaded: %s", self.path)
        LOG.info("ONNX providers: %s", self.session.get_providers())

    def infer(self, frame):
        img = cv2.resize(frame, (self.size, self.size))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))[None, ...]
        outputs = self.session.run(None, {self.input_name: img})
        return outputs[0]


def decode_pose(output, image_size, conf_threshold, max_people):
    arr = np.asarray(output)
    if arr.ndim == 3:
        arr = arr[0]
    if arr.shape[0] < arr.shape[1]:
        arr = arr.T

    if arr.shape[1] < 56:
        return []

    people = []
    for row in arr:
        conf = float(row[4])
        if conf < conf_threshold:
            continue

        x, y, w, h = map(float, row[:4])
        k = np.asarray(row[5:56], dtype=np.float32).reshape(17, 3)

        # Coordinates are in the exported model's input (letterboxed) space.
        k[:, 0] = np.clip(k[:, 0] / image_size, 0, 1)
        k[:, 1] = np.clip(k[:, 1] / image_size, 0, 1)

        bw = max(1e-6, w / image_size)
        bh = max(1e-6, h / image_size)
        people.append({
            "confidence": conf,
            "bbox": (x / image_size, y / image_size, bw, bh),
            "keypoints": k,
        })

    people.sort(key=lambda p: p["confidence"], reverse=True)
    return people[:max_people]


def pose_fall_score(person):
    """
    Heuristic score in [0, 1]:
      - horizontal bounding box (wide vs tall)
      - torso approaching horizontal (shoulder-hip line angle)
      - shoulders/hips/ankles spread horizontally
      - sufficient visible keypoints

    Deliberately conservative; combined with temporal confirmation logic
    in CameraWorker before an alert is fired.
    """
    k = person["keypoints"]
    vis = k[:, 2]
    good = vis > 0.35
    if int(np.count_nonzero(good)) < 6:
        return 0.0

    # COCO keypoints: 5/6 shoulders, 11/12 hips, 15/16 ankles.
    def mid(a, b):
        if good[a] and good[b]:
            return (k[a, :2] + k[b, :2]) / 2.0
        return None

    shoulder = mid(5, 6)
    hip = mid(11, 12)

    _, _, bw, bh = person["bbox"]
    aspect = bw / max(0.01, bh)
    aspect_score = np.clip((aspect - 1.05) / 1.5, 0, 1)

    torso_score = 0.0
    if shoulder is not None and hip is not None:
        dx = float(hip[0] - shoulder[0])
        dy = float(hip[1] - shoulder[1])
        angle_from_horizontal = abs(math.degrees(math.atan2(dy, dx)))
        angle_from_horizontal = min(angle_from_horizontal, 180 - angle_from_horizontal)
        torso_score = np.clip(1.0 - angle_from_horizontal / 75.0, 0, 1)

    visible_x = k[good, 0]
    visible_y = k[good, 1]
    pose_w = float(np.max(visible_x) - np.min(visible_x))
    pose_h = float(np.max(visible_y) - np.min(visible_y))
    spread_score = np.clip((pose_w / max(0.02, pose_h) - 1.1) / 2.0, 0, 1)

    return float(
        0.40 * aspect_score
        + 0.35 * torso_score
        + 0.25 * spread_score
    )
