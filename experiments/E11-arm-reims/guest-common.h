/* Shared only by the self-authored E11 bare-metal GPU fixtures. */
#ifndef E11_GUEST_COMMON_H
#define E11_GUEST_COMMON_H
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;

#define PAGE 0x4000u
#define SENTINEL 0xdeadbeefu
#define RAM ((volatile u8 *)0x70000000ull)
#define FIFO (RAM + PAGE)
#define TASK_ROOT (RAM + 2 * PAGE)
#define PAGE_TABLE (RAM + 3 * PAGE)
#define DATA (RAM + 4 * PAGE)
#define CB_VA (4 * PAGE)
#define REGS ((volatile u32 *)0x30201000ull)


static void puts(const char *s) {
    while (*s) *(volatile u8 *)0x20010000ull = (u8)*s++;
}

static void hex(u32 value) {
    const char digits[] = "0123456789abcdef";
    for (int shift = 28; shift >= 0; shift -= 4)
        *(volatile u8 *)0x20010000ull = digits[(value >> shift) & 15];
}

__attribute__((noreturn)) static void finish(u64 code) {
    const u64 args[] = {0x20026, code};
    register u64 x0 __asm__("x0") = 0x18;
    register const u64 *x1 __asm__("x1") = args;
    __asm__ volatile("hlt #0xf000" : "+r"(x0) : "r"(x1) : "memory");
    for (;;) __asm__ volatile("wfi");
}

/* Serialized records are packed; byte stores avoid imposing host C alignment. */
#ifndef E11_RUNTIME_ONLY
static void w32(volatile u8 *p, u32 v) {
    for (u32 i = 0; i < 4; ++i) p[i] = (u8)(v >> (i * 8));
}
static void w64(volatile u8 *p, u64 v) {
    w32(p, (u32)v); w32(p + 4, (u32)(v >> 32));
}
static u32 pfn(volatile u8 *p) { return (u64)p >> 14; }

static void object(u32 ref, u32 type, u32 length, u64 va) {
    w32(DATA + ref * 12, type | (length << 8));
    w64(DATA + ref * 12 + 4, va);
}
#endif

static u64 counter(void) {
    u64 v; __asm__ volatile("mrs %0, cntvct_el0" : "=r"(v)); return v;
}
#ifndef E11_RUNTIME_ONLY
#ifdef USE_IRQ
#include "guest-irq.h"
#else
static void configure_gpu_irq(void) {}
#endif

static void wait_stamp(u32 stamp) {
    u64 freq; __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(freq));
    u64 start = counter();
    while (*(volatile u32 *)FIFO != stamp) {
        if (REGS[0x2c / 4] || counter() - start > freq * 30) {
            puts("REIMS COMMAND WAIT FAILED stamp="); hex(stamp);
            puts(" fault="); hex(REGS[0x2c / 4]); puts("\n");
            finish(1);
        }
    }
    __asm__ volatile("dmb sy" ::: "memory");
}

static void submit(u32 command, u32 length, u32 stamp, u32 *offset) {
    volatile u8 *p = FIFO + 0x400 + *offset;
    w32(p, command); w32(p + 4, length); w32(p + 8, stamp);
    *offset += length;
#ifdef USE_IRQ
    prepare_irq_stamp(stamp);
#endif
    __asm__ volatile("dsb sy" ::: "memory");
    REGS[8 / 4] = *offset;
#ifdef USE_IRQ
    /* Negative resource teardown may poll only after the missing IRQ has
     * already failed the workload. It can never turn that failure into PASS. */
    if (irq_work_missing && stamp != 3) wait_stamp(stamp);
    else wait_irq_stamp(stamp);
#else
    wait_stamp(stamp);
#endif
}

static void finish_verified_workload(const char *success) {
#ifdef USE_IRQ
    if (irq_work_missing) {
        puts("REIMS GPU RESULT ORACLE PASS\nREIMS IRQ REQUIRED MISSING EXPECTED\n");
        finish(1);
    }
    if (!irq_work_matched) { puts("REIMS WORK IRQ NOT VERIFIED\n"); finish(1); }
    puts("REIMS GPU IRQ WORK COMPLETION PASS\n");
#endif
    puts(success);
    finish(0);
}
#endif /* E11_RUNTIME_ONLY */

#endif
