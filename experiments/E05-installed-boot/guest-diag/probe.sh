#!/bin/sh
# Guest load snapshot: sh probe.sh TAG. Uploads capacity, VM counters, two top
# samples and busy processes to guest_files.py (10.0.2.2 is host loopback).
H=${H:-http://10.0.2.2:18769}
tag=${1:-probe}
f=/tmp/$tag.txt
{
  date -u +%Y-%m-%dT%H:%M:%SZ
  uptime
  echo SYSTEM_MEMORY
  sysctl hw.memsize hw.ncpu
  vm_stat
  sysctl vm.swapusage
  top -l 2 -s 3 -n 15 -o cpu -stats pid,command,cpu,time,state,threads
  ps -A -o pid,ppid,user,pri,nice,state,pcpu,time,comm -r | head -45
} > "$f" 2>&1
curl -fsS --connect-timeout 5 -m 60 -T "$f" "$H/up/$tag.txt" && echo "uploaded $tag.txt"
