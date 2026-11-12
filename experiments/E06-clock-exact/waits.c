/* Controlled guest wait/deadline tests. WFxT permits other wake reasons;
 * this fixture disables interrupts/timers and consumes pending events. */
#include "runtime.h"
static uint64_t ticks(void) { return READ_SYSREG(cntvct_el0); }
static void clear_event(void) { __asm__ volatile("sevl; wfe" ::: "memory"); }
void main(void) {
    uint64_t hz=READ_SYSREG(cntfrq_el0);
    expect(hz>=1000000,"LOW_FREQ_FOR_WAIT_TEST");
    mask_irq();
    WRITE_SYSREG(cntv_ctl_el0,0);
    WRITE_SYSREG(cntp_ctl_el0,0);
    WRITE_SYSREG(cntkctl_el1,0);
    isb();
    uint64_t deadline=ticks()+hz/200;
    __asm__ volatile("wfit %0" :: "r"(deadline) : "memory");
    expect(ticks()>=deadline,"WFIT_EARLY_NO_WAKE_SOURCE");
    puts_uart("WFIT_EXACT_DEADLINE_PASS\n");
    clear_event();
    deadline=ticks()+hz/200;
    __asm__ volatile("wfet %0" :: "r"(deadline) : "memory");
    expect(ticks()>=deadline,"WFET_EARLY_NO_WAKE_SOURCE");
    puts_uart("WFET_EXACT_DEADLINE_PASS\n");
    /* Past deadlines must return without halting indefinitely. */
    deadline=ticks()-1;
    __asm__ volatile("wfit %0" :: "r"(deadline) : "memory");
    clear_event();
    __asm__ volatile("wfet %0" :: "r"(deadline) : "memory");
    puts_uart("WFXT_PAST_DEADLINE_PASS\n");
    /* A stream event must beat the much later WFET timeout. */
    uint64_t config=(15u<<4)|(1u<<2);
#ifdef DISABLE_EVENT
    config=0;
#endif
    WRITE_SYSREG(cntkctl_el1,config);
    isb(); clear_event();
    uint64_t before=ticks();
    deadline=before+hz/20;
    __asm__ volatile("wfet %0" :: "r"(deadline) : "memory");
    uint64_t after=ticks();
    if(after>=deadline) {
        puts_uart("EVENT_DISABLED_TIMEOUT_EXPECTED\n");
        platform_exit(1);
    }
    expect(after>before,"EVENT_DID_NOT_ADVANCE_COUNTER");
    /* Rising event at counter bit15; upper bound allows scheduling delays. */
    uint64_t boundary=((before>>16)<<16)+(1u<<15);
    if(boundary<=before)boundary+=1u<<16;
    expect(after>=boundary,"EVENT_BEFORE_TRIGGER_EDGE");
    puts_uart("WFET_EVENT_BEATS_TIMEOUT_PASS\n");
    /* Exercise falling edges through WFE, not only WFET. */
    WRITE_SYSREG(cntkctl_el1,config|(1u<<3));
    isb(); clear_event(); before=ticks();
    __asm__ volatile("wfe" ::: "memory");
    after=ticks();
    boundary=((before>>16)+1)<<16;
    expect(after>=boundary,"WFE_BEFORE_FALLING_EDGE");
    WRITE_SYSREG(cntkctl_el1,0);isb();
    puts_uart("WFE_EVENT_FALLING_EDGE_PASS\n");
    puts_uart("EXACT_CLOCK_WAIT_SUITE_PASS\n");
    platform_exit(0);
}
