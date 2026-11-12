> **Language:** **English** | [简体中文](M1-SMP-MMU.md)

# M1: Dual-Core Address Spaces, Cross-Core TLB Invalidation, and Atomic Updates

Date: 2026-10-15 (UTC). Baseline commit: 90c01b7. Current master plan: [MASTER-PLAN.en.md](../MASTER-PLAN.en.md); structured results: M1-SMP-MMU-RESULTS.json.

## Execution Scope and Unfinished M2 Work

This round first attempted the existing normal signed iBEC entry point. `job_start` was blocked by the execution-layer safety check, creating no job ID and no target log; switching executors to retry was not attempted. This round produced 0 new Apple firmware sessions, 0 TSS requests, and 0 bytes of iBEC upload.

Execution proceeded instead with standalone M1 platform work: **self-authored ARM64 firmware, existing QEMU binaries, and blank temporary AUX/root disk images**. No Apple firmware or tickets were read, no USB/IP connection was made, and no host permissions, kernel modules, or QEMU source files were modified. None of this counts toward iBEC/Stage2 boot deliverables.

## Why PSCI CPU_ON Alone Is Insufficient

Earlier experiments confirmed that the second core boots and signals the first core via shared RAM, which does not imply that address-space switching, cross-core mapping invalidation, permission revocation, or atomic concurrency remain sound once the MMU is enabled.

Here both vCPUs independently enter EL1, configure separate stacks and exception vectors, and enable the 16KiB-granule CPU MMU. CPU0 updates page tables while CPU1 performs live memory accesses; host-side userspace scripts neither write test outcomes into RAM nor synthesize exceptions on the guest's behalf.

These are CPU translation page tables rather than Reims APV page numbers. The TLBI VAE1IS operand encodes VA[55:12]; using a 16KiB granule does not shift the operand to VA>>14, following the architectural ARM register definition.

## Empirically Tested Contracts

### 1. Cross-Core Break-Before-Make and VAE1IS

CPU1 first accesses VA 0xa0000000 so the mapping is actively exercised. While peer accesses pause, CPU0 marks the PTE invalid, executes DSB ISHST, ASID-qualified VAE1IS, and DSB ISH/ISB, then writes the new PTE and matching barriers.

CPU1 accesses the VA again within the same context without any local TLBI operation. The test alternates mappings back and forth between physical pages 0x70200000 and 0x70204000, verifying on every round the read value, the updated target page, and the untouched alternate page: 32 rounds per baseline scenario and 256 rounds per extended scenario.

### 2. ASID Isolation and Reuse

Two page tables under ASID 0x11/0x22 map the identical VA to distinct physical pages using non-global leaf descriptors. CPU1 switches TTBR0 contexts while CPU0 retains its original ASID; every round confirms that writes from one context never land in the other address space. ASIDE1IS participates in remapping and ASID reuse before switching back to the original ASID to confirm expected contents.

QEMU may flush additional TLB state during TTBR switches or maintenance operations. This test verifies externally observable translation correctness without claiming minimal invalidation scope or real-CPU TLB retention policies.

### 3. Cross-Core Permission Revocation, Exceptions, and Recovery

CPU1 first reads and writes an accessible mapping; CPU0 then changes it to EL1 read-only and broadcasts invalidation. CPU1 executes a known store instruction while the exception handler verifies EC = same-EL Data Abort, FSC = level-3 permission fault, WnR = write, FAR, and the exact ELR.

Each positive scenario produces 8 real CPU1 permission faults with ESR 0x9600004f and FAR 0xa0000000, leaving the target physical page unmodified by the rejected store. After restoring write permission and invalidating, subsequent stores must succeed.

### 4. True Dual-Thread Execution and Atomic Updates

The harness explicitly selects TCG thread=single/multi and verifies via QMP query-cpus-fast while paused: single-threaded mode shares 1 host thread ID, whereas multi-threaded mode reports 2 distinct vCPU host thread IDs.

Each core performs 50,000 LDAXR/STLXR increments, requiring an exact final sum of 100,000. Exclusive-store retry counts are also recorded: all final multi-threaded positive runs observed non-zero retries while preserving the exact final count. Because retry counts depend on host scheduling and emulation internals, they are not used as fixed assertions or throughput metrics.

## Controls and Results

| Scope | Scenarios | Outcome |
|---|---:|---|
| 2 QEMU builds × single/multi × 32-round positive | 4 | Guest exited 0; all contracts satisfied |
| Same matrix, deliberately retaining old physical mapping | 4 | Guest exited 1, accurately reporting REMOTE_TRANSLATION_WRONG |
| Same matrix, deliberately omitting read-only protection | 4 | Guest exited 1, accurately reporting MISSING_REMOTE_PERMISSION_FAULT |
| Both QEMU builds, multi mode extended 256 rounds | 2 | Guest exited 0; all contracts satisfied |

**All 14 final scenarios behaved as expected: 6 positive + 8 expected negative cases, 0 unexpected failures.** Earlier runs during development remain under runs and are not summed with the final sample set.

Across the 6 positive scenarios combined: 640 cross-core remapping rounds, 640 ASID isolation rounds, 48 remote permission faults, and 600,000 atomic increments with verified final totals.

Negative controls are constructed via deliberate PTE misconfigurations rather than assuming "omitting TLBI must preserve stale cached translations" as a universal rule, since the architecture does not guarantee TLB retention. Host timeouts, crashes, missing phase markers, or wrong totals never count as passing negative controls.

## Verification Boundaries

- Identical per-scenario firmware binaries compare the two QEMU builds and both threading modes.
- Both QEMU builds share substantial code and are not two independent hardware implementations; physical ARM hardware comparison remains absent.
- Coverage spans EL1, two vCPUs, and bounded address ranges; it does not cover joint EL0/PAC/IRQ interaction, IOMMU/DMA, more than two cores, or long-running real-OS workloads.
- Latency, frame rate, CPU throughput, and energy consumption are not measured; fast test execution cannot be extrapolated into native performance claims.
- No defects requiring QEMU or Reims source changes were found; this round delivers expanded test coverage and reproducible experiments rather than fabricated fixes.

## Reproduction and Locations

    cd /workspace/macos-local
    make smp-mmu-tests
    make smp-mmu-extended

Source: experiments/E06-smp-mmu; generated code and scratch files: build/e06-smp-mmu; per-scenario UART/host error logs and full results: runs/e06-smp-mmu.

Both normal completion and host-timeout paths reap the test QEMU instance and clean up self-created temporary files without terminating unrelated processes.

## References

ARM VAE1IS operand encoding and scope:
https://support.arm.com/documentation/ddi0601/2023-09/AArch64-Instructions/TLBI-VAE1IS--TLBI-VAE1ISNXS--TLB-Invalidate-by-VA--EL1--Inner-Shareable

Linux ARM64 TLBI wrappers (pinned commit):
https://linux.googlesource.com/linux/kernel/git/torvalds/linux/+/aaed66fadba2d2de8fe0daa0aa3eac827d2076b9/arch/arm64/include/asm/tlbflush.h

Per-vCPU threads, cross-core TLB synchronization, and atomic operations in QEMU MTTCG:
https://www.qemu.org/docs/master/devel/multi-thread-tcg.html
