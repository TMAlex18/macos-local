#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$ROOT/build" "$ROOT/runs"
clang -std=c11 -O2 -Wall -Wextra "$ROOT/experiments/E03-cross-gpu/capabilities.c" -lvulkan \
  -o "$ROOT/build/e03-external-capabilities"
"$ROOT/build/e03-external-capabilities" | tee "$ROOT/runs/e03-external-capabilities.log"
