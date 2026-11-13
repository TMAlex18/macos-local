/*
 * Exact-reference check of the float16 FMA fast paths that
 * patches/qemu-platform/0050-f16-muladd-avx2-and-jmpcache-512k.patch adds:
 * f16_muladd_x86_f16c_try() (fpu/softfloat.c) and arm_gvec_fmla_idx_h_x86()
 * (target/arm/tcg/vec_helper.c).
 *
 * run.py copies both functions out of the patch into fastpath.h. This file
 * supplies the few QEMU definitions they use and compares every result the
 * fast paths return with b * c + a computed exactly in 128-bit integers and
 * rounded to float16 with round-to-nearest-even. Calls and lanes that fall
 * back to softfloat are counted, not compared: the fallback is QEMU's code.
 *
 * Usage: oracle CASES SEED    (prints one JSON object on stdout)
 */
#include <immintrin.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The QEMU definitions the extracted code uses. */
typedef uint16_t float16;
enum {
    float_round_nearest_even,
    float_round_down,
    float_round_up,
    float_round_to_zero,
};
enum { float_flag_inexact = 1 << 5 };
typedef struct {
    int float_rounding_mode;
    bool rebias_overflow;
    bool rebias_underflow;
    int float_exception_flags;
} float_status;
#define likely(x) __builtin_expect(!!(x), 1)
#define unlikely(x) __builtin_expect(!!(x), 0)
#define qatomic_read(p) __atomic_load_n((p), __ATOMIC_RELAXED)
#define qatomic_set(p, v) __atomic_store_n((p), (v), __ATOMIC_RELAXED)
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define H2(x) (x)

/* Descriptor layout private to this test: oprsz | maxsz << 8 | data << 16. */
static intptr_t simd_oprsz(uint32_t desc) { return desc & 0xff; }
static intptr_t simd_maxsz(uint32_t desc) { return (desc >> 8) & 0xff; }
static intptr_t simd_data(uint32_t desc) { return desc >> 16; }

static void clear_tail(void *vd, intptr_t oprsz, intptr_t maxsz)
{
    memset((char *)vd + oprsz, 0, maxsz - oprsz);
}

/*
 * Stand-in for softfloat in the vector fallback: it marks the lane with a
 * NaN pattern the fast path can never produce and raises no flag.
 */
#define FALLBACK_MARK 0xffff
static float16 float16_muladd(float16 a, float16 b, float16 c, int flags,
                              float_status *s)
{
    (void)a; (void)b; (void)c; (void)flags; (void)s;
    return FALLBACK_MARK;
}

#include "fastpath.h"

typedef __int128 i128;
typedef unsigned __int128 u128;

/* A finite float16 as a signed integer times 2^-24. */
static int64_t f16_scaled24(uint16_t h)
{
    int e = (h >> 10) & 0x1f;
    int64_t m = h & 0x3ff;
    if (e) {
        m |= 0x400;
    } else {
        e = 1;
    }
    int64_t v = m << (e - 1);
    return (h & 0x8000) ? -v : v;
}

/*
 * b * c + a, exact, rounded to nearest-even float16. Only normal results are
 * produced (the fast paths return nothing else); false for zero, subnormal
 * or overflowing results.
 */
static bool ref_fma(uint16_t b, uint16_t c, uint16_t a, uint16_t *out,
                    bool *inexact)
{
    i128 v = (i128)f16_scaled24(b) * f16_scaled24(c) +
             (i128)f16_scaled24(a) * ((i128)1 << 24);    /* units of 2^-48 */
    if (v == 0) {
        return false;
    }
    bool neg = v < 0;
    u128 mag = neg ? (u128)(-v) : (u128)v;
    int msb = 127;
    while (!((mag >> msb) & 1)) {
        msb--;
    }
    int e = msb - 48;
    if (e < -14) {
        return false;
    }
    int shift = msb - 10;
    u128 q = mag >> shift;
    u128 rem = mag & (((u128)1 << shift) - 1);
    u128 half = (u128)1 << (shift - 1);
    *inexact = rem != 0;
    if (rem > half || (rem == half && (q & 1))) {
        q++;
        if (q == 0x800) {
            q = 0x400;
            e++;
        }
    }
    if (e > 15) {
        return false;
    }
    *out = (uint16_t)((neg ? 0x8000 : 0) | ((e + 15) << 10) |
                      (uint16_t)(q & 0x3ff));
    return true;
}

static uint64_t rng_state;

static uint64_t rnd(void)
{
    uint64_t z = (rng_state += 0x9e3779b97f4a7c15ULL);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}

/* Normal float16 with a biased exponent in [lo, hi] and a random sign. */
static uint16_t normal16(uint64_t r, int lo, int hi)
{
    int e = lo + (int)((r >> 16) % (uint64_t)(hi - lo + 1));
    return (uint16_t)((r & 0x83ff) | (e << 10));
}

static int clampi(int v, int lo, int hi)
{
    return v < lo ? lo : v > hi ? hi : v;
}

/*
 * One operand triple (b, c, a) for b * c + a from one of four generators:
 * any bits, close exponents (cancellation and exact midpoints), a small
 * addend, or a zero operand.
 */
static void triple(uint64_t r, unsigned kind, uint16_t *b, uint16_t *c,
                   uint16_t *a)
{
    uint64_t s = rnd();
    switch (kind & 3) {
    case 0:
        *b = (uint16_t)r;
        *c = (uint16_t)(r >> 16);
        *a = (uint16_t)(r >> 32);
        break;
    case 1: {
        *b = normal16(r, 10, 19);
        *c = normal16(r >> 24, 10, 19);
        int pe = ((*b >> 10) & 0x1f) + ((*c >> 10) & 0x1f) - 15;
        *a = normal16(s, clampi(pe - 12, 1, 30), clampi(pe + 12, 1, 30));
        break;
    }
    case 2:
        *b = normal16(r, 8, 21);
        *c = normal16(r >> 24, 8, 21);
        *a = (s & 1) ? normal16(s >> 1, 1, 6) : (uint16_t)(s & 0x8000);
        break;
    default:
        *b = (s & 1) ? (uint16_t)(r & 0x8000) : normal16(r, 1, 30);
        *c = normal16(r >> 24, 1, 30);
        *a = (s & 2) ? (uint16_t)(s & 0x8000) : normal16(s >> 2, 1, 30);
        break;
    }
}

typedef struct {
    uint64_t hits, fallbacks, mismatches;
} ScalarStats;

static void scalar_case(uint16_t b, uint16_t c, uint16_t a, ScalarStats *st)
{
    for (int before = 0; before < 2; before++) {
        float_status s = {
            .float_rounding_mode = float_round_nearest_even,
            .float_exception_flags = before ? float_flag_inexact : 0,
        };
        F16FmaFastResult r = f16_muladd_x86_f16c_try(b, c, a, 0, 0, &s);
        if (!r.hit) {
            st->fallbacks++;
            if (s.float_exception_flags != (before ? float_flag_inexact : 0)) {
                st->mismatches++;                  /* a miss must not touch flags */
            }
            continue;
        }
        st->hits++;
        uint16_t want;
        bool inexact = false;
        bool ok = ref_fma(b, c, a, &want, &inexact);
        int want_flags = (before || inexact) ? float_flag_inexact : 0;
        if (!ok || r.val != want || s.float_exception_flags != want_flags) {
            if (st->mismatches < 10) {
                fprintf(stderr, "scalar mismatch b=%04x c=%04x a=%04x before=%d "
                        "fast=%04x/%d ref=%s%04x/%d\n", b, c, a, before, r.val,
                        s.float_exception_flags != 0, ok ? "" : "(none)",
                        ok ? want : 0, want_flags != 0);
            }
            st->mismatches++;
        }
    }
}

typedef struct {
    uint64_t calls, fast_lanes, fallback_lanes, mismatches, flag_mismatches;
} VectorStats;

static void vector_case(uint64_t r, VectorStats *st)
{
    static const int sizes[3] = { 8, 16, 32 };
    int oprsz = sizes[r % 3];
    int lanes = oprsz / 2;
    int segment = MIN(16, oprsz) / 2;
    int idx = (int)((r >> 8) % (uint64_t)segment);
    uint16_t negx = (r >> 16) & 1 ? 0x8000 : 0;
    bool before = (r >> 17) & 1;
    unsigned kind = (unsigned)(r >> 18);
    uint16_t n[16], m[16], a[16], d[16];

    for (int i = 0; i < lanes; i++) {
        uint16_t bb, cc, aa;
        triple(rnd(), kind, &bb, &cc, &aa);
        n[i] = bb;
        m[i] = cc;
        a[i] = aa;
    }
    /*
     * Each segment multiplies by one element of m. For the structured
     * generators, rebuild the addends around that element's exponent.
     */
    if ((kind & 3) == 1 || (kind & 3) == 2) {
        for (int base = 0; base < lanes; base += segment) {
            uint16_t mm = m[base + idx];
            for (int j = 0; j < segment; j++) {
                uint16_t nn = n[base + j];
                int pe = ((nn >> 10) & 0x1f) + ((mm >> 10) & 0x1f) - 15;
                uint64_t s = rnd();
                if ((kind & 3) == 1) {
                    a[base + j] = normal16(s, clampi(pe - 12, 1, 30),
                                           clampi(pe + 12, 1, 30));
                } else {
                    a[base + j] = (s & 1) ? normal16(s >> 1, 1, 6)
                                          : (uint16_t)(s & 0x8000);
                }
            }
        }
    }

    float_status s = {
        .float_rounding_mode = float_round_nearest_even,
        .float_exception_flags = before ? float_flag_inexact : 0,
    };
    arm_gvec_fmla_idx_h_x86(d, n, m, a, negx, &s,
                            (uint32_t)(oprsz | oprsz << 8 | idx << 16));
    st->calls++;

    bool any_inexact = false;
    for (int i = 0; i < lanes; i++) {
        uint16_t mm = m[(i / segment) * segment + idx];
        if (d[i] == FALLBACK_MARK) {
            st->fallback_lanes++;
            continue;
        }
        st->fast_lanes++;
        uint16_t want;
        bool inexact = false;
        bool ok = ref_fma((uint16_t)(n[i] ^ negx), mm, a[i], &want, &inexact);
        any_inexact |= inexact;
        if (!ok || d[i] != want) {
            if (st->mismatches < 10) {
                fprintf(stderr, "vector mismatch n=%04x m=%04x a=%04x negx=%04x "
                        "fast=%04x ref=%s%04x\n", n[i], mm, a[i], negx, d[i],
                        ok ? "" : "(none)", ok ? want : 0);
            }
            st->mismatches++;
        }
    }
    int want_flags = (before || any_inexact) ? float_flag_inexact : 0;
    if (s.float_exception_flags != want_flags) {
        st->flag_mismatches++;
    }
}

/* With non-default status the fast paths must not produce any result. */
static void disabled_cases(uint64_t count, uint64_t *scalar_hits,
                           uint64_t *vector_fast_lanes)
{
    for (uint64_t i = 0; i < count; i++) {
        uint64_t r = rnd();
        uint16_t b = normal16(r, 10, 19), c = normal16(r >> 24, 10, 19);
        uint16_t a = normal16(r >> 40, 10, 19);
        float_status s = { .float_rounding_mode = float_round_nearest_even };
        int scale = 0, flags = 0;
        switch (i % 6) {
        case 0: s.float_rounding_mode = float_round_down; break;
        case 1: s.float_rounding_mode = float_round_to_zero; break;
        case 2: s.rebias_overflow = true; break;
        case 3: s.rebias_underflow = true; break;
        case 4: scale = 1; break;
        default: flags = 1; break;
        }
        if (f16_muladd_x86_f16c_try(b, c, a, scale, flags, &s).hit) {
            (*scalar_hits)++;
        }
        if (i % 6 < 4) {
            uint16_t n[8], m[8], aa[8], d[8];
            for (int k = 0; k < 8; k++) {
                n[k] = b; m[k] = c; aa[k] = a;
            }
            arm_gvec_fmla_idx_h_x86(d, n, m, aa, 0, &s, 16 | 16 << 8);
            for (int k = 0; k < 8; k++) {
                if (d[k] != FALLBACK_MARK) {
                    (*vector_fast_lanes)++;
                }
            }
        }
    }
}

int main(int argc, char **argv)
{
    if (argc != 3) {
        fprintf(stderr, "usage: %s CASES SEED\n", argv[0]);
        return 2;
    }
    uint64_t cases = strtoull(argv[1], NULL, 0);
    rng_state = strtoull(argv[2], NULL, 0);

    ScalarStats sc = { 0 };
    for (uint64_t i = 0; i < cases; i++) {
        uint64_t r = rnd();
        uint16_t b, c, a;
        triple(r, (unsigned)i, &b, &c, &a);
        scalar_case(b, c, a, &sc);
    }

    VectorStats vc = { 0 };
    for (uint64_t i = 0; i < cases / 4; i++) {
        vector_case(rnd(), &vc);
    }

    uint64_t dis_scalar = 0, dis_vector = 0;
    disabled_cases(cases / 100 + 100, &dis_scalar, &dis_vector);

    printf("{\"cases\": %llu, \"seed\": %llu, "
           "\"scalar\": {\"hits\": %llu, \"fallbacks\": %llu, \"mismatches\": %llu}, "
           "\"vector\": {\"calls\": %llu, \"fast_lanes\": %llu, \"fallback_lanes\": %llu, "
           "\"mismatches\": %llu, \"flag_mismatches\": %llu}, "
           "\"disabled\": {\"scalar_hits\": %llu, \"vector_fast_lanes\": %llu}}\n",
           (unsigned long long)cases, (unsigned long long)strtoull(argv[2], NULL, 0),
           (unsigned long long)sc.hits, (unsigned long long)sc.fallbacks,
           (unsigned long long)sc.mismatches, (unsigned long long)vc.calls,
           (unsigned long long)vc.fast_lanes, (unsigned long long)vc.fallback_lanes,
           (unsigned long long)vc.mismatches, (unsigned long long)vc.flag_mismatches,
           (unsigned long long)dis_scalar, (unsigned long long)dis_vector);
    return 0;
}
