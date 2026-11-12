#!/bin/sh
# Inspect installed unified-log interfaces or verify one live canary event.
# Usage: sh log-canary.sh TAG capabilities | sudo sh log-canary.sh TAG probe
H=${H:-http://10.0.2.2:18769}
tag=${1:?tag required}
mode=${2:-capabilities}
case "$tag" in ''|*[!A-Za-z0-9_-]*) exit 2;; esac
case "$mode" in capabilities|probe) ;; *) exit 2;; esac
if [ "$mode" = probe ]; then
    [ "$(id -u)" = 0 ] || { echo 'probe requires root to match the GPU watcher'; exit 2; }
fi
d=/tmp/$tag
mkdir "$d" || exit 2
child=''; stream_pid=''; parser_pid=''
cleanup() {
    if [ -n "$child" ]; then
        kill -TERM "$child" 2>/dev/null
        sleep 1
        kill -KILL "$child" 2>/dev/null
        wait "$child" 2>/dev/null
        child=''
    fi
    if [ -n "$stream_pid" ]; then
        kill -TERM "$stream_pid" 2>/dev/null
        sleep 1
        kill -KILL "$stream_pid" 2>/dev/null
        wait "$stream_pid" 2>/dev/null
        stream_pid=''
    fi
    if [ -n "$parser_pid" ]; then
        kill -TERM "$parser_pid" 2>/dev/null
        sleep 1
        kill -KILL "$parser_pid" 2>/dev/null
        wait "$parser_pid" 2>/dev/null
        parser_pid=''
    fi
    rm -f "$d/live.fifo"
}
trap cleanup 0
trap 'cleanup; exit 143' TERM INT HUP
bounded_read() {
    name=$1; shift
    printf '%s\n' "$@" > "$d/$name.argv"
    "$@" > "$d/$name.txt" 2> "$d/$name.stderr.txt" &
    child=$!
    deadline=$(( $(date +%s) + 10 ))
    timed_out=false
    while kill -0 "$child" 2>/dev/null; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            timed_out=true
            kill -TERM "$child" 2>/dev/null
            sleep 1
            kill -KILL "$child" 2>/dev/null
            break
        fi
        sleep 0.1
    done
    wait "$child"
    status=$?
    child=''
    echo "$name exit=$status timeout=$timed_out" >> "$d/summary.txt"
}
cp "$0" "$d/collector.sh" || exit 1
{
    echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) uid=$(id -u) mode=$mode"
    echo 'scope=unified-log interface and live observer diagnostic; no Safari launch, stack collection or logging configuration changes'
} > "$d/summary.txt"
probe_status=0
if [ "$mode" = capabilities ]; then
bounded_read binaries /bin/ls -l /usr/bin/logger /usr/bin/log /usr/bin/syslog
bounded_read hashes /usr/bin/shasum -a 256 /usr/bin/logger /usr/bin/log
# No arguments prints log usage; a ten-second child bound also captures an
# unexpected implementation instead of leaving a live diagnostic behind.
bounded_read log-usage /usr/bin/log
for tool in logger log; do
    bounded_read "$tool-man-path" /usr/bin/man -w 1 "$tool"
    index=0
    while IFS= read -r path; do
        [ -f "$path" ] || continue
        index=$((index + 1))
        echo "${tool}_manual_$index=$path" >> "$d/summary.txt"
        case "$path" in
            *.gz) bounded_read "$tool-man-$index" gzip -dc "$path";;
            *) bounded_read "$tool-man-$index" /bin/cat "$path";;
        esac
    done < "$d/$tool-man-path.txt"
done
else
    # These exact emit options are documented in Run110 r110logcaps/log-man-1.
    nonce=e05_gpuwatch_ready_${tag}_$$_$(date +%s)
    canary_predicate="subsystem == \"local.e05.gpuwatch\" AND category == \"readiness\" AND eventMessage == \"$nonce\""
    predicate="(subsystem BEGINSWITH \"com.apple.WebKit\" OR process BEGINSWITH \"com.apple.WebKit\") OR ($canary_predicate)"
    echo "nonce=$nonce" >> "$d/summary.txt"
    printf '%s\n' "$predicate" > "$d/predicate.txt"
    mkfifo "$d/live.fifo" || exit 1
    awk -v archive="$d/live.log" -v nonce="$nonce" -v ready="$d/canary-observed.txt" '
        { print $0 >> archive; fflush(archive) }
        $1 ~ /^[0-9]+-[0-9]+-[0-9]+$/ && $2 ~ /^[0-9]+:[0-9]+:[0-9]+\.[0-9]+$/ &&
        $4 ~ /^log\[[0-9]+:[^]]+\]$/ {
            suffix="[local.e05.gpuwatch:readiness] " nonce
            if (length($0)>=length(suffix) && substr($0,length($0)-length(suffix)+1)==suffix) {
                print $0 > ready; close(ready)
            }
        }
    ' < "$d/live.fifo" &
    parser_pid=$!
    set -- /usr/bin/log stream --level info --style compact --predicate "$predicate"
    printf '%s\n' "$@" > "$d/stream.argv"
    "$@" > "$d/live.fifo" 2> "$d/stream.stderr.txt" &
    stream_pid=$!
    echo "stream_pid=$stream_pid parser_pid=$parser_pid stream_started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
    transport_deadline=$(( $(date +%s) + 10 ))
    while [ ! -s "$d/live.log" ] && [ "$(date +%s)" -lt "$transport_deadline" ]; do sleep 0.1; done
    echo "transport_checked_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ); transport output is not a readiness assertion" >> "$d/summary.txt"
    # Fixed probe schedule, not a readiness inference. The one emitted event
    # must still pass the actual stream/FIFO/parser path to prove readiness.
    sleep 2
    echo "emit_started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
    bounded_read canary-emit /usr/bin/log emit --type default --subsystem local.e05.gpuwatch \
        --category readiness --public "$nonce"
    echo "emit_finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
    event_deadline=$(( $(date +%s) + 15 ))
    while [ ! -s "$d/canary-observed.txt" ] && [ "$(date +%s)" -lt "$event_deadline" ]; do sleep 0.1; done
    if [ -s "$d/canary-observed.txt" ]; then
        echo "canary_observed=true observed_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
    else
        echo 'canary_observed=false; emitter status alone is not readiness' >> "$d/summary.txt"
        probe_status=12
        # Distinguish persistence/emit failures from a live-stream-only miss.
        bounded_read canary-history /usr/bin/log show --last 1m --style compact --predicate "$canary_predicate"
    fi
    cleanup
    echo "owned_children_reaped_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
fi
echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
tar -C /tmp -czf "/tmp/$tag.tgz" "$tag" || exit 1
curl -fsS --connect-timeout 5 -m 30 -T "/tmp/$tag.tgz" "$H/up/$tag.tgz" || exit 1
echo "uploaded $tag.tgz"
exit "$probe_status"
