#!/bin/sh
# Read the installed command's own help; never start a stack collection here.
H=${H:-http://10.0.2.2:18769}
tag=${1:?tag required}
case "$tag" in ''|*[!A-Za-z0-9_-]*) exit 2;; esac
d=/tmp/$tag
mkdir "$d" || exit 2
child=''
cleanup() {
    [ -n "$child" ] || return 0
    kill -TERM "$child" 2>/dev/null
    sleep 1
    kill -KILL "$child" 2>/dev/null
    wait "$child" 2>/dev/null
    child=''
}
trap cleanup 0
trap 'cleanup; exit 143' TERM INT HUP
cp "$0" "$d/collector.sh" || exit 1
{
    echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) uid=$(id -u)"
    echo 'scope=installed spindump help only; no live sampling, reprocessing or settings changes'
} > "$d/summary.txt"
set -- /usr/sbin/spindump -h
printf '%s\n' "$@" > "$d/help.argv"
"$@" > "$d/help.stdout.txt" 2> "$d/help.stderr.txt" &
child=$!
echo "help_pid=$child" >> "$d/summary.txt"
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
{
    echo "help_exit=$status outer_timeout=$timed_out"
    echo "owned_child_reaped_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >> "$d/summary.txt"
tar -C /tmp -czf "/tmp/$tag.tgz" "$tag" || exit 1
curl -fsS --connect-timeout 5 -m 30 -T "/tmp/$tag.tgz" "$H/up/$tag.tgz" || exit 1
echo "uploaded $tag.tgz"
