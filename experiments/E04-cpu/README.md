# E04: independent AArch64 TCG instruction/serial smoke

Usage: make arm-uart

The assembly program is an ELF payload for QEMU's ARM virt platform, loaded
at 0x40080000. It writes a sentinel through the PL011 MMIO UART, then invokes
the AArch64 semihosting SYS_EXIT using a 16-byte argument block:
reason=ADP_Stopped_ApplicationExit, status=0.

It is intentionally tiny; it does not test MMU, multicore, PAuth/HVC, actual
OS workloads, VMApple nor modern macOS.
