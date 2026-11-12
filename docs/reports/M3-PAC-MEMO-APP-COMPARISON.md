# M3 PAC memo application comparisons — Runs86–87 and Runs113–114

## Runs86–87: memo off/on decision

The candidate reduces the observed host CPU cost of PAC across all eight vCPUs, but it did not fix Safari's first-page failure. Keep `x-pauth-memo` default-off and available as an experimental option. A microbenchmark ratio or a smaller PAC share is not an application-speed result.

Both runs used binary SHA-256 `0efae78918564459ce8449f3e698271daba6861215c3e018b2a83b2034269d34`, Intel native presentation, 8 vCPUs, 4 GiB, QARMA5, exact 24 MHz, requested 30 Hz diagnostic refresh, exact half blend and user NAT. Run86 omitted the memo option; Run87 explicitly requested it. Original authentication and key/algorithm semantics remain unchanged; repeated pure ComputePAC results are cached only after full input comparison.

## Actual application results

| Observation | Run86: memo off | Run87: memo on |
|---|---|---|
| New Terminal command and network | Verified; HTTP/HTTPS200, certificate verify 0 | Verified; HTTP/HTTPS200, certificate verify 0 |
| First fresh Safari trial | 2 Safari GPU unresponsive termination requests, white page | 4 requests, white page/reload warning |
| Second fresh Safari trial | 1 Safari GPU request; other clients excluded | 6 Safari GPU requests, including 4 within the perf window |
| First-load stability established | No | No |
| Later native RGB image | First-load failure retained | RGB later appeared with reload warning; recovery only |
| Lifecycle | guest-origin shutdown 20:51:29 UTC, QEMU 0/reaped | guest-origin shutdown 21:47:35 UTC, QEMU 0/reaped |

The second off trial's GPU1480/1503/1509 belonged to `nsattributedstringagent`; they are not counted as Safari failures. A proxy's unresponsive/termination log is not necessarily the exact OS-process death time. Run87 retained a GPU PID in later ps samples after a termination request.

Per-run review: Run86, Run87.

## Aligned host CPU evidence

The useful profiles are each run's `host-perf-gpu-launch.data`, triggered by a complete prepare upload containing `SAFARI_EXIT_CONFIRMED`. The first Run86 profile only covered prepare/quit; it is retained separately and excluded from this comparison.

| Period-weighted metric | Off | On |
|---|---:|---:|
| All-QEMU user-state CPU seconds in the approximately 120 s window | 620.758 | 601.677 |
| PAC CPU seconds | 252.475 | 146.364 |
| PAC share of all-QEMU samples | 40.672% | 24.326% |
| PAC share of vCPU samples | 40.888% | 24.476% |
| Memory/MMU/system-register helper share, vCPU | 22.925% | 30.149% |
| TB dispatch/lookup/invalidation share, vCPU | 13.779% | 17.216% |
| Unresolved anonymous JIT share, vCPU | 10.937% | 14.483% |
| Translation/code-generation share, vCPU | 1.595% | 2.095% |

There were 61,455/59,566 samples and no recorded loss. Every vCPU showed lower PAC weighted CPU time. PAC self-time fell 42.03% in these windows; total QEMU user-state CPU time fell 3.07%. These are fixed-window observations, not speedup for a fixed amount of application work.

The targeted failure persisted at nearly the same deadline: off GPU1492 began connection creation at 20:41:29.527 and was judged unresponsive at32.526; on GPU1352 began at 21:35:15.335 and was judged unresponsive at18.328. Both intervals are about 3 seconds. Source events, full categories and per-vCPU values are in os-comparison.json.

## Limits and next action

This is one sequential off/on pair. Boot ages at the aligned window were about 2379 s and 1816 s, and background clients/work differed. More work and more retry activity can occur in the same elapsed interval. There was no perfmap or callchain, so anonymous execution is not assigned to a particular guest function. The data does not prove that enabling memo caused the higher failure count.

The old host pointer-keepalive wrapper was ineffective because it invoked an invalid CLI action; Run86 had 146 exit 2 receipts. Guest caffeinate had independent execution evidence and was the actual keepalive. No failed wrapper is credited as a successful control.

Both normal shutdowns are independently supported by QMP guest-origin events and process reaping. Raw collector failures, including Run87's BrokenPipeError, remain preserved. Full filesystem cleanliness is still unproven.

## Run88 diagnostic follow-up

The same binary, memo-on and 4 GiB profile was used in
`m3-audio-init-profile-20261104-88`. Two ordinary `auval -a` listings completed
with identical output in 24.16 and 20.25 seconds. The following non-sampled fresh
Safari trial still had two GPU unresponsive termination requests before a later
RGB frame. The before-listing trial also had two requests. Listing Audio Units
is not the same query as WebKit's Opus capability lookup; this pair establishes
neither a warm-up benefit nor an audio root cause.

Across 173 seconds surrounding the first trial, `vm_stat` recorded
1,350.77 MiB of cumulative compression and 1,038.20 MiB of decompression;
resident compressor storage grew 76.53 MiB. Swap used and swap I/O remained zero.
These are whole-guest activity counts, not unique bytes or CPU attribution.
Zero swap does not exclude pressure. The old `top -stats pri` field was
unsupported; that output is invalid and the timestamped valid VM counters are
retained independently.

One conditional sample caught GPU1778 in its idle Mach run loop at 23:24:44.777
UTC, before its connection-creation log at 23:24:55.409 and audio registrar
connection at 23:24:56.991. It cannot diagnose those later operations. That
instrumented trial had no GPU unresponsive request, did have WebProcess
unresponsive reports, and eventually showed RGB; it is not a performance
baseline. No repeated sample was taken to manufacture a matching stack.

Run88 completed guest-origin shutdown at 23:35:24 UTC, QEMU exit0/reaped and no
host quit. Raw collector `passed=false` remains preserved. The full evidence is
in Run88 review.

At that stage the next actions were an explicit 8 GiB control and sampling
triggered by an actual Safari-owned GPU event. Those later runs are recorded
in [RESULTS](../RESULTS.md); this historical off/on pair does not establish
RAM or audio causality. Current work is tracked in [STATUS](../STATUS.md).


## Runs113–114: 64 versus 1024 active memo entries

This is a separate comparison from Runs86–87. Both later runs used binary
`a931ea0beb1ff11ef09379789b9ae72739228b2307981be31c6558a665f5fb16`,
NVIDIA, 4 GiB/eight vCPUs, QARMA5/exact clock, 30 Hz diagnostic refresh,
WAV audio with PCM-boundary tracing, and memo statistics. The recorded CPU
property difference is `x-pauth-memo-entries=64 → 1024`; watcher/render hashes
match. Both ordinary trials used no guest sample/spindump.

The sequence was audio inventory, plain authored `afplay`, then confirmed
Safari quit and ordinary launch. The post-afplay preparation delay was closely
matched (315/316 seconds), but boot age, persistent state, process/background
history and memory/cache state were not identical or randomized.

| Recorded observation | Run113: 64 | Run114: 1024 |
|---|---:|---:|
| afplay finish → preparation | 315 s | 316 s |
| Recorded boot age at preparation | about 1453 s | about 1674 s |
| Counter interval enclosing the retained log span | 122.912581 s | 122.969280 s |
| All-vCPU memo lookup hit ratio in that interval | 66.340741% | 84.092902% |
| First GPU constructor → watchdog request | 32.072 s | 32.103 s |
| First connection entry → watchdog request | 2.992 s | 2.997 s |
| Safari GPU watchdog requests in retained stream | 5 | 8 |
| First GPU successful | No | No |
| Complete authored PCM reference | Failed: 736 frames replaced by 512 zeros | 96,000 frames exact |
| Later native RGB | Error page at retained capture | 173,400 exact interior pixels |
| Lifecycle | Guest-origin shutdown, QEMU0/reaped | Guest-origin shutdown, QEMU0/reaped |

The approximately 123-second windows are the nearest enclosing QOM samples
for the first-to-last unified-log events. They include pre/post padding and
all guest work on all eight vCPUs; each hits/misses pair and CPU is read
separately. The 17.752161 percentage-point increase is an observed lookup
ratio change, **not CPU-time savings, GPU-specific cost or exact attribution
to the roughly three-second connection window**. The earlier Run113
66.667967% value uses a different watcher-timeline envelope of 138.265883 s;
that result remains valid within its own scope.

Both first GPUs still fail. The 5→8 request count in one sequential pair does
not establish a capacity-induced regression; observation spans/retries and
background work differ. Run114's last GPU1679 is right-censored only 0.464 s
after its constructor, so no watchdog before cutoff is not proof of success.
The late RGB capture is separately pixel-verified and does not repair the
first-load verdict.

Client milestones do shift: SandboxHelper connection creation is 0.359 s
earlier relative to connection entry (2.598→2.239 s). Its reply time and
server-side work were not measured in these ordinary trials. Earlier client
creation cannot be equated with a reduced server wait or a sufficient fix.

### PCM boundary and repeatability

Run114's 37 negotiated F32/48 kHz/stereo TX packets preserve the complete
96,000-frame reference at pop, populate, accepted writes and finalized WAV
after normal format interpretation. The tone occupies capture frames
`[34816,130816)`, with all surrounding frames zero. The final trace has END,
and the original 2,436,240-byte prefix is unchanged. Run113's discontinuity
was already visible at first TX pop, with unchanged post-pop content.

The new positive is useful, but one voice under changed capacity and different
startup timing/history does not prove a universal repair or that memo capacity
caused the difference. Producer, pre-pop visibility and device feedback
explanations remain open. Physical speaker/input/latency claims are separate.

### Separate host-profile follow-up in Run114

`r114hostprofile` is a later ordinary trial, not the unprofiled capacity pair.
A 180-second, 49 Hz P-core userspace DWARF recording retained 21,674 samples.
It covers the first GPU2190, but ends before the full watcher archive; the
last two of six watchdog requests are outside the profile.

Nominal connection-entry→watchdog-window shares across the **whole QEMU PID's
captured P-core user cycles**, using nonoverlapping leaf families plus verified
native caller subdivision, are:

| Family | Period share |
|---|---:|
| PAC, including PAC-called `aa64_va_parameters` | 22.84% |
| Broad MMU/address translation, including its called `aa64` subset | 17.28% |
| TB lookup | 14.69% |

The PAC-called `aa64` subset is about 5.02 percentage points in the broader
constructor→watchdog window. It is already included in the refined PAC family,
not an extra inclusive cost to add again. All 230 `aa64` leaf samples in that
broader window resolve to four native callers with CALL sites verified in
the a931 disassembly. The analysis stops at valid native C segments rather
than inventing complete stacks through TCG/exceptions.

`float16a_unpack_canonical` is 37.05% before preparation but zero in the nominal
first-GPU window and tested clock shifts; the broader SoftFloat family is only
0.078% in that first-GPU window. The pre-prepare phase follows an earlier
trial and is not proven idle. Whole-profile half-float cost is therefore not
an established first-GPU bottleneck.

Guest/host alignment is only constrained at roughly −2 to +1 seconds from
HTTP receipts, with nominal and shifted windows retained. These percentages
are neither wall time nor GPU/SandboxHelper guest-task CPU attribution, and
they are not a randomized 64/1024 performance comparison.

The finalized 0025 implementation shares the original full-VA logic and
projects `tsz/tbi/mtx` for PAC callers. The final native oracle passes
5,714,064 field cases and 263,200 consumer cases, including 44,877 FPAC fault
outcomes, under UBSan. Actual PAC/HVC/EL0, memo off/on, 2/4/8-vCPU and
QOM/reset consumers pass on binary
`3b111abb7f66451aa0d6c3ac376b42cdc069d7486ff09f5de6d49f8b066c5b50`.
The EL1 synthetic VA-call microbenchmark is 8.245→3.755 ns (54.46% lower call
cost), **not an OS or first-load speedup**. Run115 booted this build with
the otherwise matching 1024-entry profile, but a host reboot interrupted it
before audio inventory, PCM playback or a fresh ordinary Safari trial.
Run116 completed application validation with the same build/profile and
explicit unclean reuse of the recovered ROOT/AUX. Its first Safari GPU and
complete-reference PCM still fail; the results below do not establish a
causal performance gain or regression.
The initial stale NVIDIA595.91.07 runtime override against system595.99.02
was corrected in the test harness, not by changing the driver; its failed
record remains separate.

Final native evidence,
actual consumers,
QOM/reset
and [STATUS](../STATUS.md) retain these scopes.

### Run115 interruption and Run116 continuation

Run115 reached normal installed OS, login and a native desktop before the
second host reboot. Its valid desktop capture contains restored Terminal
output from Run114 and a restored blank Safari window. These are not a new
application comparison. The later `r115-terminal-ready` action log records a
capture, but the retained PNG and JSON sidecar are both empty; those pixels
cannot be reviewed.

The collector's unchanged `results.json` last persisted at **08:00:25.545125
UTC on November 7** with `state=observing` and `errors=[]`. The previous boot's
last journal entry is 08:00:25.999191 UTC and the new boot's first kernel entry
is 15:26:35.756856 UTC; these log boundaries do not identify the fault time.
The new boot reports a fatal BERT record of 43,988 bytes/13 sections, different
from Run110's 42,716-byte/11-section record. The host reboot's cause remains
unknown; this is not evidence that 0025, NVIDIA, audio or Safari caused it.

Exact previous-boot/service recovery verified absent recorded processes and
both target-file locks, removed temporary aliases, and retained unknown exit
codes/reaping. No guest shutdown, collector finalization or filesystem-clean
state is inferred. Run116 explicitly uses `--allow-unclean-storage` for those
same ROOT/AUX inodes; resumed persistent state and background history limit
comparison with Run114. At that recovery point, Run114 remained the latest normally completed run
and observed guest shutdown. Run116 subsequently completed, as recorded below.

Run115 application record,
host reboot evidence,
recovered retention
and Run116 live results
retain the interruption and continuation separately.

### Run116: 0025 application results and separate host profile

Run116 completed on `3b111abb…6c5b50` with the same 1024-entry/stats,
NVIDIA/4 GiB/eight-vCPU/WAV-trace/30 Hz settings. Driver/CoreAudio defaults,
current guest HTTP exchange and plain `afplay` exit0 were verified. The
**complete 96,000-frame PCM reference fails**: eight reference-phase
discontinuities are already present at the earliest TX-pop boundary, and the
tone extent is 1,792 frames shorter. All 37 F32 TX packets and 148 accepted
writes preserve the same bytes through pop/populate/accepted and the entire
151,552-frame WAV after normal conversion. The selected exact reconstruction
uses 2,108 omitted and 316 reused reference frames, but the fixture's 2,400-frame
period makes that mapping nonunique; it does not identify actual deletions,
duplicated guest reads or producer/feedback cause. Final trace END is valid,
the original 2,469,344-byte prefix is unchanged, and the final 2,469,824-byte
trace adds only two control records and END.

The ordinary Safari trial used neither guest sampling nor host perf or
compilation. The first GPU1174 still received a watchdog request, as did all
five GPUs constructed in its retained log. The 16:07:17 native frame is white
with a reload warning, without an RGB page.

| Ordinary comparison | Run114 | Run116 |
|---|---:|---:|
| Constructor → watchdog | 32.103 s | 30.884 s |
| Connection entry → watchdog | 2.997 s | 3.001 s |
| Retained watchdog requests | 8 | 5 |
| afplay completion → preparation | 316 s | 316 s |
| Inventory completion → afplay start | 268 s | 26 s |
| Recorded boot age at preparation | 1674.488 s | 915.418 s |
| Log-enclosing memo interval | 122.969 s | 138.121 s |
| Lookup hit ratio in that interval | 84.0929% | 82.9617% |

The same selection rule produces unequal memo windows and padding; these are
whole-vCPU lookup counts, not CPU time. Matching the post-afplay interval does
not match earlier warmup, background/cache/memory state or the host-reboot and
unclean-storage history. The timing/count differences establish neither a
first-load fix nor causal improvement/regression from 0025. SandboxHelper
reply duration and guest-task CPU cost remain unmeasured.

A **separate** `r116hostprofile` trial recorded 44,960 P-core user-cycle samples
with zero reported lost samples. Its GPU1750 again failed: constructor to
watchdog 28.081 s, connection entry to watchdog 2.997 s. Eight requests occur
in the full retained profile archive; its last constructor is right-censored.
The nominal critical whole-QEMU period shares are:

| Nonoverlapping native family | Run114 profile | Run116 profile |
|---|---:|---:|
| PAC including its VA helper subset | 22.8395% | 18.2637% |
| MMU including verified PTW-called generic VA | 17.2835% | 17.4705% |
| Named TB + all QHT (QHT not fully attributed) | 14.6945% | 17.9323% |

The PAC VA subset, already included above, changes from 5.0150% to 2.2564%
in the critical interval and from 5.0204% to 1.9326% across constructor to
watchdog. This composition is consistent with the intended smaller VA path;
it is not a wall-time speedup measurement. Coarse −2…+1 s clock shifts change
the three-family ranking. The profiles have different preparation coverage
and workloads, so their whole-profile shares are not an equal-work comparison.

Default perf unwinding omitted some native parent frames. Independently
applying the preserved `3b111` CFI to original user-stack records and verifying
direct CALL sites recovered all 1,406 VA callers: 314 agree with existing
parents and 1,092 fill missing parents. This finite native step reconstructs
neither complete TCG stacks nor guest-process attribution. It does not explain
the default unwinder failure. No later binary was used to reinterpret these
addresses, and raw stacks remain private.

The legacy “TB lookup” group requires a narrower interpretation. In Run116's
critical window, **10.9484%** is explicitly named TB leaves; the other
**6.9839%** is QHT leaf periods with all 63 parent frames missing. Across the
broader first-GPU interval, 177 QHT samples retain `tb_htable_lookup` parents
(3.4956% of phase periods), while 137 are unresolved (2.6220%); named TB leaves
add 9.5075%. Missing parents cluster late in the known unwind-failure interval,
so the broader ratio cannot be extrapolated into the critical window. These
retained QHT chains were not independently CALL-validated. Likewise, samples
at `helper_lookup_tb_ptr+0x86` identify a region near the hash shift, but an
ordinary non-precise cycle event cannot distinguish arithmetic, preceding
load/dependency stalls or sampling skid. Neither hash arithmetic nor state-copy
cost is established as the bottleneck.

`R116_KEEP` PID697 was terminated and waited before the 16:12:30.102058 native
capture. Guest-origin shutdown occurred at **16:14:46.314246 UTC**, followed by
QEMU/file-service exit0/reaped, temporary-alias cleanup and finalization at
16:14:47.151940. Collector false reflects unavailable post-exit final capture
with `errors=[]`; filesystem cleanliness remains unproved. Run116 completed at that point; Run117 subsequently superseded it as the
latest completed run and latest observed guest shutdown.

The next implementation, **0026 TB lookup state pointer**, is built as
`a03e9f3a831e731af66a6d2bf41108e126f7773b7a9806d1bc53d8eff431c526`.
Disassembly confirms removal of the 24-byte secondary local copy and 24-byte
outgoing argument copy while the hash-table descriptor retains its owning
copy. Actual TCG hit/conflict/internal-4-KiB-page/invalidation checksums and
entry counts, PAuth/HVC/EL0/PSCI/SMP and QOM/reset consumers pass. The profile
does not specifically measure these copy costs or predict candidate speedup.
Run117 completed separate macOS application validation on the same profile,
using Run116's guest-shutdown storage without an unclean override. Failed audio
initialization makes that sequential comparison nonmatched; no 0026 benefit
or causal regression is established.

Run116 application record,
PCM boundary analysis,
ordinary Safari comparison,
separate host profile,
0026 validation
and Run117 live results
retain these separate evidence boundaries.

### Run117: unavailable audio, late RGB recovery and the empty-query repair

Run117 used `a03e9f…1c526` with the same requested 1024-entry/stats,
NVIDIA/4 GiB/eight-vCPU/WAV-trace/30 Hz profile. The actual audio inventory at
16:28:49–16:29:02 UTC, about nine minutes after launch, contains
`AppleVirtIOSound !registered/!matched` and no CoreAudio device. Inventory
command exit0 is not successful registration. The fixture hash is correct,
but `afplay` PID750 at 16:29:55–16:29:59 exits1 with
`AudioQueueStart failed (-66680)`. No WAV or frozen voice exists after the
240-second observer deadline or after shutdown. **PCM comparison is
unavailable/null, not a failed bit-exact comparison.** The final 176-byte
trace contains the unchanged 16-byte header and one END record, without PCM.

A separate read-only QMP observation reports FEATURES_OK/DRIVER_OK, a
nonbroken device and two configured runtime streams. Control queue0 has
exactly one completion, with `used_idx=last_avail_idx=1` and `inuse=0`;
the other queues are unused. Current indirect descriptors and buffers after
that completion decode as **JACK_INFO(start=0, count=0, size=24) →
NOT_SUPP `0x8002`, used length4**. This agrees with the old `process_cmd`
switch, which unconditionally rejects JACK_INFO and CHMAP_INFO; CHMAP_INFO
was not observed in this guest ring. The read was non-atomic and later than
the inventory/playback: dispatch timing and guest consumption are unobserved.
Patch0023 traces only the PCM control range, so the empty trace never meant
that the device processed zero control requests.

[VirtIO 1.3](https://docs.oasis-open.org/virtio/virtio/v1.3/virtio-v1.3.html)
§§5.14.6.1–5.14.6.2 define the common query count/size and require the requested
range to fit and the response buffer to accommodate `4 + count × size`.
The JACK_INFO and CHMAP_INFO sections (§§5.14.6.4.1 and 5.14.6.9.1) use that
query form, without requiring a positive count. The observed empty query is
valid for a zero-jack device. A real old-binary qtest independently reproduces
the exact 16-byte request, NOT_SUPP response and four-byte used length with
unchanged canaries; an empty CHMAP query also returns NOT_SUPP. This is a
concrete repair target, **not proof that the rejection is the sole audio
initialization cause or that 0026 introduced it**. Earlier successful boots
and actual guest response consumption remain unexplained.

The ordinary Safari first GPU1160 still fails: constructor to watchdog
30.450 s and connection entry to watchdog2.960 s. Four watchdog requests occur
among five owned GPUs. The last GPU1381 has no retained request over only
14.944 s to log cutoff; later load/paint events and the 16:38:32.052856 native
frame establish recovery. Three RGB interior ROIs total **173,400 exact
pixels**, without repairing the first-GPU verdict.

Compared with Run116, both post-attempt preparation gaps are316 s and the
recorded boot ages are927.404/915.418 s. But a failed audio attempt does not
reproduce successful audio warmup; the device capability/control paths and
background work differ. The nominal log-enclosing QOM intervals are
122.806378/138.121454 s, with lookup ratios83.321807%/82.961741%. Run117's
31.506 ms pre-padding is smaller than the unknown guest/host clock offset,
so strict real-time enclosure is unproved. Neither5→4 watchdog requests nor
those ratios/timings establishes a 0026 gain.

`R117_KEEP` was terminated and waited before the16:39:07.858947 native capture.
Guest-origin shutdown occurred at **16:41:39.310435 UTC**, and finalization at
16:41:40.484134. QEMU and the guest-files child exit0/reaped, no host quit was
sent, and temporary aliases were removed. The **controller service exits1 and
is failed**, because its post-exit final capture is unavailable; `errors=[]`
and successful guest shutdown remain distinct. Filesystem cleanliness is
unproved. Run117 completed here; Run118 subsequently became the latest completed run
and observed guest shutdown.

The next repair, **0027**, handles valid empty JACK_INFO/CHMAP_INFO requests,
uses the actual copied header length for short responses, and adds a successful
empty-query trace event that defaults off and is selected only with
`sound_pcm_trace`. It leaves the VSNDPCM1 format unchanged. The final build
`1b41b3abc713208e4b1fb4a4577feb2fe666d707827bab21721d983e5e4278c1`
passes40 real indirect qtest queries, including range/buffer negatives and
actual short-response lengths. The identical Run117 empty JACK request now
returns `00800000`/usedlen4; ten successful empty-query trace events match
the requests. Same-device4096B PCM and existing normal/production131072B PCM
consumers are bit-exact. Default0jacks/2streams/0chmaps and unsupported nonempty
queries remain unchanged. The installed-run preflight checks both0023 and0027
hashes. Actual validation
does not establish restored macOS registration or PCM. Run118 is the real
consumer on the same profile, using117 guest-shutdown storage without an
unclean override. It verified the empty-query success event and S_OK response,
but audio still did not register. Source-guarded actual kernel translation
then proved a4096-byte device/driver used-ring location mismatch: deviceidx1
versus internalidx0/private-lastUsed0 makes the current callback guard false.
No afplay or fresh Safari trial was run in118. The evidence, allocator/IRQ
limits and optional512-entry follow-up are maintained in the technical
[used-ring layout report](M3-VIRTIO-SOUND-USED-RING-LAYOUT.md), rather than
attributing the failure to NOT_SUPP alone or claiming a0026 regression.

Run117 application record,
audio availability and boundary analysis,
device state,
current control response,
ordinary Safari comparison,
exact late RGB validation
and old-binary qtest reproduction
retain these scopes.

### Run119: actual512 initialization succeeds; PCM and first GPU still fail

Run119 uses the0028 `8f0b…7022` binary with actual512-entry sound queues,
1024 memo entries/stats and the prior NVIDIA/4 GiB/eight-vCPU/WAV-trace/30 Hz
profile. Its source-guarded actual control-queue layout matches: capacity16390,
aligned backing, used offset12288 and equal device/internal/private indices3.
Driver/CoreAudio defaults and coreaudiod270 are present; plain afplay764 exits0.
This confirms the initialization path described in the
[used-ring report](M3-VIRTIO-SOUND-USED-RING-LAYOUT.md), not complete audio fidelity.

The full PCM reference is false. All136,704 capture frames are accounted for:
main extent `[26624,118520)` is91,896 frames, with two512-frame zero regions
`[30720,31232)` and `[34816,35328)` and13 joins among14 reference regions.
After4,872 silent frames an extra512-frame block occupies
`[123392,123904)`, with511 nonzero frames, then12,800 trailing silent frames.
The selected main mapping has6,004 omitted/876 reused reference frames and
1,024 inserted zero frames, net4,104 main frames shorter. The2,400-frame
period prevents a unique physical source-history conclusion. These contents
are already in TX pop; all34 packets/134 writes drain without pending or
discarded data, with pop=populate=accepted and normal F32→S16 conversion equal
to the entire WAV. Final trace2,228,416B has a normal END and unchanged
2,227,936B prefix; only two control records and END were appended.

The ordinary trial changed no affinity and used no guest stack sampling or
host profiler. First GPU1171 still receives a watchdog request at33.458 s
from constructor and2.998 s from connection entry. Six requests occur; the
seventh GPU1519 is observed only1.684 s after construction before cutoff.
The17:53:40.036402 native image has173,400 exact RGB interior pixels, which
establish later recovery rather than first-load success.

Against Run116, post-afplay preparation is315/316 s and recorded boot age is
928.867/915.418 s, but inventory-to-playback is65/26 s and patches0026–0028,
queue size and persistent/background state differ. The enclosing QOM windows
are122.839401/138.121454 s, with lookup hit ratios83.418315%/82.961741%.
They are non-atomic whole-vCPU lookup counts, not equal-duration CPU cost or
a causal patch-speed result. Run117's failed audio startup is a different
comparison boundary; Run116 and119 both have playable output but failed PCM
reference matching.

The retained17:49:44.782399 host snapshot has all eight last processors on
P-cores and allowed masks0–19. It does not prove placement during the first
GPU or establish mixed-core scheduling as a cause. The next optional
`--vcpu-affinity 0-7` control keeps binary8f0b/queue512 unchanged, with
per-vCPU-thread masks checked before continuation. It is a runner change,
without a new QEMU patch or global governor/nice/host-setting change.

The runner validation
now passes one actual diskless8CPU checksum consumer and five targeted offline
checks on the unchanged8f0b binary. Run120 has started with explicit
`--vcpu-affinity 0-7`: all eight startup vCPU masks are verified before initial
continuation, while29 other existing QEMU threads and controller masks remain
0–19. Startup verification is limited to those existing threads. The later
18:16:20 post-login snapshot now observes27 newly created Reims/metal threads
with masks0–7;24 surviving original non-vCPU threads and7 other new threads
still have0–19. No affinity was changed after startup. Renderer-thread mask
inheritance is therefore part of the actual Run120 effect: this is **not a
pure vCPU-only runtime comparison**. Actual residency, all future thread masks
and audio/Safari benefits remain separate. The
post-login evidence
retains individual thread identities and masks.

`R119_KEEP` was killed and waited before the17:56:07.055284 capture.
Guest-origin shutdown occurred at17:59:16.416001 UTC and finalization at
17:59:17.495623. QEMU and guest-files child0/reaped, no host quit and alias
cleanup are verified. Controller1/failed remains the missing post-exit final
capture with `errors=[]`, not failed guest shutdown; FS clean is unproved.
Run119 completed here; Run120 later became the latest completed run and
observed guest shutdown.

Run119 application record,
ordinary Safari baseline,
PCM boundaries
and retained host placement
keep these observations separate.

### Run120: compound affinity scope, PCM discontinuities and first-GPU failure

Run120 completed on unchanged8f0b/queue512. Actual layout matches again and
registered/default audio plus afplay820exit0 succeed. The intervention includes
8vCPU masks0–7 **and27 observed newly created rendering-thread masks0–7**;
no affinity changed after startup. It is not a pure-vCPU comparison or a
continuous CPU-residency measurement.

The complete reference remains false. The137,216-frame WAV is exactly
31232 leading silent frames, followed by three reference regions:

| Reference frames | Capture frames |
|---|---|
| `[0,57856)` | `[31232,89088)` |
| `[58128,58640)` | `[89088,89600)` |
| `[59120,96000)` | `[89600,126480)` |

The main extent is95,248 frames, with selected phase jumps272 and480 frames,
net752 frames shorter, followed by10,736 silent frames. There are no replacement
zero blocks or extra post-tone block in this voice. Periodicity still makes
the selected source mapping nonunique. All34 packets/134 writes drain with
no pending/discarded bytes, pop=populate=accepted and normal F32→S16 equal to
the entire WAV. Both jumps already occur at TX pop. Source-conditioned queue
accounting reaches65536B and implies first-return latency32768B, then33 zero
latencies; guest ACK, audio-clock and causal feedback behavior are unmeasured.
Final trace2,236,608B has END and unchanged2,236,128B prefix, adding only two
control records and END. The smaller observed defect than119 is not proof
of an affinity benefit from one voice.

First GPU1312 still fails: constructor→watchdog33.903 s and connection→watchdog
2.957 s. The retained stream has2 watchdog requests versus119's6, and later
native RGB has173,400 exact pixels at18:30:26.970162. This is recovery, not
successful first GPU. Both post-afplay gaps are315 s, but preparation boot age
is1204.072 versus928.867 s and inventory-to-playback88 versus65 s. Although QOM
windows are122.817963/122.839401 s, log spans are101.294/110.385 s and padding
and background work differ; lookup ratios82.420130%/83.418315% are not CPU cost.
The compound inherited masks and sequential state differences preclude a
single-pair affinity-speed conclusion.

`R120_KEEP` was killed and waited before18:34:55.294449. Guest-origin shutdown
occurred at18:38:34.211173 UTC, finalization18:38:35.088853. QEMU and guest-files
child0/reaped, nohostquit and alias cleanup are verified. Controller1/failed
is the unavailable post-exit final capture with `errors=[]`; FS clean remains
unproved. Run120 completed here; Run121 later became the latest completed run and
observed guest shutdown.

Run120 application,
PCM boundaries
and ordinary Safari
retain these outcomes.

### Separate control: continuous kernel-observer work

Run119 retains97 successful kernel-observer captures. The continuous path in
[`KernelBootObserver.capture`](../../experiments/E05-ibec/kernel_boot_observation.py)
requires `_pread(fd, 0, 268435456)` to complete the entire256 MiB before scanning
boot arguments and message buffers; a short read raises an error. The default
[installed-run loop](../../experiments/E05-installed-boot/run.py) continues that
work at a requested15-second cadence after normal-OS proof. It reads the owned
RAM memfd without QMP stop/cont; the current-process sampler was disabled.

Thus97 complete reads imply **24.25 GiB of source-derived read volume**. This
is a calculation from successful capture count and fixed source behavior,
not an additional measured I/O counter. The97 retained PAC sample timestamps
have median spacing15.350 s (15.242–15.426 s); that is whole-loop cadence,
**not scanner wall time**. Subtracting15 s does not measure the scan cost.
The observer's causal effect on PCM or watchdog outcomes remains unproved.

Optional `--kernel-observation boot-only` is implemented and passes
seven focused offline checks,
including the actual joint classifier and continued non-RAM observer paths.
Only a successful capture with joint normal-OS proof and no observed panic
stops further RAM/kernel-runtime capture. The first proof and `last_capture_*`
remain explicitly historical. QMP/status/blockstats/memo/files/screens and
lifecycle collection continue; subsequent RAM panic/reclassification is not
observed, and the RAM observer does not automatically resume on reset.
Default `continuous` behavior stays unchanged. Run121 has now started with
affinity omitted and the same8f0b/queue512 profile. Its actual first joint
normal-OS proof stops RAM capture after5captures at18:41:20.383056 UTC;
results and kernel-state monitoring agree, while QMP/memo/blockIO samples
continue beyond that count, as retained in Run121 results
and frozen kernel state.
This verifies the initial consumer branch, not
application/performance benefit or later kernel-state monitoring. Run121
subsequently completed with two distinct PCM positives but persistent Safari
failure, as detailed below.

The intended configuration comparison is against Run119's continuous observer,
not Run120's compound rendering-thread affinity effect. Persistent-state
history now includes Run120 and warmup/background work may differ; this is
not an equal-work or causal-speed guarantee. No affinity was changed during
Run120, and its manager was not expanded.

Run119 results
and kernel-observer state
retain the capture count; source-derived volume is not a performance result.

### Run121: two complete PCM voices, first GPU and late page still fail

Run121 uses unchanged8f0b/queue512, boot-only observation and no explicit
affinity. Automatic RAM capture stopped after5captures at18:41:20.383056 and
remained stopped through closure, while QMP/memo/blockIO continued to129samples.
A separate manual source-guarded layout scan at18:42:34.916393–18:42:35.550618
is an explicit pre-login exception; there was no application-phase RAM scan.
The18:44:35 snapshot shows all51 observed threads with default masks0–19,
not continuous residency. Layout matches, driver/CoreAudio defaults work,
and coreaudiod265 has its user client.

Both finalized PCM voices contain exactly one complete96,000-frame reference,
with all outer samples zero and pop=populate=accepted=the entire normally
converted WAV. They have different execution histories:

| Trial | Reference position in WAV | WAV frames | TX packets / accepted writes |
|---|---|---:|---:|
| First, before ordinary Safari | `[92672,188672)` | 208384 | 51 / 204 |
| Warm repeat after Safari | `[18432,114432)` | 132096 | 33 / 129 |

First afplay762 ran18:50:19–18:50:30 and the repeat afplay1655 ran
19:03:00–19:03:07; both exited0. The repeat uses output stream0/generation2,
with global packet sequences52–84. All packets drain without pending or
discarded bytes. The second voice is not part of the first-trial baseline.
Two same-boot positives do not isolate boot-only causality or prove universal
fidelity; Runs108/114 already retained complete-reference positives, and
Runs119/120 remain counterexamples under other recorded conditions.

The complete final trace is5,547,872B with normal END, below the8MiB cap.
The first3,395,008B prefix is unchanged, but its appended2,152,864B includes
**228 generation2 PCM records**, seven control records and END. The later
repeat-prefix5,547,392B is also unchanged; only its final480B are two control
records and END. Both generations' PCM hashes and both frozen WAV hashes
remain unchanged. These distinct prefix boundaries must not be collapsed
into a claim that the whole run appended only control records.

First Safari GPU1187 still fails: constructor→watchdog35.684 s and connection
→watchdog2.998 s. Five requests occur, and final GPU1501 is observed only2.443 s
after construction before cutoff. The18:59:59.910316 native frame is an error
page with “Reload Webpage”, without RGB. Against119, post-afplay gap316/315 s
and boot age930.304/928.867 s are close, but inventory-to-playback34/65 s,
persistent state and background work differ. QOM enclosing windows
120.094521/122.839401 s and ratios83.015921%/83.418315% remain lookup counts,
not equal-work CPU time or causal observer benefit.

`R121_KEEP` was killed/waited before19:08:43.687440. Guest-origin shutdown is
19:12:27.423079 UTC, finalization19:12:28.594239. QEMU and guest-files child
exit0/reaped, nohostquit and alias cleanup pass; controller1/failed is the
missing post-exit final capture with `errors=[]`. FS clean remains unproved.
Run121 completed here; Run122 subsequently became the latest completed run
and observed guest shutdown.

The next runtime profile requests `--sound-streams 1` for WAV output only,
keeping generic default2, queue512, boot-only, default affinity and the same
8f0b binary. Run109's old single-stream failure preceded the verified used-ring
repair and does not prove duplex is required. Physical input remains
unsupported by the WAV backend and is not claimed. Real122 registration,
PCM and Safari outcomes must be checked before asserting a single-stream benefit.

The runtime stream-count validation
now passes default2/explicit2/explicit1 actual QOM/config/direction checks,
invalid stream1 INFO/START rejection in the one-stream case, and131072B PCM
with complete trace in each512-queue profile. Five targeted offline checks
pass; omission preserves the old default2 arguments. The binary remains8f0b,
with no new QEMU patch or build. Native voice-list inspection was unavailable
because ptrace attachment was rejected; the WAV backend's zero input-voice
capacity is source evidence, not a claimed native-list observation.

Run122
has now started from121 guest-shutdown storage without an unclean override,
requesting one stream, queue512, boot-only and omitted affinity. The initial
normal proof stops RAM after5captures. Stream-count metadata remains a
configured expectation until actual QOM/guest validation; audio/application
benefit is still pending at that launch point; the completed122 result below
verifies initialization but retains PCM and Safari failures.

Run121 application,
first PCM boundaries,
post-Safari repeat boundaries
and Safari baseline
retain the separate trial histories and limits.

### Run122: single-output initialization works; PCM and all five GPUs fail

Run122 actually verifies QOM stream count1, guest configuration`[0,1,0]`,
queue512 layout MATCH, one registered AppleVirtIOSound stream and coreaudiod271.
CoreAudio reports default48 kHz stereo output and no input fields. Plain
afplay804 exits0. This is a real output-only consumer result; it is not proof
of physical input or universal fidelity, and Run109 no longer supports any
inference that duplex enumeration is required.

The full reference fails. The137,216-frame WAV has leading23040 silent frames,
a94,136-frame main extent`[23040,117176)` and trailing20040 silent frames.
Two selected phase jumps1384/480 at capture96768/97280 shorten the main extent
by1864 frames; there are no replacement zeros or extra tail blocks. The periodic
fixture does not determine a unique deletion history. All34 packets/134writes
are drained with pop=populate=accepted and the entire converted WAV equal;
the discontinuities already exist at TX pop. Final trace2,235,808B preserves
the original2,235,648B prefix and adds only one160B END record, with no new
control or PCM records.

All five observed Safari-owned GPUs receive watchdog requests. The first
GPU1239 has constructor→watchdog27.554 s and connection→watchdog2.997 s.
The constructor segment alone is shorter than121, but its start is later:

| Time from retained markers | Run121 | Run122 |
|---|---:|---:|
| Timeline → constructor | 40.196 s | 47.726 s |
| Constructor → watchdog | 35.684 s | 27.554 s |
| Timeline → watchdog | 75.880 s | 75.280 s |

An8.130 s shorter segment accompanied by a7.530 s later constructor is not
an overall8-second startup gain. The timeline marker itself is emitted before
`open -a Safari`, not an independent user-trigger/page-load timing oracle.
The19:40:48.476872 native frame remains an error page without RGB.
Post-playback preparation316 s matches121, but boot age984.840/930.304 s and
inventory-to-playback55/34 s differ. QOM120.098718/120.094521 s lookup windows
and83.151067%/83.015921% ratios do not establish equal work, CPU cost or a
single-stream causal improvement.

Boot-only stopped at capture5; the separate19:20:38 pre-login layout scan is
retained, and no later RAM/kernel state is treated as live. No Run121 host
thread snapshot is borrowed as Run122 placement evidence. `R122_KEEP` was
killed/waited before19:48:44.911616. Guest-origin shutdown is19:54:09.520209 UTC,
finalization19:54:10.472965. QEMU and guest-files child exit0/reaped, nohostquit
and alias cleanup pass; controller1/failed is missing finalcapture/errors[],
not failed guest shutdown. Run122 completed here; Run123 later became the latest completed/guest-shutdown run;
filesystem cleanliness remains unproved.

Run122 application,
PCM boundaries
and Safari timing
retain the measured boundaries.

### 0029 exact memo capacity16384: functionality and fixed costs

The new main binary is
`a96b3f9750da901ac4d23093f923e817046351050c7ea60c4535fc0571c45fa1`,
with `0029-pauth-memo-16384.patch`. The maximum array and accepted capacity
list expand to include16384; default64 and prior1024 remain available. Full
tags, hash/crypto logic and clocks are unchanged. The build/32-file patch
reconstruction,1,275,159 native UBSan oracle checks, six actual TCG/QOM cases
with two reset generations, read-only counters, five invalid-configuration
rejections and actual memo-off/16384-on PAC/HVC/EL0/2/4/8-CPU consumers pass.
An old-build guard accepts64/1024 on8f0b but rejects16384. A synthetic high-slot
reuse pattern distinguishes capacity behavior:1024 gives0hits/32misses per
generation,16384 gives16hits/16misses. That is functional evidence, not a
speed measurement or a forecast of macOS hit ratio.

Each entry is48 bytes. The fixed maximum array grows from48 KiB to768 KiB
per vCPU, an increase of720 KiB; eight arrays total6 MiB. **Even selecting
64/1024 or disabling memo still allocates the larger fixed array.** Reset
and post-load clearing also covers the larger array. These are source/layout
costs, not measured process RSS or reset-time/OS-performance results. A new
Apple comparison changes both the selectable capacity and the compiled
maximum-state footprint; benefit remains unproved.

0029 actual validation
links the native oracle, CPU consumers, old-build guard and runner checks.
Run123
has now started with single output, queue512, boot-only, default affinity and
the existing display/audio settings, selecting16384 with statistics. Initial
normal-OS proof and a QOM16384 report are retained; RAM observation stops after
capture4. This is not an application benefit or a boot-time comparison.
The same new binary still supports1024 for a capacity control. Comparing old
8f0b/1024 with new a96b/16384 also changes the fixed maximum-state footprint
and clear work, in addition to warmup/background/persistent history; the
completed123 result below still fails PCM/firstGPU. The new same-binary124
control has started, with actual timing/outcomes are recorded in the completed124 section below.

### Run123: higher lookup ratio, PCM and first GPU still fail

Run123 completed on a96b/0029 with16384 active memo entries, single output,
queue512, boot-only observation and omitted affinity. Initialization/afplay
succeeded. Automatic kernel capture stopped after4captures at20:04:13.464102;
the separately retained20:10:04 pre-login layout scan is not application-phase
monitoring. Later kernel fields remain historical, while136QMP/memo/blockIO
samples continue through closure.

The148,992-frame WAV fails the complete96,000-frame reference. Its main extent
`[34304,130020)` is95,716 frames: reference`[0,2560)` maps to`[34304,36864)`,
then512zero frames occupy`[36864,37376)`, followed by reference`[3356,96000)`.
This exact partition replaces796reference frames with512zeros, net284main
frames shorter, with outer samples zero. The zero block is already present
in packet10's earliest TX-pop data; all37packets/146writes preserve pop through
populate/accepted and the entire converted WAV, with no pending/discarded bytes.
The periodic source cannot identify a unique producer history. Final trace
2,427,728B preserves its2,427,568B prefix and adds only160B END; frozenWAV and
stream hashes are unchanged.

First GPU1405 still fails: constructor→watchdog35.420 s and connection→watchdog
2.994 s. Four requests occur; the last GPU1677 is observed only0.956 s after
construction before cutoff. The20:34:38.703213 native frame is an error page,
without RGB. The primary enclosing QOM window reports89.076384% lookup hits
versus122's83.151067% (**+5.925317 percentage points**); this is not CPU time,
wall-time gain or a successful first-load result.

The comparison is substantially unmatched. Preparation boot age is
1664.879 versus984.840 s, and inventory-to-playback323 versus55 s, although
post-playback preparation315/316 s is close. Old8f0b/1024 versus a96b/16384
also changes the fixed maximum array from48 to768KiB per vCPU. Active capacity,
compiled state/clear work, waiting and retained/background history are not
isolated by this pair.

Keepawake805 was confirmed with `jobs`, then killed and waited; the
20:35:44.818912 native capture shows `R123_KEEP_DONE`. Guest-origin shutdown
occurred at20:37:26.593450 UTC, finalization20:37:27.665626. QEMU53229 and
guest-files child53213 exit0/reaped, no host quit was requested, controller and
freeze processes are absent, and four runtime aliases were independently
confirmed absent. Controller1/collectionfalse reflects the unavailable
post-exit final capture with `errors=[]`; FS clean remains unproved. Run123 completed here; it remains the latest strict guest-only shutdown
after124's mixed cleanup, while124 is the latest completed trial.

Run123 application,
PCM analysis
and Safari/counter comparison
retain those limits.

Run124
has now started on **the same a96b binary with active1024**. The maximum array
remains16384 entries/768KiB per CPU in both runs; output/queue/observer/affinity/
display profiles are retained. Planned inventory and playback start ages are
1010.879 and1341.879 s after controller start, with Safari preparation scheduled
from the actual playback completion plus315 s. Those were targets, not measurements; the completed124 result below
records the actual12.126810/14.126810s timing offsets. This control aims to
separate active capacity from fixed build/state footprint and waiting history;
nonrandom order and persistent/background differences still prevent a claim
of strict randomized causality.

### Run124 completes the same-binary capacity pair

Run124 uses the same a96b binary and fixed maximum16384-entry array as123,
with the recorded CPU-property string differing only in active16384→1024.
The fixed array remains768KiB per vCPU/6MiB for eight CPUs. Actual output-only
layout/registration and afplay971exit0 pass. The timing control was closer,
but not exact:

| Actual interval or derived age | Run123 | Run124 |
|---|---:|---:|
| Inventory end → playback start | 323 s | 324 s |
| Playback end → Safari preparation | 315 s | 316 s |
| Playback start boot age | 1341.878727 s | 1354.005537 s |
| Preparation start boot age | 1664.878727 s | 1679.005537 s |

Playback and preparation are12.126810 and14.126810 s later in124. Bootstrap
command submission was late; GUI unreadiness was not established. Ages derive
from recorded controller start and guest receipts, not independent guest
uptime or subsecond clock calibration. The targets are not retroactively
reported as achieved, and order/background/persistent state are not randomized.

First GPU1346 still fails: constructor→watchdog35.423 s versus12335.420 s,
and connection→watchdog2.994 s in both. All six124 GPUs receive requests;
123 retained four requests with a final early-censored GPU, so6→4 counts are
not an equal-coverage causal metric. Nominal log-enclosing QOM intervals are
120.091905/120.095987 s and hit ratios83.056161%/89.076384%, a6.020223 percentage
point decrease at1024. This is aggregate lookup behavior, not measured CPU
savings or an application gain/regression. The21:17:05.329416 native frame is
an error page without RGB. The capacity pair is complete; no additional repeat
is planned on the basis of these same observations.

The complete PCM reference still fails. The165,888-frame WAV contains leading
28672zeros, a95,008-frame main extent`[28672,123680)` and42208trailing zeros.
The exact selected mapping includes one512zero block`[34816,35328)` and two
later phase boundaries84480/84992. Selected displacements912/112/480 omit1504
reference frames; adding512zeros yields net992main frames shorter. All41packets/
162writes drain with no pending/discarded bytes and preserve earliestTXpop
through the entire converted WAV. Periodicity prevents a unique physical
source history. Final trace2,702,736B preserves its2,702,576B prefix and adds
only160B END. The [HAL TX static section](M3-VIRTIO-SOUND-USED-RING-LAYOUT.md)
now establishes that the pinned completion blocks ignore numeric result/latency;
callback-arrival timing and actual producer rejection remain unproved causes.

### Run124 mixed cleanup and the forward lifecycle repair

Keepawake805 was confirmed running, killed and waited; the21:18:59.130955
capture shows termination and DONE. An owned QMP event records
`guest=true, reason=guest-shutdown` at**21:20:57.606795 UTC**, received during a
request. However, the controller subsequently sets `host_quit_requested=true`
and records `teardown=terminate` following
`ConnectionResetError: [Errno104] Connection reset by peer`. The raw strict
`guest_shutdown_observed` is **false**, and collection/observation completion
are false. This is not the earlier “only final screenshot unavailable” case.

QEMU56712 and guest-files child56697 nevertheless exit0/reaped; controller56696
and the freeze service finish, all four aliases are absent, and finalization
is21:20:58.446454. PCM END proves trace closure, not guest-only shutdown or
filesystem cleanliness. **Latest completed run is124; latest strict guest-only
shutdown remains123.** The raw124 JSONs are unchanged. A later run reusing the
current124 target inodes must explicitly use `--allow-unclean-storage`; an older alias
is not a snapshot of the pre-124 data.

The future `FirmwareSession.close` repair waits at most5s for natural exit
only after the latest owned guest-origin SHUTDOWN, with no prior host quit
and the process still live. Timeout retains ordinary host cleanup. It records
`guest_shutdown_wait` and leaves collection/finalcapture/FS-clean criteria
unchanged. Three actual a96b diskless generic-ARM controls pass: post-exit
event drain, host-quit negative control and concurrent-query shutdown race.
The race produces a real ConnectionResetError while the process is still
running; waiting0.003163s then yields exit0 without host quit and strictguesttrue.
Related offline suites10/50/46 pass. This is not yet a new macOS consumer and
does not repair the historical124 classification.

Run124 application,
PCM boundaries,
capacity/Safari comparison
and actual lifecycle-repair controls
retain the positive and negative boundaries separately. Independent native
QARMA forward-tweak screening and supported watchdog-control discovery remain
unpromoted; no further capacity loop, new optimization benefit or timer change
is asserted here.

### Lifecycle and evidence

Run114's original 1800-second keepawake had naturally completed by the
06:32 capture. The separate 600-second profile keepawake was terminated and
waited at 06:41:50 before shutdown. The guest-origin QMP event is
**2026-11-07 06:57:32.512021 UTC**, followed by QEMU/file-service exit0/reaped
and finalization at 06:57:33.345248. Run113's guest event was
05:37:56.111023 UTC. In both cases the collector remains false because final
capture was unavailable after natural exit, with `errors=[]`; this is not a
failed guest shutdown. Full filesystem cleanliness remains unproven.

Authoritative inputs:
Run113 baseline,
Run114 comparison,
Run114 PCM analysis,
late RGB validation,
separate host profile
and Run114 application record.
