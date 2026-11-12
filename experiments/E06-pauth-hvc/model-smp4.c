#include "runtime.h"

#ifndef CPUS
#define CPUS 4
#endif
_Static_assert(CPUS == 2 || CPUS == 4 || CPUS == 8, "fixture supports 2/4/8 CPUs");
#define GENERATIONS 2
#define RAW UINT64_C(0x70012340)
#define MODIFIER UINT64_C(0x771921)

typedef uint64_t (*pac_op)(uint64_t, uint64_t);
extern uint64_t do_pacia(uint64_t, uint64_t), do_pacib(uint64_t, uint64_t);
extern uint64_t do_pacda(uint64_t, uint64_t), do_pacdb(uint64_t, uint64_t);
extern uint64_t do_autia(uint64_t, uint64_t), do_autib(uint64_t, uint64_t);
extern uint64_t do_autda(uint64_t, uint64_t), do_autdb(uint64_t, uint64_t);
extern uint64_t do_pacga(uint64_t, uint64_t);
extern uint64_t enter_el0_sign(uint64_t, uint64_t), enter_el0_hvc(void);
extern char el0_sign_return[], el0_hvc_return[], el0_hvc_instruction[];
extern char do_autia_instruction[], pac_smp_secondary_entry[];

struct result { uint64_t x[5]; };
struct keys { uint64_t v[10]; };
struct cpu_result {
    uint64_t stage, done, generation, mpidr, el1_sp, frame, el0_sp;
    uint64_t preinit_checks, cpu_on_successes, cpu_off_confirmed;
    volatile uint64_t svc_count, hvc_denied, pac_faults, expect_fault;
    uint64_t defaults[4], initial_signature, expected_user[5];
    volatile uint64_t actual_user[5];
    struct keys initial, kernel, user;
} __attribute__((aligned(128)));
static struct cpu_result states[CPUS];
static uint64_t released_phase;

static unsigned cpu_id(void)
{
    return READ_SYSREG(MPIDR_EL1) & 0xff;
}

static void check(int condition, const char *reason)
{
    if (!condition) {
        field_uart("FAIL_CPU", cpu_id());
        fail(reason);
    }
}

static uint64_t el1_top(unsigned cpu)
{
    return cpu ? UINT64_C(0x70d00000) - ((uint64_t)cpu << 16) : UINT64_C(0x71000000);
}

static uint64_t el0_top(unsigned cpu)
{
    return UINT64_C(0x70e00000) - ((uint64_t)cpu << 16);
}

static struct result hvc(uint64_t id, uint64_t a, uint64_t b, uint64_t c)
{
    register uint64_t x0 __asm__("x0") = id, x1 __asm__("x1") = a;
    register uint64_t x2 __asm__("x2") = b, x3 __asm__("x3") = c;
    register uint64_t x4 __asm__("x4") = 0x5555;
    __asm__ volatile("hvc #0" : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3), "+r"(x4) :: "memory");
    return (struct result){{x0, x1, x2, x3, x4}};
}

static void call(unsigned function, uint64_t a, uint64_t b)
{
    check(hvc(0xc1000000u + function, a, b, 0).x[0] == 0, "HVC_NOT_SUCCESS");
}

static struct keys read_keys(void)
{
    return (struct keys){{READ_SYSREG(APIAKeyLo_EL1), READ_SYSREG(APIAKeyHi_EL1),
        READ_SYSREG(APDAKeyLo_EL1), READ_SYSREG(APDAKeyHi_EL1),
        READ_SYSREG(APIBKeyLo_EL1), READ_SYSREG(APIBKeyHi_EL1),
        READ_SYSREG(APDBKeyLo_EL1), READ_SYSREG(APDBKeyHi_EL1),
        READ_SYSREG(APGAKeyLo_EL1), READ_SYSREG(APGAKeyHi_EL1)}};
}

static int same(const struct keys *a, const struct keys *b, unsigned first, unsigned count)
{
    for (unsigned i = first; i < first + count; i++) {
        if (a->v[i] != b->v[i]) {
            return 0;
        }
    }
    return 1;
}

static void expect_keys(const struct keys *expected, const char *reason)
{
    struct keys observed = read_keys();
    check(same(&observed, expected, 0, 10), reason);
}

static void wait_value(uint64_t *address, uint64_t value, const char *reason)
{
    uint64_t start = READ_SYSREG(CNTVCT_EL0), limit = READ_SYSREG(CNTFRQ_EL0) * 10;
    while (__atomic_load_n(address, __ATOMIC_ACQUIRE) < value) {
        check(READ_SYSREG(CNTVCT_EL0) - start < limit, reason);
        __asm__ volatile("yield");
    }
}

static void barrier(unsigned generation, unsigned phase)
{
    uint64_t value = generation * 16 + phase;
    __atomic_store_n(&states[cpu_id()].stage, value, __ATOMIC_RELEASE);
    if (cpu_id() == 0) {
        for (unsigned cpu = 1; cpu < CPUS; cpu++) {
            wait_value(&states[cpu].stage, value, "ALL_CPU_BARRIER_TIMEOUT");
        }
        __atomic_store_n(&released_phase, value, __ATOMIC_RELEASE);
    } else {
        wait_value(&released_phase, value, "PRIMARY_BARRIER_TIMEOUT");
    }
}

void exception_sync(struct exception_frame *f)
{
    unsigned cpu = cpu_id();
    check(cpu < CPUS, "EXCEPTION_CPU_RANGE");
    struct cpu_result *s = &states[cpu];
    uint64_t address = (uintptr_t)f, top = el1_top(cpu);
    check(address >= top - 0x10000 && address + sizeof(*f) <= top, "EXCEPTION_STACK_NOT_PER_CPU");
    s->frame = address;
    uint64_t esr = READ_SYSREG(ESR_EL1), ec = esr >> 26;
    if (ec == 0x15 && (esr & 0xffff) == 0x66) {
        check((f->spsr & 15) == 0, "SVC_NOT_FROM_EL0");
        check(f->x[6] == el0_top(cpu) - 16 && f->x[7] == RAW && f->x[8] == MODIFIER,
              "EL0_STACK_PROBE_NOT_PER_CPU");
        s->el0_sp = f->x[6];
        s->actual_user[0] = f->x[0];
        for (unsigned i = 1; i < 5; i++) {
            s->actual_user[i] = f->x[i + 1];
        }
        s->svc_count++;
        f->elr = (uintptr_t)el0_sign_return;
        f->spsr = 0x3c5;
        return;
    }
    if (ec == 0 && f->elr == (uintptr_t)el0_hvc_instruction && (f->spsr & 15) == 0) {
        s->hvc_denied++;
        f->elr = (uintptr_t)el0_hvc_return;
        f->spsr = 0x3c5;
        f->x[0] = 1;
        return;
    }
    if (s->expect_fault && ec == 0x1c && f->elr == (uintptr_t)do_autia_instruction) {
        check((f->spsr & 15) == 5, "PAC_FAILURE_NOT_FROM_EL1");
        s->pac_faults++;
        f->elr += 4;
        return;
    }
    exception_bad(f);
}

static void reject_signature(struct cpu_result *s, uint64_t signature, uint64_t modifier)
{
    uint64_t previous = s->pac_faults;
    s->expect_fault = 1;
    uint64_t bad = do_autia(signature, modifier);
    s->expect_fault = 0;
    check(s->pac_faults > previous || bad != RAW, "BAD_SIGNATURE_ACCEPTED");
}

static void run_cpu(unsigned generation)
{
    unsigned cpu = cpu_id();
    check(cpu < CPUS, "CPU_RANGE");
    struct cpu_result *s = &states[cpu];
    s->generation = generation;
    s->mpidr = READ_SYSREG(MPIDR_EL1);
    check((s->mpidr & UINT64_C(0xff00ffffff)) == cpu, "UNEXPECTED_MPIDR_TOPOLOGY");
    check(READ_SYSREG(CurrentEL) == 4, "NOT_RUNNING_AT_EL1");
    __asm__ volatile("mov %0,sp" : "=r"(s->el1_sp));
    check(s->el1_sp <= el1_top(cpu) && s->el1_sp > el1_top(cpu) - 0x10000, "EL1_STACK_NOT_PER_CPU");
    volatile uint64_t *guard1 = (void *)(uintptr_t)(el1_top(cpu) - 0x10000);
    volatile uint64_t *guard0 = (void *)(uintptr_t)(el0_top(cpu) - 0x10000);
    uint64_t guard = UINT64_C(0x5041435300000000) | (generation << 8) | cpu;
    *guard1 = guard;
    *guard0 = ~guard;
    check(((READ_SYSREG(ID_AA64ISAR1_EL1) >> 4) & 15) != 0, "REQUIRES_QARMA5");
    WRITE_SYSREG(TCR_EL1, 16ull | (16ull << 16) | (1ull << 23) | (2ull << 14));
    WRITE_SYSREG(SCTLR_EL1, READ_SYSREG(SCTLR_EL1) | (1ull << 31) | (1ull << 30) |
                 (1ull << 27) | (1ull << 13));
    isb();
    if (cpu || generation == 1) {
        check(hvc(0xc1000001, 0, 0, 0).x[0] == (uint64_t)-3, "CPU_ON_DID_NOT_CLEAR_HVC_INITIALIZATION");
        s->preinit_checks++;
    }
    call(0, 0, 0);
    struct result defaults = hvc(0xc1000001, 0, 0, 0);
    check(defaults.x[0] == 0, "DEFAULTS_NOT_AVAILABLE");
    for (unsigned i = 0; i < 4; i++) {
        s->defaults[i] = defaults.x[i + 1];
    }
    s->initial = read_keys();
    s->initial_signature = do_pacia(RAW, MODIFIER);
    barrier(generation, 1);
    expect_keys(&s->initial, "PEER_INITIALIZATION_CHANGED_KEYS");
    if (cpu == 0) {
        for (unsigned other = 1; other < CPUS; other++) {
            check(same(&s->initial, &states[other].initial, 0, 10), "INITIAL_KEYS_DIFFER_ACROSS_CPUS");
            check(s->initial_signature == states[other].initial_signature, "INITIAL_SIGNATURES_DIFFER");
            for (unsigned i = 0; i < 4; i++) {
                check(s->defaults[i] == states[other].defaults[i], "INITIAL_DEFAULTS_DIFFER");
            }
        }
    }
    uint64_t salt = ((uint64_t)generation << 32) | ((cpu + 1) * UINT64_C(0x1234567));
    call(2, defaults.x[1] ^ salt, 0);
    call(3, defaults.x[2] ^ (salt << 1), 0);
    call(6, defaults.x[4] ^ (salt << 2), 0);
    s->kernel = read_keys();
    for (unsigned role = 0; role < 5; role++) {
        check(!same(&s->initial, &s->kernel, role * 2, 2), "PER_CPU_REKEY_DID_NOT_CHANGE_EACH_KEY");
    }
    reject_signature(s, s->initial_signature, MODIFIER);
    barrier(generation, 2);
    expect_keys(&s->kernel, "CONCURRENT_PEER_REKEY_CHANGED_KERNEL_KEYS");
    pac_op signs[] = {do_pacia, do_pacib, do_pacda, do_pacdb, do_pacga};
    pac_op auths[] = {do_autia, do_autib, do_autda, do_autdb};
    for (unsigned i = 0; i < 4; i++) {
        uint64_t signature = signs[i](RAW, MODIFIER);
        check(signature != RAW && auths[i](signature, MODIFIER) == RAW, "PAC_AUTH_PAIR_FAILED");
    }
    reject_signature(s, do_pacia(RAW, MODIFIER), MODIFIER ^ 0x100);
    if (cpu == 0) {
        for (unsigned a = 0; a < CPUS; a++) {
            for (unsigned b = a + 1; b < CPUS; b++) {
                for (unsigned role = 0; role < 5; role++) {
                    check(!same(&states[a].kernel, &states[b].kernel, role * 2, 2),
                          "UNIQUE_CPU_KEYS_NOT_ISOLATED");
                }
            }
        }
    }
    uint64_t diversifier = defaults.x[3] ^ salt ^ UINT64_C(0x8776655443322110);
    call(4, diversifier, 0);
    expect_keys(&s->kernel, "EL0_DIVERSIFIER_MUTATED_EL1_KEYS");
    call(5, 1, diversifier);
    s->user = read_keys();
    check(!same(&s->user, &s->kernel, 0, 8) && same(&s->user, &s->kernel, 8, 2), "USER_BANK_SCOPE_WRONG");
    barrier(generation, 3);
    expect_keys(&s->user, "CONCURRENT_USER_BANKS_NOT_ISOLATED");
    for (unsigned i = 0; i < 5; i++) {
        s->expected_user[i] = signs[i](RAW, MODIFIER);
    }
    barrier(generation, 4);
    call(5, 0, diversifier);
    expect_keys(&s->kernel, "OVERRIDE_DID_NOT_RESTORE_EL1");
    enter_el0_sign(RAW, MODIFIER);
    check(READ_SYSREG(CurrentEL) == 4 && s->svc_count == generation, "CPU_DID_NOT_RETURN_FROM_REAL_EL0_SVC");
    for (unsigned i = 0; i < 5; i++) {
        check(s->actual_user[i] == s->expected_user[i], "EL0_USED_ANOTHER_CPU_KEY_BANK");
    }
    expect_keys(&s->kernel, "EL0_TO_EL1_DID_NOT_RESTORE_KERNEL_KEYS");
    check(enter_el0_hvc() == 1 && s->hvc_denied == generation, "EL0_HVC_NOT_REJECTED_ON_THIS_CPU");
    barrier(generation, 5);
    expect_keys(&s->kernel, "PEER_EL0_TRANSITIONS_MUTATED_KERNEL_KEYS");
    check(*guard1 == guard && *guard0 == ~guard, "PER_CPU_STACK_GUARD_CORRUPTED");
    __atomic_store_n(&s->done, generation, __ATOMIC_RELEASE);
}

void pac_smp_secondary_main(uint64_t generation)
{
    run_cpu(generation);
    hvc(0x84000002, 0, 0, 0);
    check(0, "CPU_OFF_RETURNED");
}

void main(void)
{
    puts_uart("E06 SMP PAC HVC EL0 BEGIN\n");
    field_uart("CONFIGURED_CPUS", CPUS);
    check(cpu_id() == 0, "PRIMARY_CPU_NOT_ZERO");
    check(hvc(0x84000000, 0, 0, 0).x[0] == 0x10001, "PSCI_VERSION_REGRESSION");
    for (unsigned generation = 1; generation <= GENERATIONS; generation++) {
        for (unsigned cpu = 1; cpu < CPUS; cpu++) {
            check(hvc(0xc4000004, cpu, 0, 0).x[0] == 1, "CPU_NOT_OFF_BEFORE_ON");
            check(hvc(0xc4000003, cpu, (uintptr_t)pac_smp_secondary_entry, generation).x[0] == 0,
                  "PSCI_CPU_ON_FAILED");
            states[cpu].cpu_on_successes++;
        }
        run_cpu(generation);
        for (unsigned cpu = 1; cpu < CPUS; cpu++) {
            wait_value(&states[cpu].done, generation, "SECONDARY_DID_NOT_EXECUTE");
            uint64_t start = READ_SYSREG(CNTVCT_EL0), limit = READ_SYSREG(CNTFRQ_EL0) * 10;
            while (hvc(0xc4000004, cpu, 0, 0).x[0] != 1) {
                check(READ_SYSREG(CNTVCT_EL0) - start < limit, "CPU_OFF_NOT_OBSERVED");
                __asm__ volatile("yield");
            }
            states[cpu].cpu_off_confirmed++;
        }
        /* Only CPU0 writes the success log, after all per-CPU records settle. */
        for (unsigned cpu = 0; cpu < CPUS; cpu++) {
            struct cpu_result *s = &states[cpu];
            check(s->generation == generation && s->svc_count == generation &&
                  s->hvc_denied == generation && s->done == generation, "MISSING_CPU_EXECUTION");
            check(s->preinit_checks == (cpu ? generation : 1), "MISSING_PREINIT_LIFECYCLE_CHECK");
            field_uart("CPU", cpu);
            field_uart("GENERATION", generation);
            field_uart("MPIDR", s->mpidr);
            field_uart("EL1_SP", s->el1_sp);
            field_uart("EXCEPTION_FRAME", s->frame);
            field_uart("EL0_SP", s->el0_sp);
            field_uart("SVC_COUNT", s->svc_count);
            field_uart("EL0_HVC_DENIED", s->hvc_denied);
            field_uart("PREINIT_CHECKS", s->preinit_checks);
            field_uart("CPU_ON_SUCCESSES", s->cpu_on_successes);
            field_uart("CPU_OFF_CONFIRMED", s->cpu_off_confirmed);
            field_uart("NEGATIVE_PAC_FAULTS", s->pac_faults);
            puts_uart("CPU_GENERATION_PASS\n");
        }
    }
    puts_uart("E06 SMP PSCI PAC HVC EL0 ISOLATION PASS\n");
    platform_exit(0);
}
