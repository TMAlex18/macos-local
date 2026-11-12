#!/usr/bin/env bash
# The existing fixed dependency revision plus the recorded framebuffer-fetch patch.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/sources/metal2vulkan-research"
PIN=ecb80e019d54b8eb1585974fb2a6cc37bac359cc
PATCH="$ROOT/patches/metal2vulkan/0001-half-float-fetch-source-over.patch"
if [ ! -e "$SRC/.git" ]; then
  cached_source=""
  for candidate in "${CARGO_HOME:-$HOME/.cargo}"/git/checkouts/metal2vulkan-*/ecb80e0; do
    if [ -d "$candidate/.git" ] && [ "$(git -C "$candidate" rev-parse HEAD)" = "$PIN" ]; then
      cached_source="$candidate"; break
    fi
  done
  if [ -n "$cached_source" ]; then
    git clone --local --no-checkout "$cached_source" "$SRC"
  else
    git clone --no-checkout https://github.com/steelbrain/metal2vulkan "$SRC"
  fi
  git -C "$SRC" checkout --detach "$PIN"
fi
if [ "$(git -C "$SRC" rev-parse HEAD)" != "$PIN" ]; then
  echo 'metal2vulkan research source pin mismatch' >&2; exit 2
fi
if git -C "$SRC" apply --reverse --check "$PATCH" 2>/dev/null; then
  echo 'metal2vulkan framebuffer-fetch patch already applied'
elif git -C "$SRC" apply --check "$PATCH"; then
  git -C "$SRC" apply "$PATCH"
else
  echo 'metal2vulkan source differs from its recorded patch; refusing overwrite' >&2; exit 3
fi
python3 "$ROOT/tools/check-research-patches.py" --component metal2vulkan-research
