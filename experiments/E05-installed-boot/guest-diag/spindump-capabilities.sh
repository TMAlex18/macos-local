#!/bin/sh
# Read the installed tool/manual before choosing any sampling arguments.
H=${H:-http://10.0.2.2:18769}
tag=${1:?tag required}
case "$tag" in ''|*[!A-Za-z0-9_-]*) exit 2;; esac
d=/tmp/$tag
mkdir "$d" || exit 2
child=''
trap '[ -z "$child" ] || kill -TERM "$child" 2>/dev/null' 0
bounded_read() {
    name=$1; shift
    "$@" > "$d/$name.txt" 2> "$d/$name.stderr.txt" &
    child=$!
    deadline=$(( $(date +%s) + 20 ))
    timed_out=false
    while kill -0 "$child" 2>/dev/null; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            timed_out=true
            kill -TERM "$child" 2>/dev/null
            sleep 1
            kill -KILL "$child" 2>/dev/null
            break
        fi
        sleep 1
    done
    wait "$child"
    result=$?
    child=''
    echo "$name exit=$result timeout=$timed_out" >> "$d/summary.txt"
}
date -u +%Y-%m-%dT%H:%M:%SZ > "$d/summary.txt"
echo 'scope=installed binary and manual discovery only; no stack collection or settings changes' >> "$d/summary.txt"
cp "$0" "$d/collector.sh" || exit 1
bounded_read version /usr/bin/sw_vers
bounded_read identity /usr/bin/id
bounded_read binaries /bin/ls -l /usr/sbin/spindump /usr/bin/sample
bounded_read manual-path /usr/bin/man -w 8 spindump
index=0
while IFS= read -r path; do
    [ -f "$path" ] || continue
    index=$(( index + 1 ))
    echo "manual_$index=$path" >> "$d/summary.txt"
    case "$path" in
        *.gz) bounded_read "manual-$index" gzip -dc "$path";;
        *) bounded_read "manual-$index" /bin/cat "$path";;
    esac
done < "$d/manual-path.txt"
echo "manual_files_read=$index" >> "$d/summary.txt"
date -u +%Y-%m-%dT%H:%M:%SZ >> "$d/summary.txt"
tar -C /tmp -czf "/tmp/$tag.tgz" "$tag" || exit 1
curl -fsS --connect-timeout 5 -m 30 -T "/tmp/$tag.tgz" "$H/up/$tag.tgz" || exit 1
echo "uploaded $tag.tgz"
