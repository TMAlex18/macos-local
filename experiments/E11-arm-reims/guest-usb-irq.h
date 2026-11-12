/* Self-authored standard xHCI interrupt-IN / PCI INTx / GICv3 receiver.
 * Slot3 INTA -> gpex (3 + 0) % 4 -> VMApple input0x23 -> SPI INTID67.
 * This is a level signal, unlike the graphics device's pulsed INTID49.
 */
#define USB_GICD 0x10000000ull
#define USB_GICR 0x10010000ull
#define USB_INTID 67u
#define USB_SPI_BIT (1u << 3)
#define USB_SYS_WRITE(reg, value) do { u64 v_ = (value); \
    __asm__ volatile("msr " #reg ", %0" :: "r"(v_) : "memory"); } while (0)

static u32 interrupt_dci;
static volatile u32 usb_irq_count, usb_irq_completed, usb_irq_expected;
static volatile u32 usb_irq_id, usb_irq_iman, usb_irq_status, usb_irq_bad;
static volatile u64 usb_irq_trb;
static volatile u8 usb_irq_report[8];

#ifndef E11_INPUT_LIBRARY
void guest_bad_exception(void) { fail("USB IRQ UNEXPECTED EXCEPTION", 0); }
#endif

static void advance_event(void) {
    if (++event_index == 1024) { event_index = 0; event_cycle ^= 1; }
}

u32 input_usb_irq_handle(u32 id) {
    if (id == USB_INTID) {
        u32 iman = read32(runtime + 0x20), status = read32(op + 4);
        u64 p = EVENT_RING + event_index * 16;
        u32 control = read32(p + 12);
        __asm__ volatile("dmb sy" ::: "memory");
        u32 completion = read32(p + 8);
        if ((iman & 3) != 3 || !(status & 8) ||
            (control & 1) != event_cycle || ((control >> 10) & 63) != 32 ||
            ((control >> 16) & 31) != interrupt_dci || (control >> 24) != slot[0] ||
            *(volatile u64 *)p != usb_irq_trb || (completion >> 24) != 1 ||
            (completion & 0xffffff)) {
            usb_irq_bad = 1;
        } else {
            for (u32 i = 0; i < 8; ++i) usb_irq_report[i] = ((volatile u8 *)USB_BUFFER(1))[i];
            usb_irq_id = id; usb_irq_iman = iman; usb_irq_status = status;
            ++transfer_events; ++usb_irq_count;
            usb_irq_completed = usb_irq_expected;
            advance_event();
        }
        /* Deassert the controller's level before GIC EOImode0 deactivation.
         * IP/EINT are W1C; EHB clears only after advancing the consumer. */
        write32(runtime + 0x20, 3);
        write32(op + 4, 8);
        write64(runtime + 0x38, (EVENT_RING + event_index * 16) | 8);
        __asm__ volatile("dsb sy" ::: "memory");
        if (usb_irq_bad) write32(USB_GICD + 0x188, USB_SPI_BIT);
    } else return 0;
    return !usb_irq_bad;
}

#ifndef E11_INPUT_LIBRARY
void guest_irq_handler(void) {
    u64 iar;
    __asm__ volatile("mrs %0, icc_iar1_el1" : "=r"(iar));
    u32 id = iar & 0xffffff;
    if (id != 1023 && !input_usb_irq_handle(id)) usb_irq_bad = id;
    if (id < 1020) {
        USB_SYS_WRITE(icc_eoir1_el1, iar);
        __asm__ volatile("isb" ::: "memory");
    }
}
#endif

static void configure_interrupt_endpoint(void) {
    /* The actual standard configuration descriptor supplies endpoint address,
     * type, packet size and interval. Restrict this fixture to the one boot
     * keyboard's eight-byte high-speed interrupt-IN report. */
    control(0, 0x80, 6, 0x0200, 9);
    volatile u8 *d = (volatile u8 *)USB_BUFFER(0);
    u32 length = d[2] | (d[3] << 8);
    if (d[0] != 9 || d[1] != 2 || length < 9 || length > 64) fail("CONFIG DESCRIPTOR LENGTH", length);
    control(0, 0x80, 6, 0x0200, length);
    u32 endpoint = 0, packet = 0, interval = 0, count = 0;
    for (u32 offset = 0; offset < length;) {
        if (d[offset] < 2 || offset + d[offset] > length) fail("CONFIG DESCRIPTOR BOUNDS", offset);
        if (d[offset + 1] == 5) {
            if (d[offset] < 7 || !(d[offset + 2] & 0x80) || (d[offset + 3] & 3) != 3)
                fail("NOT INTERRUPT IN", offset);
            endpoint = d[offset + 2]; packet = d[offset + 4] | (d[offset + 5] << 8);
            interval = d[offset + 6]; ++count;
        }
        offset += d[offset];
    }
    if (count != 1 || packet != 8 || !(endpoint & 15) || interval < 1 || interval > 16 ||
        ((read32(DEVICE_CONTEXT(0)) >> 20) & 15) != 3)
        fail("KEYBOARD ENDPOINT CONTRACT", endpoint);
    interrupt_dci = 2 * (endpoint & 15) + 1;
    for (u32 i = 0; i < 32 + 32 * (interrupt_dci + 1); i += 4) write32(INPUT_CONTEXT + i, 0);
    write32(INPUT_CONTEXT + 4, 1 | (1u << interrupt_dci));
    for (u32 i = 0; i < 16; i += 4) write32(INPUT_CONTEXT + 32 + i, read32(DEVICE_CONTEXT(0) + i));
    write32(INPUT_CONTEXT + 32, (read32(INPUT_CONTEXT + 32) & ~(31u << 27)) | (interrupt_dci << 27));
    u64 ep = INPUT_CONTEXT + 32 + 32 * interrupt_dci;
    write32(ep, (interval - 1) << 16);
    write32(ep + 4, (3 << 1) | (7 << 3) | (packet << 16));
    write64(ep + 8, TRANSFER_RING(1) | 1);
    write32(ep + 16, (packet << 16) | packet);
    command(12, INPUT_CONTEXT, slot[0]);
    puts("INPUT INTERRUPT ENDPOINT="); hex(endpoint); puts(" DCI="); hex(interrupt_dci);
    puts(" PACKET="); hex(packet); puts(" INTERVAL="); hex(interval); puts("\n");
}

static void configure_usb_irq(void) {
    u64 el; __asm__ volatile("mrs %0, CurrentEL" : "=r"(el));
    u32 ctl = read32(USB_GICD);
    if (el != 4 || !(ctl & (1u << 6))) fail("USB GIC EL1 DS PROFILE", ctl);
    if (*(volatile u8 *)(PCI_CFG + 0x3d) != 1) fail("PCI INTA PIN", read32(PCI_CFG + 0x3c));
    /* Check capability enable bits; do not reinterpret an MSI as INTx. */
    u32 next = *(volatile u8 *)(PCI_CFG + 0x34), seen = 0;
    while (next) {
        if (next < 0x40 || next > 0xfc || (next & 3) || ++seen > 48) fail("PCI CAPABILITY CHAIN", next);
        u32 cap = read32(PCI_CFG + next);
        if (((cap & 0xff) == 5 && (cap & (1u << 16))) ||
            ((cap & 0xff) == 0x11 && (cap & (1u << 31)))) fail("MSI ACTIVE NOT INTX", cap);
        next = (cap >> 8) & 0xff;
    }
    write32(USB_GICR + 0x14, read32(USB_GICR + 0x14) & ~2u);
    u32 n = 1000000;
    while (read32(USB_GICR + 0x14) & 4) if (!--n) fail("USB GICR WAKE", 0);
    write32(USB_GICD + 0x188, USB_SPI_BIT);
    write32(USB_GICD + 0x288, USB_SPI_BIT);
    write32(USB_GICD + 0x88, read32(USB_GICD + 0x88) | USB_SPI_BIT);
    write32(USB_GICD + 0xc10, read32(USB_GICD + 0xc10) & ~(3u << 6)); /* level */
    *(volatile u8 *)(USB_GICD + 0x443) = 0x80;
    write64(USB_GICD + 0x6218, 0);
    write32(USB_GICD, ctl | 2);
    USB_SYS_WRITE(icc_sre_el1, 1); __asm__ volatile("isb" ::: "memory");
    USB_SYS_WRITE(icc_pmr_el1, 0xff); USB_SYS_WRITE(icc_bpr1_el1, 0);
    USB_SYS_WRITE(icc_ctlr_el1, 0); USB_SYS_WRITE(icc_igrpen1_el1, 1);
    write32(runtime + 0x20, 3); write32(op + 4, 8);
    write64(runtime + 0x38, (EVENT_RING + event_index * 16) | 8);
    write32(op, read32(op) | 4);
#ifndef BLOCK_WORK_SPI
    write32(USB_GICD + 0x108, USB_SPI_BIT);
#endif
    __asm__ volatile("dsb sy; isb; msr daifclr, #2" ::: "memory");
    puts("INPUT XHCI PCI INTX LEVEL INTID67 CONFIGURED\n");
}

void input_usb_irq_start(void) {
    configure_interrupt_endpoint(); configure_usb_irq();
}

void input_usb_wait_irq_key(u32 released) {
    __asm__ volatile("msr daifset, #2" ::: "memory");
    usb_irq_expected = released + 1;
    for (u32 i = 0; i < 8; ++i) ((volatile u8 *)USB_BUFFER(1))[i] = 0xa5;
    usb_irq_trb = transfer_trb(1, USB_BUFFER(1), 8, (1 << 10) | (1 << 5));
    __asm__ volatile("dsb sy" ::: "memory");
    write32(doorbell + slot[0] * 4, interrupt_dci);
    __asm__ volatile("msr daifclr, #2" ::: "memory");
#ifdef USE_NATIVE_USB_IRQ
    puts(released ? "INPUT NATIVE KEY UP READY\n" : "INPUT NATIVE KEY DOWN READY\n");
#else
    puts(released ? "INPUT QMP UP READY\n" : "INPUT QMP DOWN READY\n");
#endif
    u64 start = counter();
    /* Only ISR state can satisfy normal completion; no DMA/event-ring polling. */
    while (usb_irq_completed != released + 1) {
        if (usb_irq_bad) fail("USB IRQ EVENT OR SOURCE", usb_irq_bad);
        if (counter() - start > frequency() * 5) {
#ifdef BLOCK_WORK_SPI
            u64 p = EVENT_RING + event_index * 16;
            u32 c = read32(p + 12), s = read32(p + 8);
            volatile u8 *b = (volatile u8 *)USB_BUFFER(1);
            u32 valid = b[0] == 0 && b[1] == 0 && b[2] == 4;
            for (u32 i = 3; i < 8; ++i) valid &= b[i] == 0;
            if (!released && usb_irq_count == 0 && (c & 1) == event_cycle &&
                ((c >> 10) & 63) == 32 && ((c >> 16) & 31) == interrupt_dci &&
                (c >> 24) == slot[0] && *(volatile u64 *)p == usb_irq_trb &&
                (s >> 24) == 1 && !(s & 0xffffff) && valid &&
                (read32(runtime + 0x20) & 3) == 3 &&
                (read32(USB_GICD + 0x208) & USB_SPI_BIT)) {
                report(1, "INPUT IRQ MISSING KEY DOWN REPORT=", 8);
                puts("INPUT IRQ DISABLED DMA EVENT READY GIC PENDING WITHOUT ISR\n");
                puts("REIMS INPUT IRQ REQUIRED MISSING EXPECTED\n"); finish(1);
            }
#endif
            fail("USB IRQ WAIT TIMEOUT", released);
        }
    }
    __asm__ volatile("dmb sy; msr daifset, #2" ::: "memory");
    u32 valid = usb_irq_report[0] == 0 && usb_irq_report[1] == 0 && usb_irq_report[2] == (released ? 0 : 4);
    for (u32 i = 3; i < 8; ++i) valid &= usb_irq_report[i] == 0;
    if (!valid) fail("INTERRUPT HID REPORT", released);
    puts(released ? "INPUT IRQ KEY UP REPORT=" : "INPUT IRQ KEY DOWN REPORT=");
    for (u32 i = 0; i < 8; ++i) { hex(usb_irq_report[i]); puts(" "); } puts("\n");
    puts("INPUT IRQ CAPTURE PHASE="); hex(released + 1); puts(" INTID="); hex(usb_irq_id);
    puts(" IMAN="); hex(usb_irq_iman); puts(" USBSTS="); hex(usb_irq_status);
    puts(" COUNT="); hex(usb_irq_count); puts("\n");
    if ((read32(runtime + 0x20) & 1) || (read32(op + 4) & 8)) fail("USB IRQ ACK", read32(runtime + 0x20));
#ifdef E11_INPUT_LIBRARY
    __asm__ volatile("msr daifclr, #2" ::: "memory");
#endif
}

void input_usb_irq_shutdown(void) {
    /* Teardown commands are ordinary polled completions after disabling IRQs. */
    __asm__ volatile("msr daifset, #2" ::: "memory");
    write32(USB_GICD + 0x188, USB_SPI_BIT); write32(runtime + 0x20, 1);
    write32(op, read32(op) & ~4u); input_usb_shutdown();
}

#ifndef E11_INPUT_LIBRARY
void guest_main(void) {
    input_usb_init(1); input_usb_irq_start();
    input_usb_wait_irq_key(0); input_usb_wait_irq_key(1);
    input_usb_irq_shutdown();
    puts("REIMS ARM XHCI INTERRUPT IN GIC KEY DOWN UP PASS\n"); finish(0);
}
#endif
