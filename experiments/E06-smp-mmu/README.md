# E06: two-vCPU EL1 MMU / ASID / broadcast TLBI / atomics

This independent platform experiment runs only self-authored ARM64 firmware.
It is not the blocked Apple restore runner and uses no Apple binary, signature,
nonce, private guest image, kernel USB/IP, external network or host root rights.

## Run

    make smp-mmu-tests          # 2 QEMUs x 2 schedulers x 3 scenarios = 12
    make smp-mmu-extended       # 256-round positives on both MTTCG targets

Direct selection:

    python3 experiments/E06-smp-mmu/run.py --target headless --mode multi --case positive

Headless QEMU and modern Reims VMApple QEMU use exactly the same generated
firmware for a selected test case. Each has 2 vCPUs, 512 MiB shared RAM,
separate EL1 stacks/vectors and zero-filled temporary AUX/root block devices.
QMP query-cpus-fast verifies two distinct host vCPU thread IDs for multi,
or one shared host thread ID for single. No timing score is inferred.

## Positive contract

- CPU0 starts CPU1 with PSCI; each enables an actual 16 KiB stage-1 MMU.
- CPU0 edits a non-global leaf using break-before-make + VAE1IS and barriers;
  CPU1 never flushes locally between repeated accesses to that address space.
  It reads/writes the new PA and CPU0 checks the other backing frame unchanged.
- ASID 0x11 and 0x22 assign the same virtual address to different PAs. CPU1
  switches while CPU0 keeps ASID0x11 active; writes in one space must not
  alter the other. ASIDE1IS and BBM exercise ASID reuse.
- CPU0 revokes write permission; CPU1 traps eight times. ESR/EC/FSC/WnR,
  FAR and exact faulting PC are validated. Denied writes must not modify RAM;
  restored write permission must allow the following real store.
- Both CPUs execute 50,000 LDAXR/STLXR increments of a shared counter.
  Final count must be 100,000. Retry counts are observations, not fixed pass
  conditions or performance measures; actual contention can vary by scheduler.

## Negative controls

- wrong-map: leave PA_A installed when PA_B is required. The guest must report
  REMOTE_TRANSLATION_WRONG and exit1. This avoids depending on unspecified
  TLB cache retention after omitted invalidation.
- missing-ro: leave a writable PTE when a fault is required. The guest must
  report MISSING_REMOTE_PERMISSION_FAULT and exit1.

Host timeout, crash, missing expected marker, wrong thread topology or counter
is always failure, never a successful negative test.

## Boundaries

Both targets derive from QEMU, so these are not independent implementations.
QEMU may invalidate more entries than the architectural minimum; these tests
prove effective behavior, not TLB retention policy or real CPU cache timing.
The tests do not include EL0, Apple PAC, DMA/IOMMU, MMU plus IRQ plus PAC,
real macOS processes, more than two cores or long-term stress.

TLBI VAE1IS encodes VA[55:12] even for a 16 KiB granule; its lowest operand
bits are ignored/reserved appropriately. Do not use the Reims APV page shift
for ARM TLBI operands.

Sources:
https://support.arm.com/documentation/ddi0601/2023-09/AArch64-Instructions/TLBI-VAE1IS--TLBI-VAE1ISNXS--TLB-Invalidate-by-VA--EL1--Inner-Shareable
https://linux.googlesource.com/linux/kernel/git/torvalds/linux/+/aaed66fadba2d2de8fe0daa0aa3eac827d2076b9/arch/arm64/include/asm/tlbflush.h
https://www.qemu.org/docs/master/devel/multi-thread-tcg.html

Detailed current results: docs/reports/M1-SMP-MMU-RESULTS.json. Development exploratory
runs are retained separately from the final 14-scenario qualification sample.
