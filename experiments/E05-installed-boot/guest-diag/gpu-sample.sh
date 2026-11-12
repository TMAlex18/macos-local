#!/bin/sh
# Diagnostic stack sampling of up to three existing WebKit GPU processes.
# Sampling can perturb a process; do not use this as a performance measurement.
H=${H:-http://10.0.2.2:18769}
tag=${1:?tag required}
case "$tag" in *[!A-Za-z0-9_-]*|'') exit 2;; esac
d=/tmp/$tag
mkdir "$d" || exit 1
{
    date -u +%Y-%m-%dT%H:%M:%SZ
    command -v sample
    for p in $(pgrep -f com.apple.WebKit.GPU | head -3); do
        echo "GPU_PID=$p"
        ps -p "$p" -o pid,ppid,state,time,etime,comm
        ps -M -p "$p" > "$d/threads-$p.txt" 2>&1
        sample "$p" 1 -file "$d/sample-$p.txt" > "$d/sample-$p.log" 2>&1
        echo "sample_exit=$?"
    done
} > "$d/summary.txt" 2>&1
cat "$d/summary.txt"
tar -C /tmp -czf "/tmp/$tag.tgz" "$tag"
curl -fsS --connect-timeout 5 -m 30 -T "/tmp/$tag.tgz" "$H/up/$tag.tgz" && echo "uploaded $tag.tgz"
