FROM python:3.12-slim-bookworm

ARG BUILD_VERSION="0.1.0"
ARG BUILD_ARCH="amd64"

LABEL   io.hass.version="${BUILD_VERSION}"   io.hass.type="app"   io.hass.arch="${BUILD_ARCH}"   org.opencontainers.image.title="Home Assistant App: Fall AI"   org.opencontainers.image.description="CPU-efficient YOLO pose fall detection for Home Assistant"   org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1     PYTHONDONTWRITEBYTECODE=1     OMP_NUM_THREADS=2     OPENBLAS_NUM_THREADS=2     MKL_NUM_THREADS=2     NUMEXPR_NUM_THREADS=2

RUN apt-get update && apt-get install -y --no-install-recommends       ffmpeg       libglib2.0-0       libgl1       libgomp1       curl     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

COPY app /app
COPY config.example.yaml /app/config.example.yaml
COPY run.sh /run.sh
RUN chmod +x /run.sh

WORKDIR /app
CMD ["/run.sh"]
