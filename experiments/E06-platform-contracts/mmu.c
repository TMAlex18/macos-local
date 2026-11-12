#include "runtime.h"
#define PAGE 0x4000ull
#define BLOCK 0x02000000ull
#define ALIAS 0xa0000000ull
#define FRAME_A 0x70200000ull
#define FRAME_B 0x70204000ull
#define AF (1ull<<10)
#define SH_INNER (3ull<<8)
#define XN ((1ull<<53)|(1ull<<54))
#define NORMAL (AF|SH_INNER)
#define RO (1ull<<7) /* AP=10: privileged read-only, no EL0 access */
static uint64_t root_l2[2048] __attribute__((aligned(16384)));
static uint64_t alias_l3[2048] __attribute__((aligned(16384)));
static volatile unsigned faults;
static volatile uint64_t wanted_far,wanted_pc,wanted_fsc,wanted_write;
static volatile unsigned expected_fault;

static void invalidate_tlb(void) {
    __asm__ volatile("dsb ishst\n\ttlbi vmalle1is\n\tdsb ish\n\tisb" ::: "memory");
}
void exception_sync(struct exception_frame *f) {
    uint64_t esr=READ_SYSREG(esr_el1),far=READ_SYSREG(far_el1);
    field_uart("FAULT_ESR",esr);field_uart("FAULT_FAR",far);field_uart("FAULT_ELR",f->elr);
    expect(expected_fault,"UNEXPECTED_SYNC_EXCEPTION");
    expect((esr>>26)==0x25,"FAULT_NOT_SAME_EL_DATA_ABORT");
    expect((esr&0x3f)==wanted_fsc,"WRONG_FAULT_STATUS");
    expect(((esr>>6)&1)==wanted_write,"WRONG_FAULT_READ_WRITE");
    expect(far==wanted_far,"WRONG_FAULT_ADDRESS");
    expect(f->elr==wanted_pc,"WRONG_FAULT_INSTRUCTION");
    faults++;expected_fault=0;
    f->elr+=4; /* skip exactly the known faulting ldr/str, then execute ret */
}
static void expect_access_fault(uint64_t address,unsigned write,uint64_t fsc) {
    unsigned before=faults;
    wanted_far=address;wanted_write=write;wanted_fsc=fsc;
    wanted_pc=(uintptr_t)(write ? probe_store_instruction : probe_load_instruction);
    expected_fault=1;
    __asm__ volatile("" ::: "memory");
    if(write)probe_store(address,0xbadc0ffee0ddf00dull);
    else(void)probe_load(address);
    __asm__ volatile("" ::: "memory");
    if(faults!=before+1 || expected_fault) {
        if(fsc==0xf)fail("MISSING_EXPECTED_PERMISSION_FAULT");
        fail("MISSING_EXPECTED_DATA_ABORT");
    }
}
static uint64_t normal_page(uint64_t physical) { return physical|NORMAL|XN|3; }
static void enable_mmu(void) {
    uint64_t feature=READ_SYSREG(id_aa64mmfr0_el1);
    uint64_t granule16=(feature>>20)&15;
    field_uart("ID_AA64MMFR0",feature);
    expect(granule16==1||granule16==2,"16K_GRANULE_NOT_SUPPORTED");
    /* 32-bit input VA, 16KiB TG0 -> start at L2. One L2 block is 32MiB.
       Cover only known firmware/config RAM, device windows, and guest RAM. */
    root_l2[0]=NORMAL|1; /* firmware/config, executable */
    for(uint64_t a=0x10000000;a<0x70000000;a+=BLOCK)
        root_l2[a>>25]=a|AF|4|XN|1; /* MAIR[1]: Device-nGnRnE, XN */
    for(uint64_t a=0x70000000;a<0x90000000;a+=BLOCK)
        root_l2[a>>25]=a|NORMAL|XN|1;
    root_l2[ALIAS>>25]=(uintptr_t)alias_l3|3;
    alias_l3[0]=normal_page(FRAME_A);
#ifndef ALLOW_RO_WRITES
    alias_l3[1]=normal_page(FRAME_B)|RO;
#else
    alias_l3[1]=normal_page(FRAME_B); /* negative control: remove permission restriction */
#endif
    alias_l3[2]=0; /* translation-fault control */
    alias_l3[3]=normal_page(FRAME_A)&~AF; /* access-flag control */
    dsb_sy();
    WRITE_SYSREG(mair_el1,0x00ff); /* normal WB / device nGnRnE */
    WRITE_SYSREG(tcr_el1,32ull|(1ull<<8)|(1ull<<10)|(3ull<<12)|(2ull<<14)|(32ull<<16)|(1ull<<23));
    WRITE_SYSREG(ttbr0_el1,(uintptr_t)root_l2);
    WRITE_SYSREG(ttbr1_el1,0);
    isb();
    invalidate_tlb();
    __asm__ volatile("ic iallu\n\tdsb sy\n\tisb" ::: "memory");
    uint64_t sctlr=READ_SYSREG(sctlr_el1);
    sctlr&=~((1ull<<19)|(1ull<<25)|(1ull<<24)); /* WXN=0, little endian */
    sctlr|=1ull|(1ull<<2)|(1ull<<12); /* MMU + D/I caches */
    WRITE_SYSREG(sctlr_el1,sctlr);isb();
    expect((READ_SYSREG(sctlr_el1)&1)!=0,"MMU_NOT_ENABLED");
}
void main(void) {
    expect(READ_SYSREG(CurrentEL)==4,"NOT_EL1");
    puts_uart("E06 MMU16K BEGIN\n");
    volatile uint64_t *a=(void *)(uintptr_t)FRAME_A,*b=(void *)(uintptr_t)FRAME_B;
    *a=0x1111222233334444ull;*b=0x5555666677778888ull;
    enable_mmu();
    field_uart("TCR_EL1",READ_SYSREG(tcr_el1));
    expect(probe_load(ALIAS)==*a,"VA_TO_PA_READ_FAILED");
    probe_store(ALIAS,0x123456789abcdef0ull);
    expect(*a==0x123456789abcdef0ull,"VA_TO_PA_WRITE_FAILED");
    expect(probe_load(ALIAS+PAGE)==*b,"READ_ONLY_PAGE_NOT_READABLE");
    puts_uart("MMU16K_ALIAS_RW_PASS\n");
    expect_access_fault(ALIAS+PAGE,1,0xf); /* Permission fault level 3 */
    expect(*b==0x5555666677778888ull,"DENIED_WRITE_MODIFIED_RAM");
    puts_uart("MMU16K_RO_STORE_ABORT_PASS\n");
    expect_access_fault(ALIAS+2*PAGE,0,7); /* Translation fault level 3 */
    puts_uart("MMU16K_UNMAPPED_ABORT_PASS\n");
    expect_access_fault(ALIAS+3*PAGE,0,0xb); /* Access flag fault level 3 */
    alias_l3[3]|=AF;invalidate_tlb();
    expect(probe_load(ALIAS+3*PAGE)==*a,"ACCESS_FLAG_REPAIR_FAILED");
    puts_uart("MMU16K_ACCESS_FLAG_ABORT_REPAIR_PASS\n");
    /* Break-before-make remap an already accessed VA from A to B. */
    alias_l3[0]=0;invalidate_tlb();
    alias_l3[0]=normal_page(FRAME_B);
    __asm__ volatile("dsb ishst\n\tisb" ::: "memory");
    expect(probe_load(ALIAS)==*b,"TLBI_REMAP_STALE_TRANSLATION");
    probe_store(ALIAS,0x0fedcba987654321ull);
    expect(*b==0x0fedcba987654321ull && *a==0x123456789abcdef0ull,"REMAP_WROTE_WRONG_FRAME");
    expect(faults==3,"FAULT_COUNT_MISMATCH");
    field_uart("DATA_ABORT_COUNT",faults);
    puts_uart("MMU16K_BBM_TLBI_REMAP_PASS\n");
    puts_uart("E06 MMU16K PERMISSION TRANSLATION TLBI PASS\n");
#ifdef TIMER_AFTER_MMU
    extern void timer_contract(void);
    timer_contract();
    puts_uart("E06 MMU TIMER INTEGRATED PASS\n");
#endif
    platform_exit(0);
}
