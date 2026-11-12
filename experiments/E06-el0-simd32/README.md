# E06 — all 32 architectural SIMD Q registers in an EL0 context switch

Independent research fixture derived from E06-el0-fp-tls, not an in-place
modification of its 36 previously passed cases. **No Apple guest code, TSS,
macOS, GUI, physical USB or uninitialised host device modifications.**

The default fixture holds two EL0 tasks on one virtual CPU, both at the same virtual user
addresses but with disjoint physical data and stacks under distinct ASIDs and
different pointer authentication keys. GIC PPI27 timer IRQ preempts into EL1;
the guest saves/restores all Q0..Q31 (512 bytes), FPCR, FPSR, TPIDR_EL0,
integer register vector frame, TTBR0 and PAuth keys, and resumes through ERET.

Every user iteration *actually* stores Q0..Q31 to its own guest-owned RAM
and compares all 64 64-bit words against independent task-specific patterns.
FPCR/FPSR and TPIDR_EL0 also compare in EL0. The SIMD save/restore routines
are explicit AArch64 assembly. All C/IRQ code is compiled
-mgeneral-regs-only; no unnoticed compiler-generated use of NEON registers.

No blanket TLBI during ASID switches. The guest continues original actual
PACIA/BLRAA and PACIASP/AUTIASP, RO/UXN/EL1-only faults, SVC and preemptions.
The reference PAC HVC model is explicit opt-in, not Apple KDF equivalence.

    make el0-simd32-tests
    make el0-simd32-extended

Four QEMU profiles (headless, Reims, Reims PAC model, 24MHz exact clock).
Basic matrix: 4 x 11 = 44 cases (4 positives, 40 expected guest failures).
Extra negative cases disable restoration of Q0/Q1, Q16/Q17 or Q30/Q31,
verifying the first, middle and final register banks really matter. Existing
skip-full-FP, FPCR, FPSR, TLS, wrong-key, data-executable, and PPI-disabled
negative tests still fail at guest level. All negative expected_exit=1 and
distinct failure markers are required, never a host timeout.

Extended: 4 positives * 64 PPI27 timer IRQ per EL0 task (128/profile).
The 24MHz exact-clock profile explicitly reads CNTFRQ=24,000,000 in the guest,
where the other profiles read 1GHz. This is not an actual macOS boot.

Trace results in ignored runs/e06-el0-simd32; structured project evidence in
docs/reports/M1-EL0-SIMD32-RESULTS.json.

## Two-vCPU EL0 task migration

The additional entry point reuses this fixture's user code, all-Q-register
assembly, context implementation and linker layout, plus E06-smp-mmu's two-CPU
startup/vector code and the shared runtime. The original `run.py` defaults and
single-vCPU behavior remain unchanged.

```sh
python3 experiments/E06-el0-simd32/migration.py --case positive --mode all --rounds 16
python3 experiments/E06-el0-simd32/migration.py --case negative --mode all --rounds 16
python3 experiments/E06-el0-simd32/migration.py --case positive --mode all --rounds 64
```

This entry uses `build/qemu-bdif-usb/qemu-system-aarch64`, two vCPUs and the same
architectural settings as the current platform research path:
`max,pauth-qarma5=on,cntfrq=24000000,x-cntfrq-exact=on`. Each CPU reads and checks
24MHz independently. The architectural runs leave the software PAC/HVC context
model disabled; its separate opt-in checks are below. Neither path establishes
Apple cryptographic equivalence. QMP verifies two distinct vCPU host threads for MTTCG and one for
single-thread TCG. These modes share QEMU code and are not independent hardware.

In the eager architectural path, each real EL0 PPI27 interruption saves the integer exception frame, SP_EL0,
Q0..Q31, FPCR/FPSR, TPIDR_EL0, TTBR0 and the architectural APIA key pair before
publishing with STLR. The peer waits with LDAR before claiming the saved task.
An atomic owner claim prevents a task from running on two CPUs simultaneously;
ownership is released only after leaving EL0 and completing the save. Address
spaces retain distinct ASIDs and physical data/stacks, without a migration-time
blanket TLBI.

CPU identity comes from TPIDRRO_EL0, initialized once by that CPU's own startup
path and checked against MPIDR in every EL1 exception entry. It is not written
according to the scheduler's planned next task. After actual EL0 SIMD/TLS/owner
checks and authenticated call/stack return, user assembly records that CPU in
its private data page. The final report requires each task's actual EL0 CPU mask
to equal 3, checks on both CPUs, observed CPU changes, PPI27 counts on both CPUs,
and interruptions inside `signed_body` on both CPUs. Scheduler counts alone
cannot pass it. All C handlers still use `-mgeneral-regs-only`.

The negative group omits TLS or Q30/Q31 restoration, restores the wrong complete
TTBR0 (case name `wrong-asid`, meaning page-table base **and** ASID, not an isolated
ASID-tag test), or corrupts the restored APIA key. Each needs guest exit 1 and its
specific guest error marker; a host deadline, peer deadline, unrelated failure
or success marker never passes. Simultaneous expected failures use one UART
reporter, avoiding interleaved failure text from the two CPUs.

Verified on 2026-10-23:

| Scope | Result | Raw result |
|---|---|---|
| 16 rounds/task, MTTCG positive | exit 0; each task mask 3, 8 IRQs on each CPU | result |
| 16 rounds/task, single-thread positive | exit 0; each task mask 3, 8 IRQs on each CPU | result |
| Four restoration negatives × two modes | all eight guest exit 1 with the expected marker; no host timeout | result |
| Wrong complete TTBR0, stricter failure classification | both modes guest exit 1; a timer deadline is not accepted as this error | result |
| 64 rounds/task × two modes | both exit 0; each task mask 3, 32 IRQs/CPU and 64 EL0-observed CPU changes; signed-body IRQs 30..32/task/CPU | result |
| Existing single-vCPU clock-exact positive, 12 rounds/task | exit 0; 24 EL0 IRQs and six permission faults | result |

The initial MTTCG TLS negative did fail in the guest, but two UART writers
interleaved its marker; the host correctly rejected that run. Its
raw failed fixture result
is retained separately from the repaired negative matrix.

## Opt-in software key-context migration

```sh
python3 experiments/E06-el0-simd32/migration.py --pac-model --case positive --mode all --rounds 16
python3 experiments/E06-el0-simd32/migration.py --pac-model --case wrong-key --mode all --rounds 16
```

`--pac-model` explicitly enables the existing project-owned
`x-pac-software-model=on` property on the same research binary and 24MHz CPU.
Default architectural execution and the original single-CPU runner remain
unchanged. Model builds use `build/e06-el0-simd32/migration-model-<case>-<rounds>`;
architectural builds use `migration-<case>-<rounds>`, so they cannot overwrite
each other's firmware inputs. Model runs cover only positive and wrong-key
cases; `--case all` or `negative` selects that limited model subset.

Each CPU initializes its own HVC model state. Every EL0 exception checks the
APIA key pair against that CPU's saved kernel-bank oracle. The task's model
state is the explicit, stable context parameter (`0x1100 + task`), restored
through the model API on either CPU. EL1 kernel key reads are never copied into
model user-task key state. The two tasks execute the same actual EL0 SIMD/TLS,
authenticated branch and signed-stack-return checks and CPU observations as the
architectural path. The negative restores a different context parameter and
requires an actual guest authentication failure. No random key values appear
in UART or structured output.

| Scope | Result | Raw result |
|---|---|---|
| Model, 16 rounds/task, multi + single | both exit 0; every task mask 3 and 8 IRQs/CPU; both CPUs initialized, each passed 21 actual EL1 APIA kernel-bank checks | result |
| Model wrong-key, multi + single | both guest exit 1 with `PAC_AUTH_FAILURE_AFTER_SWITCH`; no host timeout | result |

The host positive oracle also requires exactly four EL0 SVCs and
`2 * rounds` context switches. The already-recorded architectural positives
satisfy those counts too. Model validation is 16 rounds/task; it is separate
from the architectural 64-round extension and is not Apple hardware validation.

## Opt-in lazy EL0 FP/SIMD restore

```sh
python3 experiments/E06-el0-simd32/migration.py --lazy-fp --case positive --mode all --rounds 16
python3 experiments/E06-el0-simd32/migration.py --lazy-fp --case skip-lazy-fp --mode all --rounds 16
```

This mode is architectural QARMA5 only and rejects `--pac-model`. `--case all`
selects just the lazy positive and missing-restore negative. Its
`migration-lazy-<case>-<rounds>` build directories cannot overwrite eager or
model firmware. Both scheduling modes still use the same two-vCPU 24MHz binary.

The contract was checked against the fixed QEMU primary source at
`1a72df984914fb9929d35b1217619178133d65a4` before implementation:
[FPEN decoding](https://github.com/steelbrain/qemu-reims-vgpu/blob/1a72df984914fb9929d35b1217619178133d65a4/target/arm/helper.c#L10258),
[A64 FP trap generation](https://github.com/steelbrain/qemu-reims-vgpu/blob/1a72df984914fb9929d35b1217619178133d65a4/target/arm/tcg/translate-a64.c#L1435),
[syndrome construction](https://github.com/steelbrain/qemu-reims-vgpu/blob/1a72df984914fb9929d35b1217619178133d65a4/target/arm/syndrome.h#L348),
and [exception PC handling](https://github.com/steelbrain/qemu-reims-vgpu/blob/1a72df984914fb9929d35b1217619178133d65a4/target/arm/tcg/translate.c#L1064).
FPEN `01` traps EL0 access while allowing EL1 saves/restores; `11` permits both.
The actual A64 exception must have EC `0x07`, IL set and the low ISS fields zero.
FP access uses `pc_diff=0`, and A64 exception entry saves the current PC as ELR.
These source cross-checks describe this emulator contract, not independent
hardware certification.

At migration the integer/SP/TLS/MMU/key context is restored, but the incoming
FP state remains in its task buffer and EL0 FP access is disabled. A per-CPU
loaded/owner record identifies whether CPU FP registers belong to the current
task. If an IRQ arrives before that task uses FP, the save retains its valid
memory FP state instead of copying the previous task's CPU registers over it.
The lazy-only user prelude deliberately waits for its first real PPI27 before
its first FP instruction, exercising this path on each CPU without delaying IRQ
delivery or synthesizing a trap. Final saves that reuse memory are counted
separately from the explicit `IRQ_BEFORE_FP` observations.

On the first real FP access, the handler verifies EL0 origin, CPACR, ESR and
the precise instruction address. A lazy-only assembly macro puts an independent
`ADR x24,<this instruction>` before every potentially trapping user FP operation;
the vector preserves x24 across migration. ELR must equal both that tagged
address and ELR_EL1. This distinguishes an erroneous four-byte advance even
among adjacent FP operations. LLVM disassembly independently confirms all 35
FP sites have the exact preceding ADR. The handler restores all Q0..Q31 and
FPCR/FPSR, permits EL0 FP, and returns with the identical ELR to retry; all
existing actual EL0 state comparisons and authenticated returns remain active.

| Scope | Result | Raw result |
|---|---|---|
| Lazy positive, 16 rounds/task, multi + single | both exit 0; each CPU has 15 exact EC7 traps and restores; each task/CPU has 7 or 8; both task masks 3; each CPU observes one IRQ before first FP use | result |
| Lazy missing-restore negative, multi + single | both guest exit 1 with `LAZY_FP_CONTEXT_RESTORE_MISSING`, no host timeout | result |

The positive still has 32 actual EL0 timer IRQs, four SVCs and 32 scheduler
context switches per mode. EL0-observed CPU changes are 12/task in MTTCG and
14/task in single-thread TCG; they are actual observations, not aliases for the
scheduler count. All tasks retain actual checks and signed-body IRQs on both CPUs.
The negative skips a post-migration lazy restore only when CPU FP ownership is
different, then stops that CPU's timer so the EL0 data comparison reports the
injected stale state first. Initially an intervening IRQ let the EL1 owner guard
catch the injected fault before the intended EL0 marker; that
rejected fixture result
is retained and is not counted as a passing negative.

As an isolation check, rebuilding eager architectural and software-model
positive firmware after adding the lazy guards produced byte-identical images
to their previously recorded 64-round/16-round inputs. No QEMU source changed.

Remaining gaps include nested IRQs, arbitrary scheduling/long stress, SVE/SME,
FPMR, isolated ASID-tag negative controls, longer model stress, combined lazy/model execution,
and real OS/DMA/GPU workloads. This self-authored M1 evidence does not
repair or prove the M2 root callback, XNU/macOS boot or a complete OS scheduler.
