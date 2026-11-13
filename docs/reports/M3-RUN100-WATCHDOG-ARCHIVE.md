# M3 Run100 — live watcher archives and Safari watchdog reproduction

Run `m3-watcher-http-20261106-100` used the existing promoted QEMU binary
`0efae78918564459ce8449f3e698271daba6861215c3e018b2a83b2034269d34`,
4 GiB RAM, eight vCPUs, Intel native presentation, QARMA5/PAC memo,
exact 24 MHz, exact half blend, 30 Hz diagnostic refresh and user NAT.
It reached normal macOS, password login, the native desktop and an actual
Terminal echo. This is a functional diagnostic, not a performance benchmark.

## File service and watcher integration

The new `--guest-files` option started a separately owned host process before
QEMU, verified exact fixture/script GET bytes and a PUT roundtrip, and retained
HTTP request logs. It removed the separate manual-service dependency that
blocked Run99. Host readiness and actual guest connectivity remain separate:
this run subsequently fetched scripts and returned three real guest archives.

`r100smoke.tgz` (806 bytes) contains a bounded WebKit log and process-probe
timeout markers. Its separate launcher-receipt upload failed with a curl
resolving timeout; the watcher itself continued and uploaded its archive.
The missing receipt is not treated as absence of execution.

## Fresh Safari without stack sampling

`r100fresh-prepare.txt` records `SAFARI_EXIT_CONFIRMED`, guest
`hw.memsize=4284481536`, the exact authored 185-byte HTML hash and the watcher
hash. `r100fresh.tgz` (82,822 bytes) contains 3,406 WebKit log lines.
Ordinary watcher mode invoked neither `sample` nor `spindump`.

Safari recorded eight GPU watchdog terminations and three WebProcess
termination events with reason `GPUProcessCrashedTooManyTimes`. Native captures
`r100-fresh-render.png` and `r100-fresh-final.png` show a white page with the
automatic problem-reload banner; no RGB page success is claimed.

The first Safari-owned GPU (PID 934) has this guest-local log sequence:

| Event | Time | Archive log line |
|---|---|---:|
| Connection processing starts | 02:19:02.670 | 598 |
| AudioComponentRegistrar connection | 02:19:04.652 | 662 |
| Safari decides GPU is unresponsive | 02:19:05.654 | 665 |
| WebContent receives initialization reply | 02:19:06.093 | 718 |
| WebContent connection closes | 02:19:06.094 | 719 |

The reply reached WebContent 0.439 seconds after the termination decision.
This does not identify when the separate UI-process reply was sent or received,
nor prove that audio initialization is the dominant cost. The repeated loss
of a GPU process while initialization is pending is directly observable.

The 197-second pre/post snapshot interval recorded 55,061 compressions
(860.328 MiB of 16 KiB pages), 39,185 decompressions (612.266 MiB), and no
swap-in/out increments. These are whole-guest activity counters, not proof of
RAM causality or a per-process memory attribution.

## Connection-triggered diagnostic

A separate `r100conn` trial used the new `sample-connection` mode. Its same-log
stream parser identified Safari ownership and triggered once for GPU 1487's
02:30:07.602 connection event; guest observation was recorded at 10:30:08 UTC
and host received the trigger upload at 10:30:12 UTC.

GPU 1487 was judged unresponsive at 02:30:10.681; WebContent received the
initialization reply at 02:30:11.732. The sampler returned 255 at approximately
10:30:17 UTC because its target was already gone. The 32,980-byte archive
contains the trigger and error log, but **no sample stack report**. Correct
event matching therefore did not establish sample coverage.

The actual guest `sample -h` output is retained as `r100-sample-help.txt`.
It documents duration, sampling interval, `-wait` and `-mayDie`. The next
diagnostic uses the earlier successful Safari GPU ownership assertion to start
a longer, lower-frequency sample before connection processing. Its actual
sample time must still be checked against the connection window.

## Lifecycle and evidence

At 10:35:55 UTC the 1,800-second runner completed host-QMP teardown: QEMU
exited 0/reaped, collection succeeded, and the owned file service exited
0/reaped with port 18769 released. The attempted UI shutdown command was
rejected afterward because the VM was already gone. **Guest-origin shutdown
and full filesystem cleanliness are not established by this run.**

Raw results, hashes, exact served watcher versions and per-trial details are in
`application-observation.json`,
`results.json`
and `guest-uploads/` in the same run directory. Run100 resolves the missing
live watcher-archive boundary; Safari first-load stability and the cause of
initialization latency remain open.
