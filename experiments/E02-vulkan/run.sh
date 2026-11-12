#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
T="$ROOT/experiments/E02-vulkan"
mkdir -p "$ROOT/build"
glslangValidator -V -S comp "$T/compute.comp" -o "$ROOT/build/e02-compute.spv"
clang -O2 -std=c11 -D_POSIX_C_SOURCE=200809L -Wall -Wextra "$T/compute.c" -lvulkan -o "$ROOT/build/e02-compute"
for gpu in intel nvidia; do
    echo "=== $gpu GPU: real Vulkan compute and readback ==="
    "$ROOT/build/e02-compute" "$gpu" "$ROOT/build/e02-compute.spv"
done
