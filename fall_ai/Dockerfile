FROM python:3.11-slim

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL   io.hass.version="${BUILD_VERSION}"   io.hass.type="app"   io.hass.arch="${BUILD_ARCH}"

ENV PYTHONUNBUFFERED=1     PIP_NO_CACHE_DIR=1     YOLO_CONFIG_DIR=/data/ultralytics

RUN apt-get update && apt-get install -y --no-install-recommends     ffmpeg     libglib2.0-0     libgl1     libsm6     libxext6     libxrender1     curl     ca-certificates     bash     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY fall_config.example.yaml /app/fall_config.example.yaml
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
