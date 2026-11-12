/* Optional normal EL1/GICv3 notification contract for both GPU probes.
 * No WFI: the guest's architectural counter bounds a wait on ISR state.
 */
#define GICD 0x10000000ull
#define GICR 0x10010000ull
#define GPU_SPI (1u << 17) /* GIC input0x11 -> SPI INTID49 */
#define SYS_WRITE(reg, value) do { u64 v_ = (value); \
    __asm__ volatile("msr " #reg ", %0" :: "r"(v_) : "memory"); } while (0)

static volatile u32 irq_expected, irq_completed, irq_count, irq_bad;
static volatile u32 irq_id, irq_bits, irq_memory_stamp, irq_fault;
static volatile u32 irq_work_matched, irq_work_missing;
#ifdef USE_NATIVE_USB_IRQ
#include "guest-input.h"
#endif
#ifdef USE_DISPLAY
#define DISPLAY_SHARED ((volatile u8 *)0x70090000ull)
static volatile u32 irq_display_tracking, irq_display_present_seen;
static volatile u32 irq_display_ports, irq_display_raised, irq_display_intid;
#endif

static u32 gic_read(u64 address) { return *(volatile u32 *)address; }
static void gic_write(u64 address, u32 value) { *(volatile u32 *)address = value; }

void guest_bad_exception(void) {
    puts("REIMS IRQ UNEXPECTED EXCEPTION\n"); finish(1);
}

void guest_irq_handler(void) {
    u64 iar;
    __asm__ volatile("mrs %0, icc_iar1_el1" : "=r"(iar));
    u32 id = iar & 0xffffff;
    if (id == 49) {
        /* Both bitmaps are independent READ-CLEAR registers. The present
         * fixture also atomically takes only its own shared page's events. */
#ifdef USE_DISPLAY
        u32 ports = REGS[0x14 / 4];
        if (ports & 1) {
            u32 raised = __atomic_exchange_n((volatile u32 *)(DISPLAY_SHARED + 0x100), 0, __ATOMIC_ACQ_REL);
            if (irq_display_tracking && (raised & 2)) {
                irq_display_ports = ports; irq_display_raised = raised;
                irq_display_intid = id; irq_display_present_seen = 1;
            }
        }
#endif
        u32 bits = REGS[0x18 / 4];
        __asm__ volatile("dmb sy" ::: "memory");
        u32 stamp = *(volatile u32 *)FIFO;
        u32 fault = REGS[0x2c / 4];
        ++irq_count;
        if ((bits & 1) && stamp == irq_expected && fault == 0) {
            irq_id = id; irq_bits = bits; irq_memory_stamp = stamp; irq_fault = fault;
            irq_completed = stamp;
            if (stamp == 3 && !irq_work_missing) irq_work_matched = 1;
        }
        if (fault) irq_bad = 1;
#ifdef USE_NATIVE_USB_IRQ
    } else if (id == 67) {
        /* The USB module clears its level source before this shared EOIR.
         * Graphics/display remains edge49 with its own independent ack. */
        if (!input_usb_irq_handle(id)) irq_bad = 1;
#endif
    } else if (id != 1023) {
        irq_bad = 1;
    }
    if (id < 1020) {
        SYS_WRITE(icc_eoir1_el1, iar); /* EOImode0 deactivates here. */
        __asm__ volatile("isb" ::: "memory");
    }
}

static void configure_gpu_irq(void) {
    u64 el; __asm__ volatile("mrs %0, CurrentEL" : "=r"(el));
    u32 ctl = gic_read(GICD);
    if (el != 4 || !(ctl & (1u << 6))) {
        puts("REIMS IRQ GIC EL1 DS PROFILE FAILED\n"); finish(1);
    }
    gic_write(GICR + 0x14, gic_read(GICR + 0x14) & ~2u);
    u32 n = 1000000;
    while (gic_read(GICR + 0x14) & 4u) {
        if (!--n) { puts("REIMS IRQ REDISTRIBUTOR WAKE FAILED\n"); finish(1); }
    }
    gic_write(GICD + 0x184, GPU_SPI);
    gic_write(GICD + 0x284, GPU_SPI);
    gic_write(GICD + 0x84, gic_read(GICD + 0x84) | GPU_SPI);
    gic_write(GICD + 0xc0c, (gic_read(GICD + 0xc0c) & ~(3u << 2)) | (2u << 2));
    *(volatile u8 *)(GICD + 0x431) = 0x80;
    *(volatile u64 *)(GICD + 0x6188) = 0;
    gic_write(GICD, ctl | 2u); /* Retain DS/ARE; enable Group1. */
    SYS_WRITE(icc_sre_el1, 1); __asm__ volatile("isb" ::: "memory");
    SYS_WRITE(icc_pmr_el1, 0xff); SYS_WRITE(icc_bpr1_el1, 0);
    SYS_WRITE(icc_ctlr_el1, 0); SYS_WRITE(icc_igrpen1_el1, 1);
    gic_write(GICD + 0x104, GPU_SPI);
    __asm__ volatile("dsb sy; isb; msr daifclr, #2" ::: "memory");
    puts("REIMS GPU IRQ GIC EDGE INTID49 CONFIGURED\n");
}

static void prepare_irq_stamp(u32 stamp) {
    __asm__ volatile("msr daifset, #2" ::: "memory");
    if (stamp == 3 && irq_completed != 2) {
        puts("REIMS INITIALIZATION IRQ NOT CONSUMED\n"); finish(1);
    }
    irq_expected = stamp; irq_completed = 0;
#ifdef BLOCK_WORK_SPI
    if (stamp == 3) {
        /* Initialization IRQs were consumed normally. Only the actual work's
         * notification is blocked; GPU/FIFO/backing data remain unchanged. */
        gic_write(GICD + 0x184, GPU_SPI);
        puts("REIMS GPU WORK SPI49 DISABLED AFTER INIT IRQ\n");
    }
#endif
    __asm__ volatile("dsb sy; isb; msr daifclr, #2" ::: "memory");
}

static void wait_irq_stamp(u32 stamp) {
    u64 freq; __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(freq));
    u64 start = counter(), seconds = 30;
#ifdef BLOCK_WORK_SPI
    if (stamp == 3) seconds = 2;
#endif
    /* This success condition reads ISR state, never the GPU's stamp memory. */
    while (irq_completed != stamp) {
        if (irq_bad) { puts("REIMS IRQ SOURCE OR FAULT FAILED\n"); finish(1); }
        if (counter() - start > freq * seconds) {
#ifdef BLOCK_WORK_SPI
            if (stamp == 3 && *(volatile u32 *)FIFO == 3 && REGS[0x2c / 4] == 0) {
                irq_work_missing = 1;
                puts("REIMS IRQ DISABLED GPU MEMORY STAMP3 READY WITHOUT WORK ISR\n");
                return; /* Run the original data oracle, but require exit1. */
            }
#endif
            puts("REIMS IRQ WAIT FAILED STAMP="); hex(stamp); puts("\n"); finish(1);
        }
    }
    __asm__ volatile("dmb sy; msr daifset, #2" ::: "memory");
    puts("REIMS IRQ CAPTURE EXPECTED="); hex(stamp);
    puts(" INTID="); hex(irq_id); puts(" BITS="); hex(irq_bits);
    puts(" MEMORY="); hex(irq_memory_stamp); puts(" FAULT="); hex(irq_fault);
    puts(" COUNT="); hex(irq_count); puts("\n");
    __asm__ volatile("msr daifclr, #2" ::: "memory");
}
