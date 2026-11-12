#!/usr/bin/env bash
# Fetch and verify pinned upstream checkouts (sources.lock.json) plus the
# external reference USB transport patch (by URL + commit + SHA-256) when absent.
# Pass --verify-only to check already-present checkouts and patch digest offline.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERIFY_ONLY=0
if [ "${1:-}" = "--verify-only" ]; then
  VERIFY_ONLY=1
elif [ "$#" -gt 0 ]; then
  echo "Usage: $0 [--verify-only]" >&2
  exit 2
fi

REF_PATCH="$ROOT/patches/upstream-reference/steelbrain-vmapple-usb-chardev-657bb24.patch"
REF_URL="https://raw.githubusercontent.com/steelbrain/experiment-macOS-arm64-on-asahi-linux-arm64/657bb2423ec8d572b22a97dd33563dbba1680e6a/patches/vmapple-usb-chardev.patch"
REF_SHA256="a336748e9531dd594a22c36d23996340e966b19f2ff00580eeebd4bad1a0f717"

ensure_git_pin() {
  local dest="$1"
  local url="$2"
  local pin="$3"
  local label="$4"

  if [ ! -e "$dest/.git" ]; then
    if [ "$VERIFY_ONLY" -eq 1 ]; then
      echo "Missing $label checkout at $dest (--verify-only)" >&2
      exit 2
    fi
    mkdir -p "$(dirname "$dest")"
    git clone --no-checkout "$url" "$dest"
    git -C "$dest" checkout --detach "$pin"
  fi
  local actual
  actual="$(git -C "$dest" rev-parse HEAD)"
  if [ "$actual" != "$pin" ]; then
    if [ "$VERIFY_ONLY" -eq 1 ]; then
      echo "$label pin mismatch at $dest: expected $pin, got $actual" >&2
      exit 2
    fi
    git -C "$dest" fetch origin "$pin"
    git -C "$dest" checkout --detach "$pin"
  fi
  echo "$label verified at $pin"
}

ensure_ref_patch() {
  if [ ! -f "$REF_PATCH" ]; then
    if [ "$VERIFY_ONLY" -eq 1 ]; then
      echo "Missing external reference patch at $REF_PATCH (--verify-only)" >&2
      exit 2
    fi
    mkdir -p "$(dirname "$REF_PATCH")"
    local tmp="$REF_PATCH.tmp.$$"
    trap 'rm -f "$tmp"' EXIT
    curl -fsSL "$REF_URL" -o "$tmp"
    local got
    got="$(sha256sum "$tmp" | awk '{print $1}')"
    if [ "$got" != "$REF_SHA256" ]; then
      echo "SHA-256 mismatch for fetched reference patch: expected $REF_SHA256, got $got" >&2
      rm -f "$tmp"
      exit 3
    fi
    mv -f "$tmp" "$REF_PATCH"
    trap - EXIT
  else
    local got
    got="$(sha256sum "$REF_PATCH" | awk '{print $1}')"
    if [ "$got" != "$REF_SHA256" ]; then
      echo "SHA-256 mismatch at $REF_PATCH: expected $REF_SHA256, got $got" >&2
      exit 3
    fi
  fi
  echo "External reference patch verified ($REF_SHA256)"
}

ensure_ref_patch
ensure_git_pin "$ROOT/sources/reims" "https://github.com/steelbrain/reims-vgpu.git" "86d492197228df29514a87c3de62b2ce59f2188f" "reims"
ensure_git_pin "$ROOT/sources/reims/vendor/qemu" "https://github.com/steelbrain/qemu-reims-vgpu.git" "1a72df984914fb9929d35b1217619178133d65a4" "reims-qemu"
ensure_git_pin "$ROOT/sources/qemu-portable" "https://github.com/imbushuo/qemu.git" "e1aa708569a0de3fbc95fa1ac1836b0ae0a9e96e" "qemu-portable"
