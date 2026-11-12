#include "runtime.h"
extern uint64_t do_pacia(uint64_t,uint64_t),do_pacib(uint64_t,uint64_t),do_pacda(uint64_t,uint64_t),do_pacdb(uint64_t,uint64_t),do_pacga(uint64_t,uint64_t),do_autia(uint64_t,uint64_t);
extern uint64_t enter_el0_sign(uint64_t,uint64_t),enter_el0_hvc(void);
extern char el0_sign_return[],el0_hvc_return[],el0_hvc_instruction[],do_autia_instruction[],pac_secondary_entry[];
struct result {uint64_t x[5];};
static struct result hvc(uint64_t id,uint64_t a,uint64_t b,uint64_t c) {
 register uint64_t x0 __asm__("x0")=id,x1 __asm__("x1")=a,x2 __asm__("x2")=b,x3 __asm__("x3")=c,x4 __asm__("x4")=0x5555;
 __asm__ volatile("hvc #0" : "+r"(x0),"+r"(x1),"+r"(x2),"+r"(x3),"+r"(x4) :: "memory");
 return (struct result){{x0,x1,x2,x3,x4}};
}
static void call(unsigned f,uint64_t a,uint64_t b) {
 struct result r=hvc(0xc1000000u+f,a,b,0);
 expect(r.x[0]==0,"HVC_NOT_SUCCESS");
}
struct keys {uint64_t v[10];};
static struct keys read_keys(void) {
 return (struct keys){{READ_SYSREG(APIAKeyLo_EL1),READ_SYSREG(APIAKeyHi_EL1),
 READ_SYSREG(APDAKeyLo_EL1),READ_SYSREG(APDAKeyHi_EL1),READ_SYSREG(APIBKeyLo_EL1),READ_SYSREG(APIBKeyHi_EL1),
 READ_SYSREG(APDBKeyLo_EL1),READ_SYSREG(APDBKeyHi_EL1),READ_SYSREG(APGAKeyLo_EL1),READ_SYSREG(APGAKeyHi_EL1)}};
}
static int eq(const struct keys *a,const struct keys *b,unsigned start,unsigned n) {
 for(unsigned i=start;i<start+n;i++)if(a->v[i]!=b->v[i])return 0;return 1;
}
static void enable_pauth(void) {
 WRITE_SYSREG(TCR_EL1,16ull|(16ull<<16)|(1ull<<23)|(2ull<<14));
 WRITE_SYSREG(SCTLR_EL1,READ_SYSREG(SCTLR_EL1)|(1ull<<31)|(1ull<<30)|(1ull<<27)|(1ull<<13));isb();
}
static volatile uint64_t user_signatures[5],svc_count,user_hvc_denied,pac_faults;
static volatile int expect_pac_failure;
void exception_sync(struct exception_frame *f) {
 uint64_t esr=READ_SYSREG(ESR_EL1),ec=esr>>26;
 if(ec==0x15 && (esr&0xffff)==0x66) {
  expect((f->spsr&15)==0,"SVC_NOT_FROM_EL0");
  user_signatures[0]=f->x[0];user_signatures[1]=f->x[2];user_signatures[2]=f->x[3];user_signatures[3]=f->x[4];user_signatures[4]=f->x[5];
  svc_count++;f->elr=(uintptr_t)el0_sign_return;f->spsr=0x3c5;return;
 }
 if(ec==0 && f->elr==(uintptr_t)el0_hvc_instruction && (f->spsr&15)==0) {
  user_hvc_denied++;f->elr=(uintptr_t)el0_hvc_return;f->spsr=0x3c5;f->x[0]=1;return;
 }
 if(expect_pac_failure && ec==0x1c && f->elr==(uintptr_t)do_autia_instruction) {
  pac_faults++;f->elr+=4;return;
 }
 exception_bad(f);
}
static volatile uint64_t secondary_done,secondary_checks,secondary_defaults[4],secondary_signed;
static uint64_t raw=0x70012340,modifier=0x771921;
void pac_secondary_main(uint64_t generation) {
 enable_pauth();
 struct result pre=hvc(0xc1000001,0,0,0);
 if(pre.x[0]!=(uint64_t)-3) {secondary_checks=0xdead;secondary_done=generation;for(;;)__asm__ volatile("wfi");}
 call(0,0,0);struct result d=hvc(0xc1000001,0,0,0);
 for(unsigned i=0;i<4;i++)secondary_defaults[i]=d.x[i+1];
 secondary_signed=do_pacia(raw,modifier);
 call(2,d.x[1]^0xABCDEF,0);
 secondary_checks=generation;
 dsb_sy();secondary_done=generation;
 hvc(0x84000002,0,0,0); /* PSCI CPU_OFF */
 for(;;)__asm__ volatile("wfi");
}
static void test_secondary(const struct result *defaults, const struct keys *base,uint64_t base_signed) {
 for(uint64_t generation=1;generation<=2;generation++) {
  struct result p=hvc(0xc4000003,1,(uintptr_t)pac_secondary_entry,generation);
  expect(p.x[0]==0,"PSCI_CPU_ON_MODEL");
  unsigned spins=30000000;
  while(secondary_done!=generation && --spins)__asm__ volatile("yield");
  expect(spins!=0,"SECONDARY_TIMEOUT");dsb_sy();
  expect(secondary_checks==generation,"SECONDARY_RESET_STATE_NOT_CLEARED");
  for(unsigned i=0;i<4;i++)expect(secondary_defaults[i]==defaults->x[i+1],"DEFAULTS_DIFFER_ACROSS_CPUS");
  expect(secondary_signed==base_signed,"SAME_KEY_DIFFERENT_CPU_SIGNATURE");
  struct keys after=read_keys();expect(eq(&after,base,0,10),"SECONDARY_MUTATED_PRIMARY_KEYS");
  spins=3000000;
  while(hvc(0xc4000004,1,0,0).x[0]!=1 && --spins)__asm__ volatile("yield");
  expect(spins!=0,"SECONDARY_NOT_POWERED_OFF");
 }
 puts_uart("PAC_PER_CPU_INITIALIZE_ISOLATION_POWER_CYCLE_PASS\n");
}
void main(void) {
 puts_uart("E06 SOFTWARE PAC HVC MODEL BEGIN\n");
 field_uart("MODEL_ISAR1",READ_SYSREG(ID_AA64ISAR1_EL1));
#ifdef MODEL_ACCEPT_IMPDEF
 /* Same model checks under QEMU's impdef algorithm: APA or API must be set. */
 expect((((READ_SYSREG(ID_AA64ISAR1_EL1)>>4)&15)|((READ_SYSREG(ID_AA64ISAR1_EL1)>>8)&15))!=0,"MODEL_REQUIRES_PAUTH_PROFILE");
#else
 expect(((READ_SYSREG(ID_AA64ISAR1_EL1)>>4)&15)!=0,"MODEL_REQUIRES_QARMA5_PROFILE");
#endif
 enable_pauth();
 struct result r=hvc(0xc1000001,0,0,0);expect(r.x[0]==(uint64_t)-3,"PREINIT_MUST_FAIL");
 call(0xf0,0,0);call(0,0,0);
 struct result defaults=hvc(0xc1000001,0,0,0);expect(defaults.x[0]==0,"DEFAULT_KEY_CALL");
 struct keys base=read_keys();
 expect(!eq(&base,&(struct keys){{0}},0,10),"KEYS_ALL_ZERO");
 uint64_t bs=do_pacia(raw,modifier);expect(bs!=raw,"PAC_BYPASSED");expect(do_autia(bs,modifier)==raw,"VALID_AUTH_FAIL");
 call(2,defaults.x[1]^0x1234,0);struct keys changed=read_keys();
 expect(!eq(&base,&changed,0,4)&&eq(&base,&changed,4,6),"A_KEY_DOMAIN_CHANGE");
 expect_pac_failure=1;uint64_t bad=do_autia(bs,modifier);expect_pac_failure=0;
 expect(pac_faults||bad!=raw,"OLD_SIGNATURE_ACCEPTED_AFTER_REKEY");
 call(2,defaults.x[1],0);changed=read_keys();expect(eq(&base,&changed,0,10),"A_RESTORE");
 call(3,defaults.x[2]^0x9876,0);changed=read_keys();
 expect(eq(&base,&changed,0,4)&&!eq(&base,&changed,4,4)&&eq(&base,&changed,8,2),"B_KEY_DOMAIN_CHANGE");
 call(3,defaults.x[2],0);
 call(6,defaults.x[4]^0x2345,0);changed=read_keys();expect(eq(&base,&changed,0,8)&&!eq(&base,&changed,8,2),"G_KEY_DOMAIN_CHANGE");
 call(6,defaults.x[4],0);changed=read_keys();expect(eq(&base,&changed,0,10),"G_RESTORE");
 puts_uart("HVC_INITIAL_DEFAULTS_A_B_G_REKEY_AUTH_PASS\n");
 uint64_t div=defaults.x[3]^0x123456789;
 call(4,div,0);changed=read_keys();expect(eq(&base,&changed,0,10),"EL0_DIVERSIFIER_ALTERED_EL1_KEYS");
 call(5,1,div);struct keys forced=read_keys();
 expect(!eq(&base,&forced,0,8)&&eq(&base,&forced,8,2),"EL1_OVERRIDE_KEY_SCOPE");
 uint64_t expected[5]={do_pacia(raw,modifier),do_pacib(raw,modifier),do_pacda(raw,modifier),do_pacdb(raw,modifier),do_pacga(raw,modifier)};
 r=hvc(0xc1000005,2,div,0);expect(r.x[0]==(uint64_t)-2,"INVALID_ENABLE_NOT_REJECTED");
 changed=read_keys();expect(eq(&forced,&changed,0,10),"INVALID_CALL_MUTATED_KEYS");
 call(5,0,div);changed=read_keys();expect(eq(&base,&changed,0,10),"EL1_OVERRIDE_NOT_RESTORED");
 enter_el0_sign(raw,modifier);
 expect(svc_count==1,"NO_REAL_EL0_SVC");
 for(unsigned i=0;i<5;i++)expect(user_signatures[i]==expected[i],"EL0_KEYS_DIFFER_FROM_EL1_OVERRIDE");
 changed=read_keys();expect(eq(&base,&changed,0,10),"EL0_TO_EL1_KEYS_NOT_RESTORED");
 call(5,1,div);expect(do_autia(user_signatures[0],modifier)==raw,"EL0_SIGNATURE_AUTH_AT_EL1_OVERRIDE");
 call(5,0,div);
 expect(enter_el0_hvc()==1&&user_hvc_denied==1,"EL0_PRIVILEGED_HVC_NOT_DENIED");
 puts_uart("EL0_EL1_DIVERSIFIER_SVC_RESTORE_AND_DENIAL_PASS\n");
 changed=read_keys();
 r=hvc(0xc10000ff,0,0,0);expect(r.x[0]==UINT64_MAX,"UNKNOWN_HVC_FALSE_SUCCESS");
 struct keys after=read_keys();expect(eq(&changed,&after,0,10),"UNKNOWN_HVC_MUTATION");
 call(0,0,0);after=read_keys();expect(eq(&base,&after,0,10),"REINITIALIZATION_NOT_RESTORED");
 r=hvc(0x84000000,0,0,0);expect(r.x[0]==0x10001,"PSCI_REGRESSION");
 test_secondary(&defaults,&base,bs);
 puts_uart("E06 SOFTWARE PAC MODEL SEMANTICS PASS\n");platform_exit(0);
}
