> **Language:** [English](M1-ARM-REIMS.en.md) | **简体中文**

> **历史实验快照：数值/下一步只对应记录时点。现行状态见 [STATUS.md](../STATUS.md)，总计划见 [MASTER-PLAN.md](../MASTER-PLAN.md)。**

# M1 | ARM VMApple + Modern Reims on Linux x86_64

Project date: 2026-10-14. Experimental research state. No Apple firmware or macOS image was booted.

## Pinned provenance and isolation

- Reims parent 86d492197228df29514a87c3de62b2ce59f2188f
- Modern QEMU 1a72df984914fb9929d35b1217619178133d65a4
- metal2vulkan ecb80e019d54b8eb1585974fb2a6cc37bac359cc
- Modern Reims GPU C ABI: Linux x86_64 host, alternative guest protocols
- QEMU guest ISA: ARM64, executed via Linux TCG, not accelerated by KVM

Independent source worktrees, not the pristine checkouts:

| Research component | Source worktree | Build |
| --- | --- | --- |
| Original x86 guest Reims | sources/reims-build | build/reims-target/dist-vulkan |
| ARM64 16KiB guest Reims | sources/reims-arm-guest | build/reims-arm-target/dist-vulkan |
| Modern VMApple + 4KiB Reims | sources/qemu-reims-arm | build/qemu-reims-arm |
| Modern VMApple + 16KiB Reims | same patched QEMU source | build/qemu-reims-arm16k |

Builds and guest images are ignored in integration Git. Experimental changes
are in patches/qemu-platform/0002 and patches/reims/0002, 0003.

## Experimentally verified (not macOS compatibility)

- Modern QEMU aarch64-softmmu built on x86_64 Linux with VMApple + TCG.
  HVF-only code paths were conditionalized; Apple-specific HVC/PAuth was
  NOT implemented in the replacement path.
- Modern Reims apple-gfx-mmio and IOSurface MMIO device instantiated in paused
  VMApple with shared Linux memfd RAM; QMP read 0x30200000 and 0x30210000.
- Self-written ARM64 firmware executed VMApple configuration/UART; PSCI
  CPU_ON caused the second virtual CPU to signal through guest RAM. No Apple
  guest source, firmware, restore image or bootloader involved.
- Guest CPU reads and writes selected inert/version Reims MMIO registers.
- Original Reims APV guest page shift assumes host architecture; on
  x86_64 that yields 4 KiB even for an ARM64 guest. Experimental Cargo
  arm64-guest-pages feature sets guest APV page shift to 14 (16 KiB)
  independent of host VM 4 KiB memory allocation.
- Differential guest root-page test: page number 0x1c000 means physical
  address 0x70000000 for 16 KiB; legacy 4 KiB build attempts 0x1c000000
  outside VMApple RAM and emits a root-page-mapping warning.
- Differential FIFO test: using the same ARM64 synthetic firmware, the
  4 KiB Reims build refuses root FIFO creation (expected negative case),
  whereas 16 KiB Reims allows root FIFO enable/disable and exits 0.
- MMIO device tests, firmware and PSCI also pass with the 16 KiB staticlib.

## Further IOSurface guest/host page separation

Beyond the shared APV_PAGE_SHIFT constant, IOSurface page tables,
descriptor offsets, surface memory range size and page count were
indirectly computed from the host platform VM page (4 KiB on this host).
That is wrong where these fields describe ARM64 guest protocol pages.
The additional Reims patch 0003 substitutes guest APV_PAGE_SIZE and
APV_PAGE_SHIFT in those paths while leaving host VM allocation functions
unchanged. Its IOSurface test fixture also constructs guest-size pages
and guest-based PTEs rather than host-size ones.

A deterministic single test formerly failed because it reached
memory.map.mapping_length_unaligned before the intended failure;
now it passes when ARM64 guest-page mode is active.

## Full Reims Core suite (single observed runs, 6 test threads)

| Profile | Before IOSurface correction | After correction |
| --- | --- | --- |
| Default x86 guest pages | 2286 pass, 13 fail, 2299 total | Not yet re-run in full |
| Experimental ARM guest pages | 2237 pass, 56 fail, 2293 total | 2268 pass, 25 fail, 2293 total |

These two populations have different conditional tests. Some failures
relate to shared log capture in concurrent execution. Isolated tests
also identify real mismatches where guest page size is interpreted as
host page size. The remaining 25 cases have NOT been classified, and
the full suite has NOT passed. Do not relabel them all as harmless.

## Reproduce

From the integration project root:

    make build-reims-arm64-vulkan
    make build-reims-qemu-arm16k
    make reims-arm16k-smoke

For 4KiB-vs-16KiB differential cases:

    python3 experiments/E11-arm-reims/run-root-page.py
    python3 experiments/E11-arm-reims/run-fifo.py

The first build is host x86_64 with guest ARM64 protocol; the Linux
kernel's host memory page size is unchanged. QEMU binary configuration
is isolated by build directory.

## What remains unproven

- macOS 27 boot materials, iBoot, XNU, launchd or WindowServer
- Apple proprietary HVC/PAuth/platform semantics and storage persistence
- Complex Reims FIFO commands and actual guest-driven GPU execution
- Complete IOSurface creation, descriptor mapping, surface synchronization
- Performance, input-to-photon latency, native-like responsiveness

Next: isolate remaining Core failures, run a meaningful IOSurface descriptor
experiment with ARM64-sized guest pages, then construct a real guest FIFO
command/host readback experiment.


## v3 同步后的最新结果

此前9个串行失败属于历史快照。MappingTask fixture和remote task宿主指针对齐修复后，完整Core单测试线程重跑：ARM16K 2294/2294、x86 2300/2300，0失败；新增一项对齐回归导致测试总数各增1。准确日志在 docs/PLAN-STATE.json。

新版Reims库已重建并静态重链接QEMU；旧smoke和guest Nop/GetDeviceInfo命令回写通过，NO_DOORBELL负例符合预期。现在已经超过单纯FIFO创建，但还不是draw/compute/GUI。

补丁重建验证覆盖Reims14个修改文件、QEMU3个修改文件；这不是空白宿主全量构建证明。后续队列见MASTER-PLAN.md和生成的STATUS.md。
