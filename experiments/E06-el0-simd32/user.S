/* Self-authored EL0 ALL 32 FP/SIMD/TLS variant with signed indirect call and
 * signed stack return repeatedly interrupted/resumed through EL1. */
.arch armv8.3-a
.text
.global enter_first_user
enter_first_user:
    msr sp_el0,x1
    msr elr_el1,x0
    msr spsr_el1,xzr
    isb
    eret

/* The eager expansion is unchanged. Lazy builds independently tag every
 * actual FP instruction address before it executes; x24 is otherwise unused. */
.macro fp_op instruction:vararg
#ifdef LAZY_FP
    adr x24,.Lfp_exact\@
.Lfp_exact\@:
#endif
    \instruction
.endm

.section .user.text,"ax"
.global user_entry,user_kernel_read,user_code_write,user_after_xn
.global signed_body_start,signed_body_end
user_entry:
    ldr x20,=0xa0004000
    ldr x19,[x20]                 /* immutable private owner identifier */
    mov x22,#0x1729              /* same modifier for both tasks */
    adr x23,signed_target
    mov x9,x23
    pacia x23,x22
    cmp x23,x9
    b.eq user_pac_disabled
    str x23,[x20,#24]
    /* Expected pattern is in two qwords for each 128bit SIMD register,
       intentionally different for the two ASIDs/tasks. */
    ldr x9,[x20,#48]
    msr tpidr_el0,x9
#ifdef LAZY_FP
    /* Exercise a real EL0 IRQ before the first FP access on each task.
       This does not delay IRQ delivery or synthesize an FP trap. */
.Llazy_irq_first:
    ldr x9,[x20,#8]
    cbnz x9,.Llazy_irq_seen
    mrs x9,cntvct_el0
    ldr x10,[x20,#32]
    cmp x9,x10
    b.lo .Llazy_irq_first
    mov x0,#3
    svc #0x7f
.Llazy_irq_seen:
#endif
    add x26,x20,#0x200
    fp_op ldp q0,q1,[x26,#0]
    fp_op ldp q2,q3,[x26,#32]
    fp_op ldp q4,q5,[x26,#64]
    fp_op ldp q6,q7,[x26,#96]
    fp_op ldp q8,q9,[x26,#128]
    fp_op ldp q10,q11,[x26,#160]
    fp_op ldp q12,q13,[x26,#192]
    fp_op ldp q14,q15,[x26,#224]
    fp_op ldp q16,q17,[x26,#256]
    fp_op ldp q18,q19,[x26,#288]
    fp_op ldp q20,q21,[x26,#320]
    fp_op ldp q22,q23,[x26,#352]
    fp_op ldp q24,q25,[x26,#384]
    fp_op ldp q26,q27,[x26,#416]
    fp_op ldp q28,q29,[x26,#448]
    fp_op ldp q30,q31,[x26,#480]

    ldr x10,=0xa000c000
user_kernel_read:
    ldr x9,[x10]                 /* EL0 read of EL1-only page must fault */
    ldr x10,=0xa0000000
user_code_write:
    str xzr,[x10]                /* user code is read-only */
    add x10,x20,#0x100
    blr x10                     /* data contains RET, but is UXN */
user_after_xn:
    svc #0x10                   /* EL1 verifies all three faults happened */
user_loop:
#ifdef SMP_MIGRATION
    /* A wrong address-space restore must be noticed by the EL0 task itself. */
    ldr x9,[x20]
    cmp x9,x19
    b.ne user_wrong_owner
#endif
    /* Actually write back all 32 vector registers on every iteration.
       First compare all 64 words to the independent task-owned expected
       page. The compare touches general purpose regs, never clobbers Qs. */
    add x26,x20,#0x400
    fp_op stp q0,q1,[x26,#0]
    fp_op stp q2,q3,[x26,#32]
    fp_op stp q4,q5,[x26,#64]
    fp_op stp q6,q7,[x26,#96]
    fp_op stp q8,q9,[x26,#128]
    fp_op stp q10,q11,[x26,#160]
    fp_op stp q12,q13,[x26,#192]
    fp_op stp q14,q15,[x26,#224]
    fp_op stp q16,q17,[x26,#256]
    fp_op stp q18,q19,[x26,#288]
    fp_op stp q20,q21,[x26,#320]
    fp_op stp q22,q23,[x26,#352]
    fp_op stp q24,q25,[x26,#384]
    fp_op stp q26,q27,[x26,#416]
    fp_op stp q28,q29,[x26,#448]
    fp_op stp q30,q31,[x26,#480]
    add x26,x20,#0x200
    add x27,x20,#0x400
    mov x28,#64
simd32_cmp:
    ldr x9,[x26],#8
    ldr x10,[x27],#8
    cmp x9,x10
    b.ne user_bad_fp
    subs x28,x28,#1
    b.ne simd32_cmp
    /* Actual EL0 TLS/FPCR/FPSR observations, not synthetic logs. */
    mrs x9,tpidr_el0
    ldr x10,[x20,#48]
    cmp x9,x10
    b.ne user_bad_tls
    add x9,x20,#96
    fp_op stp q8,q9,[x9]
    ldp x9,x10,[x20,#96]
    ldp x11,x12,[x20,#64]
    cmp x9,x11
    ccmp x10,x12,#0,eq
    b.ne user_bad_fp
    ldp x9,x10,[x20,#112]
    ldp x11,x12,[x20,#80]
    cmp x9,x11
    ccmp x10,x12,#0,eq
    b.ne user_bad_fp
    fp_op mrs x9,fpcr
    ldr x10,[x20,#128]
    cmp x9,x10
    b.ne user_bad_fp
    fp_op mrs x9,fpsr
    ldr x10,[x20,#136]
    cmp x9,x10
    b.ne user_bad_fp
    ldr x9,[x20]
    cmp x9,x19
    b.ne user_wrong_owner
    blraa x23,x22                /* authenticate before real branch */
#ifdef SMP_MIGRATION
    /* CPU identity is CPU-local, read-only at EL0, and is never migrated as
       task state. These stores occur after SIMD/TLS checks and PAC return. */
    mrs x9,tpidrro_el0
    cmp x9,#1
    b.hi user_wrong_owner
    add x10,x20,#0x600
    ldr x11,[x10,x9,lsl #3]
    add x11,x11,#1
    str x11,[x10,x9,lsl #3]
    ldr x11,[x10,#16]
    cmp x11,x9
    b.eq 3f
    cmp x11,#2
    b.eq 3f
    ldr x11,[x10,#24]
    add x11,x11,#1
    str x11,[x10,#24]
3:  str x9,[x10,#16]
#endif
    ldr x9,[x20,#8]
    ldr x10,[x20,#40]
    cmp x9,x10
    b.hs user_done
    mrs x9,cntvct_el0
    ldr x10,[x20,#32]
    cmp x9,x10
    b.lo user_loop
    mov x0,#3
    svc #0x7f                   /* bounded guest failure, not host timeout */
user_bad_fp:
    mov x0,#7
    svc #0x7f
user_bad_tls:
    mov x0,#8
    svc #0x7f
user_wrong_owner:
    mov x0,#1
    svc #0x7f
user_pac_disabled:
    mov x0,#5
    svc #0x7f
user_done:
    svc #0x20
1:  b 1b

signed_target:
    paciasp
    stp x29,x30,[sp,#-16]!
    mov x29,sp
signed_body_start:
    ldr x9,=100000
2:  subs x9,x9,#1
    b.ne 2b
    ldr x9,[x20,#16]
    add x9,x9,#1
    str x9,[x20,#16]
signed_body_end:
    ldp x29,x30,[sp],#16
    autiasp
    ret
    .ltorg
