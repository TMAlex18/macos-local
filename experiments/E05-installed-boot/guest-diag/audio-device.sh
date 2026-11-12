#!/bin/sh
# Collect runtime sound binding and CoreAudio inventory; no playback or settings changes.
H=${H:-http://10.0.2.2:18769}
tag=${1:?tag required}
case "$tag" in ''|*[!A-Za-z0-9_-]*) exit 2;; esac
d=/tmp/$tag
mkdir "$d" || exit 2
{
    date -u +%Y-%m-%dT%H:%M:%SZ
    echo 'scope=runtime registry and CoreAudio inventory only; not PCM or audible playback'
} > "$d/summary.txt"
shasum -a 256 "$0" > "$d/collector.sha256"
ioreg -r -c AppleVirtIOSound -l > "$d/virtio-sound.txt" 2>&1
registry_status=$?
echo "ioreg_exit=$registry_status" >> "$d/summary.txt"
system_profiler -json -timeout 60 SPAudioDataType > "$d/coreaudio.json" 2> "$d/coreaudio-stderr.txt"
audio_status=$?
echo "system_profiler_exit=$audio_status" >> "$d/summary.txt"
date -u +%Y-%m-%dT%H:%M:%SZ >> "$d/summary.txt"
tar -C /tmp -czf "/tmp/$tag.tgz" "$tag" || exit 1
curl -fsS --connect-timeout 5 -m 60 -T "/tmp/$tag.tgz" "$H/up/$tag.tgz" || exit 1
[ "$registry_status" -eq 0 ] && [ "$audio_status" -eq 0 ]
