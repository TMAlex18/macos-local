    .text
    .global enter_user,user_return
enter_user:
    stp x29,x30,[sp,#-16]!
    msr elr_el1,x0
    mov x1,#0x3c0
    msr spsr_el1,x1
    ldr x1,=0x803f0000
    msr sp_el0,x1
    eret
user_return:
    ldp x29,x30,[sp],#16
    ret

    .global enter_privileged
enter_privileged:
    stp x29,x30,[sp,#-16]!
    br x0

    .global secondary_entry
secondary_entry:
    msr daifset,#15
    msr spsel,#1
    ldr x1,=0x41000000
    sub x1,x1,x0,lsl #16
    mov sp,x1
    adr x1,vectors
    msr vbar_el1,x1
    isb
    bl secondary_main
1:  wfi
    b 1b

    .global fp_prepare,fp_read_state
fp_prepare:
    ldr x0,=0x0800001f
    msr fpsr,x0
    ldr x0,=0x0123456789abcdef
    fmov d10,x0
    movi v8.16b,#255
    movi v9.16b,#255
    ret
fp_read_state:
    mrs x1,fpsr
    str x1,[x0]
    fmov x1,d10
    str x1,[x0,#8]
    umov x1,v8.d[1]
    str x1,[x0,#16]
    umov x1,v9.d[1]
    str x1,[x0,#24]
    ret

    .section .test_sites,"a"
    .balign 64
    .global site0,site1
site0:
    fmov d8,#3.0
    fmov x0,d8
    svc #0
    .space 64-(.-site0)
site1:
    fmov d9,#3.0
    fmov x0,d9
    svc #0
    .space 64-(.-site1)
