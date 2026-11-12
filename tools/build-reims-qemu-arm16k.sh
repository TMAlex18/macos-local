#!/usr/bin/env bash
# Rebuild the same experimental QEMU ARM source against the independently
# compiled 16 KiB ARM guest vGPU ABI (native x86_64 host, no Apple media).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export MACOS_LOCAL_QEMU_ARM_OUT="$ROOT/build/qemu-reims-arm16k"
export MACOS_LOCAL_REIMS_DIST="$ROOT/build/reims-arm-target/dist-vulkan"
exec "$ROOT/tools/build-reims-qemu-arm.sh"
