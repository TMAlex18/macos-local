#!/usr/bin/env bash
# Log host CPU temperatures while the mainline QEMU is running.
#
# Usage: tools/host-temperature-log.sh OUTPUT.tsv [INTERVAL_SECONDS]
#
# Waits up to two minutes for a qemu-system-aarch64 process to appear, then
# writes one line per interval until it exits:
#   unix_time <TAB> x86_pkg_temp_C <TAB> hottest_zone_C <TAB> load_1min
# Prints a one-line summary at the end. Reads sysfs only; needs no root.
set -u
out="${1:?output file}"
interval="${2:-5}"

pkg_zone=''
for zone in /sys/class/thermal/thermal_zone*; do
    [ "$(cat "$zone/type" 2>/dev/null)" = 'x86_pkg_temp' ] && pkg_zone="$zone"
done

# Match the process name, not the command line: a shell that merely mentions
# the binary path must not count. The kernel truncates the name to 15 bytes.
running() { pgrep -x 'qemu-system-aar' >/dev/null; }

for _ in $(seq 120); do running && break; sleep 1; done
running || { echo "no qemu-system-aarch64 process appeared within 120 s" >&2; exit 1; }

: > "$out"
while running; do
    pkg=''
    [ -n "$pkg_zone" ] && pkg=$(awk '{printf "%.0f", $1/1000}' "$pkg_zone/temp" 2>/dev/null)
    hottest=$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1 | awk '{printf "%.0f", $1/1000}')
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$pkg" "$hottest" "$(cut -d' ' -f1 /proc/loadavg)" >> "$out"
    sleep "$interval"
done

awk -F'\t' 'NR==1{min=$2; max=$2} {s+=$2; if($2>max)max=$2; if($2<min)min=$2; if($3>hot)hot=$3}
     END{if(NR) printf "temperature samples=%d package_min=%dC package_mean=%.1fC package_max=%dC hottest_zone_max=%dC\n", NR, min, s/NR, max, hot}' "$out"
