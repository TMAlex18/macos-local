# E06: VMApple/ARM64 headless hardware-platform experiments

This proves part of the **virtual machine and TCG implementation only**.
No Apple code or macOS image is used, and it is not evidence that modern
macOS boots, or that its Apple-specific HVC/PAuth and GPU semantics work.

## Build

From project root: make build-vmapple-headless

The pinned portable QEMU source is unchanged; a separate git worktree
sources/qemu-headless carries the patch in
patches/qemu-platform/0001-vmapple-optional-gpu.patch.
The patch makes graphics opt-out through -M vmapple,gfx-device=none.

The custom Kconfig minimal device profile is the .mak file in this directory.
Besides VMApple, it includes VIRTIO_PCI and VIRTIO_BLK because the
VMApple-specific virtio block subclass depends on their link symbols.

## Tests

- make vmapple-cfg: self-written 0x00100000 firmware reads VMApple config
  at 0x00400000 (version 2, configured vCPU count, 512 MiB RAM and
  initial CPU IDs), writes through PL011 at 0x20010000, then exits cleanly.
  Tested with -smp 1, 2 and 4.
- make vmapple-psci: CPU0 calls PSCI_CPU_ON via HVC to start CPU1, passing
  context 0x42. CPU1 checks context, writes to guest RAM at 0x70000000,
  and CPU0 observes signal and exits successfully.

Secondaries beyond CPU1, timer interrupts, guest stage-1 MMU, PAuth, Apple
proprietary hypercalls and storage correctness are **not** established.

Synthetic blank raw block files are created in build/e06-vmapple only to
fulfill the machine's AUX/root block requirements. They are NOT Apple media.
