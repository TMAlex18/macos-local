#ifndef PLATFORM_TEST_RUNTIME_H
#define PLATFORM_TEST_RUNTIME_H
#include <stdint.h>
#include <stddef.h>
struct exception_frame { uint64_t x[31]; uint64_t elr; uint64_t spsr; uint64_t pad; };
_Static_assert(sizeof(struct exception_frame)==272,"vector frame layout");
#define READ_SYSREG(reg) ({ uint64_t v_; __asm__ volatile("mrs %0, " #reg : "=r"(v_)); v_; })
#define WRITE_SYSREG(reg,value) do { uint64_t v_=(value); __asm__ volatile("msr " #reg ", %0" :: "r"(v_) : "memory"); } while(0)
static inline void dsb_sy(void) { __asm__ volatile("dsb sy" ::: "memory"); }
static inline void isb(void) { __asm__ volatile("isb" ::: "memory"); }
static inline void mask_irq(void) { __asm__ volatile("msr daifset, #2" ::: "memory"); }
static inline void unmask_irq(void) { __asm__ volatile("msr daifclr, #2" ::: "memory"); }
static inline uint32_t mmio32(uint64_t address) { return *(volatile uint32_t *)(uintptr_t)address; }
static inline void write32(uint64_t address,uint32_t v) { *(volatile uint32_t *)(uintptr_t)address=v; }
void puts_uart(const char *s);
void hex_uart(uint64_t v);
void field_uart(const char *name,uint64_t v);
void fail(const char *reason) __attribute__((noreturn));
void platform_exit(unsigned status) __attribute__((noreturn));
void expect(int condition,const char *reason);
void exception_sync(struct exception_frame *f);
void exception_irq(struct exception_frame *f);
void exception_bad(struct exception_frame *f) __attribute__((noreturn));
uint64_t probe_load(uint64_t address);
void probe_store(uint64_t address,uint64_t value);
extern const unsigned char probe_load_instruction[], probe_store_instruction[];
#endif
