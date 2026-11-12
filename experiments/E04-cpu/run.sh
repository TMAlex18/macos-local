#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
T="$ROOT/experiments/E04-cpu"
Q="$ROOT/build/qemu-armvirt/qemu-system-aarch64"
clang --target=aarch64-none-elf -c "$T/hello_uart.S" -o "$ROOT/build/hello_uart.o"
ld.lld -T "$T/link.ld" "$ROOT/build/hello_uart.o" -o "$ROOT/build/hello_uart.elf"
LOG="$ROOT/runs/e04-baremetal-uart.log"
set +e
timeout --signal=TERM 8 "$Q" -M virt -cpu cortex-a72 -m 256M -smp 1 -nographic \
    -serial stdio -monitor none -nic none -semihosting-config enable=on,target=native \
    -kernel "$ROOT/build/hello_uart.elf" >"$LOG" 2>&1
STATUS=$?
set -e
cat "$LOG"
if [ "$STATUS" -ne 0 ]; then echo "FAIL: guest returned nonzero exit=$STATUS"; exit 1; fi
if ! grep -q 'M0 ARM64 TCG UART PASS' "$LOG"; then echo "FAIL: UART sentinel missing (exit=$STATUS)"; exit 1; fi
printf 'PASS: AArch64 instructions executed, PL011 UART output captured (guest exit=%s)\n' "$STATUS"
