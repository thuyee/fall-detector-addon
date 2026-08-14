ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apk add --no-cache ffmpeg libstdc++ libgcc

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

# Export the model during the IMAGE BUILD, not at runtime.
# Opset 21 is explicitly requested because ORT 1.22 rejects Opset 22.
RUN mkdir -p /opt/models && \
    python3 -c "from ultralytics import YOLO; YOLO('yolo11n-pose.pt').export(format='onnx', opset=21, imgsz=416, simplify=False, dynamic=False)" && \
    cp /app/yolo11n-pose.onnx /opt/models/yolo11n-pose.onnx && \
    rm -f /app/yolo11n-pose.onnx

COPY app/main.py /app/main.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
