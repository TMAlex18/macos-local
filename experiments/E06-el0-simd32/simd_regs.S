/* Explicitly preserve all Q0..Q31 512-byte vector state without enabling compiler use
 * of FP in any C IRQ handler. All Q registers are 16B each; FPCR/FPSR at bytes 512/520. */
.arch armv8.3-a
.text
.global fp_save_regs,fp_restore_regs
fp_save_regs:
    stp q0,q1,[x0,#0]
    stp q2,q3,[x0,#32]
    stp q4,q5,[x0,#64]
    stp q6,q7,[x0,#96]
    stp q8,q9,[x0,#128]
    stp q10,q11,[x0,#160]
    stp q12,q13,[x0,#192]
    stp q14,q15,[x0,#224]
    stp q16,q17,[x0,#256]
    stp q18,q19,[x0,#288]
    stp q20,q21,[x0,#320]
    stp q22,q23,[x0,#352]
    stp q24,q25,[x0,#384]
    stp q26,q27,[x0,#416]
    stp q28,q29,[x0,#448]
    stp q30,q31,[x0,#480]
    mrs x9,fpcr
    str x9,[x0,#512]
    mrs x9,fpsr
    str x9,[x0,#520]
    ret
fp_restore_regs:
#ifndef SKIP_Q0_RESTORE
    ldp q0,q1,[x0,#0]
#endif
    ldp q2,q3,[x0,#32]
    ldp q4,q5,[x0,#64]
    ldp q6,q7,[x0,#96]
    ldp q8,q9,[x0,#128]
    ldp q10,q11,[x0,#160]
    ldp q12,q13,[x0,#192]
    ldp q14,q15,[x0,#224]
#ifndef SKIP_Q16_RESTORE
    ldp q16,q17,[x0,#256]
#endif
    ldp q18,q19,[x0,#288]
    ldp q20,q21,[x0,#320]
    ldp q22,q23,[x0,#352]
    ldp q24,q25,[x0,#384]
    ldp q26,q27,[x0,#416]
    ldp q28,q29,[x0,#448]
#ifndef SKIP_Q31_RESTORE
    ldp q30,q31,[x0,#480]
#endif
#ifndef SKIP_FPCR_RESTORE
    ldr x9,[x0,#512]
    msr fpcr,x9
#endif
#ifndef SKIP_FPSR_RESTORE
    ldr x9,[x0,#520]
    msr fpsr,x9
#endif
    isb
    ret
