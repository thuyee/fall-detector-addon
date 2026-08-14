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

# Export YOLO11n Pose during image build.
# ONNX Runtime 1.22 supports official Opset <= 21.
RUN mkdir -p /opt/models && \
    python -c "from ultralytics import YOLO; YOLO('yolo11n-pose.pt').export(format='onnx', opset=21, imgsz=416, simplify=False, dynamic=False)" && \
    cp /app/yolo11n-pose.onnx /opt/models/yolo11n-pose.onnx && \
    rm -f /app/yolo11n-pose.onnx

COPY app/main.py /app/main.py
COPY run.sh /run.sh

RUN chmod +x /run.sh

CMD ["/run.sh"]
