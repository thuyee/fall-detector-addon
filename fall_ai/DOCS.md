# Fall AI

Xem `README.md` ở thư mục gốc repository.

Sau khi cài app, file cấu hình nằm trong thư mục `/addon_configs/<repo-hash>_fall_ai/config.yaml`.

Ví dụ:

```yaml
cameras:
  - id: phong_khach
    name: "Phòng khách"
    rtsp: "rtsp://USER:PASSWORD@CAMERA_IP:554/..."
    enabled: true
  - id: san
    name: "Sân"
    rtsp: "rtsp://USER:PASSWORD@CAMERA_IP:554/..."
    enabled: true

global:
  inference_fps: 3
  alert_fps: 5
  image_size: 416
  person_confidence: 0.45
  motion_threshold: 8.0
  motion_pixels: 250
  confirmation_seconds: 8
  cooldown_seconds: 60
  cpu_threads: 2
  fall_score_threshold: 0.72
  snapshot_on_event: true
```
