#!/usr/bin/env bash
# Experimental modern Reims+VMApple ARM64 QEMU under Linux TCG.
# Source and runtime semantics remain research-only, not proven macOS capable.
# 0003 is cumulative from pinned upstream and replaces (not stacks on) 0002.
# Its x-pac-software-model feature is OFF unless explicitly requested at launch.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$ROOT/sources/reims/vendor/qemu"
P="$ROOT/sources/qemu-reims-arm"
OUT="$ROOT/build/qemu-reims-arm"
if printenv MACOS_LOCAL_QEMU_ARM_OUT >/dev/null; then OUT="$MACOS_LOCAL_QEMU_ARM_OUT"; fi
PIN=1a72df984914fb9929d35b1217619178133d65a4
PATCH="$ROOT/patches/qemu-platform/0003-vmapple-tcg-pac-model-stack.patch"
PATCH_AVP="$ROOT/patches/qemu-platform/0004-research-avp-rtc-ctrr-mmio.patch"
PROFILE="$ROOT/experiments/E01-build/reims-arm64-tcg.mak"
DIST="$ROOT/build/reims-target/dist-vulkan"
if printenv MACOS_LOCAL_REIMS_DIST >/dev/null; then DIST="$MACOS_LOCAL_REIMS_DIST"; fi

if [ "$(git -C "$BASE" rev-parse HEAD)" != "$PIN" ]; then
  echo 'Modern Reims QEMU source pin mismatch' >&2; exit 2
fi
if [ ! -e "$P/.git" ]; then
  git -C "$BASE" worktree add --detach "$P" "$PIN"
fi
if [ "$(git -C "$P" rev-parse HEAD)" != "$PIN" ]; then
  echo 'ARM integration worktree pin mismatch' >&2; exit 2
fi
# Incremental patches touch overlapping contexts. A single reverse-apply
# check on the base patch cannot recognize a correctly extended worktree.
# Compare actual changed file bytes to clean pinned HEAD + ordered patches.
if python3 "$ROOT/tools/check-research-patches.py" --component qemu-reims-arm >/dev/null 2>&1; then
  echo 'QEMU research worktree matches recorded patches'
elif git -C "$P" diff --quiet && git -C "$P" apply --check "$PATCH"; then
  git -C "$P" apply "$PATCH"
  git -C "$P" apply --check "$PATCH_AVP"
  git -C "$P" apply "$PATCH_AVP"
  python3 "$ROOT/tools/check-research-patches.py" --component qemu-reims-arm >/dev/null
else
  echo 'QEMU patch chain differs from current worktree; refusing to overwrite source' >&2
  exit 2
fi
ln -sfn "$PROFILE" "$P/configs/devices/aarch64-softmmu/reims-arm64-tcg.mak"

if [ ! -s "$DIST/lib/pkgconfig/reims-vgpu.pc" ]; then
  echo 'Reims Vulkan C ABI distribution missing: first make build-reims-vulkan' >&2
  exit 3
fi
export PKG_CONFIG_PATH="$DIST/lib/pkgconfig"
pkg-config --exists --static reims-vgpu
if ! pkg-config --atleast-version=3.7.3 nettle; then
  echo 'Missing nettle development files >=3.7.3 (Ubuntu: nettle-dev)' >&2; exit 3
fi
mkdir -p "$OUT" "$ROOT/runs"
if [ ! -f "$OUT/build.ninja" ]; then
  (
    cd "$OUT"
    "$P/configure" --target-list=aarch64-softmmu --enable-tcg --disable-kvm \
      --enable-pvg --disable-docs --disable-gtk --disable-sdl --disable-vnc \
      --disable-werror --disable-tools --disable-user --without-default-devices \
      --with-devices-aarch64=reims-arm64-tcg \
      --enable-nettle --disable-gcrypt --disable-crypto-afalg
  ) > "$ROOT/runs/qemu-reims-arm-configure.log" 2>&1
elif ! grep -Eq '^#define CONFIG_NETTLE([[:space:]]+1)?$' "$OUT/config-host.h" || \
     grep -Eq '^#define CONFIG_(GCRYPT|AF_ALG)([[:space:]]+1)?$' "$OUT/config-host.h"; then
  "$OUT/pyvenv/bin/meson" setup --reconfigure --clearcache \
    -Dnettle=enabled -Dgcrypt=disabled -Dcrypto_afalg=disabled "$OUT" "$P" \
    > "$ROOT/runs/qemu-reims-arm-configure.log" 2>&1
fi
ninja -C "$OUT" -j 10 qemu-system-aarch64 > "$ROOT/runs/qemu-reims-arm-build.log" 2>&1
if ! grep -Eq '^#define CONFIG_NETTLE([[:space:]]+1)?$' "$OUT/config-host.h" || \
   grep -Eq '^#define CONFIG_(GCRYPT|AF_ALG)([[:space:]]+1)?$' "$OUT/config-host.h"; then
  echo 'Required Nettle cipher backend not selected' >&2; exit 3
fi
BIN="$OUT/qemu-system-aarch64"
"$BIN" --version | head -n 1
"$BIN" -machine help | grep -F 'vmapple '
# Built-in sysbus devices may not appear in -device help.
if ! nm "$BIN" 2>/dev/null | grep -F 'apple_gfx_mmio_class_init' >/dev/null; then
  echo 'Modern apple-gfx-mmio object not linked' >&2; exit 3
fi
echo 'Experimental VMApple+Reims ARM linked; validate realization with E11'
