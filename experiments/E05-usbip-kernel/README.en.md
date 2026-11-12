> **Language:** **English** | [简体中文](README.md)

# E05-usbip-kernel (Public Snapshot Subset)

In the v1 public research snapshot, this directory exports the shared QEMU/QMP session manager (`firmware_session.py`), storage retention and log checks (`validate-storage.py`, `source_log_check.py`), VirtIO network/sound helpers (`run-user-network.py`, `run-virtio-sound.py`, `pcm_boundary_trace.py`), and corresponding offline unit suites (`test-qmp-lifecycle.py`, `test-storage-retention.py`, `test-real-control.py`, `test-virtio-usb-control.py`, `test-pcm-boundary.py`) used by [`E05-installed-boot`](../E05-installed-boot/README.md).
