# Fall AI for Home Assistant

CPU-conscious multi-camera fall detection for Home Assistant OS, with
built-in mobile push and Zalo (via the `zalo_bot` integration) notifications.

## v0.3.0

- Zalo notifications now call your actual `zalo_bot.send_message` /
  `zalo_bot.send_image` actions (thread_id/account_selection/type/ttl),
  matching your existing automations — not a generic `notify.*` service.
- Snapshots are written into Home Assistant's own `www/` folder (new
  `homeassistant_config:rw` permission) so both the mobile app
  (`/local/...`) and `zalo_bot.send_image` (`/config/www/...`) can read
  them reliably, with no host/IP guessing.
- Fixed a tracking bug where fall-confirmation state could attach to the
  wrong person across frames; now uses a small IoU-based tracker.
- Reduced CPU use further: frames are no longer decoded at all while the
  configured motion sensor is off.
- Optional MQTT discovery integration (off by default).
- Automatic snapshot cleanup (retention configurable).
- Supports multiple RTSP cameras, uses Home Assistant `binary_sensor`
  motion entities as the inference gate.
- YOLO11n-pose exported at build time with ONNX Opset 21.

> Experimental. This is not a medical or life-safety system.

## Install

1. Upload/replace the repository contents on GitHub.
2. In Home Assistant, refresh the App Store/repository.
3. Update or reinstall Fall AI to 0.3.0.
4. Open the addon's Info tab and confirm both `addon_config:rw` and
   `homeassistant_config:rw` are listed under its folder mappings (needed
   for notifications — see `fall_ai/DOCS.md` for what this grants).
5. Open the persistent addon folder (e.g. via Samba/SMB):
   `\\100.101.9.49\addon_configs`
6. Open the `*_fall_ai` folder and edit `config.yaml`.
7. Add the RTSP passwords. The repository intentionally does **not**
   contain camera passwords.
8. Add your `notify.mobile_app_...` service name(s), and/or your
   `zalo_bot` `thread_id` / `account_selection` (same values your existing
   Zalo automations use).
9. Start Fall AI and check the Log tab.

The two supplied motion entities are already included in `config.yaml.example`:

- `binary_sensor.c6n_e17394610_motion`
- `binary_sensor.h6c_bf2378127_motion`

For a public GitHub repository, do not commit real RTSP passwords or Zalo
thread IDs.

See `fall_ai/DOCS.md` for full details on tuning fall detection and
configuring notifications/MQTT.
