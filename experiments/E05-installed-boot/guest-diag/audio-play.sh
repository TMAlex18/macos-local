#!/bin/sh
# Usage: sh audio-play.sh TAG [plain|sample]
# Play the self-authored stereo fixture; host WAV validation remains separate.
H=${H:-http://10.0.2.2:18769}
tag=${1:?tag required}
sampling=${2:-plain}
case "$tag" in ''|*[!A-Za-z0-9_-]*) exit 2;; esac
case "$sampling" in plain|sample) ;; *) exit 2;; esac
d=/tmp/$tag
mkdir "$d" || exit 2
child=''
sample_pid=''
cleanup() {
    for p in "$child" "$sample_pid"; do
        [ -z "$p" ] || kill -TERM "$p" 2>/dev/null
    done
}
trap cleanup 0
trap 'cleanup; exit 143' TERM INT
finish() {
    status=$1
    echo "collector_exit=$status" >> "$d/summary.txt"
    tar -C /tmp -czf "/tmp/$tag.tgz" "$tag" || exit 1
    curl -fsS --connect-timeout 5 -m 60 -T "/tmp/$tag.tgz" "$H/up/$tag.tgz" || exit 1
    echo "uploaded $tag.tgz"
    exit "$status"
}
{
    date -u +%Y-%m-%dT%H:%M:%SZ
    echo "sampling=$sampling"
    echo 'scope=self-authored fixture and afplay result; inspect the host WAV separately for PCM content'
} > "$d/summary.txt"
cp "$0" "$d/collector.sh" || finish 11
shasum -a 256 "$0" > "$d/collector.sha256"
for name in audio-test-tone.wav audio-test-tone.wav.sha256; do
    curl -fsS --connect-timeout 5 -m 30 "$H/get/$name" -o "$d/$name" || finish 12
done
(cd "$d" && shasum -a 256 -c audio-test-tone.wav.sha256) > "$d/hash-check.txt" 2>&1 || finish 13
echo "play_started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
/usr/bin/afplay "$d/audio-test-tone.wav" > "$d/afplay.txt" 2>&1 &
child=$!
echo "afplay_pid=$child" >> "$d/summary.txt"
deadline=$(( $(date +%s) + 60 ))
if [ "$sampling" = sample ]; then
    echo "sample_started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
    /usr/bin/sample "$child" 10 20 -mayDie -file "$d/sample.txt" > "$d/sample.log" 2>&1 &
    sample_pid=$!
fi
timed_out=false
while kill -0 "$child" 2>/dev/null ||
      { [ -n "$sample_pid" ] && kill -0 "$sample_pid" 2>/dev/null; }; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        timed_out=true
        cleanup
        sleep 1
        for p in "$child" "$sample_pid"; do
            [ -z "$p" ] || kill -KILL "$p" 2>/dev/null
        done
        break
    fi
    sleep 1
done
wait "$child"
play_status=$?
child=''
sample_status=not_run
if [ -n "$sample_pid" ]; then
    wait "$sample_pid"
    sample_status=$?
    sample_pid=''
fi
{
    echo "afplay_exit=$play_status"
    echo "sample_exit=$sample_status"
    echo "timeout=$timed_out"
    echo "play_finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >> "$d/summary.txt"
if [ -f "$d/sample.txt" ]; then
    awk '
      /^Call graph:/ { graph=1; next }
      /^Total number in stack/ { graph=0 }
      graph && /Thread_[0-9]+/ { threads++ }
      graph && /\(in / { frames++ }
      END { printf "thread_rows=%d\nstack_frame_rows=%d\nstack_acquired=%s\n",
            threads,frames,(threads>0 && frames>0 ? "true" : "false") }
    ' "$d/sample.txt" >> "$d/summary.txt"
else
    echo 'stack_acquired=false' >> "$d/summary.txt"
fi
[ "$timed_out" = false ] || finish 124
finish "$play_status"
