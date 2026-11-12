# M3 Run112 — GPU and SandboxHelper startup in system-wide Stackshots

Run `m3-nvidia-gpu-system-stackshot-20261107-112` kept the existing
`93f6e7ea…b921db91` binary, 4 GiB/eight-vCPU/WAV-two-stream/memo/30 Hz profile
and selected NVIDIA instead of Intel. Normal macOS, the native desktop and
actual guest HTTP exchange were verified. The important new result is a
same-sample client-to-server chain for the first Safari GPU, rather than a
service-name guess or a late fixed-PC snapshot.

## Raw capture and independent decoding reached the actual GPU and server

`r112gpuraw3` saved 3,618,822 binary bytes with native/watcher exit 0 and
no outer timeout. `r112gpudecode` processed an exact copy of that raw input
and produced a 10,562,162-byte report, also exit 0/no timeout. Owned children
were reaped and the final archive uploaded at 04:04:51 UTC.

The raw SHA-256 is
`e3b5c8f9677dd28fef258eb7ec6aae3953016d65ef341e00d5bddc0e36a0f130`;
the report SHA-256 is
`5ffc97e12721c47451b9d4b6e2859c292df7086eb8237764acec7466c9018b15`.
The actual report window is **03:51:32.920–03:51:43.226 UTC on November 7,
10.31 seconds / 103 steps**. Raw generation took 149 seconds and separate
reprocessing 256 seconds. Those operation totals are not sampling duration
or normal application performance.

GPU4569 is explicitly owned by Safari4440. Its main thread `0xb43a` has
69 samples (1–69), ending at 03:51:39.849 UTC, with reported process/main
CPU times 1.299/1.089 seconds. The sequence is:

| GPU sample indices | Observed work |
|---|---|
| 1–29 | Mach message wait |
| 30–48 | AudioSession initialization |
| 49–53 | VP9/Metal device discovery |
| 54–55 | Opus registration data processing |
| 56–66 | Synchronous wait on the identified SandboxHelper startup chain |
| 67; 68–69 | `copyout`; then truncated kernel stacks during termination |

The system-wide report resolves both the recipient identity and its work at
the corresponding indices:

| Same indices | Explicit GPU wait target | Matching server-side evidence |
|---|---|---|
| 56–57 | xpcproxy PID4683, TID `0xb666` | dyld, APFS close and `__posix_spawn`, with running leaves; report says `Execed to: com.apple.audio.SandboxHelper [4683]` |
| 58–60 | SandboxHelper PID4683, TID `0xb669` | dyld fixups/validation, ObjC class initialization, Foundation/Swift and APFS work |
| 61 | Same Helper thread | User-directory/notify initialization, waiting for launchd TID `0xb5eb` |
| 62–65 | Same Helper thread | `sandbox_init_with_parameters → sandbox_compile_file`; Scheme parsing, allocation/GC and policy output, with multiple running leaves |
| 66 | Same Helper thread | `xpc_main` listener bootstrap, waiting for launchd TID `0xb551` |

The actual service path is
`/System/Library/Frameworks/AudioToolbox.framework/XPCServices/com.apple.audio.SandboxHelper.xpc/Contents/MacOS/com.apple.audio.SandboxHelper`.
Its section spans samples 58–69, 03:51:38.646–03:51:39.849 UTC, with reported
process/main CPU times 0.962/0.901 seconds. The connection activated at
03:51:38.401; the watchdog fired at **03:51:38.811**, during service startup.
The client was waiting for a concrete service that was actively progressing.

This does not prove a unique root cause or that any proposed optimization is
sufficient. Step index × 100 ms is not precise function timing. The two
launchd owner TIDs have no reviewable user stacks at the corresponding 61/66
indices, so recursive attribution stops there. Helper samples after 66 enter
dispatch/pthread cleanup waiting; earlier startup frames do not establish
that the business request was handled. Observer `-noIPC/-noSymbolicate` flags
do not disable the guest's IPC or remove sampling intervention.

Line locators in `r112gpudecode/report.txt`: GPU header 16029–16055;
client wait targets 16457/16458/16460; Helper identity 14233–14243;
Helper work 14257–14439; xpcproxy exec transition 61746–61795.

## Failed attempts and controls remain part of the result

- `r112system` timed out its Safari quit AppleEvent (`-1712`, quit exit 1,
  `SAFARI_QUIT_PENDING`). No collector started; it did not prove a hung pgrep
  or interruption of a root watcher.
- `r112system2` confirmed Safari absent, established canary readiness and
  launched an actual all-process/noIPC sample. It reported sampling complete
  and symbol processing, then hit its 120-second limit: native 137/watcher124,
  report/index zero bytes. This proves output was not saved, not that no raw
  kernel stacks were collected.
- `r112rawcontrol` saved 2,341,173 bytes from a three-second/100 ms control;
  exact-copy separate decoding produced 30 real sleep2377 samples. The
  603 Process headers and 1038 Thread rows are occurrence counts, not distinct
  thread counts or GPU initialization evidence.
- The first GPU-raw preflight found expired sudo credentials and did not
  launch. `r112gpuraw2` then sampled ten seconds but saved zero raw bytes
  under 120 seconds; this was not a demonstrated format failure. The native
  screenshot is an error page, not RGB, despite page-load timing logs.
- Only GPU-raw generation/save budgets changed to 270 seconds internally,
  300 seconds outside and ten seconds of TERM grace. Sampling stayed ten
  seconds/100 ms; older modes and three-second controls were unchanged.
  The successful `r112gpuraw3` and its decoder are the subsequent evidence.

The separate `processor_stuck_snapshot` analysis is preserved in the
application observation. It does not replace the decoded target/server
report or erase earlier failure limits.

## Actual WebKit timer mapping and the media-preference boundary

A later bounded read-only study binds the26A434 WebKit image to this run's
report: **LC_UUID`70BEE322-37EE-3B22-8012-4F76BA105C29`** matches
`r112gpudecode/report.txt:16601`, with version625.1.29.11.28. It verifies two
separate3.0-second paths in the actual library:

- Safari/UIProcess's GPUProcessProxy constructor passes3.0 through the
  AuxiliaryProcessProxy/ResponsivenessTimer construction path.
- WebContent's `GPUProcessConnection::waitForDidInitialize` independently waits
  3.0 seconds and invalidates its connection on failure.

These are independent guards. An observed three-second UI responsiveness
interval must not be treated as the only initialization deadline, and the
presence of an internal timeout parameter is not evidence of a supported
user preference for changing seconds.

The actual library also contains the GuardMalloc diagnostic condition:
`DYLD_INSERT_LIBRARIES` containing `libgmalloc` selects a cached branch that
rearms the timer instead of executing the unresponsive callback. This is a
**diagnostic exemption**, not an ordinary timeout-duration setting. Neither
activation nor whether Safari launch preserves the environment was tested;
it is not a validated workaround or performance configuration.

The media-switch question has a narrower source-only result. At public
WebKit commit`47fa426c169f32f8fc3dedd2c28bd04af887713d`,
`UseGPUProcessForMediaEnabled` controls media routing. The
GPUConnectionToWebProcess constructor calls `enableMediaPlayback` without
that runtime preference guarding the call. Its codec defaults, including
Opus/Vorbis capability queries, occur as eagerly evaluated `value_or` arguments;
supplying an optional value does not itself skip those default expressions.
[GPU constructor and codec defaults](https://github.com/WebKit/WebKit/blob/47fa426c169f32f8fc3dedd2c28bd04af887713d/Source/WebKit/GPUProcess/GPUConnectionToWebProcess.cpp#L361),
[connection parameters](https://github.com/WebKit/WebKit/blob/47fa426c169f32f8fc3dedd2c28bd04af887713d/Source/WebKit/Shared/GPUProcessConnectionParameters.h#L42).
The distinct `MediaPlaybackEnabled` guard identified in the UI process is
not that GPU-constructor guard.
[Separate UI-process path](https://github.com/WebKit/WebKit/blob/47fa426c169f32f8fc3dedd2c28bd04af887713d/Source/WebKit/UIProcess/WebProcessProxy.cpp#L3041).

The actual26A434 image confirms the preference key string, **not** equivalence
of its compiled media-constructor branch to that source revision. Therefore
neither the key's name nor this source study establishes a standard switch
that lets a GPU-rendered RGB page skip SandboxHelper startup. The fixed-source
result prevents promising that behavior; it does not supply missing actual
branch or runtime evidence.

The evidence is retained in
`private/webkit-watchdog-source/actual-26A434/evidence.json`; actual code bytes,
addresses and disassembly remain private. The selected WebKit TEXT has SHA256
`61d191a579f36ec77ca73d02042c585ebcbec738ad10798a6095a71e1a600926`.
Pinned primary-source copies were hash-checked. This follow-up made no guest,
environment, runtime or timeout change and claims no whole-source-to-binary
identity beyond the explicitly verified paths.

## 0030 diagnostic and validation before Run125

`0030-arm-timeout-diagnostic.patch` implements the **default-off, finite
3→30-second diagnostic at the two fixed sites**. It is distinct from the
withdrawn CPU experiment `0030-pauth-memo-miss-boundary.patch`; that experiment
remains rejected. The new main binary is
`6ad57aa8f969e3f4369ed6bbecb1e1833c18c282b9425e2e2943a06b17f4ac71`,
with patch SHA256`9059c2a738e6b6203404b71a44558fa79d43239e65b4f56c42a4d6bc0e9c4fad`.
Forty-file patch reconstruction passed.

The profile checks current virtual-address identity/UUID, the original
instruction and64-byte context on each eligible helper execution, including
actual runtime PC under `CF_PCREL`. It preserves guest code bytes, disks and
clock settings. Arming is an explicit paused publication; counters and epochs
are read back per vCPU. Hot disarm and reset revoke the guard, and migration
is refused while the diagnostic profile is enabled. These are diagnostic
constraints, not a supported Safari preference or a change to the acceptance
of the original three-second requirement.

Core-final results
contain five real self-authored **two-vCPU/4KiB** guest groups: off, matching,
wrong UUID, wrong instruction and wrong context. All exit0/reaped. Matching
has14 phase visits in order`1,2,3,4,5,6,7,8,9,12,13,10,11,1(reset)`:

| Phase visits | Verified boundary |
|---|---|
| 1 before arming; 2 | Hot unarmed TBs retain3; then both guarded sites return30 and the non-target remains3. |
| 3; 4 | UUID mutation rejects replacement; restoring identity works on subsequent execution. |
| 5 | Current address-space change affects one vCPU independently of the other. |
| 6; 7; 8 | MMIO identity leaf, MMIO translation-table and unmapped identity are rejected without MMIO callbacks. |
| 9 | FP-access traps, FPSR, unrelated FP state and scalar upper-lane behavior remain architectural; guard counters do not invent hits. |
| 12; 13 | Debug identity reads preserve a clear access flag; privileged execution rejects before an unsupported/MMIO walk. |
| 10 | An explicit MMIO read supplies the positive observation control; matched sites still work. |
| 11; final1 | Hot disarm restores3; reset clears arming/site counters and leaves ordinary behavior. |

Both virtual-alias directions are checked in every phase, and guest assertions
verify unchanged code windows and exact FP/counter results. Before the final
reset, CPU0 has5/5 site hits and10 failures, CPU1 has4/4 and12; reset returns
counters to0. Read-only counter setters and arming while running are rejected.
These observations validate the synthetic4KiB boundary; **real macOS16KiB
translation and actual WebKit-site execution remain separate consumer checks**.

Host integration evidence
is an index of already observed tool completions, not a fabricated raw log or
another test run. The runner full suite passes48 methods and the host helper
full suites pass14. Helper tests use synthetic owned-controller/QMP fixtures
plus real file persistence, not real guest arming. File-service tests exercise
actual loopback GET/PUT and a child service: the initial full7 invocation has
6passes and1 bad test assertion. That assertion was corrected to compare the
actual HTTP body hash with the verified child-service wrapper hash, and only
that one method was rerun successfully. The six retained passes plus the
corrected method cover seven methods; **no single all-green full7 rerun was
observed**. Source hashes in that evidence index were recorded at index creation,
not retroactively at the earlier test invocations.

The identity-probe build
passes cross-built Mach-O/import/entry and13 ad-hoc page-hash checks, plus
5host test methods/22 synthetic dyld-ABI executions under UBSan. It does not
load the target library and the build record has `guest_executed=false`.
Guest loader/signature/ABI acceptance and a real identity result are unproved
by those host checks.

## Run125: real probe and partial guards do not prove Safari30s behavior

Run125 on6ad57 completed the actual guest probe and eight-vCPU v1 publication.
The23:39:05 receipt records checksum/signature/designated-requirement success,
exit0, no timeout and empty stderr. Its metadata is verified without loading or
calling `dlopen` on WebKit; image count stays81→81. This proves that probe's
loader/ABI consumer, not a universal cross-process image base.

Publication at23:40:48 succeeds with each CPU's epoch incremented once.
The retained epochs then remain`[3,4,4,4,4,4,4,4]`: they must match per CPU,
not be equal across CPUs. The counter sequence is:

| Snapshot | Site0 / site1 / guard failures | Boundary |
|---|---|---|
| Before-trigger23:43:08.993977 | 0 / 0 / 0 | After old Safari exit/publication, before the actual trigger. |
| After-prepare23:45:42.314185 | 2 / 0 / 0 | Already after timeline start23:45:30; not a prelaunch baseline. |
| Observation23:47:24.185145 | 4 / 0 / 0 | Global counter evidence, not a guest PID attribution. |
|23:48:14.131987 and23:49:41.915822 | 4 / 0 / 0 | Last retained counter observations; not continuous through shutdown. |

Four site0 guards succeeded in the real16KiB guest. Site1 has no hit, which
neither proves correct30s behavior nor identifies a specific guard fault.
The actual ordinary wrapper uploaded prepare, then continued into gpuwatch
and its3s sleep/open sequence; it had no host counter-read gate. The retained
before-trigger snapshot is therefore the usable zero boundary.

Safari1138 owns GPU1245,1417 and1434, all of which receive watchdog requests.
The first UI request at23:46:39.903 is followed by watchdog23:46:42.906:
**3.003 s**, or2.803 s from the GPU connection-entry marker. Its30.724 s
constructor-to-watchdog span is not the target timer interval. Later GPUs
are requested terminated3.010/3.008 s after launch-completion markers.
No Safari `DidInitialize` is retained. The two positive initialization events
belong to nsattributedstringagent clients, with GPU1218/1325 and WebContent1238/
1326; their request-to-initialize times2.874/2.517 s are already below3s.
They do not establish Safari success beyond3s within30s.

The23:48:06.092328 native image is white. RGB becomes visible later, behind
foreground Terminal at23:49:00.236347, with another owner confirmation at
23:49:49.717459 and no reload. Those post-watcher pixels have no retained
first-GPU/PID association or exact pixel oracle. The two bracketed counter
snapshots stay4/0/0, and the last read precedes the final image and shutdown.
Late RGB is not first-GPU success, an ordinary120s-window success or an
original3-second performance pass.

Thus the requested30s Safari behavior was **not demonstrated**. Global site0
hits cannot choose between mapping/site coverage and another unmodified3s
path. The cause remains unresolved; the claim that the historical UUID came
only from nsattributedstringagent is not adopted. Inventory was performed,
but no authored afplay or old315/323s warmup was used, so this is not a matched
PCM-warmup/performance control.

Keepawake703 was killed/waited, with DONE at23:49:29.181146. Guest-origin
shutdown is23:51:48.985926 UTC and finalization23:51:49.878012. QEMU and
guest-files child0/reaped, no host quit and alias cleanup pass. Controller1
is only unavailable post-exit final capture with `errors=[]`. The event was
recovered by post-exit drain and `guest_shutdown_wait=not_attempted`: this
macOS close did not exercise the new bounded race-wait branch. Full filesystem
cleanliness remains unproved; Run124's original mixed outcome is unchanged.

The unique Run125 analysis
and application record
retain source lines, hashes, counter epochs and target/non-target distinctions.

## Same identity can have different process slides

The existing Run112 report supplies a concrete counterexample to treating
one process's probe base as universal. Safari4440, GPU4569, Networking4551 and
WebContent4505 all list WebKit625.1.29.11.28 with UUID
`70BEE322-37EE-3B22-8012-4F76BA105C29` and shared-cache UUID
`C284A291-B68B-3D0D-A1A0-3F866F87EFDC`. Safari's image/cache base group differs
from the common GPU/Networking/WebContent group. GPU4569 explicitly names
Safari4440 as responsible. Exact addresses remain in the original archive.

Locators in `r112gpudecode.tgz!r112gpudecode/report.txt`:
Safari section46524–49654 (cache46532/image49629), GPU16029–16605
(cache16037/responsible16039/image16601), Networking17199–18218 (image18212),
WebContent18219–19270 (cache18227/image19259). There is no named
nsattributedstringagent section from which to assign its address group;
this is not an absence-of-process claim. These archived differences invalidate
a general mapping assumption, but do not establish Run125's unique cause.

## 0031 derives the candidate base at each actual execution

The0031 build is
`b4d85fe0d7e4e10d7b1c99f95ecfd54d8079a3ecb8665245c47db8e514a2774a`;
patch`0031-arm-timeout-runtime-slide.patch` has SHA256
`5c000c5dbbb511f07f02eba3ef4c40c4371cac319e1b164e3c4437a104a6d493`.
For each eligible instruction, QEMU derives the candidate base from actual
runtime PC minus the fixed site offset, then checks current-space UUID/header
and original instruction/context. The host publishes only paused-state
boolean `armed`, never a probe-process base. The probe remains process-local
material/ABI evidence. Code bytes, disks and clock settings remain unchanged.

Core results
pass five actual two-CPU/4KiB groups, all exit0/reaped, with19matching phase
visits. Complete images at two bases work in both TB-generation orders,
including different per-CPU TTBRs; a code-only alias without matching identity
is rejected. FP, access-flag, MMIO positive/negative, reset and other existing
controls remain covered. This is still synthetic4KiB evidence, not a completed
real16KiB WebKit consumer.

Host v2 evidence
records15full helper methods,3targeted runner methods and a full7-method
loopback/file-service pass, plus actual0030+0031 manifest preflight. The3runner
methods are not a rerun of the earlier48-method suite; the22probe ABI executions
were not rerun. The index preserves completed tool observations without
inventing raw logs or test-time source snapshots.

Public/private publication schema2 uses
`runtime-pc-slide-armed-bool-v2`. `rejections` counts eligible candidate guard
rejections, **not Safari errors**. Global site hits remain cumulative
per-CPU/mapping observations, without PID attribution even with valid epoch
deltas. Historical Run125 schema1/global-base/`failures` records are unchanged;
legacy receipts are rejected, not migrated. Original3s performance pass remains
false for this diagnostic.

## Run126 could not reach the target trial

Run126 booted b4d85, but guest curl7 could not download the inventory helper.
A current host GET returned200 and guest route lookup reported not-in-table.
The retained en0 image is inactive without IPv4, but its MAC differs from the
configured VirtIO target; that observation cannot establish target-interface
state. The no-route/curl failure and target CTRL overlap are independent. Inventory execution, identity probe, arming
and target Safari were unavailable. Restored old Safari pixels are not a
new trial, and no empty Safari-analysis JSON was created. All8CPUs remain
unarmed with site0/site1/rejections0 in the00:23:37 final snapshot.

A bounded371-byte read records network CTRL queue2 size64 with device used
only16B after avail, inside its134B expected extent: published physical overlap.
One used entry has length1 and ACK0 for RX_ALLMULTI. RX/TX queues0/1 remain256
with4096B gaps. These non-atomic observations do not translate the guest's
internal used pointer or prove callback/IRQ history, so they do not establish
Run118's same root cause or a0031-induced regression.

Keepawake686 was killed/waited. Guest-origin shutdown is00:25:49.261682 UTC
on November8, finalization00:25:50.434078. QEMU/files0reaped, nohostquit,
strictguesttrue and aliasesremoved pass; controller1 is finalcapture unavailable
with `errors=[]`. Again, post-exit drain leaves the5s wait not attempted.
Filesystem cleanliness is unproved. Run126 application
and network read
retain this unavailable-consumer boundary.

## 0032 changes only network CTRL capacity; Run127 is still a consumer test

`0032-virtio-net-control-queue-size.patch` builds as
`4a433616dc04e90ba41d720fe0dfb8925294799eff94a6eef24b52108a6b4d66`
with patch SHA256`22493bcf050ca468e8907245d109e4d156407db1d2942dc40f1c728e2e46a926`.
The uint16 `ctrl_queue_size` property defaults64 and accepts powers of two1–1024.
Realize and MQ rebuild use the same field. RX/TX256, feature/ACK handling and
guest physical addresses are not rewritten. Pinned26A434 AppleNetwork source
reads the transport-announced size without a64clamp and enters the common
queue allocator; actual guest mapping/consumption remains a separate check.

Actual device tests
pass default, explicit64, explicit512 and MQ-rebuild cases, all exit0/reaped.
The512 case completes515 controls across wrap, changing preset ACK255 to0 for
valid RX_ALLMULTI and to1 for an invalid class, with direct/indirect SG and
canaries intact. The separate data checks transfer60B in both socket directions
through unchanged RX/TX256 queues. Actual MQ generations3→5→3→5 preserve
CTRL512 and complete ACKs. Vhost evidence is limited to queue advertisement/
linkdown handshake; no offloaded data plane is claimed. Invalid0/3/2048/65535/
65536 configurations are rejected. Forty-two-file patch reconstruction passed.

The installed opt-in `--net-control-queue-size 64|512` requires user networking
and matching0032. It only appends the control-size device property; omission
preserves default arguments. The host evidence index
records5targeted runner methods,2FirmwareSession argv methods and one focused
recheck after adding a post-spawn metadata assertion, plus actual manifest
preflight. It reuses completed tool observations, not raw logs or a full-suite
rerun. Those fixtures alone do not establish macOS network recovery.

Run127
actually started at00:40:54.185035 UTC from126's strict guest-shutdown state,
without an unclean override. It retains the v2 diagnostic and no afplay/old
warmup. The early00:42:20.519241 read confirms selectedCTRL512, used at
avail+4096B, unchanged RX/TX256 and no physical overlap in all three device
rings. CTRL index0 had no completion in that early snapshot; that geometry alone
was not network recovery. Later, the00:50:25–00:51:01 inventory completes an
actual guest GET200/PUT chain and reports registered/matched single-output
CoreAudio48kHz stereo. The00:53:37 interface record identifies **en1** by the
configured target MAC, with10.0.2.15/24, UP/RUNNING/active and a10.0.2.2 route.
The retained old126 en0 image has a different MAC, so its inactive state cannot
be retroactively assigned to the target VirtIO device.

The later network read has RXused4038, TXused2297 and CTRL512used/last-avail13
without physical overlap. Its current descriptor does not locate a reliable
ACK and may have been reused; no ACK is inferred from an earlier zero. These
facts prove the current HTTP chain works underCTRL512, not all network
functionality or Run126's unique cause. Guest internal used-VA translation remains unverified. The completed v2
Safari consumer result below adds bounded initialization progress; it does
not establish Run126's unique cause. Original3s performance pass remains false.

## Run127: bounded Safari initialization progress under the diagnostic

The completed Run127 analysis
retains one Safari-owned GPU1135, responsible to Safari1017, without mixing
nsattributedstringagent initialization into the target result. The first
matched client is WebContent1069/connection18:

| Matched event | Guest UTC on November8 |
|---|---|
| Client connection create |00:59:13.075 |
| Safari UI request |00:59:13.091 |
| GPU1135 server connection entry |00:59:13.099 |
| Client `DidInitialize` |00:59:17.172 |

That is4.081 s from UI request and4.073 s from server entry: **above3s and
below30s**. No GPU watchdog request is retained. The same GPU later initializes
WebContent1271/connection21; the requested main frame finishes at00:59:31.248
and emits page timing at00:59:34.982. Three Safari WebProcess-unresponsive
notifications remain, so this does not mean all responsiveness problems have
been solved.

The before-trigger00:56:59 and after-prepare00:58:15 snapshots are both zero;
after-prepare is still not assumed to be prelaunch. After-archive01:00:36 and
before-shutdown01:01:17 both retain site0=1 on CPU2, site1=0 and rejections=0,
with the same per-CPU epochs`[3,4,4,4,4,4,4,4]`. All eight publication setters
were boolean true; no probe base was published. This is one observed site0
guard, not a PID-qualified site trace or validation of both fixed sites.
The last counter read precedes later pixels and shutdown.

Native captures at01:00:46.348176 and01:01:54.903683 show RGB, first with Safari
foreground and later behind Terminal. Their first presentation time and exact
pixel fidelity were not independently measured. First-GPU progress comes from
the separate ownership/connection-ID chain, not from these later pictures.
The result is **limited initialization progress under the enabled finite30s
diagnostic**, not an original3s performance pass, a matched performance
reproduction, complete stability or a unique explanation of Run125/126.
Site1 remains unobserved; its zero count is not a proof that the site was unused
or that every mapping was covered. No authored afplay or old warmup ran.

Keepawake727 was killed/waited. Guest-origin shutdown occurred at
01:04:58.963146 UTC, finalization01:05:00.180448. QEMU and guest-files child
exit0/reaped, nohostquit and alias cleanup pass; controller1 is unavailable
post-exit final capture with `errors=[]`. The event came from post-exit drain
and the5s wait was not attempted. Filesystem cleanliness is unproved.
Run127 application
keeps this closure and the diagnostic limits separate.

## Run128: fixed WebGL2 render and raw readback complete

The actual macOS oracle result
passes both complete96×64 RGBA8 outputs: offscreen FBO and default framebuffer,
each24576bytes, SHA256
`d8da4828f987b4c3334729f48fdeb6036bd810c23d16526fcca6373c203355da`,
with zero mismatched bytes. Context attributes, actual dimensions, shader
compile/program link, GL errors and binary PUTs also pass. The API reports
WebGL2/GLSL ES3 and `Apple GPU`; these strings do not establish physical GPU
execution, scanout, FPS, latency or general WebGL/Metal coverage. Native
02:06:18.737142 shows upper cyan/magenta/yellow and lower red/green/blue.
The exact oracle is raw framebuffer data, not a native screenshot comparison.

The unique Safari analysis
retains Safari1013 and one owned GPU1124, with zero GPU watchdog requests.
For first WebContent1058/connection18, UIrequest→DidInitialize is3.781s
and GPU entry→DidInitialize3.752s. The target page uses WebContent1311 /
connection21: create→DidInitialize13.716s, UIrequest13.676s and GPU entry13.631s.
These are log event gaps, not measured `waitForDidInitialize` blocking times.
Two WebProcess unresponsive notifications remain. Final02:06:28 counters are
site0=1 only onCPU2, site1=0, rejections=0 with unchanged per-CPU epochs
`[3,4,4,4,4,4,4,4]`. Global counters do not provide PID attribution; site1 is
unvalidated and original3s performance remains false.

The page reports start02:05:20.220 and finish02:05:35.691. Offscreen/default
PUTs arrive at02:05:34/35 and JSON at02:05:36, before the ordinary watcher ends
02:05:45 and archive completes02:05:48. The retained timeline spans133s despite
the requested120s watcher; it is not a ≤120s end-to-end result. No optional
late60s, reload, authored afplay or previous warmup was used. This new content
load is not a matched Run127 performance comparison.

The earlier host Chrome/SwiftShader control
remains separate software-fixture evidence: two exact outputs, eight expected
negative rejections and three syntax checks. Browser/service cleanup passed;
the distinct real-child cleanup-race regression did not repeat rendering.
The transport index
records seven targeted methods (URL3, legacy directory1, GET/PUT/render2,
ordinary helper1), with shell syntax separate. It is a completed-tool index,
not a fabricated raw log or test-time source snapshot. Existing URL behavior
was preserved; this did not introduce a global URL whitelist.

Keepawake713 was killed/waited. Guest shutdown is02:10:41.940319UTC and
finalization02:10:43.049062. QEMU123269 and files123248 exit0/reaped;
controller123246 ends1 only because natural exit prevents final capture,
with `errors=[]`, no host quit and aliases removed. The event is post-exit
drain, so the5s natural-exit wait was not attempted. Filesystem cleanliness
remains unproved. Run128 application
is the closed consumer record. Latest completed and strict guest shutdown
are128; no VM remains active. The user requests temporary project maintenance
next, not Run129 or a new feature experiment. Maintenance has not yet been
executed; the long-term goal is neither complete nor paused.

## Performance and candidate boundaries

The earlier ten-second desktop-restoration host profile contains 6226
samples. PAC is 24.592% of sampled P-core user-cycle periods under exact leaf
classification; P/E PMUs stay separate. `aa64_va_parameters` has PAC and
translation callers and incomplete FP chains, so its cost is not wholly MMU
or PAC. This is not a GPU-initialization benchmark or an OS speedup result.

Run112 used the old `93f6e7…db91` binary throughout; patches 0023 (PCM
boundary trace) and 0024 (PAC memo capacity/statistics) were separate
candidate/offline work at its closure. Their subsequent main build and
actual CPU/QOM/audio preflight passed, with Apple baseline/comparison work
tracked in [STATUS](../STATUS.md). At Run112 closure, the planned comparison used one new binary:
64 entries with stats/audio trace in Run113, then 1024 under matching
conditions in Run114. Run112 supplies no optimization-sufficiency result.

## Presentation stopped advancing; the final exit was host initiated

Around 04:10 UTC, native presentation stopped updating. The observed gap
exceeded 700 seconds while QMP still reported `running` and guest kernel
logging continued. Apple-menu input produced no verified new frame. The
inspected host kernel journal had no new entries after 04:00. This is an
independent observed presentation boundary, not a demonstrated recurrence
or cause of the Run110 BERT reboot or Run111 Intel context reset.

At 04:33:57 UTC the attempted `system_powerdown` returned `UIError` in the
local helper. The GuestUI whitelist excludes that command before journaling
or sending, and the journal contains only the preceding successful
`query-status`, with no `system_powerdown` before-send entry. This does not
establish an actual QMP powerdown request or a rejection by QEMU.

Root then sent SIGINT only to the owned controller, allowing its normal
cleanup. The owned QMP event at **04:37:53.032858 UTC** has `guest=false`,
`reason=host-qmp-quit`. The run finalized at **04:37:54.013328 UTC** with
QEMU exit 0/reaped, file-service exit 0/reaped/stopped and temporary links
removed. The controller exits 1 with `interrupted=true`,
`FirmwareSessionInterrupted` signal 2, and `collection_success=false`.
All diagnostic outputs had already returned and their observers were reaped.

**Guest-origin shutdown and full filesystem cleanliness are unproven.**
At its closure, Run112 was the latest finalized run; the last verified
guest-origin shutdown was Run109. Lifecycle evidence is in `results.json`, `qmp-lifecycle.json`,
`shutdown-request.json` and `ui-actions.jsonl`. Current execution and build
state remain in [STATUS](../STATUS.md).

Evidence: [`application-observation.json`](../../runs/e05-installed-boot/m3-nvidia-gpu-system-stackshot-20261107-112/application-observation.json),
`guest-uploads/r112gpuraw3.tgz`, `r112gpudecode.tgz`, all earlier named trial
archives/preparation receipts, and
[`host-desktop-profile.json`](../../runs/e05-installed-boot/m3-nvidia-gpu-system-stackshot-20261107-112/host-desktop-profile.json).
