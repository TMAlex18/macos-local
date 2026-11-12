#!/usr/bin/env bash
# Build a tiny diagnostic ARM64+TCG QEMU from pinned portable QEMU without
# VMApple/Reims, to test execution before platform integration is solved.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
P="$ROOT/sources/qemu-portable"
OUT="$ROOT/build/qemu-armvirt"
PROFILE="$ROOT/experiments/E01-build/research-armvirt-only.mak"
EXPECTED=e1aa708569a0de3fbc95fa1ac1836b0ae0a9e96e

if [ ! -d "$P/.git" ]; then echo "Portable QEMU not fetched: $P" >&2; exit 2; fi
if [ "$(git -C "$P" rev-parse HEAD)" != "$EXPECTED" ]; then
   echo "Unexpected portable-QEMU commit; build manually after assessing differences" >&2
   exit 2
fi
ln -sfn "$PROFILE" "$P/configs/devices/aarch64-softmmu/research-armvirt-only.mak"
grep -q '^/configs/devices/aarch64-softmmu/research-armvirt-only.mak$' "$P/.git/info/exclude" \
 || printf '%s\n' '/configs/devices/aarch64-softmmu/research-armvirt-only.mak' >>"$P/.git/info/exclude"
mkdir -p "$OUT" "$ROOT/runs"
if [ ! -f "$OUT/build.ninja" ]; then
 ( cd "$OUT" && "$P/configure" \
  --target-list=aarch64-softmmu --enable-tcg --disable-kvm --disable-docs \
  --disable-gtk --disable-sdl --disable-vnc --disable-werror \
  --disable-tools --disable-user --without-default-devices \
  --with-devices-aarch64=research-armvirt-only ) >"$ROOT/runs/qemu-armvirt-configure.log" 2>&1
fi
ninja -C "$OUT" -j 10 qemu-system-aarch64 >"$ROOT/runs/qemu-armvirt-build.log" 2>&1
"$OUT/qemu-system-aarch64" --version | head -n 1
if "$OUT/qemu-system-aarch64" -machine help | grep -q 'vmapple'; then
  echo 'Unexpected VMApple presence in diagnostic build' >&2; exit 3
fi
"$OUT/qemu-system-aarch64" -machine help | grep 'virt ' | head -n 1
echo 'Diagnostic build ready (ARM virt platform ONLY)'
