/* Self-authored dual-vCPU AArch64 bare-metal probe for 0033 sync counters. */
.section .text.boot, "ax"
.global _start
_start:
    mrs x0, mpidr_el1
    and x0, x0, #0xff
    cbnz x0, secondary_entry

    /* Install EL1 vector table */
    ldr x1, =vector_table
    msr vbar_el1, x1
    isb

    /* Wake CPU 1 via PSCI CPU_ON (0xc4000003) */
    ldr x0, =0xc4000003
    mov x1, #1
    ldr x2, =secondary_entry
    mov x3, #0
    hvc #0

    /* Wait until CPU 1 signals ready == 1 */
    ldr x9, =shared_state
1:  ldar w10, [x9]
    cmp w10, #1
    b.ne 1b

    /* 1. 37 reads of CNTVCT_EL0 (ARM_CP_IO read) */
    mov x11, #37
2:  mrs x12, cntvct_el0
    subs x11, x11, #1
    b.ne 2b

    /* 2. 13 writes of CNTP_CVAL_EL0 (ARM_CP_IO write; timer stays disabled) */
    mov x11, #13
3:  msr cntp_cval_el0, xzr
    subs x11, x11, #1
    b.ne 3b

    /* 3. 19 SVC #0 + ERET round-trips (19 do_interrupt_svc, 38 bql_eret) */
    mov x11, #19
4:  svc #0
    subs x11, x11, #1
    b.ne 4b

    /* 4. 11 TLBI VAE1IS + DSB ISH (11 excl_tlbi_page) */
    mov x11, #11
5:  tlbi vae1is, xzr
    dsb ish
    subs x11, x11, #1
    b.ne 5b

    /* 5. Configure 4 KiB TG0 in TCR_EL1 (T0SZ=16, TG0=0) and run 3 TLBI RVAE1IS
     *    with TG=1 (4 KiB), SCALE=0, NUM=1 -> (1+1)<<(1+12) = 16 KiB = 4 pages
     *    (3 excl_tlbi_range).
     */
    mov x12, #16
    msr tcr_el1, x12
    isb
    /* bit[46]=1 (TG=1 for 4K), bit[39]=1 (NUM=1) -> (1<<46) | (1<<39) */
    mov x12, #(1 << 14)
    lsl x12, x12, #32
    orr x12, x12, #(1 << 39)
    mov x11, #3
6:  tlbi rvae1is, x12
    dsb ish
    subs x11, x11, #1
    b.ne 6b

    /* 6. 5 TLBI VMALLE1IS + DSB ISH (5 excl_tlbi_all) */
    mov x11, #5
7:  tlbi vmalle1is
    dsb ish
    subs x11, x11, #1
    b.ne 7b

    /* 7. 8 consecutive TLBI VALE1IS followed by ONE DSB ISH
     *    (8 TLBI_VALE1IS writes coalesced into 1 excl_tlbi_range batch).
     */
    mov x11, #8
    mov x12, #0
11: tlbi vale1is, x12
    add x12, x12, #1
    subs x11, x11, #1
    b.ne 11b
    dsb ish

    /* Release CPU 1 to park in WFI, then emit DONE\n on PL011 UART (5 MMIO writes) */
    mov w10, #2
    stlr w10, [x9]

    ldr x13, =0x09000000
    mov w14, #'D'
    str w14, [x13]
    mov w14, #'O'
    str w14, [x13]
    mov w14, #'N'
    str w14, [x13]
    mov w14, #'E'
    str w14, [x13]
    mov w14, #'\n'
    str w14, [x13]

8:  wfi
    b 8b

secondary_entry:
    ldr x1, =vector_table
    msr vbar_el1, x1
    isb
    ldr x9, =shared_state
    mov w10, #1
    stlr w10, [x9]
9:  ldar w10, [x9]
    cmp w10, #2
    b.ne 9b
10: wfi
    b 10b

    .balign 2048
vector_table:
    /* Current EL with SP0: 4 entries * 128B = 512B */
    .rept 4
    eret
    .balign 128
    .endr
    /* Current EL with SPx: sync + irq + fiq + serror */
    eret
    .balign 128
    eret
    .balign 128
    eret
    .balign 128
    eret
    .balign 128
    /* Lower EL AArch64 + AArch32 */
    .rept 8
    eret
    .balign 128
    .endr

.section .data
.balign 64
shared_state:
    .word 0
