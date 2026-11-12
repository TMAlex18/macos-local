/* Ordinary caller-supplied AES128 ECB only: select0/context0.
 * NIST SP800-38A F.1.1 block1. No other key selector or second MMIO window.
 */
#include "runtime.h"
#define AES 0x30220000ull
#define FIFO (AES + 0x200)
#define IRQ_STATUS (AES + 0x18)
#define IRQ_ENABLE (AES + 0x1c)
#define FLAG_INFO (AES + 0x30)
#define FLAG_IRQ (1u << 5)
#define GICD 0x10000000ull
#define GICR 0x10010000ull
#define AES_INTID 50u /* VMApple input0x12 + 32 private interrupts */
#define SPI_BIT (1u << 18)
#define PAGE 16384u
#define DATA_OFFSET 256u

static const uint8_t key[16] = {
    0x2b,0x7e,0x15,0x16,0x28,0xae,0xd2,0xa6,0xab,0xf7,0x15,0x88,0x09,0xcf,0x4f,0x3c
};
static const uint8_t plaintext[16] = {
    0x6b,0xc1,0xbe,0xe2,0x2e,0x40,0x9f,0x96,0xe9,0x3d,0x7e,0x11,0x73,0x93,0x17,0x2a
};
static const uint8_t ciphertext[16] = {
    0x3a,0xd7,0x7b,0xb4,0x0d,0x7a,0x36,0x60,0xa8,0x9e,0xca,0xf3,0x24,0x66,0xef,0x97
};
static volatile uint64_t irq_count, irq_id, irq_status, irq_flag, irq_bad;
static volatile uint32_t expected_flag;

void exception_irq(struct exception_frame *f) {
    (void)f;
    uint64_t iar = READ_SYSREG(icc_iar1_el1);
    uint32_t id = iar & 0xffffff;
    if (id == AES_INTID) {
        irq_id = id; irq_status = mmio32(IRQ_STATUS); irq_flag = mmio32(FLAG_INFO);
        if (irq_status != FLAG_IRQ || irq_flag != expected_flag) irq_bad = 1;
        write32(IRQ_STATUS, FLAG_IRQ); /* W1C level source before EOImode0 */
        dsb_sy();
        if (mmio32(IRQ_STATUS) != 0) irq_bad = 1;
        ++irq_count;
    } else if (id != 1023) irq_bad = id;
    if (id < 1020) { WRITE_SYSREG(icc_eoir1_el1, iar); isb(); }
}

static void configure_gic(void) {
    expect(READ_SYSREG(CurrentEL) == 4, "AES_NOT_EL1");
    uint32_t ctl = mmio32(GICD);
    expect(ctl & (1u << 6), "AES_GIC_DS_PROFILE");
    write32(GICR + 0x14, mmio32(GICR + 0x14) & ~2u);
    unsigned n = 0;
    while (mmio32(GICR + 0x14) & 4) if (++n > 1000000) fail("AES_GICR_WAKE");
    write32(GICD + 0x184, SPI_BIT); write32(GICD + 0x284, SPI_BIT);
    write32(GICD + 0x84, mmio32(GICD + 0x84) | SPI_BIT);
    write32(GICD + 0xc0c, mmio32(GICD + 0xc0c) & ~(3u << 4)); /* level50 */
    *(volatile uint8_t *)(uintptr_t)(GICD + 0x432) = 0x80;
    *(volatile uint64_t *)(uintptr_t)(GICD + 0x6190) = 0;
    write32(GICD, ctl | 2u);
    WRITE_SYSREG(icc_sre_el1, 1); isb();
    WRITE_SYSREG(icc_pmr_el1, 0xff); WRITE_SYSREG(icc_bpr1_el1, 0);
    WRITE_SYSREG(icc_ctlr_el1, 0); WRITE_SYSREG(icc_igrpen1_el1, 1);
#ifndef BLOCK_AES_SPI
    write32(GICD + 0x104, SPI_BIT);
#endif
    write32(IRQ_STATUS, FLAG_IRQ); write32(IRQ_ENABLE, FLAG_IRQ);
    dsb_sy(); isb();
}

static void prepare_pages(uint64_t src_page, uint64_t dst_page) {
    volatile uint8_t *src = (void *)(uintptr_t)src_page, *dst = (void *)(uintptr_t)dst_page;
    for (unsigned i = 0; i < PAGE; ++i) { src[i] = 0xa5; dst[i] = 0x5a; }
    for (unsigned i = 0; i < 16; ++i) src[DATA_OFFSET + i] = plaintext[i];
    dsb_sy();
}

static void check_pages(uint64_t src_page, uint64_t dst_page) {
    volatile uint8_t *src = (void *)(uintptr_t)src_page, *dst = (void *)(uintptr_t)dst_page;
    dsb_sy();
    for (unsigned i = 0; i < PAGE; ++i) {
        unsigned in_data = i >= DATA_OFFSET && i < DATA_OFFSET + 16;
        uint8_t wanted_src = in_data ? plaintext[i - DATA_OFFSET] : 0xa5;
        uint8_t wanted_dst = in_data ? ciphertext[i - DATA_OFFSET] : 0x5a;
        expect(src[i] == wanted_src, "AES_INPUT_OR_GUARD_CHANGED");
        expect(dst[i] == wanted_dst, "AES_CIPHERTEXT_OR_OUTPUT_GUARD");
    }
    puts_uart("AES_CIPHERTEXT=");
    const char digits[] = "0123456789abcdef";
    for (unsigned i = 0; i < 16; ++i) {
        uint8_t v = dst[DATA_OFFSET + i];
        char byte[3] = {digits[v >> 4], digits[v & 15], 0}; puts_uart(byte);
    }
    puts_uart("\nAES_INPUT_OUTPUT_16K_GUARDS_PASS\n");
}

static void submit_job(uint32_t flag, uint64_t src_page, uint64_t dst_page) {
    mask_irq(); expected_flag = flag;
    prepare_pages(src_page, dst_page);
    uint64_t src = src_page + DATA_OFFSET, dst = dst_page + DATA_OFFSET;
    /* Source[47:32] occupies bits31:16, destination[47:32] bits15:0. */
    uint32_t high = ((src >> 16) & 0xffff0000u) | ((dst >> 32) & 0xffff);
    field_uart("AES_JOB", flag); field_uart("AES_SRC_PA", src); field_uart("AES_DST_PA", dst);
    field_uart("AES_ADDR_WORD", high);
    write32(FIFO, 0x50000010); /* DATA len16, caller key context0 */
    write32(FIFO, high); write32(FIFO, src); write32(FIFO, dst);
    write32(FIFO, 0x88000000u | flag); /* FLAG raiseIRQ, distinct info */
    dsb_sy();
    expect(irq_count == flag - 1, "AES_IRQ_IGNORED_CPU_MASK");
    expect(mmio32(IRQ_STATUS) == FLAG_IRQ && mmio32(FLAG_INFO) == flag, "AES_FLAG_NOT_READY");
    unmask_irq();
    uint64_t frequency = READ_SYSREG(cntfrq_el0), start = READ_SYSREG(cntvct_el0);
    while (irq_count != flag) {
        expect(!irq_bad, "AES_WRONG_IRQ_SOURCE");
        if (READ_SYSREG(cntvct_el0) - start > frequency * 2) {
#ifdef BLOCK_AES_SPI
            mask_irq();
            expect(flag == 1 && irq_count == 0, "AES_NEGATIVE_ISR_PRESENT");
            expect(mmio32(IRQ_STATUS) == FLAG_IRQ && mmio32(FLAG_INFO) == flag, "AES_NEGATIVE_FLAG_ABSENT");
            expect(mmio32(GICD + 0x204) & SPI_BIT, "AES_NEGATIVE_GIC_NOT_PENDING");
            check_pages(src_page, dst_page);
            field_uart("AES_NEGATIVE_IRQ_STATUS", mmio32(IRQ_STATUS));
            field_uart("AES_NEGATIVE_FLAG_INFO", mmio32(FLAG_INFO));
            field_uart("AES_NEGATIVE_GIC_PENDING", mmio32(GICD + 0x204));
            field_uart("AES_IRQ_COUNT", irq_count);
            puts_uart("E06 AES SPI50 MASKED DATA FLAG READY WITHOUT ISR EXPECTED\n"); platform_exit(1);
#endif
            fail("AES_ISR_TIMEOUT");
        }
    }
    mask_irq();
    expect(!irq_bad && irq_id == AES_INTID && irq_status == FLAG_IRQ && irq_flag == flag, "AES_ISR_NOT_VERIFIED");
    expect(mmio32(IRQ_STATUS) == 0, "AES_LEVEL_SOURCE_NOT_ACKED");
    check_pages(src_page, dst_page);
    field_uart("AES_ISR_INTID", irq_id); field_uart("AES_ISR_STATUS", irq_status);
    field_uart("AES_ISR_FLAG", irq_flag); field_uart("AES_IRQ_COUNT", irq_count);
    puts_uart("AES_JOB_ISR_AND_DATA_PASS\n");
}

void main(void) {
    puts_uart("E06 AES128 ECB USER KEY SELECT0 CONTEXT0 BEGIN\n");
    configure_gic();
    /* KEY op1, context0, select0, 128-bit, encrypt, ECB; exactly four
     * little-endian words carry only the above public NIST test key. */
    write32(FIFO, 0x10100000);
    for (unsigned i = 0; i < 16; i += 4)
        write32(FIFO, key[i] | ((uint32_t)key[i + 1] << 8) |
                      ((uint32_t)key[i + 2] << 16) | ((uint32_t)key[i + 3] << 24));
    submit_job(1, 0x72000000ull, 0x72004000ull);
    submit_job(2, 0x110000000ull, 0x110004000ull);
    write32(IRQ_ENABLE, 0); write32(GICD + 0x184, SPI_BIT);
    field_uart("AES_FINAL_IRQ_STATUS", mmio32(IRQ_STATUS));
    puts_uart("E06 AES128 USER KEY LOW HIGH DMA IRQ ACK REARM PASS\n"); platform_exit(0);
}
