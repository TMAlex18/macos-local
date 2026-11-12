#include "runtime.h"
#define GICD 0x10000000ull
#define GICR 0x10010000ull
#define SGI (GICR+0x10000)
#define TIMER_IRQ 27u
#define TIMER_MASK (1u<<TIMER_IRQ)
static volatile uint64_t irq_count, irq_id, irq_status;
static uint64_t ticks(void) { return READ_SYSREG(cntvct_el0); }
static void wait_ticks(uint64_t delta) { uint64_t end=ticks()+delta; while(ticks()<end) {} }
static void arm_timer(uint64_t deadline) {
    WRITE_SYSREG(cntv_ctl_el0,0);
    WRITE_SYSREG(cntv_cval_el0,deadline);
    WRITE_SYSREG(cntv_ctl_el0,1);
    isb();
}
void exception_irq(struct exception_frame *f) {
    (void)f;
    uint64_t id=READ_SYSREG(icc_iar1_el1);
    if ((id&0xffffff)!=TIMER_IRQ) { field_uart("INTID",id);fail("UNEXPECTED_INTID"); }
    irq_id=id;
    irq_status=READ_SYSREG(cntv_ctl_el0);
    WRITE_SYSREG(cntv_ctl_el0,0); /* clear level source before deactivation */
    isb();
    irq_count++;
    WRITE_SYSREG(icc_eoir1_el1,id);
    isb();
}
static void configure_gic(void) {
    uint32_t ctl=mmio32(GICD);
    field_uart("GICD_RESET_CTLR",ctl);
    expect((ctl&(1u<<6))!=0,"GIC_SECURITY_PROFILE_DIFFERENT");
    write32(GICR+0x14,mmio32(GICR+0x14)&~(1u<<1));
    unsigned i=0;
    while(mmio32(GICR+0x14)&4u) { if(++i>1000000)fail("GICR_WAKE_TIMEOUT"); }
    write32(SGI+0x180,TIMER_MASK);
    write32(SGI+0x280,TIMER_MASK);
    write32(SGI+0x380,TIMER_MASK);
    write32(SGI+0x80,mmio32(SGI+0x80)|TIMER_MASK); /* Group1 */
    *(volatile uint8_t *)(uintptr_t)(SGI+0x400+TIMER_IRQ)=0x80;
    write32(SGI+0xc04,mmio32(SGI+0xc04)&~(3u<<22)); /* level-trigger PPI27 */
    write32(GICD,ctl|2u); /* enable non-secure Group1, retain ARE/DS */
    WRITE_SYSREG(icc_sre_el1,1);
    isb();
    WRITE_SYSREG(icc_pmr_el1,0xff);
    WRITE_SYSREG(icc_bpr1_el1,0);
    WRITE_SYSREG(icc_ctlr_el1,0); /* EOImode=0 */
    WRITE_SYSREG(icc_igrpen1_el1,1);
#ifndef BLOCK_PPI
    write32(SGI+0x100,TIMER_MASK);
#endif
    dsb_sy();isb();
}
void timer_contract(void) {
    expect(READ_SYSREG(CurrentEL)==4,"NOT_EL1");
    puts_uart("E06 TIMER BEGIN\n");
    uint64_t freq=READ_SYSREG(cntfrq_el0);
    expect(freq>=1000,"BAD_COUNTER_FREQUENCY");
    field_uart("CNTFRQ",freq);
    configure_gic();
    /* While DAIF.I is set, timer expires but no exception executes. */
    arm_timer(ticks()+freq/1000);
    wait_ticks(freq/500);
    expect(irq_count==0,"IRQ_IGNORED_CPU_MASK");
    expect((READ_SYSREG(cntv_ctl_el0)&4)!=0,"TIMER_NOT_PENDING");
    puts_uart("MASKED_TIMER_PENDING_PASS\n");
    unmask_irq();
    uint64_t until=ticks()+freq/10;
    while(irq_count<1 && ticks()<until) {}
#ifdef BLOCK_PPI
    expect(irq_count==0,"NEGATIVE_PPI_STILL_DELIVERED");
    puts_uart("PPI_DISABLED_NO_IRQ_EXPECTED\n");
    platform_exit(1);
#else
    expect(irq_count==1,"TIMER_IRQ_NOT_DELIVERED");
    expect(irq_id==TIMER_IRQ && (irq_status&4)!=0,"IRQ_SOURCE_NOT_TIMER");
    puts_uart("GIC_PPI27_IRQ_ERET_PASS\n");
    /* WFI can wake for a pending IRQ while DAIF.I remains set; unmasking
       after wake avoids the check/interrupt/WFI race. */
    mask_irq();
    arm_timer(ticks()+freq/500);
    __asm__ volatile("wfi" ::: "memory");
    expect(irq_count==1,"IRQ_IGNORED_SECOND_CPU_MASK");
    unmask_irq();
    until=ticks()+freq/10;
    while(irq_count<2 && ticks()<until) {}
    expect(irq_count==2,"TIMER_REARM_OR_WFI_FAILED");
    mask_irq();
    expect((READ_SYSREG(cntv_ctl_el0)&1)==0,"TIMER_SOURCE_NOT_CLEARED");
    unmask_irq();
    wait_ticks(freq/500);
    expect(irq_count==2,"SPURIOUS_IRQ_AFTER_DISABLE");
    field_uart("IRQ_COUNT",irq_count);
    field_uart("LAST_INTID",irq_id);
    puts_uart("E06 TIMER GIC WFI REARM PASS\n");
#endif
}
#ifndef TIMER_LIBRARY
void main(void) { timer_contract(); platform_exit(0); }
#endif
