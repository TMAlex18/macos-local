#include "runtime.h"
void puts_uart(const char *s) {
    volatile uint32_t *data=(void *)(uintptr_t)0x20010000;
    volatile uint32_t *flags=(void *)(uintptr_t)0x20010018;
    while (*s) { while (*flags & (1u<<5)) {} *data=(unsigned char)*s++; }
}
void hex_uart(uint64_t v) {
    char b[19]; b[0]='0';b[1]='x';
    for(unsigned i=0;i<16;i++) b[2+i]="0123456789abcdef"[(v>>((15-i)*4))&15];
    b[18]=0;puts_uart(b);
}
void field_uart(const char *name,uint64_t v) { puts_uart(name);puts_uart("=");hex_uart(v);puts_uart("\n"); }
void platform_exit(unsigned status) {
    uint64_t block[2]={0x20026,status};
    register uint64_t x0 __asm__("x0")=0x18;
    register uint64_t x1 __asm__("x1")=(uintptr_t)block;
    __asm__ volatile("hlt #0xf000" : "+r"(x0),"+r"(x1) :: "memory");
    for(;;) __asm__ volatile("wfi");
}
void fail(const char *reason) { puts_uart("FAIL: ");puts_uart(reason);puts_uart("\n");platform_exit(1); }
void expect(int condition,const char *reason) { if(!condition)fail(reason); }
__attribute__((weak)) void exception_sync(struct exception_frame *f) { exception_bad(f); }
__attribute__((weak)) void exception_irq(struct exception_frame *f) { exception_bad(f); }
void exception_bad(struct exception_frame *f) {
    field_uart("UNEXPECTED_ESR",READ_SYSREG(esr_el1));
    field_uart("UNEXPECTED_FAR",READ_SYSREG(far_el1));
    field_uart("UNEXPECTED_ELR",f->elr);
    fail("UNEXPECTED_EXCEPTION");
}
