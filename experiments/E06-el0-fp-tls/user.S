/* Self-authored EL0 FP/SIMD/TLS variant: permission probes, signed indirect call and
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
    add x9,x20,#64
    ldp q8,q9,[x9]

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
    /* Actual EL0 observations, not a host-side emulator of SIMD state.
       Distinct TLS cookies and vector patterns are compared on every loop. */
    mrs x9,tpidr_el0
    ldr x10,[x20,#48]
    cmp x9,x10
    b.ne user_bad_tls
    add x9,x20,#96
    stp q8,q9,[x9]
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
    mrs x9,fpcr
    ldr x10,[x20,#128]
    cmp x9,x10
    b.ne user_bad_fp
    mrs x9,fpsr
    ldr x10,[x20,#136]
    cmp x9,x10
    b.ne user_bad_fp
    ldr x9,[x20]
    cmp x9,x19
    b.ne user_wrong_owner
    blraa x23,x22                /* authenticate before real branch */
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
