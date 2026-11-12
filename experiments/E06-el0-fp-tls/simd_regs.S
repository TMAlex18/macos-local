/* Explicitly preserve the q8/q9 vector state without enabling compiler use
 * of FP in any C IRQ handler. q8/q9 are 16B each; FPCR/FPSR at byte 32/40. */
.arch armv8.3-a
.text
.global fp_save_regs,fp_restore_regs
fp_save_regs:
    stp q8,q9,[x0]
    mrs x9,fpcr
    str x9,[x0,#32]
    mrs x9,fpsr
    str x9,[x0,#40]
    ret
fp_restore_regs:
    ldp q8,q9,[x0]
#ifndef SKIP_FPCR_RESTORE
    ldr x9,[x0,#32]
    msr fpcr,x9
#endif
#ifndef SKIP_FPSR_RESTORE
    ldr x9,[x0,#40]
    msr fpsr,x9
#endif
    isb
    ret
