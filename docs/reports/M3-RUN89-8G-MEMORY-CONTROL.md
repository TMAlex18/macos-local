# M3 Run89 — 8 GiB memory control

Run `m3-memory-8g-20261105-89` used the promoted main binary
`0efae78918564459ce8449f3e698271daba6861215c3e018b2a83b2034269d34` with
the Run88 profile held constant: Intel native window, eight vCPUs, QARMA5,
exact 24 MHz counter, PAC memo enabled, 30 Hz diagnostic refresh, exact half
blend and user-mode NAT. The only intended workload variable was fresh RAM
capacity: `--ram-gib 8`.

## Capacity and boot evidence

The live QEMU command contained both
`memory-backend-memfd,id=ram,size=8G,share=on` and `-m 8G`. The read-only boot
observer verified one owned RAM memfd of `8,589,934,592` bytes and preserved
the 256 MiB capture limit. Its boot-args candidates used the 8 GiB bound and
the normal-OS evidence recorded Darwin, PCI enumeration, the installed BSD
root and launchd PID 1 activity without a kernel panic.

Inside the guest, `sysctl` reported `hw.memsize: 8579448832` and `hw.ncpu: 8`.
The difference from the QEMU memfd is the platform's reserved area; this is
direct evidence that the guest saw the larger capacity. Two bounded snapshots
(`r89pre.txt`, `r89pre2.txt`) retained VM counters. Both reported zero swap
I/O; they are preparation snapshots, not a complete before/after pressure
experiment.

The native login and desktop reached the existing user session. The first
screen capture after the new page trial was an X11 client-bound native window,
not a host-root capture.

## Fresh Safari result

The first fresh trial was started by `safari-probe.sh` after confirming Safari
had exited. Its timeline was:

```
start 10:53:35 (guest local time), URL=http://10.0.2.2:18769/get/render.html
GPU PIDs 1676, 1688, 1714
end 10:55:48
```

The archived WebKit stream contains **zero**
`GPUProcessProxy::didBecomeUnresponsive` lines. It contains four WebProcess
unresponsive lines, including background `nsattributedstringagent` activity.
For the Safari page, the stream records a 185-byte resource, main-frame
commit/finish, `loadEvent=16.240`, and `firstMeaningfulPaint=19.396` seconds.
It also records GPU connection creation and AudioComponentRegistrar XPC
connections. The native capture
`native-r89-before-final.png` shows the authored HTML heading and red, green
and blue bars. Exact RGB counts in that full desktop capture are retained by
the image hash; this is a visual functional result, not a frame-rate result.

This is a positive single fresh-load observation at 8 GiB. It does not prove
that RAM is the root cause: there was no matched 4 GiB retry in this run, no
second fresh trial, and no post-trial VM counter snapshot. It also does not
show that audio initialization is irrelevant; the sample only records the
ordinary connection events after the page was already progressing.

## Lifecycle and limits

The guest later locked its session. Repeated unlock attempts reached the
password UI but the kernel log recorded AppleVPKeyStore `unlock failed (-5)`;
the existing password helper had worked at initial login, so this is retained
as a lock/unlock limitation rather than reclassified as a bad credential.
The controller was then interrupted for bounded cleanup. QEMU exited 0 and was
reaped, but `qmp-lifecycle.json` records `host_quit_requested=true` and
`guest_shutdown_observed=false`. Raw collection is failed and
`observation_completed=false`; no clean guest shutdown or clean filesystem is
claimed. The file service was stopped after QEMU exit.

The next discriminating run is a same-binary, same-profile 4 GiB fresh-load
control with the same memory snapshots and no diagnostic sample. If it fails
while this 8 GiB result repeats, capacity becomes a supported leading
hypothesis; a single success remains insufficient to claim causality.
