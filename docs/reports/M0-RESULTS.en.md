> **Language:** **English** | [简体中文](M0-RESULTS.md)

# M0 preparation / early M1 bring-up — 2026-10-14

> **Historical experiment snapshot: Numerical values and next steps correspond only to the recorded point in time. For current status see [STATUS.en.md](../STATUS.en.md); for the master plan see [MASTER-PLAN.en.md](../MASTER-PLAN.en.md).**

The authoritative operational summary is docs/STATUS.md; this file is a
concise record of confirmed experiment outcomes.

| Experiment | Result | Verified boundary |
| --- | --- | --- |
| E01 portable QEMU ARM virt | PASS | AArch64+TCG executable from pinned source |
| E04 AArch64 UART | PASS | Self-authored firmware, PL011 serial, exit=0 |
| E06 VMApple | PASS | Local x86_64 TCG machine, firmware, MMIO config 1/2/4 CPUs |
| E06 PSCI | PASS | HVC CPU_ON, secondary CPU executes and signals via shared RAM |
| E02 Intel Vulkan compute | PASS | 256 calculated u32 values, host readback, zero errors |
| E02 NVIDIA Vulkan compute | PASS | Same independent result |
| E02 Intel Vulkan render | PASS | Offscreen RGBA8 triangle and known pixels |
| E02 NVIDIA Vulkan render | PASS | Same independent result |
| E03 capability query | COMPLETE | Import/export capabilities reported, not successful interchange |
| E03 DMA_BUF host mapping | NEGATIVE | NV->Intel vkMapMemory -5, Intel->NV vkAllocateMemory -2 |
| E01 modern Reims C ABI | PASS | Rust 1.98.1, release .a/.so, rvg_ exports |
| E01 modern Reims dist | PASS | localized .a/.so, C header, pkgconfig and EFI ROM |
| E01 modern x86 QEMU | PASS | apple-gfx-pci compiled and registered |
| E01 modern PCI realization | PASS | paused q35, shared memfd RAM, QMP vendor 106b/device eeee |
| Apple ARM boot | NOT TESTED | No matching ROM/aux/root guest bundle |
| ARM VMApple + modern GPU | NOT INTEGRATED | Two tested components remain separate |
| Native-like experience | NOT TESTED | No macOS userland or UI |

Reproducible commands from project root:
  make smoke
  make gpu-external-caps gpu-external-import
  make build-reims-vulkan
  make reims-pci-realize

Source pins in sources.lock.json; patch for portable VMApple in
patches/qemu-platform; Cargo.lock source annotation patch in patches/reims.

The headless experiments do not rely on Apple code, Apple DRM services or
an ARM Mac. The binary obtained by compiling VMApple is an experimental QEMU
device model, not a macOS emulator certified compatible with current releases.

Notes: LLVM/QEMU/Cargo/Vulkan work requires several GB of derived storage;
outputs are under build/, cache/ and runs/ and are intentionally not versioned.
