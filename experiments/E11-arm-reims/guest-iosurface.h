/* Guest driver of the normal IOSurface MMIO descriptor/ring, not CMD_TEST.
 * All page numbers are guest 16KiB PFNs; MMIO addresses fit QEMU's low32 ABI.
 */
#define IOSFC ((volatile u32 *)0x30211000ull)
#define SURFACE_RING ((volatile u8 *)0x70080000ull)
#define SURFACE_TABLE ((volatile u8 *)0x70084000ull)
#define SURFACE_DESCRIPTORS ((volatile u8 *)0x70088000ull)
#define SURFACE_PT ((volatile u8 *)0x7008c000ull)
#define SURFACE_DESC (SURFACE_DESCRIPTORS + 0x400) /* mapping ID1 */
#define SURFACE_A ((volatile u8 *)0x70100000ull)
#define SURFACE_B ((volatile u8 *)0x70110000ull)
#define RETIRED_SENTINEL 0x13579bdfu
#ifdef SURFACE_RGBA16FLOAT
#define PIXEL_FORMAT 115u /* RGBA16Float */
#define SURFACE_FOURCC 0x52476841u /* RGhA */
#define PIXEL_BYTES 8u
typedef u64 pixel_t;
/* Little-endian IEEE binary16 RGBA: 0 = 0x0000, 1 = 0x3c00. */
#define RED 0x3c00000000003c00ull
#define BLUE 0x3c003c0000000000ull
#define GREEN 0x3c0000003c000000ull
#else
#define PIXEL_FORMAT 80u /* BGRA8Unorm */
#define SURFACE_FOURCC 0x42475241u /* BGRA */
#define PIXEL_BYTES 4u
typedef u32 pixel_t;
#define RED 0xffff0000u
#define BLUE 0xff0000ffu
#define GREEN 0xff00ff00u
#endif
#define ROW_BYTES (64 * PIXEL_BYTES)
#define IMAGE_BYTES (64 * ROW_BYTES)
#define ALLOCATION_BYTES (IMAGE_BYTES + PAGE)
static u32 surface_written;

static u32 surface_pte(volatile u8 *address) { return (pfn(address) << 2) | 1; }

static u32 surface_command_id(u32 command, u32 mapping_id, u32 *mapped) {
    volatile u8 *slot = SURFACE_RING + 16 * (surface_written % 1024);
    w32(slot, command); w32(slot + 4, mapping_id); w32(slot + 8, 0x7eadbeef);
    __asm__ volatile("dsb sy" ::: "memory");
    IOSFC[0x18 / 4] = ++surface_written;
    u64 freq; __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(freq));
    u64 start = counter();
    while (IOSFC[0x20 / 4] != surface_written) {
        if (counter() - start > freq * 10) { puts("IOSURFACE RING TIMEOUT\n"); finish(1); }
    }
    __asm__ volatile("dmb sy" ::: "memory");
    u32 status = *(volatile u32 *)(slot + 8);
    *mapped = (*(volatile u32 *)(SURFACE_DESCRIPTORS + mapping_id * 0x400) >> 1) & 1;
    puts("REIMS IOSURFACE COMMAND="); hex(command); puts(" ID="); hex(mapping_id); puts(" STATUS="); hex(status);
    puts(" READ="); hex(surface_written); puts(" MAPPED="); hex(*mapped); puts("\n");
    return status;
}

static void surface_command(u32 command, u32 expected_status, u32 expected_mapped) {
    u32 mapped;
    u32 status = surface_command_id(command, 1, &mapped);
    if (status != expected_status || mapped != expected_mapped) {
        puts("IOSURFACE RING STATUS OR DESCRIPTOR WRITEBACK FAILED\n"); finish(1);
    }
}

#ifdef USE_DISPLAY_UPDATE
#define SURFACE_PT_SECOND ((volatile u8 *)0x70094000ull) /* beyond display shared page */

static void surface_map_second(void) {
    volatile u8 *d = SURFACE_DESCRIPTORS + 2 * 0x400;
    for (u32 i = 0; i < PAGE; ++i) SURFACE_PT_SECOND[i] = 0;
    for (u32 i = 0; i < ALLOCATION_BYTES / 4; ++i) ((volatile u32 *)SURFACE_B)[i] = SENTINEL;
    for (u32 i = 0; i < ALLOCATION_BYTES / PAGE; ++i)
        w32(SURFACE_PT_SECOND + i * 4, surface_pte(SURFACE_B + i * PAGE));
    for (u32 i = 0; i < 0x400; ++i) d[i] = 0;
    w32(d, surface_pte(SURFACE_PT_SECOND)); w32(d + 4, SURFACE_FOURCC);
    w64(d + 24, ALLOCATION_BYTES); w64(d + 32, 64); w64(d + 40, 64); w64(d + 48, ROW_BYTES);
    d[56] = PIXEL_BYTES; d[60] = 1; d[61] = 1;
    u32 mapped;
    if (surface_command_id(1, 2, &mapped) != 0 || mapped != 1 ||
        *(volatile u32 *)SURFACE_PT != surface_pte(SURFACE_A) ||
        *(volatile u32 *)(SURFACE_PT + 4) != surface_pte(SURFACE_A + PAGE)) {
        puts("IOSURFACE SECOND MAP OR INDEPENDENT TABLES FAILED\n"); finish(1);
    }
    puts("REIMS IOSURFACE INDEPENDENT IDS AND PAGE TABLES PASS\n");
}

static void surface_retire_first(void) {
    u64 freq; __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(freq));
    u64 start = counter();
    for (;;) {
        u32 mapped, status = surface_command_id(2, 1, &mapped);
        if (status == 0 && mapped == 0) break;
        if (status != 0xe00002d5 || mapped != 1 || counter() - start > freq * 10) {
            puts("IOSURFACE RETIRE STATUS OR DEADLINE FAILED\n"); finish(1);
        }
        u64 retry = counter();
        while (counter() - retry < freq / 1000) { /* ordinary bounded Busy retry */ }
    }
    for (u32 i = 0; i < ALLOCATION_BYTES / 4; ++i) ((volatile u32 *)SURFACE_A)[i] = RETIRED_SENTINEL;
    puts("REIMS IOSURFACE OLD ID1 UNMAPPED AND POISONED\n");
}
#endif

static void surface_map(volatile u8 *backing) {
    for (u32 i = 0; i < ALLOCATION_BYTES / 4; ++i) ((volatile u32 *)backing)[i] = SENTINEL;
    for (u32 i = 0; i < ALLOCATION_BYTES / PAGE; ++i)
        w32(SURFACE_PT + i * 4, surface_pte(backing + i * PAGE));
    /* Non-planar image/plane. v2 plane_count0 uses base dimensions. */
    for (u32 i = 0; i < 0x400; ++i) SURFACE_DESC[i] = 0;
    w32(SURFACE_DESC, surface_pte(SURFACE_PT));
    w32(SURFACE_DESC + 4, SURFACE_FOURCC);
    w64(SURFACE_DESC + 24, ALLOCATION_BYTES);
    w64(SURFACE_DESC + 32, 64); w64(SURFACE_DESC + 40, 64); w64(SURFACE_DESC + 48, ROW_BYTES);
    SURFACE_DESC[56] = PIXEL_BYTES; SURFACE_DESC[60] = 1; SURFACE_DESC[61] = 1;
    surface_command(1, 0, 1);
}

static void surface_start(void) {
    puts("REIMS SURFACE FOURCC="); hex(SURFACE_FOURCC);
    puts(" MTL_FORMAT="); hex(PIXEL_FORMAT); puts(" BPE="); hex(PIXEL_BYTES);
    puts(" ROW_BYTES="); hex(ROW_BYTES); puts(" IMAGE_BYTES="); hex(IMAGE_BYTES);
    puts(" ALLOCATION_BYTES="); hex(ALLOCATION_BYTES); puts("\n");
    for (u32 i = 0; i < 4 * PAGE; ++i) SURFACE_RING[i] = 0;
    w32(SURFACE_TABLE, surface_pte(SURFACE_DESCRIPTORS));
    IOSFC[0x30 / 4] = 1; /* v2, 0x400-byte descriptors */
    IOSFC[0x10 / 4] = (u32)(u64)SURFACE_TABLE;
    IOSFC[0 / 4] = (u32)(u64)SURFACE_RING;
    IOSFC[8 / 4] = 1024; /* exactly one 16KiB guest ring page */
    if (IOSFC[0x28 / 4] != 1) { puts("IOSURFACE MMIO FAILED\n"); finish(1); }
    surface_map(SURFACE_A);
}

static void surface_delete_texture(u32 stamp, u32 *offset) {
    volatile u8 *p = FIFO + 0x400 + *offset + 12;
    w32(p, 1); w32(p + 4, 2);
    submit(37, 20, stamp, offset);
    surface_command(2, 0, 0);
}
