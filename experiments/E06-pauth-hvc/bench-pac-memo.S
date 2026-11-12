// Self-authored fixed-loop workloads. UART exposes a final accumulator so
// off/on runs can be checked before comparing whole-process timings.
    .text
    .globl _start
_start:
    ldr x0, =0x40400000
    mov sp, x0
    mrs x0, sctlr_el1
    orr x0, x0, #(3 << 30)
    msr sctlr_el1, x0
    isb
    ldr x4, =ITERATIONS
    mov x9, #0
1:
#ifdef PACGA_STREAM
    mov x0, x4
    mov x1, #0x1937
    pacga x10, x0, x1
    eor x9, x9, x10
#else
#ifdef STREAM_MODIFIER
    add sp, sp, #16
#endif
    bl leaf
#endif
    subs x4, x4, #1
    b.ne 1b
    // Print the 64-bit accumulator, without exposing any key or input tuple.
    ldr x11, =0x09000000
    mov x12, #60
2:
    lsrv x13, x9, x12
    and x13, x13, #15
    cmp x13, #10
    add x14, x13, #48
    add x15, x13, #87
    csel x14, x14, x15, lo
3:
    ldr w15, [x11, #0x18]
    tbnz w15, #5, 3b
    str w14, [x11]
    subs x12, x12, #4
    b.ge 2b
    mov w14, #10
    str w14, [x11]
    ldr x0, =0x84000008
    hvc #0
4:  b 4b

leaf:
#ifdef WITH_PAC
    pacibsp
    add x9, x9, #1
    retab
#else
    add x9, x9, #1
    ret
#endif
