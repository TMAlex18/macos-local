.section .text.start
.global _start
_start:
#if defined(USE_IRQ) || defined(USE_BSS_INIT)
    msr daifset, #15
    msr spsel, #1
#endif
    ldr x0, =0x70200000
    mov sp, x0
#if defined(USE_IRQ) || defined(USE_BSS_INIT)
    ldr x0, =__bss_start
    ldr x1, =__bss_end
2:  cmp x0, x1
    b.hs 3f
    str xzr, [x0], #8
    b 2b
3:
#endif
#ifdef USE_IRQ
    adr x0, guest_vectors
    msr vbar_el1, x0
    isb
#endif
    bl guest_main
1:  wfi
    b 1b

#ifdef USE_IRQ
.section .text.irq_vectors,"ax"
.balign 2048
guest_vectors:
.macro vector target
    b \target
    .balign 128
.endm
    vector guest_bad_exception
    vector guest_irq_entry
    vector guest_bad_exception
    vector guest_bad_exception
    vector guest_bad_exception
    vector guest_irq_entry
    vector guest_bad_exception
    vector guest_bad_exception
    vector guest_bad_exception
    vector guest_irq_entry
    vector guest_bad_exception
    vector guest_bad_exception
    vector guest_bad_exception
    vector guest_irq_entry
    vector guest_bad_exception
    vector guest_bad_exception

.section .text.irq_entry,"ax"
guest_irq_entry:
    /* C is compiled -mgeneral-regs-only; no SIMD state is touched. */
    sub sp, sp, #256
    stp x0, x1, [sp, #0]
    stp x2, x3, [sp, #16]
    stp x4, x5, [sp, #32]
    stp x6, x7, [sp, #48]
    stp x8, x9, [sp, #64]
    stp x10, x11, [sp, #80]
    stp x12, x13, [sp, #96]
    stp x14, x15, [sp, #112]
    stp x16, x17, [sp, #128]
    stp x18, x19, [sp, #144]
    stp x20, x21, [sp, #160]
    stp x22, x23, [sp, #176]
    stp x24, x25, [sp, #192]
    stp x26, x27, [sp, #208]
    stp x28, x29, [sp, #224]
    str x30, [sp, #240]
    bl guest_irq_handler
    ldp x0, x1, [sp, #0]
    ldp x2, x3, [sp, #16]
    ldp x4, x5, [sp, #32]
    ldp x6, x7, [sp, #48]
    ldp x8, x9, [sp, #64]
    ldp x10, x11, [sp, #80]
    ldp x12, x13, [sp, #96]
    ldp x14, x15, [sp, #112]
    ldp x16, x17, [sp, #128]
    ldp x18, x19, [sp, #144]
    ldp x20, x21, [sp, #160]
    ldp x22, x23, [sp, #176]
    ldp x24, x25, [sp, #192]
    ldp x26, x27, [sp, #208]
    ldp x28, x29, [sp, #224]
    ldr x30, [sp, #240]
    add sp, sp, #256
    eret
#endif
