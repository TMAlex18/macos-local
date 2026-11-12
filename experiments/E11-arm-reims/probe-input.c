/* Self-authored EL1 xHCI/HID input receiver. All commands/transfers use normal
 * PCI and USB interfaces. QMP sends synthetic events only to this owned VM.
 * No SDL window, OS driver, Apple code, or host-filled guest report buffer.
 */
#define E11_RUNTIME_ONLY
#include "guest-common.h"
#include "guest-input.h"
typedef unsigned short u16;

#define PCI_CFG 0x40018000ull /* explicit PCI slot3 on VMApple ECAM */
#define XHCI_BASE 0x50010000ull
#define DMA_BASE 0x70400000ull
#define DCBAA (DMA_BASE)
#define COMMAND_RING (DMA_BASE + PAGE)
#define EVENT_RING (DMA_BASE + 2 * PAGE)
#define ERST (DMA_BASE + 3 * PAGE)
#define INPUT_CONTEXT (DMA_BASE + 4 * PAGE)
#define DEVICE_CONTEXT(n) (DMA_BASE + (5 + (n)) * PAGE)
#define TRANSFER_RING(n) (DMA_BASE + (7 + (n)) * PAGE)
#define USB_BUFFER(n) (DMA_BASE + (9 + (n)) * PAGE)

static u64 op, runtime, doorbell;
static u32 event_index, event_cycle = 1, command_index;
static u32 ring_index[2], ring_cycle[2] = {1, 1}, slot[2];
static u32 transfer_events;
static u32 initialized_devices;

static u32 read32(u64 p) { return *(volatile u32 *)p; }
static void write32(u64 p, u32 v) { *(volatile u32 *)p = v; }
static void write64(u64 p, u64 v) { *(volatile u64 *)p = v; }

__attribute__((noreturn)) static void fail(const char *why, u32 value) {
    puts("INPUT FAIL "); puts(why); puts(" VALUE="); hex(value); puts("\n"); finish(1);
}

static u64 frequency(void) {
    u64 v; __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(v)); return v;
}

static void wait_clear(u64 p, u32 mask) {
    u64 start = counter();
    while (read32(p) & mask) if (counter() - start > frequency() * 3) fail("REGISTER TIMEOUT", read32(p));
}

static void trb(u64 p, u64 parameter, u32 status, u32 control) {
    write64(p, parameter); write32(p + 8, status);
    __asm__ volatile("dmb sy" ::: "memory"); write32(p + 12, control);
}

static u32 event(u32 wanted_type, u64 wanted_pointer) {
    u64 start = counter();
    for (;;) {
        u64 p = EVENT_RING + event_index * 16;
        u32 control = read32(p + 12);
        if ((control & 1) == event_cycle) {
            __asm__ volatile("dmb sy" ::: "memory");
            u32 status = read32(p + 8), type = (control >> 10) & 63;
            u64 pointer = *(volatile u64 *)p;
            if (++event_index == 1024) { event_index = 0; event_cycle ^= 1; }
            write64(runtime + 0x38, (EVENT_RING + event_index * 16) | 8);
            if (type == wanted_type && pointer == wanted_pointer) {
                if ((status >> 24) != 1) fail("XHCI COMPLETION", status);
                if (type == 32) ++transfer_events;
                return control;
            }
            if (type != 34) fail("UNEXPECTED EVENT", control);
        }
        if (counter() - start > frequency() * 3) fail("EVENT TIMEOUT", wanted_type);
    }
}

static u32 command(u32 type, u64 parameter, u32 slot_id) {
    u64 p = COMMAND_RING + command_index++ * 16;
    trb(p, parameter, 0, (type << 10) | (slot_id << 24) | 1);
    __asm__ volatile("dsb sy" ::: "memory"); write32(doorbell, 0);
    return event(33, p) >> 24;
}

static u64 transfer_trb(u32 n, u64 parameter, u32 length, u32 control) {
    if (ring_index[n] == 1023) {
        trb(TRANSFER_RING(n) + 1023 * 16, TRANSFER_RING(n), 0, (6 << 10) | 2 | ring_cycle[n]);
        ring_index[n] = 0; ring_cycle[n] ^= 1;
    }
    u64 p = TRANSFER_RING(n) + ring_index[n]++ * 16;
    trb(p, parameter, length, control | ring_cycle[n]);
    return p;
}

static void control(u32 n, u8 request_type, u8 request, u16 value, u16 length) {
    volatile u8 *buffer = (volatile u8 *)USB_BUFFER(n);
    for (u32 i = 0; i < 64; ++i) buffer[i] = 0xa5; /* DMA must replace this */
    u64 setup = request_type | ((u64)request << 8) | ((u64)value << 16) | ((u64)length << 48);
    transfer_trb(n, setup, 8, (2 << 10) | (1 << 6) | (length ? (3 << 16) : 0));
    if (length) transfer_trb(n, USB_BUFFER(n), length, (3 << 10) | (1 << 16));
    u64 status = transfer_trb(n, 0, 0, (4 << 10) | (1 << 5) | (length ? 0 : (1 << 16)));
    __asm__ volatile("dsb sy" ::: "memory"); write32(doorbell + slot[n] * 4, 1);
    event(32, status);
    __asm__ volatile("dmb sy" ::: "memory");
}

static void enumerate(u32 n, u32 port) {
    u64 portsc = op + 0x400 + port * 16;
    u32 speed = (read32(portsc) >> 10) & 15;
    write32(portsc, (read32(portsc) & (1 << 9)) | (1 << 4));
    wait_clear(portsc, 1 << 4);
    if (!(read32(portsc) & 2)) fail("PORT NOT ENABLED", port);
    slot[n] = command(9, 0, 0);
    if (!slot[n] || slot[n] > 8) fail("SLOT ID", slot[n]);
    write64(DCBAA + slot[n] * 8, DEVICE_CONTEXT(n));
    for (u32 i = 0; i < 128; i += 4) write32(INPUT_CONTEXT + i, 0);
    write32(INPUT_CONTEXT + 4, 3);
    write32(INPUT_CONTEXT + 32, (speed << 20) | (1 << 27));
    write32(INPUT_CONTEXT + 36, (port + 1) << 16);
    write32(INPUT_CONTEXT + 68, (3 << 1) | (4 << 3) | ((speed >= 3 ? 64 : 8) << 16));
    write64(INPUT_CONTEXT + 72, TRANSFER_RING(n) | 1);
    write32(INPUT_CONTEXT + 80, 8);
    command(11, INPUT_CONTEXT, slot[n]);
    control(n, 0x80, 6, 0x0100, 18); /* USB GET_DESCRIPTOR(device) */
    volatile u8 *d = (volatile u8 *)USB_BUFFER(n);
    if (d[0] != 18 || d[1] != 1 || d[7] != (speed >= 3 ? 64 : 8)) fail("USB DESCRIPTOR", d[0]);
    puts("INPUT USB DEVICE INDEX="); hex(n); puts(" PORT="); hex(port + 1);
    puts(" SLOT="); hex(slot[n]); puts(" SPEED="); hex(speed);
    puts(" BCDUSB="); hex(d[2] | (d[3] << 8)); puts("\n");
    control(n, 0x00, 9, 1, 0); /* standard SET_CONFIGURATION(1) */
    control(n, 0xa1, 1, 0x0100, n == 0 ? 8 : 6); /* HID GET_REPORT, interface0 */
}

static void report(u32 n, const char *label, u32 length) {
    puts(label);
    for (u32 i = 0; i < length; ++i) { hex(((volatile u8 *)USB_BUFFER(n))[i]); puts(" "); }
    puts("\n");
}

#if !defined(E11_INPUT_LIBRARY) && !defined(USE_USB_IRQ)
static void wait_input(u32 released) {
    u64 start = counter();
    for (;;) {
        control(0, 0xa1, 1, 0x0100, 8);
        volatile u8 *k = (volatile u8 *)USB_BUFFER(0);
        u32 keyboard_ok = k[0] == 0 && k[1] == 0 && k[2] == (released ? 0 : 4);
        for (u32 i = 3; i < 8; ++i) keyboard_ok &= k[i] == 0;
        control(1, 0xa1, 1, 0x0100, 6);
        volatile u8 *t = (volatile u8 *)USB_BUFFER(1);
        u32 pointer_ok = t[0] == (released ? 0 : 1) &&
            (t[1] | (t[2] << 8)) == 12345 && (t[3] | (t[4] << 8)) == 23456 && t[5] == 0;
        if (keyboard_ok && pointer_ok) {
            report(0, released ? "INPUT KEY UP REPORT=" : "INPUT KEY DOWN REPORT=", 8);
            report(1, released ? "INPUT POINTER UP REPORT=" : "INPUT POINTER DOWN REPORT=", 6);
            return;
        }
        if (counter() - start > frequency() * 15) fail("HID INPUT REPORT TIMEOUT", released);
        u64 pause = counter(); while (counter() - pause < frequency() / 100) {}
    }
}
#endif

void input_usb_init(u32 expected_devices) {
    if (expected_devices < 1 || expected_devices > 2) fail("REQUESTED DEVICE COUNT", expected_devices);
    if ((read32(PCI_CFG) & 0xffff) != 0x1b36) fail("XHCI PCI VENDOR", read32(PCI_CFG));
    write32(PCI_CFG + 0x10, XHCI_BASE); write32(PCI_CFG + 0x14, 0);
    *(volatile u16 *)(PCI_CFG + 4) = 6; /* ordinary guest memory/bus-master enables */
    op = XHCI_BASE + (*(volatile u8 *)XHCI_BASE);
    runtime = XHCI_BASE + (read32(XHCI_BASE + 0x18) & ~31u);
    doorbell = XHCI_BASE + (read32(XHCI_BASE + 0x14) & ~3u);
    if (read32(XHCI_BASE + 0x10) & 4) fail("64BYTE CONTEXT NOT THIS FIXTURE", 0);
    write32(op, 0); wait_clear(op, 1);
    write32(op, 2); wait_clear(op, 2); wait_clear(op + 4, 1 << 11);
    if (!(read32(op + 8) & 1)) fail("XHCI 4K DMA PAGE UNSUPPORTED", read32(op + 8));
    for (u32 i = 0; i < 11 * PAGE; ++i) *(volatile u8 *)(DMA_BASE + i) = 0;
    write64(ERST, EVENT_RING); write32(ERST + 8, 1024);
    write32(runtime + 0x28, 1); write64(runtime + 0x30, ERST); write64(runtime + 0x38, EVENT_RING);
    write64(op + 0x30, DCBAA); write64(op + 0x18, COMMAND_RING | 1); write32(op + 0x38, 8);
    write32(op, 1);
    u32 devices = 0, ports = read32(XHCI_BASE + 4) >> 24;
    for (u32 p = 0; p < ports && devices < expected_devices; ++p)
        if (read32(op + 0x400 + p * 16) & 1) enumerate(devices++, p);
    if (devices != expected_devices) fail("CONNECTED HID DEVICE COUNT", devices);
    initialized_devices = devices;
}

void input_usb_wait_key(u32 released) {
    u64 start = counter();
    for (;;) {
        control(0, 0xa1, 1, 0x0100, 8);
        volatile u8 *k = (volatile u8 *)USB_BUFFER(0);
        u32 ok = k[0] == 0 && k[1] == 0 && k[2] == (released ? 0 : 4);
        for (u32 i = 3; i < 8; ++i) ok &= k[i] == 0;
        if (ok) {
            report(0, released ? "INPUT NATIVE KEY UP REPORT=" : "INPUT NATIVE KEY DOWN REPORT=", 8);
            return;
        }
        if (counter() - start > frequency() * 8) fail("NATIVE HID KEY TIMEOUT", released);
        u64 pause = counter(); while (counter() - pause < frequency() / 100) {}
    }
}

void input_usb_shutdown(void) {
    for (u32 i = 0; i < initialized_devices; ++i) command(10, 0, slot[i]);
    write32(op, 0);
}

#ifdef USE_USB_IRQ
#include "guest-usb-irq.h"
#elif !defined(E11_INPUT_LIBRARY)
void guest_main(void) {
    input_usb_init(2);
    puts("INPUT QMP DOWN READY\n"); wait_input(0);
    puts("INPUT QMP UP READY\n"); wait_input(1);
    puts("INPUT XHCI TRANSFER EVENTS="); hex(transfer_events); puts("\n");
    input_usb_shutdown();
    puts("REIMS ARM XHCI USB HID INPUT REPORTS PASS\n"); finish(0);
}
#endif
