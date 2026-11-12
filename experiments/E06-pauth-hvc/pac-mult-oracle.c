/* Native harness appended after source-extracted old and new ComputePAC.
 * The reference below is an explicit 4x4 matrix of nibble rotation counts;
 * it does not use either source's column formula or packed word operations.
 */
static uint64_t matrix_oracle(uint64_t input)
{
    static const unsigned matrix[4][4] = {
        { 0, 1, 2, 1 }, { 1, 0, 1, 2 },
        { 2, 1, 0, 1 }, { 1, 2, 1, 0 },
    };
    unsigned cells[4][4], result[4][4] = { { 0 } };
    uint64_t output = 0;

    for (unsigned row = 0; row < 4; row++) {
        for (unsigned col = 0; col < 4; col++) {
            cells[row][col] = (input >> (row * 16 + col * 4)) & 15;
        }
    }
    for (unsigned row = 0; row < 4; row++) {
        for (unsigned col = 0; col < 4; col++) {
            for (unsigned term = 0; term < 4; term++) {
                unsigned n = matrix[row][term], cell = cells[term][col];
                if (n) {
                    result[row][col] ^= ((cell << n) | (cell >> (4 - n))) & 15;
                }
            }
            output |= (uint64_t)result[row][col] << (row * 16 + col * 4);
        }
    }
    return output;
}

static uint64_t rng_state = UINT64_C(0x9e3779b97f4a7c15);
static uint64_t random64(void)
{
    /* Deterministic SplitMix64; unsigned overflow is intentional. */
    uint64_t z = (rng_state += UINT64_C(0x9e3779b97f4a7c15));
    z = (z ^ (z >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
    z = (z ^ (z >> 27)) * UINT64_C(0x94d049bb133111eb);
    return z ^ (z >> 31);
}

static uint64_t mult_cases, sub_cases, permutation_cases, pac_cases[2];
static void check_permutation(uint64_t input)
{
    uint64_t forward = new_pac_cell_shuffle(input);
    uint64_t backward = new_pac_cell_inv_shuffle(input);
    uint64_t tweak = new_tweak_shuffle(input), untweak = new_tweak_inv_shuffle(input);
    if (forward != old_pac_cell_shuffle(input) || backward != old_pac_cell_inv_shuffle(input) ||
        new_pac_cell_inv_shuffle(forward) != input || new_pac_cell_shuffle(backward) != input ||
        tweak != old_tweak_shuffle(input) || untweak != old_tweak_inv_shuffle(input) ||
        new_tweak_inv_shuffle(tweak) != input || new_tweak_shuffle(untweak) != input) {
        fprintf(stderr, "permutation/feedback mismatch: %016" PRIx64 "\n", input);
        exit(1);
    }
    permutation_cases++;
}

static void check_sbox(uint64_t input)
{
    /* Independent, pinned scalar lookup oracles, including both compositions. */
    uint64_t forward = new_pac_sub(input), inverse = new_pac_inv_sub(input);
    if (forward != old_pac_sub(input) || inverse != old_pac_inv_sub(input) ||
        new_pac_inv_sub(forward) != input || new_pac_sub(inverse) != input) {
        fprintf(stderr, "sbox input=%016" PRIx64 " forward=%016" PRIx64
                " inverse=%016" PRIx64 "\n", input, forward, inverse);
        exit(1);
    }
    sub_cases++;
}

static void check_mult(uint64_t input)
{
    uint64_t expected = matrix_oracle(input);
    uint64_t previous = old_pac_mult(input), current = new_pac_mult(input);
    if (expected != previous || expected != current) {
        fprintf(stderr, "mult input=%016" PRIx64 " matrix=%016" PRIx64
                " old=%016" PRIx64 " new=%016" PRIx64 "\n",
                input, expected, previous, current);
        exit(1);
    }
    mult_cases++;
    check_sbox(input);
    check_permutation(input);
}

static uint64_t check_pac(const uint64_t input[4], bool qarma3)
{
    ARMPACKey key = { .lo = input[2], .hi = input[3] };
    uint64_t previous = old_pauth_computepac_architected(input[0], input[1], key, qarma3);
    uint64_t current = new_pauth_computepac_architected(input[0], input[1], key, qarma3);
    if (previous != current) {
        fprintf(stderr, "QARMA%d data=%016" PRIx64 " modifier=%016" PRIx64
                " key.lo=%016" PRIx64 " key.hi=%016" PRIx64
                " old=%016" PRIx64 " new=%016" PRIx64 "\n",
                qarma3 ? 3 : 5, input[0], input[1], key.lo, key.hi, previous, current);
        exit(1);
    }
    pac_cases[qarma3]++;
    return current;
}

int main(void)
{
    static const uint64_t edges[] = {
        0, UINT64_MAX, 1, UINT64_C(0x8000000000000000),
        UINT64_C(0xaaaaaaaaaaaaaaaa), UINT64_C(0x5555555555555555),
        UINT64_C(0x0123456789abcdef), UINT64_C(0xfedcba9876543210),
    };
    check_mult(0);
    for (unsigned bit = 0; bit < 64; bit++) {
        check_mult(UINT64_C(1) << bit);
        check_mult(~(UINT64_C(1) << bit));
        for (unsigned other = bit + 1; other < 64; other++) {
            check_mult((UINT64_C(1) << bit) | (UINT64_C(1) << other));
        }
    }
    /* Exhaust all 16^4 values in each column, including row/cell boundaries. */
    for (unsigned col = 0; col < 4; col++) {
        for (unsigned value = 0; value < 65536; value++) {
            uint64_t input = 0;
            for (unsigned row = 0; row < 4; row++) {
                input |= (uint64_t)((value >> (row * 4)) & 15) << (row * 16 + col * 4);
            }
            check_mult(input);
        }
    }
    for (unsigned i = 0; i < sizeof(edges) / sizeof(edges[0]); i++) {
        check_mult(edges[i]);
    }
    for (unsigned i = 0; i < 1000000; i++) {
        check_mult(random64());
    }
    /* Every value in every lane, with zero, all-one and mixed neighboring cells. */
    for (unsigned lane = 0; lane < 16; lane++) {
        for (unsigned value = 0; value < 16; value++) {
            uint64_t input = (uint64_t)value << (lane * 4);
            uint64_t mask = UINT64_C(15) << (lane * 4);
            check_sbox(input);
            check_sbox((UINT64_MAX & ~mask) | input);
            check_sbox((UINT64_C(0x0123456789abcdef) & ~mask) | input);
        }
    }
    for (unsigned value = 0; value < 16; value++) {
        check_sbox(UINT64_C(0x1111111111111111) * value);
    }
    uint64_t kat_input[] = { known[0], known[1], known[3], known[2] };
    uint64_t kat_output = check_pac(kat_input, false);
    if (kat_output != known[4] ||
        (kat_output & UINT64_C(0xffffffff00000000)) !=
        (known[4] & UINT64_C(0xffffffff00000000))) {
        fprintf(stderr, "upstream QARMA5 known answer mismatch: %016" PRIx64 "\n", kat_output);
        return 1;
    }
    for (unsigned algorithm = 0; algorithm < 2; algorithm++) {
        /* Independently vary every bit of data/modifier/both key halves. */
        for (unsigned field = 0; field < 4; field++) {
            for (unsigned bit = 0; bit < 64; bit++) {
                uint64_t input[4] = { 0 };
                input[field] = UINT64_C(1) << bit;
                check_pac(input, algorithm);
            }
        }
        for (unsigned combination = 0; combination < 4096; combination++) {
            uint64_t input[4];
            for (unsigned field = 0; field < 4; field++) {
                input[field] = edges[(combination >> (3 * field)) & 7];
            }
            check_pac(input, algorithm);
        }
        for (unsigned i = 0; i < 100000; i++) {
            uint64_t input[] = { random64(), random64(), random64(), random64() };
            check_pac(input, algorithm);
        }
    }
    printf("{\"matrix_cases\":%" PRIu64 ",\"zero_and_64_basis\":true,"
           "\"all_bit_pairs\":2016,\"exhaustive_column_cases\":262144,"
           "\"random_matrix_cases\":1000000,\"sbox_cases\":%" PRIu64
           ",\"sbox_all_values_per_lane_backgrounds\":3,\"sbox_both_inverse_compositions\":true,"
           "\"permutation_cases\":%" PRIu64 ",\"permutation_both_inverse_compositions\":true,"
           "\"qarma5_full64_cases\":%" PRIu64
           ",\"qarma3_full64_cases\":%" PRIu64 ",\"random_cases_per_algorithm\":100000,"
           "\"random_seed\":\"9e3779b97f4a7c15\",\"known_answer_full64\":\"%016" PRIx64
           "\",\"known_answer_upstream_high32\":true,\"passed\":true}\n",
           mult_cases, sub_cases, permutation_cases, pac_cases[0], pac_cases[1], kat_output);
    return 0;
}
