> **Language:** **English** | [简体中文](M1-EL0-SIMD32.md)

# M1: Full Q0–Q31 SIMD/FPCR/FPSR/TLS Combined Verification with MMU/ASID/PAC/IRQ

Single-core sample date: 2026-10-16; dual-core migration extension: 2026-10-23. Single-core starting commit: bd06949; the experiment lives in experiments/E06-el0-simd32 and **does not overwrite** the already-passing E06-el0-fp-tls (Q8/Q9 only) or integer-context experiments. No Apple firmware, TSS, virtual OS installation, physical USB device, or graphics workload is involved.

## Beyond Cloning the Earlier Narrow Test

Two EL0 tasks on a single ARM64 TCG vCPU run at identical user virtual addresses, mapping distinct physical data pages and user stacks via separate ASID/TTBR0 contexts; distinct PAC pointer authentication keys protect function pointers and stack return addresses; real GICv3 virtual timer PPI27 interrupts trap into EL1, switch tasks, and resume via ERET without executing a full TLBI on each switch.

Newly added: save and restore of all 32 AArch64/AdvSIMD Q0–Q31 registers (16 bytes × 32 = 512 bytes). Dedicated AArch64 assembly executes 16 STP pairs and 16 LDP pairs across all vector registers; FPCR/FPSR are saved and restored via real MRS/MSR instructions, and TPIDR_EL0 represents the live thread-local storage context. C interrupt code continues to compile with -mgeneral-regs-only to prevent the compiler from implicitly touching SIMD registers.

Both EL0 tasks populate independent data patterns across all 64 uint64 lanes of Q0–Q31; every EL0 work loop **writes all Q registers out to guest RAM and compares them byte-for-byte against its own expected contents**. During completion, the test also reads and compares the full vector state saved by EL1. FPCR/FPSR/TLS are read back and verified inside the user loop while continuing to execute the original PAC/ASID/IRQ/SVC/permission-fault paths. Negative controls deliberately omit restoring any one pair among Q0/Q1, Q16/Q17, or Q30/Q31, and each must produce an explicit guest-side data cross-contamination report rather than masking a timeout behind "test exited 1."

## Single-vCPU Results

| QEMU Profile | 12-round baseline (normal + 10 expected negatives) | 64-round extended |
|---|---:|---:|
| Portable VMApple (GPU-less) | 11/11 | 1/1 |
| Reims VMApple | 11/11 | 1/1 |
| Reims + default-off experimental PAC HVC software model | 11/11 | 1/1 |
| Standalone 24MHz exact-clock branch | 11/11 | 1/1 |

**44/44 baseline scenarios behaved as expected (4 positive + 40 expected negatives), and 4/4 extended positive cases passed.**
Each of the four extended samples delivered 64 real EL0 IRQs per task, totaling 512 IRQs and 516 context switches. On the 24MHz branch the guest reads CNTFRQ=24000000, whereas the other three profiles read 1GHz. Carried-over negative controls (wrong PAC key, data UXN removal, PPI disable, and FPCR/FPSR/TLS restore failures) remain effective, and the three newly added omitted-restore negative controls (Q0, Q16, Q31) trigger reliably.

This is a **behavioral verification of the full Q0–Q31 AdvSIMD register bank across the bounded context switches above**, not the complete architectural context of an ARM processor: SVE/SME/FPMR, FP exception injection and lazy trapping, nested IRQs, long-term stability, and real XNU remain unverified here; nor do these single-core samples cover the dual-core migration below. The QEMU builds share substantial code rather than constituting independent hardware certifications, and counted IRQs/context switches are not system performance benchmarks.

Reproduce:

    make el0-simd32-tests
    make el0-simd32-extended

Results: docs/reports/M1-EL0-SIMD32-RESULTS.json; raw UART transcripts and per-scenario outcomes remain in runs/e06-el0-simd32/.

## Cross-vCPU User Task Migration

The same fixture adds an explicit `migration.py` entry point reusing the original EL0 code, SIMD save assembly, and dual-core boot vectors. Two CPUs execute EL0 tasks concurrently; upon real PPI27 interrupts, each saves the integer frame, SP, TTBR0, Q0..Q31, FPCR/FPSR, TLS, and architectural PAuth state, then publishes completion with release/acquire ordering and atomically claims the peer's task. A single task never executes on both CPUs simultaneously, and migrations do not flush the entire TLB.

CPU identity is initialized per CPU into read-only TPIDRRO_EL0 and verified against MPIDR at exception entry; EL0 records its executing CPU after completing data checks and an actual authenticated return. Each task records non-zero checks on both CPUs, mask=3, and evidence of interrupts landing inside signed stack frames on both CPUs. QMP simultaneously verifies that MTTCG uses two host vCPU threads whereas single mode shares one thread. Both modes use the same research QEMU binary and exact 24MHz configuration and cannot be treated as independent hardware validations.

| Independent sample | Result and scope |
|---|---|
| 16-round architectural QARMA5, one window each for multi and single | Both tasks ran on both CPUs; 32 EL0 IRQs and 32 migrations per window |
| TLS, Q30/Q31, and key-restore negative controls | Explicit guest error and exit 1 in both modes; host timeouts never counted as pass |
| Tightened recheck of wrong-TTBR0 restore | Detected in both modes; alters both page-table base and ASID, so not an isolated ASID-tag negative case |
| 64-round architectural QARMA5, both modes | 32 IRQs per task per CPU, with 30–32 landing inside signed stack frames; 128 migrations per window |
| Original single-core clock-exact positive regression | 12 rounds passed; historical 44+4 samples are not claimed as rerun here |
| Explicit software key-context model migration | 16-round multi/single positives and wrong-key negatives passed in both modes; independent per-CPU init, 21 EL1 APIA bank checks per positive run |
| Original single-core reims-model positive regression | 12 rounds passed after model-init refactoring, preserving that profile's original 1GHz clock |

Per-case raw paths and scopes are recorded under `cross_vcpu_migration` in the [structured report](M1-EL0-SIMD32-RESULTS.json). Negative cases initially showed interleaved dual-CPU serial output; the fixture now uses an atomically claimed single failure reporter to emit error lines while preserving the earlier raw run logs. The results above come from separate invocations and must not be combined into one monolithic matrix.

Explicit `--pac-model` exercises the project's own software key-context model. Each CPU initializes independently and verifies the EL1 kernel bank; task restoration applies the matching diversifier without saving EL1 key readouts as user keys or logging random key values. Architectural and model build directories remain separate. This is a functional verification of the project's model and does not prove Apple hardware or cryptographic equivalence.

Reproduce architectural migration: `make el0-migration-tests`; extended: `make el0-migration-extended`; explicit model: `make el0-migration-model-tests`. This verifies bounded context behavior for two synthetic tasks across two vCPUs and does not establish real OS multicore scheduling, SVE/SME/FPMR, full floating-point arithmetic exceptions, nested IRQs, DMA/IOMMU, long-term stability, or performance conclusions, nor does it resolve M2 platform root callbacks.

## Restoring FP State on First Access

`--lazy-fp` is a standalone architectural QARMA5 mode mutually exclusive with `--pac-model`. After each task migration, CPACR_EL1.FPEN=01 allows EL1 FP operations while trapping the first EL0 FP/SIMD access. Each of the 35 trappable instructions records its own address into x24 via a dedicated ADR immediately beforehand; the trap handler verifies EL0 origin, EC=7, syndrome, and exact equality between ELR and x24, restores the full Q/FP state, and preserves ELR to retry the exact faulting instruction. Merely checking that "ELR falls on some FP instruction" is insufficient to pass.

Each CPU also tracks whether live FP state belongs to the current task. When another IRQ arrives before the task has touched FP, the handler preserves the task's already-saved state rather than writing stale registers from an earlier task back from the CPU. A lazy-specific integer preamble actively waits for the first PPI27 interrupt, ensuring this branch executes in practice on both CPUs rather than relying on code inspection alone.

One 16-round positive window each for multi and single passed: 15 exact traps and restores per CPU, 7/8 FP traps per task on each CPU, and final EL0 mask=3. Both omitted-restore negative cases failed via full EL0 data comparison with guest exit 1 and no host timeout; only the fault-injection branch disables the local CPU timer so the data mismatch reports ahead of the next EL1 owner check. Raw records from the initial negative runs caught early by the owner check are preserved and are not cited as final data-comparison evidence.

Entry point: `make el0-migration-lazy-fp-tests`. Per-case raw paths are in `cross_vcpu_migration.lazy_fp` of the [structured report](M1-EL0-SIMD32-RESULTS.json). Recompiled firmware binaries for the default eager and model modes match prior inputs byte-for-byte; their runtime matrices were not repeated in this pass. This result does not cover floating-point arithmetic exceptions, SVE/SME, a real OS, or combining lazy mode with the software key model.
