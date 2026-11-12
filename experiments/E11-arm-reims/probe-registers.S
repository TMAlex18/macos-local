/* Guest CPU -> QEMU MMIO -> Reims Rust device 32-bit write/read.
 * Sets only version and trace marker, not the root FIFO.
 */
.section .text
.global _start
_start:
    ldr x20, =0x20010000
    ldr x21, =0x30201034 /* guest binary-version register */
    ldr x22, =0x30201038 /* inert trace marker */
    ldr w8, [x21]
    cbnz w8, failure
    mov w8, #245
    str w8, [x21]
    ldr w9, [x21]
    cmp w9, #245
    b.ne failure
    mov w8, #0x1234
    str w8, [x22]
    ldr w9, [x21]
    cmp w9, #245
    b.ne failure
    adr x1, success_message
    bl uart_puts
    mov x0, #0x18
    adr x1, success_exit
    hlt #0xf000
failure:
    adr x1, failure_message
    bl uart_puts
    mov x0, #0x18
    adr x1, failure_exit
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
failure_exit:
    .quad 0x20026
    .quad 1
success_message:
    .asciz "REIMS ARM TCG MMIO RW PASS\n"
failure_message:
    .asciz "REIMS ARM TCG MMIO RW FAIL\n"
