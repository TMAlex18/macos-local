/* AArch64 VMApple TCG PSCI CPU_ON proof: CPU0 issues HVC, CPU1 writes RAM.
 * Only QEMU's documented ARM PSCI semantics are assumed here.
 * Exit through ARM semihosting; no macOS or Apple firmware involved.
 */
.section .text
.global _start
_start:
    ldr x20, =0x20010000 /* PL011 UART */
    ldr x21, =0x70000000 /* guest RAM */
    str xzr, [x21]
    dsb sy
    ldr x0, =0xc4000003 /* PSCI_CPU_ON_AARCH64 */
    mov x1, #1          /* MPIDR affinity of CPU1 */
    adr x2, secondary_entry
    mov x3, #0x42       /* context_id -> secondary x0 */
    hvc #0
    cbnz x0, failure
    ldr x22, =10000000
poll:
    ldr x6, [x21]
    cmp x6, #0x42
    b.eq success
    cmp x6, #0x7f
    b.eq failure
    subs x22, x22, #1
    b.ne poll
failure:
    adr x1, fail_message
    bl uart_puts
    mov x0, #0x18
    adr x1, fail_exit
    hlt #0xf000
success:
    adr x1, success_message
    bl uart_puts
    mov x0, #0x18
    adr x1, success_exit
    hlt #0xf000
uart_puts:
    ldrb w2, [x1], #1
    cbz w2, uart_done
    strb w2, [x20]
    b uart_puts
uart_done:
    ret
/* PSCI brings up CPU1 at this physical instruction address. */
secondary_entry:
    ldr x21, =0x70000000
    mov x6, #0x42
    cmp x0, #0x42
    b.eq secondary_done
    mov x6, #0x7f
secondary_done:
    str x6, [x21]
    dsb sy
    sev
wait_secondary:
    wfi
    b wait_secondary
.balign 8
success_exit:
    .quad 0x20026
    .quad 0
fail_exit:
    .quad 0x20026
    .quad 1
success_message:
    .asciz "VMAPPLE PSCI CPU_ON RAM SIGNAL PASS\n"
fail_message:
    .asciz "VMAPPLE PSCI CPU_ON RAM SIGNAL FAIL\n"
