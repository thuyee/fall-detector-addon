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

## Notifications (mobile app + Zalo)

When a fall is confirmed, the addon (no automation required):

1. Saves a snapshot JPEG from the camera's last frame.
2. Serves it over its own small HTTP server on port `8099`
   (`notifications.snapshot_port`).
3. Calls the Home Assistant `notify.*` service(s) you list under
   `notifications.mobile.services`, with the snapshot attached as `data.image`.
4. If `notifications.zalo.enabled: true`, calls the `notify.*` service you
   name under `notifications.zalo.service` the same way (it sends both an
   `image` and `photo` key in the payload data, to cover either convention).
5. Also fires an HA event `fall_ai_event` for anyone who wants to build their
   own automation in addition to / instead of the built-in notifications.

### Finding your notify service names

**Mobile app:** Settings → Devices & services → Mobile App → your device →
the service is `notify.mobile_app_<device_name>`.

**Zalo:** whatever `notify.*` service name your Zalo integration/add-on
exposes in Home Assistant (check Developer Tools → Actions, search "zalo").

### Image URL auto-detection

The snapshot server listens on the addon's own port `8099`. To build a URL
your phone (or the Zalo bot) can fetch, the addon takes the hostname from
Home Assistant's `internal_url` (Settings → System → Network) and appends
`:8099`. If that's wrong for your network (e.g. reverse proxy, VLANs), set
`notifications.base_url` explicitly, e.g.:

```yaml
notifications:
  base_url: "http://192.168.0.50:8099"
```

> Note: alerts sent while your phone is away from home need that URL to be
> reachable from outside your LAN (port-forward, VPN, or a reverse proxy to
> port 8099). On the local network it works out of the box.

## Optional: MQTT integration

Disabled by default. Set `mqtt.enabled: true` to also publish a per-camera
`binary_sensor` ("Fall AI <camera name>") via Home Assistant MQTT discovery,
turning `ON` when a fall is confirmed and back `OFF` on the next detector
reset. If the Mosquitto add-on is installed, broker credentials are
auto-discovered; otherwise set `mqtt.host` / `port` / `username` / `password`
explicitly.

## Snapshot retention

Snapshots older than `global.snapshot_retention_days` (default 7) are deleted
automatically once an hour, so `/config/snapshots` doesn't grow unbounded.
