#!/usr/bin/env bash
# Modern Reims/QEMU integration smoke: x86_64 QEMU + apple-gfx-pci.
# Does not boot macOS and does not require KVM/VT-x.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
Q="$ROOT/sources/reims/vendor/qemu"
OUT="$ROOT/build/qemu-reims-x86"
DIST="$ROOT/build/reims-target/dist-vulkan"
EXPECTED=1a72df984914fb9929d35b1217619178133d65a4

if [ "$(git -C "$Q" rev-parse HEAD)" != "$EXPECTED" ]; then
    echo "Pinned modern QEMU source mismatch" >&2; exit 2
fi
if [ ! -f "$DIST/lib/pkgconfig/reims-vgpu.pc" ]; then
    echo "Reims Vulkan dist missing; complete cargo xtask dist --platform vulkan first" >&2
    exit 2
fi
export PKG_CONFIG_PATH="$DIST/lib/pkgconfig"
if ! pkg-config --exists --static reims-vgpu; then
    echo "Reims pkg-config package unusable" >&2; exit 2
fi
mkdir -p "$OUT" "$ROOT/runs"
if [ ! -f "$OUT/build.ninja" ]; then
  (
    cd "$OUT"
    "$Q/configure" --target-list=x86_64-softmmu --enable-tcg --disable-kvm \
      --enable-pvg --disable-docs --disable-gtk --disable-sdl \
      --disable-vnc --disable-werror --disable-tools --disable-user
  ) > "$ROOT/runs/qemu-reims-x86-configure.log" 2>&1
fi
ninja -C "$OUT" -j 10 qemu-system-x86_64 > "$ROOT/runs/qemu-reims-x86-build.log" 2>&1
BIN="$OUT/qemu-system-x86_64"
"$BIN" --version | head -n 1
if ! "$BIN" -device help | grep -q 'apple-gfx-pci'; then
  echo "C API linked but apple-gfx-pci device not exposed" >&2; exit 3
fi
echo "Modern QEMU + Reims apple-gfx-pci C device compiled and registered. Guest boot UNTESTED."
