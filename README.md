# Fall AI for Home Assistant

CPU-conscious multi-camera fall detection for Home Assistant OS.

- YOLO pose inference only while a configured HA motion entity is ON.
- Supports multiple RTSP cameras.
- Uses Home Assistant's internal API through the Supervisor proxy.
- Fires a `fall_ai_event` event in Home Assistant when a fall is confirmed.
- Keeps camera configuration in the add-on's persistent `addon_config` folder.
- Designed for CPU-only systems such as Intel NUC/i5-8250U.

> Experimental. This is not a medical or life-safety system.

## Repository layout

The repository contains the `fall_ai` app. The app's `map` includes `addon_config:rw`,
so Supervisor creates `/addon_configs/<repo-hash>_fall_ai` and mounts it as `/config`
inside the container.

Do not create a second `config.yaml` for camera settings. The camera file is
`fall_config.yaml`.

## Install

1. Replace `YOUR_GITHUB_USER` in `repository.yaml`, `fall_ai/config.yaml` and URLs if desired.
2. Push this repository to GitHub.
3. Add the repository URL in Home Assistant App Store.
4. Install/update **Fall AI** to 0.1.1.
5. Refresh `\\100.101.9.49\addon_configs`.
6. Open the new `*_fall_ai` folder and edit `fall_config.yaml`.
7. Set the two RTSP URLs and the two Home Assistant motion entity IDs.
8. Start the app and inspect the Log tab.

The first start downloads the YOLO pose model if it is not already cached. Do not run
Frigate during the first CPU-load test.
