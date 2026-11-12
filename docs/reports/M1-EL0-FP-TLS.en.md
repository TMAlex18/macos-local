> **Language:** **English** | [简体中文](M1-EL0-FP-TLS.md)

# M1 — Combined EL0 FP/SIMD, FPCR/FPSR, TLS, and MMU/PAuth/Timer Preemption Experiment

2026-10-16; this experiment duplicates the existing E06-el0-context into an **independent directory**, experiments/E06-el0-fp-tls/, as a fixed comparison baseline while leaving the original historical test untouched. Two EL0 tasks execute on the same ARM64 TCG vCPU. No Apple firmware, TSS, guest operating system, physical USB device, or host system disk is involved.

## Actual Test Mechanism

Both tasks share identical virtual addresses backed by separate ASID/TTBR0 contexts and distinct physical data and stack pages. Real GIC PPI27 timer interrupts drive EL0->EL1->EL0 rotation while preserving all prior PACIA/BLRAA/PACIASP/AUTIASP, permission-fault, and SVC checks verbatim. On top of that foundation, the test adds:

- Userspace SIMD registers Q8/Q9, with each task holding distinct 128-bit values; each EL0 loop iteration first stores the live Q registers back to its own data page and compares them against its expected bytes.
- Userspace TPIDR_EL0 carrying distinct TLS cookies, read via MRS and compared on every iteration.
- FPCR configured with distinct valid rounding-control values and FPSR seeded with distinct cumulative exception status bits; EL0 reads both registers back to verify independent save and restore.
- Explicit EL1 state save/restore routines in a standalone simd_regs.S implemented with real stp/ldp q8/q9 and mrs/msr fpcr/fpsr instructions; all C interrupt handlers are compiled with -mgeneral-regs-only so the compiler does not touch SIMD registers behind the scenes. Negative controls that disable restoration cannot produce false-green results.
- Per-round ASID switches do not execute full-TLB invalidations (TLBI), avoiding masking isolation bugs by flushing the TLB.

## Results and Controls

| Profile | 12-round baseline: normal + 7 expected negatives | 64-round extended positive |
|---|---:|---:|
| GPU-less VMApple | 8/8 | 1/1 |
| Reims ARM VMApple | 8/8 | 1/1 |
| Reims + experimental PAC HVC model | 8/8 | 1/1 |
| Standalone exact-clock research build, 24 MHz | 8/8 | 1/1 |

Baseline total: **32/32** (4 positive + 28 expected guest failures); extended total: **4/4**.
Across the extended positive runs, 512 real EL0 timer interrupts and 516 task switches occurred in total. Negative controls include deliberately omitting SIMD, FPCR, FPSR, or TPIDR_EL0 restoration — requiring the guest itself to detect state cross-contamination — alongside the carried-over negative cases for wrong PAC key, data UXN removal, and disabling PPI27.

This is a behavioral verification of **selected registers** and does not represent full floating-point context coverage across Q0..Q31/SVE/SME. Multi-vCPU EL0 scheduling, nested IRQs, FP lazy trapping, and long-duration stability remain uncovered, nor does this represent Apple's PAC KDF or complete XNU hardware semantics.

The fourth profile explicitly selects `cntfrq=24000000,x-cntfrq-exact=on`; guest MRS CNTFRQ reads 24,000,000 Hz while the other three profiles read 1 GHz, and all profiles execute the identical userspace FP/TLS preemption test. **This is neither full multicore exact-clock validation nor GUI performance validation.**

Raw per-case UART logs and exit codes are in runs/e06-el0-fp-tls/, with the sanitized summary in M1-EL0-FP-TLS-RESULTS.json.

Reproduce:

    make el0-fp-tls-tests
    make el0-fp-tls-extended
