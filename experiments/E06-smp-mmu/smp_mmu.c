/* Two self-authored ARM64 EL1 CPUs; guest translation + cross-CPU TLBI.
 * No Apple code, restore session or real OS. MMU tables are 16 KiB.
 * Synthetic negatives alter guest test PTEs, not the QEMU implementation.
 */
#include "runtime.h"
#ifndef ROUNDS
#define ROUNDS 32
#endif
#define PAGE 0x4000ull
#define BLOCK 0x02000000ull
#define VA 0xa0000000ull
#define PA_A 0x70200000ull
#define PA_B 0x70204000ull
#define PA_C 0x70208000ull
#define ASID_A 0x11ull
#define ASID_B 0x22ull
#define AF (1ull<<10)
#define NG (1ull<<11)
#define ISH (3ull<<8)
#define RO (1ull<<7)
#define XN ((1ull<<53)|(1ull<<54))
#define NORMAL (AF|ISH)
#define MAG_A 0x1122334455660000ull
#define MAG_B 0x9988776655440000ull
#define MAG_C 0xabcdef9876540000ull
static uint64_t root[2][2048] __attribute__((aligned(16384)));
static uint64_t leaf[2][2048] __attribute__((aligned(16384)));
/* One producer/consumer, command and reply are release/acquire published. */
struct message {
    uint64_t sequence, done, operation, address_space, expected, replacement;
    uint64_t value, result, faults, esr, far, pc;
} __attribute__((aligned(128)));
static struct message mail;
static uint64_t online __attribute__((aligned(128)));
static uint64_t count __attribute__((aligned(128)));
static volatile uint64_t expect_fault, fault_count, saved_esr, saved_far, saved_pc;
static uint64_t serial;
extern void secondary_entry(void);

static inline uint64_t acquire(const uint64_t *p) {
    uint64_t v; __asm__ volatile("ldar %0,[%1]":"=r"(v):"r"(p):"memory"); return v;
}
static inline void release(uint64_t *p,uint64_t v) {
    __asm__ volatile("stlr %0,[%1]"::"r"(v),"r"(p):"memory");
}
static inline unsigned cpu_id(void) { return READ_SYSREG(mpidr_el1)&255; }
static void deadline_wait(uint64_t *p,uint64_t wanted,const char *why) {
    uint64_t end=READ_SYSREG(cntvct_el0)+READ_SYSREG(cntfrq_el0)*8;
    while(acquire(p)!=wanted) {
        if(READ_SYSREG(cntvct_el0)>end)fail(why);
        __asm__ volatile("yield" ::: "memory");
    }
}
static uint64_t pte(uint64_t pa) { return pa|NORMAL|NG|XN|3; }
static void choose(unsigned context) {
    uint64_t asid=context?ASID_B:ASID_A;
    WRITE_SYSREG(ttbr0_el1,((uint64_t)(uintptr_t)root[context])|(asid<<48));
    isb();
}
static void setup_tables(void) {
    for(unsigned k=0;k<2;k++) {
        root[k][0]=NORMAL|1; /* executable firmware, vectors, tables */
        for(uint64_t a=0x10000000;a<0x70000000;a+=BLOCK)
            root[k][a>>25]=a|AF|4|XN|1;
        for(uint64_t a=0x70000000;a<0x90000000;a+=BLOCK)
            root[k][a>>25]=a|NORMAL|XN|1;
        root[k][VA>>25]=(uintptr_t)leaf[k]|3;
    }
    leaf[0][0]=pte(PA_A);leaf[1][0]=pte(PA_C);
    dsb_sy();
}
static void mmu_on(void) {
    expect(READ_SYSREG(CurrentEL)==4,"CPU_NOT_EL1");
    uint64_t granule=(READ_SYSREG(id_aa64mmfr0_el1)>>20)&15;
    expect(granule==1||granule==2,"16K_UNAVAILABLE");
    WRITE_SYSREG(mair_el1,0x00ff);
    WRITE_SYSREG(tcr_el1,32ull|(1ull<<8)|(1ull<<10)|(3ull<<12)|(2ull<<14)|(32ull<<16)|(1ull<<23));
    WRITE_SYSREG(ttbr1_el1,0);
    choose(0);
    __asm__ volatile("tlbi vmalle1\n\tdsb sy\n\tisb\n\tic iallu\n\tdsb sy\n\tisb" ::: "memory");
    uint64_t sctlr=READ_SYSREG(sctlr_el1);
    sctlr&=~((1ull<<19)|(1ull<<25)|(1ull<<24));
    sctlr|=1ull|(1ull<<2)|(1ull<<12);
    WRITE_SYSREG(sctlr_el1,sctlr);isb();
    expect(READ_SYSREG(sctlr_el1)&1,"MMU_NOT_ENABLED");
}
/* VA[55:12] encoding is independent of the 16 KiB guest granule. */
static void invalidate(unsigned context,unsigned by_asid) {
    uint64_t arg=(context?ASID_B:ASID_A)<<48;
    __asm__ volatile("dsb ishst" ::: "memory");
    if(by_asid) __asm__ volatile("tlbi aside1is,%0"::"r"(arg):"memory");
    else {
        arg|=VA>>12;
        __asm__ volatile("tlbi vae1is,%0"::"r"(arg):"memory");
    }
    __asm__ volatile("dsb ish\n\tisb" ::: "memory");
}
static void replace_page(unsigned ctx,uint64_t entry,unsigned by_asid) {
    leaf[ctx][0]=0;
    invalidate(ctx,by_asid); /* break-before-make after remote access is quiescent */
    leaf[ctx][0]=entry;
    __asm__ volatile("dsb ishst\n\tisb" ::: "memory");
}
static uint64_t increment(unsigned times) {
    uint64_t retries=0;
    for(unsigned i=0;i<times;i++) {
        uint64_t v;unsigned failed,attempts=0;
        __asm__ volatile(
            "1: ldaxr %0,[%3]\n\tadd %0,%0,#1\n\tstlxr %w1,%0,[%3]\n\t"
            "cbz %w1,2f\n\tadd %w2,%w2,#1\n\tb 1b\n2:"
            :"=&r"(v),"=&r"(failed),"+r"(attempts):"r"(&count):"memory");
        retries+=attempts;
    }
    return retries;
}
void exception_sync(struct exception_frame *f) {
    uint64_t esr=READ_SYSREG(esr_el1),far=READ_SYSREG(far_el1);
    if(cpu_id()!=1 || !expect_fault || (esr>>26)!=0x25 || (esr&63)!=15 ||
       ((esr>>6)&1)!=1 || far!=VA || f->elr!=(uintptr_t)probe_store_instruction)
        exception_bad(f);
    saved_esr=esr;saved_far=far;saved_pc=f->elr;
    fault_count++;expect_fault=0;f->elr+=4;
}
static void issue(unsigned op,unsigned context,uint64_t expected,uint64_t replacement) {
    mail.operation=op;mail.address_space=context;
    mail.expected=expected;mail.replacement=replacement;
    release(&mail.sequence,++serial);
}
static void collect(const char *why) {
    deadline_wait(&mail.done,serial,"SECONDARY_RESPONSE_TIMEOUT");
    if(mail.result) {
        field_uart("REMOTE_ERROR",mail.result);field_uart("REMOTE_VALUE",mail.value);
        field_uart("EXPECTED_VALUE",mail.expected);fail(why);
    }
}
static void request(unsigned op,unsigned ctx,uint64_t want,uint64_t set,const char *why) {
    issue(op,ctx,want,set);collect(why);
}
void secondary_main(void) {
    expect(cpu_id()==1,"WRONG_SECONDARY_ID");
    mmu_on();release(&online,1);
    uint64_t last=0;unsigned current=0;
    for(;;) {
        uint64_t seq=acquire(&mail.sequence);
        if(seq==last) { __asm__ volatile("yield" ::: "memory");continue; }
        unsigned next=(unsigned)mail.address_space;
        if(next!=current) { choose(next);current=next; }
        mail.result=0;mail.value=0;
        switch(mail.operation) {
        case 1: /* read */
        case 2: /* read and write */
            mail.value=probe_load(VA);
            if(mail.value!=mail.expected)mail.result=1;
            if(!mail.result && mail.operation==2)probe_store(VA,mail.replacement);
            break;
        case 3: { /* expectation of actual CPU1 permission fault */
            uint64_t before=fault_count;
            expect_fault=1;__asm__ volatile("" ::: "memory");
            probe_store(VA,0xbadc0ffee0ddf00dull);
            __asm__ volatile("" ::: "memory");
            if(fault_count!=before+1||expect_fault)mail.result=2;
            expect_fault=0;
            mail.faults=fault_count;mail.esr=saved_esr;mail.far=saved_far;mail.pc=saved_pc;
            break;
        }
        case 4: mail.value=increment((unsigned)mail.expected);break;
        case 5: release(&mail.done,seq);for(;;)__asm__ volatile("wfi");
        default:mail.result=99;
        }
        last=seq;release(&mail.done,seq);
    }
}
static void start_cpu1(void) {
    register uint64_t x0 __asm__("x0")=0xc4000003;
    register uint64_t x1 __asm__("x1")=1;
    register uint64_t x2 __asm__("x2")=(uintptr_t)secondary_entry;
    register uint64_t x3 __asm__("x3")=0;
    __asm__ volatile("hvc #0":"+r"(x0),"+r"(x1),"+r"(x2),"+r"(x3)::"memory");
    expect(x0==0,"PSCI_CPU_ON_FAILED");
    deadline_wait(&online,1,"SECONDARY_ONLINE_TIMEOUT");
}
void main(void) {
    puts_uart("E06 SMP16K BEGIN\n");expect(cpu_id()==0,"PRIMARY_CPU_WRONG");
    setup_tables();mmu_on();start_cpu1();
    puts_uart("SMP_BOTH_CPUS_MMU_ENABLED\n");
    volatile uint64_t *a=(void*)PA_A,*b=(void*)PA_B,*c=(void*)PA_C;
    *a=MAG_A;*b=MAG_B;*c=MAG_C;dsb_sy();
    request(1,0,MAG_A,0,"INITIAL_ALIAS_READ_WRONG");
    for(unsigned i=0;i<ROUNDS;i++) {
        uint64_t av=MAG_A+i,bv=MAG_B+i,mark=0x4242000000000000ull+i;
        *a=av;*b=bv;dsb_sy();
        uint64_t wanted=(i&1)?PA_A:PA_B;
#ifndef OMIT_REMAP
        replace_page(0,pte(wanted),0);
#else
        /* Negative case: only invalidate, intentionally keep original PA_A. */
        invalidate(0,0);
#endif
        request(2,0,(i&1)?av:bv,mark,"REMOTE_TRANSLATION_WRONG");
        expect(*(volatile uint64_t*)(uintptr_t)wanted==mark,"REMOTE_WRITE_MISDIRECTED");
        expect((i&1)?(*b==bv):(*a==av),"NON_TARGET_FRAME_CHANGED");
    }
    field_uart("CROSS_CPU_VAE1IS_ROUNDS",ROUNDS);
    /* Same VA under a different non-global ASID must have its own content. */
    for(unsigned i=0;i<ROUNDS;i++) {
        *a=MAG_A+i;*c=MAG_C+i;dsb_sy();
        replace_page(0,pte(PA_A),1);
        expect(probe_load(VA)==MAG_A+i,"PRIMARY_ASID_A_MISMATCH");
        request(1,0,MAG_A+i,0,"ASID_A_MISMATCH");
        replace_page(1,pte(PA_C),1);
        request(2,1,MAG_C+i,MAG_B+i,"ASID_B_MISMATCH");
        /* CPU1 has ASID B active while CPU0 still addresses ASID A. */
        expect(probe_load(VA)==MAG_A+i && *c==MAG_B+i,"REMOTE_ASID_CHANGED_PRIMARY_MAPPING");
        probe_store(VA,MAG_A+0x800+i);
        request(1,1,MAG_B+i,0,"PRIMARY_WRITE_LEAKED_TO_OTHER_ASID");
        request(1,0,MAG_A+0x800+i,0,"ASID_A_REVISIT_MISMATCH");
    }
    field_uart("ASID_SWITCH_ROUNDS",ROUNDS);
    for(unsigned i=0;i<8;i++) {
        *a=MAG_A+i;dsb_sy();
        replace_page(0,pte(PA_A),0);
        request(1,0,MAG_A+i,0,"PERMISSION_WARMUP_FAILED");
#ifndef REMOVE_RO
        replace_page(0,pte(PA_A)|RO,0);
#else
        replace_page(0,pte(PA_A),0); /* wrong permission is deliberately detectable */
#endif
        request(3,0,0,0,"MISSING_REMOTE_PERMISSION_FAULT");
        expect(*a==MAG_A+i,"DENIED_REMOTE_STORE_CHANGED_RAM");
        replace_page(0,pte(PA_A),0);
        request(2,0,MAG_A+i,MAG_B+i,"REMOTE_PERMISSION_RESTORE_FAILED");
        expect(*a==MAG_B+i,"RESTORED_STORE_DID_NOT_REACH_RAM");
    }
    expect(mail.faults==8,"WRONG_REMOTE_FAULT_COUNT");
    field_uart("REMOTE_PERMISSION_FAULTS",mail.faults);
    field_uart("REMOTE_FAULT_ESR",mail.esr);field_uart("REMOTE_FAULT_FAR",mail.far);
    /* Both CPUs update the same shared normal-memory counter. */
    count=0;dsb_sy();issue(4,0,50000,0);
    uint64_t primary_retries=increment(50000);
    collect("REMOTE_ATOMIC_FAILED");
    expect(acquire(&count)==100000,"ATOMIC_UPDATES_LOST");
    field_uart("ATOMIC_FINAL_COUNT",acquire(&count));
    field_uart("CPU0_EXCLUSIVE_RETRIES",primary_retries);
    field_uart("CPU1_EXCLUSIVE_RETRIES",mail.value);
    request(5,0,0,0,"SECONDARY_STOP_FAILED");
    puts_uart("E06 SMP16K ASID TLBI PERMISSION ATOMIC PASS\n");
    platform_exit(0);
}
