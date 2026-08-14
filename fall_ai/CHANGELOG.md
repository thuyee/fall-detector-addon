# Changelog

## 0.1.2
- Fix ONNX Runtime Opset 22 incompatibility by using an Opset 21-compatible model.
- Harden YAML parsing so `cameras:` with no entries becomes an empty list.
- Add per-camera `motion_entity` configuration.
- CPU-thread limiting for ONNX Runtime.
