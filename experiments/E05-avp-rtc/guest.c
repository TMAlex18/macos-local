/* Self-authored EL1 checks for the native AVP RTC register contract. */
#include "runtime.h"

#define RTC 0x30240000ull
static uint64_t frequency;
static unsigned passed_cases;

static uint64_t read64(unsigned offset) {
    return *(volatile uint64_t *)(uintptr_t)(RTC + offset);
}

static void write64(unsigned offset, uint64_t value) {
    *(volatile uint64_t *)(uintptr_t)(RTC + offset) = value;
    dsb_sy();
}

static void write_rtc32(unsigned offset, uint32_t value) {
    write32(RTC + offset, value);
    dsb_sy();
}

static void wait_ticks(uint64_t interval) {
    uint64_t begin = READ_SYSREG(cntpct_el0);
    while (READ_SYSREG(cntpct_el0) - begin < interval) {}
}

static void case_pass(const char *name) {
    puts_uart("RTC_CASE_PASS ");
    puts_uart(name);
    puts_uart("\n");
    passed_cases++;
}

static void check_loaded_counter(uint64_t loaded) {
    uint64_t count = read64(0);
    expect(count >= loaded, "COUNTER_BEFORE_LAST_LOAD");
    expect(count - loaded < 1000000, "COUNTER_LOAD_NOT_MICROSECOND_BASE");
}

void main(void) {
    expect(READ_SYSREG(CurrentEL) == 4, "NOT_EL1");
    frequency = READ_SYSREG(cntfrq_el0);
    expect(frequency == 24000000, "UNEXPECTED_COUNTER_FREQUENCY");
    puts_uart("AVP_RTC_GUEST_BEGIN\n");
    field_uart("CNTFRQ", frequency);

    expect(mmio32(RTC + 0x40) == 0xf001, "RTC_VERSION_32");
    expect(read64(0x40) == 0xf001, "RTC_VERSION_64");
    expect(mmio32(RTC + 0x44) == 0, "RTC_VERSION_HIGH_LANE");
    case_pass("version-widths");

    /* The host independently compares this pre-load value with Unix time
     * immediately before and after the process. No fixed date is assumed. */
    uint64_t epoch_us = read64(0);
    field_uart("RTC_EPOCH_US", epoch_us);
    expect(epoch_us != 0, "RTC_EPOCH_ZERO");
    case_pass("unix-epoch-snapshot");

    uint64_t tick_before = READ_SYSREG(cntpct_el0);
    uint64_t before = read64(0);
    wait_ticks(frequency / 40); /* 25ms measured by an independent counter. */
    uint64_t after = read64(0);
    uint64_t tick_after = READ_SYSREG(cntpct_el0);
    uint64_t expected_us = (tick_after - tick_before) * 1000000 / frequency;
    expect(after > before, "RTC_COUNTER_DID_NOT_ADVANCE");
    uint64_t measured_us = after - before;
    uint64_t error_us = measured_us > expected_us ? measured_us - expected_us : expected_us - measured_us;
    field_uart("RTC_RATE_US", measured_us);
    field_uart("RTC_RATE_EXPECTED_US", expected_us);
    field_uart("RTC_RATE_TICKS", tick_after - tick_before);
    /* TCG and host monotonic clocks can have scheduling skew. This bound
     * still decisively distinguishes microseconds from ns/ms/seconds. */
    expect(error_us <= expected_us / 10 + 3000, "RTC_MICROSECOND_RATE_MISMATCH");
    case_pass("running-microsecond-rate");

    before = read64(0);
    uint32_t high_before, low, high_after;
    unsigned attempts = 0;
    do {
        high_before = mmio32(RTC + 4);
        low = mmio32(RTC);
        high_after = mmio32(RTC + 4);
        expect(++attempts <= 4, "COUNTER_LANES_NEVER_STABLE");
    } while (high_before != high_after);
    uint64_t split_count = ((uint64_t)high_after << 32) | low;
    after = read64(0);
    expect(before <= split_count && split_count <= after, "COUNTER_32BIT_LANES_MISMATCH");
    case_pass("counter-32bit-lanes");

    uint64_t loaded = 0x1234567800002000ull;
    write64(0x10, loaded);
    expect(read64(0x10) == loaded, "LOAD64_READBACK");
    check_loaded_counter(loaded);
    before = read64(0);
    wait_ticks(frequency / 500);
    after = read64(0);
    expect(after > before, "LOADED_COUNTER_DID_NOT_ADVANCE");
    expect(read64(0x10) == loaded, "LOAD_REGISTER_CHANGED_WITH_ELAPSED_TIME");
    case_pass("load64-and-counter");

    write_rtc32(0x10, 0xabcdef00u);
    loaded = 0x12345678abcdef00ull;
    expect(read64(0x10) == loaded, "LOAD_LOW_WRITE_DAMAGED_HIGH_LANE");
    expect(mmio32(RTC + 0x10) == 0xabcdef00u, "LOAD_LOW_READBACK");
    expect(mmio32(RTC + 0x14) == 0x12345678u, "LOAD_HIGH_READBACK");
    check_loaded_counter(loaded);
    write_rtc32(0x14, 1);
    loaded = 0x00000001abcdef00ull;
    expect(read64(0x10) == loaded, "LOAD_HIGH_WRITE_DAMAGED_LOW_LANE");
    check_loaded_counter(loaded);
    case_pass("load-32bit-lanes");

    expect(read64(0x18) == 1, "CONTROL_NOT_FIXED_ONE");
    write64(0x18, 0);
    expect(read64(0x18) == 1, "CONTROL_ZERO_WRITE_CHANGED_STATE");
    write64(0x18, UINT64_MAX);
    expect(read64(0x18) == 1, "CONTROL_ONES_WRITE_CHANGED_STATE");
    write64(0x20, UINT64_MAX);
    expect(read64(0x20) == 7, "MASK_NOT_LOW_THREE_BITS");
    expect(mmio32(RTC + 0x24) == 0, "MASK_HIGH_LANE_NONZERO");
    write_rtc32(0x24, UINT32_MAX);
    expect(read64(0x20) == 7, "MASK_HIGH_WRITE_MODIFIED_LOW_BITS");
    write_rtc32(0x20, 2);
    expect(read64(0x20) == 2, "MASK_32BIT_READBACK");
    expect(read64(0x28) == 0 && read64(0x30) == 0, "UNEXPECTED_RAW_OR_MASKED_STATE");
    write64(0x38, 7);
    expect(read64(0x38) == 0 && read64(0x28) == 0 && read64(0x30) == 0,
           "W1C_WITHOUT_EVENTS_CHANGED_STATE");
    case_pass("control-mask-idle-state");

    before = read64(0);
    write64(0, 0);
    after = read64(0);
    expect(after >= before, "COUNTER_WRITE_CHANGED_TIME");
    write64(0x40, UINT64_MAX);
    expect(read64(0x40) == 0xf001, "VERSION_WRITE_CHANGED_ID");
    const unsigned reserved[] = { 0x48, 0x3ff0, 0xfff8 };
    for (unsigned i = 0; i < sizeof(reserved) / sizeof(reserved[0]); i++) {
        expect(read64(reserved[i]) == 0, "RESERVED_READ_NONZERO");
        write64(reserved[i], 0xaabbccddeeff1020ull);
        expect(read64(reserved[i]) == 0, "RESERVED_WRITE_RETAINED");
    }
    expect(read64(0x10) == loaded && read64(0x20) == 2 && read64(0x18) == 1,
           "IGNORED_WRITES_DAMAGED_OTHER_REGISTERS");
    case_pass("readonly-and-reserved");

    write64(0x20, 7);
    write64(0x08, read64(0) + 2000);
    expect(read64(0x08) == 0, "MATCH_WRITE_MUST_NOT_BE_RETAINED");
    wait_ticks(frequency / 50); /* Advance well beyond the requested match. */
    expect(read64(0x28) == 0 && read64(0x30) == 0, "MATCH_CREATED_SYNTHETIC_ALARM_STATE");
    write_rtc32(0x38, 7);
    expect(read64(0x38) == 0 && read64(0x28) == 0 && read64(0x30) == 0,
           "IDLE_W1C_CHANGED_ALARM_STATE");
    case_pass("match-does-not-create-alarm-state");

    expect(passed_cases == 9, "WRONG_RTC_CASE_COUNT");
    field_uart("RTC_CASES", passed_cases);
    puts_uart("AVP_RTC_GUEST_PASS\n");
    platform_exit(0);
}
