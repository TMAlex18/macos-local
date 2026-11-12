/* Normal offscreen Reims render submission by our bare-metal ARM guest.
 * Shader inputs and the exact pixel-center geometry oracle live in the guest.
 * No Apple firmware, host-generated answer image, or CPU rasterizer.
 */
#include "guest-common.h"
#ifdef USE_NATIVE_INPUT
#include "guest-input.h"
#endif

#define TARGET_VA (8 * PAGE)   /* 64x64 RGBA8, followed by one guard page */
#define VERTICES_VA (10 * PAGE)
#define COLOR_VA (11 * PAGE)
#define VERT_SHADER_VA (12 * PAGE)
#define FRAG_SHADER_VA (13 * PAGE)
#ifdef USE_IOSURFACE
#include "guest-iosurface.h"
#ifdef USE_DISPLAY
#include "guest-display.h"
#endif
#else
#define PIXEL_FORMAT 70u
typedef u32 pixel_t;
#define IMAGE_BYTES PAGE
#define ALLOCATION_BYTES (2 * PAGE)
#define RED 0xff0000ffu
#define BLUE 0xffff0000u
#endif

extern const u8 vertex_start[], vertex_end[], fragment_start[], fragment_end[];

static void function(u32 ref, u32 record, u32 va, const u8 *first, const u8 *end) {
    u32 length = end - first;
    if (length > PAGE) { puts("DRAW SHADER SIZE FAILED\n"); finish(1); }
    for (u32 i = 0; i < length; ++i) DATA[va + i] = first[i];
    object(ref, 6, 20, record);
    w64(DATA + record, va); w32(DATA + record + 8, length);
    w32(DATA + record + 12, 15);
}

static void dump_pixels(volatile u8 *pixels) {
    const char digits[] = "0123456789abcdef";
#ifdef SURFACE_RGBA16FLOAT
    puts("PIXELS_RGBA16F_BEGIN\n");
#elif defined(USE_IOSURFACE)
    puts("PIXELS_BGRA_BEGIN\n");
#else
    puts("PIXELS_RGBA_BEGIN\n");
#endif
    for (u32 i = 0; i < IMAGE_BYTES; ++i) {
        u8 v = pixels[i];
        *(volatile u8 *)0x20010000ull = digits[v >> 4];
        *(volatile u8 *)0x20010000ull = digits[v & 15];
        if ((i + 1) % 256 == 0) puts("\n");
    }
#ifdef SURFACE_RGBA16FLOAT
    puts("PIXELS_RGBA16F_END\n");
#elif defined(USE_IOSURFACE)
    puts("PIXELS_BGRA_END\n");
#else
    puts("PIXELS_RGBA_END\n");
#endif
}

static void render_and_check(u32 cb_length, u32 stamp, volatile pixel_t *pixels, pixel_t foreground, u32 *offset) {
#ifdef NO_DRAW
    (void)foreground;
#endif
    volatile u8 *p = FIFO + 0x400 + *offset + 12;
    w32(p, 1); w32(p + 4, 3); w32(p + 8, 1);
#ifdef USE_IOSURFACE
    w32(p + 12, 2); w32(p + 16, 0); /* mapper texture aliases guest surface */
#else
    w32(p + 12, 1); w32(p + 16, 5);
#endif
    w32(p + 20, 6); w32(p + 24, 5);
    w32(p + 28, 7); w32(p + 32, 5);
    w64(p + 36, CB_VA); w64(p + 44, cb_length);
    submit(67, 64, stamp, offset); puts("REIMS DRAW EXEC COMPLETED\n");

    /* Screen vertices (8,56),(56,56),(32,8). Pixel centers never meet an
     * edge: their doubled coordinates are odd, every triangle edge is even.
     * Therefore integer half-plane tests give an exact independent oracle.
     */
    u32 foreground_count = 0;
    for (int y = 0; y < 64; ++y) for (int x = 0; x < 64; ++x) {
        pixel_t expected = BLUE;
#ifndef NO_DRAW
        if (y < 56 && 2 * x + y >= 71 && 2 * x - y <= 55) {
            expected = foreground; ++foreground_count;
        }
#endif
        pixel_t got = pixels[y * 64 + x];
        if (got != expected) {
            puts("REIMS DRAW PIXEL FAILED x="); hex(x); puts(" y="); hex(y);
            puts(" got=");
#ifdef SURFACE_RGBA16FLOAT
            hex(got >> 32);
#endif
            hex(got); puts(" expected=");
#ifdef SURFACE_RGBA16FLOAT
            hex(expected >> 32);
#endif
            hex(expected); puts("\n");
            finish(1);
        }
    }
    for (u32 i = IMAGE_BYTES / 4; i < ALLOCATION_BYTES / 4; ++i)
        if (((volatile u32 *)pixels)[i] != SENTINEL) { puts("REIMS DRAW GUARD FAILED\n"); finish(1); }
    if (REGS[0xc / 4] != *offset || REGS[0x2c / 4]) {
        puts("REIMS DRAW FIFO STATE FAILED\n"); finish(1);
    }
    puts("REIMS DRAW FOREGROUND PIXELS="); hex(foreground_count); puts("\n");
    dump_pixels((volatile u8 *)pixels);
}

void guest_main(void) {
    for (u32 i = 0; i < 20 * PAGE; ++i) RAM[i] = 0;
    for (u32 i = 0; i < 16; ++i)
        w32(PAGE_TABLE + i * 4, pfn(DATA + i * PAGE) | 0x80000000u);
    w32(TASK_ROOT, pfn(PAGE_TABLE)); w32(TASK_ROOT + 4, 1);
    configure_gpu_irq();
#ifdef USE_NATIVE_INPUT
    input_usb_init(1);
#ifdef USE_NATIVE_USB_IRQ
    input_usb_irq_start();
#endif
#endif
#ifdef USE_IOSURFACE
    surface_start();
#endif
    for (u32 i = 0; i < 2 * PAGE / 4; ++i)
        ((volatile u32 *)(DATA + TARGET_VA))[i] = SENTINEL;

    /* The non-IOSurface path uses a shared-buffer RGBA8 texture view. */
    object(1, 1, 16, PAGE);
    w64(DATA + PAGE, 2 * PAGE); w32(DATA + PAGE + 8, 8);
#ifdef USE_IOSURFACE
    object(2, 11, 56, PAGE + 16); /* mapping ID + IOSurface texture op12 */
    volatile u8 *texture = DATA + PAGE + 16;
    w64(texture, 1); w32(texture + 8, 12);
    volatile u8 *desc = texture + 20;
#else
    object(2, 8, 64, PAGE + 16);
    volatile u8 *texture = DATA + PAGE + 16;
    w32(texture, 9); w32(texture + 12, 1); w64(texture + 24, 256);
    volatile u8 *desc = texture + 32;
#endif
    w32(desc, 2 | (5 << 8) | (PIXEL_FORMAT << 16));
    w32(desc + 4, 64); w32(desc + 8, 64); w32(desc + 12, 1);
    w32(desc + 16, 0x00010001); w32(desc + 20, 1);
    function(3, PAGE + 80, VERT_SHADER_VA, vertex_start, vertex_end);
    function(4, PAGE + 112, FRAG_SHADER_VA, fragment_start, fragment_end);

    /* Suite render_lib.py's basic descriptor, function refs3/4, selected format. */
    const u8 pipeline_blob[] = {
        0x03,0x08,0x04,0x14,0,0,0,0x01,0x04,3,0,0,0,0x02,0x04,4,0,0,0,
        0,0x01,0,0,0,0x08,0,0,0,0x02,0,0x04,0,0,0,0,0x01,0x04,PIXEL_FORMAT,0,0,0
    };
    object(5, 7, 16 + sizeof(pipeline_blob), PAGE + 144);
    w32(DATA + PAGE + 144, 14);
    w32(DATA + PAGE + 156, sizeof(pipeline_blob));
    for (u32 i = 0; i < sizeof(pipeline_blob); ++i) DATA[PAGE + 160 + i] = pipeline_blob[i];
    object(6, 1, 16, PAGE + 224);
    w64(DATA + PAGE + 224, PAGE); w32(DATA + PAGE + 232, 10);
    object(7, 1, 16, PAGE + 240);
    w64(DATA + PAGE + 240, PAGE); w32(DATA + PAGE + 248, 11);

    /* Exact IEEE float4 bits: (-.75,-.75), (.75,-.75), (0,.75), z0/w1. */
    const u32 vertices[] = {
        0xbf400000,0xbf400000,0,0x3f800000,
        0x3f400000,0xbf400000,0,0x3f800000,
        0,0x3f400000,0,0x3f800000
    };
    for (u32 i = 0; i < 12; ++i) w32(DATA + VERTICES_VA + i * 4, vertices[i]);
    w32(DATA + COLOR_VA, 0x3f800000); w32(DATA + COLOR_VA + 12, 0x3f800000);

    volatile u8 *cb = DATA + CB_VA;
    /* DescribeRenderPass payload: depth0x28 + stencil0x24 + 8 colors0x3c. */
    w32(cb + 8, 0x1a); w32(cb + 12, 8 + 0x248);
    volatile u8 *pass = cb + 16;
    volatile u8 *color = pass + 0x4c;
    w32(color, 2); w32(color + 20, 0x00010002); /* Clear, Store */
    w64(color + 44, 0x3ff0000000000000ull); /* blue1 */
    w64(color + 52, 0x3ff0000000000000ull); /* alpha1 */
    w64(pass + 0x238, 64); w64(pass + 0x240, 64);
    w32(cb + 600, 0x74); w32(cb + 604, 12); w32(cb + 608, 5);
    w32(cb + 612, 0x7d); w32(cb + 616, 40);
    w32(cb + 620, 0); w32(cb + 624, 2);
    w32(cb + 628, 6); w64(cb + 632, 0);
    w32(cb + 640, 7); w64(cb + 644, 0);
    w32(cb + 652, 0x82); w32(cb + 656, 56); /* viewport 0,0,64,64,0,1 */
    w64(cb + 676, 0x4050000000000000ull); w64(cb + 684, 0x4050000000000000ull);
    w64(cb + 700, 0x3ff0000000000000ull);
    w32(cb + 708, 0x75); w32(cb + 712, 40); /* scissor whole target */
    w64(cb + 732, 64); w64(cb + 740, 64);
    u32 cb_length = 748;
#ifndef NO_DRAW
    w32(cb + 748, 1); w32(cb + 752, 16);
    w32(cb + 756, 3); w32(cb + 760, 0x00030000); /* triangle, start0,count3 */
    cb_length += 16;
#endif
    w32(cb, cb_length); /* render encoder type0, no continuation */
    REGS[0x34 / 4] = 245; REGS[0x1c / 4] = pfn(RAM); REGS[0x30 / 4] = pfn(FIFO);
    REGS[4 / 4] = PAGE; REGS[0x10 / 4] = 0x400;
    __asm__ volatile("dsb sy" ::: "memory");
    REGS[0] = 1;
    if (REGS[0] != 1) { puts("DRAW FIFO ENABLE FAILED\n"); finish(1); }
    u32 offset = 0;
    volatile u8 *p = FIFO + 0x400 + 12;
    w32(p, 2); w64(p + 4, 16 * PAGE); w32(p + 12, pfn(TASK_ROOT));
    submit(56, 28, 1, &offset); puts("REIMS DRAW TASK DEFINED\n");
    p = FIFO + 0x400 + offset + 12;
    w32(p, 1); w32(p + 4, 0); w32(p + 8, 8 * 12);
    submit(51, 24, 2, &offset); puts("REIMS DRAW OBJECT LIST INSTALLED\n");
#if defined(USE_IOSURFACE) && !defined(USE_DISPLAY)
    const u32 rounds = 2;
#else
    const u32 rounds = 1;
#endif
    for (u32 round = 0; round < rounds; ++round) {
#ifdef USE_IOSURFACE
    volatile pixel_t *pixels = (volatile pixel_t *)(round == 0 ? SURFACE_A : SURFACE_B);
#else
    volatile u32 *pixels = (volatile u32 *)(DATA + TARGET_VA);
#endif
    render_and_check(cb_length, 3 + 2 * round, pixels, RED, &offset);
#ifdef USE_DISPLAY_UPDATE
    display_connect(&offset);
    display_swap(1, 7, 0, &offset);
    puts("REIMS DISPLAY RED PHASE\n");
#ifdef USE_NATIVE_INPUT
#ifdef USE_NATIVE_USB_IRQ
    input_usb_wait_irq_key(0);
#else
    puts("INPUT NATIVE KEY DOWN READY\n"); input_usb_wait_key(0);
#endif
#else
    display_hold();
#endif
    p = FIFO + 0x400 + offset + 12; w32(p, 1); w32(p + 4, 2);
    submit(37, 20, 8, &offset); /* release task texture; display still holds ID1 */
    surface_command(2, 0xe00002d5, 1);
    surface_map_second();
    w64(DATA + PAGE + 16, 2); /* recreate ref2 over independent mapping ID2 */
    w32(DATA + COLOR_VA, 0); w32(DATA + COLOR_VA + 4, 0x3f800000);
    render_and_check(cb_length, 9, (volatile pixel_t *)SURFACE_B, GREEN, &offset);
    display_swap(2, 10, 1, &offset);
    surface_retire_first();
#ifdef USE_NATIVE_INPUT
#ifdef USE_NATIVE_USB_IRQ
    input_usb_wait_irq_key(1);
#else
    puts("INPUT NATIVE KEY UP READY\n"); input_usb_wait_key(1);
#endif
#endif
    puts("REIMS DISPLAY GREEN PHASE\n"); display_hold();
    for (u32 i = 0; i < ALLOCATION_BYTES / 4; ++i)
        if (((volatile u32 *)SURFACE_A)[i] != RETIRED_SENTINEL) {
            puts("DISPLAY RETIRED BACKING CHANGED\n"); finish(1);
        }
    puts("REIMS DISPLAY RETIRED BACKING AFTER GREEN HOLD PASS\n");
    REGS[0] = 0;
#ifdef USE_NATIVE_INPUT
#ifdef USE_NATIVE_USB_IRQ
    input_usb_irq_shutdown();
    finish_verified_workload("REIMS ARM NATIVE WINDOW USB IRQ KEY TO GREEN PASS\n");
#else
    input_usb_shutdown();
    finish_verified_workload("REIMS ARM NATIVE WINDOW USB KEY TO GREEN PASS\n");
#endif
#else
    finish_verified_workload("REIMS ARM IOSURFACE TWO FRAME PRESENT RETIRE PASS\n");
#endif
#elif defined(USE_DISPLAY)
    display_present_and_hold(&offset);
    REGS[0] = 0;
    /* Keep the presented surface alive through the native-window hold. The
     * process shutdown releases the display and its retained front surface. */
    finish_verified_workload("REIMS ARM IOSURFACE WINDOW PRESENT PASS\n");
#endif
#ifdef USE_IOSURFACE
    if (round == 0) surface_command(2, 0xe00002d5, 1); /* held texture is Busy */
    surface_delete_texture(4 + 2 * round, &offset);
    if (round == 0) {
        for (u32 i = 0; i < ALLOCATION_BYTES / 4; ++i) ((volatile u32 *)SURFACE_A)[i] = RETIRED_SENTINEL;
        surface_map(SURFACE_B); /* same mapping ID, different physical backing */
    } else {
        for (u32 i = 0; i < ALLOCATION_BYTES / 4; ++i)
            if (((volatile u32 *)SURFACE_A)[i] != RETIRED_SENTINEL) {
                puts("IOSURFACE RETIRED BACKING CHANGED\n"); finish(1);
            }
        puts("REIMS IOSURFACE RELEASE REMAP NEW BACKING AND RETIRED GUARD PASS\n");
    }
#endif
    }
    p = FIFO + 0x400 + offset + 12; w32(p, 1);
    submit(32, 16, 3 * rounds + 1, &offset); REGS[0] = 0;
#ifdef USE_IOSURFACE
    IOSFC[0] = 0; IOSFC[0x10 / 4] = 0;
    finish_verified_workload("REIMS ARM IOSURFACE GPU DRAW RELEASE REUSE PASS\n");
#else
#ifndef NO_DRAW
    finish_verified_workload("REIMS ARM GPU DRAW 4096 PIXELS AND GUARD PASS\n");
#else
    finish_verified_workload("REIMS ARM NO DRAW CLEAR ONLY 4096 PIXELS AND GUARD PASS\n");
#endif
#endif
}
