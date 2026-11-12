#!/bin/sh
# Usage: sudo sh spindump-control.sh TAG stable|early-exit|system-noipc|system-raw
#        sudo sh spindump-control.sh TAG reprocess SOURCE_TAG [capture|stuck]
#        sudo sh spindump-control.sh TAG export-stuck SOURCE_TAG
# Installed Run109 man and Run112 help define these options. No process queries.
H=${H:-http://10.0.2.2:18769}
tag=${1:?tag required}
mode=${2:?mode required}
source_tag=${3:-}
input_kind=${4:-capture}
case "$tag" in ''|*[!A-Za-z0-9_-]*) exit 2;; esac
case "$mode" in stable|early-exit|system-noipc|system-raw|reprocess|export-stuck) ;; *) exit 2;; esac
[ "$(id -u)" = 0 ] || { echo 'Run this diagnostic with sudo'; exit 2; }
if [ "$mode" = reprocess ] || [ "$mode" = export-stuck ]; then
    case "$source_tag" in ''|*[!A-Za-z0-9_-]*) exit 2;; esac
    [ "$source_tag" != "$tag" ] || exit 2
    source_dir=/tmp/$source_tag
    [ -d "$source_dir" ] && [ ! -L "$source_dir" ] && [ -f "$source_dir/collector.sh" ] || exit 2
    case "$input_kind" in capture|stuck) ;; *) exit 2;; esac
fi
d=/tmp/$tag
mkdir "$d" || exit 2
long_pid=''; short_pid=''; sampler_pid=''
cleanup() {
    for p in "$sampler_pid" "$short_pid" "$long_pid"; do
        [ -z "$p" ] || kill -TERM "$p" 2>/dev/null
    done
    sleep 1
    for p in "$sampler_pid" "$short_pid" "$long_pid"; do
        if [ -n "$p" ]; then
            kill -KILL "$p" 2>/dev/null
            wait "$p" 2>/dev/null
        fi
    done
    sampler_pid=''; short_pid=''; long_pid=''
}
trap cleanup 0
trap 'cleanup; exit 143' TERM INT HUP
{
    date -u +%Y-%m-%dT%H:%M:%SZ
    echo "mode=$mode uid=$(id -u)"
    echo 'scope=kernel stackshot observer diagnostic; not normal performance or watchdog timing'
} > "$d/summary.txt"
cp "$0" "$d/collector.sh" || exit 1
sample_status=0; timed_out=false
report="$d/report.txt"
if [ "$mode" = export-stuck ]; then
    # Accept only the path printed by this owned collector, with its exact PID
    # suffix. Never accept an arbitrary caller-supplied /var/tmp filename.
    source_summary="$source_dir/spindump-summary.txt"
    [ -f "$source_summary" ] || source_summary="$source_dir/summary.txt"
    source_pid=$(awk -F= '$1=="spindump_pid" {print $2; exit}' "$source_summary")
    case "$source_pid" in ''|*[!0-9]*) exit 2;; esac
    stuck=$(awk '/^spindump-stuck: Wrote stackshot to / {path=$NF} END {print path}' "$source_dir/spindump.stderr.txt")
    case "$stuck" in /var/tmp/spindump-stuck-stackshot.*."$source_pid".buf) ;; *) exit 2;; esac
    stuck_name=${stuck#/var/tmp/}
    case "$stuck_name" in *[!A-Za-z0-9_.:-]*) exit 2;; esac
    [ ! -L "$stuck" ] || exit 2
    echo "scope=owned processor diagnostic snapshot; NOT original sampling window; source_tag=$source_tag source_pid=$source_pid" >> "$d/summary.txt"
    printf '%s\n' "$stuck" > "$d/input-reference.txt"
    if [ -s "$stuck" ]; then
        cp "$stuck" "$d/owned-stuck-stackshot.buf" || exit 1
        shasum -a 256 "$stuck" "$d/owned-stuck-stackshot.buf" > "$d/input-sha256.txt"
        wc -c "$d/owned-stuck-stackshot.buf" >> "$d/summary.txt"
    else
        echo 'owned_stuck_buffer=missing-or-empty' >> "$d/summary.txt"
        sample_status=15
    fi
else
    shasum -a 256 /usr/sbin/spindump /bin/sleep > "$d/input-sha256.txt"
    save_grace=1
    outer_seconds=120
    if [ "$mode" = reprocess ]; then
        input="$source_dir/spindump-raw.spindump"
        if [ "$input_kind" = stuck ]; then
            input="$source_dir/owned-stuck-stackshot.buf"
            grep -q '^mode=export-stuck uid=0$' "$source_dir/summary.txt" || exit 2
        else
            grep -Fxq "$input" "$source_dir/spindump.argv" || exit 2
        fi
        [ ! -L "$input" ] && [ -f "$input" ] || exit 2
        printf '%s\n' "$input" > "$d/input-reference.txt"
        echo "scope=reprocess-only existing $input_kind input; no new live samples; source_tag=$source_tag" >> "$d/summary.txt"
        if [ ! -s "$input" ]; then
            echo 'input_present=false reason=empty; reprocess not started' >> "$d/summary.txt"
            sample_status=15
        else
            cp "$input" "$d/input.spindump" || exit 1
            shasum -a 256 "$input" "$d/input.spindump" >> "$d/input-sha256.txt"
            set -- -i "$d/input.spindump" -noIPC -timeline -timelimit 270 -o "$report"
            save_grace=10
            outer_seconds=300
        fi
    else
        /bin/sleep 150 &
        long_pid=$!
        echo "long_pid=$long_pid argv=/bin/sleep,150 started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
        duration=3
        set -- "$long_pid" "$duration" 100 -onlyTarget
        if [ "$mode" = early-exit ]; then
            /bin/sleep 6 &
            short_pid=$!
            echo "short_pid=$short_pid argv=/bin/sleep,6 started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
            set -- "$long_pid" 10 100 -onlyTarget -proc "$short_pid" -sampleWithoutTarget
        fi
        if [ "$mode" = system-noipc ] || [ "$mode" = system-raw ]; then
            duration=10
            [ "$mode" != system-raw ] || duration=3
            set -- "$long_pid" "$duration" 100 -sampleWithoutTarget -noIPC
            echo 'collection_scope=system-wide diagnostic-intervention; independent of Safari quit, pgrep and ownership events' >> "$d/summary.txt"
            echo 'collector_daemon_ipc=noIPC; installed help says more expensive, but more reliable' >> "$d/summary.txt"
        fi
        if [ "$mode" = system-raw ]; then
            report="$d/spindump-raw.spindump"
            set -- "$@" -noText -noSymbolicate
            echo 'report_format=spindump-binary-only symbolication=disabled' >> "$d/summary.txt"
        fi
        set -- "$@" -timelimit 90 -noProcessingWhileSampling -timeline -o "$report"
    fi
    if [ "$sample_status" -eq 0 ]; then
        printf '%s\n' /usr/sbin/spindump "$@" > "$d/spindump.argv"
        echo "collector_started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) outer_seconds=$outer_seconds term_save_grace_seconds=$save_grace" >> "$d/summary.txt"
        /usr/sbin/spindump "$@" > "$d/spindump.stdout.txt" 2> "$d/spindump.stderr.txt" &
        sampler_pid=$!
        echo "spindump_pid=$sampler_pid" >> "$d/summary.txt"
        deadline=$(( $(date +%s) + outer_seconds ))
        while kill -0 "$sampler_pid" 2>/dev/null; do
            if [ -n "$short_pid" ] && ! kill -0 "$short_pid" 2>/dev/null; then
                wait "$short_pid"
                echo "short_exit=$? observed_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
                short_pid=''
            fi
            if [ "$(date +%s)" -ge "$deadline" ]; then
                timed_out=true
                kill -TERM "$sampler_pid" 2>/dev/null
                grace_deadline=$(( $(date +%s) + save_grace ))
                while kill -0 "$sampler_pid" 2>/dev/null && [ "$(date +%s)" -lt "$grace_deadline" ]; do sleep 0.1; done
                kill -KILL "$sampler_pid" 2>/dev/null
                break
            fi
            sleep 1
        done
        wait "$sampler_pid"
        sample_status=$?
        sampler_pid=''
        echo "spindump_exit=$sample_status outer_timeout=$timed_out" >> "$d/summary.txt"
        echo "collector_finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
    fi
    if [ -s "$report" ]; then
        wc -c "$report" >> "$d/summary.txt"
        shasum -a 256 "$report" > "$d/output-sha256.txt"
    else
        echo 'output_present=false reason=missing-or-empty' >> "$d/summary.txt"
        if [ "$mode" = system-raw ] || [ "$mode" = reprocess ]; then
            [ "$sample_status" -ne 0 ] || sample_status=15
        fi
    fi
fi
echo 'stack_acquired=unverified report_completeness=unverified; bytes or exit 0 do not establish decoded frames' >> "$d/summary.txt"
cleanup
echo "owned_children_reaped_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
tar -C /tmp -czf "/tmp/$tag.tgz" "$tag" || exit 1
archive_bytes=$(wc -c < "/tmp/$tag.tgz")
if [ "$archive_bytes" -gt 33554432 ]; then
    echo "archive_upload=refused bytes=$archive_bytes limit=33554432; retained /tmp/$tag.tgz" >&2
    exit 14
fi
curl -fsS --connect-timeout 5 -m 60 -T "/tmp/$tag.tgz" "$H/up/$tag.tgz" || exit 1
echo "uploaded $tag.tgz"
[ "$timed_out" = false ] || exit 124
exit "$sample_status"
