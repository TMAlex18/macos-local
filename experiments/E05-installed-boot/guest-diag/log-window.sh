#!/bin/sh
# What every process logged in one short window, launchd's own log lines for it,
# one process list and the memory-pressure summary. Read-only: it launches no
# application and changes no setting.
# Usage: sh log-window.sh TAG 'YYYY-MM-DD HH:MM:SS' 'YYYY-MM-DD HH:MM:SS' [LOG_SHOW_LIMIT_SECONDS]
# The times are guest local time, as `log show` prints them.
H=${H:-http://10.0.2.2:18769}
tag=${1:?tag required}
start=${2:?start required}
end=${3:?end required}
limit=${4:-120}
base=${E05_TMP:-/tmp}
case "$tag" in ''|*[!A-Za-z0-9_-]*) exit 2;; esac
case "$limit" in ''|*[!0-9]*) exit 2;; esac
for stamp in "$start" "$end"; do
    case "$stamp" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]\ [0-9][0-9]:[0-9][0-9]:[0-9][0-9]) ;;
        *) exit 2;;
    esac
done
d=$base/$tag
mkdir "$d" || exit 2
bounded() {
    name=$1
    seconds=$2
    shift 2
    printf '%s\n' "$@" > "$d/$name.argv"
    "$@" > "$d/$name.txt" 2> "$d/$name.stderr.txt" &
    child=$!
    deadline=$(( $(date +%s) + seconds ))
    timed_out=false
    while kill -0 "$child" 2>/dev/null; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            timed_out=true
            kill -TERM "$child" 2>/dev/null
            sleep 1
            kill -KILL "$child" 2>/dev/null
            break
        fi
        sleep 0.5
    done
    wait "$child"
    echo "$name exit=$? timeout=$timed_out finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
}
echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) start=$start end=$end limit=$limit" > "$d/summary.txt"
bounded log-show "$limit" log show --start "$start" --end "$end" --style compact --info --debug
# launchd records service spawns in its own file, not in the unified log.
bounded launchd-log 20 awk -v a="$start" -v b="$end" '{ t = $1 " " substr($2, 1, 8) } t >= a && t <= b' \
    /var/log/com.apple.xpc.launchd/launchd.log
bounded ps-all 30 ps -Ao pid,ppid,lstart,etime,time,comm
bounded memory-pressure 15 memory_pressure
tar -C "$base" -czf "$base/$tag.tgz" "$tag" || exit 1
curl -fsS --connect-timeout 5 -m 60 -T "$base/$tag.tgz" "$H/up/$tag.tgz" || exit 1
echo "uploaded $tag.tgz"
