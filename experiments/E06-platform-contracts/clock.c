/* Synthetic AArch64 guest timebase rate measurement using virtual CNTVCT.
 * No macOS; reports elapsed guest ticks for comparison with host wall time.
 */
#include "runtime.h"
int main(void) {
    uint64_t hz = READ_SYSREG(cntfrq_el0);
    expect(hz > 0 && hz < 2000000000, "UNUSUAL_CNTFRQ");
    field_uart("CNTFRQ",hz);
    puts_uart("MEASURE_BEGIN\n");
    __asm__ volatile("isb" ::: "memory");
    uint64_t start = READ_SYSREG(cntvct_el0);
    uint64_t desired = hz * 3 / 4; /* nominal 750 milliseconds */
    uint64_t current = start;
    do {
        current = READ_SYSREG(cntvct_el0);
    } while (current - start < desired);
    field_uart("CNTVCT_TICKS",current-start);
    puts_uart("MEASURE_END\n");
    platform_exit(0);
}
