> **Language:** **English** | [简体中文](INTEGRATION_NOTES.md)

# E01 — portable QEMU and modern Reims interface mismatch

> **Historical experiment snapshot: Numerical values and next steps correspond only to the recorded point in time. For current status see [STATUS.en.md](../STATUS.en.md); for the master plan see [MASTER-PLAN.en.md](../MASTER-PLAN.en.md).**

This is a **directly observed local build failure**, not proof that ARM TCG
or VMApple cannot run on an Intel host.

## Source pins

- Portable: imbushuo/qemu e1aa708569a0de3fbc95fa1ac1836b0ae0a9e96e
- Modern Reims: steelbrain/reims-vgpu
  86d492197228df29514a87c3de62b2ce59f2188f
- Modern Reims QEMU submodule expected:
  1a72df984914fb9929d35b1217619178133d65a4

## Actual failure

In build/qemu-portable, generated custom target was:

  cargo build --release --manifest-path
  /workspace/macos-local/sources/qemu-portable/../../Cargo.toml
  -p reims-vgpu --features backend-metal

Cargo returned: manifest path does not exist.

The portable fork's hw/display/meson.build obtains the Cargo repository by
walking two parent directories from its own source root and expects the
reims-vgpu Rust *staticlib* and legacy feature names.

There is a second API drift: the pinned modern Reims public crate defines
a "vulkan" feature, not "backend-vulkan,host-window", and its C-facing
staticlib resides in a distinct reims-vgpu-c crate. The modern QEMU uses a
pkg-config C ABI instead of the portable fork's historic glue.

Simply creating a Cargo.toml at the expected relative path would mask the
first error without resolving the real ABI incompatibility.

## Experimental separation (already working)

A custom device selection disables both VMAPPLE and REIMS_VGPU and enables
ARM_VIRT, allowing a diagnostic AArch64/TCG binary to build from the **same
portable pinned QEMU revision**. This does not change or validate VMApple.

- Profile: experiments/E01-build/research-armvirt-only.mak
- Source configuration symlink: in portable QEMU configs/devices/aarch64-softmmu
- Build tree: build/qemu-armvirt/
- Logs: runs/qemu-armvirt-{configure,build}.log

## Planned integration options

A. Keep portable VMApple/QEMU, adapt its device bridge to modern Reims C ABI.
B. Bring portable VMApple/TCG/HVC changes into pinned modern Reims QEMU.
C. Separate a no-vGPU VMApple research build first, then join graphics later.

Decide after actual diff, compile feedback and the guest-visible ABI inventory.
Do NOT label the two pinned upstreams "compatible" yet.

## New findings (2026-10-14)

- The pinned *modern* Reims QEMU submodule finally completed checkout at
  1a72df984914fb9929d35b1217619178133d65a4; no longer blocked by git.
- A **separate, intentionally narrower** VMApple research build now compiles
  from portable QEMU + a small graphics-optional patch and local device profile.
- Reims Cargo ABI integration is still unimplemented; the new worktree
  demonstrates that VMApple machine initialization/PSCI can be investigated
  independently of the modern GPU bridge.
- VMApple-specific virtio-blk needs VIRTIO_PCI and VIRTIO_BLK in a custom
  minimal build; missing either caused link failures, resolved through
  explicit device selections rather than dead code or stub linkage.
- Reims Rust 1.98.1 is installed and verified. Modern Reims C/Vulkan build,
  dist package, and modern Linux x86 QEMU apple-gfx-pci device realization
  have passed on this laptop. The ARM+Reims integration gap remains.
