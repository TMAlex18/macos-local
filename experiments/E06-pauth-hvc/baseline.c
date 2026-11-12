#include "runtime.h"
typedef uint64_t (*pac_op)(uint64_t,uint64_t);
extern uint64_t do_pacia(uint64_t,uint64_t),do_pacib(uint64_t,uint64_t);
extern uint64_t do_pacda(uint64_t,uint64_t),do_pacdb(uint64_t,uint64_t);
extern uint64_t do_autia(uint64_t,uint64_t),do_autib(uint64_t,uint64_t);
extern uint64_t do_autda(uint64_t,uint64_t),do_autdb(uint64_t,uint64_t);
extern uint64_t do_xpaci(uint64_t),do_pacga(uint64_t,uint64_t);
extern char do_autia_instruction[];
static volatile uint64_t expected_fault_pc,auth_faults;
struct call_result { uint64_t r[5]; };
static struct call_result hvc(uint64_t id,uint64_t a,uint64_t b) {
    register uint64_t x0 __asm__("x0")=id,x1 __asm__("x1")=a;
    register uint64_t x2 __asm__("x2")=b,x3 __asm__("x3")=0x3333,x4 __asm__("x4")=0x4444;
    __asm__ volatile("hvc #0" : "+r"(x0),"+r"(x1),"+r"(x2),"+r"(x3),"+r"(x4) :: "memory");
    return (struct call_result){{x0,x1,x2,x3,x4}};
}
void exception_sync(struct exception_frame *f) {
    uint64_t esr=READ_SYSREG(esr_el1);
    if(expected_fault_pc && f->elr==expected_fault_pc && (esr>>26)==0x1c) {
        field_uart("NEGATIVE_PAC_ESR",esr);auth_faults++;f->elr+=4;return;
    }
    exception_bad(f);
}
static void key_init(void) {
    WRITE_SYSREG(TCR_EL1,(16ull)|(16ull<<16)|(1ull<<23)|(2ull<<14));
    WRITE_SYSREG(APIAKeyLo_EL1,0x0123456789abcdefull);WRITE_SYSREG(APIAKeyHi_EL1,0x8899aabbccddeeffull);
    WRITE_SYSREG(APIBKeyLo_EL1,0x0123456700000001ull);WRITE_SYSREG(APIBKeyHi_EL1,0x8899aabb00000002ull);
    WRITE_SYSREG(APDAKeyLo_EL1,0x0123456700000003ull);WRITE_SYSREG(APDAKeyHi_EL1,0x8899aabb00000004ull);
    WRITE_SYSREG(APDBKeyLo_EL1,0x0123456700000005ull);WRITE_SYSREG(APDBKeyHi_EL1,0x8899aabb00000006ull);
    WRITE_SYSREG(APGAKeyLo_EL1,0x0123456700000007ull);WRITE_SYSREG(APGAKeyHi_EL1,0x8899aabb00000008ull);
    uint64_t s=READ_SYSREG(SCTLR_EL1);
    WRITE_SYSREG(SCTLR_EL1,s|(1ull<<31)|(1ull<<30)|(1ull<<27)|(1ull<<13));isb();
}
void main(void) {
    puts_uart("E06 PAUTH HVC BASELINE BEGIN\n");
    field_uart("ISAR1",READ_SYSREG(ID_AA64ISAR1_EL1));field_uart("ISAR2",READ_SYSREG(ID_AA64ISAR2_EL1));
    key_init();
    const uint64_t raw=0x70012340,mod=0x19375;
    pac_op signs[]={do_pacia,do_pacib,do_pacda,do_pacdb};
    pac_op auths[]={do_autia,do_autib,do_autda,do_autdb};
    for(unsigned i=0;i<4;i++) {
        uint64_t signed_p=signs[i](raw,mod);
        expect(signed_p!=raw,"PAC_MUST_NOT_BE_NOP");
        expect(auths[i](signed_p,mod)==raw,"VALID_PAC_AUTH_ROUNDTRIP");
    }
    puts_uart("IA_IB_DA_DB_SIGN_AUTH_PASS\n");
    uint64_t p=do_pacia(raw,mod);
    expect(do_xpaci(p)==raw,"XPACI_RESTORES_RAW");
    expected_fault_pc=(uintptr_t)do_autia_instruction;
    /* Change a bit added by PAC, keeping the pure ComputePAC tuple identical.
     * A memoized result must still pass through the real AUT comparison. */
    uint64_t changed=p^raw;
    expect(changed!=0,"NEGATIVE_SIGNATURE_REQUIRES_PAC_BITS");
    uint64_t previous=auth_faults;
    uint64_t bad=do_autia(p^(changed&-changed),mod);
    expect(auth_faults>previous||bad!=raw,"TAMPERED_PAC_ACCEPTED");
    puts_uart("SAME_TUPLE_NEGATIVE_SIGNATURE_PASS\n");
    previous=auth_faults;bad=do_autia(p,mod^0x100);
    expect(auth_faults>previous||bad!=raw,"WRONG_MODIFIER_ACCEPTED");
    WRITE_SYSREG(APIAKeyLo_EL1,0x1123456789abcdefull);isb();
    previous=auth_faults;bad=do_autia(p,mod);
    expect(auth_faults>previous||bad!=raw,"WRONG_KEY_ACCEPTED");
    expected_fault_pc=0;
    WRITE_SYSREG(APIAKeyLo_EL1,0x0123456789abcdefull);isb();
    expect(do_autia(p,mod)==raw,"KEY_RESTORE_FAILED");
    puts_uart("NEGATIVE_KEY_MODIFIER_AND_RESTORE_PASS\n");
    uint64_t ga=do_pacga(raw,mod);
    WRITE_SYSREG(APGAKeyLo_EL1,0x0123456700000009ull);isb();
    expect(do_pacga(raw,mod)!=ga,"PACGA_KEY_CHANGE_IGNORED");
    WRITE_SYSREG(APGAKeyLo_EL1,0x0123456700000007ull);isb();
    expect(do_pacga(raw,mod)==ga,"PACGA_RESTORE_FAILED");
    puts_uart("PACGA_KEY_DEPENDENCE_PASS\n");
    struct call_result ret=hvc(0x84000000,0,0);
    expect(ret.r[0]==0x10001,"PSCI_VERSION_1_1");
    const uint64_t ids[]={0xc1000000,0xc1000001,0xc1000002,0xc1000003,
       0xc1000004,0xc1000005,0xc1000006,0xc10000f0,0xc10000ff,0xc3000003,0xc3000004};
    uint64_t before=READ_SYSREG(APIAKeyLo_EL1);
    for(unsigned i=0;i<sizeof(ids)/sizeof(ids[0]);i++) {
        ret=hvc(ids[i],0x123456789,0x234567891);
        field_uart("HVC_ID",ids[i]);field_uart("HVC_RET",ret.r[0]);
        expect(ret.r[0]==UINT64_MAX,"BASELINE_HVC_EXPECTED_NOT_SUPPORTED");
        expect(READ_SYSREG(APIAKeyLo_EL1)==before,"UNSUPPORTED_HVC_MUTATED_KEY");
    }
    puts_uart("APPLE_HVC_UNSUPPORTED_BASELINE_CONFIRMED\n");
    puts_uart("E06 PAUTH BASELINE PASS\n");platform_exit(0);
}
