# Fall AI v0.1.2

## Motion sensors
Each camera may specify:
`motion_entity: binary_sensor.example`

The entity is logged and reserved for HA event/MQTT gating. This release also keeps an image-difference motion gate so the addon works without a direct HA API connection.

## Opset
The bundled model must be exported with ONNX Opset 21 or lower. v0.1.2 validates this during build/runtime.

## CPU
Start with:
- inference_fps: 3
- image_size: 416
- cpu_threads: 2
