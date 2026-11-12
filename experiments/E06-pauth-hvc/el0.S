.arch armv8.3-a
.text
.global enter_el0_sign,el0_sign_return
enter_el0_sign:
    ldr x9,=0x70e00000
    msr sp_el0,x9
    adr x9,el0_sign_body
    msr elr_el1,x9
    mov x9,#0x3c0
    msr spsr_el1,x9
    eret
el0_sign_body:
    mov x2,x0
    mov x3,x0
    mov x4,x0
    mov x5,x0
    pacia x0,x1
    pacib x2,x1
    pacda x3,x1
    pacdb x4,x1
    pacga x5,x5,x1
    svc #0x66
1:  b 1b
el0_sign_return:
    ret
.global enter_el0_hvc,el0_hvc_instruction,el0_hvc_return
enter_el0_hvc:
    adr x9,el0_hvc_body
    msr elr_el1,x9
    mov x9,#0x3c0
    msr spsr_el1,x9
    eret
el0_hvc_body:
    mov x0,#0xc1000000
    add x0,x0,#0xf0
el0_hvc_instruction:
    hvc #0
    svc #0x67
2:  b 2b
el0_hvc_return:
    ret
.global pac_secondary_entry
pac_secondary_entry:
    msr daifset,#15
    msr spsel,#1
    ldr x9,=0x70d00000
    mov sp,x9
    adr x9,vectors
    msr vbar_el1,x9
    isb
    bl pac_secondary_main
3:  wfi
    b 3b
