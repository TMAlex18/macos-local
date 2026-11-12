/* Self-authored bare-metal ARM guest. No Apple firmware or guest OS.
 * ABI: Reims guest records, and the upstream self-authored compute_add shader.
 * The guest initializes, submits, waits, and checks every output itself.
 */
#include "guest-common.h"

#ifdef USE_HEAP
#define HEAP_BYTES 0x1400000u
#define COUNT (HEAP_BYTES / 4)
#define INPUT_BYTES HEAP_BYTES
#define GROUP_SIZE 256u
#define OBJECT_COUNT 6u
#define INPUT_PAGE (16 + HEAP_BYTES / PAGE)
#define TASK_BYTES ((INPUT_PAGE + HEAP_BYTES / PAGE) * PAGE)
#define INPUT_MEMORY ((volatile u32 *)0x74000000ull)
#define HEAP_MEMORY ((volatile u8 *)0x71000000ull)
static volatile u32 *output_word(u32 i) {
    /* Adjacent guest 16KiB pages deliberately have reversed physical order. */
    return (volatile u32 *)(HEAP_MEMORY + ((i / (PAGE / 4)) ^ 1u) * PAGE) + i % (PAGE / 4);
}
#else
#define COUNT 256u
#define INPUT_BYTES PAGE
#define GROUP_SIZE 64u
#define OBJECT_COUNT 5u
#define INPUT_PAGE 8u
#define TASK_BYTES (16 * PAGE)
#define INPUT_MEMORY ((volatile u32 *)(DATA + INPUT_VA))
#endif
#define INPUT_VA (8 * PAGE)
#define OUTPUT_VA (9 * PAGE)
#define SHADER_VA (10 * PAGE)
extern const u8 shader_start[], shader_end[];
static u32 input(u32 i) { return (i * 1664525u) ^ 1013904223u; }

void guest_main(void) {
    /* This RAM belongs solely to this guest. Host never supplies an answer. */
    for (u32 i = 0; i < 20 * PAGE; ++i) RAM[i] = 0;
    for (u32 i = 0; i < 16; ++i)
        w32(PAGE_TABLE + i * 4, pfn(DATA + i * PAGE) | 0x80000000u);
    w32(TASK_ROOT, pfn(PAGE_TABLE));
    w32(TASK_ROOT + 4, 1); /* one-level table, 4096 u32 entries */
    configure_gpu_irq();
#ifdef USE_HEAP
    for (u32 page = 0; page < HEAP_BYTES / PAGE; ++page) {
        w32(PAGE_TABLE + (16 + page) * 4, pfn(HEAP_MEMORY + (page ^ 1u) * PAGE) | 0x80000000u);
        w32(PAGE_TABLE + (INPUT_PAGE + page) * 4,
            pfn((volatile u8 *)INPUT_MEMORY + page * PAGE) | 0x80000000u);
    }
    for (u32 i = 0; i < (HEAP_BYTES + PAGE) / 4; ++i) ((volatile u32 *)HEAP_MEMORY)[i] = SENTINEL;
    for (u32 i = COUNT; i < COUNT + PAGE / 4; ++i) INPUT_MEMORY[i] = SENTINEL;
    puts("REIMS HEAP BYTES=01400000 GUEST_PAGES=00000500 PHYSICAL_ORDER=PAIR_SWAPPED\n");
#endif
    for (u32 i = 0; i < INPUT_BYTES / 4; ++i) {
        INPUT_MEMORY[i] = i < COUNT ? input(i) : SENTINEL;
#ifndef USE_HEAP
        ((volatile u32 *)(DATA + OUTPUT_VA))[i] = SENTINEL;
#endif
    }
    u32 shader_len = shader_end - shader_start;
    if (shader_len > PAGE) { puts("SHADER SIZE FAILED\n"); finish(1); }
    for (u32 i = 0; i < shader_len; ++i) DATA[SHADER_VA + i] = shader_start[i];

    /* refs 1/2 shared input/output buffers, 3 function, 4 compute pipeline. */
    object(1, 1, 16, PAGE);
    w64(DATA + PAGE, INPUT_BYTES); w32(DATA + PAGE + 8, INPUT_PAGE);
#ifdef USE_HEAP
    object(2, 14, 20, PAGE + 128); /* child buffer over the entire type13 heap */
    w32(DATA + PAGE + 128, 5); w64(DATA + PAGE + 132, 0); w64(DATA + PAGE + 140, HEAP_BYTES);
    object(5, 13, 16, PAGE + 160);
    w32(DATA + PAGE + 160, 16); w64(DATA + PAGE + 164, HEAP_BYTES);
#else
    object(2, 1, 16, PAGE + 16);
    w64(DATA + PAGE + 16, PAGE); w32(DATA + PAGE + 24, 9);
#endif
    object(3, 6, 20, PAGE + 32);
    w64(DATA + PAGE + 32, SHADER_VA); w32(DATA + PAGE + 40, shader_len);
    w32(DATA + PAGE + 44, 15); /* function-with-IR operation */
    object(4, 7, 23, PAGE + 64);
    w32(DATA + PAGE + 64, 11); /* compute-pipeline operation */
    w32(DATA + PAGE + 76, 7);  /* serialized descriptor byte length */
    DATA[PAGE + 80] = 1; DATA[PAGE + 81] = 0; DATA[PAGE + 82] = 4;
    w32(DATA + PAGE + 83, 3);  /* descriptor key0 = function ref3 */

    volatile u8 *cb = DATA + CB_VA;
    w32(cb, 116); cb[4] = 1; /* segment length; compute encoder */
    w32(cb + 8, 0xd0); w32(cb + 12, 12); w32(cb + 16, 4);
    w32(cb + 20, 0xcb); w32(cb + 24, 40);
    w32(cb + 28, 0); w32(cb + 32, 2); /* buffer slots0..1 */
    w32(cb + 36, 2); w64(cb + 40, 0); /* out */
    w32(cb + 48, 1); w64(cb + 52, 0); /* in */
    w32(cb + 60, 0xca); w32(cb + 64, 56);
    w64(cb + 68, COUNT); w64(cb + 76, 1); w64(cb + 84, 1);
    w64(cb + 92, GROUP_SIZE); w64(cb + 100, 1); w64(cb + 108, 1);

    REGS[0x34 / 4] = 245;
    REGS[0x1c / 4] = pfn(RAM);
    REGS[0x30 / 4] = pfn(FIFO);
    REGS[4 / 4] = PAGE;
    REGS[0x10 / 4] = 0x400;
    __asm__ volatile("dsb sy" ::: "memory");
    REGS[0] = 1;
    if (REGS[0] != 1) { puts("FIFO ENABLE FAILED\n"); finish(1); }

    u32 offset = 0;
    volatile u8 *p = FIFO + 0x400 + 12;
    w32(p, 2); w64(p + 4, TASK_BYTES); w32(p + 12, pfn(TASK_ROOT));
    submit(56, 28, 1, &offset);
    puts("REIMS COMPUTE TASK DEFINED\n");
    p = FIFO + 0x400 + offset + 12;
    w32(p, 1); w32(p + 4, 0); w32(p + 8, OBJECT_COUNT * 12);
    submit(51, 24, 2, &offset);
    puts("REIMS COMPUTE OBJECT LIST INSTALLED\n");
#ifndef NO_EXEC
    p = FIFO + 0x400 + offset + 12;
#ifdef USE_HEAP
    w32(p, 1); w32(p + 4, 3); w32(p + 8, 1);
    w32(p + 12, 1); w32(p + 16, 5);
    w32(p + 20, 5); w32(p + 24, 5); /* prepare the heap before its child */
    w32(p + 28, 2); w32(p + 32, 0);
    w64(p + 36, CB_VA); w64(p + 44, 116);
    submit(67, 64, 3, &offset);
#else
    w32(p, 1); w32(p + 4, 2); w32(p + 8, 1);
    w32(p + 12, 1); w32(p + 16, 5); /* invalidateHost + synchronize */
    w32(p + 20, 2); w32(p + 24, 5);
    w64(p + 28, CB_VA); w64(p + 36, 116);
    submit(67, 56, 3, &offset);
#endif
    puts("REIMS COMPUTE EXEC COMPLETED\n");
#else
    submit(30, 12, 3, &offset); /* live FIFO completion without any GPU exec */
#endif
#ifndef USE_HEAP
    volatile u32 *out = (volatile u32 *)(DATA + OUTPUT_VA);
#endif
    for (u32 i = 0; i < INPUT_BYTES / 4; ++i) {
#ifndef NO_EXEC
        u32 expected = i < COUNT ? input(i) + 1u : SENTINEL;
#else
        u32 expected = SENTINEL;
#endif
        u32 got =
#ifdef USE_HEAP
            *output_word(i);
#else
            out[i];
#endif
        if (got != expected) {
            puts("REIMS COMPUTE DATA FAILED index="); hex(i);
            puts(" got="); hex(got); puts(" expected="); hex(expected); puts("\n");
            finish(1);
        }
        if (INPUT_MEMORY[i] != (i < COUNT ? input(i) : SENTINEL)) {
            puts("REIMS COMPUTE INPUT GUARD FAILED\n"); finish(1);
        }
    }
#ifdef USE_HEAP
    for (u32 i = COUNT; i < COUNT + PAGE / 4; ++i)
        if (((volatile u32 *)HEAP_MEMORY)[i] != SENTINEL || INPUT_MEMORY[i] != SENTINEL) {
            puts("REIMS HEAP OUTER GUARD FAILED\n"); finish(1);
        }
    puts("REIMS HEAP 5242880 VALUES INPUT AND BOTH GUARDS PASS\n");
#else
    puts("REIMS OUTPUT FIRST="); hex(out[0]); puts(" LAST="); hex(out[COUNT - 1]); puts("\n");
#endif
    if (REGS[0xc / 4] != offset || REGS[0x2c / 4]) {
        puts("REIMS COMPUTE FIFO STATE FAILED\n"); finish(1);
    }
    p = FIFO + 0x400 + offset + 12; w32(p, 1);
    submit(32, 16, 4, &offset);
    REGS[0] = 0;
#ifndef NO_EXEC
#ifdef USE_HEAP
    finish_verified_workload("REIMS ARM GPU HEAP 20MIB COMPUTE AND GUARDS PASS\n");
#else
    finish_verified_workload("REIMS ARM GPU COMPUTE 256 VALUES AND GUARDS PASS\n");
#endif
#else
    finish_verified_workload("REIMS ARM NO EXEC OUTPUT UNCHANGED PASS\n");
#endif
}
