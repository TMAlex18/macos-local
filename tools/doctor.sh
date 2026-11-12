#!/usr/bin/env bash
set -uo pipefail
printf '=== Host ===\n'; date -Is; uname -a; lscpu | grep -E 'Architecture:|Model name:|CPU\(s\):' | head -n 4
printf '\n=== Memory and filesystem ===\n'; free -h | head -n 3; df -h "$HOME"
printf '\n=== KVM (VT-x != VT-d) ===\n'
if [ -e /dev/kvm ]; then ls -l /dev/kvm; else echo '/dev/kvm missing'; fi
printf '\n=== GPUs ===\n'; lspci -nn | grep -Ei 'VGA compatible|3D controller|Display controller' || true
printf '\n=== Tools ===\n'
for t in git gcc clang meson ninja rustc cargo qemu-system-aarch64 qemu-system-x86_64; do
  printf '%-24s ' "$t"; command -v "$t" || true
done
printf '\n=== Root free space ===\n'; df -h "$HOME" | tail -n 1
