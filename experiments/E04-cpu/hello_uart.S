/* Minimal AArch64 bare-metal guest: PL011 UART, QEMU virt board only.
 * This is not VMApple and does not depend on Apple firmware.
 */
.section .text
.global _start
_start:
    ldr x4, =0x09000000
    adr x1, message
1:
    ldrb w2, [x1], #1
    cbz w2, 2f
    strb w2, [x4]
    b 1b
2:
    mov x0, #0x18
    adr x1, exit_block
    hlt #0xf000
3:
    wfi
    b 3b
.balign 8
exit_block:
    .quad 0x20026
    .quad 0
message:
    .asciz "M0 ARM64 TCG UART PASS\n"
