#!/usr/bin/env bash
# Stable modern VMApple/Reims integration + separate opt-in fractional clock.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$ROOT/sources/reims/vendor/qemu"
P="$ROOT/sources/qemu-clock-exact"
OUT="$ROOT/build/qemu-clock-exact"
PIN=1a72df984914fb9929d35b1217619178133d65a4
if [ ! -e "$P/.git" ]; then git -C "$BASE" worktree add --detach "$P" "$PIN"; fi
if [ "$(git -C "$P" rev-parse HEAD)" != "$PIN" ]; then echo 'Clock source pin mismatch' >&2; exit 2; fi
for name in 0003-vmapple-tcg-pac-model-stack.patch 0005-exact-counter-deadline-clock.patch; do
 PATCH="$ROOT/patches/qemu-platform/$name"
 if git -C "$P" apply --reverse --check "$PATCH" 2>/dev/null; then
   echo "Present: $name"
 elif git -C "$P" apply --check "$PATCH"; then
   git -C "$P" apply "$PATCH"
 else echo "Patch conflict: $name" >&2; exit 2; fi
done
ln -sfn "$ROOT/experiments/E01-build/reims-arm64-tcg.mak" "$P/configs/devices/aarch64-softmmu/reims-arm64-tcg.mak"
export PKG_CONFIG_PATH="$ROOT/build/reims-arm-target/dist-vulkan/lib/pkgconfig"
pkg-config --exists --static reims-vgpu
mkdir -p "$OUT" "$ROOT/runs"
if [ ! -f "$OUT/build.ninja" ]; then
 (cd "$OUT"; "$P/configure" --target-list=aarch64-softmmu --enable-tcg --disable-kvm \
   --enable-pvg --disable-docs --disable-gtk --disable-sdl --disable-vnc \
   --disable-werror --disable-tools --disable-user --without-default-devices \
   --with-devices-aarch64=reims-arm64-tcg) > "$ROOT/runs/qemu-clock-exact-configure.log" 2>&1
fi
ninja -C "$OUT" -j 10 qemu-system-aarch64 > "$ROOT/runs/qemu-clock-exact-build.log" 2>&1
"$OUT/qemu-system-aarch64" --version | head -n1
echo 'Clock experimental binary ready; x-cntfrq-exact defaults off'
