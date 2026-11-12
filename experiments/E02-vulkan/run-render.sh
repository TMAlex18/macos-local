#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
T="$ROOT/experiments/E02-vulkan"
mkdir -p "$ROOT/build" "$ROOT/runs"
glslangValidator -V -S vert "$T/triangle.vert" -o "$ROOT/build/e02-triangle.vert.spv"
glslangValidator -V -S frag "$T/triangle.frag" -o "$ROOT/build/e02-triangle.frag.spv"
clang -std=c11 -O2 -Wall -Wextra "$T/render.c" -lvulkan -o "$ROOT/build/e02-render"
for gpu in intel nvidia; do
  echo "=== offscreen rasterization: $gpu ==="
  "$ROOT/build/e02-render" "$gpu" "$ROOT/build/e02-triangle.vert.spv" "$ROOT/build/e02-triangle.frag.spv"
done
