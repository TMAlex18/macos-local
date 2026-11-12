/* Real QEMU state/feature encodings, synthetic inputs, aggregate output only. */
#include "qemu/osdep.h"
#include "cpu.h"
#include "internals.h"
#include <time.h>

ARMVAParameters reference_va(CPUARMState *, uint64_t, ARMMMUIdx, bool, bool);
uint64_t check_pac_consumer(CPUARMState *, uint64_t, uint64_t, ARMPACKey *,
                          ARMMMUIdx, unsigned, bool, bool, unsigned *);

static uint64_t rng = UINT64_C(0xd8ebcc6bfe34a217);
static uint64_t random64(void)
{
    uint64_t z = (rng += UINT64_C(0x9e3779b97f4a7c15));
    z = (z ^ (z >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
    z = (z ^ (z >> 27)) * UINT64_C(0x94d049bb133111eb);
    return z ^ (z >> 31);
}

static void configure(ARMCPU *cpu, unsigned s1, unsigned s2, unsigned feature)
{
    CPUARMState *env = &cpu->env;
    uint64_t mmfr0 = random64();
    mmfr0 = FIELD_DP64(mmfr0, ID_AA64MMFR0, TGRAN4,
                      s1 & 1 ? !!(feature & 8) : 15);
    mmfr0 = FIELD_DP64(mmfr0, ID_AA64MMFR0, TGRAN16,
                      s1 & 2 ? 1 + !!(feature & 8) : 0);
    mmfr0 = FIELD_DP64(mmfr0, ID_AA64MMFR0, TGRAN64, s1 & 4 ? 0 : 15);
    mmfr0 = FIELD_DP64(mmfr0, ID_AA64MMFR0, TGRAN4_2,
                      !s2 ? 0 : s2 & 1 ? 2 + !!(feature & 16) : 1);
    mmfr0 = FIELD_DP64(mmfr0, ID_AA64MMFR0, TGRAN16_2,
                      !s2 ? 0 : s2 & 2 ? 2 + !!(feature & 16) : 1);
    mmfr0 = FIELD_DP64(mmfr0, ID_AA64MMFR0, TGRAN64_2,
                      !s2 ? 0 : s2 & 4 ? 2 : 1);
    SET_IDREG(&cpu->isar, ID_AA64MMFR0, mmfr0);
    SET_IDREG(&cpu->isar, ID_AA64MMFR1, random64());
    SET_IDREG(&cpu->isar, ID_AA64MMFR2, random64());
    FIELD_DP64_IDREG(&cpu->isar, ID_AA64MMFR2, ST, !!(feature & 1));
    FIELD_DP64_IDREG(&cpu->isar, ID_AA64MMFR2, VARANGE, !!(feature & 2));
    SET_IDREG(&cpu->isar, ID_AA64MMFR3, random64());
    SET_IDREG(&cpu->isar, ID_AA64PFR1, random64());
    FIELD_DP64_IDREG(&cpu->isar, ID_AA64PFR1, MTEX, !!(feature & 4));
    SET_IDREG(&cpu->isar, ID_AA64PFR2, random64());
    env->features = (UINT64_C(1) << ARM_FEATURE_AARCH64) |
                    ((random64() & 1) << ARM_FEATURE_EL2) |
                    ((random64() & 1) << ARM_FEATURE_EL3);
    env->cp15.scr_el3 = random64();
    env->cp15.hcrx_el2 = random64();
    for (unsigned i = 0; i < ARRAY_SIZE(env->cp15.tcr_el); i++) {
        env->cp15.tcr_el[i] = random64();
    }
    for (unsigned i = 0; i < ARRAY_SIZE(env->cp15.tcr2_el); i++) {
        env->cp15.tcr2_el[i] = random64();
    }
    env->cp15.vtcr_el2 = random64();
    env->cp15.vstcr_el2 = random64();
}

static void set_tcr(ARMCPU *cpu, uint64_t tcr)
{
    for (unsigned i = 0; i < ARRAY_SIZE(cpu->env.cp15.tcr_el); i++) {
        cpu->env.cp15.tcr_el[i] = tcr;
    }
    cpu->env.cp15.vtcr_el2 = tcr;
    cpu->env.cp15.vstcr_el2 = tcr ^ (UINT64_C(1) << 14);
}

static uint64_t encoding(unsigned tsz, unsigned tg0, unsigned tg1, unsigned bits)
{
    uint64_t tcr = random64();
    tcr = deposit64(tcr, 0, 6, tsz);
    tcr = deposit64(tcr, 16, 6, 63 - tsz);
    tcr = deposit64(tcr, 14, 2, tg0);
    tcr = deposit64(tcr, 30, 2, tg1);
    tcr = deposit64(tcr, 37, 2, bits & 3);
    tcr = deposit64(tcr, 51, 2, bits >> 2);
    return tcr;
}

#ifndef PAC_VA_BENCH
static uint64_t field_checks, consumer_checks, fault_checks;

static void require(bool condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "FAIL %s at field=%" PRIu64 " consumer=%" PRIu64 "\n",
                message, field_checks, consumer_checks);
        exit(1);
    }
}

static bool full_equal(ARMVAParameters a, ARMVAParameters b)
{
    return a.tsz == b.tsz && a.ps == b.ps && a.sh == b.sh &&
           a.select == b.select && a.tbi == b.tbi && a.epd == b.epd &&
           a.hpd == b.hpd && a.tsz_oob == b.tsz_oob && a.ds == b.ds &&
           a.ha == b.ha && a.hd == b.hd && a.gran == b.gran &&
           a.pie == b.pie && a.aie == b.aie && a.mtx == b.mtx;
}

static void fields(ARMCPU *cpu, ARMMMUIdx idx, uint64_t va, bool data)
{
    ARMVAParameters reference = reference_va(&cpu->env, va, idx, data, false);
    ARMVAParameters current = aa64_va_parameters(&cpu->env, va, idx, data, false);
    ARMVAParameters pac = aa64_pauth_va_parameters(&cpu->env, va, idx, data);
    ARMVAParameters projection = { .tsz = reference.tsz, .tbi = reference.tbi,
                                  .mtx = reference.mtx };
    require(full_equal(current, reference), "full MMU fields unchanged");
    require(full_equal(pac, projection), "PAC projection equals full reference");
    if (regime_is_stage2(idx)) {
        current = aa64_va_parameters(&cpu->env, va, idx, data, true);
        reference = reference_va(&cpu->env, va, idx, data, true);
        require(full_equal(current, reference), "AArch32 EL1 stage2 full fields");
    }
    field_checks++;
}

static void consumers(ARMCPU *cpu, ARMMMUIdx idx, uint64_t va, bool data)
{
    ARMPACKey key = { .lo = random64(), .hi = random64() };
    uint64_t modifier = random64();
    unsigned a, b;
    for (unsigned qarma = 3; qarma <= 5; qarma += 2) {
        for (unsigned feature = PauthFeat_1; feature <= PauthFeat_FPACCOMBINED; feature++) {
            SET_IDREG(&cpu->isar, ID_AA64ISAR1,
                      FIELD_DP64(0, ID_AA64ISAR1, APA, qarma == 5 ? feature : 0));
            SET_IDREG(&cpu->isar, ID_AA64ISAR2,
                      FIELD_DP64(0, ID_AA64ISAR2, APA3, qarma == 3 ? feature : 0));
            uint64_t signed_old = check_pac_consumer(&cpu->env, va, modifier, &key,
                                                   idx, 0, false, data, &a);
            uint64_t signed_new = check_pac_consumer(&cpu->env, va, modifier, &key,
                                                   idx, 0, true, data, &b);
            require(signed_old == signed_new && a == b, "PAC insertion bits");
            consumer_checks++;
            for (unsigned operation = 1; operation < 4; operation++) {
                for (unsigned corrupt = 0; corrupt < 2; corrupt++) {
                    uint64_t ptr = signed_old ^ (corrupt ? UINT64_C(1) << 52 : 0);
                    uint64_t x = check_pac_consumer(&cpu->env, ptr, modifier, &key,
                                                  idx, operation, false, data, &a);
                    uint64_t y = check_pac_consumer(&cpu->env, ptr, modifier, &key,
                                                  idx, operation, true, data, &b);
                    require(x == y && a == b, "strip/auth bits and FPAC fault tuple");
                    fault_checks += a != 0;
                    consumer_checks++;
                }
            }
        }
    }
}

int main(void)
{
    ARMCPU *cpu = g_new0(ARMCPU, 1);
    static const unsigned sizes[] = {0, 11, 12, 15, 16, 38, 39, 40, 47, 48, 49, 63};
    ARMMMUIdx indices[32];
    unsigned count = 0;
    for (unsigned idx = 0; idx < ARRAY_SIZE(arm_mmuidx_table); idx++) {
        if (!(idx & ARM_MMU_IDX_M) &&
            FIELD_EX32(arm_mmuidx_table[idx], MMUIDXINFO, RELVALID)) {
            indices[count++] = idx;
        }
    }
    require(count == 23, "all A-profile translation regimes enumerated");
    for (unsigned s1 = 1; s1 < 8; s1++) {
        for (unsigned s2 = 0; s2 < 8; s2++) {
            for (unsigned feature = 0; feature < 32; feature++) {
                configure(cpu, s1, s2, feature);
                for (unsigned i = 0; i < count; i++) {
                    for (unsigned k = 0; k < ARRAY_SIZE(sizes); k++) {
                        set_tcr(cpu, encoding(sizes[k], k & 3, (k >> 1) & 3, k));
                        for (unsigned mode = 0; mode < 8; mode++) {
                            uint64_t va = random64();
                            va = deposit64(va, 55, 1, mode & 1);
                            va = deposit64(va, 63, 1, (mode >> 1) & 1);
                            fields(cpu, indices[i], va, mode & 4);
                        }
                    }
                }
            }
        }
    }
    /* All raw T0SZ/T1SZ and TG0/TG1 encodings, all TBI/TBID combinations. */
    for (unsigned tsz = 0; tsz < 64; tsz++) {
        for (unsigned gran = 0; gran < 16; gran++) {
            for (unsigned tag = 0; tag < 16; tag++) {
                configure(cpu, 7, 0, tag * 2);
                set_tcr(cpu, encoding(tsz, gran & 3, gran >> 2, tag));
                for (unsigned i = 0; i < count; i++) {
                    for (unsigned mode = 0; mode < 4; mode++) {
                        fields(cpu, indices[i], deposit64(random64(), 55, 1, mode & 1), mode & 2);
                    }
                }
            }
        }
    }
    for (unsigned trial = 0; trial < 250000; trial++) {
        configure(cpu, 1 + random64() % 7, random64() % 8, random64() & 31);
        ARMMMUIdx idx = indices[random64() % count];
        uint64_t va = random64();
        bool data = random64() & 1;
        fields(cpu, idx, va, data);
        if (trial < 4096 && !regime_is_stage2(idx)) {
            consumers(cpu, idx, va, data);
        }
    }
    printf("{\"field_cases\":%" PRIu64 ",\"pac_consumer_cases\":%" PRIu64
           ",\"fault_cases\":%" PRIu64 ",\"regimes\":%u}\n",
           field_checks, consumer_checks, fault_checks, count);
    g_free(cpu);
    return 0;
}
#else
typedef ARMVAParameters (*va_fn)(CPUARMState *, uint64_t, ARMMMUIdx, bool);
static ARMVAParameters full_for_pac(CPUARMState *env, uint64_t va, ARMMMUIdx idx, bool data)
{
    return reference_va(env, va, idx, data, false);
}

static double elapsed(struct timespec a, struct timespec b)
{
    return (b.tv_sec - a.tv_sec) + (b.tv_nsec - a.tv_nsec) * 1e-9;
}

static void bench(ARMCPU *cpu, unsigned round, const char *name, va_fn fn, unsigned profile)
{
    struct timespec start, end, cs, ce;
    uint64_t digest = 0;
    uint64_t addresses[256];
    for (unsigned i = 0; i < ARRAY_SIZE(addresses); i++) {
        addresses[i] = UINT64_C(0x0000000100000000) + i * 4096;
        if (i & 1) {
            addresses[i] |= UINT64_C(0xfffffe0000000000);
        }
    }
    ARMMMUIdx idx = profile == 0 ? ARMMMUIdx_Stage1_E1 :
                    profile == 1 ? ARMMMUIdx_E2 : ARMMMUIdx_E3;
    clock_gettime(CLOCK_MONOTONIC, &start);
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &cs);
    for (unsigned i = 0; i < 20000000; i++) {
        ARMVAParameters value = fn(&cpu->env, addresses[i & 255], idx, i & 1);
        digest += value.tsz + (value.tbi << 8) + (value.mtx << 9);
    }
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ce);
    clock_gettime(CLOCK_MONOTONIC, &end);
    printf("{\"round\":%u,\"variant\":\"%s\",\"profile\":%u,\"calls\":20000000,"
           "\"wall_seconds\":%.9f,\"thread_cpu_seconds\":%.9f,\"digest\":%" PRIu64 "}\n",
           round, name, profile, elapsed(start, end), elapsed(cs, ce), digest);
}

int main(void)
{
    ARMCPU *cpu = g_new0(ARMCPU, 1);
    configure(cpu, 7, 0, 31);
    cpu->env.features = (UINT64_C(1) << ARM_FEATURE_AARCH64) |
                        (UINT64_C(1) << ARM_FEATURE_EL2);
    /* 16 KiB lower/upper ranges, 48-bit VAs, TBI enabled, TBID/MTX disabled. */
    set_tcr(cpu, UINT64_C(16) | (UINT64_C(16) << 16) | (UINT64_C(2) << 14) |
            (UINT64_C(1) << 30) | (UINT64_C(3) << 37));
    for (unsigned profile = 0; profile < 3; profile++) {
        for (unsigned round = 0; round < 5; round++) {
            if (round & 1) {
                bench(cpu, round, "projection", aa64_pauth_va_parameters, profile);
                bench(cpu, round, "full", full_for_pac, profile);
            } else {
                bench(cpu, round, "full", full_for_pac, profile);
                bench(cpu, round, "projection", aa64_pauth_va_parameters, profile);
            }
        }
    }
    g_free(cpu);
    return 0;
}
#endif
