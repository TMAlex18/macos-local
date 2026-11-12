/* Self-authored CTRR RAM/cache/CPU-fault/DMA fixture. No Apple code. */
#include "runtime.h"

#define CTRR 0x30260000ull
#define CODE 0x72000000ull
#define DATA (CODE + 0x100)
#define REQ 0x72800000ull
#define STATUS 0x72800100ull
#define COPY 0x72802000ull
#define MAILBOX 0x72830000ull
#define N 64u
#define REG(i, f) (CTRR + 0x40 + (i) * 0x20 + (f))

static volatile uint64_t expected_fault;
static volatile unsigned fault_count;
static volatile unsigned calls;

static void write64(uint64_t address, uint64_t value)
{
    *(volatile uint64_t *)(uintptr_t)address = value;
}

void exception_sync(struct exception_frame *frame)
{
    uint64_t esr = READ_SYSREG(esr_el1);
    uint64_t far = READ_SYSREG(far_el1);
    unsigned ec = (esr >> 26) & 0x3f;
    if ((ec == 0x24 || ec == 0x25) && far == expected_fault &&
        (esr & (1u << 6)) && (esr & 0x3f) == 0x10) {
        ++fault_count;
        frame->elr += 4;
        return;
    }
    exception_bad(frame);
}

static void store_must_fault(uint64_t address, uint64_t value)
{
    unsigned before = fault_count;
    expected_fault = address;
    probe_store(address, value);
    expect(fault_count == before + 1, "CTRR_EXPECTED_SYNC_EXTERNAL_WRITE_ABORT");
    expected_fault = 0;
}

static void code_value(unsigned value)
{
    *(volatile uint32_t *)(uintptr_t)CODE = 0xd2800000u | (value << 5); /* MOVZ X0 */
    *(volatile uint32_t *)(uintptr_t)(CODE + 4) = 0xd65f03c0; /* RET */
    __asm__ volatile("dc cvau, %0; dsb ish; ic ivau, %0; dsb ish; isb" :: "r"(CODE) : "memory");
}

static void execute(unsigned value, unsigned count)
{
    uint64_t (*function)(void) = (void *)(uintptr_t)CODE;
    for (unsigned i = 0; i < count; ++i) {
        expect(function() == value, "CTRR_CODE_OR_SMC_STALE");
        ++calls;
    }
    field_uart("CTRR_EXEC_VALUE", value);
    field_uart("CTRR_EXEC_COUNT", count);
}

static void range(unsigned index, uint64_t lower, uint64_t upper)
{
    write64(REG(index, 0), lower);
    write64(REG(index, 8), upper);
}

struct part { uint64_t address; uint32_t length, flags; };
struct sector { uint32_t pad0, pad1, number, pad3; };
_Static_assert(sizeof(struct part) == 16, "BDIF descriptor width");

static unsigned block_io(uint64_t address, unsigned flags)
{
    volatile struct part *p = (void *)(uintptr_t)REQ;
    volatile struct sector *s = (void *)(uintptr_t)(REQ + 64);
    *s = (struct sector){0, 0, 2, 0};
    p[0] = (struct part){REQ + 64, 16, 0};
    p[1] = (struct part){address, 512, flags};
    p[2] = (struct part){STATUS, 1, 0};
    *(volatile uint8_t *)(uintptr_t)STATUS = 0xa5;
    dsb_sy();
    write64(0x30000408ull, REQ); /* owned fixture root block backend */
    dsb_sy();
    return *(volatile uint8_t *)(uintptr_t)STATUS;
}

static void check_bytes(uint64_t address, unsigned value)
{
    for (unsigned i = 0; i < 512; ++i) {
        expect(*(volatile uint8_t *)(uintptr_t)(address + i) == value,
               "CTRR_RAM_OR_DMA_BYTES_CHANGED");
    }
}

#ifndef CTRR_BASELINE
static void checkpoint(unsigned phase)
{
    write64(MAILBOX, phase);
    write64(MAILBOX + 8, 0);
    dsb_sy();
    field_uart("CTRR_HOST_CHECKPOINT", phase);
    while (*(volatile uint64_t *)(uintptr_t)(MAILBOX + 8) != phase) {
        __asm__ volatile("yield" ::: "memory");
    }
}
#endif

void main(void)
{
#ifndef CTRR_BASELINE
    if (*(volatile uint64_t *)(uintptr_t)(MAILBOX + 16) == 0xc7aa55) {
        /* Main RAM survives machine reset; all CTRR locks/mappings must not. */
        for (unsigned i = 0; i < 16; ++i) {
            expect(*(volatile uint64_t *)(uintptr_t)REG(i, 0) == 0 &&
                   *(volatile uint64_t *)(uintptr_t)REG(i, 8) == 0 &&
                   mmio32(REG(i, 16)) == 0, "CTRR_RESET_RANGE_OR_LOCK");
        }
        expect(*(volatile uint64_t *)(uintptr_t)DATA == 0xb7b7b7b7b7b7b7b7ull,
               "CTRR_RESET_LOST_SHARED_RAM");
        code_value(66); execute(66, 1);
        range(0, CODE, CODE); write32(REG(0, 16), 6); dsb_sy();
        execute(66, N); store_must_fault(DATA, 0);
        field_uart("CTRR_PHASE2_CALLS", calls);
        puts_uart("CTRR_RESET_REENABLE_SMC_PASS\nCTRR_GUEST_PASS\n");
        platform_exit(0);
    }
#endif
    puts_uart("CTRR_GUEST_BEGIN\n");
    expect(mmio32(CTRR) == 0x63747272, "CTRR_ID");
    expect(mmio32(CTRR + 4) == 1 && mmio32(CTRR + 12) == 16, "CTRR_VERSION_RANGE_COUNT");
    expect(mmio32(CTRR + 16) == 12, "CTRR_PAGE_SHIFT");
    expect(*(volatile uint64_t *)(uintptr_t)(CTRR + 0x20) == 0x70000000, "CTRR_RAM_BASE");
    for (unsigned i = 0; i < 512; ++i) {
        *(volatile uint8_t *)(uintptr_t)(DATA + i) = 0xa6;
        *(volatile uint8_t *)(uintptr_t)(COPY + i) = 0x5c;
    }
    expect(block_io(COPY, 0x10001) == 0, "CTRR_FIXTURE_DISK_PREPARE");
    code_value(11); execute(11, 1);
    range(0, CODE, CODE); write32(REG(0, 16), 6); dsb_sy();
    execute(11, N);
    store_must_fault(DATA, 0x1122334455667788ull);
    check_bytes(DATA, 0xa6);
    expect(block_io(DATA, 0x30001) == 1, "CTRR_DMA_WRITE_MUST_REPORT_ERROR");
    check_bytes(DATA, 0xa6);
    expect(block_io(DATA, 0x10001) == 0, "CTRR_PROTECTED_DMA_READ");
    expect(block_io(COPY, 0x30001) == 0, "CTRR_DMA_READBACK");
    check_bytes(COPY, 0xa6);
    puts_uart("CTRR_CPU_FAULT_UNCHANGED_AND_DMA_ERROR_PASS\n");
#ifdef CTRR_BASELINE
    field_uart("CTRR_BASELINE_CALLS", calls);
    puts_uart("CTRR_BASELINE_PASS\n"); platform_exit(0);
#else
    /* The old range/control contract still applies to a RAM-backed view. */
    range(1, CODE, CODE); store_must_fault(REG(1, 16), 6);
    range(2, CODE + 1, CODE); store_must_fault(REG(2, 16), 6);
    range(3, 0x90000000, 0x90000000); store_must_fault(REG(3, 16), 6);
    range(4, CODE + 0x4000, CODE); store_must_fault(REG(4, 16), 6);
    range(5, CODE, 0xfffffffffffff000ull); store_must_fault(REG(5, 16), 6);
    write32(REG(0, 16), 0x10); store_must_fault(DATA, 0);
    write32(REG(0, 16), 0); dsb_sy();
    code_value(22); execute(22, 1);
    range(0, CODE, CODE); write32(REG(0, 16), 6); dsb_sy(); execute(22, N);
    write32(REG(0, 16), 0); write32(REG(0, 16), 2); dsb_sy(); execute(22, N);
    code_value(33); execute(33, N);
    write32(REG(0, 16), 6); /* active permissions cannot change */
    write64(DATA, 0xa6a6a6a6a6a6a6a6ull); /* still RW, must not fault */
    write32(REG(0, 16), 0); write32(REG(0, 16), 7); dsb_sy();
    write32(REG(0, 16), 0); range(0, CODE + 0x4000, CODE + 0x4000);
    expect(*(volatile uint64_t *)(uintptr_t)REG(0, 0) == CODE, "CTRR_LOCKED_BOUNDARY_CHANGED");
    store_must_fault(DATA, 0); execute(33, N);
    puts_uart("CTRR_RANGE_LOCK_RW_SMC_PASS\n");
    checkpoint(1); /* privileged debug write, explicitly outside guest protection */
    expect(*(volatile uint64_t *)(uintptr_t)DATA == 0xb7b7b7b7b7b7b7b7ull,
           "CTRR_SHARED_ROMD_READ_AFTER_PRIVILEGED_WRITE");
    store_must_fault(DATA, 0); execute(44, N);
    puts_uart("CTRR_ROMD_SHARED_READ_AND_DEBUG_SMC_PASS\n");
    checkpoint(2); /* unrealize/re-realize, backing must remain the original RAM */
    for (unsigned i = 0; i < 16; ++i) expect(mmio32(REG(i, 16)) == 0, "CTRR_UNREALIZE_STATE");
    expect(*(volatile uint64_t *)(uintptr_t)DATA == 0xb7b7b7b7b7b7b7b7ull,
           "CTRR_UNREALIZE_BACKING_LOST");
    execute(55, 1); range(0, CODE, CODE); write32(REG(0, 16), 7); dsb_sy(); execute(55, N);
    field_uart("CTRR_PHASE1_CALLS", calls);
    write64(MAILBOX + 16, 0xc7aa55); checkpoint(3);
    fail("CTRR_RESET_DID_NOT_RESTART_GUEST");
#endif
}
