// Synthetic aligned-PC reuse. Two identical passes, 16 distinct tuples.
// UART prints a completion marker only; inputs and keys are never reported.
    .text
    .globl _start
_start:
    mov x3, #2
1:  mov x0, #0x1000
    mov x1, #0x2000
    mov x2, #16
2:  pacga x10, x0, x1
#ifdef WIDE_REUSE
    add x0, x0, #0x1000
#else
    add x0, x0, #0x100
#endif
    subs x2, x2, #1
    b.ne 2b
    subs x3, x3, #1
    b.ne 1b
    ldr x11, =0x09000000
    adr x12, message
3:  ldrb w13, [x12], #1
    cbz w13, 5f
4:  ldr w14, [x11, #0x18]
    tbnz w14, #5, 4b
    str w13, [x11]
    b 3b
5:  wfi
    b 5b
message:
    .asciz "MEMO CAPACITY DONE\n"
