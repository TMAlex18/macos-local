#!/bin/sh
# Fresh Safari launch plus the existing bounded GPU watcher; no system settings changed.
H=${H:-http://10.0.2.2:18769}
tag=${1:?tag required}
case "$tag" in *[!A-Za-z0-9_-]*|'') exit 2;; esac
url=${2:-file:///tmp/$tag.html}
# Select only the new fixed local WebGL resource. Other URLs retain their
# existing behavior; this is not a new general URL permission or restriction.
select_fixture_url() {
    fixture_name=render.html
    webgl_probe_tag=''
    case "$1" in
        "$H/get/webgl-probe.html"|"$H/get/webgl-probe.html#"*|"$H/get/webgl-probe.html?"*)
            case "$1" in "$H/get/webgl-probe.html#"*) ;; *) return 2;; esac
            webgl_probe_tag=${1#"$H/get/webgl-probe.html#"}
            (
                LC_ALL=C; export LC_ALL
                case "$webgl_probe_tag" in ''|[!A-Za-z0-9]*|*[!A-Za-z0-9_-]*) exit 2;; esac
                [ "${#webgl_probe_tag}" -le 48 ]
            ) || return 2
            fixture_name=webgl-probe.html
            ;;
    esac
    return 0
}
select_fixture_url "$url" || { echo 'invalid fixed WebGL URL or fragment tag' >&2; exit 2; }
fixture_url=$H/get/$fixture_name
fixture_step=${fixture_name%.html}
mode=${3:-}
case "$mode" in ''|sample|sample-connection|sample-window|sample-burst|stackshot|stackshot-system|stackshot-system-noipc|stackshot-system-raw) ;; *) exit 2;; esac
if [ "$mode" = stackshot ] || [ "$mode" = stackshot-system ] || [ "$mode" = stackshot-system-noipc ] || [ "$mode" = stackshot-system-raw ]; then
    secs=${4:-120}
    case "$secs" in ''|*[!0-9]*) exit 2;; esac
    [ "$secs" -ge 1 ] && [ "$secs" -le 600 ] || exit 2
    [ "$(id -u)" != 0 ] || { echo 'Launch this helper as the ordinary login user, after sudo -v'; exit 2; }
    [ ! -e "/tmp/$tag" ] && [ ! -e "/tmp/$tag.tgz" ] || { echo 'tag already used'; exit 2; }
    mkdir "/tmp/$tag-launch-lock" || exit 2
    cp "$0" "/tmp/$tag-safari-probe-source.sh" || exit 1
    # Authentication belongs to the active ordinary-user Terminal. Never
    # request a password in a background process or open Safari from root.
    # This preflight log intentionally belongs to the ordinary user.
    # shellcheck disable=SC2024
    if ! sudo -n /usr/bin/true > "/tmp/$tag-sudo-preflight.txt" 2>&1; then
        cat "/tmp/$tag-sudo-preflight.txt"
        echo 'sudo_preflight=failed; run sudo -v in this Terminal before using a new tag'
        curl -fsS --connect-timeout 5 -m 20 -T "/tmp/$tag-sudo-preflight.txt" "$H/up/$tag-sudo-preflight.txt"
        exit 2
    fi
fi
prep=/tmp/$tag-prepare.txt
prep_child=''
prep_out=/tmp/$tag-prepare-current.stdout
prep_err=/tmp/$tag-prepare-current.stderr
prep_step() {
    prep_name=$1
    prep_limit=$2
    shift 2
    echo "prep_step=$prep_name argv=$* started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    "$@" > "$prep_out" 2> "$prep_err" &
    prep_child=$!
    echo "prep_child_pid=$prep_child"
    prep_deadline=$(( $(date +%s) + prep_limit ))
    prep_timeout=false
    while kill -0 "$prep_child" 2>/dev/null; do
        if [ "$(date +%s)" -ge "$prep_deadline" ]; then
            prep_timeout=true
            kill -TERM "$prep_child" 2>/dev/null
            sleep 1
            kill -KILL "$prep_child" 2>/dev/null
            break
        fi
        sleep 0.1
    done
    wait "$prep_child"
    prep_actual_status=$?
    echo "prep_step=$prep_name pid=$prep_child exit=$prep_actual_status timeout=$prep_timeout reaped=true"
    prep_child=''
    prep_stdout_present=false; prep_stderr_present=false
    [ ! -s "$prep_out" ] || prep_stdout_present=true
    [ ! -s "$prep_err" ] || prep_stderr_present=true
    cat "$prep_out" "$prep_err"
    rm -f "$prep_out" "$prep_err"
    [ "$prep_timeout" = false ] || return 124
    return "$prep_actual_status"
}
abort_preparation() {
    trap '' TERM INT HUP
    # The enclosing prepare function opened its log without O_APPEND. Switch
    # both descriptors before wait can print a signal diagnostic, so it cannot
    # overwrite an abort marker appended through a separate descriptor.
    exec >> "$prep" 2>&1
    echo "preparation_aborted=true signal=$1 stage=${prep_name:-initialization}" >> "$prep"
    if [ -n "$prep_child" ]; then
        kill -TERM "$prep_child" 2>/dev/null
        sleep 1
        kill -KILL "$prep_child" 2>/dev/null
        wait "$prep_child"
        echo "aborted_child_pid=$prep_child exit=$? reaped=true" >> "$prep"
        prep_child=''
    fi
    for partial in "$prep_out" "$prep_err"; do
        [ ! -f "$partial" ] || cat "$partial" >> "$prep"
    done
    rm -f "$prep_out" "$prep_err"
    echo 'SAFARI_EXIT_UNKNOWN reason=preparation-aborted' >> "$prep"
    curl -fsS --connect-timeout 5 -m 20 -T "$prep" "$H/up/$tag-prepare.txt" \
        > "/tmp/$tag-prepare-upload.log" 2>&1
    exit 143
}
query_safari() {
    prep_step safari-pgrep 3 pgrep -x Safari
    query_status=$?
    safari_state=unknown
    if [ "$query_status" -eq 1 ] && [ "$prep_stdout_present" = false ] && [ "$prep_stderr_present" = false ]; then
        safari_state=absent
        return 0
    fi
    if [ "$query_status" -eq 0 ] && [ "$prep_stdout_present" = true ] && [ "$prep_stderr_present" = false ]; then
        safari_state=present
        return 0
    fi
    echo "SAFARI_EXIT_UNKNOWN query_status=$query_status stderr_present=$prep_stderr_present"
    [ "$query_status" -ne 124 ] || return 124
    return 13
}
prepare_safari() {
    date -u +%Y-%m-%dT%H:%M:%SZ
    prep_step memory 5 sysctl hw.memsize
    prep_step vm-stat 5 vm_stat
    if ! prep_step "fetch-$fixture_step" 22 curl -fsS --connect-timeout 5 -m 20 "$fixture_url" -o "/tmp/$tag.html"; then
        echo "guest_files_preflight=failed endpoint=$fixture_url"
        return 12
    fi
    echo "guest_files_preflight=passed endpoint=$fixture_url"
    prep_step "hash-$fixture_step" 5 shasum -a 256 "/tmp/$tag.html" || return 12
    if ! prep_step fetch-watcher 22 curl -fsS --connect-timeout 5 -m 20 "$H/get/gpuwatch.sh" -o "/tmp/$tag-watch.sh"; then
        echo "guest_files_preflight=failed endpoint=$H/get/gpuwatch.sh"
        return 12
    fi
    echo "guest_files_preflight=passed endpoint=$H/get/gpuwatch.sh"
    prep_step hash-watcher 5 shasum -a 256 "/tmp/$tag-watch.sh" || return 12
    query_safari || return $?
    if [ "$safari_state" = absent ]; then
        echo 'SAFARI_EXIT_CONFIRMED source=initial-pgrep-exit-1-no-diagnostics quit_skipped=true'
        return 0
    fi
    prep_step quit-safari 35 osascript -e 'with timeout of 30 seconds' \
        -e 'tell application "Safari" to quit' -e 'end timeout'
    echo "quit_exit=$?"
    quit_deadline=$(( $(date +%s) + 30 ))
    while :; do
        query_safari || return $?
        if [ "$safari_state" = absent ]; then
            echo 'SAFARI_EXIT_CONFIRMED source=pgrep-exit-1-no-diagnostics'
            return 0
        fi
        if [ "$(date +%s)" -ge "$quit_deadline" ]; then
            echo SAFARI_QUIT_PENDING
            return 1
        fi
        sleep 1
    done
}
trap 'abort_preparation TERM' TERM
trap 'abort_preparation INT' INT
trap 'abort_preparation HUP' HUP
prepare_safari > "$prep" 2>&1
prep_status=$?
trap - TERM INT HUP
cat "$prep"
curl -fsS --connect-timeout 5 -m 20 -T "$prep" "$H/up/$tag-prepare.txt" || exit 1
[ "$prep_status" -eq 0 ] || exit "$prep_status"
if [ "$mode" = stackshot ] || [ "$mode" = stackshot-system ] || [ "$mode" = stackshot-system-noipc ] || [ "$mode" = stackshot-system-raw ]; then
    launch=/tmp/$tag-launch.txt
    launch_status=12
    echo "launcher_uid=$(id -u) started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$launch"
    printf '%s\n' "requested_url=$url seconds=$secs mode=$mode" >> "$launch"
    # The enclosing ordinary-user shell records completion. kill -0 on the
    # root child can return EPERM, so it is not a readiness/liveness test here.
    (
        sudo -n /usr/bin/env H="$H" /bin/sh "/tmp/$tag-watch.sh" "$tag" "$secs" - "$mode"
        echo "$?" > "/tmp/$tag-root-status.txt"
    ) > "/tmp/$tag-root-launch.log" 2>&1 </dev/null &
    watch_pid=$!
    trap 'echo launcher_interrupted=true >> "$launch"; wait "$watch_pid"; exit 143' TERM INT HUP
    ready_deadline=$(( $(date +%s) + 45 ))
    while [ ! -f "/tmp/$tag/watcher-ready.txt" ] && [ ! -f "/tmp/$tag-root-status.txt" ] &&
          [ "$(date +%s)" -lt "$ready_deadline" ]; do sleep 0.1; done
    if [ -f "/tmp/$tag/watcher-ready.txt" ] && [ ! -f "/tmp/$tag-root-status.txt" ]; then
        cat "/tmp/$tag/watcher-ready.txt" >> "$launch"
        echo "safari_open_started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) uid=$(id -u)" >> "$launch"
        open -a Safari "$url" >> "$launch" 2>&1
        launch_status=$?
        echo "safari_open_exit=$launch_status finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$launch"
    else
        echo 'safari_open_skipped=true reason=root-watcher-not-ready' >> "$launch"
    fi
    # Root owns bounded termination and reaping, including late triggers. The
    # ordinary-user launcher only waits, and never kills a root sampler.
    wait "$watch_pid"
    trap - TERM INT HUP
    watch_status=$(cat "/tmp/$tag-root-status.txt")
    [ "$watch_status" -ne 0 ] || watch_status=$launch_status
    curl -fsS --connect-timeout 5 -m 20 -T "/tmp/$tag-root-launch.log" "$H/up/$tag-root-launch.log" || exit 1
    curl -fsS --connect-timeout 5 -m 20 -T "$launch" "$H/up/$tag-launch.txt" || exit 1
else
    sh "/tmp/$tag-watch.sh" "$tag" 120 "$url" "$mode"
    watch_status=$?
fi
post=/tmp/$tag-after.txt
{
    date -u +%Y-%m-%dT%H:%M:%SZ
    echo "watcher_exit=$watch_status"
    sysctl hw.memsize
    vm_stat
} > "$post" 2>&1
curl -fsS --connect-timeout 5 -m 20 -T "$post" "$H/up/$tag-after.txt" || exit 1
exit "$watch_status"
