#!/bin/sh
# Start gpuwatch.sh without occupying the interactive Terminal session.
# Usage: sh start-gpuwatch.sh TAG SECONDS [URL|-] [sample|sample-connection|sample-window|sample-burst]
# For root stackshots plus a fresh ordinary-user Safari launch, use
# safari-probe.sh TAG URL stackshot|stackshot-system|stackshot-system-noipc|stackshot-system-raw [SECONDS] after sudo -v.
H=${H:-http://10.0.2.2:18769}
tag=${1:?tag required}
secs=${2:-120}
url=${3:--}
mode=${4:-}
case "$tag" in *[!A-Za-z0-9_-]*|'') exit 2;; esac
case "$mode" in ''|sample|sample-connection|sample-window|sample-burst) ;; *) exit 2;; esac
watch=/tmp/$tag-gpuwatch.sh
log=/tmp/$tag-gpuwatch-launch.log
printf 'started=%s seconds=%s url=%s mode=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$secs" "$url" "$mode" > "$log"

# Make the host-side file service a recorded precondition. A missing listener
# must be distinguishable from a watcher that ran and found no GPU process.
if ! curl -fsS --connect-timeout 5 -m 20 "$H/get/gpuwatch.sh" -o "$watch" >> "$log" 2>&1; then
  echo "guest_files_preflight=failed endpoint=$H/get/gpuwatch.sh" >> "$log"
  rm -f "$watch"
  exit 12
fi
echo "guest_files_preflight=passed endpoint=$H/get/gpuwatch.sh" >> "$log"
shasum -a 256 "$watch" >> "$log" || exit 1
chmod 700 "$watch" || exit 1
nohup sh "$watch" "$tag" "$secs" "$url" "$mode" >> "$log" 2>&1 </dev/null &
pid=$!
echo "gpuwatch_started pid=$pid tag=$tag seconds=$secs" >> "$log"
curl -fsS --connect-timeout 5 -m 20 -T "$log" "$H/up/$tag-started.txt" || exit 1
echo "gpuwatch_started pid=$pid tag=$tag seconds=$secs"
