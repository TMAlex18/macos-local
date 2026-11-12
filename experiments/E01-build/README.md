# E01: two-source reproducible build baseline

One diagnostic configuration has been built from the pinned portable QEMU
without macOS/VMApple, to establish working AArch64 TCG on the laptop.

QEMU source: sources/qemu-portable/ (unchanged tracked files).
QEMU diagnostic configuration: research-armvirt-only.mak.
Build dir: build/qemu-armvirt (ignored).
The full VMApple-enabled build currently fails at old Reims Cargo integration;
see docs/reports/INTEGRATION_NOTES.md.

Do not conflate diagnostic ARM virt success with bootable VMApple.


Modern Reims (different pinned QEMU tree) was also built independently.
To check its PCI graphics device *without booting any guest OS*:

  make build-reims-vulkan
  make build-reims-qemu-x86
  make reims-pci-realize

The last command uses Python subprocess + QMP on a paused headless q35 VM
with Linux memfd-backed RAM, and verifies PCI 106b:eeee and clean quit.
It does not exercise real guest display commands or macOS.

## Current ARM build dependency

The modern ARM graphics and BDIF restore build scripts require Nettle >=3.7.3
development files (`nettle-dev` on this Ubuntu host). Install with:

```sh
sudo apt-get install --no-install-recommends nettle-dev
```

Both scripts explicitly select `--enable-nettle --disable-gcrypt
--disable-crypto-afalg`, and update an existing build through Meson reconfigure
with a cleared dependency cache when the required backend is absent. A missing
backend fails the build workflow. `runs/qemu-bdif-usb-build.json` records the
selected backend version, configuration header and device-profile hashes.

Earlier automatic dependency detection allowed a QEMU binary with only the
cipher stub. A self-authored VMApple AES user-key probe then observed KEY
acceptance followed by DATA failure and an unconsumed FLAG. The original
failure and selected pre-rebuild configuration are retained in
[`runs/e06-aes-user-key/20261023T191925.134225Z/results.json`](../../runs/e06-aes-user-key/20261023T191925.134225Z/results.json).
Enabling a backend is a build requirement; actual AES DMA/IRQ and signed Apple
boot behavior are validated separately.
