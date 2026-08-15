# Fall AI

## CPU-safe operation

Inference only runs while the configured Home Assistant motion sensor is `on`.
When it's `off`, the addon still drains the RTSP buffer (so the stream doesn't
stall) but **skips frame decoding entirely** — it only calls `grab()`, not
`read()` — so no CPU is spent on JPEG/H264 decode or the pose model while
nothing is moving.

## Adding another camera

Add another item under `cameras` in `/config/config.yaml`:

```yaml
- id: camera_3
  name: "Camera 3"
  rtsp: "rtsp://USER:PASSWORD@IP:554"
  motion_entity: "binary_sensor.xxx_motion"
  enabled: true
```

Restart Fall AI.

## Fall confirmation

The detector looks for a person's pose changing toward a horizontal/ground-level
configuration and requires the condition to remain confirmed for
`confirmation_seconds`. Each detected person is tracked across frames with a
small IoU-based tracker (not just list position), so the "how long has this
person looked fallen" timer stays attached to the correct person even with
multiple people in frame. A cooldown (`cooldown_seconds`) prevents repeat
alerts for the same camera.

This is an experimental computer-vision detector; tune the thresholds for
the actual camera angle and environment. **It is not a medical or
life-safety system.**

## Notifications (mobile app + Zalo) — required setup

When a fall is confirmed, the addon (no automation required) saves a
snapshot and calls Home Assistant services directly to alert your phone
and/or Zalo.

### 1. Mount Home Assistant's real config folder

The addon's own persistent storage (`/config` inside the addon, mapped via
`addon_config:rw`) is **isolated** from Home Assistant Core's actual config
directory — it is not the same `/config` your automations and `www/` folder
live in. To publish images that HA Core (and the `zalo_bot` integration,
which runs inside HA Core) can read, this addon's manifest adds:

```yaml
map:
  - addon_config:rw
  - homeassistant_config:rw
```

This mounts your real HA config directory into the addon read/write.
**This grants the addon access to your entire HA config, including
`secrets.yaml`.** It's the standard technique many add-ons use to publish
files under `www/`, but you should be aware of the scope before enabling
it. If you'd rather not grant this, set `notifications.enabled: false` and
build your own automation off the `fall_ai_event` event instead (see below).

Snapshots are written to `<your HA config>/www/fall_ai/<file>.jpg`, which
Home Assistant serves at `http://<your-ha>:8123/local/fall_ai/<file>.jpg`.

### 2. Mobile app push notifications

List the `notify.*` service name(s) for the phone(s) you want alerted:

```yaml
notifications:
  mobile:
    enabled: true
    services:
      - notify.mobile_app_dien_thoai_cua_a
```

Find the exact name at Settings → Devices & services → Mobile App → your
device. The snapshot is attached via `data.image: "/local/fall_ai/<file>.jpg"`,
a path the companion app resolves against your configured HA server URL —
no manual host/IP configuration needed.

### 3. Zalo (via the zalo_bot integration)

This calls `zalo_bot.send_message` and `zalo_bot.send_image` directly —
the same actions your own automations already use — with:

```yaml
notifications:
  zalo:
    enabled: true
    thread_id: "1058896116335801995"       # same as your existing automations
    account_selection: "+84868837123"      # same as your existing automations
    msg_type: "1"
    ttl: 0
    send_text: true     # sends the alert message via zalo_bot.send_message
    send_image: true     # sends the snapshot via zalo_bot.send_image
```

`image_path` sent to `zalo_bot.send_image` is
`/config/www/fall_ai/<file>.jpg` — this is HA Core's own view of the file
(not the addon's), which is why step 1 (mounting `homeassistant_config:rw`)
is required for this to work.

### If you don't want to grant homeassistant_config:rw

Set `notifications.enabled: false`. The addon still fires an HA event
`fall_ai_event` (with `camera_id`, `camera_name`, `score`, `snapshot`,
`timestamp`) on every confirmed fall — you can build your own automation
around that event and your own snapshot delivery method instead.

## Optional: MQTT integration

Disabled by default. Set `mqtt.enabled: true` to also publish a per-camera
`binary_sensor` ("Fall AI <camera name>") via Home Assistant MQTT discovery,
turning `ON` when a fall is confirmed and back `OFF` on the next detector
reset. If the Mosquitto add-on is installed, broker credentials are
auto-discovered; otherwise set `mqtt.host` / `port` / `username` / `password`
explicitly.

## Snapshot retention

Snapshots older than `global.snapshot_retention_days` (default 7) are
deleted automatically once an hour, so `www/fall_ai/` doesn't grow
unbounded.
