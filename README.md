# Home Assistant Fall AI Add-on

AI phát hiện ngã bằng YOLO Pose + phân tích chuyển động theo thời gian, chạy trực tiếp trên Home Assistant OS.

## Mục tiêu

- Không dùng Frigate.
- Hỗ trợ nhiều camera RTSP.
- YOLO pose chỉ chạy khi camera có chuyển động.
- Giới hạn CPU bằng ONNX Runtime.
- MQTT tự động tạo entity trong Home Assistant.
- Có cooldown, xác nhận sau cú ngã và chống báo giả.
- Có thể thêm camera mới bằng cách sửa một file YAML.
- Thiết kế để sau này thay bộ luật bằng model temporal/LSTM mà không đổi phần MQTT/HA.

> Lưu ý: đây là hệ thống hỗ trợ cảnh báo, không phải thiết bị an toàn sinh mạng. Cần kiểm thử với camera thực tế trước khi tin cậy.

## Cài đặt

1. Upload repository này lên GitHub.
2. Trong Home Assistant: Settings -> Apps -> App store -> menu `...` -> Repositories.
3. Thêm URL repository GitHub.
4. Cài `Fall AI`.
5. Chưa Start ngay. Mở thư mục `/addon_configs/.../config.yaml` bằng File editor.
6. Sửa RTSP của các camera.
7. Start app.

## Cấu hình camera

Ví dụ:

```yaml
cameras:
  - id: phong_khach
    name: "Phòng khách"
    rtsp: "rtsp://user:password@192.168.0.50:554/stream1"
    enabled: true

  - id: san
    name: "Sân"
    rtsp: "rtsp://user:password@192.168.0.51:554/stream1"
    enabled: true
```

Thêm camera mới chỉ cần thêm một block `cameras`.

## Cấu hình khuyến nghị cho i5-8250U

```yaml
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
```

Sau khi test ổn có thể tăng `image_size` lên 512 hoặc `inference_fps` lên 4, nhưng không nên bắt đầu cao.

## Logic phát hiện

Add-on không coi "người nằm" là ngã.

Nó tìm chuỗi:

1. Có người.
2. Thân người thay đổi góc/chiều cao nhanh.
3. Hông hạ xuống nhanh hoặc bounding box chuyển từ dọc sang ngang.
4. Sau đó tư thế nằm/ngang được duy trì.
5. Người ít chuyển động trong khoảng xác nhận.

Các trạng thái sinh hoạt như ngồi xuống, cúi người, nằm sẵn được giảm điểm.

## MQTT

Prefix mặc định:

`fall_ai/<camera_id>/...`

Các topic chính:

- `fall_ai/<id>/fall`
- `fall_ai/<id>/event`
- `fall_ai/<id>/confidence`
- `fall_ai/<id>/status`

MQTT discovery được publish tự động.

## Model

Mặc định dùng `yolo11n-pose.onnx`, model pose nano. Model được tải vào thư mục persistent của app lần đầu chạy.

Ultralytics công bố YOLO11n-pose cho pose/keypoints và hỗ trợ export ONNX; ONNX Runtime được dùng ở đây để tránh kéo theo PyTorch nặng.

## Tuning

Nếu báo giả khi ngồi xuống:
- tăng `fall_score_threshold` từ 0.72 lên 0.78
- tăng `confirmation_seconds` lên 10
- tăng `cooldown_seconds`

Nếu bỏ sót ngã:
- giảm `person_confidence` xuống 0.35
- tăng `image_size` lên 512
- tăng `inference_fps` lên 4 hoặc 5
- giảm nhẹ `fall_score_threshold`

Nên lưu video các tình huống thật/giả để tinh chỉnh.

## License

Code của addon: MIT.

Model YOLO và Ultralytics có điều khoản cấp phép riêng; xem tài liệu Ultralytics trước khi dùng cho mục đích thương mại.


## GitHub lần đầu

Bản này cố ý **không dùng image GHCR** để anh có thể test ngay bằng local build của Home Assistant. Supervisor sẽ build Docker image trên chính mini PC khi cài app. Sau khi thuật toán ổn định, có thể chuyển sang GHCR để cài/update nhanh hơn.

## Thêm camera

Không cần sửa `config.yaml` của app. Chỉ sửa file:

`/addon_configs/<repo-hash>_fall_ai/config.yaml`

Sau đó restart app. Supervisor tạo thư mục này theo cơ chế `addon_config` của Home Assistant.
