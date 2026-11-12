#!/bin/sh
# Host per-thread CPU samples for cpu_occupancy.py: host-thread-sampler.sh QEMU_PID OUTPUT [SECONDS]
# Appends `ps -L` blocks headed by '=== ISO-UTC ===' until the PID exits.
pid=$1
out=$2
interval=${3:-30}
while kill -0 "$pid" 2> /dev/null; do
  { echo "=== $(date -u +%Y-%m-%dT%H:%M:%S.%3NZ) ==="
    ps -L -o pid=,tid=,ppid=,psr=,stat=,pcpu=,time=,comm= -p "$pid"; } >> "$out"
  sleep "$interval"
done
