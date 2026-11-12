> **Language:** **English** | [简体中文](M1-EL0-CONTEXT.md)

# M1: Combined EL0 / MMU / PAuth / IRQ Context Verification

**Date: 2026-10-15 (UTC) | Starting commit: d5f037d.** Preserves the prior docs/RESULTS consolidation, the reports directory, and changes for Variant-based restore identity selection, explicit preflight checks, and reuse of iBEC reference artifacts.

## Execution Boundaries

Affected restore preflight checks were rerun locally: 33 iBEC checks, 7 signing checks, and 15 EP0 adapter checks, totaling 55 passing checks. A normal signed restore was then attempted via the existing run-ibec entry point; the invocation was blocked by the tool safety check, producing no process/job or new session log, and a read-only check found no new QEMU process. Neither switching executors, rewriting the rejected operation, nor asking the user for a manual bypass was attempted.

Execution continued instead with an independent M1 self-authored payload involving no Apple firmware, TSS, USB attachment, system-disk modification, or newly added dependencies. All new code lives exclusively under `experiments/E06-el0-context/`, reusing the existing E06 boot vectors/runtime without modifying QEMU or Reims.

## More Than Stitching Isolated Micro-Tests Together

A single vCPU alternates execution between two EL0 tasks. Both tasks run at identical virtual addresses while mapping their own physical data pages and stacks through ASID 0x31/0x32 and separate page tables, using distinct pointer authentication keys. Timer IRQs enter EL1 from EL0; the handler saves general-purpose registers, ELR/SPSR, and SP_EL0, switches TTBR0/ASID and key state, and executes ERET to resume the other task. Task switches do not flush the entire TLB to mask ASID isolation defects.

User code first signs an actual function pointer with PACIA and repeatedly enters the function via BLRAA; the function protects its live stack return address using PACIASP/AUTIASP. The experiment requires interrupts to land inside the function body while the signed return address has been saved on the stack and not yet restored, then verifies that subsequent calls and returns continue cleanly after resumption and that owner markers and work counters in private pages never cross-contaminate.

At the same time, each EL0 task probes in sequence: reading an EL1-only mapping, writing to a read-only code page, and branching to a UXN data page. The data page contains an actual RET instruction so that undecodable data is not mistaken for execute-permission enforcement. The exception handler checks the source EL, expected instruction/FAR, and permission-fault status, advancing only past its own pre-designed probes.

## Three Profiles

| Profile | Memory / Scheduling | PAuth Key Source |
|---|---|---|
| headless | Existing GPU-less VMApple, 1 vCPU, 2 tasks | Self-authored EL1 program writes architectural key registers |
| reims | Existing Reims VMApple, 1 vCPU, 2 tasks | Same as above; device initialization coexists with context tests |
| reims-model | Same as above with local PAC model explicitly enabled | EL0 diversifier from existing HVC reference model; checks EL1 bank restoration on IRQ/SVC/permission faults |

All profiles explicitly select `max,pauth-qarma5=on` rather than QEMU's default non-cryptographic impdef algorithm. **QARMA5 is the architectural algorithm; the HMAC key derivation in the third mode remains this project's reference model, not an equivalent implementation of Apple's proprietary cipher/KDF.**

## Results and Negative Controls

Each of the three profiles ran four scenarios — normal, wrong-key restore, UXN removal, and PPI disable — totaling 12 cases, plus an extended positive run of 64 IRQs/task across all three profiles, totaling 15 cases: **6 positive cases + 9 expected negative cases, all behaving as expected.**

| Condition | Expected behavior | Observed |
|---|---|---|
| Normal switching | 12 or 64 IRQs per task; signed call/return continues after resume | All 6 positive runs exited 0; counters, private memory, and distinct signature checks passed |
| Restoring wrong key/diversifier | Must reject prior signatures | All three profiles raised an actual authentication exception, ESR_EL1=0x72000000, and exited 1 |
| Marking data page executable | Former UXN probe no longer faults; test must detect missing fault | All three profiles reported the missing expected permission exception and exited 1 |
| Leaving GIC PPI27 disabled | No EL0 timer preemption; must not silently count as pass | All three profiles failed via bounded guest wait and exited 1 rather than hitting a host timeout |

Counting only the 6 final positive runs: 456 EL0 timer interrupts, 462 task context switches, 36 expected permission exceptions, and 24 SVCs; among these, 449 IRQs observed a PC inside the function body carrying a signed stack return address. Context-switch counts include voluntary scheduling triggered by task-completion SVCs, so not all 462 switches are claimed as timer preemptions.

Totals correspond to the two final run batches in this report and must not be summed with earlier development runs or prior SMP results. The profiles share substantial QEMU code and do not constitute three independent hardware validations.

### Two Test-Code Issues During Development

In the initial build, struct assignment was lowered to `memcpy` in the absence of `libc`; this was replaced with explicit field-by-field copying matching the existing vector layout. The model-only build subsequently triggered `-Werror` due to an unused direct-key helper, resolved by compiling that helper only under the corresponding architectural configuration. Both were issues in the newly written test code rather than QEMU platform defects, and neither is counted toward the final 15 passing cases.

## Current Conclusions and Limitations

EL0/PAC and EL1/MMU/IRQ — previously verified separately — have now jointly undergone userspace preemption and restoration with live stacks and isolated address spaces, without modifying the existing emulator implementation. What is verified is two integer tasks on a single vCPU rather than a full kernel or multicore EL0 scheduler.

FP/SIMD/SVE, TLS, nested IRQs, additional vCPUs, DMA/cache coherency, combining the 24MHz clock with these contracts, real Apple KDF/HVC semantics, and macOS remain unverified. Loop counts and startup wall-clock times are not treated as performance benchmarks. M1 continues; M2 has no new milestone deliverable in this round.

## Reproduction and Records

    cd /workspace/macos-local
    make el0-context-tests
    make el0-context-extended

Results: `M1-EL0-CONTEXT-RESULTS.json`, listing the two final run logs, source-file SHA-256 digests, and expected/actual exit codes and counters for every scenario. Raw logs remain in `runs`, build artifacts in `build`, and temporary disk images used `TemporaryDirectory` and were cleaned up.

Continuing the established documentation split: current status updates `PLAN-STATE` and generates `STATUS`, deliverables add a single row to `RESULTS`, and detailed reports reside in `reports`; per-experiment running logs are no longer appended to `MASTER-PLAN`.

References: QEMU CPU features documentation and `docs/system/arm/cpu-features.rst` in the pinned QEMU commit; architectural PAuth background follows the official Linux `arm64/pointer-authentication` documentation. Concrete runtime evidence remains the output of the local self-authored payload.
