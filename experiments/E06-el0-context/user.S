/* Self-authored EL0 program: permission probes, signed indirect call and
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
