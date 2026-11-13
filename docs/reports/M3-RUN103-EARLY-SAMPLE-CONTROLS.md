# M3 Run103 — early sampling controls and guest shutdown

Run `m3-gpu-window-sample-20261106-103` used the existing 4 GiB,
eight-vCPU, Intel native-window, QARMA5/PAC memo profile. A separate user
service owned the runner with a 3600-second observation window. Normal
installed macOS, password login, the native desktop, and actual guest HTTP
GET/PUT were verified. This is a functional diagnostic, not a performance
benchmark.

## Earlier triggers did not establish initialization-stack coverage

The fresh `r103window` trial confirmed Safari exit before relaunch, matched
Safari ownership of GPU 886, and invoked a 45-second sample at 20 ms intervals
with `-mayDie`. The report records Date/Time 17:37:05.629 UTC and sample exit
0, but its Call graph has **zero nonblank lines**. Metadata, image lists and
a successful sampler exit do not establish that any stack was collected.
The watcher recorded four GPU watchdog termination requests; the native
capture shows a white page/reload banner, not the authored RGB page.
Termination requests alone do not prove when each target actually exited.

The separate `r103burst` diagnostic triggered at the first GPU constructor
event and checked Safari ownership afterward in the same retained log:

| GPU 1788 event | UTC | WebKit log line |
|---|---|---:|
| Constructor | 17:58:09.182 | 116 |
| Safari ownership confirmed | 17:58:29.659 | 219 |
| Connection initialization | 17:58:37.196 | 599 |
| Watchdog termination requested | 17:58:40.182 | 666 |

The constructor event preceded ownership by 20.477 seconds. Both sample
segments hit their requested 12-second deadlines, returned 124 and produced
no reports or stack rows. The sampler wrapper ran from 17:58:10 to 17:58:41:
**31 seconds**, not a hard 30-second bound. Guest scheduling, deadline polling
and termination contribute to that overrun. Six watchdog termination requests
were recorded in this trial. An earlier trigger therefore closed the trigger
timing gap but did not produce initialization stacks.

## Controls distinguish sampler operation from target execution

- A persistent owned `zsh` process produced a nonempty one-second sample:
  41 main-thread samples in `__sigsuspend`. The sampling path can return
  stacks for a stable process.
- GPU 1030, GPU 1123 and GPU 1279 produced 500, 400 and 503 main-thread samples
  about ten minutes after the watchdog events. Their respective paths include
  Opus registration/AudioComponent registry deserialization, rendering-backend
  allocation, and VP9/Metal plugin loading/dyld lookup. Each report repeats
  the same leaf/PC; two `ps` observations ten seconds apart show no CPU-time
  increase. These are retained stack states, **not measurements of ongoing
  initialization work or its cost**.
- The old three PIDs were absent after a later Safari quit/relaunch. Their
  earlier Mach suspension state remains unknown. At 18:07 UTC, launchd listed
  a new Safari-owned GPU 2129 as running, never exited and not start-suspended,
  but Mach inspection returned `0x5` even with sudo. This does not classify
  the earlier PIDs or prove their termination timing.

These limits motivated the separately runnable Opus `AudioComponentFindNext`
probe subsequently executed in [Run104](M3-RUN104-ISOLATED-CODEC-QUERIES.md),
which retains wall/CPU timing and actual query-window stacks. Those ordinary
process results remain separate from Safari GPU initialization. Current work
is tracked in [STATUS](../STATUS.md).

## Lifecycle

At 18:09:08 UTC the owned QMP connection recorded `SHUTDOWN` with
`guest=true` and `reason=guest-shutdown`; QEMU exited 0 and was reaped without
a host quit request. The file service stopped, exited 0 and was reaped.
The runner still reports `collection_success=false` and exits 1 because its
final screen capture was unavailable after the natural guest exit;
`errors=[]`. Guest-origin shutdown and collector success are separate.
**Full filesystem cleanliness remains unproven.** No VM remains active.

The constructor-trigger, post-hoc ownership, empty-versus-real-stack, target
exit, timeout and archive paths passed the existing 18-test offline watcher
suite. This reuses the delivery worker's completed validation; it is not
another test run and does not establish a hard guest deadline or Safari
stability.

Details and source locators are in
`application-observation.json`,
`results.json`,
`qmp-lifecycle.json`,
and `guest-uploads/r103window.tgz`, `r103burst.tgz`, `r103stable.tgz`,
`r103-shell.txt`, `r103-state.txt`, `r103-procinfo.txt` and
`r103-current-procinfo.txt` in the same run directory.
