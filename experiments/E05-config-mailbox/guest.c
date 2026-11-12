/* Self-authored EL1 guest: exercise the real VMApple configuration mailbox. */
#include "runtime.h"

#define CFG_BASE 0x00400000ull
#define SLOT 0x004ffff8ull
#define RAM_END 0x90000000ull
#define DESC_PA 0x72800000ull
#define ARG_PA 0x72000000ull
#define ARG_SIZE 64u
#define COMPLETE_HEADER 0x01000001u

struct request { uint32_t header, operation; uint64_t argument, capacity; };
_Static_assert(sizeof(struct request) == 24, "native request size");
_Static_assert(offsetof(struct request, operation) == 4, "native operation offset");
_Static_assert(offsetof(struct request, argument) == 8, "native argument offset");
_Static_assert(offsetof(struct request, capacity) == 16, "native capacity offset");

static unsigned passed_cases;
static volatile struct request *const request = (void *)(uintptr_t)DESC_PA;

static uint64_t load64(uint64_t address) {
    return *(volatile uint64_t *)(uintptr_t)address;
}

static void store64(uint64_t address, uint64_t value) {
    *(volatile uint64_t *)(uintptr_t)address = value;
}

static unsigned char pattern(unsigned index) {
    return (unsigned char)(0x39u + index * 19u);
}

static void fill_argument_bytes(unsigned length) {
    volatile unsigned char *p = (void *)(uintptr_t)(ARG_PA - 16);
    for (unsigned i = 0; i < length + 32; i++) p[i] = pattern(i);
}

static void check_argument_bytes(unsigned length) {
    volatile unsigned char *p = (void *)(uintptr_t)(ARG_PA - 16);
    for (unsigned i = 0; i < length + 32; i++)
        expect(p[i] == pattern(i), "ARGUMENT_OR_GUARD_MODIFIED");
}

static void set_guards(void) {
    store64(DESC_PA - 16, 0x1020304050607080ull);
    store64(DESC_PA - 8, 0x8172635445362718ull);
    store64(DESC_PA + 24, 0x1122334455667788ull);
    store64(DESC_PA + 32, 0x8877665544332211ull);
}

static void check_guards(void) {
    expect(load64(DESC_PA - 16) == 0x1020304050607080ull, "DESCRIPTOR_PREFIX_GUARD_0");
    expect(load64(DESC_PA - 8) == 0x8172635445362718ull, "DESCRIPTOR_PREFIX_GUARD_1");
    expect(load64(DESC_PA + 24) == 0x1122334455667788ull, "DESCRIPTOR_SUFFIX_GUARD_0");
    expect(load64(DESC_PA + 32) == 0x8877665544332211ull, "DESCRIPTOR_SUFFIX_GUARD_1");
}

static struct request prepare(unsigned version, unsigned type, unsigned operation,
                              uint64_t argument, uint64_t capacity) {
    struct request expected = { version | (type << 16), operation, argument, capacity };
    set_guards();
    fill_argument_bytes(ARG_SIZE);
    request->header = expected.header;
    request->operation = expected.operation;
    request->argument = expected.argument;
    request->capacity = expected.capacity;
    dsb_sy();
    return expected;
}

static void submit(uint64_t descriptor) {
    dsb_sy();
    store64(SLOT, descriptor);
    dsb_sy();
    /* Native host reads return zero, never the last submitted pointer. */
    expect(load64(SLOT) == 0, "SLOT_READ_MUST_BE_ZERO");
}

static void check_unchanged(struct request expected) {
    expect(request->header == expected.header, "INVALID_REQUEST_HEADER_CHANGED");
    expect(request->operation == expected.operation, "INVALID_REQUEST_OPERATION_CHANGED");
    expect(request->argument == expected.argument, "INVALID_REQUEST_ARGUMENT_CHANGED");
    expect(request->capacity == expected.capacity, "INVALID_REQUEST_CAPACITY_CHANGED");
    check_guards();
    check_argument_bytes(ARG_SIZE);
}

static void case_pass(const char *name) {
    puts_uart("MAILBOX_CASE_PASS ");
    puts_uart(name);
    puts_uart("\n");
    passed_cases++;
}

static void completed_error(unsigned type, unsigned operation, uint64_t status, unsigned length,
                            const char *name) {
    (void)prepare(1, type, operation, ARG_PA, length);
    fill_argument_bytes(length);
    submit(DESC_PA);
    expect(request->header == COMPLETE_HEADER, "MISSING_NATIVE_COMPLETION_HEADER");
    expect(request->operation == 0, "COMPLETION_OPERATION_NOT_CLEARED");
    expect(request->argument == status, "WRONG_NATIVE_ERROR_STATUS");
    expect(request->capacity == 0, "ERROR_COMPLETION_NONZERO_SIZE");
    check_guards();
    check_argument_bytes(length);
    case_pass(name);
}

static void rejected_request(unsigned version, unsigned type, uint64_t argument,
                             uint64_t capacity, const char *name) {
    struct request expected = prepare(version, type, 2, argument, capacity);
    submit(DESC_PA);
    check_unchanged(expected);
    case_pass(name);
}

static void ram_tail_fill(void) {
    volatile unsigned char *tail = (void *)(uintptr_t)(RAM_END - 32);
    for (unsigned i = 0; i < 32; i++) tail[i] = (unsigned char)(0xa0 + i);
}

static void ram_tail_check(void) {
    volatile unsigned char *tail = (void *)(uintptr_t)(RAM_END - 32);
    for (unsigned i = 0; i < 32; i++)
        expect(tail[i] == (unsigned char)(0xa0 + i), "RAM_BOUNDARY_PREFIX_MODIFIED");
}

void main(void) {
    expect(READ_SYSREG(CurrentEL) == 4, "NOT_EL1");
    puts_uart("CONFIG_MAILBOX_GUEST_BEGIN\n");
    expect(mmio32(CFG_BASE) == 2, "CFG_VERSION_BEFORE");
    expect(mmio32(CFG_BASE + 4) == 1, "CFG_CPU_COUNT_BEFORE");
    expect(load64(CFG_BASE + 0x20) == 512ull * 1024 * 1024, "CFG_RAM_SIZE_BEFORE");
    expect(load64(SLOT) == 0, "SLOT_INITIAL_READ_MUST_BE_ZERO");

    /* Match the original Stage2 operation-2 capacity, not only a tiny DMA. */
    completed_error(1, 2, 45, 5 * 1024 * 1024, "op2-type1-enotsup");
    completed_error(2, 2, 45, ARG_SIZE, "op2-type2-enotsup");
    completed_error(3, 2, 45, ARG_SIZE, "op2-type3-enotsup");
    completed_error(1, 1, 2, ARG_SIZE, "op1-type1-missing-identity");
    completed_error(2, 1, 22, ARG_SIZE, "op1-wrong-type-einval");
    completed_error(2, 3, 22, ARG_SIZE, "op3-wrong-type-einval");

    rejected_request(2, 1, ARG_PA, ARG_SIZE, "invalid-version");
    /* Both endpoints matter: type must be in the closed interval 1..3. */
    struct request expected = prepare(1, 0, 2, ARG_PA, ARG_SIZE);
    submit(DESC_PA);
    check_unchanged(expected);
    expected = prepare(1, 4, 2, ARG_PA, ARG_SIZE);
    submit(DESC_PA);
    check_unchanged(expected);
    case_pass("invalid-types");

    rejected_request(1, 1, 0xa0000000ull, ARG_SIZE, "unmapped-argument");

    ram_tail_fill();
    expected = prepare(1, 1, 2, RAM_END - 16, 32);
    submit(DESC_PA);
    check_unchanged(expected);
    ram_tail_check();
    case_pass("argument-crosses-ram-end");

    rejected_request(1, 1, ARG_PA, UINT64_MAX, "argument-length-overflow");

    expected = prepare(1, 1, 2, ARG_PA, ARG_SIZE);
    /* Submit an unmapped guest pointer; no load/store is made through it. */
    submit(0xa0000000ull);
    check_unchanged(expected);
    case_pass("unmapped-descriptor");

    expected = prepare(1, 1, 2, ARG_PA, ARG_SIZE);
    ram_tail_fill();
    /* Only sixteen descriptor bytes exist before RAM_END. Never touch the
     * missing bytes in guest code. The device must reject the whole range. */
    store64(RAM_END - 16, 0x0000000200010001ull);
    store64(RAM_END - 8, ARG_PA);
    submit(RAM_END - 16);
    expect(load64(RAM_END - 16) == 0x0000000200010001ull, "PARTIAL_DESCRIPTOR_HEADER_CHANGED");
    expect(load64(RAM_END - 8) == ARG_PA, "PARTIAL_DESCRIPTOR_ARGUMENT_CHANGED");
    volatile unsigned char *tail = (void *)(uintptr_t)(RAM_END - 32);
    for (unsigned i = 0; i < 16; i++)
        expect(tail[i] == (unsigned char)(0xa0 + i), "PARTIAL_DESCRIPTOR_PREFIX_CHANGED");
    check_unchanged(expected);
    case_pass("descriptor-crosses-ram-end");

    expect(mmio32(CFG_BASE) == 2, "CFG_VERSION_AFTER");
    expect(mmio32(CFG_BASE + 4) == 1, "CFG_CPU_COUNT_AFTER");
    expect(load64(CFG_BASE + 0x20) == 512ull * 1024 * 1024, "CFG_RAM_SIZE_AFTER");
    case_pass("existing-cfg-fields");

    expect(passed_cases == 14, "WRONG_CASE_COUNT");
    field_uart("MAILBOX_CASES", passed_cases);
    puts_uart("CONFIG_MAILBOX_GUEST_PASS\n");
    platform_exit(0);
}
