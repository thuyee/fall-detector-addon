# ---- Stage 1: export the ONNX model (needs ultralytics + torch) ----
FROM python:3.12-slim AS export

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /export

# ultralytics pulls in torch/torchvision just to run the .pt -> .onnx export.
# This whole stage is discarded after the export, so none of that weight
# ends up in the final image.
COPY requirements-build.txt /tmp/requirements-build.txt
RUN pip install --no-cache-dir -r /tmp/requirements-build.txt

# Export YOLO11n Pose during image build.
# ONNX Runtime 1.22 supports official Opset <= 21.
RUN python -c "from ultralytics import YOLO; YOLO('yolo11n-pose.pt').export(format='onnx', opset=21, imgsz=416, simplify=False, dynamic=False)" && \
    mkdir -p /opt/models && \
    cp /export/yolo11n-pose.onnx /opt/models/yolo11n-pose.onnx

# ---- Stage 2: the actual runtime image ----
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Pull only the exported .onnx model from the export stage — no torch/ultralytics here.
COPY --from=export /opt/models/yolo11n-pose.onnx /opt/models/yolo11n-pose.onnx

COPY app/ /app/
COPY run.sh /run.sh

RUN chmod +x /run.sh

CMD ["/run.sh"]
