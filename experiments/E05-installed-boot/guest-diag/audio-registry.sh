#!/bin/sh
# List registered Audio Units twice; do not validate units or change audio settings.
# Each process is bounded; the listing is not identical to WebKit's Opus query.
H=${H:-http://10.0.2.2:18769}
tag=${1:?tag required}
case "$tag" in *[!A-Za-z0-9_-]*|'') exit 2;; esac
d=/tmp/$tag
mkdir "$d" || exit 1
for pass in 1 2; do
    {
        date -u +%Y-%m-%dT%H:%M:%SZ
        command -v auval
        pgrep -lf AudioComponentRegistrar || true
    } > "$d/pass-$pass-meta.txt" 2>&1
    /usr/bin/time -p /usr/bin/auval -a > "$d/pass-$pass-list.txt" 2> "$d/pass-$pass-time.txt" &
    runner=$!
    deadline=$(( $(date +%s) + 60 ))
    while kill -0 "$runner" 2>/dev/null && [ "$(date +%s)" -lt "$deadline" ]; do
        sleep 1
    done
    if kill -0 "$runner" 2>/dev/null; then
        children=$(pgrep -P "$runner")
        for child in $children; do kill -TERM "$child" 2>/dev/null; done
        kill -TERM "$runner" 2>/dev/null
        echo "timeout=true deadline_epoch=$deadline observed_epoch=$(date +%s) cleanup_requested_runner=$runner children=$children" >> "$d/pass-$pass-meta.txt"
        break
    fi
    wait "$runner"
    au_exit=$?
    echo "exit=$au_exit" >> "$d/pass-$pass-meta.txt"
    date -u +%Y-%m-%dT%H:%M:%SZ >> "$d/pass-$pass-meta.txt"
    [ "$au_exit" -eq 0 ] || break
done
cat "$d"/*-meta.txt "$d"/*-time.txt
tar -C /tmp -czf "/tmp/$tag.tgz" "$tag"
curl -fsS --connect-timeout 5 -m 30 -T "/tmp/$tag.tgz" "$H/up/$tag.tgz" && echo "uploaded $tag.tgz"
