// Self-authored AArch64 TCG lookup regression. All checksums have simple
// arithmetic oracles in test-tb-lookup.py; no QEMU result supplies an oracle.
    .text
    .global _start
_start:
    ldr x0, =0x40400000
    mov sp, x0

    mov x9, #0
    ldr x4, =ITERATIONS
    adr x10, hot_target
    adr x11, hot_done
    br x10
hot_done:
    adr x0, hot_name
    bl print_result

    mov x9, #0
    ldr x4, =ITERATIONS
    adr x10, conflict_a
    adr x11, conflict_b
    adr x12, conflict_done
    br x10
conflict_done:
    adr x0, conflict_name
    bl print_result

    mov x9, #0
    ldr x4, =ITERATIONS
    adr x10, cross_page_target
cross_loop:
    blr x10
    subs x4, x4, #1
    b.ne cross_loop
    adr x0, cross_name
    bl print_result

    // Executable RAM starts four bytes before an internal 4 KiB boundary.
    // Change each page in turn after translation, using architectural cache
    // maintenance, then invoke exactly the same guest PC through BLR.
    mov x9, #0
    ldr x10, =0x40200ffc
    ldr w0, =0x91004400        // add x0, x0, #17
    str w0, [x10]
    ldr w0, =0x91003400        // add x0, x0, #13
    str w0, [x10, #4]
    ldr w0, =0xd65f03c0        // ret
    str w0, [x10, #8]
    bl sync_code
    mov x0, #0
    blr x10
    add x9, x9, x0            // 30
    ldr w0, =0x91004c00        // add x0, x0, #19
    str w0, [x10]
    bl sync_code
    mov x0, #0
    blr x10
    add x9, x9, x0            // 32
    ldr w0, =0x91005c00        // add x0, x0, #23
    str w0, [x10, #4]
    bl sync_code
    mov x0, #0
    blr x10
    add x9, x9, x0            // 42
    adr x0, invalidate_name
    bl print_result

    ldr x0, =0x84000008       // PSCI SYSTEM_OFF
    hvc #0
1:  b 1b

sync_code:
    add x1, x10, #4
    dc cvau, x10
    dc cvau, x1
    dsb ish
    ic ivau, x10
    ic ivau, x1
    dsb ish
    isb
    ret

// x0 is a NUL-terminated label; x9 is the checksum. Clobbers x0-x3 only.
print_result:
    ldr x1, =0x09000000
1:  ldrb w2, [x0], #1
    cbz w2, 3f
2:  ldr w3, [x1, #0x18]
    tbnz w3, #5, 2b
    str w2, [x1]
    b 1b
3:  mov x0, #60
4:  lsrv x2, x9, x0
    and x2, x2, #15
    cmp x2, #10
    add x3, x2, #87
    add x2, x2, #48
    csel x2, x2, x3, lo
5:  ldr w3, [x1, #0x18]
    tbnz w3, #5, 5b
    str w2, [x1]
    subs x0, x0, #4
    b.ge 4b
    mov w2, #10
    str w2, [x1]
    ret

    .balign 4096
    .global hot_target
hot_target:
    add x9, x9, #1
    subs x4, x4, #1
    csel x13, x10, x11, ne
    br x13

    .balign 4096
    .global conflict_a, conflict_b
conflict_a:
    add x9, x9, #3
    br x11
    .space 0x104 - (. - conflict_a)
conflict_b:
    add x9, x9, #5
    subs x4, x4, #1
    csel x13, x10, x12, ne
    br x13

    .balign 4096
    .space 4092
    .global cross_page_target
cross_page_target:
    add x9, x9, #7
    add x9, x9, #11
    ret

    .section .rodata
hot_name:        .asciz "hit="
conflict_name:   .asciz "conflict="
cross_name:      .asciz "cross_page="
invalidate_name:.asciz "invalidate="
