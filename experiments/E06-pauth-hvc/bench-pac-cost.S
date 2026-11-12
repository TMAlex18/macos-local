// Self-authored EL1 cost probe: ITERATIONS calls to one leaf whose prologue and
// epilogue are either PACIBSP/RETAB (the arm64e function shape) or a plain RET.
// Exits through PSCI SYSTEM_OFF so the host times the whole QEMU process.
    .text
    .globl _start
_start:
    ldr     x0, =0x40400000
    mov     sp, x0
    mrs     x0, sctlr_el1
    orr     x0, x0, #(3 << 30)          // EnIA | EnIB
    msr     sctlr_el1, x0
    isb
    ldr     x4, =ITERATIONS
    mov     x9, #0
1:
    bl      leaf
    subs    x4, x4, #1
    b.ne    1b
    ldr     x0, =0x84000008             // PSCI SYSTEM_OFF
    hvc     #0
2:  b       2b

leaf:
#ifdef WITH_PAC
    pacibsp
    add     x9, x9, #1
    retab
#else
    add     x9, x9, #1
    ret
#endif
