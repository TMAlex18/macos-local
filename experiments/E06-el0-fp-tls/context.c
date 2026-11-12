/* FP/SIMD and TPIDR_EL0 variant. Different ASIDs, physical data/stack pages,
 * and PAuth keys; timer IRQ preempts through the existing EL1 vector ABI.
 * No Apple firmware, TSS, disk restore, or guest-signature alteration. */
#include "runtime.h"
#define PAGE 0x4000ull
#define BLOCK 0x2000000ull
#define USER_CODE 0xa0000000ull
#define USER_DATA (USER_CODE+PAGE)
#define USER_STACK (USER_CODE+2*PAGE)
#define USER_GUARD (USER_CODE+3*PAGE)
#define AF (1ull<<10)
#define NG (1ull<<11)
#define INNER (3ull<<8)
#define PXN (1ull<<53)
#define UXN (1ull<<54)
#define USER (1ull<<6)
#define RO (1ull<<7)
#define GICD 0x10000000ull
#define GICR 0x10010000ull
#define SGI (GICR+0x10000)
#define PPI27 (1u<<27)
#ifndef ROUNDS
#define ROUNDS 12
#endif
extern char __user_page_start[],user_entry[],user_kernel_read[],user_code_write[],user_after_xn[];
extern char signed_body_start[],signed_body_end[];
extern void enter_first_user(uint64_t,uint64_t) __attribute__((noreturn));

/* Preserve the first 48B ABI used by the existing user fixture, then add
 * user-owned TLS and two 128-bit sentinel vectors + writeback snapshots. */
struct data {
    uint64_t owner, irqs, work, signed_function, deadline, goal;
    uint64_t tls_expected;
    uint64_t reserved;  /* keeps SIMD vectors aligned to 16B */
    uint64_t q8_expected[2], q9_expected[2], q8_seen[2], q9_seen[2];
    uint64_t fpcr_expected, fpsr_expected;
};
struct fp_state {
    uint64_t q_words[4];
    uint64_t fpcr,fpsr,tls;
} __attribute__((aligned(16)));

struct task {
    struct exception_frame frame;
    uint64_t sp,lo,hi;
    unsigned phase,done,body_irqs;
    struct fp_state fp_tls;
};
static uint64_t root[2][2048] __attribute__((aligned(16384)));
static uint64_t leaves[2][2048] __attribute__((aligned(16384)));
static struct task tasks[2];
static unsigned current, switches, total_irqs, total_faults, total_svcs;
static unsigned bad_key_armed;
static uint64_t freq, kernel_key_lo, kernel_key_hi;
static const uint64_t data_pa[2]={0x70200000,0x7020c000};
static struct data *data(unsigned t) { return (void *)(uintptr_t)data_pa[t]; }
static uint64_t user_pc(const char *symbol) {
    return USER_CODE+(uintptr_t)symbol-(uintptr_t)__user_page_start;
}
#ifndef USE_PAC_MODEL
static void key_write(uint64_t lo,uint64_t hi) {
    WRITE_SYSREG(APIAKeyLo_EL1,lo);WRITE_SYSREG(APIAKeyHi_EL1,hi);isb();
}
#endif
#ifdef USE_PAC_MODEL
static void model_call(unsigned function,uint64_t value) {
    register uint64_t x0 __asm__("x0")=0xc1000000u+function;
    register uint64_t x1 __asm__("x1")=value;
    register uint64_t x2 __asm__("x2")=0;
    register uint64_t x3 __asm__("x3")=0;
    register uint64_t x4 __asm__("x4")=0;
    __asm__ volatile("hvc #0" : "+r"(x0),"+r"(x1),"+r"(x2),"+r"(x3),"+r"(x4) :: "memory");
    expect(x0==0,"MODEL_HVC_FAILED");
}
#endif
/* FP instructions are in a standalone .S module; all C vectors and IRQ
 * handlers compile with -mgeneral-regs-only to avoid incidental SIMD use. */
extern void fp_save_regs(uint64_t *data);
extern void fp_restore_regs(const uint64_t *data);
static void save_fp_tls(unsigned id) {
    struct fp_state *f=&tasks[id].fp_tls;
    fp_save_regs(f->q_words);
    f->tls=READ_SYSREG(tpidr_el0);
}
static void restore_fp_tls(unsigned id) {
    struct fp_state *f=&tasks[id].fp_tls;
#ifndef SKIP_FP_RESTORE
    fp_restore_regs(f->q_words);
#endif
#ifndef SKIP_TLS_RESTORE
    WRITE_SYSREG(tpidr_el0,f->tls);
#endif
    isb();
}
static void set_context(unsigned id,int bad) {
    current=id;
    /* The tables stay fixed during this test: unique ASIDs isolate the same
       user VA. No blanket TLBI on context switch that could hide ASID bugs. */
    uint64_t ttbr=(uintptr_t)root[id]|((uint64_t)(0x31+id)<<48);
    WRITE_SYSREG(ttbr0_el1,ttbr);isb();
    WRITE_SYSREG(sp_el0,tasks[id].sp);
#ifdef USE_PAC_MODEL
    model_call(4,0x1100+id+(bad?0x4000:0));
#else
    key_write(tasks[id].lo^(bad?0x442211:0),tasks[id].hi);
#endif
}
static void check_entry(struct exception_frame *f) {
    expect((f->spsr&15)==0,"EXCEPTION_NOT_FROM_EL0");
    uint64_t asid=READ_SYSREG(ttbr0_el1)>>48;
    expect(asid==0x31+current,"EXCEPTION_WRONG_ADDRESS_SPACE");
    uint64_t lo=READ_SYSREG(APIAKeyLo_EL1),hi=READ_SYSREG(APIAKeyHi_EL1);
#ifdef USE_PAC_MODEL
    expect(lo==kernel_key_lo&&hi==kernel_key_hi,"IRQ_SVC_DID_NOT_RESTORE_EL1_KEY_BANK");
#else
    if(!bad_key_armed)expect(lo==tasks[current].lo&&hi==tasks[current].hi,"ARCH_KEY_CONTEXT_MISMATCH");
#endif
    if(tasks[current].phase==3)expect(f->x[19]==current+1,"GENERAL_REGISTERS_LEAKED_BETWEEN_TASKS");
    uint64_t sp=READ_SYSREG(sp_el0);
    expect(sp>=USER_STACK&&sp<=USER_STACK+PAGE&&(sp&15)==0,"USER_STACK_CONTEXT_CORRUPT");
}
static void arm_next(void) {
    WRITE_SYSREG(cntv_ctl_el0,0);
#ifndef DISABLE_PPI
    if(data(current)->irqs<ROUNDS && !bad_key_armed) {
        WRITE_SYSREG(cntv_cval_el0,READ_SYSREG(cntvct_el0)+freq/4000);
        WRITE_SYSREG(cntv_ctl_el0,1);
    }
#else
    WRITE_SYSREG(cntv_cval_el0,READ_SYSREG(cntvct_el0)+freq/4000);
    WRITE_SYSREG(cntv_ctl_el0,1);
#endif
    isb();
}
/* Volatile fields keep this freestanding frame copy from becoming a libc
   memcpy call; all fields match the shared assembly vector frame. */
static void copy_frame(volatile struct exception_frame *dst,
                       const volatile struct exception_frame *src) {
    for(unsigned i=0;i<31;i++)dst->x[i]=src->x[i];
    dst->elr=src->elr;dst->spsr=src->spsr;dst->pad=src->pad;
}
static void resume_task(struct exception_frame *f,unsigned next) {
    copy_frame(&tasks[current].frame,f);tasks[current].sp=READ_SYSREG(sp_el0);
    save_fp_tls(current);
    if(next!=current)switches++;
    int corrupt=0;
#ifdef WRONG_RESTORE_KEY
    if(total_irqs>=2&&data(next)->signed_function){corrupt=1;bad_key_armed=1;}
#endif
    set_context(next,corrupt);
    restore_fp_tls(next);
    copy_frame(f,&tasks[next].frame);
    arm_next();
}
static void finish(void) {
    mask_irq();WRITE_SYSREG(cntv_ctl_el0,0);isb();
    for(unsigned i=0;i<2;i++) {
        expect(tasks[i].fp_tls.tls==data(i)->tls_expected,"TLS_SAVED_STATE_WRONG");
        expect(tasks[i].fp_tls.fpcr==data(i)->fpcr_expected,"FPCR_SAVED_STATE_WRONG");
        expect(tasks[i].fp_tls.fpsr==data(i)->fpsr_expected,"FPSR_SAVED_STATE_WRONG");
        for(unsigned j=0;j<2;j++) {
            expect(tasks[i].fp_tls.q_words[j]==data(i)->q8_expected[j],
                   "FP_Q8_SAVED_STATE_WRONG");
            expect(tasks[i].fp_tls.q_words[j+2]==data(i)->q9_expected[j],
                   "FP_Q9_SAVED_STATE_WRONG");
        }
        field_uart(i?"TASK1_IRQS":"TASK0_IRQS",data(i)->irqs);
        field_uart(i?"TASK1_WORK":"TASK0_WORK",data(i)->work);
        field_uart(i?"TASK1_SIGNED_FRAME_IRQS":"TASK0_SIGNED_FRAME_IRQS",tasks[i].body_irqs);
        expect(data(i)->owner==i+1,"PRIVATE_DATA_PAGE_CORRUPTED");
        expect(data(i)->irqs==ROUNDS,"WRONG_USER_TIMER_COUNT");
        expect(data(i)->work>0&&tasks[i].body_irqs>0,"NO_SIGNED_STACK_FRAME_PREEMPTION");
    }
    expect(total_faults==6,"WRONG_PERMISSION_FAULT_COUNT");
    expect(data(0)->signed_function!=data(1)->signed_function,"CONTEXT_PAC_KEYS_NOT_DISTINCT");
    expect(switches>=2*ROUNDS-2,"NOT_ENOUGH_PREEMPTIVE_CONTEXT_SWITCHES");
    field_uart("USER_CONTEXT_SWITCHES",switches);
    field_uart("EL0_PERMISSION_FAULTS",total_faults);
    field_uart("EL0_SVCS",total_svcs);
    field_uart("EL0_TIMER_IRQS",total_irqs);
    field_uart("FP_Q_REGISTERS_SAVED",2*2);
    field_uart("TLS_CONTEXT_SWITCHES",switches);
    puts_uart("E06 EL0 FP SIMD TLS MMU PAC IRQ CONTEXT PASS\n");platform_exit(0);
}
void exception_irq(struct exception_frame *f) {
    uint64_t id=READ_SYSREG(icc_iar1_el1);
    expect((id&0xffffff)==27,"WRONG_USER_IRQ_ID");
    check_entry(f);
    expect((READ_SYSREG(cntv_ctl_el0)&4)!=0,"USER_TIMER_NOT_PENDING");
    WRITE_SYSREG(cntv_ctl_el0,0);isb();
    total_irqs++;data(current)->irqs++;
    if(f->elr>=user_pc(signed_body_start)&&f->elr<user_pc(signed_body_end)) tasks[current].body_irqs++;
    WRITE_SYSREG(icc_eoir1_el1,id);isb();
    unsigned next=current^1;
    if(tasks[next].done)next=current;
    resume_task(f,next);
}
void exception_sync(struct exception_frame *f) {
    uint64_t esr=READ_SYSREG(esr_el1),ec=esr>>26,far=READ_SYSREG(far_el1);
    check_entry(f);
    if(bad_key_armed && (ec==0x1c || ec==0x20)) {
        field_uart("AUTH_FAILURE_ESR",esr);field_uart("AUTH_FAILURE_ELR",f->elr);
        fail("PAC_AUTH_FAILURE_AFTER_SWITCH");
    }
    struct task *t=&tasks[current];
    if(ec==0x24 && (esr&63)==15) {
        if(t->phase==0&&!(esr&64)&&far==USER_GUARD&&f->elr==user_pc(user_kernel_read))t->phase=1;
        else if(t->phase==1&&(esr&64)&&far==USER_CODE&&f->elr==user_pc(user_code_write))t->phase=2;
        else {field_uart("BAD_DATA_ESR",esr);field_uart("BAD_DATA_FAR",far);fail("UNEXPECTED_EL0_DATA_ABORT");}
        total_faults++;f->elr+=4;return;
    }
    if(ec==0x20 && (esr&63)==15 && far==USER_DATA+0x100 && t->phase==2) {
        expect(f->elr==USER_DATA+0x100&&f->x[30]==user_pc(user_after_xn),"WRONG_XN_ABORT_CONTEXT");
        t->phase=3;total_faults++;f->elr=user_pc(user_after_xn);return;
    }
    if(ec==0x15) {
        total_svcs++;
        unsigned number=esr&0xffff;
        if(number==0x10) {
            expect(t->phase==3,"MISSING_EL0_PERMISSION_FAULT");
            return;
        }
        if(number==0x20) {
            expect(t->phase==3,"USER_DONE_BEFORE_PROTECTION_CHECKS");
            t->done=1;
            if(tasks[0].done&&tasks[1].done)finish();
            resume_task(f,current^1);return;
        }
        if(number==0x7f) {
            field_uart("USER_ERROR",f->x[0]);
            if(f->x[0]==3)fail("TIMER_PREEMPTION_MISSING");
            if(f->x[0]==5)fail("PAC_INSTRUCTIONS_BYPASSED");
            if(f->x[0]==7)fail("FP_SIMD_CONTEXT_CROSSTALK");
            if(f->x[0]==8)fail("TPIDR_EL0_CONTEXT_CROSSTALK");
            fail("USER_TASK_STATE_FAILED");
        }
    }
    field_uart("FAULT_PHASE",t->phase);exception_bad(f);
}
static void setup_gic(void) {
    uint32_t ctl=mmio32(GICD);expect(ctl&(1u<<6),"GIC_PROFILE");
    write32(GICR+0x14,mmio32(GICR+0x14)&~2u);
    unsigned n=1000000;while(mmio32(GICR+0x14)&4u)if(!--n)fail("GICR_WAKE_TIMEOUT");
    write32(SGI+0x180,PPI27);write32(SGI+0x280,PPI27);write32(SGI+0x380,PPI27);
    write32(SGI+0x80,mmio32(SGI+0x80)|PPI27);
    *(volatile uint8_t *)(uintptr_t)(SGI+0x400+27)=0x80;
    write32(SGI+0xc04,mmio32(SGI+0xc04)&~(3u<<22));
    write32(GICD,ctl|2u);
    WRITE_SYSREG(icc_sre_el1,1);isb();WRITE_SYSREG(icc_pmr_el1,0xff);
    WRITE_SYSREG(icc_bpr1_el1,0);WRITE_SYSREG(icc_ctlr_el1,0);WRITE_SYSREG(icc_igrpen1_el1,1);
#ifndef DISABLE_PPI
    write32(SGI+0x100,PPI27);
#endif
    dsb_sy();isb();
}
static void setup_mmu(void) {
    for(unsigned i=0;i<2;i++) {
        root[i][0]=AF|INNER|1;
        for(uint64_t a=0x10000000;a<0x70000000;a+=BLOCK)root[i][a>>25]=a|AF|4|PXN|UXN|1;
        for(uint64_t a=0x70000000;a<0x90000000;a+=BLOCK)root[i][a>>25]=a|AF|INNER|PXN|UXN|1;
        root[i][USER_CODE>>25]=(uintptr_t)leaves[i]|3;
        leaves[i][0]=(uintptr_t)__user_page_start|AF|INNER|NG|USER|RO|PXN|3;
        leaves[i][1]=data_pa[i]|AF|INNER|NG|USER|PXN|UXN|3;
        leaves[i][2]=(data_pa[i]+PAGE)|AF|INNER|NG|USER|PXN|UXN|3;
        leaves[i][3]=(data_pa[i]+2*PAGE)|AF|INNER|NG|PXN|UXN|3;
#ifdef REMOVE_DATA_XN
        leaves[i][1]&=~UXN;
#endif
        data(i)->owner=i+1;data(i)->goal=ROUNDS;
        data(i)->tls_expected=0x7711000000000000ull|((uint64_t)i+1);
        for(unsigned j=0;j<4;j++)
            tasks[i].fp_tls.q_words[j]=(0x1010202000112233ull ^ ((uint64_t)i+1)*0x1111111111111111ull) + j*0x1379231ull;
        for(unsigned j=0;j<2;j++) {
            data(i)->q8_expected[j]=tasks[i].fp_tls.q_words[j];
            data(i)->q9_expected[j]=tasks[i].fp_tls.q_words[j+2];
        }
        tasks[i].fp_tls.tls=data(i)->tls_expected;
        tasks[i].fp_tls.fpcr=(uint64_t)(i+1)<<22; /* distinct legal RMode values */
        tasks[i].fp_tls.fpsr=(uint64_t)(i+1); /* architected cumulative flags */
        data(i)->fpcr_expected=tasks[i].fp_tls.fpcr;
        data(i)->fpsr_expected=tasks[i].fp_tls.fpsr;
        *(uint32_t *)(uintptr_t)(data_pa[i]+0x100)=0xd65f03c0; /* RET for UXN negative */
        tasks[i].frame.elr=user_pc(user_entry);tasks[i].frame.spsr=0;
        tasks[i].sp=USER_STACK+PAGE;
        tasks[i].lo=0x1100558822446699ull^(0x66667777ull*i);
        tasks[i].hi=0xffeeddccbbaa9988ull^(0x33334444ull*i);
    }
    dsb_sy();WRITE_SYSREG(mair_el1,0xff);
    WRITE_SYSREG(tcr_el1,32ull|(1ull<<8)|(1ull<<10)|(3ull<<12)|(2ull<<14)|(32ull<<16)|(1ull<<23));
    WRITE_SYSREG(ttbr0_el1,(uintptr_t)root[0]|(0x31ull<<48));WRITE_SYSREG(ttbr1_el1,0);
    __asm__ volatile("dsb ishst\n\ttlbi vmalle1is\n\tdsb ish\n\tisb" ::: "memory");
    uint64_t sctlr=READ_SYSREG(sctlr_el1);
    sctlr&=~((1ull<<19)|(1ull<<24)|(1ull<<25));
    sctlr|=1ull|(1ull<<2)|(1ull<<12)|(1ull<<31)|(1ull<<30)|(1ull<<27)|(1ull<<13);
    WRITE_SYSREG(sctlr_el1,sctlr);isb();
    WRITE_SYSREG(cntkctl_el1,READ_SYSREG(cntkctl_el1)|2);isb(); /* EL0 CNTVCT allowed */
}
void main(void) {
    expect(READ_SYSREG(CurrentEL)==4,"START_NOT_EL1");
    puts_uart("E06 EL0 CONTEXT BEGIN\n");
    /* Permit EL0 SIMD/FP instructions; without this the q-register work
       would trap before a context save and the test must not pass. */
    WRITE_SYSREG(cpacr_el1,READ_SYSREG(cpacr_el1)|(3ull<<20));isb();
    setup_mmu();setup_gic();
    freq=READ_SYSREG(cntfrq_el0);expect(freq>10000,"BAD_TIMER_FREQUENCY");
#ifdef APPLE_24MHZ
    expect(freq==24000000,"APPLE_DEVICE_TREE_COUNTER_FREQUENCY_MISMATCH");
#endif
    field_uart("CNTFRQ_HZ",freq);
#ifdef USE_PAC_MODEL
    model_call(0,0);
    kernel_key_lo=READ_SYSREG(APIAKeyLo_EL1);kernel_key_hi=READ_SYSREG(APIAKeyHi_EL1);
#else
    (void)kernel_key_lo;(void)kernel_key_hi;
#endif
    uint64_t deadline=READ_SYSREG(cntvct_el0)+2*freq;
    data(0)->deadline=deadline;data(1)->deadline=deadline;
    set_context(0,0);
    restore_fp_tls(0);
    WRITE_SYSREG(cntv_cval_el0,READ_SYSREG(cntvct_el0)+freq/500);
    WRITE_SYSREG(cntv_ctl_el0,1);isb();
    field_uart("EL0_ROUNDS_PER_TASK",ROUNDS);
    enter_first_user(user_pc(user_entry),USER_STACK+PAGE);
}
