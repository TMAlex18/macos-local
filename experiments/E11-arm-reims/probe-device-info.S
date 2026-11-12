/* Self-authored guest -> Reims FIFO -> guest RAM result roundtrip.
 * No Apple code. Commands: Nop (30) and GetDeviceInfo (58).
 * Correct completion is proven by host-written stamps AND output contents.
 */
.section .text
.global _start
_start:
    ldr x20, =0x20010000             /* UART */
    ldr x21, =0x70004000             /* FIFO backing and stamp slots */
    ldr x22, =0x30201000             /* GPU register window */
    ldr x23, =0x70008000             /* output page */
    ldr w10, =0xdeadbeef
    mov x9, #0
1:  str w10, [x23, x9]
    add x9, x9, #4
    cmp x9, #32
    b.lo 1b
    str wzr, [x21]                  /* stamp0 sentinel */
    /* Prepare NOP at FIFO start (header is <u16,u16,u32,u32>). */
    add x24, x21, #0x400
    mov w8, #30
    str w8, [x24]
    mov w8, #12
    str w8, [x24, #4]
    mov w8, #0x11
    str w8, [x24, #8]
    /* Prepare GetDeviceInfo: max_key=3, max_count=3, output page=0x1c002. */
    mov w8, #58
    str w8, [x24, #12]
    mov w8, #24
    str w8, [x24, #16]
    mov w8, #0x22
    str w8, [x24, #20]
    mov w8, #3
    str w8, [x24, #24]
    str w8, [x24, #28]
    ldr w8, =0x1c002
    str w8, [x24, #32]
    dsb sy
    mov w8, #245
    str w8, [x22, #0x34]
    ldr w8, =0x1c000
    str w8, [x22, #0x1c]
    ldr w8, =0x1c001
    str w8, [x22, #0x30]
    mov w8, #0x4000
    str w8, [x22, #4]
    mov w8, #0x400
    str w8, [x22, #0x10]
    mov w8, #1
    str w8, [x22]
    ldr w9, [x22]
    cmp w9, #1
    b.ne fail_enable
#ifndef NO_DOORBELL
    mov w8, #12                     /* publish NOP alone */
    str w8, [x22, #8]
#endif
    mov w25, #0x11
    bl wait_stamp
    cbnz w0, fail_nop
    adr x1, nop_ok
    bl uart_puts
    mov w8, #36                     /* publish second command */
    str w8, [x22, #8]
    mov w25, #0x22
    bl wait_stamp
    cbnz w0, fail_info
    dmb sy
    ldr w8, [x23]                   /* key1 */
    cmp w8, #1
    b.ne fail_data
    ldr w8, [x23, #4]               /* supported sample-count: 1,2,4,8 */
    cmp w8, #1
    b.eq 2f
    cmp w8, #2
    b.eq 2f
    cmp w8, #4
    b.eq 2f
    cmp w8, #8
    b.ne fail_data
2:  ldr w8, [x23, #8]               /* key2 */
    cmp w8, #2
    b.ne fail_data
    ldr w8, [x23, #12]              /* boolean */
    cmp w8, #1
    b.hi fail_data
    ldr x8, [x23, #16]              /* terminator {0,0} */
    cbnz x8, fail_data
    ldr w8, [x23, #24]              /* count bound: untouched sentinel */
    ldr w9, =0xdeadbeef
    cmp w8, w9
    b.ne fail_data
    ldr w8, [x22, #0xc]             /* all 36 bytes consumed */
    cmp w8, #36
    b.ne fail_data
    ldr w8, [x22, #0x2c]            /* FIFO fault must be clear */
    cbnz w8, fail_data
    str wzr, [x22]                  /* drain and disable */
    adr x1, info_ok
    bl uart_puts
    mov x0, #0x18
    adr x1, exit_ok
    hlt #0xf000
fail_enable:
    adr x1, msg_enable
    b failure
fail_nop:
    adr x1, msg_nop
    b failure
fail_info:
    adr x1, msg_info
    b failure
fail_data:
    adr x1, msg_data
failure:
    bl uart_puts
    mov x0, #0x18
    adr x1, exit_bad
    hlt #0xf000
wait_stamp:
    ldr x26, =20000000              /* bounded even without a doorbell */
3:  ldr w8, [x21]
    cmp w8, w25
    b.eq 4f
    subs x26, x26, #1
    b.ne 3b
    mov w0, #1
    ret
4:  mov w0, #0
    ret
uart_puts:
    ldrb w2, [x1], #1
    cbz w2, 5f
    strb w2, [x20]
    b uart_puts
5:  ret
.balign 8
exit_ok:  .quad 0x20026, 0
exit_bad: .quad 0x20026, 1
nop_ok:     .asciz "FIFO NOP STAMP PASS\n"
info_ok:    .asciz "FIFO GET_DEVICE_INFO ROUNDTRIP PASS\n"
msg_enable: .asciz "FIFO ENABLE FAILED\n"
msg_nop:    .asciz "FIFO NOP STAMP TIMEOUT\n"
msg_info:   .asciz "FIFO INFO STAMP TIMEOUT\n"
msg_data:   .asciz "FIFO INFO DATA CHECK FAILED\n"
