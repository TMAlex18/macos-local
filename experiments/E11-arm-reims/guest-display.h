/* Ordinary display shared-state connect/ack and mapped-surface present.
 * The ISR records the notification; native window pixels are checked by host.
 */
static u32 display_exchange_raised(void) {
    return __atomic_exchange_n((volatile u32 *)(DISPLAY_SHARED + 0x100), 0, __ATOMIC_ACQ_REL);
}

static void display_connect(u32 *offset) {
    for (u32 i = 0; i < PAGE; ++i) DISPLAY_SHARED[i] = 0;
    volatile u8 *p = FIFO + 0x400 + *offset + 12;
    w32(p, 0); w64(p + 4, 16 * PAGE); w32(p + 12, pfn(TASK_ROOT));
    submit(56, 28, 4, offset); /* valid kernel task0 for the display */
    p = FIFO + 0x400 + *offset + 12;
    w32(p, 0); w32(p + 4, pfn(DISPLAY_SHARED));
    submit(1, 20, 5, offset);
    u64 freq; __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(freq));
    u64 start = counter();
    while (!(DISPLAY_SHARED[0xfa9] & 1)) {
        if (counter() - start > freq * 10) { puts("DISPLAY ACK PENDING TIMEOUT\n"); finish(1); }
    }
    u32 expected_ack = *(volatile u32 *)(DISPLAY_SHARED + 0x200);
    p = FIFO + 0x400 + *offset + 12;
    w32(p, 0); w32(p + 4, expected_ack);
    submit(2, 20, 6, offset);
    start = counter();
    while (*(volatile u32 *)(DISPLAY_SHARED + 0xfc0) != 2) {
        if (counter() - start > freq * 10) { puts("DISPLAY RUNNING STATE TIMEOUT\n"); finish(1); }
    }
    puts("REIMS DISPLAY CONNECT ACK RUNNING PASS\n");

}

static void display_swap(u32 mapping_id, u32 stamp, u64 expected_before, u32 *offset) {
    u64 freq; __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(freq));
    __asm__ volatile("msr daifset, #2" ::: "memory");
    (void)REGS[0x14 / 4]; /* clear the initial display port bitmap */
    (void)display_exchange_raised();
    irq_display_present_seen = 0; irq_display_ports = 0; irq_display_raised = 0;
    irq_display_tracking = 1;
    *(volatile u32 *)(DISPLAY_SHARED + 0x104) = 2; /* present interrupt */
    u64 before = *(volatile u64 *)(DISPLAY_SHARED + 0xf98);
    __asm__ volatile("dsb sy; msr daifclr, #2" ::: "memory");
    if (before != expected_before) { puts("DISPLAY PREVIOUS PRESENT COUNT FAILED\n"); finish(1); }
    volatile u8 *p = FIFO + 0x400 + *offset + 12;
    w32(p, 0); w32(p + 4, 0); w64(p + 8, mapping_id);
    w64(p + 16, 0); w64(p + 24, 0); w32(p + 32, 0); w32(p + 36, 0);
    submit(8, 52, stamp, offset);
    u64 start = counter();
    /* The present IRQ can precede count/mapping publication, and FIFO/display
     * IRQs can coalesce. Require all three only after returning from the ISR. */
    while (!irq_display_present_seen ||
           *(volatile u64 *)(DISPLAY_SHARED + 0xf98) != before + 1 ||
           *(volatile u64 *)(DISPLAY_SHARED + 0xfb0) != mapping_id) {
        if (counter() - start > freq * 10) { puts("DISPLAY PRESENT STATE OR IRQ TIMEOUT\n"); finish(1); }
    }
    __asm__ volatile("dmb sy" ::: "memory");
    puts("REIMS DISPLAY PRESENT BEFORE="); hex((u32)before);
    puts(" AFTER="); hex((u32)*(volatile u64 *)(DISPLAY_SHARED + 0xf98));
    puts(" MAPPING="); hex(mapping_id); puts(" INTID="); hex(irq_display_intid);
    puts(" PORTS="); hex(irq_display_ports); puts(" RAISED="); hex(irq_display_raised); puts("\n");
}

static void display_hold(void) {
    u64 freq; __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(freq));
    puts("REIMS DISPLAY FRAME HOLD BEGIN\n");
    u64 start = counter();
    while (counter() - start < freq * 5) { /* bounded hold for owned-window capture */ }
    puts("REIMS DISPLAY FRAME HOLD END\n");
}

#ifndef USE_DISPLAY_UPDATE
static void display_present_and_hold(u32 *offset) {
    display_connect(offset);
    display_swap(1, 7, 0, offset);
    display_hold();
}
#endif
