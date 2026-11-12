#!/usr/bin/env bash
# Reproducible modern Reims Vulkan C ABI package without macOS guest material.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$ROOT/sources/reims"
WORK="$ROOT/sources/reims-build"
PIN=86d492197228df29514a87c3de62b2ce59f2188f
PATCH="$ROOT/patches/reims/0001-pin-metal2vulkan-git-sources.patch"
if [ "$(git -C "$BASE" rev-parse HEAD)" != "$PIN" ]; then
  echo 'Pinned Reims superproject revision mismatch' >&2; exit 2
fi
if [ ! -e "$WORK/.git" ]; then
  git -C "$BASE" worktree add --detach "$WORK" "$PIN"
fi
if [ "$(git -C "$WORK" rev-parse HEAD)" != "$PIN" ]; then
  echo 'Reims build worktree pin mismatch' >&2; exit 2
fi
if git -C "$WORK" apply --reverse --check "$PATCH" 2>/dev/null; then
  echo 'Cargo.lock pin patch already applied'
elif git -C "$WORK" apply --check "$PATCH"; then
  git -C "$WORK" apply "$PATCH"
else
  echo 'Reims Cargo.lock patch collision; no changes applied' >&2; exit 2
fi
export CARGO_TARGET_DIR="$ROOT/build/reims-target"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-8}"
RUST_TC="${MACOS_LOCAL_RUST_TOOLCHAIN:-1.98.1}"
(cd "$WORK" && cargo +"$RUST_TC" xtask dist --platform vulkan)
DIST="$CARGO_TARGET_DIR/dist-vulkan"
test -s "$DIST/lib/libreims_vgpu.a"
test -s "$DIST/lib/libreims_vgpu.so"
test -s "$DIST/lib/pkgconfig/reims-vgpu.pc"
PKG_CONFIG_PATH="$DIST/lib/pkgconfig" pkg-config --exists reims-vgpu
echo "Reims vGPU Linux/Vulkan C ABI dist ready: $DIST"
