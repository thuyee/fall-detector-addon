# Fall AI

## CPU-safe operation

Inference is only performed when the configured Home Assistant motion sensor is `on`.
With two cameras and `inference_fps: 3`, this is intentionally much lighter than
running continuous inference on both RTSP streams.

## Adding another camera

Add another item under `cameras`:

```yaml
- id: camera_3
  name: "Camera 3"
  rtsp: "rtsp://USER:PASSWORD@IP:554"
  motion_entity: "binary_sensor.xxx_motion"
  enabled: true
```

Restart Fall AI.

## Fall confirmation

The detector looks for a person pose changing toward a horizontal/ground-level
configuration and requires the condition to remain confirmed for the configured
`confirmation_seconds`. A cooldown prevents repeated alerts.

This is an experimental computer-vision detector; tune the thresholds for the
actual camera angle and environment.
