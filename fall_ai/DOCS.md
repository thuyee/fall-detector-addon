# Fall AI configuration

## 1. Find the persistent config folder

After installing/updating the app, Home Assistant creates:

`/addon_configs/<repo-hash>_fall_ai/`

The app maps this folder to `/config` inside the container.

The file used by default is:

`/addon_configs/<repo-hash>_fall_ai/fall_config.yaml`

## 2. Camera settings

Each camera needs:
- `id`
- `name`
- `rtsp`
- `motion_entity`

The motion entity is a Home Assistant binary_sensor. When it is `on`, Fall AI opens the RTSP stream and runs pose inference.

If motion turns off, inference continues for `post_motion_seconds` so a person who becomes still immediately after a fall can still be evaluated.

## 3. Events

When a fall is confirmed the app fires:

`fall_ai_event`

Example event data:

```yaml
camera_id: phong_khach
camera_name: C6N Phòng Khách
confidence: 0.83
score: 0.91
snapshot: /media/fall_ai/fall_phong_khach_2026....jpg
```

You can trigger a Home Assistant automation with:

```yaml
triggers:
  - trigger: event
    event_type: fall_ai_event
```

## 4. CPU

For an Intel i5-8250U start with:
- inference_fps: 3
- cpu_threads: 2
- frame_width: 640
- frame_height: 360

Do not run Frigate at the same time while testing this app.

## 5. Important

This is experimental fall detection. Camera angle, distance, lighting, occlusion and furniture can strongly affect accuracy.
