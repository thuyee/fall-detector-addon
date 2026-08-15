# Fall AI cho Home Assistant

Hệ thống phát hiện người bị ngã sử dụng AI/YOLO Pose, được thiết kế tối ưu CPU cho Home Assistant OS.

Fall AI hỗ trợ:

- Phát hiện ngã bằng YOLO Pose.
- Nhiều camera RTSP.
- Sử dụng cảm biến chuyển động của Home Assistant làm điều kiện kích hoạt phân tích.
- Theo dõi người bằng tracker để hạn chế nhầm trạng thái giữa nhiều người.
- Xác nhận ngã theo thời gian và điểm tin cậy.
- Tự động chụp ảnh khi phát hiện ngã.
- Lưu ảnh trực tiếp vào thư mục `www` của Home Assistant.
- Gửi thông báo lên ứng dụng Home Assistant.
- Gửi thông báo Zalo bằng integration `zalo_bot`.
- Gửi ảnh phát hiện ngã trực tiếp qua `zalo_bot.send_image`.
- Tự động xóa ảnh cũ theo thời gian lưu trữ.
- MQTT tùy chọn.
- Có cơ chế cooldown để tránh gửi cảnh báo liên tục.
- Chạy CPU hiệu quả trên các máy Home Assistant cấu hình thấp.

> ⚠️ **Lưu ý:** Đây là hệ thống thử nghiệm hỗ trợ phát hiện ngã, không phải thiết bị y tế hoặc hệ thống an toàn tính mạng.

---

# Tính năng chính

## 1. Phát hiện ngã bằng AI

Fall AI sử dụng mô hình YOLO Pose để phân tích tư thế người trong hình ảnh.

Hệ thống kết hợp nhiều thông tin như:

- Tư thế cơ thể.
- Góc thân người.
- Chuyển động của cơ thể.
- Độ thay đổi vị trí hông.
- Tỷ lệ bounding box.
- Lịch sử chuyển động.
- Theo dõi ID của từng người.

Nhờ đó hệ thống có thể phân biệt tốt hơn giữa:

```text
Đang đứng
    ↓
Mất thăng bằng
    ↓
Ngã
    ↓
Nằm trên sàn
```

thay vì chỉ phát hiện:

```text
Người đang nằm = ngã
```

---

# 2. Hỗ trợ nhiều camera

Có thể cấu hình nhiều camera RTSP.

Ví dụ:

```yaml
cameras:

  - id: phong_khach
    name: "C6N Phòng Khách"
    rtsp: "rtsp://USER:PASSWORD@IP_CAMERA:554/STREAM"
    motion_entity: "binary_sensor.c6n_e17394610_motion"
    enabled: true

  - id: san
    name: "H6C Sân"
    rtsp: "rtsp://USER:PASSWORD@IP_CAMERA:554/STREAM"
    motion_entity: "binary_sensor.h6c_bf2378127_motion"
    enabled: true
```

Mỗi camera có thể cấu hình:

- ID riêng.
- Tên camera.
- RTSP riêng.
- Cảm biến chuyển động riêng.
- Bật/tắt camera độc lập.

---

# 3. Motion Gate - chỉ xử lý khi có chuyển động

Fall AI có thể sử dụng `binary_sensor` chuyển động của Home Assistant để làm điều kiện kích hoạt.

Ví dụ:

```yaml
motion_entity: "binary_sensor.c6n_e17394610_motion"
```

Khi cảm biến chuyển động đang tắt:

```text
Motion OFF
    ↓
Không giải mã frame
    ↓
Không chạy YOLO
    ↓
Giảm tải CPU
```

Khi phát hiện chuyển động:

```text
Motion ON
    ↓
Đọc frame camera
    ↓
YOLO Pose
    ↓
Phân tích người
```

Điều này giúp giảm đáng kể mức sử dụng CPU khi khu vực camera không có hoạt động.

---

# 4. Xác nhận ngã

Fall AI không lập tức báo chỉ vì một frame có tư thế bất thường.

Sự kiện phải đạt các điều kiện xác nhận được cấu hình, ví dụ:

```yaml
confirmation_seconds: 2.5
```

Sau khi đạt điều kiện xác nhận:

```text
Nghi ngờ ngã
    ↓
Theo dõi tiếp
    ↓
Xác nhận trạng thái
    ↓
FALL
```

Sau khi phát cảnh báo, hệ thống sử dụng:

```yaml
cooldown_seconds: 60
```

để tránh gửi cảnh báo liên tục cho cùng một camera trong thời gian ngắn.

---

# 5. Tracker nhiều người

Fall AI có tracker dựa trên IoU để duy trì ID của người giữa các frame.

Điều này đặc biệt quan trọng khi trong camera có nhiều người.

Ví dụ:

```text
Người A → ID 1
Người B → ID 2
```

Nếu người A có trạng thái nghi ngờ ngã, trạng thái xác nhận sẽ tiếp tục gắn với người A thay vì chuyển nhầm sang người B.

---

# 6. Tự động chụp ảnh khi phát hiện ngã

Khi Fall AI xác nhận sự kiện FALL, hệ thống tự động chụp snapshot.

Tính năng này được bật bằng:

```yaml
snapshot_on_event: true
```

Ảnh được lưu trực tiếp vào thư mục `www` của Home Assistant:

```text
/config/www/fall_ai/
```

Ví dụ:

```text
/config/www/fall_ai/san_20260816-043900.jpg
```

Ảnh có thể được truy cập từ Home Assistant bằng:

```text
/local/fall_ai/san_20260816-043900.jpg
```

---

# 7. Gửi ảnh phát hiện ngã qua Zalo

Fall AI tích hợp trực tiếp với:

```text
zalo_bot
```

Khi phát hiện ngã, hệ thống có thể gửi:

1. Tin nhắn cảnh báo.
2. Ảnh snapshot của camera.

Tin nhắn sử dụng:

```text
zalo_bot.send_message
```

Ảnh sử dụng:

```text
zalo_bot.send_image
```

Không cần tạo automation Home Assistant trung gian để bắt MQTT rồi chụp ảnh và gửi Zalo.

Luồng xử lý:

```text
Fall AI phát hiện ngã
        ↓
Xác nhận FALL
        ↓
Chụp snapshot
        ↓
Lưu /config/www/fall_ai/
        ↓
zalo_bot.send_message
        ↓
zalo_bot.send_image
        ↓
Zalo nhận cảnh báo + ảnh
```

---

# 8. Cấu hình Zalo

Fall AI sử dụng integration `zalo_bot` đang có trong Home Assistant.

Các thông tin cấu hình sử dụng cùng giá trị với automation Zalo hiện tại:

```yaml
zalo:
  enabled: true
  thread_id: "YOUR_THREAD_ID"
  account_selection: "YOUR_ACCOUNT"
  type: "1"
```

Không đưa thông tin Zalo thật lên GitHub công khai.

---

# 9. Gửi thông báo Home Assistant

Fall AI có thể gửi thông báo tới ứng dụng Home Assistant thông qua các service:

```text
notify.mobile_app_...
```

Có thể cấu hình một hoặc nhiều thiết bị nhận thông báo.

Ví dụ:

```yaml
mobile_notify_services:
  - notify.mobile_app_dien_thoai
```

Tên service phải đúng với service đang tồn tại trong Home Assistant.

---

# 10. Tự động xóa snapshot cũ

Snapshot không được lưu vĩnh viễn.

Fall AI có cơ chế tự động dọn ảnh cũ.

Thời gian lưu trữ có thể cấu hình, ví dụ:

```yaml
snapshot_retention_days: 7
```

Có nghĩa là:

```text
Ảnh mới
   ↓
Giữ lại

Ảnh > 7 ngày
   ↓
Tự động xóa
```

Điều này giúp tránh thư mục:

```text
/config/www/fall_ai/
```

tăng dung lượng liên tục.

---

# 11. MQTT tùy chọn

Fall AI có thể xuất sự kiện qua MQTT nếu cần tích hợp với hệ thống khác.

MQTT là tùy chọn và không bắt buộc phải sử dụng để gửi Zalo.

Có thể sử dụng MQTT cho:

- Node-RED.
- n8n.
- Home Assistant automation.
- Hệ thống giám sát khác.

Nếu không sử dụng MQTT, có thể tắt tính năng này.

---

# 12. Quyền truy cập thư mục

Để Fall AI có thể lưu snapshot vào thư mục của Home Assistant, addon cần quyền:

```yaml
homeassistant_config:rw
```

Ngoài ra addon sử dụng:

```yaml
addon_config:rw
```

để lưu cấu hình và dữ liệu riêng của addon.

`homeassistant_config:rw` cho phép addon ghi snapshot vào:

```text
/config/www/fall_ai/
```

Đây là lý do snapshot có thể đồng thời được sử dụng bởi:

```text
Home Assistant
      +
zalo_bot.send_image
```

---

# Cài đặt

## Bước 1 - Cài addon

Thêm repository Fall AI vào Home Assistant.

Sau đó:

```text
Settings
    ↓
Apps
    ↓
App Store
    ↓
Fall AI
```

Cài đặt hoặc cập nhật addon.

---

## Bước 2 - Kiểm tra quyền

Mở:

```text
Fall AI
    ↓
Info
```

Kiểm tra addon có các mapping:

```text
addon_config:rw
homeassistant_config:rw
```

`homeassistant_config:rw` cần thiết để lưu snapshot vào:

```text
/config/www/fall_ai/
```

---

## Bước 3 - Mở thư mục cấu hình

Có thể sử dụng Samba/SMB hoặc phương pháp quản lý file khác để mở thư mục addon.

Ví dụ:

```text
\\HOME_ASSISTANT_IP\addon_configs
```

Tìm thư mục:

```text
*_fall_ai
```

Sau đó mở:

```text
config.yaml
```

---

# Cấu hình camera

Ví dụ:

```yaml
cameras:

  - id: phong_khach
    name: "C6N Phòng Khách"
    rtsp: "rtsp://admin:PASSWORD@192.168.0.107:554/ch1/main"
    motion_entity: "binary_sensor.c6n_e17394610_motion"
    enabled: true

  - id: san
    name: "H6C Sân"
    rtsp: "rtsp://admin:PASSWORD@192.168.0.122:554/ch1/main"
    motion_entity: "binary_sensor.h6c_bf2378127_motion"
    enabled: true
```

**Không commit mật khẩu RTSP thật lên GitHub.**

---

# Cấu hình hiệu năng

Ví dụ:

```yaml
global:

  inference_fps: 3
  alert_fps: 5

  image_size: 416

  person_confidence: 0.45

  motion_threshold: 8.0
  motion_pixels: 250

  confirmation_seconds: 2.5
  cooldown_seconds: 60

  fall_score_threshold: 0.72

  cpu_threads: 2

  snapshot_on_event: true

  snapshot_retention_days: 7
```

Các giá trị này có thể điều chỉnh tùy theo:

- CPU.
- Camera.
- Góc đặt camera.
- Khoảng cách tới người.
- Điều kiện ánh sáng.
- Mức độ nhạy mong muốn.

---

# Kiểm tra sau khi cài đặt

Sau khi cấu hình xong:

```text
Fall AI
    ↓
Start
```

Mở:

```text
Fall AI
    ↓
Log
```

Kiểm tra addon khởi động bình thường.

Khi camera có chuyển động, kiểm tra log xem hệ thống bắt đầu xử lý frame.

Khi phát hiện ngã, kiểm tra:

```text
1. Fall AI xác nhận FALL
2. Snapshot được tạo
3. File xuất hiện trong /config/www/fall_ai/
4. Zalo nhận tin nhắn
5. Zalo nhận ảnh
```

---

# Kiểm tra Zalo

Trong Home Assistant phải tồn tại hai action:

```text
zalo_bot.send_message
zalo_bot.send_image
```

Fall AI sử dụng trực tiếp hai action này.

Không sử dụng:

```text
notify.zalo
```

và không sử dụng:

```text
zalo_bot.send_photo
```

---

# Cấu trúc thông báo khi phát hiện ngã

Khi xảy ra sự kiện:

```text
🚨 PHÁT HIỆN NGÃ

📍 Camera: H6C Sân

🕐 Thời gian: 04:39:00 16/08/2026

⚠️ Fall AI đã xác nhận có khả năng người bị ngã.
```

Sau đó hệ thống gửi snapshot của chính camera phát hiện sự kiện.

---

# Xử lý lỗi

## Không có snapshot

Kiểm tra:

```text
/config/www/fall_ai/
```

và:

```yaml
snapshot_on_event: true
```

Đồng thời kiểm tra addon có:

```text
homeassistant_config:rw
```

---

## Có snapshot nhưng Zalo không nhận ảnh

Kiểm tra trong Home Assistant:

```text
Developer Tools
    ↓
Actions
    ↓
zalo_bot.send_image
```

Đảm bảo action này tồn tại và có thể gửi ảnh thủ công.

Kiểm tra đường dẫn ảnh:

```text
/config/www/fall_ai/
```

---

## Không phát hiện ngã

Có thể điều chỉnh:

```yaml
confirmation_seconds:
fall_score_threshold:
inference_fps:
image_size:
```

Không nên giảm ngưỡng quá thấp vì có thể làm tăng cảnh báo nhầm.

---

# Bảo mật

Không commit các thông tin sau lên GitHub:

```text
RTSP password
Zalo thread_id
Zalo account_selection
API key
Token
Mật khẩu
```

Đặc biệt không đưa mật khẩu camera thật vào:

```text
config.yaml.example
```

Repository công khai chỉ nên chứa cấu hình mẫu.

---

# Phiên bản

## v0.4.1

- Cải thiện hệ thống phát hiện ngã.
- Theo dõi người bằng tracker.
- Xác nhận ngã dựa trên lịch sử chuyển động.
- Hỗ trợ nhiều camera RTSP.
- Motion Gate thông qua Home Assistant binary sensor.
- Giảm tải CPU khi không có chuyển động.
- Tự động chụp snapshot khi phát hiện ngã.
- Snapshot được lưu vào `/config/www/fall_ai/`.
- Hỗ trợ truy cập snapshot bằng `/local/fall_ai/...`.
- Tích hợp trực tiếp với `zalo_bot.send_message`.
- Tích hợp trực tiếp với `zalo_bot.send_image`.
- Gửi cả cảnh báo và ảnh hiện trường qua Zalo.
- Tự động dọn snapshot cũ.
- MQTT tùy chọn.
- Có cooldown chống cảnh báo lặp.
- Hỗ trợ theo dõi nhiều người.
- YOLO Pose tối ưu cho CPU.
- Tối ưu cho Home Assistant OS.

---

> ⚠️ **Fall AI là hệ thống hỗ trợ phát hiện ngã thử nghiệm. Không nên sử dụng nó như hệ thống bảo vệ tính mạng duy nhất.**
