#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
T="$ROOT/experiments/E06-vmapple"
BUILD="$ROOT/build/e06-vmapple"
Q="$ROOT/build/qemu-vmapple-headless/qemu-system-aarch64"
mkdir -p "$BUILD" "$ROOT/runs"
clang --target=aarch64-none-elf -c "$T/psci_cpu_on.S" -o "$BUILD/psci.o"
ld.lld -T "$T/firmware.ld" "$BUILD/psci.o" -o "$BUILD/psci.elf"
llvm-objcopy -O binary "$BUILD/psci.elf" "$BUILD/psci.bin"
truncate -s 4M "$BUILD/aux-test.raw"
truncate -s 32M "$BUILD/root-test.raw"
LOG="$ROOT/runs/e06-psci.log"
set +e
timeout --signal=TERM 12 "$Q" \
  -machine vmapple,gfx-device=none -accel tcg -cpu max -m 512M -smp 2 \
  -nodefaults -nic none -nographic -serial stdio -monitor none \
  -semihosting-config enable=on,target=native \
  -bios "$BUILD/psci.bin" \
  -drive file="$BUILD/aux-test.raw",format=raw,if=pflash,index=0 \
  -drive file="$BUILD/root-test.raw",format=raw,if=pflash,index=1 \
  > "$LOG" 2>&1
STATUS=$?
set -e
cat "$LOG"
if [ "$STATUS" -ne 0 ] || ! grep -q 'VMAPPLE PSCI CPU_ON RAM SIGNAL PASS' "$LOG"; then
  echo "FAIL: PSCI CPU_ON/secondary CPU signalling, guest rc=$STATUS" >&2
  exit 1
fi
echo "PASS: PSCI_CPU_ON via HVC started CPU1 and its RAM signal was observed by CPU0"
