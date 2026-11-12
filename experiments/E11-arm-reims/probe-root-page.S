/* Probe real Reims guest-page mapping via RootPage MMIO.
 * The chosen page number 0x1c000 encodes guest physical 0x70000000
 * only when APV_PAGE_SHIFT=14. With shift=12 it encodes 0x1c000000,
 * below VMApple's 0x70000000 guest RAM.
 */
.section .text
.global _start
_start:
    ldr x20, =0x20010000
    ldr x21, =0x70000000
    str xzr, [x21]           /* ensure first root page exists */
    dsb sy
    ldr x22, =0x3020101c    /* modern Reims RootPage MMIO */
    ldr w8, =0x1c000        /* 0x70000000 >> 14 */
    str w8, [x22]
    ldr w9, [x22]
    cmp w9, w8
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
    .asciz "REIMS ARM ROOT PAGE WRITE PASS\n"
fail_message:
    .asciz "REIMS ARM ROOT PAGE WRITE FAIL\n"
