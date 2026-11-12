#!/bin/sh
# Follow WebKit GPU processes: sh gpuwatch.sh TAG SECONDS [URL|-] [sample|sample-connection|sample-window|sample-burst|stackshot|stackshot-system|stackshot-system-noipc|stackshot-system-raw]
# Opens URL in Safari (unless '-'), then once a second records each GPU process's
# threads (ps -M) and the WebKit/Safari process table. Stack sampling is opt-in
# through the sample mode; ordinary timing runs do not invoke sample/spindump.
# The WebKit log stream, timeline and any samples are uploaded as TAG.tgz.
H=${H:-http://10.0.2.2:18769}
tag=$1
secs=${2:-120}
url=${3:-https://www.iana.org/domains/reserved}
mode=${4:-}
case "$mode" in ''|sample|sample-connection|sample-window|sample-burst|stackshot|stackshot-system|stackshot-system-noipc|stackshot-system-raw) ;; *) exit 2;; esac
if [ "$mode" = stackshot ] || [ "$mode" = stackshot-system ] || [ "$mode" = stackshot-system-noipc ] || [ "$mode" = stackshot-system-raw ]; then
  case "$tag" in ''|*[!A-Za-z0-9_-]*) exit 2;; esac
  case "$secs" in ''|*[!0-9]*) exit 2;; esac
  [ "$secs" -ge 1 ] && [ "$secs" -le 600 ] || exit 2
  [ "$(id -u)" = 0 ] && [ "$url" = - ] || {
    echo 'stackshot requires root and URL=-; launch Safari separately as the login user' >&2
    exit 2
  }
  # Readiness is readable by the ordinary-user launcher, with no root GUI use.
  umask 022
fi
d=/tmp/$tag
mkdir "$d" || exit 1

# macOS process inspection can stop responding while a WebKit GPU process is
# wedged. Keep one bad observation from preventing the archive upload and the
# rest of the bounded run from completing.
bounded_exec() {
  limit=$1
  output=$2
  shift 2
  "$@" >> "$output" 2>&1 &
  child=$!
  deadline=$(( $(date +%s) + limit ))
  while kill -0 "$child" 2>/dev/null; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      kill -TERM "$child" 2>/dev/null
      sleep 1
      kill -KILL "$child" 2>/dev/null
      wait "$child" 2>/dev/null
      echo "timeout=true command=$* pid=$child" >> "$output"
      child=''
      return 124
    fi
    sleep 1
  done
  wait "$child"
  result=$?
  child=''
  return "$result"
}

stream_connection_events() {
  # exec keeps the parser's PID directly owned by the collector. Keep draining
  # and archiving after the first trigger; exiting here would break log stream.
  exec awk -v archive="$1" -v trigger="$2" -v raw_trigger="$3" \
    -v sample_mode="${4:-sample-connection}" -v ready_nonce="${5:-}" -v ready_file="${6:-}" '
    function fire(pid, event) {
      fired = 1
      printf "%s\n%s\n%s\n", pid, owner[pid], event > raw_trigger
      close(raw_trigger)
      printf "%s\n%s\n%s\n", pid, owner[pid], event > trigger
      close(trigger)
    }
    {
      print $0 >> archive; fflush(archive)
      # Run110 r110canary verified this actual compact event shape. The
      # Filtering line also contains the nonce, so it must never satisfy this.
      if (ready_nonce != "" && $1 ~ /^[0-9]+-[0-9]+-[0-9]+$/ &&
          $2 ~ /^[0-9]+:[0-9]+:[0-9]+\.[0-9]+$/ && $4 ~ /^log\[[0-9]+:[^]]+\]$/) {
        suffix="[local.e05.gpuwatch:readiness] " ready_nonce
        if (length($0)>=length(suffix) && substr($0,length($0)-length(suffix)+1)==suffix) {
          print $0 > ready_file; close(ready_file)
        }
      }
      if ($0 ~ /ProcessAssertion::acquireSync/ &&
          $0 ~ /GPUProcess (Foreground|Background) Assertion/ &&
          match($0, /for process with PID=[0-9]+/)) {
        pid = substr($0, RSTART, RLENGTH); sub(/^.*=/, "", pid)
        safari[pid] = ($4 ~ /^Safari\[[0-9]+:/)
        owner[pid] = $0
        if ((sample_mode == "sample-window" || sample_mode == "stackshot") &&
            !fired && safari[pid] &&
            $0 ~ /Successfully took RBS assertion/)
          fire(pid, $0)
      }
      if (sample_mode == "sample-burst" && !fired &&
          $4 ~ /^com\.apple\.WebKit\.GPU\[[0-9]+:[^]]+\]$/ &&
          $0 ~ /GPUProcess::GPUProcess:/) {
        pid = $4; sub(/^[^[]*\[/, "", pid); sub(/:.*/, "", pid)
        # Constructor discovery is not Safari ownership. Keep the subsequent
        # stream so the same PID can be attributed after collection.
        owner[pid] = "pending-posthoc-Safari-ownership"
        fire(pid, $0)
      }
      if (sample_mode == "sample-connection" && !fired &&
          $4 ~ /^com\.apple\.WebKit\.GPU\[[0-9]+:/ &&
          $0 ~ /GPUProcess::createGPUConnectionToWebProcess:/) {
        pid = $4; sub(/^[^[]*\[/, "", pid); sub(/:.*/, "", pid)
        if (safari[pid]) fire(pid, $0)
      }
    }
  '
}

sample_stack_counts() {
  # Metadata, an exit code and Binary Images are not collected stack frames.
  awk '
    /^Call graph:/ { graph=1; next }
    /^Total number in stack/ { graph=0 }
    graph {
      line=$0; sub(/^[[:space:]+!:|]*/, "", line)
      if (line ~ /^[0-9]+[[:space:]]+Thread_[0-9]+/) threads++
      else if (line ~ /^[0-9]+[[:space:]]+/ &&
               (line ~ /\(in / || line ~ /\[0x[0-9a-fA-F]+\]/)) frames++
    }
    END {
      printf "thread_rows=%d\nstack_frame_rows=%d\nstack_acquired=%s\n", \
        threads, frames, (threads>0 && frames>0 ? "true" : "false")
    }
  ' "$1"
}

sample_burst() {
  burst_gpu=$1
  burst_started=$(date +%s)
  burst_deadline=$(( burst_started + 30 ))
  burst_segment=1
  while [ "$burst_segment" -le 4 ]; do
    if ! kill -0 "$burst_gpu" 2>/dev/null; then
      echo "$(date -u +%H:%M:%S) sample-burst-stop gpu=$burst_gpu reason=target-gone" >> "$d/timeline.txt"
      break
    fi
    # Reserve the existing bounded_exec termination grace within the total
    # budget. Recompute from wall time before each independently saved segment.
    burst_remaining=$(( burst_deadline - $(date +%s) - 1 ))
    if [ "$burst_remaining" -le 0 ]; then
      echo "$(date -u +%H:%M:%S) sample-burst-stop gpu=$burst_gpu reason=budget" >> "$d/timeline.txt"
      break
    fi
    burst_limit=$burst_remaining
    [ "$burst_limit" -le 12 ] || burst_limit=12
    burst_base="$d/sample-$burst_gpu-$burst_segment"
    set -- sample "$burst_gpu" 1 20 -mayDie -file "$burst_base.txt"
    printf '%s\n' "$@" > "$burst_base.argv"
    {
      echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo "segment=$burst_segment total_budget_seconds=30 segment_deadline_seconds=$burst_limit"
      echo "ownership=pending-posthoc-Safari-ownership"
    } > "$burst_base.timing.txt"
    bounded_exec "$burst_limit" "$burst_base.log" "$@"
    burst_status=$?
    {
      echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo "sample_exit=$burst_status"
      if [ -f "$burst_base.txt" ]; then
        grep '^Date/Time:' "$burst_base.txt"
        sample_stack_counts "$burst_base.txt"
      else
        echo 'thread_rows=0'
        echo 'stack_frame_rows=0'
        echo 'stack_acquired=false'
      fi
    } >> "$burst_base.timing.txt"
    echo "$(date -u +%H:%M:%S) sample-burst-segment gpu=$burst_gpu segment=$burst_segment exit=$burst_status" >> "$d/timeline.txt"
    burst_segment=$(( burst_segment + 1 ))
  done
}

sample_connection() {
  sample_limit=$1
  sampling_mode=${2:-sample-connection}
  read -r connection_gpu && read -r connection_owner && read -r connection_event || return 1
  child=''
  trigger_upload=''
  trap 'kill -TERM "$child" "$trigger_upload" 2>/dev/null; exit 143' TERM INT
  if [ "$sampling_mode" = sample-burst ]; then
    set -- sample-burst "$connection_gpu"
  elif [ "$sampling_mode" = sample-window ]; then
    # Read symbols while the PID is alive, then retain a low-frequency window
    # spanning the later connection event. This is explicitly diagnostic.
    set -- sample "$connection_gpu" 45 20 -mayDie -file "$d/sample-$connection_gpu.txt"
  else
    set -- sample "$connection_gpu" 1 -file "$d/sample-$connection_gpu.txt"
  fi
  printf '%s\n' "$@" > "$d/sample-$connection_gpu.argv"
  {
    echo "guest_observed_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "gpu_pid=$connection_gpu mode=$sampling_mode diagnostic-intervention"
    echo "sample_argv_file=sample-$connection_gpu.argv sample_deadline_seconds=$sample_limit"
    printf 'owner_log=%s\ntrigger_log=%s\n' "$connection_owner" "$connection_event"
    echo 'host_time_source=guest_files HTTP PUT receipt; guest script cannot read host clock'
  } > "$d/connection-trigger.txt"
  echo "$(date -u +%H:%M:%S) $sampling_mode-start gpu=$connection_gpu diagnostic-intervention" >> "$d/timeline.txt"
  # The host HTTP access log timestamps receipt independently. Upload and the
  # bounded sample run separately from the parser, so neither delays log reads.
  curl -fsS --connect-timeout 3 -m 10 -T "$d/connection-trigger.txt" \
    "$H/up/$tag-connection-trigger.txt" > "$d/connection-trigger-upload.log" 2>&1 &
  trigger_upload=$!
  if [ "$sampling_mode" = sample-burst ]; then
    sample_burst "$connection_gpu"
    sample_status=$?
  else
    bounded_exec "$sample_limit" "$d/sample-$connection_gpu.log" "$@"
    sample_status=$?
  fi
  child=''
  echo "$(date -u +%H:%M:%S) $sampling_mode-finish gpu=$connection_gpu exit=$sample_status" >> "$d/timeline.txt"
  if [ -f "$d/sample-$connection_gpu.txt" ]; then
    grep '^Date/Time:' "$d/sample-$connection_gpu.txt" >> "$d/connection-trigger.txt"
  fi
  wait "$trigger_upload"
  trigger_upload=''
}

stackshot_counts() {
  # Restrict counts to the requested GPU PID, never the healthy anchor. Keep
  # time ranges and raw report; row counts cannot prove initialization coverage.
  awk -v pid="$2" '
    !in_processes && /^(Date\/Time:|End time:|Duration:|Steps:)/ { print }
    /^Process:/ {
      in_processes=1
      target=($0 ~ ("\\[" pid "\\]$") && $0 ~ /^Process:[[:space:]]+com\.apple\.WebKit\.GPU[[:space:]]/)
      graph=target
      if (target) { found++; print }
      next
    }
    target && /^(Path:|UUID:|UID:|Time Since Fork:|End time:|Num samples:|Num threads:)/ { print }
    /^[[:space:]]*Binary Images:|^Kextstat:/ { graph=0 }
    graph && /^[[:space:]]+Thread [^[:space:]]+/ { threads++ }
    graph && /^[[:space:]]+\*?[0-9]+[[:space:]]+/ && /\[0x[0-9a-fA-F]+\]/ { frames++ }
    END {
      printf "target_process_sections=%d\nthread_rows=%d\nstack_frame_rows=%d\n", found, threads, frames
      printf "target_stack_acquired=%s\n", (found==1 && threads>0 && frames>0 ? "true" : "unknown")
      print "safari_initialization_coverage=unknown; correlate report times, target frames and target-events.txt"
    }
  ' "$1"
}

stackshot_task_index() {
  # Post-collection identity aid only: retain each task's real thread IDs and
  # sample ranges, never guess which executable implements a Mach service.
  awk '
    /^Process:/ { processes=1; print; next }
    /^Kextstat:/ { exit }
    processes && /^(Path:|Identifier:|UID:|Num samples:|Num threads:|Time Since Fork:)/ { print }
    processes && /^[[:space:]]+Thread / { print }
  ' "$1"
}

stackshot_watch() {
  # This root shell directly owns every long-running child. In particular, a
  # late trigger can extend collection, but packaging starts only after reaping.
  spin_pid=''; anchor_pid=''; log_pid=''; parser_pid=''; canary_pid=''
  spin_started=false; spin_timeout=false; watch_status=0
  report_path="$d/spindump-report.txt"
  sampler_timelimit=90
  sampler_outer_seconds=120
  sampler_term_grace=1
  anchor_seconds=$((secs + 160))
  if [ "$mode" = stackshot-system-raw ]; then
    report_path="$d/spindump-raw.spindump"
    sampler_timelimit=270
    sampler_outer_seconds=300
    sampler_term_grace=10
    # Covers readiness, a last-moment trigger, generation and TERM save grace.
    anchor_seconds=$((secs + 370))
  fi
  stop_owned() {
    [ -n "$1" ] || return 0
    kill -TERM "$1" 2>/dev/null
    sleep 1
    kill -KILL "$1" 2>/dev/null
    wait "$1" 2>/dev/null
  }
  finish_spin() {
    [ -n "$spin_pid" ] || return 0
    wait "$spin_pid"
    spin_status=$?
    {
      echo "spindump_exit=$spin_status outer_timeout=$spin_timeout"
      echo "collector_finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } >> "$d/spindump-summary.txt"
    spin_pid=''
    [ "$spin_status" -eq 0 ] || watch_status=$spin_status
    [ "$spin_timeout" = false ] || watch_status=124
  }
  stop_raw_spin() {
    kill -TERM "$spin_pid" 2>/dev/null
    echo "spindump_term_requested_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) save_grace_seconds=$sampler_term_grace" >> "$d/spindump-summary.txt"
    save_deadline=$(( $(date +%s) + sampler_term_grace ))
    while kill -0 "$spin_pid" 2>/dev/null && [ "$(date +%s)" -lt "$save_deadline" ]; do sleep 0.1; done
    if kill -0 "$spin_pid" 2>/dev/null; then
      kill -KILL "$spin_pid" 2>/dev/null
      echo 'spindump_kill_sent=true' >> "$d/spindump-summary.txt"
    else
      echo 'spindump_kill_sent=false' >> "$d/spindump-summary.txt"
    fi
  }
  cleanup_stackshot() {
    stop_owned "$canary_pid"; canary_pid=''
    if [ -n "$spin_pid" ]; then
      if [ "$mode" = stackshot-system-raw ]; then
        stop_raw_spin
      else
        kill -TERM "$spin_pid" 2>/dev/null
        sleep 1
        kill -KILL "$spin_pid" 2>/dev/null
      fi
      finish_spin
    fi
    stop_owned "$anchor_pid"; anchor_pid=''
    # Closing the stream first lets awk drain the final ownership events.
    stop_owned "$log_pid"; log_pid=''
    stop_owned "$parser_pid"; parser_pid=''
    rm -f "$d/stackshot-stream.fifo"
    echo "owned_children_reaped_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/spindump-summary.txt"
  }
  trap cleanup_stackshot 0
  trap 'echo interrupted=true >> "$d/spindump-summary.txt"; watch_status=143; stackshot_interrupted=true' TERM INT HUP
  stackshot_interrupted=false
  cp "$0" "$d/collector.sh" || return 1
  printf '%s\n' sh "$0" "$tag" "$secs" - "$mode" > "$d/collector.argv"
  {
    echo "watcher_started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) uid=$(id -u) pid=$$"
    echo "mode=$mode diagnostic-intervention; not normal performance or watchdog timing"
    if [ "$mode" != stackshot ]; then
      echo 'collection_scope=system-wide process_selection=all-processes; posthoc GPU and service-thread correlation required'
    else
      echo 'collection_scope=target-and-anchor process_selection=onlyTarget+proc'
    fi
    if [ "$mode" = stackshot-system-noipc ] || [ "$mode" = stackshot-system-raw ]; then
      echo 'collector_daemon_ipc=noIPC'
      echo 'noIPC_scope=do not IPC out to any daemons; installed help says more expensive, but more reliable; not a performance guarantee'
    else
      echo 'collector_daemon_ipc=spindump-default'
    fi
    if [ "$mode" = stackshot-system-raw ]; then
      echo "report_format=spindump-binary-only report_file=spindump-raw.spindump symbolication=disabled; decoding is a separate operation"
    fi
    echo "trigger=first-successful-Safari-GPU-ownership-assertion"
    echo "requested_duration_seconds=10 interval_ms=100 processing_timelimit_seconds=$sampler_timelimit outer_sampler_seconds=$sampler_outer_seconds term_save_grace_seconds=$sampler_term_grace"
  } > "$d/spindump-summary.txt"
  spindump_path=$(command -v spindump) || { echo 'spindump_missing=true' >> "$d/spindump-summary.txt"; return 127; }
  log_path=$(command -v log) || return 127
  shasum -a 256 "$spindump_path" "$log_path" /bin/sleep > "$d/input-sha256.txt"
  /bin/sleep "$anchor_seconds" &
  anchor_pid=$!
  echo "anchor_pid=$anchor_pid parent=$$ uid=0 argv=/bin/sleep,$anchor_seconds started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/spindump-summary.txt"
  nonce=e05_gpuwatch_ready_${tag}_$$_$(date +%s)
  predicate="(subsystem BEGINSWITH \"com.apple.WebKit\" OR process BEGINSWITH \"com.apple.WebKit\") OR (subsystem == \"local.e05.gpuwatch\" AND category == \"readiness\" AND eventMessage == \"$nonce\")"
  echo "canary_nonce=$nonce" >> "$d/spindump-summary.txt"
  mkfifo "$d/stackshot-stream.fifo" || return 1
  stream_connection_events "$d/webkit-stream.log" "$d/stackshot-trigger-ready.txt" \
    "$d/connection-trigger-raw.txt" stackshot "$nonce" "$d/canary-observed.txt" < "$d/stackshot-stream.fifo" &
  parser_pid=$!
  set -- "$log_path" stream --level info --style compact --predicate "$predicate"
  printf '%s\n' "$@" > "$d/log-stream.argv"
  "$@" > "$d/stackshot-stream.fifo" 2> "$d/log-stream.stderr.txt" &
  log_pid=$!
  echo "log_pid=$log_pid parser_pid=$parser_pid" >> "$d/spindump-summary.txt"
  transport_deadline=$(( $(date +%s) + 10 ))
  while [ ! -s "$d/webkit-stream.log" ]; do
    if ! kill -0 "$log_pid" 2>/dev/null || ! kill -0 "$parser_pid" 2>/dev/null ||
       [ "$(date +%s)" -ge "$transport_deadline" ] || [ "$stackshot_interrupted" = true ]; then
      watch_status=12
      break
    fi
    sleep 0.1
  done
  # Match the real Run110 canary's schedule. Transport output only tells us
  # the FIFO/parser is attached; readiness requires the emitted event below.
  if [ "$watch_status" -eq 0 ]; then
    sleep 2
    set -- "$log_path" emit --type default --subsystem local.e05.gpuwatch \
      --category readiness --public "$nonce"
    printf '%s\n' "$@" > "$d/canary-emit.argv"
    echo "canary_emit_started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/spindump-summary.txt"
    "$@" > "$d/canary-emit.stdout.txt" 2> "$d/canary-emit.stderr.txt" &
    canary_pid=$!
    echo "canary_pid=$canary_pid" >> "$d/spindump-summary.txt"
    canary_deadline=$(( $(date +%s) + 5 ))
    canary_timeout=false
    while kill -0 "$canary_pid" 2>/dev/null; do
      if [ "$(date +%s)" -ge "$canary_deadline" ] || [ "$stackshot_interrupted" = true ]; then
        canary_timeout=true
        kill -TERM "$canary_pid" 2>/dev/null
        sleep 1
        kill -KILL "$canary_pid" 2>/dev/null
        break
      fi
      sleep 0.1
    done
    wait "$canary_pid"
    canary_status=$?
    canary_pid=''
    echo "canary_emit_exit=$canary_status timeout=$canary_timeout finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/spindump-summary.txt"
    [ "$canary_status" -eq 0 ] && [ "$canary_timeout" = false ] || watch_status=12
  fi
  ready_deadline=$(( $(date +%s) + 15 ))
  while [ "$watch_status" -eq 0 ] && [ ! -s "$d/canary-observed.txt" ]; do
    if ! kill -0 "$log_pid" 2>/dev/null || ! kill -0 "$parser_pid" 2>/dev/null ||
       [ "$(date +%s)" -ge "$ready_deadline" ] || [ "$stackshot_interrupted" = true ]; then
      watch_status=12
      break
    fi
    sleep 0.1
  done
  if [ "$watch_status" -eq 0 ]; then
    echo "watcher_ready=true uid=0 pid=$$ method=unified-log-canary nonce=$nonce observed_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$d/watcher-ready.tmp"
    mv "$d/watcher-ready.tmp" "$d/watcher-ready.txt"
  else
    echo 'watcher_ready=false reason=live-canary-not-confirmed' >> "$d/spindump-summary.txt"
  fi
  observe_end=$(( $(date +%s) + secs ))
  while [ "$watch_status" -eq 0 ] || [ -n "$spin_pid" ]; do
    now=$(date +%s)
    [ "$stackshot_interrupted" = false ] || break
    if [ "$spin_started" = false ] && [ -s "$d/stackshot-trigger-ready.txt" ]; then
      # raw is closed before the ready file is written; no ps/pgrep preflight.
      { read -r target_pid; read -r target_owner; read -r target_event; } < "$d/connection-trigger-raw.txt"
      case "$target_pid" in ''|*[!0-9]*) watch_status=2; break;; esac
      spin_started=true
      set -- "$spindump_path" "$anchor_pid" 10 100
      if [ "$mode" = stackshot ]; then
        set -- "$@" -onlyTarget -proc "$target_pid"
      fi
      if [ "$mode" = stackshot-system-noipc ] || [ "$mode" = stackshot-system-raw ]; then
        set -- "$@" -noIPC
      fi
      if [ "$mode" = stackshot-system-raw ]; then
        set -- "$@" -noText -noSymbolicate
      fi
      set -- "$@" -sampleWithoutTarget -timelimit "$sampler_timelimit" -noProcessingWhileSampling -timeline -o "$report_path"
      printf '%s\n' "$@" > "$d/spindump.argv"
      trigger_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
      {
        echo "target_pid=$target_pid guest_observed_utc=$trigger_utc"
        printf 'owner_log=%s\ntrigger_log=%s\n' "$target_owner" "$target_event"
      } > "$d/connection-trigger.txt"
      echo "collector_started_utc=$trigger_utc target_pid=$target_pid" >> "$d/spindump-summary.txt"
      "$@" > "$d/spindump.stdout.txt" 2> "$d/spindump.stderr.txt" &
      spin_pid=$!
      echo "spindump_pid=$spin_pid" >> "$d/spindump-summary.txt"
      spin_deadline=$(( now + sampler_outer_seconds ))
    fi
    if [ -n "$spin_pid" ]; then
      if ! kill -0 "$spin_pid" 2>/dev/null; then
        finish_spin
      elif [ "$now" -ge "$spin_deadline" ]; then
        spin_timeout=true
        if [ "$mode" = stackshot-system-raw ]; then
          stop_raw_spin
        else
          kill -TERM "$spin_pid" 2>/dev/null
          sleep 1
          kill -KILL "$spin_pid" 2>/dev/null
        fi
        finish_spin
      fi
    fi
    if ! kill -0 "$log_pid" 2>/dev/null || ! kill -0 "$parser_pid" 2>/dev/null; then
      echo 'log_stream_ended_early=true' >> "$d/spindump-summary.txt"
      watch_status=13
      break
    fi
    if [ "$now" -ge "$observe_end" ] && [ -z "$spin_pid" ]; then break; fi
    sleep 0.1
  done
  cleanup_stackshot
  trap - 0 TERM INT HUP
  if [ "$spin_started" = true ]; then
    awk -v pid="$target_pid" '$0 ~ ("GPU\\[" pid ":") || $0 ~ ("for process with PID=" pid "([^0-9]|$)") { print }' \
      "$d/webkit-stream.log" > "$d/target-events.txt"
    if [ "$mode" = stackshot-system-raw ]; then
      raw_bytes=0
      [ ! -f "$report_path" ] || raw_bytes=$(wc -c < "$report_path")
      {
        echo 'report_format=spindump-binary-only'
        echo "raw_file=spindump-raw.spindump raw_bytes=$raw_bytes"
        echo 'target_stack_acquired=unknown reason=raw-not-decoded'
        echo 'safari_initialization_coverage=unknown'
        echo 'raw_completeness=unverified; nonempty bytes are not decoded frames'
      } > "$d/stackshot-assessment.txt"
      if [ "$raw_bytes" -gt 0 ]; then
        shasum -a 256 "$report_path" > "$d/raw-sha256.txt"
      else
        echo 'raw_present=false' >> "$d/stackshot-assessment.txt"
        [ "$watch_status" -ne 0 ] || watch_status=15
      fi
    elif [ -f "$d/spindump-report.txt" ]; then
      stackshot_counts "$d/spindump-report.txt" "$target_pid" > "$d/stackshot-assessment.txt"
      if [ "$mode" != stackshot ]; then
        stackshot_task_index "$d/spindump-report.txt" > "$d/stackshot-task-index.txt"
        echo 'service_identity_and_wait_chain=unverified; correlate exact thread IDs and sample ranges with full report' >> "$d/stackshot-assessment.txt"
      fi
    else
      printf '%s\n' 'target_stack_acquired=unknown reason=report-missing' \
        'safari_initialization_coverage=unknown' > "$d/stackshot-assessment.txt"
    fi
  else
    printf '%s\n' 'target_stack_acquired=unknown reason=no-trigger' \
      'safari_initialization_coverage=unknown' > "$d/stackshot-assessment.txt"
  fi
  echo 'report_completeness=unverified; retain raw report and sampler errors, including outer_timeout even when spindump_exit=0' >> "$d/stackshot-assessment.txt"
  echo "watcher_finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) watcher_exit=$watch_status" >> "$d/spindump-summary.txt"
  # Keep a launch snapshot; the ordinary helper uploads its final launch record
  # separately after waiting, including failures before readiness.
  for suffix in prepare.txt launch.txt safari-probe-source.sh; do
    [ ! -f "/tmp/$tag-$suffix" ] || cp "/tmp/$tag-$suffix" "$d/$suffix"
  done
  tar -C /tmp -czf "/tmp/$tag.tgz" "$tag" || return 1
  archive_bytes=$(wc -c < "/tmp/$tag.tgz")
  if [ "$archive_bytes" -gt 33554432 ]; then
    echo "archive_upload=refused bytes=$archive_bytes limit=33554432; retained /tmp/$tag.tgz" >&2
    return 14
  fi
  curl -fsS --connect-timeout 5 -m 120 -T "/tmp/$tag.tgz" "$H/up/$tag.tgz" || return 1
  echo "uploaded $tag.tgz watcher_exit=$watch_status"
  return "$watch_status"
}

collect_window() {
  lp=''
  connection_parser=''
  connection_sampler=''
  sample_jobs=''
  child=''
  cleanup_collection() {
    kill -TERM "$lp" "$connection_parser" "$connection_sampler" "$child" 2>/dev/null
    for pair in $sample_jobs; do
      kill -TERM "${pair#*:}" 2>/dev/null
    done
  }
  trap cleanup_collection 0
  trap 'exit 143' TERM INT
  : > "$d/timeline.txt"
  stream_output="$d/webkit-stream.log"
  if [ "$mode" = sample-connection ] || [ "$mode" = sample-window ] || [ "$mode" = sample-burst ]; then
    mkfifo "$d/connection-stream.fifo" "$d/connection-trigger.fifo" || return 1
    stream_connection_events "$d/webkit-stream.log" "$d/connection-trigger.fifo" \
      "$d/connection-trigger-raw.txt" "$mode" < "$d/connection-stream.fifo" &
    connection_parser=$!
    sample_limit=20
    [ "$mode" != sample-window ] || sample_limit=80
    [ "$mode" != sample-burst ] || sample_limit=30
    sample_connection "$sample_limit" "$mode" < "$d/connection-trigger.fifo" &
    connection_sampler=$!
    stream_output="$d/connection-stream.fifo"
  fi
  log stream --level info --style compact \
    --predicate 'subsystem BEGINSWITH "com.apple.WebKit" OR process BEGINSWITH "com.apple.WebKit"' \
    > "$stream_output" 2>&1 &
  lp=$!
  sleep 3
  echo "start $(date -u +%H:%M:%S) url=$url" >> "$d/timeline.txt"
  [ "$url" = - ] || open -a Safari "$url"
  seen=' '
  sample_count=0
  pids_file="$d/gpu-pids.txt"
  ps_file="$d/ps-current.tmp"
  end=$(( $(date +%s) + secs ))
  while [ "$(date +%s)" -lt "$end" ]; do
    t=$(date -u +%H:%M:%S)
    : > "$pids_file"
    bounded_exec 3 "$pids_file" pgrep -f com.apple.WebKit.GPU
    inspect_status=$?
    if [ "$inspect_status" -eq 124 ]; then
      echo "$t pgrep-gpu-timeout" >> "$d/timeline.txt"
    fi
    for p in $(awk '$1 ~ /^[0-9]+$/ { print $1 }' "$pids_file"); do
      case "$seen" in
        *" $p "*) ;;
        *) seen="$seen$p "
           echo "$t gpu $p" >> "$d/timeline.txt"
           if [ "$mode" = sample ]; then
             if [ "$sample_count" -lt 3 ]; then
               echo "$t sample-start gpu=$p diagnostic-intervention" >> "$d/timeline.txt"
               sample "$p" 1 -file "$d/sample-$p.txt" > "$d/sample-$p.log" 2>&1 &
               sample_jobs="$sample_jobs $p:$!"
               sample_count=$((sample_count + 1))
             fi
           fi ;;
      esac
      echo "=== $t pid $p" >> "$d/threads.txt"
      bounded_exec 3 "$d/threads.txt" ps -M -p "$p"
      inspect_status=$?
      if [ "$inspect_status" -eq 124 ]; then
        echo "$t gpu $p ps-m-timeout" >> "$d/timeline.txt"
      fi
    done
    echo "=== $t" >> "$d/ps.txt"
    # Own ps directly, so its deadline kills/reaps the observer itself rather
    # than a pipeline shell. Filter completed output, retaining diagnostics.
    : > "$ps_file"
    bounded_exec 3 "$ps_file" ps -A -o pid,ppid,state,pri,pcpu,time,etime,comm
    inspect_status=$?
    awk 'tolower($0) ~ /webkit|safari|mtlcompiler/ || $1 !~ /^[0-9]+$/' \
      "$ps_file" >> "$d/ps.txt"
    rm -f "$ps_file"
    if [ "$inspect_status" -eq 124 ]; then
      echo "$t ps-all-timeout" >> "$d/timeline.txt"
    fi
    sleep 1
  done
  kill "$lp" 2> /dev/null
  wait "$lp" 2>/dev/null
  lp=''
  if [ -n "$connection_parser" ]; then
    wait "$connection_parser"
    connection_parser=''
    if [ ! -s "$d/connection-trigger-raw.txt" ]; then
      echo "$mode-no-trigger" >> "$d/timeline.txt"
      kill -TERM "$connection_sampler" 2>/dev/null
    fi
    wait "$connection_sampler" 2>/dev/null
    connection_sampler=''
    rm -f "$d/connection-stream.fifo" "$d/connection-trigger.fifo"
  fi
  for pair in $sample_jobs; do
    pair_pid=$(printf '%s' "$pair" | sed 's/^[^:]*://')
    pair_gpu=$(printf '%s' "$pair" | sed 's/:.*//')
    wait "$pair_pid"
    echo "sample-finish gpu=$pair_gpu exit=$?" >> "$d/timeline.txt"
  done
  sample_jobs=''
  sleep 5
  echo "end $(date -u +%H:%M:%S)" >> "$d/timeline.txt"
}

if [ "$mode" = stackshot ] || [ "$mode" = stackshot-system ] || [ "$mode" = stackshot-system-noipc ] || [ "$mode" = stackshot-system-raw ]; then
  stackshot_watch
  exit $?
fi

# The inner loop has per-command deadlines, but an unexpected shell or system
# utility stall must not suppress the archive forever. The parent always gets
# a chance to package the partial logs.
collect_window &
collector=$!
outer_allowance=45
[ "$mode" != sample-window ] || outer_allowance=105
outer_deadline=$(( $(date +%s) + secs + outer_allowance ))
while kill -0 "$collector" 2>/dev/null; do
  if [ "$(date +%s)" -ge "$outer_deadline" ]; then
    echo "outer-timeout=true collector=$collector" >> "$d/timeline.txt"
    kill -TERM "$collector" 2>/dev/null
    sleep 1
    kill -KILL "$collector" 2>/dev/null
    break
  fi
  sleep 1
done
wait "$collector" 2>/dev/null
tar -C /tmp -czf "/tmp/$tag.tgz" "$tag" || exit 1
curl -fsS --connect-timeout 5 -m 120 -T "/tmp/$tag.tgz" "$H/up/$tag.tgz" && echo "uploaded $tag.tgz"
