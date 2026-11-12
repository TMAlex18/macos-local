/* AArch64 VMApple -> Reims guest root-FIFO handshake.
 * Host x86 software execution, guest APV page size must be 16 KiB.
 * No Apple code, no shader dispatch, no live graphics workload.
 */
.section .text
.global _start
_start:
    ldr x20, =0x20010000   /* PL011 */
    ldr x21, =0x70000000   /* root page @ main guest RAM start */
    str xzr, [x21]
    ldr x21, =0x70004000   /* FIFO page */
    str xzr, [x21]
    dsb sy

    ldr x22, =0x30201000   /* reims MMIO register window */
    ldr w8, =0x1c000       /* root page: 0x70000000 >> 14 */
    str w8, [x22, #0x1c]
    ldr w8, =0x1c001       /* FIFO page: 0x70004000 >> 14 */
    str w8, [x22, #0x30]
    mov w8, #0x4000        /* FIFO length exactly one ARM guest page */
    str w8, [x22, #0x04]
    mov w8, #0x400         /* start at aligned offset within the FIFO page */
    str w8, [x22, #0x10]
    mov w8, #1            /* FIFO enable */
    str w8, [x22, #0x00]
    ldr w9, [x22, #0x00]
    cmp w9, #1
    b.ne failure

    mov w8, #0
    str w8, [x22, #0x00] /* disable FIFO before exiting */
    ldr w9, [x22, #0x00]
    cbnz w9, failure

    adr x1, ok_message
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
ok_message:
    .asciz "REIMS ARM ROOT FIFO ENABLE PASS\n"
fail_message:
    .asciz "REIMS ARM ROOT FIFO ENABLE FAIL\n"
