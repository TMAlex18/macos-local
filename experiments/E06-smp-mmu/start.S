.section .text.boot,"ax"
.global _start
_start:
    msr daifset, #15
    msr spsel, #1
    ldr x0, =0x71000000
    mov sp, x0
    ldr x0, =__bss_start
    ldr x1, =__bss_end
1:  cmp x0, x1
    b.hs 2f
    str xzr, [x0], #8
    b 1b
2:  adr x0, vectors
    msr vbar_el1, x0
    isb
    bl main
    mov x0, #2
    bl platform_exit

/* PSCI CPU1 entry uses a distinct stack and never clears shared BSS. */
.global secondary_entry
secondary_entry:
    msr daifset, #15
    msr spsel, #1
    ldr x0, =0x71010000
    mov sp, x0
    adr x0, vectors
    msr vbar_el1, x0
    isb
    bl secondary_main
    mov x0, #3
    bl platform_exit

.section .vectors,"ax"
.balign 2048
.global vectors
vectors:
.macro vector dest
    b \dest
    .balign 128
.endm
    vector sync_entry
    vector irq_entry
    vector bad_entry
    vector bad_entry
    vector sync_entry
    vector irq_entry
    vector bad_entry
    vector bad_entry
    vector sync_entry
    vector irq_entry
    vector bad_entry
    vector bad_entry
    vector sync_entry
    vector irq_entry
    vector bad_entry
    vector bad_entry

.section .text,"ax"
.macro exception_entry name, handler
\name:
    sub sp, sp, #272
    stp x0,x1,[sp,#0]
    stp x2,x3,[sp,#16]
    stp x4,x5,[sp,#32]
    stp x6,x7,[sp,#48]
    stp x8,x9,[sp,#64]
    stp x10,x11,[sp,#80]
    stp x12,x13,[sp,#96]
    stp x14,x15,[sp,#112]
    stp x16,x17,[sp,#128]
    stp x18,x19,[sp,#144]
    stp x20,x21,[sp,#160]
    stp x22,x23,[sp,#176]
    stp x24,x25,[sp,#192]
    stp x26,x27,[sp,#208]
    stp x28,x29,[sp,#224]
    str x30,[sp,#240]
    mrs x1,elr_el1
    mrs x2,spsr_el1
    str x1,[sp,#248]
    str x2,[sp,#256]
    mov x0,sp
    bl \handler
    ldr x1,[sp,#248]
    ldr x2,[sp,#256]
    msr elr_el1,x1
    msr spsr_el1,x2
    ldp x0,x1,[sp,#0]
    ldp x2,x3,[sp,#16]
    ldp x4,x5,[sp,#32]
    ldp x6,x7,[sp,#48]
    ldp x8,x9,[sp,#64]
    ldp x10,x11,[sp,#80]
    ldp x12,x13,[sp,#96]
    ldp x14,x15,[sp,#112]
    ldp x16,x17,[sp,#128]
    ldp x18,x19,[sp,#144]
    ldp x20,x21,[sp,#160]
    ldp x22,x23,[sp,#176]
    ldp x24,x25,[sp,#192]
    ldp x26,x27,[sp,#208]
    ldp x28,x29,[sp,#224]
    ldr x30,[sp,#240]
    add sp,sp,#272
    eret
.endm
exception_entry sync_entry,exception_sync
exception_entry irq_entry,exception_irq
exception_entry bad_entry,exception_bad

.global probe_load,probe_load_instruction
probe_load:
probe_load_instruction:
    ldr x0,[x0]
    ret
.global probe_store,probe_store_instruction
probe_store:
probe_store_instruction:
    str x1,[x0]
    ret
