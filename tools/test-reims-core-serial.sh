#!/usr/bin/env bash
# Full Core unit suite in the two guest-ABI profiles. Serial test scheduling
# avoids this upstream suite's process-wide shared log-capture interference.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/sources/reims-arm-guest"
export CARGO_TARGET_DIR="$ROOT/build/reims-arm-target"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-8}"
RUST_TC="${MACOS_LOCAL_RUST_TOOLCHAIN:-1.98.1}"
for abi in arm16k x86; do
  features=()
  if [ "$abi" = arm16k ]; then features=(--features arm64-guest-pages); fi
  LOG="$ROOT/runs/v3-core-$abi-serial-final.log"
  if cargo +"$RUST_TC" test --locked -p reims-vgpu-core "${features[@]}" -- --test-threads=1 > "$LOG" 2>&1; then
    echo "$abi Core suite PASS"
    grep '^test result:' "$LOG"
  else
    rc=$?
    tail -n 70 "$LOG" >&2
    exit "$rc"
  fi
done
