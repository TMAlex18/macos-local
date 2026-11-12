> **Language:** **English** | [简体中文](M1-FIRMWARE-TIMEBASE-MAINTENANCE.md)

# M1 Firmware Samples, DeviceTree, and 24MHz Timebase Contract: Maintenance Record

Recorded date: 2026-10-14 (UTC). This document pins down evidence already observed; exact-clock fix results are recorded separately rather than claimed prematurely here. Master Plan remains v3 and stage remains M1.

## Acquired Apple Components (Not Executed)

Five small IM4P files — DeviceTree, LLB, iBoot, iBSS, and iBEC — were retrieved via HTTP Range requests from the pinned candidate 27.0.1/26A434 Apple CDN IPSW in a single 1,760,833-byte payload transfer, without downloading the entire 26,637,307,067-byte IPSW. Raw files reside exclusively under private/official-restore-research/27.0.1-26A434 and are excluded from Git.

ZIP CRCs, byte lengths, and local SHA256 digests were verified. These checks do not constitute Apple signature verification, full-archive digest verification, or proof of successful boot. Static IM4P/LZFSE parsing identifies mBoot-20457.1.29; neither iBoot nor LLB can substitute for the still-unacquired AVPBooter. AUX storage, machine identity, an installed system disk, and the Linux restore chain remain open.

Artifacts: runs/e05-source-audit/im4p-small-firmware-audit.json. The extraction script is an explicit networked operation; DeviceTree and MMIO comparisons proceed offline once materials are present.

## Direct DeviceTree Observations

Decompressing the DeviceTree from the same candidate yields 61,208 bytes; recursive parsing recovers 139 nodes and 1,272 properties, consuming the buffer to the exact byte, and all 32 CPU template nodes declare timebase-frequency=24,000,000Hz. Having 32 CPU template nodes does not mean the system runs 32 vCPUs at runtime.

Child addresses under arm-io translate through its 0x10000000 ranges base. Compared against effective MMIO windows in the default Reims QEMU instance, three nodes — avp-ctrr, avp-sealed-registers, and avp-rtc — have no corresponding device mappings. Declaring an address does not prove that drivers access it unconditionally; conversely, a larger DT reservation window paired with a smaller QEMU register range is not automatically a bug.

Artifacts: runs/e05-source-audit/27-vmapple-dtree-contracts.json and 27-adt-vs-qemu-mtree.json. Default-off placeholder RTC/CTRR experiments are tracked separately; returning zero and ignoring writes does not implement these two device classes, and sealed-registers remains unimplemented.

## Evidence and Mechanism of the 24MHz Drift

In the pinned QEMU source, gt_cntfrq_period_ns truncates the per-tick period to integer nanoseconds. The ideal period at 24MHz is 125/3 nanoseconds; the legacy implementation uses 41ns, producing an actual frequency of roughly 24.3902439MHz while CNTFRQ still reports 24MHz. Predicted guest-time / host-time = 125/123 ≈ 1.01626016.

Across the prior round of three frequency groups with 3 guest-counter wait samples each, medians were: default 1GHz ~0.9999, 24MHz ~1.0161, and 25MHz ~0.9998. Raw UART observations rely on the host monotonic clock and include observation and scheduling jitter; they are neither precision hardware timing nor application performance measurements.

Artifacts: runs/e06-contracts-clock-rate.json; clock.c, run-clock.py. That baseline report is preserved rather than overwritten by new experiments.

Counter reads cannot be altered in isolation: timer deadlines must share the exact mathematical time domain, rounding tick deadlines upward to nanoseconds so early callbacks do not rearm in the past and spin in a tight loop. WFIT/WFET and event-stream paths must also be audited, with explicit saturation bounds for long durations and extreme deadlines.

## Scope for Continued Execution

Implement a default-off exact-clock experiment first while keeping the legacy 24MHz algorithm as a negative control. The goal is to align actual elapsed rate with the declared 24MHz without modifying the Apple DeviceTree or altering the host clock. Verification covers integer-arithmetic boundaries, guest counter rates, timer/GIC retriggering, combined MMU+timer execution, and wait-instruction behavior.

Even if passing, this fix does not certify the macOS time subsystem, OEM HVC services, or full ARM architectural compliance; real Apple boot materials and platform contracts still require independent progress.
