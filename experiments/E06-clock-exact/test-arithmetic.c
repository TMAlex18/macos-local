/* Compare the production helper against an independent 128-bit oracle. */
#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdint.h>
#include "gtimer-exact.h"
static uint64_t seed = UINT64_C(0x610cefe24);
static uint64_t random64(void) {
    seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17; return seed;
}
static unsigned long checks;
static void test(uint64_t ns, uint64_t tick, uint32_t hz) {
    uint64_t counter = arm_gt_exact_ticks(ns, hz);
    __uint128_t expected = (__uint128_t)ns * hz / 1000000000;
    assert(counter == expected);
    __uint128_t wide = ((__uint128_t)tick * 1000000000 + hz - 1) / hz;
    int64_t wanted = wide > INT64_MAX ? INT64_MAX : (int64_t)wide;
    int64_t deadline = arm_gt_exact_deadline_ns(tick, hz);
    assert(deadline == wanted);
    if (wide <= INT64_MAX && tick) {
        assert(arm_gt_exact_ticks(deadline, hz) >= tick);
        assert(arm_gt_exact_ticks(deadline - 1, hz) < tick);
    }
    checks++;
}
int main(void) {
    uint32_t frequencies[] = {1, 32768, 19200000, 24000000, 25000000, 62500000, 1000000000};
    uint64_t edges[] = {0,1,2,3,40,41,42,83,84,124,125,126,999999999,
                       1000000000,1000000001,INT64_MAX-1,INT64_MAX,UINT64_MAX-1,UINT64_MAX};
    for (unsigned f=0; f<sizeof(frequencies)/sizeof(*frequencies); f++) {
        uint32_t hz=frequencies[f];
        for (unsigned i=0;i<sizeof(edges)/sizeof(*edges);i++)
            for(unsigned j=0;j<sizeof(edges)/sizeof(*edges);j++) test(edges[i],edges[j],hz);
        for (unsigned i=0;i<50000;i++) test(random64(),random64(),hz);
        for (unsigned i=0;i<5000;i++) test(random64()&INT64_MAX, random64()%10000000000ULL, hz);
    }
    unsigned events=0;
    for(unsigned bit=0;bit<12;bit++) for(unsigned i=0;i<80;i++) for(unsigned falling=0;falling<2;falling++) {
        uint64_t physical=random64()&INT64_MAX, offset=random64();
        uint64_t wanted=0;
        for(uint64_t step=1;step<=(2ULL<<bit);step++) {
            unsigned before=((physical-offset+step-1)>>bit)&1;
            unsigned after=((physical-offset+step)>>bit)&1;
            if (before != after && after == !falling) { wanted=physical+step;break; }
        }
        assert(wanted && wanted==arm_gt_exact_event_tick(physical,offset,bit,falling));
        events++;
    }
    assert(arm_gt_exact_event_tick(UINT64_MAX,0,0,0)==UINT64_MAX);
    assert(arm_gt_exact_deadline_ns(UINT64_MAX,24000000)==INT64_MAX);
    printf("PASS arithmetic_vectors=%lu inverse_rounding_checked=1 event_edge_vectors=%u overflow_saturation_checked=1\n",checks,events);
}
