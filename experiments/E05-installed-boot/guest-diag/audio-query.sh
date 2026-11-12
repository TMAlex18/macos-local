#!/bin/sh
# Usage: sh audio-query.sh TAG [query|load-only|codecs] [plain|sample]
# The self-authored process survives the lookup; sampled timings are interventions.
tag=$1
mode=$2
sampling=$3
[ -n "$mode" ] || mode=query
[ -n "$sampling" ] || sampling=plain
[ -n "$H" ] || H=http://10.0.2.2:18769
case "$tag" in ''|*[!A-Za-z0-9_-]*) exit 2;; esac
case "$mode" in query|load-only|codecs) ;; *) exit 2;; esac
case "$sampling" in plain|sample) ;; *) exit 2;; esac
d=/tmp/$tag
mkdir "$d" || exit 2
probe_pid=''
sample_pid=''
receipt_pid=''
stop_children() {
    for p in "$probe_pid" "$sample_pid" "$receipt_pid"; do
        [ -z "$p" ] || kill -TERM "$p" 2>/dev/null
    done
}
finish() {
    wrapper_status=$1
    echo "wrapper_exit=$wrapper_status" >> "$d/summary.txt"
    tar -C /tmp -czf "/tmp/$tag.tgz" "$tag" || exit 1
    curl -fsS --connect-timeout 5 -m 120 -T "/tmp/$tag.tgz" "$H/up/$tag.tgz" || exit 1
    echo "uploaded $tag.tgz"
    exit "$wrapper_status"
}
trap stop_children 0
trap 'stop_children; finish 143' TERM INT
{
    date -u +%Y-%m-%dT%H:%M:%SZ
    echo "mode=$mode sampling=$sampling"
    echo 'scope=ordinary standalone process, not the Safari GPU sandbox'
} > "$d/summary.txt"
shasum -a 256 "$0" > "$d/wrapper.sha256"
if ! curl -fsS --connect-timeout 5 -m 30 "$H/get/audio-query" -o "$d/audio-query" ||
   ! curl -fsS --connect-timeout 5 -m 30 "$H/get/audio-query.sha256" -o "$d/audio-query.sha256"; then
    echo 'download_failed=true' >> "$d/summary.txt"
    finish 12
fi
if ! (cd "$d" && shasum -a 256 -c audio-query.sha256) > "$d/hash-check.txt" 2>&1; then
    finish 13
fi
chmod 700 "$d/audio-query" || finish 13
if ! /usr/bin/codesign --verify --strict --verbose=2 "$d/audio-query" > "$d/codesign.txt" 2>&1; then
    echo 'codesign_verification_failed=true' >> "$d/summary.txt"
    finish 14
fi
"$d/audio-query" "$mode" > "$d/probe.jsonl" 2> "$d/probe-stderr.txt" &
probe_pid=$!
echo "probe_pid=$probe_pid" >> "$d/summary.txt"
deadline=$(( $(date +%s) + 240 ))
ready=false
while kill -0 "$probe_pid" 2>/dev/null; do
    if grep -q '"phase":"READY"' "$d/probe.jsonl"; then
        ready=true
        break
    fi
    [ "$(date +%s)" -lt "$deadline" ] || break
    sleep 1
done
{
    echo "ready=$ready"
    echo "ready_observed_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >> "$d/summary.txt"
if [ "$ready" = true ]; then
    cp "$d/probe.jsonl" "$d/ready.jsonl"
    # The receipt upload must not consume the probe's 15-second lead-in.
    curl -fsS --connect-timeout 3 -m 10 -T "$d/ready.jsonl" "$H/up/$tag-ready.jsonl" \
        > "$d/ready-upload.log" 2>&1 &
    receipt_pid=$!
    if [ "$sampling" = sample ]; then
        echo "sample_started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
        sample "$probe_pid" 45 20 -mayDie -file "$d/sample.txt" > "$d/sample.log" 2>&1 &
        sample_pid=$!
    fi
fi
timed_out=false
while kill -0 "$probe_pid" 2>/dev/null ||
      { [ -n "$sample_pid" ] && kill -0 "$sample_pid" 2>/dev/null; }; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        timed_out=true
        stop_children
        sleep 1
        for p in "$probe_pid" "$sample_pid"; do
            [ -z "$p" ] || kill -KILL "$p" 2>/dev/null
        done
        break
    fi
    sleep 1
done
wait "$probe_pid"
probe_status=$?
probe_pid=''
sample_status=not_run
if [ -n "$sample_pid" ]; then
    wait "$sample_pid"
    sample_status=$?
    sample_pid=''
fi
if [ -n "$receipt_pid" ]; then wait "$receipt_pid"; receipt_pid=''; fi
{
    echo "probe_exit=$probe_status"
    echo "sample_exit=$sample_status"
    echo "outer_timeout=$timed_out"
    echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
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
[ "$probe_status" -eq 0 ] || finish "$probe_status"
grep -q '"phase":"DONE"' "$d/probe.jsonl" || finish 1
if [ "$sampling" = sample ]; then
    [ "$sample_status" = 0 ] && grep -q '^stack_acquired=true$' "$d/summary.txt" || finish 1
fi
finish 0
