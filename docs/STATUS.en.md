> **Language:** **English** | [简体中文](STATUS.md)

# Current R&D Status (Generated from PLAN-STATE.json)

Updated: 2026-11-13T00:07:16.047075+00:00; Master Plan v3: [MASTER-PLAN.en.md](MASTER-PLAN.en.md); Results Summary: [RESULTS.en.md](RESULTS.en.md).

**Phase: tcg_orig3s_peak_and_quiet_validated_run150. The latest completed runs yielding new functional conclusions are Run149–150 (2026-11-12): with the 30 s timeout diagnostic completely off, the 0045 mainline build pushed quiet-guest cold first-launch Safari strictly below the nominal 3.000 s deadline to 2.734 s (Run149) and passed cold first launch directly at the +720 s post-login background-load window with 0 watchdog exits (Run150, 3.254 s; +1020 s second launch 1.662 s), achieving 5/5 bit-exact fixed WebGL2 dual-buffer readbacks across five cold boots. No VM is currently running. Long-term goals remain incomplete.**

### Current: Dual-Pass QEMU Sync & Throughput Optimization (0033–0045) Complete — Quiet-Guest Diagnostic-Off Cold First Launch Strictly Sub-3.000 s at 2.734 s (Run149) and +720 s Peak-Window Cold First Launch Passed with 0 Watchdog Exits (Run150, 3.254 s / Second 1.662 s)

Completed on 2026-11-12: patches 0033–0039 first cut 8-vCPU saturated BQL acquisition rate by 90% and raised running/runnable vCPU share to 96.6% (3.4% total stall; [sync report](reports/M3-QEMU-SYNC-SOURCES.en.md)); DWARF `perf` profiling then guided six pure-throughput patches 0040–0045 (`CF_PCREL` read-before-write jump-cache invalidation + 256 page buckets, `cputlb` 16 KiB subpage invalidation + unit-bug fix + `!EL2` inline fast paths, PAuth VA cache + SSSE3 `pshufb` QARMA5, SME `do_fmopa_h` pre-unpacking, host-RAM-tagged persistent `CPUJumpCache` across context switches, and selective `TTBR0_EL1` flushes preserving `Phys_NS` and `TTBR1` kernel upper-half TLBs; [throughput report](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md)). Saturated guest `[JIT]` P-core share jumped from 13.52% to 24.12% (+78.4%) and Safari-launch `tb_htable_lookup` fell 62.7%. With **`--webkit-timeout-diagnostic` completely off**: (1) on a quiet guest (Run149), cold first launch passed in **2.734 s** (0 watchdog exits, strictly below nominal `3.000 s` without needing UIProcess runloop slack); (2) at the `+720 s` post-login background-load window (`vcpu_mean_busy = 6.32`, Run150), cold first launch also passed with **0 watchdog exits** (3.254 s; `+1020 s` second launch **1.662 s**); across five independent diagnostic-off cold boots (Run146–150), fixed WebGL2 dual-buffer readbacks passed **5/5** `bit_exact=true`, with **8/8** consecutive zero-watchdog fresh launches across Run148–150. Still unproven: nominal `< 3.000 s` without runloop slack under peak load, general Metal, physical metrics, and filesystem-clean shutdown.

## Milestones

| Phase | Status | Remaining |
|---|---|---|
| M0 Experimental Baseline | Current-host baseline, full IPSW, and installation target established | Full rebuild on a second, blank host; reusable delivery and recovery workflow for the installed target |
| M1 ARM Platform | Self-authored firmware validated timer/GIC, 16 KiB MMU, 2/4/8-core isolation, full EL0 context and migration, 24 MHz clock, and PAC HVC model; stock macOS runs on 8 vCPUs | Real Apple PAC/HVC semantics and OS ABI; SVE/SME/FPMR and FP arithmetic exceptions; real OS multi-core scheduling and performance, DMA/IOMMU |
| M2 Boot Loop Closure | Full native restore (-53) and independent cold boot of installed system (-54) verified | Additional independent reproductions and upgrade/recovery; proof of filesystem-clean shutdown and platform hardware semantic boundaries |
| M3 Real System | Run149–150: without the 30 s timeout diagnostic, quiet-guest cold first launch strictly cleared 3.000 s at 2.734 s (0 watchdog exits), and +720 s peak-window cold first launch also passed with 0 watchdog exits (3.254 s; +1020 s second launch 1.662 s); 5/5 bit-exact WebGL2 readbacks across five cold boots and 8/8 zero-watchdog fresh launches across Run148–150 | Peak-load cold first launch and multi-run diagnostic-off repeatability; broader application, protocol, and long-term stability; proof of filesystem-clean shutdown |
| M4 Graphics Loop Closure | Run146–150: without the 30 s timeout diagnostic, across 5 independent cold boots (including quiet guests and the +720 s peak-load window), Safari rendered the fixed WebGL2 shader with both 24,576-byte readbacks matching the independent oracle byte-for-byte and six color blocks visible in the native window; general WebGL/Metal and physical presentation performance remain unverified | First GPU initialization and validation across more configurations; broader Metal coverage; physical input/presentation latency, FPS, and Metal fidelity |
| M5 Baseline Product | Representative features usable; performance and long-term stability untested | 60 FPS, input latency, and tail SLOs; long-duration stability and recovery/delivery workflow |
| M6 Native-Class Performance | Untested | CPU/GPU/presentation/power breakdown |
| M7 Full-System Experience | Run128 HTTP, output-device inventory, and guest-initiated shutdown passed (reference PCM was not run in that round); filesystem-clean shutdown and physical experience remain unverified | Sleep, input methods, audio, upgrade, recovery |
| M8 Second Platform | Hardware unconfirmed | Actual desktop profile; port and regression |

## Experiment Inventory

| ID | Scope | Status | Remaining |
|---|---|---|---|
| E01 | Pinned dependencies / build | Current-host build and patch reconstruction passed; on 2026-11-12 a fresh clone of the public snapshot fetched the upstreams from scratch on the same host, completed 10 build targets and the patch-chain reconstruction, and passed 13 self-checks and 14 offline suites (RESULTS 7.2) | Full rebuild on a second, blank host (fresh system packages and toolchain) |
| E02 | Dual-GPU Vulkan | Minimal compute and triangle readback passed | Broader semantic coverage; true performance benchmarks |
| E03 | Cross-GPU interop | One host-visible DMA-BUF scheme failed in both directions | device-local/format modifier; sync/transfer/present cost |
| E04 | ARM/x86 execution baseline | ARM UART smoke passed only | Same-host x86 control; instruction/memory/throughput profiling |
| E05 | Restore artifacts and metadata | Run149–150: diagnostic-off quiet-guest (2.734 s) and +720 s peak-window (3.254 s) cold Safari GPU initialization, 5/5 bit-exact fixed WebGL2 dual-buffer readbacks, and guest-initiated shutdown passed. Controller exit code 1 occurred only because final capture was unavailable after shutdown; filesystem-clean shutdown remains unproven | Causal controls for first GPU initialization wait vs. watchdog and RAM; root cause of lock-screen AppleVPKeyStore unlock failure and guest clean shutdown; broader application, protocol, and interactive performance baselines; wide compatibility and persistence |
| E06 | VMApple platform (GPU-less control + Reims mainline) | 0033–0045 mainline passed diagnostic-off Safari first GPU initialization on both quiet guests (2.734 s, < 3.000 s) and the +720 s peak window (3.254 s, 0 watchdog exits); default-off 0031 diagnostic retained for reference | Real Apple ABI/key model; SVE/SME/FPMR and FP exceptions; real OS multi-core scheduling, nested IRQs, and DMA |
| E07 | Real 26 x86 control | Not started (VT-x disabled in BIOS) | BIOS prerequisite; actual system artifacts and guest |
| E08 | ARM target/control first boot | BGRA8 build reached native desktop on both NVIDIA and Intel; Run85 networking and warm-start Safari page, Run94–96 (4 GiB) and Run97 (8 GiB) HTTP pages verified | First Safari/GPU launch stability; additional independent reproductions, upgrade/recovery, and proof of filesystem-clean shutdown |
| E09 | Hotspot / lock / device cost | Run143–150: 0033–0039 cut sync stalls to 3.4%; 0040–0045 raised saturated `[JIT]` host P-core share by 78.4% (13.52% → 24.12%) and cut `tb_htable_lookup` by 62.7%, achieving diagnostic-off cold 1st launch in 2.734 s (quiet) and 3.254 s (+720 s peak; 2nd 1.662 s) | Representative macOS workloads and causal breakdown; native/GUI input performance baselines |
| E10 | Native input / presentation baseline | Owned-window key press→HID interrupt→new frame path passed; physical input and latency untested | Native pointer/IME; physical input and latency |
| E11 | Cross-ISA graphics protocol | Run146–150: without the timeout diagnostic, across 5 independent cold boots both FBO and default framebuffer readbacks (24,576 bytes each) of a fixed WebGL2 shader in actual macOS Safari passed 5/5 byte-for-byte, with six color blocks visible in the native window; general Metal and physical metrics remain unverified | Safari/GPUProcess and validation across more configurations; arbitrary applications and complex Metal state; physical input/latency and performance |

Total test samples: 277; see [TEST-HISTORY.en.md](TEST-HISTORY.en.md) for per-sample results; latest sample: Post-Run150 maintenance offline regression, 57-item build/patch SHA-256 identity check, and 7-gate public snapshot check (2026-11-13 UTC), 310 / 0.

## Next Steps

1. Re-run a full native restore on the current `0045` mainline binary (last performed in run-53 on 2026-10-30; requires root) to verify that `0033–0045` preserve the native restore pipeline and to obtain a fresh disk baseline; if further pushing peak-window `3.254 s -> < 3.000 s` is desired later, explore a 2-way set-associative `CPUJumpCache` to eliminate the remaining 4.3% direct-mapped `qht_lookup_custom` misses.
2. Expand validation across broader stock macOS applications, more complex WebGL2/Metal workloads, and multi-hour interactive stability, alongside causal checks for intermittent dropped audio frames and clean APFS unmount.
3. Host stability: record temperatures on every run; BIOS, microcode, and power mode remain user decisions.

## Architecture and Completion Boundary

Mainline: `sources/qemu-bdif-usb` + `sources/reims-arm-guest`; binary: `build/qemu-bdif-usb/qemu-system-aarch64`.

ARM guest APV 16 KiB pages are decoupled from Linux host 4 KiB VM pages; legacy x86 4 KiB Reims and GPU-less Portable VMApple are retained as controls.

Proven: native restore, normal-OS cold boot, account and desktop, file persistence across reboots, guest-initiated shutdown, Safari page rendering (Run89, 94–97), sound device binding, and diagnostic-off cold first-launch Safari plus bit-exact fixed WebGL2 dual-buffer readbacks on both quiet guests (Run149, 2.734 s, strictly < 3.000 s) and the +720 s post-login background-load window (Run150, 3.254 s, 0 watchdog exits; 5/5 cold boots WebGL2 bit-exact, 8/8 consecutive launches 0 watchdog exits across Run148–150). Still missing: nominal < 3.000 s without runloop slack under peak load, proof of filesystem-clean shutdown, broad application and Metal coverage, physical latency, and performance SLOs. Run90/93 Vulkan DeviceLost remains tracked as an independent runtime issue.
