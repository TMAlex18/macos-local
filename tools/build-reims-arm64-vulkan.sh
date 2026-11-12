#!/usr/bin/env bash
# Build modern Reims C API on Linux/x86_64 with experimental ARM64 guest pages.
# Guest 16 KiB page numbers and host 4 KiB mmap granules are distinct.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$ROOT/sources/reims"
WORK="$ROOT/sources/reims-arm-guest"
PIN=86d492197228df29514a87c3de62b2ce59f2188f
if [ "$(git -C "$BASE" rev-parse HEAD)" != "$PIN" ]; then
  echo 'Modern Reims superproject pin mismatch' >&2; exit 2
fi
if [ ! -e "$WORK/.git" ]; then
    git -C "$BASE" worktree add --detach "$WORK" "$PIN"
fi
if [ "$(git -C "$WORK" rev-parse HEAD)" != "$PIN" ]; then
    echo 'Reims ARM guest worktree pin mismatch' >&2; exit 2
fi
bash "$ROOT/tools/prepare-metal2vulkan-research.sh"
# Later patches may deliberately change an earlier patch's lines (the pinned dependency's
# local patch overlay changes Cargo.lock's source fields). Verify the complete chain first.
if ! python3 "$ROOT/tools/check-research-patches.py" --component reims-arm-guest >/dev/null 2>&1; then
 if ! git -C "$WORK" diff --quiet; then
  echo 'Reims worktree differs from recorded patch chain; refusing overwrite' >&2; exit 3
 fi
 for patch in "$ROOT/patches/reims/0001-pin-metal2vulkan-git-sources.patch" \
             "$ROOT/patches/reims/0002-cross-isa-arm64-guest-pages.patch" \
             "$ROOT/patches/reims/0003-iosurface-guest-page-separation.patch" \
             "$ROOT/patches/reims/0004-fixture-host-guest-page-boundaries.patch" \
             "$ROOT/patches/reims/0005-isolated-task-host-alignment.patch" \
             "$ROOT/patches/reims/0006-detached-heap-aliasable-backing.patch" \
             "$ROOT/patches/reims/0007-half-float-fetch-source-over.patch"; do
  git -C "$WORK" apply "$patch"
 done
fi
python3 "$ROOT/tools/check-research-patches.py" --component reims-arm-guest
export CARGO_TARGET_DIR="$ROOT/build/reims-arm-target"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-8}"
export MACOS_LOCAL_ARM64_GUEST=1
RUST_TC="${MACOS_LOCAL_RUST_TOOLCHAIN:-1.98.1}"
(cd "$WORK" && cargo +"$RUST_TC" xtask dist --platform vulkan)
DIST="$CARGO_TARGET_DIR/dist-vulkan"
test -s "$DIST/lib/libreims_vgpu.a"
test -s "$DIST/lib/libreims_vgpu.so"
test -s "$DIST/lib/pkgconfig/reims-vgpu.pc"
PKG_CONFIG_PATH="$DIST/lib/pkgconfig" pkg-config --exists --static reims-vgpu
echo "ARM64 guest-page (16 KiB) experimental Reims Linux/Vulkan distribution ready: $DIST"
