/* VMApple MMIO lab payload: firmware, config, serial & semihosting.
 * No Apple source/firmware. Run with exact EXPECT_CPUS (1/2/4).
 */
#ifndef EXPECT_CPUS
#define EXPECT_CPUS 1
#endif
.section .text
.global _start
_start:
    ldr x20, =0x20010000 /* VMApple PL011 UART */
    ldr x21, =0x00400000 /* VMApple config ROM/RAM */
    ldr w6, [x21, #0x00] /* cfg.version == 2 */
    cmp w6, #2
    b.ne failure
    ldr w6, [x21, #0x04] /* cfg.nr_cpus */
    mov w7, #EXPECT_CPUS
    cmp w6, w7
    b.ne failure
    ldr x6, [x21, #0x20] /* cfg.ram_size == 512MiB */
    ldr x7, =0x20000000
    cmp x6, x7
    b.ne failure
    ldr w6, [x21, #0x100] /* cfg.cpu_ids[0] */
    cbnz w6, failure
#if EXPECT_CPUS >= 2
    ldr w6, [x21, #0x104]
    cmp w6, #1
    b.ne failure
#endif
#if EXPECT_CPUS >= 4
    ldr w6, [x21, #0x108]
    cmp w6, #2
    b.ne failure
    ldr w6, [x21, #0x10c]
    cmp w6, #3
    b.ne failure
#endif
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
hang:
    wfi
    b hang
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
    .asciz "VMAPPLE TCG CFG UART PASS\n"
fail_message:
    .asciz "VMAPPLE TCG CFG UART FAIL\n"
