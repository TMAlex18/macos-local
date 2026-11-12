/* Direct guest AArch64 reads of modern Reims apple-gfx-mmio IOSurface registers.
 * Synthetic firmware only; no real Metal/Apple driver protocol exercised.
 */
.section .text
.global _start
_start:
    ldr x20, =0x20010000 /* PL011 UART */
    ldr x21, =0x30200000 /* graphics MMIO */
    ldr x22, =0x30210000 /* IOSurface MMIO */
    ldr w8, [x21]        /* host device read through TCG guest memory */
    ldr w9, [x22]
    cbnz w8, failure
    mov w10, #-1
    cmp w9, w10
    b.ne failure
    adr x1, success_message
    bl uart_puts
    mov x0, #0x18
    adr x1, success_exit
    hlt #0xf000
failure:
    adr x1, fail_message
    bl uart_puts
    mov x0, #0x18
    adr x1, fail_exit
    hlt #0xf000
uart_puts:
    ldrb w2, [x1], #1
    cbz w2, 1f
    strb w2, [x20]
    b uart_puts
1:
    ret
.balign 8
success_exit:
    .quad 0x20026
    .quad 0
fail_exit:
    .quad 0x20026
    .quad 1
success_message:
    .asciz "REIMS ARM TCG MMIO READ PASS\n"
fail_message:
    .asciz "REIMS ARM TCG MMIO READ FAIL\n"
