/* Full Q0..Q31 FP/SIMD and TPIDR_EL0 variant. Different ASIDs and physical pages,
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
#ifdef SMP_MIGRATION
static unsigned cpu_id(void) { return (unsigned)(READ_SYSREG(mpidr_el1)&255); }
#define REDIST (GICR+cpu_id()*0x20000ull)
#else
#define REDIST GICR
#endif
#define SGI (REDIST+0x10000)
#define PPI27 (1u<<27)
#ifndef ROUNDS
#define ROUNDS 12
#endif
#if defined(LAZY_FP) && (!defined(SMP_MIGRATION) || defined(USE_PAC_MODEL))
#error "LAZY_FP is a separate architectural two-CPU migration mode"
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
    uint64_t q_words[64];
    uint64_t fpcr,fpsr,tls;
} __attribute__((aligned(16)));

struct task {
    struct exception_frame frame;
    uint64_t sp,lo,hi,ttbr;
    unsigned phase,done,body_irqs;
    struct fp_state fp_tls;
};
static uint64_t root[2][2048] __attribute__((aligned(16384)));
static uint64_t leaves[2][2048] __attribute__((aligned(16384)));
static struct task tasks[2];
#ifdef SMP_MIGRATION
static unsigned cpu_current[2],cpu_switches[2],cpu_irqs[2],cpu_faults[2],cpu_svcs[2];
static unsigned cpu_bad_key[2],cpu_bad_asid[2];
#define current cpu_current[cpu_id()]
#define switches cpu_switches[cpu_id()]
#define total_irqs cpu_irqs[cpu_id()]
#define total_faults cpu_faults[cpu_id()]
#define total_svcs cpu_svcs[cpu_id()]
#define bad_key_armed cpu_bad_key[cpu_id()]
static uint64_t online,go,ready[2],finished[2],task_owner[2],epoch[2];
static uint64_t cpu_freq[2];
static unsigned task_cpu_irqs[2][2],task_cpu_body_irqs[2][2];
#ifdef LAZY_FP
static unsigned fp_loaded[2],fp_owner_task[2],lazy_skipped[2];
static unsigned fp_traps[2][2],fp_restores[2][2],fp_deferred[2][2],fp_saved_memory_reused[2][2];
static unsigned fp_irq_before_access[2][2];
static uint64_t fp_last_esr[2],fp_last_elr[2];
static unsigned fp_exact_elr_checks[2];
/* Pinned QEMU 1a72df9 helper.c:10289..10313: FPEN=01 traps EL0 only,
   while EL1 can still save/restore FP. FPEN=11 permits both. */
static void fp_el0_access(unsigned fpen) {
    uint64_t cpacr=READ_SYSREG(cpacr_el1);
    WRITE_SYSREG(cpacr_el1,(cpacr&~(3ull<<20))|((uint64_t)fpen<<20));isb();
}
#endif
static unsigned failure_reporter;
static void migration_fail(const char *reason) __attribute__((noreturn));
static void migration_fail(const char *reason) {
    unsigned expected=0;
    if(!__atomic_compare_exchange_n(&failure_reporter,&expected,1,0,
                                    __ATOMIC_ACQUIRE,__ATOMIC_RELAXED))
        for(;;)__asm__ volatile("wfi");
    puts_uart("FAIL: ");puts_uart(reason);puts_uart("\n");platform_exit(1);
}
/* Negative controls can fail on both CPUs together. One CPU owns the UART
   failure record; the other must not interleave its characters. */
#define fail migration_fail
static uint64_t acquire(const uint64_t *p) {
    uint64_t v;__asm__ volatile("ldar %0,[%1]":"=r"(v):"r"(p):"memory");return v;
}
static void release(uint64_t *p,uint64_t v) {
    __asm__ volatile("stlr %0,[%1]"::"r"(v),"r"(p):"memory");
}
static void wait_at_least(const uint64_t *p,uint64_t wanted) {
    uint64_t deadline=READ_SYSREG(cntvct_el0)+READ_SYSREG(cntfrq_el0)*5;
    while(acquire(p)<wanted) {
        if(READ_SYSREG(cntvct_el0)>deadline)fail("MIGRATION_PEER_GUEST_DEADLINE");
        __asm__ volatile("yield":::"memory");
    }
}
static void claim_task(unsigned id) {
    uint64_t expected=0;
    expect(__atomic_compare_exchange_n(&task_owner[id],&expected,cpu_id()+1,0,
                                      __ATOMIC_ACQUIRE,__ATOMIC_RELAXED),
           "TASK_SIMULTANEOUS_CPU_OWNERSHIP");
}
extern void secondary_entry(void);
#else
static unsigned current, switches, total_irqs, total_faults, total_svcs;
static unsigned bad_key_armed;
#endif
static uint64_t freq;
#if defined(SMP_MIGRATION) && defined(USE_PAC_MODEL)
static uint64_t cpu_kernel_key_lo[2],cpu_kernel_key_hi[2];
static unsigned model_initialized[2],kernel_bank_checks[2];
#define kernel_key_lo cpu_kernel_key_lo[cpu_id()]
#define kernel_key_hi cpu_kernel_key_hi[cpu_id()]
#else
static uint64_t kernel_key_lo,kernel_key_hi;
#endif
#ifdef USE_PAC_MODEL
/* Explicit task context is portable across CPUs; EL1 key reads are only the
   CPU's kernel-bank oracle and are never saved as model user-task keys. */
static uint64_t task_model_context[2];
#endif
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
static void initialize_model_cpu(void) {
    model_call(0,0);
    kernel_key_lo=READ_SYSREG(APIAKeyLo_EL1);kernel_key_hi=READ_SYSREG(APIAKeyHi_EL1);
#ifdef SMP_MIGRATION
    model_initialized[cpu_id()]=1;
#endif
}
#endif
/* FP instructions are in a standalone .S module; all C vectors and IRQ
 * handlers compile with -mgeneral-regs-only to avoid incidental SIMD use. */
extern void fp_save_regs(uint64_t *data);
extern void fp_restore_regs(const uint64_t *data);
static void save_fp_tls(unsigned id) {
    struct fp_state *f=&tasks[id].fp_tls;
#ifdef LAZY_FP
    /* An IRQ can arrive before this task's first FP access. Its complete
       saved FP state remains authoritative; these CPU registers belong to
       an older task and must never overwrite it. */
    if(fp_loaded[cpu_id()]) {
        expect(fp_owner_task[cpu_id()]==id,"LAZY_FP_CPU_OWNER_MISMATCH");
        fp_save_regs(f->q_words);
    } else fp_saved_memory_reused[id][cpu_id()]++;
#else
    fp_save_regs(f->q_words);
#endif
    f->tls=READ_SYSREG(tpidr_el0);
}
static void restore_fp_tls(unsigned id) {
    struct fp_state *f=&tasks[id].fp_tls;
#ifdef LAZY_FP
    fp_loaded[cpu_id()]=0;fp_deferred[id][cpu_id()]++;
    fp_el0_access(1);
#else
#ifndef SKIP_FP_RESTORE
    fp_restore_regs(f->q_words);
#endif
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
#ifdef SMP_MIGRATION
    ttbr=tasks[id].ttbr;
#ifdef WRONG_RESTORE_ASID
    if(epoch[cpu_id()]>=2) {ttbr=tasks[id^1].ttbr;cpu_bad_asid[cpu_id()]=1;}
#endif
#endif
    WRITE_SYSREG(ttbr0_el1,ttbr);isb();
    WRITE_SYSREG(sp_el0,tasks[id].sp);
#ifdef USE_PAC_MODEL
    model_call(4,task_model_context[id]+(bad?0x4000:0));
#else
    key_write(tasks[id].lo^(bad?0x442211:0),tasks[id].hi);
#endif
}
static void check_entry(struct exception_frame *f) {
    expect((f->spsr&15)==0,"EXCEPTION_NOT_FROM_EL0");
    uint64_t asid=READ_SYSREG(ttbr0_el1)>>48;
#ifdef SMP_MIGRATION
    if(!cpu_bad_asid[cpu_id()])expect(asid==0x31+current,"EXCEPTION_WRONG_ADDRESS_SPACE");
    expect(acquire(&task_owner[current])==cpu_id()+1,"TASK_SIMULTANEOUS_CPU_OWNERSHIP");
    expect(READ_SYSREG(tpidrro_el0)==cpu_id(),"CPU_LOCAL_EL0_ID_MISMATCH");
#else
    expect(asid==0x31+current,"EXCEPTION_WRONG_ADDRESS_SPACE");
#endif
    uint64_t lo=READ_SYSREG(APIAKeyLo_EL1),hi=READ_SYSREG(APIAKeyHi_EL1);
#ifdef USE_PAC_MODEL
    expect(lo==kernel_key_lo&&hi==kernel_key_hi,"IRQ_SVC_DID_NOT_RESTORE_EL1_KEY_BANK");
#ifdef SMP_MIGRATION
    kernel_bank_checks[cpu_id()]++;
#endif
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
    if(data(current)->irqs<ROUNDS && !bad_key_armed
#ifdef SMP_MIGRATION
       && !cpu_bad_asid[cpu_id()]
#endif
#ifdef LAZY_FP
       && !lazy_skipped[cpu_id()]
#endif
       ) {
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
#ifdef SMP_MIGRATION
    unsigned cpu=cpu_id(),previous=current;
    tasks[previous].ttbr=READ_SYSREG(ttbr0_el1);
#ifndef USE_PAC_MODEL
    tasks[previous].lo=READ_SYSREG(APIAKeyLo_EL1);
    tasks[previous].hi=READ_SYSREG(APIAKeyHi_EL1);
#endif
    expect(acquire(&task_owner[previous])==cpu+1,"TASK_SAVE_WITHOUT_OWNERSHIP");
    /* Publish only after integer/SP, all Qs/FP/TLS, TTBR and keys are saved.
       Both CPUs are in EL1 before either may claim the other's task. */
    release(&task_owner[previous],0);
    release(&ready[cpu],++epoch[cpu]);
    wait_at_least(&ready[cpu^1],epoch[cpu]);
    claim_task(next);
#endif
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
#ifdef SMP_MIGRATION
    total_faults=cpu_faults[0]+cpu_faults[1];
    total_svcs=cpu_svcs[0]+cpu_svcs[1];
    total_irqs=cpu_irqs[0]+cpu_irqs[1];
    switches=cpu_switches[0]+cpu_switches[1];
#endif
    for(unsigned i=0;i<2;i++) {
        expect(tasks[i].fp_tls.tls==data(i)->tls_expected,"TLS_SAVED_STATE_WRONG");
        expect(tasks[i].fp_tls.fpcr==data(i)->fpcr_expected,"FPCR_SAVED_STATE_WRONG");
        expect(tasks[i].fp_tls.fpsr==data(i)->fpsr_expected,"FPSR_SAVED_STATE_WRONG");
        /* All 32 registers (64 64-bit words) must be preserved by the
           actual IRQ exception route, not just by the user-side comparison. */
        volatile uint64_t *ref=(void *)(uintptr_t)(data_pa[i]+0x200);
        for(unsigned j=0;j<64;j++) {
            expect(tasks[i].fp_tls.q_words[j]==ref[j],
                   "FP_ALL_Q_REGISTERS_SAVED_STATE_WRONG");
        }
        for(unsigned j=0;j<2;j++) {
            expect(tasks[i].fp_tls.q_words[16+j]==data(i)->q8_expected[j],
                   "FP_Q8_SAVED_STATE_WRONG");
            expect(tasks[i].fp_tls.q_words[18+j]==data(i)->q9_expected[j],
                   "FP_Q9_SAVED_STATE_WRONG");
        }
        field_uart(i?"TASK1_IRQS":"TASK0_IRQS",data(i)->irqs);
        field_uart(i?"TASK1_WORK":"TASK0_WORK",data(i)->work);
        field_uart(i?"TASK1_SIGNED_FRAME_IRQS":"TASK0_SIGNED_FRAME_IRQS",tasks[i].body_irqs);
#ifdef SMP_MIGRATION
        volatile uint64_t *seen=(void *)(uintptr_t)(data_pa[i]+0x600);
        field_uart(i?"TASK1_EL0_CPU0_CHECKS":"TASK0_EL0_CPU0_CHECKS",seen[0]);
        field_uart(i?"TASK1_EL0_CPU1_CHECKS":"TASK0_EL0_CPU1_CHECKS",seen[1]);
        field_uart(i?"TASK1_EL0_CPU_CHANGES":"TASK0_EL0_CPU_CHANGES",seen[3]);
        field_uart(i?"TASK1_EL0_CPU_MASK":"TASK0_EL0_CPU_MASK",(seen[0]?1u:0u)|(seen[1]?2u:0u));
        field_uart(i?"TASK1_CPU0_IRQS":"TASK0_CPU0_IRQS",task_cpu_irqs[i][0]);
        field_uart(i?"TASK1_CPU1_IRQS":"TASK0_CPU1_IRQS",task_cpu_irqs[i][1]);
        field_uart(i?"TASK1_CPU0_SIGNED_IRQS":"TASK0_CPU0_SIGNED_IRQS",task_cpu_body_irqs[i][0]);
        field_uart(i?"TASK1_CPU1_SIGNED_IRQS":"TASK0_CPU1_SIGNED_IRQS",task_cpu_body_irqs[i][1]);
        expect(seen[0]>0&&seen[1]>0&&seen[3]>0,"MISSING_ACTUAL_EL0_MIGRATION_OBSERVATION");
        expect(task_cpu_irqs[i][0]>0&&task_cpu_irqs[i][1]>0,"MISSING_CROSS_CPU_EL0_IRQ");
        expect(task_cpu_body_irqs[i][0]>0&&task_cpu_body_irqs[i][1]>0,"MISSING_CROSS_CPU_SIGNED_FRAME_IRQ");
#ifdef LAZY_FP
        field_uart(i?"TASK1_CPU0_FP_TRAPS":"TASK0_CPU0_FP_TRAPS",fp_traps[i][0]);
        field_uart(i?"TASK1_CPU1_FP_TRAPS":"TASK0_CPU1_FP_TRAPS",fp_traps[i][1]);
        field_uart(i?"TASK1_CPU0_FP_RESTORES":"TASK0_CPU0_FP_RESTORES",fp_restores[i][0]);
        field_uart(i?"TASK1_CPU1_FP_RESTORES":"TASK0_CPU1_FP_RESTORES",fp_restores[i][1]);
        field_uart(i?"TASK1_CPU0_FP_DEFERRED":"TASK0_CPU0_FP_DEFERRED",fp_deferred[i][0]);
        field_uart(i?"TASK1_CPU1_FP_DEFERRED":"TASK0_CPU1_FP_DEFERRED",fp_deferred[i][1]);
        field_uart(i?"TASK1_CPU0_SAVED_FP_REUSED":"TASK0_CPU0_SAVED_FP_REUSED",fp_saved_memory_reused[i][0]);
        field_uart(i?"TASK1_CPU1_SAVED_FP_REUSED":"TASK0_CPU1_SAVED_FP_REUSED",fp_saved_memory_reused[i][1]);
        field_uart(i?"TASK1_CPU0_IRQ_BEFORE_FP":"TASK0_CPU0_IRQ_BEFORE_FP",fp_irq_before_access[i][0]);
        field_uart(i?"TASK1_CPU1_IRQ_BEFORE_FP":"TASK0_CPU1_IRQ_BEFORE_FP",fp_irq_before_access[i][1]);
        for(unsigned c=0;c<2;c++)
            expect(fp_traps[i][c]>0&&fp_restores[i][c]==fp_traps[i][c],"LAZY_FP_CROSS_CPU_TRAP_OR_RESTORE_MISSING");
        expect(fp_irq_before_access[i][0]+fp_irq_before_access[i][1]>0,
               "LAZY_FP_PENDING_IRQ_PATH_NOT_EXECUTED");
#endif
#endif
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
    field_uart("FP_Q_REGISTERS_SAVED",32*2);
    field_uart("TLS_CONTEXT_SWITCHES",switches);
#ifdef SMP_MIGRATION
    field_uart("CPU0_CNTFRQ_HZ",cpu_freq[0]);field_uart("CPU1_CNTFRQ_HZ",cpu_freq[1]);
#ifdef USE_PAC_MODEL
    field_uart("PAC_MODEL_ENABLED",1);
    field_uart("CPU0_MODEL_INITIALIZED",model_initialized[0]);
    field_uart("CPU1_MODEL_INITIALIZED",model_initialized[1]);
    field_uart("CPU0_KERNEL_BANK_CHECKS",kernel_bank_checks[0]);
    field_uart("CPU1_KERNEL_BANK_CHECKS",kernel_bank_checks[1]);
#endif
#ifdef LAZY_FP
    field_uart("LAZY_FP_ENABLED",1);
    field_uart("CPU0_FP_EXACT_ELR_CHECKS",fp_exact_elr_checks[0]);
    field_uart("CPU1_FP_EXACT_ELR_CHECKS",fp_exact_elr_checks[1]);
    field_uart("CPU0_FP_LAST_ESR",fp_last_esr[0]);field_uart("CPU1_FP_LAST_ESR",fp_last_esr[1]);
    field_uart("CPU0_FP_LAST_ELR",fp_last_elr[0]);field_uart("CPU1_FP_LAST_ELR",fp_last_elr[1]);
#endif
    puts_uart("E06 DUAL CPU EL0 MIGRATION ALL32 TLS ASID QARMA5 PASS\n");
#else
    puts_uart("E06 EL0 ALL32 SIMD TLS MMU PAC IRQ CONTEXT PASS\n");
#endif
    platform_exit(0);
}
void exception_irq(struct exception_frame *f) {
    uint64_t id=READ_SYSREG(icc_iar1_el1);
    expect((id&0xffffff)==27,"WRONG_USER_IRQ_ID");
    check_entry(f);
#ifdef LAZY_FP
    if(!fp_loaded[cpu_id()])fp_irq_before_access[current][cpu_id()]++;
#endif
    expect((READ_SYSREG(cntv_ctl_el0)&4)!=0,"USER_TIMER_NOT_PENDING");
    WRITE_SYSREG(cntv_ctl_el0,0);isb();
    total_irqs++;data(current)->irqs++;
#ifdef SMP_MIGRATION
    task_cpu_irqs[current][cpu_id()]++;
    if(f->elr>=user_pc(signed_body_start)&&f->elr<user_pc(signed_body_end))task_cpu_body_irqs[current][cpu_id()]++;
#endif
    if(f->elr>=user_pc(signed_body_start)&&f->elr<user_pc(signed_body_end)) tasks[current].body_irqs++;
    WRITE_SYSREG(icc_eoir1_el1,id);isb();
    unsigned next=current^1;
    if(tasks[next].done)next=current;
    resume_task(f,next);
}
void exception_sync(struct exception_frame *f) {
    uint64_t esr=READ_SYSREG(esr_el1),ec=esr>>26,far=READ_SYSREG(far_el1);
    check_entry(f);
#ifdef LAZY_FP
    if(ec==0x07) {
        unsigned cpu=cpu_id();uint64_t trapped=f->elr;
        expect(!fp_loaded[cpu],"UNEXPECTED_FP_ACCESS_TRAP");
        expect(((READ_SYSREG(cpacr_el1)>>20)&3)==1,"FP_TRAP_WRONG_CPACR_FPEN");
        expect((esr&(1ull<<25))&&(esr&0xfffff)==0,"FP_ACCESS_TRAP_SYNDROME");
        /* Every potentially trapping user FP instruction has an independent
           preceding ADR x24,<exact site>. The vector preserves x24 even if
           a timer IRQ lands between that ADR and the instruction. */
        expect(trapped==f->x[24]&&trapped==READ_SYSREG(elr_el1)&&
               trapped>=USER_CODE&&trapped<USER_CODE+PAGE&&(trapped&3)==0,
               "FP_ACCESS_TRAP_ELR_NOT_EXACT_INSTRUCTION");
        fp_traps[current][cpu]++;fp_exact_elr_checks[cpu]++;
        fp_last_esr[cpu]=esr;fp_last_elr[cpu]=trapped;
#ifdef SKIP_LAZY_FP_RESTORE
        if(epoch[cpu]>=2&&fp_owner_task[cpu]!=current) {
            lazy_skipped[cpu]=1;
            /* After injecting stale FP state, let the actual EL0 comparison
               report it before another timer save can catch the owner guard. */
            WRITE_SYSREG(cntv_ctl_el0,0);isb();
        }
        else
#endif
        {
            fp_restore_regs(tasks[current].fp_tls.q_words);
            fp_restores[current][cpu]++;fp_owner_task[cpu]=current;
        }
        fp_loaded[cpu]=1;fp_el0_access(3);
        expect(f->elr==trapped,"FP_ACCESS_RETRY_ELR_CHANGED");
        /* Retry the identical instruction; never advance ELR for this trap. */
        return;
    }
#endif
#ifdef SMP_MIGRATION
    if(cpu_bad_asid[cpu_id()]&&(ec==0x1c||ec==0x20||ec==0x24))
        fail("EL0_ADDRESS_SPACE_RESTORE_FAILURE");
#endif
    if(bad_key_armed && (ec==0x1c || ec==0x20)) {
#ifndef SMP_MIGRATION
        field_uart("AUTH_FAILURE_ESR",esr);field_uart("AUTH_FAILURE_ELR",f->elr);
#endif
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
#ifdef SMP_MIGRATION
            copy_frame(&t->frame,f);t->sp=READ_SYSREG(sp_el0);save_fp_tls(current);
            release(&finished[cpu_id()],1);
            if(cpu_id()==1)for(;;)__asm__ volatile("wfi");
            wait_at_least(&finished[1],1);finish();
#else
            if(tasks[0].done&&tasks[1].done)finish();
            resume_task(f,current^1);return;
#endif
        }
        if(number==0x7f) {
#ifndef SMP_MIGRATION
            field_uart("USER_ERROR",f->x[0]);
#endif
#ifdef SMP_MIGRATION
            if(cpu_bad_asid[cpu_id()]&&(f->x[0]==1||f->x[0]==7||f->x[0]==8))
                fail("EL0_ADDRESS_SPACE_RESTORE_FAILURE");
#endif
            if(f->x[0]==3)fail("TIMER_PREEMPTION_MISSING");
            if(f->x[0]==5)fail("PAC_INSTRUCTIONS_BYPASSED");
#ifdef SKIP_LAZY_FP_RESTORE
            if(f->x[0]==7&&lazy_skipped[cpu_id()])fail("LAZY_FP_CONTEXT_RESTORE_MISSING");
#endif
            if(f->x[0]==7)fail("FP_SIMD_CONTEXT_CROSSTALK");
            if(f->x[0]==8)fail("TPIDR_EL0_CONTEXT_CROSSTALK");
            fail("USER_TASK_STATE_FAILED");
        }
    }
    field_uart("FAULT_PHASE",t->phase);exception_bad(f);
}
static void setup_gic(void) {
    uint32_t ctl=mmio32(GICD);expect(ctl&(1u<<6),"GIC_PROFILE");
    write32(REDIST+0x14,mmio32(REDIST+0x14)&~2u);
    unsigned n=1000000;while(mmio32(REDIST+0x14)&4u)if(!--n)fail("GICR_WAKE_TIMEOUT");
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
#ifdef SMP_MIGRATION
    if(cpu_id()==0) {
#endif
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
        volatile uint64_t *expected=(void *)(uintptr_t)(data_pa[i]+0x200);
        volatile uint64_t *observed=(void *)(uintptr_t)(data_pa[i]+0x400);
        for(unsigned j=0;j<64;j++) {
            uint64_t v=(0x1010202000112233ull ^ ((uint64_t)i+1)*0x1111111111111111ull)
                       + j*0x1379231ull + (j*j)*0x271a9ull;
            tasks[i].fp_tls.q_words[j]=v;
            expected[j]=v;
            observed[j]=0;
        }
        for(unsigned j=0;j<2;j++) {
            data(i)->q8_expected[j]=tasks[i].fp_tls.q_words[16+j];
            data(i)->q9_expected[j]=tasks[i].fp_tls.q_words[18+j];
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
        tasks[i].ttbr=(uintptr_t)root[i]|((uint64_t)(0x31+i)<<48);
#ifdef USE_PAC_MODEL
        task_model_context[i]=0x1100+i;
#endif
#ifdef SMP_MIGRATION
        volatile uint64_t *seen=(void *)(uintptr_t)(data_pa[i]+0x600);
        seen[0]=seen[1]=seen[3]=0;seen[2]=2;
#endif
    }
#ifdef SMP_MIGRATION
    }
#endif
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
#ifdef SMP_MIGRATION
static void begin_migration_user(unsigned id) __attribute__((noreturn));
static void begin_migration_user(unsigned id) {
    WRITE_SYSREG(tpidrro_el0,cpu_id());
#ifdef LAZY_FP
    fp_owner_task[cpu_id()]=2;
#endif
    claim_task(id);set_context(id,0);restore_fp_tls(id);
    WRITE_SYSREG(cntv_cval_el0,READ_SYSREG(cntvct_el0)+freq/500);
    WRITE_SYSREG(cntv_ctl_el0,1);isb();
    enter_first_user(user_pc(user_entry),USER_STACK+PAGE);
}
void secondary_main(void) {
    expect(cpu_id()==1,"SECONDARY_CPU_ID");
    WRITE_SYSREG(cpacr_el1,READ_SYSREG(cpacr_el1)|(3ull<<20));isb();
    setup_mmu();setup_gic();
    cpu_freq[1]=READ_SYSREG(cntfrq_el0);expect(cpu_freq[1]==24000000,"SECONDARY_CNTFRQ_MISMATCH");
#ifdef USE_PAC_MODEL
    initialize_model_cpu();
#endif
    release(&online,1);wait_at_least(&go,1);begin_migration_user(1);
}
#endif
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
    initialize_model_cpu();
#endif
#ifdef SMP_MIGRATION
    expect(cpu_id()==0,"PRIMARY_CPU_ID");cpu_freq[0]=freq;
    register uint64_t x0 __asm__("x0")=0xc4000003;
    register uint64_t x1 __asm__("x1")=1;
    register uint64_t x2 __asm__("x2")=(uintptr_t)secondary_entry;
    register uint64_t x3 __asm__("x3")=0;
    __asm__ volatile("hvc #0":"+r"(x0),"+r"(x1),"+r"(x2),"+r"(x3)::"memory");
    expect(x0==0,"PSCI_CPU_ON_FAILED");wait_at_least(&online,1);
    data(0)->deadline=data(1)->deadline=READ_SYSREG(cntvct_el0)+10*freq;
    (void)kernel_key_lo;(void)kernel_key_hi;
    release(&go,1);begin_migration_user(0);
#else
#ifndef USE_PAC_MODEL
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
#endif
}
