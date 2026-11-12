/* Self-authored AArch64 image and EL0 execution; no Apple bytes or identity. */
#include "runtime.h"

#define IMAGE_PA 0x40200000ull
#define IMAGE_VA 0x80000000ull
#define ALIAS_VA 0x90000000ull
#define HEADER_PA 0x40400000ull
#define CODE_OFFSET 0x200000ull
#define CPUS 2
#define NORMAL ((1ull << 10) | (3ull << 8))
static uint64_t root[512] __attribute__((aligned(4096)));
static uint64_t image_l2[512] __attribute__((aligned(4096)));
static uint64_t header_l3[512] __attribute__((aligned(4096)));
static uint64_t bad_root[512] __attribute__((aligned(4096)));
static uint64_t bad_l2[512] __attribute__((aligned(4096)));
static uint64_t bad_l3[512] __attribute__((aligned(4096)));
static volatile uint64_t online[CPUS],done[CPUS],values[CPUS][7],traps[CPUS];
static volatile uint64_t ready_phase;
static volatile uint64_t *const command=(void *)0x40300000;
extern const unsigned char site0[],site1[];
extern uint64_t enter_user(uint64_t pc);
extern uint64_t enter_privileged(uint64_t pc);
extern const unsigned char user_return[];
extern const unsigned char secondary_entry[];
extern void fp_prepare(void);
extern void fp_read_state(uint64_t *state);

static unsigned cpu_id(void) { return READ_SYSREG(mpidr_el1)&255; }

void exception_sync(struct exception_frame *frame)
{
    uint64_t ec=READ_SYSREG(esr_el1)>>26;
    if(ec==7 && *command==9) {
        traps[cpu_id()]++;
        frame->x[0]=0xffff;
    } else {
        if(ec!=0x15) {
            if(cpu_id())for(;;)__asm__ volatile("wfi");
            field_uart("UNEXPECTED_ESR",READ_SYSREG(esr_el1));
            field_uart("UNEXPECTED_FAR",READ_SYSREG(far_el1));
            field_uart("UNEXPECTED_ELR",frame->elr);
        }
        expect(ec==0x15,"UNEXPECTED_USER_EXCEPTION");
    }
    frame->elr = (uintptr_t)user_return;
    frame->spsr = 0x3c5;
}

static void copy_bytes(unsigned char *dest,const unsigned char *src,unsigned size)
{
    for(unsigned i=0;i<size;i++)dest[i]=src[i];
}

static void set_mmu(uint64_t *table)
{
    WRITE_SYSREG(cpacr_el1,3ull<<20);
    WRITE_SYSREG(mair_el1,0x00ff);
    WRITE_SYSREG(tcr_el1,32ull|(1ull<<8)|(1ull<<10)|(3ull<<12)|(1ull<<23));
    WRITE_SYSREG(ttbr0_el1,(uintptr_t)table);
    WRITE_SYSREG(ttbr1_el1,0);
    dsb_sy();
    __asm__ volatile("tlbi vmalle1\n\tdsb sy\n\tisb" ::: "memory");
    uint64_t control=READ_SYSREG(sctlr_el1);
    control&=~((1ull<<19)|(1ull<<24)|(1ull<<25));
    WRITE_SYSREG(sctlr_el1,control|1|(1ull<<2)|(1ull<<12));
    isb();
}

static void execute_phase(unsigned id,uint64_t phase)
{
    uint64_t fp[4];
    set_mmu((phase==5 || phase==17) && id==1 ? bad_root : root);
    fp_prepare();
    if(phase==9) { WRITE_SYSREG(cpacr_el1,1ull<<20); isb(); }
    uint64_t (*execute)(uint64_t)=phase==13 ? enter_privileged : enter_user;
    /* The same physical image is present at two user VAs. Exercise both
     * alias orders, including hot TBs created before arming. */
    if(phase==14) {
        values[id][0]=execute(IMAGE_VA+CODE_OFFSET+0x1000);
        values[id][1]=execute(IMAGE_VA+CODE_OFFSET+0x1100);
        values[id][3]=execute(ALIAS_VA+CODE_OFFSET+0x1000);
        values[id][4]=execute(ALIAS_VA+CODE_OFFSET+0x1100);
        values[id][5]=execute(IMAGE_VA+CODE_OFFSET+0x1000);
        values[id][6]=execute(IMAGE_VA+CODE_OFFSET+0x1100);
    } else {
        values[id][3]=execute(ALIAS_VA+CODE_OFFSET+0x1000);
        values[id][4]=execute(ALIAS_VA+CODE_OFFSET+0x1100);
        values[id][0]=execute(IMAGE_VA+CODE_OFFSET+0x1000);
        values[id][1]=execute(IMAGE_VA+CODE_OFFSET+0x1100);
        values[id][5]=execute(ALIAS_VA+CODE_OFFSET+0x1000);
        values[id][6]=execute(ALIAS_VA+CODE_OFFSET+0x1100);
    }
    values[id][2]=execute(IMAGE_VA+CODE_OFFSET+0x1200);
    fp_read_state(fp);
    expect(fp[0]==0x0800001f,"FPSR_CHANGED");
    expect(fp[1]==0x0123456789abcdefull,"OTHER_FP_REGISTER_CHANGED");
    expect(fp[2]==(phase==9 ? UINT64_MAX : 0),"V8_UPPER_CHANGED");
    expect(fp[3]==(phase==9 ? UINT64_MAX : 0),"V9_UPPER_CHANGED");
    expect(traps[id]==(phase==9 ? 7 : 0),"FP_TRAP_COUNT");
    traps[id]=0;
    unsigned char *image=(void *)(uintptr_t)IMAGE_PA;
    for(unsigned i=0;i<64;i++) {
        expect(image[0x1000+i]==site0[i],"SITE0_CODE_CHANGED");
        expect(image[0x1100+i]==site1[i],"SITE1_CODE_CHANGED");
    }
    dsb_sy();done[id]=phase;dsb_sy();
}

void secondary_main(uint64_t id)
{
    set_mmu(root);online[id]=1;dsb_sy();
    uint64_t last=0;
    for(;;) {
        uint64_t phase=*command;
        if(phase==255)for(;;)__asm__ volatile("wfi");
        if(!phase || phase==last || ready_phase!=phase) { __asm__ volatile("yield");continue; }
        dsb_sy();execute_phase(id,phase);last=phase;
    }
}

void main(void)
{
    unsigned char *image=(void *)(uintptr_t)IMAGE_PA;
    unsigned char *header=(void *)(uintptr_t)HEADER_PA;
    static const unsigned char uuid[16]={1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};
    expect(READ_SYSREG(CurrentEL)==4,"NOT_EL1");
    *command=0;
    for(unsigned i=0;i<0x4000;i++)image[i]=0;
    for(unsigned i=0;i<4096;i++)header[i]=0;
    ((uint32_t *)header)[0]=0xfeedfacf;((uint32_t *)header)[1]=0x0100000c;
    ((uint32_t *)header)[8]=0x1b;((uint32_t *)header)[9]=24;
    copy_bytes(header+40,uuid,sizeof(uuid));
    copy_bytes(header+4096,header,64);header[4096+40]^=0xff;
    copy_bytes(image+0x1000,site0,64);copy_bytes(image+0x1100,site1,64);copy_bytes(image+0x1200,site0,64);
    root[0]=1|NORMAL|(1ull<<2)|(1ull<<53)|(1ull<<54);
    root[1]=0x40000000ull|NORMAL|1;root[2]=(uintptr_t)image_l2|3;
    image_l2[0]=(uintptr_t)header_l3|3;image_l2[128]=image_l2[0];
    image_l2[1]=IMAGE_PA|NORMAL|(1ull<<6)|1;image_l2[129]=image_l2[1];
    header_l3[0]=HEADER_PA|NORMAL|(1ull<<6)|3;
    for(unsigned i=0;i<512;i++) { bad_root[i]=root[i];bad_l2[i]=image_l2[i]; }
    bad_root[2]=(uintptr_t)bad_l2|3;bad_l2[0]=(uintptr_t)bad_l3|3;bad_l2[128]=bad_l2[0];
    bad_l3[0]=(HEADER_PA+4096)|NORMAL|(1ull<<6)|3;
    dsb_sy();set_mmu(root);
    register uint64_t x0 __asm__("x0")=0xc4000003;
    register uint64_t x1 __asm__("x1")=1;
    register uint64_t x2 __asm__("x2")=(uintptr_t)secondary_entry;
    register uint64_t x3 __asm__("x3")=1;
    __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1),"r"(x2),"r"(x3) : "memory");
    expect(x0==0,"CPU_ON_FAILED");
    while(!online[1])__asm__ volatile("yield");
    puts_uart("TIMEOUT_DIAG_READY\n");
    uint64_t last=0;
    for(;;) {
        uint64_t phase=*command;
        if(phase==255) { puts_uart("TIMEOUT_DIAG_GUEST_DONE\n");platform_exit(0); }
        if(!phase || phase==last) { __asm__ volatile("yield");continue; }
        header[40]=phase==3 ? 0xff : 1;
        image_l2[1]=IMAGE_PA|NORMAL|(1ull<<6)|1;
        /* A64 forbids EL1 execution from an EL0-writable mapping. */
        if(phase==13)image_l2[1]|=1ull<<7;
        image_l2[129]=image_l2[1];
        image_l2[0]=(uintptr_t)header_l3|3;
        header_l3[0]=HEADER_PA|NORMAL|(1ull<<6)|3;
        if(phase==6)header_l3[0]=0x09000000ull|NORMAL|(1ull<<6)|(1ull<<2)|3;
        if(phase==7 || phase==13)image_l2[0]=0x09000000ull|3;
        if(phase==8)header_l3[0]=0;
        if(phase==12)header_l3[0]&=~(1ull<<10); /* Debug reads must not update AF. */
        image_l2[128]=image_l2[0];
        /* Code-only alias and a complete alias with a different UUID. */
        if(phase==15)image_l2[128]=0;
        if(phase==16)image_l2[128]=(uintptr_t)bad_l3|3;
        bad_l3[0]=(phase==17 ? HEADER_PA : HEADER_PA+4096)|NORMAL|(1ull<<6)|3;
        dsb_sy();__asm__ volatile("tlbi vmalle1is\n\tdsb sy\n\tisb" ::: "memory");
        ready_phase=phase;dsb_sy();execute_phase(0,phase);
        while(done[1]!=phase)__asm__ volatile("yield");
        if(phase==12)expect(!(header_l3[0]&(1ull<<10)),"GUARD_UPDATED_AF");
        if(phase==10)field_uart("MMIO_POSITIVE",mmio32(0x09000000));
        for(unsigned id=0;id<CPUS;id++) {
            field_uart("PHASE",phase);field_uart("CPU",id);
            field_uart("SITE0",values[id][0]);field_uart("SITE1",values[id][1]);field_uart("ALIAS0",values[id][3]);field_uart("ALIAS1",values[id][4]);
            field_uart("REPEAT0",values[id][5]);field_uart("REPEAT1",values[id][6]);field_uart("OTHER",values[id][2]);
        }
        puts_uart("PHASE_DONE\n");last=phase;
    }
}
