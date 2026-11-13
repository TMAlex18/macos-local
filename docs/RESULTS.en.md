> **Language:** **English** | [简体中文](RESULTS.md)

# Consolidated Research Results

Last consolidated: 2026-11-13 UTC. Results cover through Run156; the most recent runs yielding new conclusions are Run152–156 (2026-11-13: with the 30 s timeout diagnostic completely off, patches 0046–0050 pushed +720 s cold first-launch Safari strictly below nominal `3.000 s` across consecutive boots at **2.941 s** and **2.163 s**, set an all-time fresh-relaunch record of **1.417 s**, verified **2.10 h** continuous stability and paced lock-screen unlock, and in Run156 passed 98,304-byte zero-error multi-pass WebGL2/Metal readbacks, a 7/7 stock GUI app and `arm64e` framework suite, dual zero-underrun `afplay` PCM captures, and dual on-disk + kernel proof of clean APFS container unmounting).

| What You Need | Where to Look |
|---|---|
| Current phase, remaining work, next actions | [STATUS.en.md](STATUS.en.md) |
| Goals, architecture, and milestone definitions | [MASTER-PLAN.en.md](MASTER-PLAN.en.md) |
| Per-suite test pass/fail counts | [TEST-HISTORY.en.md](TEST-HISTORY.en.md) |
| Experimental procedures, failure samples, and boundaries | [reports/](reports/) |

## Summary

On a Linux x86_64 laptop running QEMU TCG, **unmodified macOS 27.0.1 (`26A434`, `VirtualMac2,1`) has completed full native restore, cold boot, and entry into a usable desktop**:

- Every stage of the boot chain executes stock Apple components and cryptographic signatures: `AVPBooter` → `iBSS` → `iBEC` → `restore`, personalized with live TSS tickets.
- Run `-53` completed full native restore; `-54` cold-booted independently from the installed disk; `-63` completed Setup Assistant, login, desktop, and Terminal across 8 vCPUs; `-64` verified cross-reboot file persistence and clean guest-initiated shutdown; `-65` verified `curl` HTTPS, Safari, and TextEdit.
- Graphics render on the host GPU via Vulkan across the Reims cross-ISA protocol, reaching the native macOS desktop on both NVIDIA and Intel GPUs; keyboard, mouse, networking, and audio all use standard virtual devices.
- Added across Run70–128: automatic DHCP and HTTP, VirtIO sound binding as the default CoreAudio device, byte-exact PCM output in selected runs, and fixed WebGL2 readback in Run128 (byte-for-byte identical against an independent oracle).

**Safari first-page load breakthrough, TCG throughput optimization, and expanded subsystem validation (Run143–156):** Fresh GPU-process initialization previously missed WebKit's 3-second watchdog. Per-vCPU lockless counters in Run143 (patch 0033) identified the sources of 409k–456k BQL acquisitions/s and 6,516–7,721 full-VM pauses/s under saturation; patches 0034–0039 cut saturated BQL acquisitions by 90% and raised running/runnable vCPU share to **96.6%** (3.4% total synchronization stall; [sync-sources report](reports/M3-QEMU-SYNC-SOURCES.en.md)). Two DWARF `perf`-guided throughput waves 0040–0050 (`CF_PCREL` jump-cache refinement, 16 KiB subpage invalidation, PAuth VA cache + SSSE3 QARMA5, SME pre-unpacking, 64B-aligned `512 KiB` 2-way 256-bucket inlined persistent `CPUJumpCache`, selective `TTBR0_EL1` flushes, BTI code-TLB fast path, `64 KiB` leaf PAuth op cache with `PAC->AUT` inverse pre-fill, and bit-exact hardware `F16C+AVX2+FMA` FP16 muladd) eliminated the `9.18%` softfloat FP16 hotspot, raised `[JIT]` P-core share to **25.71%** (**+90.2%** cumulative), drained post-login indexing 6+ minutes earlier, and lowered mean host package temperature to **68.6 °C**. With **the 30 s timeout diagnostic completely off**: `+720 s` cold first launch strictly cleared `< 3.000 s` across consecutive boots at **2.941 s** (Run155) and **2.163 s** (Run156) with 0 watchdog exits, and fresh relaunch hit an all-time record **1.417 s** (faster than non-cryptographic `impdef`; [throughput report](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md)). Run151 re-verified full signed native restore on `0045` in **2,614 s** (**2.57× faster** than `-53`); Run156 then verified a multi-pass WebGL2/Metal pipeline (offscreen FBO, depth/stencil, UBO, texture, instancing, source-over alpha blend: 98,304 bytes with 0 mismatches), 3 stock GUI apps + `jsc`/`sips`/`sqlite3`/`plutil`/`gzip` frameworks (7/7 passed), dual zero-underrun bit-exact `afplay` PCM captures, and on-disk + XNU `nx_mount` proof of clean APFS unmounting on the main OS container ([expanded validation report](reports/M3-EXPANDED-VALIDATION-RUN156.en.md)). Other unfulfilled targets are in Section 8.

## 1. Boot Chain and System Milestones

| # | Stage | Outcome | Evidence |
|---|---|---|---|
| 1 | Official materials | Full `VirtualMac2,1` 27.0.1 (`26A434`) IPSW (26,637,307,067 B) plus 5 small IM4P components, all CRC / SHA-256 verified | M1-RESTORE-FIRMWARE-ADT-CLOCK |
| 2 | AVPBooter | Decrypted and extracted on demand from the official OS image (AEA-encrypted APFS), 334,960 B, SHA-256 `6d010540…a845` | M2-ORIGINAL-AVPBOOTER |
| 3 | Unmodified ROM & DFU | Stock ROM executes and answers DFU (`05ac:1227`); fixed initial BDIF TX-ready state (0007) | Same as above |
| 4 | Linux USB transport | ROM → BDIF → USB/IP → `vhci_hcd` → unmodified `irecovery`/`libusb`, verified across 3 attach/query/detach cycles | M2-KERNEL-USBIP |
| 5 | Live TSS signing & iBSS | Queried nonce in-session → obtained live TSS ticket → transferred 353,151 B IMG4 across 173 chunks | M2-SIGNED-IBSS-TRANSFER |
| 6 | iBSS & Stage1 Recovery | Entry handoff (0008); reached Recovery (`05ac:1281`) after fixing AUX capacity, BDIF writes, and RX capacity (0009) | M2-IBSS-HANDOFF, M2-IBSS-RECOVERY |
| 7 | Stage1 configuration & bulk data | USB alternate-setting switch; bulk-OUT transfers up to 65,539 B verified byte-for-byte in guest RAM | M2-GATEWAY-SELECTION, M2-STAGE1-BULK-OUT |
| 8 | iBEC / Stage2 | Signed LocalPolicy; stock iBEC entry and Stage2 Recovery; config DMA (0010), RTC (0011), CTRR (0012/0016) | M2-NATIVE-RESTORE-CONTRACT |
| 9 | Full native restore | `-53` (0017): 6,707 s, `Restore Finished`, `seal_system_volume` / `create_system_snapshot` succeeded; Run151 (0045): **2,614 s** (2.57× faster, `bootx`→USB activation 17.3 s), producing a fresh sealed & cleanly unmounted target disk baseline; prior `-45…-52` failures and fixes documented in report | -53 results, Run151, M2-PAC-HVC-CTRR-LIVE-FOLLOWUP |
| 10 | Installed-OS cold boot | `-54`: fresh QEMU process and empty RAM with zero restore uploads; APFS volume group / sealed snapshot / normal SystemOS pivot formed the identity chain, launching `loginwindow` and `WindowServer` | -54 results |
| 11 | Initial GUI & input | `-57`/`-58`: language chooser visible with live keyboard and pointer UI feedback (requiring Reims 0006 heap-aliasing fix); subsequent `FramebufferFetch` abort resolved by Reims 0007 + `metal2vulkan` 0001 | -58 review |
| 12 | Networking | `-60`: `AppleVirtIONetwork` driver ready with bidirectional TCP 443 traffic (requiring ascending PCI capability order in 0019) | -60 network |
| 13 | User account & desktop | `-63` (8 vCPUs): completed Setup Assistant, password login, Finder/Dock/Terminal; Terminal confirmed `27.0.1`/`26A434`, `arm64`, 8 CPUs | -63 review |
| 14 | Persistence & shutdown | `-64`: files written in `-63` retained identical bytes and SHA-256 across a cold reboot; Apple menu Shut Down produced a guest-initiated `SHUTDOWN` event and clean QEMU exit | -64 verification |
| 15 | Representative apps | `-65`: `curl` HTTPS 200 (TLS certificate verification passed), pixel-exact local Safari page plus remote page, and byte-identical TextEdit document save | -65 applications |

## 2. Progress on the Installed System and Mainline Re-Verification (Run70–156)

Only consolidated findings appear below. Full per-run observation logs live in each run's individual report.

### 2.1 Verified Capabilities

| Capability | Outcome | Evidence |
|---|---|---|
| Native-window pixels | Older builds aborted under `native-window` with `FramebufferFetch / NotImplemented` (Run73–75, 77). After extending the `metal2vulkan` patch to cover `BGRA8Unorm` source-over blending, native-window pixels passed on both NVIDIA (Run83) and Intel (Run85, 88, 89, 91) | [Run83](reports/M3-BGRA8-NATIVE-VALIDATION-RUN83.md), [Run85](reports/M3-SAFARI-GPU-INPUT-RUN85.md) |
| Networking | Automatic DHCP (`10.0.2.15/24`), DNS, HTTPS, and guest HTTP GET/PUT | [Run85](reports/M3-SAFARI-GPU-INPUT-RUN85.md), [Run92](reports/M3-RUN92-NETWORK-DHCP.md), [Run106](reports/M3-RUN106-AUDIO-OFF-NETWORK-CONTROL.md) |
| Safari pages & unmodified 3 s cold launch | Warm and 4/8 GiB HTTP pages rendered expected pixels; on `0050` with the 30 s diagnostic off, `+720 s` cold first launch cleared `< 3.000 s` across consecutive boots at **2.941 s** (Run155) and **2.163 s** (Run156) with 0 watchdog exits, and fresh relaunch hit **1.417 s** | [Throughput](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md), [Run156](reports/M3-EXPANDED-VALIDATION-RUN156.en.md) |
| Sound device | `AppleVirtIOSound` bound and registered as CoreAudio's default input/output device (2-channel, 48 kHz), coexisting cleanly with networking | [Run105](reports/M3-RUN105-VIRTIO-SOUND-BINDING.md), [Run107](reports/M3-RUN107-AUDIO-NETWORK-COEXISTENCE.md) |
| PCM output | `afplay` output matched a 96,000-frame reference PCM stream byte-for-byte in Run108, 114, 121, and **both consecutive voices in Run156 on `0050`** (`0` internal zero-block underruns in `VSNDPCM1`). Older builds intermittently dropped frames (Section 2.3) | [Run108](reports/M3-RUN108-PCM-BIT-EXACT.md), [Run156](reports/M3-EXPANDED-VALIDATION-RUN156.en.md) |
| WebGL2 / Metal cross-ISA pipeline | Fixed six-tile dual-buffer readbacks passed **8/8** across cold boots; Run156 multi-pass pipeline (128x96 `RGBA8`+`DEPTH24_STENCIL8` FBO, `std140` UBO, 2D `REPEAT` texture, stencil clip, depth occlusion, 3-instance `drawArraysInstanced`, source-over alpha blend, and default-FBO `texelFetch` composite: 98,304 B) passed with **0 mismatched bytes** | [Run156](reports/M3-EXPANDED-VALIDATION-RUN156.en.md) |
| Stock GUI apps & `arm64e` framework suite | Run156: concurrent `Calculator`, `TextEdit`, and `System Information` launch, LaunchServices foreground registration, native window capture, and clean exit; `JavaScriptCore` (`jsc`), `sips` lossless PNG/resize, `sqlite3` WAL ACID, `plutil` `bplist00`, and `gzip`/SHA-256 all passed independent host verification (7/7) | [Run156](reports/M3-EXPANDED-VALIDATION-RUN156.en.md) |
| Guest shutdown & clean APFS unmount | Guest `shutdown -h now` produced QEMU exit `0`; read-only `check-apfs-clean-unmount.py` proved intact Fletcher-64 and `block0_xid == latest_checkpoint.xid` on both the main `Apple_APFS` OS container (`disk0s2`, all 6 volumes) and `Apple_APFS_ISC` (`disk0s1`), corroborated by zero `reloading after unclean unmount` logs from `apfs.kext` `nx_mount` on subsequent boots (standalone `disk0s3` Recovery retains its initial restore state) | [Run156](reports/M3-EXPANDED-VALIDATION-RUN156.en.md) |

### 2.2 Safari First-Page Load (Resolved on `0050` Mainline Across Consecutive Cold Boots)

**Symptom.** Opening a page for the first time in a freshly launched Safari instance frequently displays a blank page or an error banner stating that the webpage was reloaded because a problem occurred; subsequent reloads or warm Safari launches render normally.

**Mechanism (reproduced repeatedly since Run69).** WebKit enforces a 3-second responsiveness deadline on its GPU process. Under TCG, a newly spawned GPU process fails to finish initialization in time and is killed as unresponsive; replacement GPU processes are likewise killed mid-startup, and once more than 2 crashes occur within 30 seconds WebKit terminates all WebContent page processes. See [Run69](reports/M3-SAFARI-GPU-CAUSE-RUN69.md).

**What has been established:**

- At the instant the watchdog fires, the GPU process main thread is blocked synchronously waiting on `com.apple.audio.SandboxHelper`, which is itself still starting up (`xpcproxy` → `exec` → `dyld`/ObjC runtime initialization). See [Run110](reports/M3-RUN110-GPU-INITIALIZATION-STACKSHOT.md) and [Run112](reports/M3-RUN112-GPU-SANDBOXHELPER-STARTUP.md). This is the observed stall point, not a proven sole root cause.
- Elapsed time from IPC connection creation to watchdog firing sits consistently right at 3 seconds (2.96–3.00 s across Run114–124). Raising the deadline to 30 seconds allows initialization to finish in 4.081 s (Run127) and 3.781 s (Run128) with 0 GPU watchdog kills. Because Safari had been auto-restored at login and quit beforehand in both runs, those two figures represent second launches within the same boot.
- Repeated trials under a standardized protocol (Run131–137, 14 trials total, all completing under the 30-second diagnostic with 0 unresponsive kills) measured first-launch initialization after boot at 3.515–3.791 s (6 samples) and second-launch initialization at 2.768–3.419 s (6 samples). Run-to-run variance under identical settings spans 0.3 to 0.65 s. Run133 (2.907 s) and Run136 (2.768 s) were the first measurements ever observed below 3 seconds.
- Initialization decomposes into four sub-stages. Among them, the `SandboxHelper` connection lifetime from creation to teardown takes ~1 second across all 14 trials (0.771–1.084 s, mean 0.992 s), invariant to both PAC algorithm and launch order. See [PAC algorithm comparison](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.en.md).
- That `SandboxHelper` interval reflects cold process-spawn overhead rather than an idle timer wait (Run140): ~0.15 s from `launchd` spawn decision until `xpcproxy` `exec`s the target binary, ~0.3 s for dynamic linking and system library initialization, and ~0.25 s to compile and apply the Seatbelt sandbox profile, replying in under 0.1 s thereafter. Each `SandboxHelper` instance consumes only 0.6–1.1 CPU-seconds over its entire lifetime. See [RAM size control](reports/M3-RAM-SIZE-GPU-INIT-TIMING.en.md).
- During the initialization window, the GPU process itself performs little computation: its main thread consumes CPU for less than half the window duration (18%–39% across Run131–137) and spends the remainder waiting on replies from `tccd`, `coreaudiod`, `AudioComponentRegistrar`, and `SandboxHelper`. Substantively, the window consists of audio-subsystem initialization.
- Guest CPUs are saturated throughout the window (Run140): all 8 vCPUs are simultaneously busy roughly 90% of the time. From 90 seconds after boot until shutdown, the guest remains in this state almost continuously even when sitting untouched on the desktop—whereas sitting at `loginwindow` without logging in (Run138) averages only 2.3 busy vCPUs. Across a 20-minute post-login span, hundreds of background processes accumulate ~7,300 CPU-seconds. Even during "all-8-busy" intervals, each vCPU thread runs on a physical host CPU only ~76% of the time and is blocked ~22% of the time for reasons not yet fully decomposed. See [host CPU report](reports/M3-GPU-INIT-HOST-CPU-USE.en.md).
- Post-login background saturation subsides after roughly 20 minutes (Run142): per-minute mean busy vCPUs sit at 4.9–6.1 initially and drop to 1.3–2.4 starting at minute 22. Launching Safari on that quiet guest starts from 1.3 busy vCPUs right before pressing Return yet jumps back to 6.0 during the initialization window (all 8 busy 89% of the time): launching Safari alone saturates the guest CPUs. Initialization took 3.249 s (first launch) and 2.588 s (second launch)—both faster than the under-load ranges, yet still above 3 s on the first launch. See [quiet-guest report](reports/M3-QUIET-GUEST-GPU-INIT-TIMING.en.md).
- Whenever the guest was busy on earlier builds, vCPU threads spent 14%–30% of wall time stalled on QEMU synchronization (Run141–142). After decomposing trigger sources via per-vCPU counters (0033) and landing six optimization patches (0034–0039) across Run143–145, saturated BQL acquisitions fell by 90% and running/runnable vCPU share rose to **96.6%** (`bql` 0.2%–0.3%, exclusive stalls 2.8%). Landing patches `0040–0050` (persistent 2-way 256-bucket `512 KiB` inlined `CPUJumpCache`, 16 KiB subpage TLB + selective `TTBR0` flush, BTI fast path, `64 KiB` leaf PAuth op cache with `PAC->AUT` inverse pre-fill, SSSE3 QARMA5, and bit-exact hardware `F16C+AVX2+FMA` FP16 muladd) eliminated the `9.18%` softfloat FP16 bottleneck, raised `[JIT]` P-core share to **25.71%**, and drained post-login background indexing before `+720 s`. With **the 30 s timeout diagnostic completely off**, `+720 s` cold first launch cleared nominal `< 3.000 s` with **0 watchdog exits** across consecutive boots in Run155 (**2.941 s**) and Run156 (**2.163 s**), and fresh relaunch hit **1.417 s**. See [sync-sources report](reports/M3-QEMU-SYNC-SOURCES.en.md) and [throughput report](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md).

**Disproven hypotheses and changes that did not fix first load:**

| Hypothesis or Change | Outcome |
|---|---|
| Insufficient guest RAM (4 GiB vs. 8 GiB) | Both sizes achieved passing HTTP page renders in some runs (Run89, 94–97). Timed under the 30-second diagnostic (Run140, 1 run): 8 GiB cut VM compression by 60%–71% and decompression by ~80%, yet initialization took 3.444 s on the first launch and 3.061 s on the second—indistinguishable from the 4 GiB range |
| Inherently slow audio codec lookup | Standalone codec queries inside an ordinary un-sandboxed process are fast (~0.46 s first Opus query, Run104); that control does not cover the sandboxed Safari GPU-process path |
| Sampling / observation tooling slowed the guest | First-load failure reproduces identically with stack sampling disabled (Run104: 6 watchdog termination requests) |
| Exact PAC memoization (0022, 0024, 0029) | Sampled PAC CPU time fell 42.03% in Run86/87; hit rate scaled with table capacity (~66% at 64 entries, ~83–84% at 1024 entries, ~89% at 16384 entries), yet Safari first load failed at every capacity |
| PAC VA projection (0025), TB lookup state pointer (0026) | 0025 improved isolated microbenchmarks only (8.245 → 3.755 ns/call) while 0026 showed no measurable gain; first load still failed (Run116, 117) |
| Host P-core vCPU pinning (Run120), boot-only kernel observer (Run121), single output stream (Run122) | First load still failed in all three |
| Replacing QARMA5 + memo with `impdef` PAC (Run132–137, 3 runs per profile under the 30-second diagnostic) | First-launch initialization time did not move: 3.515–3.791 s on QARMA5 vs. 3.515–3.726 s on `impdef`. Second-launch ranges overlapped as well (2.768–3.419 s vs. 2.907–3.018 s). `impdef` sped up Safari's overall launch trajectory by 11%–21% (post-hoc analysis), without shortening the GPU initialization window |

**Diagnostic hook (the earlier workaround).** With this hook off, the 0050 build completes the first GPU initialization within the stock 3 s deadline (Run155–156); the patches stay in the tree, default-off, for comparison runs. Patches 0030 and 0031 provide a default-off diagnostic hook: when QEMU translates the two WebKit instruction sites that materialize the `3.0` s timeout constant (`site0`, `site1`), it substitutes `30.0` s at runtime without modifying guest code in memory or on disk, guarded by live verification of the shared-cache image UUID and surrounding instruction bytes in the active address space. Run125 tested the initial revision (0030) and recorded hit counts, but the target Safari process still timed out at ~3 s. Patch 0031 switched to deriving the shared-cache slide dynamically from the runtime PC (since dyld shared-cache slides differ across processes); in Run127–128 `site0` fired and GPU initialization completed.

This demonstrates only that granting extra time lets initialization finish—not that performance passes: `site1` has never fired, hit counters cannot be attributed per PID, and Run128 still logged 2 `WebProcess` unresponsive notifications (3 in Run127). Full details are in [E06-webkit-timeout](../experiments/E06-webkit-timeout/README.md).

### 2.3 Audio

- **Device binding.** `AppleVirtIOSound` bound and became the default audio device starting in Run105. In Run117–118 the sound card failed to register; Run118 traced the failure to a 4096-byte physical address mismatch between QEMU's and Apple's calculation of the split-virtqueue `used` ring offset. Setting queue size to 512 (patch 0028, opt-in, default remains 64) aligns both layouts and restored registration from Run119 onward. Patch 0027 separately fixed empty `JACK_INFO`/`CHMAP_INFO` queries being rejected as unsupported. Single-stream audio in Run109 also failed to register, but because that run preceded the `used`-ring root cause it does not establish that dual streams are required. Full analysis: [used-ring layout report](reports/M3-VIRTIO-SOUND-USED-RING-LAYOUT.md).
- **Intermittent PCM bit-exactness.** Complete (96,000/96,000 frames bit-exact): Run108, 114, 121. Missing frames (partially replaced by zeroed blocks): Run110, 113, 116, 119, 120, 122, 123, 124. Boundary tracing added from Run113 onward (patch 0023) proves the missing frames are already absent when QEMU dequeues TX buffers from the virtqueue, with zero corruption between virtqueue pop and WAV output; the upstream cause remains undetermined.
- **Unverified:** Microphone capture, physical host speaker playback, and audio latency.

### 2.4 Networking

- Most runs acquire a DHCP lease automatically; `en1` lacked an IPv4 address in Run91 and Run105 for reasons not isolated.
- In Run126 the guest had no default route. Read-only inspection found that `virtio-net`'s control virtqueue (size 64) suffered physical overlap between its `avail` and `used` rings under 16 KiB guest page alignment. Patch 0032 added an opt-in control-queue size property; setting it to 512 eliminates the overlap and restored normal networking in Run127–128, though this alone does not prove ring overlap was the sole cause in Run126.

### 2.5 Runtime and Host-Side Issues

- **Vulkan `DeviceLost`:** Observed in Run90 (shortly after reaching the desktop) and Run93 (after relaunching Safari); post-run host Vulkan health checks passed. See [Run90](reports/M3-RUN90-4G-DEVICE-LOST.md) and [Run93](reports/M3-RUN93-4G-HTTP-DEVICE-LOST.md).
- **Host GPU hang:** In Run111 the Linux host `i915` driver logged `GPU HANG` and reset the context, triggering `DeviceLost` and aborting QEMU. See [Run111](reports/M3-RUN111-INTEL-GPU-HANG.md).
- **Host machine crashes:** Two unplanned host reboots occurred during Run110 and Run115. Firmware BERT crash records from Run115 contained a 64-bit word that decodes as an `IA32_MCi_STATUS` fatal internal parity machine-check error; Run110's record lacked any such status word and its cause remains unknown.
- **Host CPU temperatures:** Across Run129's 10-minute smoke run, CPU package temperature averaged 78.7 °C and peaked at 96 °C. Across Run131–137 (21 to 33 minutes per run), package temperature averaged ~80 °C and peaked at 92–96 °C, with zero host resets across ~2.7 hours of continuous load. Run140 (23 minutes) averaged 80.5 °C (peak 92 °C); Run142 (41 minutes) averaged 77.1 °C (peak 91 °C).
- **Controller killed by parent session exit:** Run130 was interrupted after login when its launching shell session exited, delivering `SIGTERM` to the controller script which in turn terminated QEMU without an internal guest shutdown; the next run booted cleanly using `--allow-unclean-storage`.
- **Direct host signal delivery to QEMU on session exit:** Run138 was terminated 662 s after launch (prior to login) when QEMU itself received a host signal (`SHUTDOWN reason=host-signal`) before the controller could run its cleanup hook. Consequently, Run139 was refused at preflight (producing no VM run) until stale temporary symlinks were cleared via `recover-abandoned.py --run <label>` and `--allow-unclean-storage` was supplied for Run140 (which booted normally). Run141 was similarly interrupted at 672 s; in that case the controller caught `SIGTERM` and completed teardown cleanly, allowing Run142 to boot normally with `--allow-unclean-storage`.
- **Clean guest shutdown misrecorded as host termination:** In Run133, 135, and 137 the guest shut down cleanly and QEMU exited `0`, but a periodic QMP poll landed in the narrow window after QEMU closed its QMP socket and before process exit; the send error aborted the teardown reader before draining the buffered `SHUTDOWN` event, recording `teardown=terminate`. Root cause and fix are documented in [PAC algorithm comparison](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.en.md).
- **Native window freeze:** Late in Run112 the native presentation window stopped updating for over 700 s while QMP and the guest kernel remained alive.
- **Lock-screen unlock failure:** Run89 hit `AppleVPKeyStore` `unlock failed (-5)`; Run102 unlocked successfully after clearing the password field and typing at a slower cadence, without proving causality.
- **Collector `passed=false` flags:** Most instances merely reflect post-shutdown final collection failing after the guest had already powered off, with an empty error array—not a failed run.

### 2.6 Run Summary Table

"First-load failed" means a freshly launched Safari's GPU process was killed by the 3-second watchdog. Run113–128 have no standalone markdown reports; their `[record]` links point to local per-run JSON observation artifacts under `runs/` (Git-ignored), while cross-run analyses appear in [PAC memo application comparisons](reports/M3-PAC-MEMO-APP-COMPARISON.md) and [used-ring layout report](reports/M3-VIRTIO-SOUND-USED-RING-LAYOUT.md).

| Run | Configuration / Action | Outcome | Details |
|---|---|---|---|
| 70, 71 | 3600 s idle observation; 1200 s targeted launch collection | Safari never triggered; no new conclusions | [70](reports/M3-SAFARI-GPU-FOLLOWUP-RUN70.md), [71](reports/M3-SAFARI-GPU-TARGETED-RUN71.md) |
| 73–77 | `native-window` takeover (older build) | 73–75 and 77 aborted on `FramebufferFetch`; 76 served as headless control | — |
| 81, 82 | Headless login, desktop, and Safari | UI screenshots verified; external network failed in 81 (cause unisolated), collector failed in 82 due to QEMU PID mismatch | [81](reports/M3-SAFARI-GPU-TAKEOVER-RUN81.md) |
| 83 | New BGRA8 build on NVIDIA | Clean boot, login, and native-window pixels passed | [83](reports/M3-BGRA8-NATIVE-VALIDATION-RUN83.md) |
| 85 | Intel GPU, networking & Safari | DHCP/DNS/HTTPS, warm Safari, and guest shutdown passed; first-load failed; PAC accounted for ~42.61% of vCPU samples | [85](reports/M3-SAFARI-GPU-INPUT-RUN85.md) |
| 86, 87 | PAC memo off vs. on (identical binary) | Sampled PAC CPU time dropped 42.03%; first-load failed in both | [comparison](reports/M3-PAC-MEMO-APP-COMPARISON.md) |
| 88 | Audio-unit enumeration timing | Two `auval` enumerations took 24.16 s and 20.25 s, followed by continued GPU timeouts | [85](reports/M3-SAFARI-GPU-INPUT-RUN85.md) |
| 89 | 8 GiB RAM | Fresh Safari HTTP page succeeded (first meaningful paint ~19.4 s); later lock-screen unlock failed | [89](reports/M3-RUN89-8G-MEMORY-CONTROL.md) |
| 90 | 4 GiB RAM | Vulkan `DeviceLost` after reaching desktop | [90](reports/M3-RUN90-4G-DEVICE-LOST.md) |
| 91 | 4 GiB RAM, local `file://` page | Fresh first-load succeeded (first meaningful paint ~3.9 s), clean guest shutdown; no IPv4 lease in that run | [91](reports/M3-RUN91-4G-LOCAL-FRESH.md) |
| 92 | Networking | Automatic DHCP, HTTP upload, clean guest shutdown; Safari not launched | [92](reports/M3-RUN92-NETWORK-DHCP.md) |
| 93 | 4 GiB RAM, HTTP page | Page rendered; `DeviceLost` after relaunching Safari | [93](reports/M3-RUN93-4G-HTTP-DEVICE-LOST.md) |
| 94–97 | HTTP page controls: 4 GiB (94–96) vs. 8 GiB (97) | Matching RGB pixel counts across runs; guest shutdown in 94, host-terminated in 95–97 | [94](reports/M3-RUN94-4G-HTTP-FRESH-CONTROL.md), [95](reports/M3-RUN95-4G-HTTP-REPEAT.md), [96](reports/M3-RUN96-4G-HTTP-REPEAT-BOUNDED.md), [97](reports/M3-RUN97-8G-HTTP-MATCHED-CONTROL.md) |
| 98, 99 | Added watchdog archive harness | Harness issues (stole window focus in 98; host upload listener absent in 99); inconclusive | [98](reports/M3-RUN98-WATCHER-HARNESS-LOSS.md), [99](reports/M3-RUN99-WATCHER-SERVICE-MISSING.md) |
| 100 | Guest file exchange; fresh Safari | Retrieved 3 guest diagnostic archives; reproduced 8 GPU watchdog kills | [100](reports/M3-RUN100-WATCHDOG-ARCHIVE.md) |
| 101, 102 | — | Runs interrupted; 102 succeeded in unlocking screen and reaching desktop | [101](reports/M3-RUN101-INTERRUPTED.md), [102](reports/M3-RUN102-DESKTOP-INTERRUPTED.md) |
| 103 | Sampling controls | 4 watchdog requests; documented sampling-tool timing caveats; guest shutdown | [103](reports/M3-RUN103-EARLY-SAMPLE-CONTROLS.md) |
| 104 | Isolated codec queries | Standalone queries proved fast; 6 watchdog requests persisted with sampling off | [104](reports/M3-RUN104-ISOLATED-CODEC-QUERIES.md) |
| 105 | Enabled VirtIO sound | Device bound as default audio device; no IPv4 lease in that run | [105](reports/M3-RUN105-VIRTIO-SOUND-BINDING.md) |
| 106 | Audio-off network control | DHCP, HTTP, and guest shutdown passed | [106](reports/M3-RUN106-AUDIO-OFF-NETWORK-CONTROL.md) |
| 107 | Audio + network coexistence | Coexistence verified; Safari first-load failed (error page) | [107](reports/M3-RUN107-AUDIO-NETWORK-COEXISTENCE.md) |
| 108 | `afplay` PCM verification | Both playback trials byte-for-byte exact; first-load failed (3 watchdog kills) | [108](reports/M3-RUN108-PCM-BIT-EXACT.md) |
| 109 | Single-stream audio; `stackshot` harness | Sound card did not register; `stackshot` capture verified | [109](reports/M3-RUN109-SINGLE-STREAM-STACKSHOT-CONTROLS.md) |
| 110 | Restored dual streams; GPU-init `stackshot` | PCM missed 764 frames; GPU main thread caught waiting on audio `SandboxHelper`; unplanned host reboot afterward | [110](reports/M3-RUN110-GPU-INITIALIZATION-STACKSHOT.md) |
| 111 | — | Host `i915` GPU hang aborted QEMU | [111](reports/M3-RUN111-INTEL-GPU-HANG.md) |
| 112 | `stackshot` symbolication | Confirmed `SandboxHelper` was still mid-startup when watchdog fired | [112](reports/M3-RUN112-GPU-SANDBOXHELPER-STARTUP.md) |
| 113, 114 | PAC memo 64 vs. 1024 entries (0023, 0024) | Hit rate ~66% → 84%; first-load failed in both; PCM dropped frames in 113, bit-exact in 114 | record, record |
| 115 | Patch 0025 profile | Interrupted by second unplanned host reboot | record |
| 116 | PAC VA projection (0025) | First-load failed; PCM dropped frames | record |
| 117 | TB lookup state pointer (0026) | Sound card failed to register; first-load failed | record |
| 118 | Empty audio-info query fix (0027) | Sound card still unregistered; pinpointed 4096-byte `used`-ring offset mismatch | record |
| 119 | Sound queue size 512 (0028) | Audio registration restored; PCM dropped frames; first-load failed | record |
| 120 | Host P-core vCPU pinning | PCM dropped frames; first-load failed | record |
| 121 | Boot-only kernel observer | Both PCM trials bit-exact; first-load failed | record |
| 122 | Output-only single audio stream | Audio registered; PCM dropped frames; all 5 GPU processes killed by watchdog | record |
| 123, 124 | PAC memo 16384 vs. 1024 entries (0029) | Hit rate ~89% vs. 83%; both first-load and PCM failed; 124 terminated from host | record, record |
| 125 | Initial 30 s timeout diagnostic (0030) | Hit counter incremented, yet target Safari still timed out at ~3 s | record |
| 126 | Runtime-slide timeout diagnostic (0031) | Guest lacked default route, blocking trial; uncovered `virtio-net` control-queue ring overlap | record |
| 127 | `virtio-net` control queue 512 (0032) | Networking restored; Safari GPU initialization finished in 4.081 s and page rendered | record |
| 128 | Fixed WebGL2 readback | Both readbacks passed byte-for-byte; GPU init 3.781 s; clean guest shutdown | record |
| 129 | Post-hiatus smoke run: stock boot, login, shutdown (ordinary 3 s deadline) | Reached `loginwindow` in 150 s; guest shutdown, QEMU exited `0`; host package temp peaked at 96 °C | [129](reports/M3-RUN129-SMOKE.en.md) |
| 130 | Dry run for PAC algorithm comparison (QARMA5, 30 s diagnostic) | Interrupted post-login (`SIGTERM` to controller); 0 samples, unclean guest stop | [comparison](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.en.md) |
| 131 | Repeat dry run (QARMA5, `--allow-unclean-storage`) | Clean boot; baseline-matched GPU init 4.866 s and 3.802 s; guest shutdown | [comparison](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.en.md) |
| 132 | QARMA5 + memo, round 1 | First launch 3.791 s, second launch 3.348 s | [comparison](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.en.md) |
| 133 | `impdef`, round 1 (first-ever boot of installed OS under `impdef`) | First launch 3.726 s, second launch 2.907 s | [comparison](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.en.md) |
| 134 | QARMA5 + memo, round 2 | First launch 3.515 s, second launch 3.419 s | [comparison](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.en.md) |
| 135 | `impdef`, round 2 | First launch 3.693 s, second launch 3.018 s | [comparison](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.en.md) |
| 136 | QARMA5 + memo, round 3 | First launch 3.727 s, second launch 2.768 s | [comparison](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.en.md) |
| 137 | `impdef`, round 3 | First launch 3.515 s, second launch 3.009 s; conclusion: PAC algorithm is not the governing factor | [comparison](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.en.md) |
| 138 | Host CPU sampling (QARMA5 + memo, 4 GiB) | Interrupted at 662 s prior to login by session exit (0 trials); 11 min of sampling validated the harness and measured 2.3 mean busy vCPUs at `loginwindow` | [host CPU](reports/M3-GPU-INIT-HOST-CPU-USE.en.md) |
| 139 | 8 GiB RAM control | No VM launched: controller preflight refused startup due to uncleaned temporary symlinks from Run138 | [RAM](reports/M3-RAM-SIZE-GPU-INIT-TIMING.en.md) |
| 140 | 8 GiB RAM control with host sampling and log-window export | First launch 3.444 s, second 3.061 s—no material RAM-size effect; all 8 vCPUs simultaneously busy ~90% of the init window; `SandboxHelper` slice traced to process-spawn cost; clean guest shutdown recorded properly | [RAM](reports/M3-RAM-SIZE-GPU-INIT-TIMING.en.md), [host CPU](reports/M3-GPU-INIT-HOST-CPU-USE.en.md) |
| 141 | Quiet-guest trial (QARMA5 + memo, 4 GiB) | Interrupted at 672 s (0 trials); two off-CPU block samples during saturation showed vCPU threads spending 21%–26% of time waiting on `bql` and exclusive sections | [quiet guest](reports/M3-QUIET-GUEST-GPU-INIT-TIMING.en.md) |
| 142 | Repeat quiet-guest trial | Background load subsided ~22 min after boot; quiet first launch 3.249 s, second 2.588 s; init window still CPU-saturated; clean guest shutdown | [quiet guest](reports/M3-QUIET-GUEST-GPU-INIT-TIMING.en.md) |
| 143 | Per-vCPU sync-source counters (0033, `--sync-stats`) and block sampling | First launch 3.704 s, second 2.818 s; 409k–456k `bql` locks/s and 6,516–7,721 exclusive sections/s, with 27.3% vCPU time blocked during Safari launch; pinpointed top four causes (`CNTVCT_EL0` 96% of cpreg reads, masked-IRQ/`EXITTB` no-ops 29% of BQL, broadcast `TLBI` outer-loop stampedes + exclusive pauses, PAC `HVC` heap-allocated SHA-256 under BQL); clean guest shutdown | [sync sources](reports/M3-QEMU-SYNC-SOURCES.en.md) |
| 144 | Top-four `bql` contention patches (0034–0037) A/B evaluation | `bql` rate down 77%–82%, `bql` stall share down to 0.4%–1.3% (-90% to -98%); end-to-end Safari launch ~43% faster (105→60 s, 76→43 s), GPU init down to **3.336 s** and **2.612 s** (new under-load lows, 0 WebProcess unresponsive notices); remaining stall dominated by per-`TLBI` `start_exclusive` (11.1%); clean guest shutdown | [sync sources](reports/M3-QEMU-SYNC-SOURCES.en.md) |
| 145 | Broadcast `TLBI` `DSB` batching and EL1&0 `ERET` BQL elision (0038–0039) | Saturated vCPU running/runnable share rose to **96.6%** (total blocked **3.4%**, `bql` 0.2%, exclusive stalls down 75% to 2.8%, cumulative `bql` rate down 90%); second launch set all-time low **2.431 s** (first launch during peak background load 3.798 s, 0 unresponsive notices); clean guest shutdown | [sync sources](reports/M3-QEMU-SYNC-SOURCES.en.md) |
| 146 | **Diagnostic-off** unmodified-deadline and fixed WebGL2 trial on quiet guest | Quiet reached at 1123 s (6.5 min earlier); **without `--webkit-timeout-diagnostic`, cold first-launch Safari initialized with 0 GPU watchdog exits (3.219 s) and passed both bit-exact WebGL2 readbacks**; second launch **1.912 s** (first sub-2 s); clean guest shutdown | [sync sources](reports/M3-QEMU-SYNC-SOURCES.en.md) |
| 147 | **Diagnostic-off cold-boot repeatability & symbol-matched `0039` `perf` profile** | Quiet reached at 1335 s; **H1 falsified (1/2 zero-watchdog repeatability)**: 1st GPU timed out at `3.008 s` (without Run146's 0.22 s runloop slack), then 4th GPU self-recovered in `1.661 s` without diagnostic and **both bit-exact WebGL2 readbacks passed (2/2)**; `perf` proved **80.3%–81.1% of P-core cycles execute in QEMU C code** (`[JIT]` only 13.5%–14.5%), isolating `CF_PCREL` jump-cache wipes, 16 KiB `large_page_mask` full-TLB flushes, PAuth VA/scalar QARMA5 overhead, and SME `do_fmopa_h` `2N²` unpacking; clean guest shutdown | [throughput profile](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md) |
| 148 | **Diagnostic-off validation & `perf` re-profile of `0040–0043` TCG throughput build** | Quiet reached at **958 s** (6.3 min earlier than Run147); **`[JIT]` P-core share jumped +71.6% (`13.52% → 23.20%`)**, eliminating SME and 16 KiB full-TLB flushes; **all 3 diagnostic-off launches passed with 0 watchdog exits**: cold 1st launch set record **`3.180 s`** (`constructor->proxy` `23.5 → 9.12 s`, **both WebGL2 readbacks `bit_exact=true`**), unperturbed 2nd launch **`1.620 s`**, 3rd launch under `perf` `2.056 s`; isolated `TTBR0_EL1` jump-cache and kernel/phys-TLB flushes for `0044–0045`; clean guest shutdown | [throughput profile](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md) |
| 149 | **Diagnostic-off validation of `0044–0045` persistent `CPUJumpCache` & selective `TTBR0` flush** | `tcg_flush_jmp_cache` and `vmsa_ttbr_write` dropped to `0.00%`, Safari-launch `tb_htable_lookup` fell 62.7% (`14.43% → 5.38%`), saturated `[JIT]` rose to **`24.12%`** (+78.4% cumulative); **cold 1st launch passed without diagnostic in `2.734 s` (0 watchdog exits, strictly below nominal `3.000 s` without runloop slack, `entry->registrar` vCPU-s down 37.4%, both WebGL2 readbacks `bit_exact=true`)**; 2nd launch `1.679 s`, 3rd under `perf` `1.856 s` (mean host temp down to `71.2 °C`); clean guest shutdown | [throughput profile](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md) |
| 150 | **`+720 s` peak-background-load diagnostic-off cold launch validation (`0045`)** | Triggered directly at `+720 s` (`vcpu_mean_busy = 6.32`) without waiting for quiet: **without `--webkit-timeout-diagnostic`, peak-load cold 1st launch passed on the 1st attempt with 0 watchdog exits (`3.254 s`, 14.3% faster than Run145's `3.798 s` at `+720 s`, both WebGL2 readbacks 5/5 `bit_exact=true`)**; `+1020 s` 2nd launch **`1.662 s`** (0 exits, 0 Web notices, 31.6% faster than Run145); **8/8** consecutive diagnostic-off zero-watchdog launches across Run148–150; clean guest shutdown | [throughput profile](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md) |
| 151 | **Full signed native restore re-verification & fresh disk baseline on `0045` mainline (2-vCPU QARMA5)** | Fresh blank `128 GiB` / `32 MiB` target passed DFU → iBSS → LocalPolicy → iBEC Stage2 → 5 signed restore payloads → XNU RestoreKernel (`bootx`→USB activation **`17.334 s`**, **6.85× faster** than `-43`'s `118.704 s`) → `idevicerestore` **`Status: Restore Finished`** (`0x68B` seal, `0x669` snapshot, `0x642` unmount all `result=0`); **total elapsed `2,613.93 s` (`43.57 min`), `2.57×` faster (-61.0%) than `-53` (`6,706.78 s`)**; host CPU package temp mean `67.6 °C` (peak `93 °C`) | Run151 |
| 152 | **Diagnostic-off peak-window validation of `0046–0048` 2-way inlined jump cache, PAuth fast path & BTI TLB** | Eliminated `arm_stage1_mmu_idx`/`is_guarded_page`, cut `probe_access_internal` 41.9% and `qht_lookup` 32.3%, but exposed `+2.63%` net L2 miss loss from `256 KiB` L2 QARMA table; `+720 s` Trial 1 self-recovered after 1 watchdog + bit-exact WebGL2 (6/6), `+1020 s` `1.795 s`, concurrent-`perf` `1.725 s`; clean guest shutdown | [throughput profile](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md) |
| 153 | **Diagnostic-off validation of `0049` (`64 KiB` leaf PAuth op cache + `PAC->AUT` inverse pre-fill, L2 table removed)** | Raised `[JIT]` to `24.82%`, cut PAuth self share from `21.1%` to `12.1%–13.4%` (`pauth_op_miss_aut` `0.44%`); 3/3 zero-watchdog launches (`+720 s` `3.314 s` + WebGL2 7/7, `+1020 s` **`1.659 s`** with `entry->registrar = 0.760 s`, 3rd `2.185 s`); uncovered `9.18%` `helper_gvec_fmla_idx_h` FP16 hotspot and `1.0 MiB` jump-cache L1.5 miss cost; clean guest shutdown | [throughput profile](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md) |
| 154 | **`0050` mainline 2.10-hour continuous soak, paced lock-screen unlock & clean shutdown** | Host pause spanned `+720 s`; woke display, unlocked lock screen on 1st attempt via `guest_ui.py` `--hold 0.35 --gap 0.35` pacing (preventing 16-slot HID queue overflow), verified **`7,550.7 s` (`2.10 h`, `191.38B` PAuth ops at `89.06%` hit rate, `1.5 ms` clock spread, `67.3 °C` mean temp)**, and shut down cleanly | [throughput profile](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md) |
| 155 | **Diagnostic-off `+720 s` evaluation of `0050` (`F16C+AVX2+FMA` FP16 muladd & `512 KiB` 256-bucket jump cache)** | `float16_muladd` dropped `9.18% -> < 0.05%`, `helper_lookup_tb_ptr` fell `-6.29%`, Safari-launch `[JIT]` hit **`25.71%`**; post-login storm drained by `+660 s` (`-7.7 °C` cooler mean temp **`68.6 °C`**, peak `92 °C`); **`+720 s` cold 1st launch broke `< 3.000 s` at `2.941 s` (0 exits) + WebGL2 8/8**, `+1020 s` `1.803 s`, **concurrent-`perf` Trial 3 set all-time project record `1.417 s` (faster than `impdef`)**; clean guest shutdown | [throughput profile](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md) |
| 156 | **Expanded installed-system validation on `0050` (multi-pass WebGL2/Metal, stock GUI/framework suite, dual-voice audio, APFS clean unmount)** | **`+720 s` diagnostic-off cold 1st launch repeated `< 3.000 s` at `2.163 s` (0 exits, 2/2 cold boots)**; **98,304-byte multi-pass WebGL2/Metal readbacks passed with 0 mismatched bytes**; **3 stock GUI apps + `jsc`/`sips`/`sqlite3`/`plutil`/`gzip` passed 7/7**; **both `afplay` voices passed `full_tone_bit_exact=true` with `0` zero-block underruns in `VSNDPCM1`**; read-only on-disk checkpoints (`disk0s2` `xid 88420 == 88420`, `disk0s1` `555 == 555`) + `nx_mount` logs proved clean OS container unmount | [expanded validation](reports/M3-EXPANDED-VALIDATION-RUN156.en.md) |

## 3. ARM Platform (M1 / E06, Self-Authored Firmware, Zero Apple Code)

| Contract | Outcome | Report |
|---|---|---|
| VMApple config, UART, PSCI | Passed across 1 / 2 / 4 vCPUs | [M0-RESULTS](reports/M0-RESULTS.en.md) |
| Timer / GICv3 & 16 KiB MMU | 10/10 (including 4 expected negative controls) | M1-TIMER-MMU-BOOT-AUDIT |
| Exact 24 MHz timebase | Fixed ~1.6% fast drift in legacy implementation; exposed via default-off `x-cntfrq-exact` | [M1-EXACT-CLOCK-RESULTS](reports/M1-EXACT-CLOCK-RESULTS.en.md) |
| Dual-vCPU MMU / ASID / TLBI / atomics | 14/14 | [M1-SMP-MMU](reports/M1-SMP-MMU.en.md) |
| EL0 context integrity | Full `Q0..Q31`, `FPCR`/`FPSR`/TLS, PAC + IRQ preemption, dual-vCPU migration, and lazy FP restore, all with negative controls | [M1-EL0-SIMD32](reports/M1-EL0-SIMD32.en.md) |
| PAC HVC software model | Default-off reference model bridging the Apple PAC HVC gap; not cryptographically equivalent to Apple hardware | [M1-PAC-HVC-RESTORE-TRANSPORT](reports/M1-PAC-HVC-RESTORE-TRANSPORT.en.md) |
| PAC emulation cost | Per-`PACIBSP`/`RETAB` QARMA5 overhead reduced from ~550 ns to ~214 ns via 0018/0020/0021, and further to **57.7 ns** (3.71× faster) via 0042 (VA cache + SSSE3 `pshufb` QARMA5); self-authored microbenchmark only, macOS impact measured separately | [E06-pauth-hvc](../experiments/E06-pauth-hvc/README.md) |
| AES, AVP RTC, CTRR | Standard supplied-key AES DMA/IRQ passed; RTC and CTRR contracts partially verified under self-authored guests and stock boot—not full hardware equivalence | M2-NATIVE-RESTORE-CONTRACT |

## 4. Transport and Storage (E05)

| Component | Outcome | Report |
|---|---|---|
| BDIF USB | Framing, queue, and connection lifecycle fixes (0006–0009); 18/18 transport scenarios, 1,049,600 B backpressure transfer byte-exact | [M1-BDIF-USB-TRANSPORT](reports/M1-BDIF-USB-TRANSPORT.en.md) |
| BDIF block storage | 9/9 R/W, flush, and bounds checks; dynamic capacity derived from backing image size (0017, fixing hardcoded 64 GiB constant against 128 GiB backing file) | M2-IBSS-RECOVERY |
| Kernel VirtIO USB & `restored` | Standard transfers via 0015 carried an entire native restore end-to-end across stock Apple drivers and `usbmux`/`restored` sessions | M2-PAC-HVC-CTRR-LIVE-FOLLOWUP |
| VirtIO networking | Default-off patch 0019 reorders PCI capabilities into ascending offset order when explicitly enabled, allowing the stock Apple driver to bind | [E05-installed-boot](../experiments/E05-installed-boot/README.md) |

## 5. Graphics (E01–E03 / E11)

| Area | Outcome | Report |
|---|---|---|
| Host Vulkan (E02) | Compute and offscreen render/readback passed on both Intel and NVIDIA | [M0-RESULTS](reports/M0-RESULTS.en.md) |
| Cross-GPU sharing (E03) | One specific host-visible DMA-BUF scheme failed in both directions, refuting only that single scheme | Same as above |
| Cross-ISA protocol (E11) | Separated ARM guest 16 KiB pages from Linux host 4 KiB pages (Reims 0002–0005); Reims Core serial suite passed 2294/0 (ARM16K) and 2300/0 (x86) | [M1-ARM-REIMS](reports/M1-ARM-REIMS.en.md) |
| Self-authored guest GPU | Compute/draw on both GPUs, completion IRQs, IOSurface lifecycle, native-window presentation, and USB HID keypress-to-new-frame loop, all with negative controls | [E11](../experiments/E11-arm-reims/README.en.md) |
| Unmodified macOS rendering | Historical Setup Assistant, desktop, Safari, and TextEdit rendering verified; native login/desktop pixels verified on NVIDIA under the new BGRA8 build in Run83 | Section 1 rows 11–15 and Section 2 |

## 6. Static and Offline Analysis of Apple Components

- **Signature chain:** Verified IM4M RSA-4096 signature chains up to the Apple Root CA (43 tests) and decoded certificate-constraint extensions into 13 evaluation rules (56 tests). See M2-IM4M-OFFLINE-VERIFICATION and M2-CERTIFICATE-CONSTRAINTS.
- **SystemOS & `Virtualization.framework`:** Parsed sealed APFS volumes and reconstructed `Virtualization.framework`'s raw `__TEXT` segment (2,979,720 B). See M2-APPLE-FRAMEWORK-FILESYSTEM-AUDIT.
- **AUX storage:** Statically established container layout, UID key generation/wrapping, configuration schema, and XPC consumer paths (`M2-VZ-*` report series). Stock `Virtualization.framework` AUX initialization has never been executed here, and live native restore proved not to require pre-populated AUX contents; see M2-NATIVE-AUX-READINESS.

## 7. Source Worktrees and Patches

| Worktree | Pinned Baseline | Patch Stack | Purpose |
|---|---|---|---|
| `sources/qemu-bdif-usb` | `qemu-reims-vgpu` `1a72df9` | 0003, 0005, upstream USB reference patch, 0006–0050 | Active mainline: stock firmware, native restore, and desktop |
| `sources/reims-arm-guest` | `reims-vgpu` `86d4921` | Reims 0001–0007 | ARM 16 KiB guest graphics library |
| `sources/metal2vulkan-research` | `ecb80e0` | `metal2vulkan` 0001 | `FramebufferFetch` / source-over blending |
| `sources/qemu-reims-arm` / `qemu-clock-exact` | `qemu-reims-vgpu` `1a72df9` | 0003+0004 / 0003+0005 | Early graphics mainline / exact-clock testbed |
| `sources/qemu-headless`, `qemu-portable`, `reims-build` | Respective baselines | 0001 / none / Reims 0001 | Headless control, diagnostics, x86 graphics library |

Current mainline binary: `build/qemu-bdif-usb/qemu-system-aarch64`, SHA-256 `e6f7b2cb32df3779531cf1b93612a67d8e1fa350c8b894c19d0c9e71a60f4a51`, patch series recorded through 0050; see build record.

**Patches 0022–0050 (added from Run86 onward):**

| Patch | Change | Observed Effect on the Installed System |
|---|---|---|
| 0022 | Per-vCPU exact PAC memoization (default-off) | Sampled PAC CPU time dropped 42.03% (Run86/87); no application-level speedup proven |
| 0023 | `virtio-snd` PCM boundary tracing (default-off) | Isolated where dropped audio frames originate (Run113 onward) |
| 0024 | Configurable PAC memo capacity and hit/miss counters | Enabled 64-entry vs. 1024-entry comparison (Run113/114) |
| 0025 | PAC virtual-address projection | Microbenchmark improved 8.245 → 3.755 ns/call; Safari first-load still failed in Run116 |
| 0026 | State-pointer indirection for TB lookup | No measurable performance gain (Run117) |
| 0027 | Valid replies to empty `virtio-snd` `JACK_INFO`/`CHMAP_INFO` queries | Queries succeeded, though audio device remained unregistered until 0028 (Run118) |
| 0028 | Configurable `virtio-snd` queue size (default 64) | Setting 512 aligned guest/host `used`-ring layouts and restored registration (Run119) |
| 0029 | Raise maximum PAC memo capacity to 16384 entries | Higher hit rate (~89%), first-load still failed (Run123/124) |
| 0030 | Guarded 30 s timeout diagnostic at two WebKit sites (default-off) | Initial revision did not make target Safari pass in Run125 |
| 0031 | Derive shared-cache base slide from runtime PC in timeout diagnostic | `site0` fired and GPU initialization completed in Run127–128 |
| 0032 | Configurable `virtio-net` control-queue size (default 64) | Setting 512 eliminated ring overlap and restored networking (Run127–128) |
| 0033 | Per-vCPU lockless TCG sync-source counters (default-off) | Run143 decomposed 409k–456k `bql`/s and 6,516–7,721 exclusive pauses/s |
| 0034–0037 | Lockless `CNTVCT_EL0`, masked-IRQ/`EXITTB` BQL elision, lockless stack-HMAC PAC `HVC` + 64-entry cache, BQL-free `TLBI` queue drain | Run144 cut `bql` stalls by 90%–98%, sped up Safari launch ~43%, and lowered GPU init to 3.336 / 2.612 s |
| 0038–0039 | Broadcast `TLBI` `DSB` batching + per-insn `hflags`/TB-exit suppression, EL1&0 `ERET` BQL elision | Run145 raised saturated running share to 96.6% (3.4% blocked); Run146 passed diagnostic-off once (3.219 / 1.912 s), while Run147 showed 1/2 zero-watchdog repeatability and isolated the 80%–81% C-helper bottleneck |
| 0040–0043 | Selective `CF_PCREL` TB jump-cache invalidation + 256 page buckets, `cputlb` 16 KiB subpage invalidation (fixing `tlb_flush_range_locked` byte-vs-VA unit bug) + `!EL2` inline fast paths, PAuth VA cache + SSSE3 `pshufb` QARMA5, SME `do_fmopa_h` pre-unpacking | Bare-metal QARMA5 `PACIBSP/RETAB` cut to **57.7 ns** (-73.0%); Run148 raised `[JIT]` P-core share +71.6% (`13.5% → 23.2%`), reached quiet at 958 s, and passed 3/3 diagnostic-off launches with 0 watchdog exits (`3.180 s` / `1.620 s` / `2.056 s`) |
| 0044–0045 | Host-RAM-tagged persistent `CPUJumpCache` across TLB flushes (bypassing global `qht_lookup_custom` on shared-cache context switches) + selective `TTBR0_EL1` ASID switch preserving `ARMMMUIdx_Phys_NS` and `TTBR1_EL1` kernel upper-half SoftMMU TLBs | Run149 eliminated `tcg_flush_jmp_cache` and `vmsa_ttbr_write`, cut `tb_htable_lookup` 62.7%, raised saturated `[JIT]` to `24.12%`, and **pushed quiet cold 1st launch strictly below `3.000 s` to `2.734 s` without diagnostic**; **Run150 passed `+720 s` peak-load diagnostic-off cold 1st launch with 0 watchdog exits (`3.254 s`, 2nd `1.662 s`)** |
| 0046–0050 | 64B-aligned `512 KiB` 2-way 256-bucket inlined jump cache (0046/0049/0050), near-`env` PAuth state & pre-unpacked SSSE3 constants (0047), BTI `is_guarded_page` code-TLB fast path (0048), per-vCPU `64 KiB` leaf PAuth op cache with `PAC->AUT` inverse pre-fill (0049), and bit-exact hardware `F16C+AVX2+FMA` `float16_muladd_scalbn` & `gvec_fmla_idx_h` fast paths (0050) | `float16_muladd` dropped `9.18% -> < 0.05%`, PAuth self share fell ~40%, `[JIT]` reached **`25.71%`**, background indexing drained 6+ min earlier at `68.6 °C` mean temp; **Run155/156 cleared `< 3.000 s` at `+720 s` across consecutive boots (`2.941 s`, `2.163 s`), fresh relaunch hit an all-time record `1.417 s`, and Run154 verified 2.10 h continuous stability** |

The mainline QEMU binary was rebuilt incrementally across this series.

Several opt-in runner flags that preserve legacy behavior when omitted were also added during this span (`--virtio-sound`, `--sound-streams`, `--vcpu-affinity`, `--kernel-observation boot-only`, `--guest-files`); see the [installed-boot README](../experiments/E05-installed-boot/README.md) for usage.

### 7.1 2026-11-08 Maintenance Audit

- `make installed-offline-tests`: 11 suites, 257 test methods, 100% passed with 0 skipped; launches no QEMU instances, browsers, guests, or external network connections. results
- Clean patch-series reconstruction passed across all 5 patched components (modified file counts: Reims 21, `metal2vulkan` 5, `qemu-reims` 6, `exact-clock` 9, `qemu-bdif-usb` 42); all 43 binary, configuration, library, and patch identity hashes matched without relinking the mainline binary. reconstruction, identity
- Workspace cleanup: removed only 4 historical `kernel-usb` session directories of verified provenance and 8 hardlink aliases; retained 90 `private/` hardlinks per disk image without reclaiming underlying image blocks; preserved 2 `kernel-usb` directories whose provenance was uncertain. cleanup record
- The 2026-11-01 8-worktree reconstruction check remains preserved as a historical baseline (`runs/maintenance-20261101/`). Full blank-host reconstruction remains untested.

These checks provide source-tree and artifact-identity verification rather than new runtime execution results.

### 7.2 2026-11-12 Fresh-Clone Rebuild of the Public Snapshot

A public snapshot exported from mother-repo commit 7523547 was cloned from its git bundle into a fresh checkout on this host: no `sources/`, `build/`, `runs/` or `private/`, and an empty Cargo cache. All 26 make targets run in order in that checkout passed, in about 22 minutes:

- `fetch-sources`: the three upstreams were fetched from GitHub at the revisions pinned in `sources.lock.json`, and the external reference USB patch matched its SHA-256.
- 10 build targets (`build-reims-arm64-vulkan`, `build-reims-vulkan`, `build-qemu-bdif-usb`, `build-reims-qemu-arm16k`, `build-reims-qemu-arm`, `build-qemu-clock-exact`, `build-armvirt`, `build-vmapple-headless`, `build-reims-qemu-x86`, `build-shared-cache-identity`) and `check-research-patches` (all 5 patch chains reconstructed exactly). The patched source trees are byte-identical to the worktrees this repository actually uses, apart from build outputs and symlinks that point into the checkout.
- 13 self-contained checks that use self-written guests and no Apple code (`smoke`, `reims-pci-realize`, `reims-arm16k-smoke`, `reims-arm-compare`, `reims-gpu-compute`, `bdif-usb-tests`, `platform-contracts`, `smp-mmu-tests`, `el0-context-tests`, `pauth-model`, `clock-functional`, `config-mailbox-tests`, `avp-rtc-tests`), and the snapshot's own 14 offline suites (276 methods; 2 skipped by design without `private/`; 41-byte checkout path).

Earlier attempts exposed three problems that appear only in a fresh checkout, all fixed: `check-research-patches.py` wrote its report before `runs/` existed; `build-reims-qemu-arm.sh` checked all 5 worktrees, two of which exist only after their own builds have run (7523547); and with a checkout path longer than 37 bytes, the storage-retention tests' UNIX socket paths exceeded the 107-byte limit (cc32608).

Limits: the same host, system packages and Rust toolchain were used, so a rebuild on a second, blank machine is still untested; the binaries are not bit-for-bit reproducible, because the build writes the checkout path into them. Raw logs: `runs/maintenance-20261112/v1-fresh-clone/`.

### 7.3 2026-11-12 Comparison with Upstream

- **Reims and metal2vulkan:** upstream development happens on the `development` branch (`c410a064b` and `43d230817` on 2026-11-11), 2,067 and 3,172 commits ahead of `master`. None of our 8 patches is needed any more: reims 0001 only pins the local build; the 16 KiB guest-page handling of 0002–0005 has been rewritten upstream in `crates/reims-vgpu-vulkan/src/core/memory/map.rs`; upstream's Linux `allocate_aliasable` does what 0006's shareable temporary backing did; and the source-over blending of metal2vulkan 0001 and reims 0007 is covered by upstream's in-shader blending, which computes any blend as Metal does. This conclusion comes from reading the code and documentation; this project's guest has not been run on the new upstream versions.
- **QEMU master (11.1.90):** 0013 has been fixed upstream. The vmapple capability list linked in descending order (0014, 0019), virtio-snd answering zero-count JACK_INFO/CHMAP_INFO with `NOT_SUPP` (0027) and its four queues fixed at 64 entries (0028), and the virtio-net control queue fixed at 64 entries (0032) are still present.

## 8. Remaining Open Work

- **System:** Independent cold-boot verification of Run151's newly restored disk baseline; remount verification of standalone `Apple_APFS_Recovery` (`disk0s3`) under a Recovery boot (clean shutdown unmount of the main `Apple_APFS` OS container `disk0s2` and `Apple_APFS_ISC` `disk0s1` is now proven by both on-disk checkpoints and `nx_mount`); root cause of historical Run112 native-window stall and Run90/93 `DeviceLost`; timeout-diagnostic `site1` path; larger third-party applications, additional network protocols, and multi-day continuous soak.
- **Platform:** Genuine Apple PAC/HVC cryptographic semantics, SVE/SME, real-OS SMP scheduling and scaling, DMA/IOMMU contracts, and `avp-sealed-registers`.
- **Graphics & interactivity:** Arbitrary third-party 3D engines and compute-shader Metal coverage, physical input-to-present latency, and sustained 60 FPS benchmarks.
- **Audio:** Necessary and sufficient trigger conditions for historical dropped PCM frames on older builds (`0050` Run156 achieved `0` zero-block underruns across both voices), microphone capture, physical speaker output, audio latency, and multi-hour continuous playback.
- **Performance & whole-machine integration (M5–M7):** 60 FPS steady state, input latency bounds, CPU/GPU/power sub-budgets; sleep/wake, CJK IME, and OS updates.
- **Baselines:** E04/E09/E10 performance baselines, E07 x86 macOS control (VT-x currently disabled in BIOS), and M0 full rebuild on a second, blank host (the same-host fresh-clone rebuild is in 7.2).

## 9. Evidence Storage

| Location | Contents | Git Policy |
|---|---|---|
| `docs/reports/` | Technical reports and sanitized JSON summaries (nonces recorded by hash and byte length only) | Tracked |
| `runs/` | Raw execution logs and per-scenario JSON outputs | Ignored |
| `private/` | Apple firmware, keys, TSS tickets, nonces, guest disk images, and credentials (files `0600`, directories `0700`) | Ignored; strictly never committed or distributed |
| `build/`, `cache/`, `sources/` | Compiled build outputs, download caches, and pinned third-party source trees | Ignored |
