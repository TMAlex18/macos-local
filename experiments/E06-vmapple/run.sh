#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
T="$ROOT/experiments/E06-vmapple"
BUILD="$ROOT/build/e06-vmapple"
Q="$ROOT/build/qemu-vmapple-headless/qemu-system-aarch64"
mkdir -p "$BUILD" "$ROOT/runs"
if [ ! -x "$Q" ]; then
  echo "VMApple research QEMU not built yet: $Q" >&2
  exit 2
fi
# Test 1, 2, and 4 configured vCPUs. Secondary vCPUs are not exercised yet.
truncate -s 4M "$BUILD/aux-test.raw"
truncate -s 32M "$BUILD/root-test.raw"
for n in 1 2 4; do
  clang --target=aarch64-none-elf -DEXPECT_CPUS="$n" -c "$T/firmware.S" -o "$BUILD/firmware-$n.o"
  ld.lld -T "$T/firmware.ld" "$BUILD/firmware-$n.o" -o "$BUILD/firmware-$n.elf"
  llvm-objcopy -O binary "$BUILD/firmware-$n.elf" "$BUILD/firmware-$n.bin"
  LOG="$ROOT/runs/e06-vmapple-$n.log"
  set +e
  timeout --signal=TERM 15 "$Q" \
    -machine vmapple,gfx-device=none -accel tcg -cpu max -m 512M -smp "$n" \
    -nodefaults -nic none -nographic -serial stdio -monitor none \
    -semihosting-config enable=on,target=native \
    -bios "$BUILD/firmware-$n.bin" \
    -drive file="$BUILD/aux-test.raw",format=raw,if=pflash,index=0 \
    -drive file="$BUILD/root-test.raw",format=raw,if=pflash,index=1 \
    > "$LOG" 2>&1
  STATUS=$?
  set -e
  cat "$LOG"
  if [ "$STATUS" -ne 0 ] || ! grep -q 'VMAPPLE TCG CFG UART PASS' "$LOG"; then
    echo "E06 FAIL: n=$n, VMApple cfg/UART status=$STATUS" >&2
    exit 1
  fi
  echo "E06 PASS: VMApple cfg version/cpu IDs/RAM, serial; vCPU count=$n"
done
