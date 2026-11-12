#!/usr/bin/env bash
# Reconstructible VMApple research build; no Apple code or boot image.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$ROOT/sources/qemu-portable"
P="$ROOT/sources/qemu-headless"
OUT="$ROOT/build/qemu-vmapple-headless"
REV=e1aa708569a0de3fbc95fa1ac1836b0ae0a9e96e
PATCH="$ROOT/patches/qemu-platform/0001-vmapple-optional-gpu.patch"
PROFILE="$ROOT/experiments/E06-vmapple/research-vmapple-headless.mak"
if [ "$(git -C "$BASE" rev-parse HEAD)" != "$REV" ]; then
  echo "Portable QEMU pin differs from expected source; inspect before rebuilding" >&2
  exit 2
fi
if [ ! -e "$P/.git" ]; then
  git -C "$BASE" worktree add --detach "$P" "$REV"
fi
if [ "$(git -C "$P" rev-parse HEAD)" != "$REV" ]; then
  echo "Headless worktree is not at expected source revision" >&2; exit 2
fi
if git -C "$P" apply --reverse --check "$PATCH" 2>/dev/null; then
  echo "VMApple experiment patch already applied"
elif git -C "$P" apply --check "$PATCH"; then
  git -C "$P" apply "$PATCH"
else
  echo "Patch conflict; refusing to overwrite source modifications" >&2; exit 2
fi
ln -sfn "$PROFILE" "$P/configs/devices/aarch64-softmmu/research-vmapple-headless.mak"
mkdir -p "$OUT" "$ROOT/runs"
if [ ! -e "$OUT/build.ninja" ]; then
  (cd "$OUT"; "$P/configure" \
    --target-list=aarch64-softmmu --enable-tcg --disable-kvm --disable-docs \
    --disable-gtk --disable-sdl --disable-vnc --disable-werror \
    --disable-tools --disable-user --without-default-devices \
    --with-devices-aarch64=research-vmapple-headless \
  ) > "$ROOT/runs/qemu-vmapple-headless-configure.log" 2>&1
fi
ninja -C "$OUT" -j 10 qemu-system-aarch64 > "$ROOT/runs/qemu-vmapple-headless-build.log" 2>&1
BIN="$OUT/qemu-system-aarch64"
"$BIN" --version | head -n 1
if ! "$BIN" -machine help | grep -q '^vmapple '; then
  echo 'VMApple machine not available in built emulator' >&2; exit 3
fi
echo 'VMApple/TCG headless research binary ready; not a macOS-compatible product.'
