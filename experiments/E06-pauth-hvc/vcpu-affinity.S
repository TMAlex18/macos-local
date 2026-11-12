// Self-authored diskless 8-vCPU execution check; no timing oracle.
    .text
    .global _start
_start:
    adr x20, results
    stp xzr, xzr, [x20]
    stp xzr, xzr, [x20, #16]
    stp xzr, xzr, [x20, #32]
    stp xzr, xzr, [x20, #48]
    dsb sy
    mov x19, #1
1:  ldr x0, =0xc4000003       // PSCI CPU_ON, 64-bit entry point
    mov x1, x19
    adr x2, secondary
    mov x3, x19
    hvc #0
    cbnz x0, failed
    add x19, x19, #1
    cmp x19, #8
    b.ne 1b
    mov x0, #0
    bl compute
    mov x19, #0
2:  add x21, x20, x19, lsl #3
    ldar x0, [x21]
    cbnz x0, 3f
    wfe
    b 2b
3:  bl print_hex
    add x19, x19, #1
    cmp x19, #8
    b.ne 2b
    ldr x0, =0x84000008       // PSCI SYSTEM_OFF
    hvc #0
4:  b 4b

secondary:
    bl compute
1:  wfe
    b 1b

compute:
    mrs x4, mpidr_el1
    and x4, x4, #255
    cmp x4, x0
    b.ne failed
    add x1, x0, #1
    mov x2, #4096
    mov x3, #0
1:  add x3, x3, x1
    subs x2, x2, #1
    b.ne 1b
    adr x4, results
    add x4, x4, x0, lsl #3
    stlr x3, [x4]
    sev
    ret

print_hex:
    ldr x10, =0x09000000
    mov x11, #60
1:  lsrv x12, x0, x11
    and x12, x12, #15
    cmp x12, #10
    add x13, x12, #87
    add x12, x12, #48
    csel x12, x12, x13, lo
2:  ldr w13, [x10, #0x18]
    tbnz w13, #5, 2b
    str w12, [x10]
    subs x11, x11, #4
    b.ge 1b
    mov w12, #10
    str w12, [x10]
    ret

failed:
    mov x0, #-1
    bl print_hex
    ldr x0, =0x84000008
    hvc #0
1:  b 1b

    .bss
    .balign 64
results:
    .skip 64
