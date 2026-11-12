#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/build/e06-clock-exact" "$ROOT/runs"
clang -std=c11 -O2 -Wall -Wextra -Werror -fsanitize=undefined,address \
 -I "$ROOT/sources/qemu-clock-exact/target/arm" \
 "$ROOT/experiments/E06-clock-exact/test-arithmetic.c" \
 -o "$ROOT/build/e06-clock-exact/test-arithmetic"
"$ROOT/build/e06-clock-exact/test-arithmetic" | tee "$ROOT/runs/e06-clock-exact-arithmetic.log"
