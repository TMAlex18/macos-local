/* Appended after the pinned scalar oracle and source-extracted candidate.
 * All inputs here are synthetic. Only aggregate counts and case names print.
 */
static uint64_t checks, hit_checks, miss_checks;
static unsigned configured_entries = ARM_PAUTH_MEMO_DEFAULT_ENTRIES;
static uint64_t aligned_hits[3], aligned_misses[3];
static uint64_t wide_hits[3], wide_misses[3];
static uint64_t rng_state = UINT64_C(0x9e3779b97f4a7c15);

static void require(bool ok, const char *name)
{
    if (!ok) {
        fprintf(stderr, "FAIL: %s\n", name);
        exit(1);
    }
}

static uint64_t random64(void)
{
    uint64_t z = (rng_state += UINT64_C(0x9e3779b97f4a7c15));
    z = (z ^ (z >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
    z = (z ^ (z >> 27)) * UINT64_C(0x94d049bb133111eb);
    return z ^ (z >> 31);
}

static void check(CPUARMState *env, uint64_t data, uint64_t modifier,
                  ARMPACKey key, unsigned expected_calls)
{
    uint64_t expected = env->test_algorithm == 0 ?
        qemu_xxhash64_4(data, modifier, key.lo, key.hi) :
        old_pauth_computepac_architected(data, modifier, key,
                                        env->test_algorithm == 3);
    uint64_t before = slow_calls;
    uint64_t hits_before = env->pauth_memo_hits;
    uint64_t misses_before = env->pauth_memo_misses;
    uint64_t actual = pauth_computepac(env, data, modifier, key);
    require(actual == expected, "complete scalar ComputePAC equivalence");
    require(slow_calls - before == expected_calls, "expected hit/miss path");
    bool counted = env->prop_pauth_memo && env->prop_pauth_memo_stats &&
                   env->test_algorithm != 0;
    require(env->pauth_memo_hits - hits_before == (counted && !expected_calls),
            "aggregate hit count matches independent slow-path oracle");
    require(env->pauth_memo_misses - misses_before == (counted && expected_calls),
            "aggregate miss count matches independent slow-path oracle");
    checks++;
    if (expected_calls) {
        miss_checks++;
    } else {
        hit_checks++;
    }
}

static CPUARMState fresh(unsigned algorithm, bool enabled)
{
    CPUARMState env = { 0 };
    env.test_algorithm = algorithm;
    env.prop_pauth_memo = enabled;
    env.prop_pauth_memo_entries = configured_entries;
    env.prop_pauth_memo_stats = true;
    return env;
}

static void run_capacity(unsigned entries)
{
    configured_entries = entries;
    /* Upstream vector stores high half before low half. */
    ARMPACKey key = { .lo = known[3], .hi = known[2] };
    CPUARMState env = fresh(5, true);
    require(old_pauth_computepac_architected(known[0], known[1], key, false)
            == known[4], "upstream full known answer");
    check(&env, known[0], known[1], key, 1);
    check(&env, known[0], known[1], key, 0);

    /* The same tuple must not reuse QARMA5 as QARMA3 (or vice versa). */
    env.test_algorithm = 3;
    check(&env, known[0], known[1], key, 1);
    check(&env, known[0], known[1], key, 0);
    env.test_algorithm = 5;
    check(&env, known[0], known[1], key, 1);

    for (unsigned algorithm = 3; algorithm <= 5; algorithm += 2) {
        for (unsigned field = 0; field < 4; field++) {
            for (unsigned bit = 0; bit < 64; bit++) {
                uint64_t values[4] = { known[0], known[1], key.lo, key.hi };
                env = fresh(algorithm, true);
                check(&env, values[0], values[1], key, 1);
                values[field] ^= UINT64_C(1) << bit;
                ARMPACKey changed = { values[2], values[3] };
                check(&env, values[0], values[1], changed, 1);
                check(&env, values[0], values[1], changed, 0);
            }
        }
        /* Bit zero deliberately does not enter the index: force eviction. */
        env = fresh(algorithm, true);
        require(pauth_memo_slot(known[0], known[1], entries) ==
                pauth_memo_slot(known[0] ^ 1, known[1], entries), "forced collision");
        check(&env, known[0], known[1], key, 1);
        check(&env, known[0] ^ 1, known[1], key, 1);
        check(&env, known[0], known[1], key, 1);
        check(&env, known[0], known[1], key, 0);

        env = fresh(algorithm, true);
        for (unsigned i = 0; i < entries; i++) {
            check(&env, (uint64_t)i << 2, 0, key, 1);
        }
        for (unsigned i = 0; i < entries; i++) {
            check(&env, (uint64_t)i << 2, 0, key, 0);
        }
        memset(env.pauth_memo, 0, sizeof(env.pauth_memo));
        for (unsigned i = 0; i < entries; i++) {
            require(!env.pauth_memo[i].valid, "all active slots cleared");
        }
        check(&env, 0, 0, key, 1);
        check(&env, (uint64_t)(entries - 1) << 2, 0, key, 1);

        /* Realistic aligned strides expose the low-bit index collision.
         * Complete scalar results still have to match in all capacities.
         */
        for (unsigned stream = 0; stream < 2; stream++) {
            env = fresh(algorithm, true);
            for (unsigned pass = 0; pass < 2; pass++) {
                for (unsigned i = 0; i < 16; i++) {
                    uint64_t data = UINT64_C(0x1000) + (stream ? 0 : i * 0x100);
                    uint64_t modifier = UINT64_C(0x2000) + (stream ? i * 0x400 : 0);
                    check(&env, data, modifier, key, entries == 64 || pass == 0);
                }
            }
            unsigned slot = entries == 64 ? 0 : entries == 1024 ? 1 : 2;
            aligned_hits[slot] += env.pauth_memo_hits;
            aligned_misses[slot] += env.pauth_memo_misses;
        }

        /* Indices 1024 apart collide at 64/1024, remain distinct at 16384. */
        env = fresh(algorithm, true);
        for (unsigned pass = 0; pass < 2; pass++) {
            for (unsigned i = 0; i < 16; i++) {
                check(&env, UINT64_C(0x1000) + i * 0x1000, UINT64_C(0x2000),
                      key, entries < 16384 || pass == 0);
            }
        }
        unsigned slot = entries == 64 ? 0 : entries == 1024 ? 1 : 2;
        wide_hits[slot] += env.pauth_memo_hits;
        wide_misses[slot] += env.pauth_memo_misses;

        /* SplitMix is a bijection: each new data word forces a miss. */
        env = fresh(algorithm, true);
        for (unsigned i = 0; i < 100000; i++) {
            uint64_t data = random64(), modifier = random64();
            ARMPACKey random_key = { random64(), random64() };
            check(&env, data, modifier, random_key, 1);
            check(&env, data, modifier, random_key, 0);
        }
    }

    /* Per-vCPU storage: identical tuples on two CPUs still have two misses. */
    CPUARMState other = fresh(5, true);
    env = fresh(5, true);
    check(&env, known[0], known[1], key, 1);
    check(&other, known[0], known[1], key, 1);
    check(&env, known[0], known[1], key, 0);
    check(&other, known[0], known[1], key, 0);
    other.pauth_memo[pauth_memo_slot(known[0], known[1], entries)].valid = false;
    check(&env, known[0], known[1], key, 0);
    check(&other, known[0], known[1], key, 1);

    env = fresh(5, false);
    check(&env, known[0], known[1], key, 1);
    check(&env, known[0], known[1], key, 1);
    env.test_algorithm = 0;
    env.prop_pauth_memo = true;
    check(&env, known[0], known[1], key, 1);
    check(&env, known[0], known[1], key, 1);
    for (unsigned i = 0; i < entries; i++) {
        require(!env.pauth_memo[i].valid, "disabled/impdef leaves cache empty");
    }
    /* Instrumentation stays off unless explicitly selected. */
    env = fresh(5, true);
    env.prop_pauth_memo_stats = false;
    check(&env, known[0], known[1], key, 1);
    check(&env, known[0], known[1], key, 0);
    require(env.pauth_memo_hits == 0 && env.pauth_memo_misses == 0,
            "statistics disabled without disabling memo hits");
}

int main(void)
{
    run_capacity(ARM_PAUTH_MEMO_DEFAULT_ENTRIES);
    run_capacity(1024);
    run_capacity(ARM_PAUTH_MEMO_MAX_ENTRIES);
    printf("{\"checks\":%" PRIu64 ",\"hit_checks\":%" PRIu64
           ",\"miss_checks\":%" PRIu64 ",\"capacities\":[64,1024,16384],"
           "\"allocated_cache_bytes_per_cpu\":%zu,"
           "\"entry_bytes\":%zu,\"reset_cache_clear_bytes_per_cpu\":%zu,"
           "\"aligned_64\":{\"hits\":%" PRIu64 ",\"misses\":%" PRIu64 "},"
           "\"aligned_1024\":{\"hits\":%" PRIu64 ",\"misses\":%" PRIu64 "},"
           "\"aligned_16384\":{\"hits\":%" PRIu64 ",\"misses\":%" PRIu64 "},"
           "\"wide_64\":{\"hits\":%" PRIu64 ",\"misses\":%" PRIu64 "},"
           "\"wide_1024\":{\"hits\":%" PRIu64 ",\"misses\":%" PRIu64 "},"
           "\"wide_16384\":{\"hits\":%" PRIu64 ",\"misses\":%" PRIu64 "}}\n",
           checks, hit_checks, miss_checks,
           sizeof(((CPUARMState *)0)->pauth_memo),
           sizeof(ARMPAuthMemoEntry), sizeof(((CPUARMState *)0)->pauth_memo),
           aligned_hits[0], aligned_misses[0], aligned_hits[1], aligned_misses[1],
           aligned_hits[2], aligned_misses[2], wide_hits[0], wide_misses[0],
           wide_hits[1], wide_misses[1], wide_hits[2], wide_misses[2]);
    return 0;
}
