/* Reuse the independently established PPI27 setup and interrupt handler. */
#define TIMER_LIBRARY 1
#include "../E06-platform-contracts/timer.c"
void main(void) {
    uint64_t hz=READ_SYSREG(cntfrq_el0);
    configure_gic();
    uint64_t intervals[]={1,2,3,5,11,24,25,41,42,83,100,1000};
    for(unsigned i=0;i<96;i++) {
        mask_irq();
        uint64_t old=irq_count;
        uint64_t due=ticks()+intervals[i%12];
        arm_timer(due);
        __asm__ volatile("wfi" ::: "memory");
        expect(ticks()>=due,"STRESS_IRQ_BEFORE_DEADLINE");
        unmask_irq();
        uint64_t limit=ticks()+hz/10;
        while(irq_count==old && ticks()<limit) {}
        expect(irq_count==old+1,"STRESS_LOST_OR_DUPLICATE_IRQ");
        expect((irq_status&4)!=0,"STRESS_FALSE_ISTATUS");
    }
    mask_irq();
    uint64_t old=irq_count;
    arm_timer(UINT64_MAX);
    wait_ticks(hz/1000);
    expect((READ_SYSREG(cntv_ctl_el0)&4)==0 && irq_count==old,
           "FAR_DEADLINE_WRAPPED_TO_PRESENT");
    WRITE_SYSREG(cntv_ctl_el0,0);isb();
    field_uart("IRQ_COUNT",irq_count);
    puts_uart("EXACT_CLOCK_TIMER_STRESS_PASS\n");
    platform_exit(0);
}
