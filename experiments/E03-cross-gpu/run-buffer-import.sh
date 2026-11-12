#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$ROOT/build" "$ROOT/runs"
clang -O2 -std=c11 -Wall -Wextra "$ROOT/experiments/E03-cross-gpu/import_buffer.c" -lvulkan -o "$ROOT/build/e03-import-buffer"
"$ROOT/build/e03-import-buffer" | tee "$ROOT/runs/e03-import-buffer.log"
