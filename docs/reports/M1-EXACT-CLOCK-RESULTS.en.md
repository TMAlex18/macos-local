> **Language:** **English** | [简体中文](M1-EXACT-CLOCK-RESULTS.md)

# M1: 24MHz Exact-Clock Fix Experiment

Date: 2026-10-14 (UTC); Master Plan v3, still without booting Apple firmware or macOS.

## 1. Original Defect and Fix

The pinned QEMU version truncates 1e9/24e6 nanoseconds to 41ns and uses that period to simulate both counter reads and timer deadlines. Although internally consistent, that legacy approximation causes a nominal 24MHz clock to run at roughly 24.39024MHz, making guest time advance ~1.626% faster than host time. Prior measurements had already observed ~1.6% drift.

This round implements a default-off `x-cntfrq-exact` option in an isolated worktree. Counter reads use floor(ns×Hz/1e9) and deadlines use ceil(tick×1e9/Hz), saturating to INT64_MAX when exceeding the signed QEMUTimer range; the implementation uses integer quotient/remainder arithmetic with no floating point. Reads and deadline scheduling are fixed as a pair so early callbacks cannot spin in a past-deadline loop.

Time domains for WFIT/WFET and the event stream are updated alongside: in exact mode, the event stream first computes the next absolute counter edge and then converts to nanoseconds. The legacy code path is preserved so new behavior is not forced onto existing virtual machines.

Neither the Apple DeviceTree nor the host system time is modified, nor is the guest CNTFRQ register altered to masquerade as a clock-rate fix. This remains an experimental TCG option rather than an upstream default capability.

## 2. Arithmetic and Live Guest Tests

The exact production header was validated against an independent 128-bit arithmetic oracle: 387,527 conversion inputs, 1,920 event-edge cases, and overflow boundaries all passed with UBSan/ASan enabled on the native test binary. This does not constitute sanitizer certification of the entire QEMU codebase.

All 12 functional scenarios behaved as expected: 8 positive cases, 3 negative controls expected to exit 1 in the guest, and 1 startup rejection for a >1GHz configuration. Covered cases include:

- 24MHz counter, GIC PPI27, repeated interrupts, and MMU enabled simultaneously;
- carried-over negative controls disabling PPI or omitting read-only permissions;
- WFIT/WFET absolute deadlines and immediate continuation on past deadlines;
- event stream waking ahead of timeout, WFE falling edges, and a negative control with the event stream disabled;
- 96 short-deadline firings each at 24MHz and 1GHz verifying zero early deliveries, drops, or duplicates;
- far-future UINT64_MAX deadlines verified not to overflow into immediate interrupts.

Every case runs a single-vCPU self-authored ARM64 EL1 program rather than an Apple OS. QEMU and the guest tests share implementation assumptions, which does not equate to independent physical ARM hardware certification.

## 3. Rate Comparison on the Same Binary

Each group ran 3 trials of ~750ms each, recording the guest counter and the host monotonic clock upon reading UART markers. All samples are retained without filtering; exact figures are in the standalone JSON report.

| Mode | Frequency | Median guest-time / host-time |
|---|---:|---:|
| Legacy branch | 24MHz | 1.0161359 |
| Exact branch | 24MHz | 0.9998432 |
| Legacy branch | 1GHz | 0.9997839 |
| Exact branch | 1GHz | 0.9998968 |
| Exact branch | 25MHz | 0.9997898 |
| Exact branch | 19.2MHz | 0.9998970 |

Observations confirm that the prior ~1.6% systematic rate bias no longer appears in exact mode while control groups remain near 1. Residual small offsets include host scheduling jitter and observation overhead; **0.9998432 must neither be interpreted as a constant 0.01568% guest slowness nor used to promise ppm-grade accuracy.** Loose regression thresholds serve to catch percent-scale regressions rather than define product SLOs.

## 4. Isolation, Reproduction, and Boundaries

`build/qemu-clock-exact/qemu-system-aarch64` uses upstream pin `1a72df984914fb9929d35b1217619178133d65a4` + patch0003 + patch0005, isolated from both normal mainline and headless negative-control builds. This branch excludes the placeholder AVP RTC/CTRR patch0004.

Exact mode is enabled only via `max,cntfrq=24000000,x-cntfrq-exact=on` and defaults to off. Mainline default frequencies and existing guest bundles remain unmodified. Live migration, dynamic frequency scaling, KVM, full-EL/multicore time synchronization, and the target Apple OS remain unverified.

Run:

    make build-qemu-clock-exact
    make clock-math
    make clock-functional
    make clock-rate-exact

Patch-reconstruction checks cover the newly added header so generated files need not be committed into source Git trees. `make check-research-patches` verifies that modified files can be reconstructed from pinned sources and patches; it is not a full-dependency bootstrap on a blank machine.

## 5. Documentation and Next Mainline Steps

The maintenance pass has already synchronized the five small official components, the 24MHz DeviceTree contract, missing AVP mappings, and the baseline clock-drift report, preserving the distinction between provenance/CRC checks and unverified Apple signatures. Subsequent real-boot research can use this 24MHz exact profile, though it cannot bypass AVPBooter, AUX/identity/system disk, or real Apple ABI gaps.

Priority remains on E05 matching boot materials and restore transport, while building combined tests for EL0+MMU+PAC and multicore clocking. Real GPU workloads, input latency, throughput, and whole-system power remain evaluated separately; native experience is not claimed from this clock fix.

Reference interfaces: QEMU nanosecond timer API in `include/qemu/timer.h`; Arm `CNTKCTL_EL1` event-direction definitions. Execution is grounded in locally pinned sources and logs rather than substituting live web pages for locked revisions.
