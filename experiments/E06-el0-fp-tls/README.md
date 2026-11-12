# E06 — EL0 FP/SIMD and TLS state on a preemptive VMApple/TCG test

This is a standalone derivative of the pre-existing E06-el0-context self-authored
guest. It duplicates that experiment's pinned baseline sources rather than changing
the historical 15-case fixture. No Apple guest material, TSS, OS or host GPU.

Real EL0 code checks that TPIDR_EL0, SIMD Q8/Q9, FPCR and FPSR have distinct,
task-owned values across timer PPI27 → EL1 → distinct TTBR0/ASID and PAC key →
ERET. Physical user data and stack pages are disjoint, the virtual user VAs are
equal, and no global TLB invalidate happens per switch. C is built with
-mgeneral-regs-only; simd_regs.S explicitly saves/restores the selected two
Q registers and FPCR/FPSR. All state checks occur in guest EL0 code.

The experimental *selected registers* are NOT the full architected
FP/SIMD context (Q0..Q31), nor SVE, SME, FPMR or nested interrupts.
FPCR rounding modes and FPSR cumulative flags are distinct between tasks.
The negative cases intentionally disable restoration of one item each and
must fail in the guest; an accidental host timeout cannot count as success.

    make el0-fp-tls-tests
    make el0-fp-tls-extended

The first runs 4 QEMU profiles x 8 cases: 4 positives and 28 negative controls.
The second runs 4 positives, 64 IRQs *per* EL0 task (128 per profile).
The fourth profile is the independent opt-in exact 24MHz QEMU build; actual
EL0 CNTFRQ readback must equal 24000000Hz (other profiles report 1GHz).
Full original guest UART logs/metadata are under ignored runs/e06-el0-fp-tls.
A positive result is an isolated TCG contract, not evidence that XNU runs.
