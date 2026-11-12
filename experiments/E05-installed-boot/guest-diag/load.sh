#!/bin/sh
# Synthetic guest CPU load: sh load.sh COUNT SECONDS. Starts COUNT `yes` loops at
# the shell's default priority and stops them after SECONDS.
n=${1:-8}
s=${2:-180}
pids=
i=0
while [ "$i" -lt "$n" ]; do
  yes > /dev/null &
  pids="$pids $!"
  i=$((i + 1))
done
( sleep "$s"; kill $pids ) > /dev/null 2>&1 &
echo "load $n for ${s}s:$pids"
