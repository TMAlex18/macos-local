> **Language:** [English](README.en.md) | **简体中文**

# E05-usbip-kernel（公开快照子集）

本目录在 v1 公开研究快照中导出供 [`E05-installed-boot`](../E05-installed-boot/README.md) 与平台测试共享的 QEMU/QMP 生命周期会话（`firmware_session.py`）、存储保留与恢复校验（`validate-storage.py`、`source_log_check.py`）、VirtIO 网络/声卡运行辅助（`run-user-network.py`、`run-virtio-sound.py`、`pcm_boundary_trace.py`）及对应的纯离线单元测试（`test-qmp-lifecycle.py`、`test-storage-retention.py`、`test-real-control.py`、`test-virtio-usb-control.py`、`test-pcm-boundary.py`）。
