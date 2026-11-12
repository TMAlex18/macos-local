/* Self-authored modern VirtIO PCI fixture, not an Apple USB service. */
#include "runtime.h"
#define PCI 0x40018000ull /* explicit slot3, INTA -> GPEX3 -> GIC SPI67 */
#define GICD 0x10000000ull
#define GICR 0x10010000ull
#define SPI (1u << 3)
#define PAGE 16384u
#define RING_SIZE 256u
#define QDESC(q) (0x72000000ull + (q) * 0x20000ull)
#define QAVAIL(q) (QDESC(q) + PAGE)
#define QUSED(q) (QDESC(q) + 2 * PAGE)
#define REQUEST_HEADER 0x72100000ull
#define REQUEST_ARENA 0x72200000ull
#define REQUEST_ARENA_BYTES 0x24000u
#define RESPONSE_HEADER 0x72300000ull
#define RESPONSE_ARENA 0x72400000ull
#define RESPONSE_ARENA_BYTES 0x84000u
#define BUFFER_OFFSET 256u
#define REQUEST_CAPACITY 0x20000u
#define LARGE_BYTES (512u * 1024u)

static uint64_t common, notify, isr, notify_address[2], bar_base[6], bar_size[6];
static uint32_t notify_multiplier, notify_bytes;
static uint16_t available[2];
static volatile uint32_t seen_used[2], irq_count, irq_id, irq_bitmap, irq_bad, config_irq_count;
#ifdef SMALL_Q0
static volatile uint32_t config_irq_id, config_irq_bitmap;
#endif
static uint8_t r8(uint64_t p) { return *(volatile uint8_t *)(uintptr_t)p; }
static uint16_t r16(uint64_t p) { return *(volatile uint16_t *)(uintptr_t)p; }
static void w8(uint64_t p, uint8_t v) { *(volatile uint8_t *)(uintptr_t)p = v; }
static void w16(uint64_t p, uint16_t v) { *(volatile uint16_t *)(uintptr_t)p = v; }
static void w64(uint64_t p, uint64_t v) { *(volatile uint64_t *)(uintptr_t)p = v; }
static uint64_t ticks(void) { return READ_SYSREG(cntvct_el0); }
static uint64_t frequency(void) { return READ_SYSREG(cntfrq_el0); }
static void clear(uint64_t p, unsigned n, uint8_t value) {
    for (unsigned i = 0; i < n; ++i) w8(p + i, value);
}

void exception_irq(struct exception_frame *f) {
    (void)f;
    uint64_t iar = READ_SYSREG(icc_iar1_el1);
    uint32_t id = iar & 0xffffff;
    if (id == 67) {
        uint8_t bits = r8(isr); /* read-clear deasserts INTx before EOIR */
        dsb_sy();
        ++irq_count;
        if (!bits || (bits & ~3u)) irq_bad = 1;
        if (bits & 2) {
            ++config_irq_count;
#ifdef SMALL_Q0
            config_irq_id = id; config_irq_bitmap = bits;
#endif
        }
        if (bits & 1) {
            irq_id = id; irq_bitmap = bits;
            seen_used[0] = r16(QUSED(0) + 2);
            seen_used[1] = r16(QUSED(1) + 2);
        } /* A config-only IRQ cannot satisfy queue completion. */
    } else if (id != 1023) irq_bad = id;
    if (id < 1020) { dsb_sy(); WRITE_SYSREG(icc_eoir1_el1, iar); isb(); }
}

static void configure_gic(void) {
    expect(READ_SYSREG(CurrentEL) == 4, "VUSB_NOT_EL1");
    uint32_t ctl = mmio32(GICD); expect(ctl & (1u << 6), "VUSB_GIC_DS_PROFILE");
    write32(GICR + 0x14, mmio32(GICR + 0x14) & ~2u);
    unsigned attempts = 1000000;
    while (mmio32(GICR + 0x14) & 4) if (!--attempts) fail("VUSB_GICR_WAKE");
    write32(GICD + 0x188, SPI); write32(GICD + 0x288, SPI);
    write32(GICD + 0x88, mmio32(GICD + 0x88) | SPI);
    write32(GICD + 0xc10, mmio32(GICD + 0xc10) & ~(3u << 6));
    w8(GICD + 0x443, 0x80); w64(GICD + 0x6218, 0);
    write32(GICD, ctl | 2u);
    WRITE_SYSREG(icc_sre_el1, 1); isb(); WRITE_SYSREG(icc_pmr_el1, 0xff);
    WRITE_SYSREG(icc_bpr1_el1, 0); WRITE_SYSREG(icc_ctlr_el1, 0);
    WRITE_SYSREG(icc_igrpen1_el1, 1);
}

static void configure_pci(void) {
    expect(mmio32(PCI) == 0x1a01106b, "VUSB_PCI_ID");
    expect(r8(PCI + 0x3d) == 1, "VUSB_NOT_INTA");
    w16(PCI + 4, 0); /* ordinary BAR sizing with decode disabled */
    uint64_t address = 0x51000000;
    for (unsigned bar = 0; bar < 6; ++bar) {
        uint64_t reg = PCI + 0x10 + 4 * bar;
        uint32_t original = mmio32(reg), hi = 0;
        write32(reg, 0xffffffff); uint32_t mask = mmio32(reg); write32(reg, original);
        if (!(mask & ~15u)) continue;
        expect(!(mask & 1), "VUSB_IO_BAR_UNEXPECTED");
        unsigned wide = (original & 6) == 4;
        uint64_t full_mask = mask & ~15u;
        if (wide) {
            expect(bar < 5, "VUSB_64BIT_BAR_END"); hi = mmio32(reg + 4);
            write32(reg + 4, 0xffffffff); full_mask |= (uint64_t)mmio32(reg + 4) << 32;
            write32(reg + 4, hi);
        } else full_mask |= 0xffffffff00000000ull;
        uint64_t size = ~full_mask + 1;
        expect(size && !(size & (size - 1)) && size <= 0x1000000, "VUSB_BAR_SIZE");
        address = (address + size - 1) & ~(size - 1);
        expect(address + size < 0x6fff0000, "VUSB_BAR_APERTURE");
        bar_base[bar] = address; bar_size[bar] = size;
        write32(reg, (uint32_t)address | (original & 15));
        if (wide) write32(reg + 4, address >> 32);
        field_uart("VUSB_BAR_INDEX", bar); field_uart("VUSB_BAR_ADDRESS", address);
        field_uart("VUSB_BAR_BYTES", size); address += size;
        if (wide) ++bar;
    }
    uint8_t visited[256] = {0}; unsigned count = 0;
    for (unsigned cap = r8(PCI + 0x34); cap; cap = r8(PCI + cap + 1)) {
        expect(cap >= 0x40 && cap <= 0xfc && !(cap & 3) && !visited[cap] && ++count <= 48,
               "VUSB_PCI_CAP_CHAIN"); visited[cap] = 1;
        unsigned id = r8(PCI + cap), length = r8(PCI + cap + 2);
        if (id == 0x11) expect(!(r16(PCI + cap + 2) & 0x8000), "VUSB_MSIX_ENABLED");
        if (id == 5) expect(!(r16(PCI + cap + 2) & 1), "VUSB_MSI_ENABLED");
        if (id != 9) continue;
        unsigned type = r8(PCI + cap + 3);
        if (type < 1 || type > 3) continue; /* no type5 data-window reads */
        expect(length >= (type == 2 ? 20 : 16) && cap + length <= 256, "VUSB_CAP_LENGTH");
        unsigned bar = r8(PCI + cap + 4);
        uint32_t offset = mmio32(PCI + cap + 8), bytes = mmio32(PCI + cap + 12);
        expect(bar < 6 && bar_base[bar] && bytes && offset <= bar_size[bar] &&
               bytes <= bar_size[bar] - offset, "VUSB_CAP_BAR_RANGE");
        uint64_t target = bar_base[bar] + offset;
        if (type == 1) { expect(!common && bytes >= 56, "VUSB_COMMON_CAP"); common = target; }
        if (type == 2) { expect(!notify && bytes >= 2, "VUSB_NOTIFY_CAP"); notify = target;
                         notify_multiplier = mmio32(PCI + cap + 16); notify_bytes = bytes; }
        if (type == 3) { expect(!isr, "VUSB_ISR_CAP"); isr = target; }
        field_uart("VUSB_CAP_TYPE", type); field_uart("VUSB_CAP_ADDRESS", target);
    }
    expect(common && notify && isr, "VUSB_MODERN_CAP_MISSING");
    w16(PCI + 4, 6); /* memory decode + bus master; INTx remains enabled */
    w8(common + 20, 0); dsb_sy(); expect(r8(common + 20) == 0, "VUSB_RESET_STATUS");
    w8(common + 20, 3); /* ACKNOWLEDGE + DRIVER */
    write32(common, 0); uint32_t low = mmio32(common + 4);
    write32(common, 1); uint32_t high = mmio32(common + 4);
    field_uart("VUSB_HOST_FEATURES_LOW", low); field_uart("VUSB_HOST_FEATURES_HIGH", high);
    expect((low & 0xffffff) == 0 && (high & 1), "VUSB_DEVICE_FEATURE_CONTRACT");
    write32(common + 8, 0); write32(common + 12, 0);
    write32(common + 8, 1); write32(common + 12, 1); /* VERSION_1 only */
    w8(common + 20, 11); dsb_sy(); expect((r8(common + 20) & 8) != 0, "VUSB_FEATURES_REJECTED");
    expect(r16(common + 18) == 2, "VUSB_QUEUE_COUNT");
    w16(common + 16, 0xffff);
    for (unsigned q = 0; q < 2; ++q) {
        clear(QDESC(q), 3 * PAGE, 0);
        w16(common + 22, q); expect(r16(common + 24) == RING_SIZE, "VUSB_QUEUE_SIZE");
        w16(common + 24, RING_SIZE); w16(common + 26, 0xffff);
        w64(common + 32, QDESC(q)); w64(common + 40, QAVAIL(q)); w64(common + 48, QUSED(q));
        uint64_t notify_offset = (uint64_t)r16(common + 30) * notify_multiplier;
        expect(notify_offset <= notify_bytes - 2, "VUSB_QUEUE_NOTIFY_RANGE");
        notify_address[q] = notify + notify_offset;
        w16(common + 28, 1); expect(r16(common + 28) == 1, "VUSB_QUEUE_ENABLE");
    }
    w8(common + 20, 15); expect(r8(common + 20) == 15, "VUSB_DRIVER_OK");
    (void)r8(isr); write32(GICD + 0x288, SPI);
    write32(GICD + 0x108, SPI); dsb_sy(); isb(); unmask_irq();
    puts_uart("VUSB MODERN VERSION1 TWO SPLIT256 INTX67 READY\n");
}

static void descriptor(unsigned q, unsigned index, uint64_t addr, uint32_t length, uint16_t flags, uint16_t next) {
    uint64_t p = QDESC(q) + 16 * index;
    w64(p, addr); write32(p + 8, length); w16(p + 12, flags); w16(p + 14, next);
}
static void publish(unsigned q) {
    mask_irq(); w16(QAVAIL(q) + 4 + 2 * (available[q] & 255), 0);
    dsb_sy(); ++available[q]; w16(QAVAIL(q) + 2, available[q]);
    dsb_sy(); w16(notify_address[q], q); dsb_sy(); unmask_irq();
}
#ifndef SMALL_Q0
static void wait_used_irq(unsigned q, unsigned wanted, uint32_t used_length) {
    uint64_t start = ticks();
    while (seen_used[q] != wanted) {
        expect(!irq_bad, "VUSB_IRQ_SOURCE");
        if (ticks() - start > frequency() * 10) fail("VUSB_USED_OR_ISR_TIMEOUT");
    }
    mask_irq(); dsb_sy(); expect(!irq_bad && irq_id == 67 && (irq_bitmap & 1), "VUSB_IRQ_NOT_VERIFIED");
    expect(r16(QUSED(q) + 2) == wanted, "VUSB_USED_INDEX");
    uint64_t elem = QUSED(q) + 4 + 8 * ((wanted - 1) & 255);
    expect(mmio32(elem) == 0 && mmio32(elem + 4) == used_length, "VUSB_USED_ELEMENT");
    puts_uart("VUSB IRQ QUEUE="); hex_uart(q); puts_uart(" USED="); hex_uart(wanted);
    puts_uart(" LENGTH="); hex_uart(used_length); puts_uart(" INTID="); hex_uart(irq_id);
    puts_uart(" BITS="); hex_uart(irq_bitmap); puts_uart(" COUNT="); hex_uart(irq_count); puts_uart("\n");
    unmask_irq();
}
static uint8_t request_byte(unsigned phase, unsigned i) { return (i * 13u + phase * 37u) ^ 0xa6u; }
static uint8_t response_byte(unsigned phase, unsigned i) {
    return (i * 17u + 0x5au + request_byte(phase, i % (phase == 1 ? 48u : 17u)));
}
static uint8_t large_byte(unsigned i) { return (i * 29u + (i >> 8) + 0x73u) ^ 0xa6u; }
#endif
#ifdef SMALL_Q0
static void small_q0_negative(void) {
    clear(REQUEST_HEADER, PAGE, 0xa5); clear(REQUEST_ARENA, REQUEST_ARENA_BYTES, 0xa5);
    descriptor(0, 0, REQUEST_HEADER + BUFFER_OFFSET, 6, 3, 1);
    descriptor(0, 1, REQUEST_ARENA + BUFFER_OFFSET, 8, 2, 0); /* capacity14 < valid body54 */
    publish(0); puts_uart("VUSB REQUEST1 READY\n");
    uint64_t start = ticks();
    while (!(r8(common + 20) & 64) || !config_irq_count) {
        expect(!irq_bad, "VUSB_NEGATIVE_IRQ_ERROR");
        if (ticks() - start > frequency() * 3) fail("VUSB_NEGATIVE_ERROR_NOT_OBSERVED");
    }
    mask_irq(); dsb_sy();
    expect(r16(QUSED(0) + 2) == 0 && seen_used[0] == 0 && mmio32(QUSED(0) + 4) == 0 &&
           mmio32(QUSED(0) + 8) == 0, "VUSB_NEGATIVE_FAKE_USED_COMPLETION");
    expect(config_irq_id == 67 && (config_irq_bitmap & 2), "VUSB_NEGATIVE_CONFIG_IRQ");
    for (unsigned i = 0; i < PAGE; ++i)
        expect(r8(REQUEST_HEADER + i) == 0xa5, "VUSB_NEGATIVE_HEADER_DMA_CHANGED");
    for (unsigned i = 0; i < REQUEST_ARENA_BYTES; ++i)
        expect(r8(REQUEST_ARENA + i) == 0xa5, "VUSB_NEGATIVE_PAYLOAD_DMA_CHANGED");
    field_uart("VUSB_NEGATIVE_DEVICE_STATUS", r8(common + 20));
    field_uart("VUSB_NEGATIVE_USED_INDEX", r16(QUSED(0) + 2));
    field_uart("VUSB_NEGATIVE_CONFIG_INTID", config_irq_id);
    field_uart("VUSB_NEGATIVE_CONFIG_BITMAP", config_irq_bitmap);
    puts_uart("VUSB NEGATIVE DMA TARGET GUARDS UNCHANGED PASS\n");
    puts_uart("E05 VIRTIO USB SMALL Q0 NEEDS RESET EXPECTED\n"); platform_exit(1);
}
#endif
#ifndef SMALL_Q0
static void request(unsigned phase) {
    unsigned length = phase == 1 ? 48 : 17;
    clear(REQUEST_HEADER, PAGE, 0xa5); clear(REQUEST_ARENA, REQUEST_ARENA_BYTES, 0xa5);
    descriptor(0, 0, REQUEST_HEADER + BUFFER_OFFSET, 6, 3, 1);
    descriptor(0, 1, REQUEST_ARENA + BUFFER_OFFSET, REQUEST_CAPACITY - 6, 2, 0);
    publish(0); puts_uart(phase == 1 ? "VUSB REQUEST1 READY\n" : "VUSB REQUEST2 READY\n");
    wait_used_irq(0, phase, length + 6);
    uint64_t h = REQUEST_HEADER + BUFFER_OFFSET;
    expect(mmio32(h) == length && r8(h + 4) == 4 && r8(h + 5) == 1, "VUSB_REQUEST_HEADER_BYTES");
    for (unsigned i = 0; i < PAGE; ++i)
        if (i < BUFFER_OFFSET || i >= BUFFER_OFFSET + 6) expect(r8(REQUEST_HEADER + i) == 0xa5, "VUSB_HEADER_GUARD");
    for (unsigned i = 0; i < REQUEST_ARENA_BYTES; ++i) {
        uint8_t expected = i >= BUFFER_OFFSET && i < BUFFER_OFFSET + length ? request_byte(phase, i - BUFFER_OFFSET) : 0xa5;
        expect(r8(REQUEST_ARENA + i) == expected, "VUSB_REQUEST_DATA_OR_GUARD");
    }
    puts_uart(phase == 1 ? "VUSB REQUEST1 DMA SG GUARDS PASS\n" : "VUSB REQUEST2 DMA SG GUARDS PASS\n");
}
static void response(unsigned phase, unsigned length) {
    clear(RESPONSE_HEADER, PAGE, 0x5a); clear(RESPONSE_ARENA, RESPONSE_ARENA_BYTES, 0x5a);
    w8(RESPONSE_HEADER + BUFFER_OFFSET, 1); w8(RESPONSE_HEADER + BUFFER_OFFSET + 1, 0x81);
    for (unsigned i = 0; i < length; ++i) w8(RESPONSE_ARENA + BUFFER_OFFSET + i,
                                          phase == 3 ? large_byte(i) : response_byte(phase, i));
    descriptor(1, 0, RESPONSE_HEADER + BUFFER_OFFSET, 2, 1, 1);
    descriptor(1, 1, RESPONSE_ARENA + BUFFER_OFFSET, length, 0, 0);
    publish(1);
    puts_uart(phase == 1 ? "VUSB RESPONSE1 QUEUED\n" : phase == 2 ? "VUSB RESPONSE2 QUEUED\n" : "VUSB LARGE QUEUED\n");
    if (phase == 3) {
        uint64_t start = ticks();
        while (ticks() - start < frequency() / 2) {
            expect(r16(QUSED(1) + 2) == 2 && seen_used[1] == 2, "VUSB_LARGE_EARLY_COMPLETION");
            expect(!irq_bad, "VUSB_LARGE_IRQ_ERROR");
        }
        puts_uart("VUSB BACKPRESSURE HELD WITHOUT USED OR ISR\n");
    }
    wait_used_irq(1, phase, 0);
    for (unsigned i = 0; i < PAGE; ++i) {
        uint8_t expected = i == BUFFER_OFFSET ? 1 : i == BUFFER_OFFSET + 1 ? 0x81 : 0x5a;
        expect(r8(RESPONSE_HEADER + i) == expected, "VUSB_RESPONSE_HEADER_CHANGED");
    }
    for (unsigned i = 0; i < RESPONSE_ARENA_BYTES; ++i) {
        uint8_t expected = i >= BUFFER_OFFSET && i < BUFFER_OFFSET + length ?
            (phase == 3 ? large_byte(i - BUFFER_OFFSET) : response_byte(phase, i - BUFFER_OFFSET)) : 0x5a;
        expect(r8(RESPONSE_ARENA + i) == expected, "VUSB_RESPONSE_DATA_OR_GUARD_CHANGED");
    }
    puts_uart(phase == 1 ? "VUSB RESPONSE1 IRQ GUARDS PASS\n" : phase == 2 ? "VUSB RESPONSE2 IRQ GUARDS PASS\n" : "VUSB LARGE COMPLETED IRQ GUARDS PASS\n");
}
#endif

void main(void) {
    field_uart("VUSB_CNTFRQ", frequency());
    configure_gic(); configure_pci();
#ifdef SMALL_Q0
    small_q0_negative();
#else
    request(1); response(1, 64); request(2); response(2, 113); response(3, LARGE_BYTES);
    mask_irq(); expect(r16(QUSED(0) + 2) == 2 && r16(QUSED(1) + 2) == 3, "VUSB_FINAL_USED_COUNTS");
    field_uart("VUSB_FINAL_IRQ_COUNT", irq_count);
    field_uart("VUSB_FINAL_CONFIG_IRQ_COUNT", config_irq_count);
    puts_uart("E05 VIRTIO USB REAL DMA SG IRQ REARM BACKPRESSURE PASS\n"); platform_exit(0);
#endif
}
