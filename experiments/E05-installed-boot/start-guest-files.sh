#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LABEL=${1:?label required}
case "$LABEL" in *[!A-Za-z0-9_-]*|'') exit 2;; esac
RUN_DIR="$ROOT/runs/e05-installed-boot/$LABEL"
SERVE="$ROOT/experiments/E05-installed-boot/guest-diag"
UPLOADS="$RUN_DIR/guest-uploads"

if [[ ! -d "$RUN_DIR" ]]; then
  echo "run directory does not exist: $RUN_DIR" >&2
  exit 1
fi
# Python owns binding, GET/PUT readiness and signal cleanup. exec keeps the
# foreground PID identical to the service, with no shell wait/trap race.
exec python3 "$ROOT/experiments/E05-installed-boot/guest_files.py" \
  --serve "$SERVE" --uploads "$UPLOADS"
