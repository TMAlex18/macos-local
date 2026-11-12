> **Language:** **English** | [简体中文](STATUS.md)

# Current R&D Status (Generated from PLAN-STATE.json)

Updated: 2026-11-12T11:01:45.746335+00:00; Master Plan v3: [MASTER-PLAN.en.md](MASTER-PLAN.en.md); Results Summary: [RESULTS.en.md](RESULTS.en.md).

**Phase: qemu_lock_contention_run142. The latest completed run is Run142 (2026-11-12): post-login background load subsides after about 20 minutes; on a quiet guest, initialization of Safari's first GPU connection takes 3.249 s (first launch) and 2.588 s (second launch), with all 8 vCPUs still fully busy during the window. The most recent run with a new functional conclusion is Run128 (fixed WebGL2 readback). No VM is currently running. Long-term goals remain incomplete.**

### Current: Saturated Guest and QEMU Lock Contention (Run138–142)

Completed on 2026-11-11 to 11-12: using host-side thread sampling, guest-side log windows, and root-level blocking sampling, we determined the system state during initialization of Safari's first GPU connection. There are four findings. First, RAM size has no significant effect (Run140, 8 GiB, [report](reports/M3-RAM-SIZE-GPU-INIT-TIMING.en.md)). Second, all 8 vCPUs remain saturated by background work for about 20 minutes after login; waiting for it to subside before launching Safari (Run142) brings initialization to 3.249 s (first launch) and 2.588 s (second launch), both below the under-load range, though the first launch still exceeds 3 s because launching Safari itself saturates all 8 vCPUs ([report](reports/M3-QUIET-GUEST-GPU-INIT-TIMING.en.md)). Third, when the guest is busy, vCPU threads spend 14%–30% of their time waiting on QEMU's global lock and "stop-all-vCPUs" exclusive sections, the latter triggered by broadcast TLB invalidations from the guest. Fourth, the roughly 0.9 s spent in SandboxHelper is the overhead of spawning a new process. Run138 and 141 were interrupted by session termination, and Run139 produced no run. `make installed-offline-tests` now covers 16 suites and 301 tests (including the 7-gate public snapshot suite and read-only RSP unit tests), all passing. What remains unproven is unchanged: Safari first load within the original 3 s deadline, timeout diagnostic site1, general Metal, physical metrics, and filesystem-clean shutdown.

## Milestones

| Phase | Status | Remaining |
|---|---|---|
| M0 Experimental Baseline | Current-host baseline, full IPSW, and installation target established | Full rebuild on a second, blank host; reusable delivery and recovery workflow for the installed target |
| M1 ARM Platform | Self-authored firmware validated timer/GIC, 16 KiB MMU, 2/4/8-core isolation, full EL0 context and migration, 24 MHz clock, and PAC HVC model; stock macOS runs on 8 vCPUs | Real Apple PAC/HVC semantics and OS ABI; SVE/SME/FPMR and FP arithmetic exceptions; real OS multi-core scheduling and performance, DMA/IOMMU |
| M2 Boot Loop Closure | Full native restore (-53) and independent cold boot of installed system (-54) verified | Additional independent reproductions and upgrade/recovery; proof of filesystem-clean shutdown and platform hardware semantic boundaries |
| M3 Real System | Run132–142: under the default-off 30 s diagnostic, Safari first GPU connection initialization takes 3.4–3.8 s (first launch) and 2.8–3.4 s (second launch) under load, and 3.249 and 2.588 s on a quiet guest; independent of PAC algorithm and RAM size; all 8 vCPUs remain fully busy during the window; Run128 fixed WebGL2 readback passed; original 3 s deadline not passed | GPU initialization and Safari first-load stability within the original 3 s deadline; broader application, protocol, and long-term stability; proof of filesystem-clean shutdown |
| M4 Graphics Loop Closure | Run128: in actual macOS, two readbacks of a fixed WebGL2 shader match an independent oracle byte-for-byte, with six color blocks visible in the native window; general WebGL/Metal, diagnostic site1, and physical presentation performance remain unverified | First GPU initialization and validation across more configurations; broader Metal coverage; physical input/presentation latency, FPS, and Metal fidelity |
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
| E05 | Restore artifacts and metadata | Run128: fixed WebGL2 readback, HTTP and output-device inventory, and guest-initiated shutdown passed. Controller exit code 1 occurred only because final capture was unavailable after shutdown; filesystem-clean shutdown remains unproven | Causal controls for first GPU initialization wait vs. watchdog and RAM; root cause of lock-screen AppleVPKeyStore unlock failure and guest clean shutdown; broader application, protocol, and interactive performance baselines; wide compatibility and persistence |
| E06 | VMApple platform (GPU-less control + Reims mainline) | Timeout diagnostic (0031) passed on self-authored 4 KiB guest; in the real 16 KiB system (Run127–128), site0 hit while site1 did not hit; original 3 s deadline not passed | Real Apple ABI/key model; SVE/SME/FPMR and FP exceptions; real OS multi-core scheduling, nested IRQs, and DMA |
| E07 | Real 26 x86 control | Not started (VT-x disabled in BIOS) | BIOS prerequisite; actual system artifacts and guest |
| E08 | ARM target/control first boot | BGRA8 build reached native desktop on both NVIDIA and Intel; Run85 networking and warm-start Safari page, Run94–96 (4 GiB) and Run97 (8 GiB) HTTP pages verified | First Safari/GPU launch stability; additional independent reproductions, upgrade/recovery, and proof of filesystem-clean shutdown |
| E09 | Hotspot / lock / device cost | Run131–142: initialization is a cross-process serial chain (audio subsystem initialization, within which SandboxHelper process startup takes about 0.9 s). It always executes on a saturated guest: background work for about 20 minutes after login, and Safari's own launch thereafter. When the guest is busy, vCPU threads spend 14%–30% of their time waiting on QEMU's global lock and exclusive sections, not yet broken down by trigger cause or patched. Diagnostic hit counts cannot be attributed to specific processes, and site1 is uncovered; original 3 s deadline not passed | Representative macOS workloads and causal breakdown; native/GUI input performance baselines |
| E10 | Native input / presentation baseline | Owned-window key press→HID interrupt→new frame path passed; physical input and latency untested | Native pointer/IME; physical input and latency |
| E11 | Cross-ISA graphics protocol | Run128: in actual macOS, FBO and default framebuffer readbacks (24,576 bytes each) of a fixed WebGL2 shader passed byte-for-byte, with six color blocks visible in the native window; original 3 s deadline, general Metal, and physical metrics remain unverified | Safari/GPUProcess and validation across more configurations; arbitrary applications and complex Metal state; physical input/latency and performance |

Total test samples: 270; see [TEST-HISTORY.en.md](TEST-HISTORY.en.md) for per-sample results; latest sample: Offline suites in the fresh clone of the v1 public snapshot (2026-11-12 UTC), 276 / 0.

## Next Steps

1. QEMU side: measure what triggers the global lock and exclusive sections when the guest is busy (counting by system register, execution-loop exit reason, and TLB invalidation instruction), then patch the top contributors—for example, avoiding stopping all vCPUs on broadcast TLB invalidations and keeping common exit reasons off the global lock. Evaluate each patch with before/after comparisons of Safari two-launch initialization times and blocking sampling.
2. Bring the blocking sampler and summary scripts (currently in `runs/quiet-guest-gpu-init-20261112/driver/`) into the repository with tests.
3. Re-run a full native restore with the current binary (last performed in run-53 on 2026-10-30) to confirm that key conclusions have not been broken by subsequent patches and to obtain a clean disk image. This step requires root.
4. Host stability: record temperatures on every run; BIOS, microcode, and power mode remain user decisions.
5. Trim PLAN-STATE.json: move historical run entries to an archive, keeping only the fields needed to generate STATUS and the current state.

## Architecture and Completion Boundary

Mainline: `sources/qemu-bdif-usb` + `sources/reims-arm-guest`; binary: `build/qemu-bdif-usb/qemu-system-aarch64`.

ARM guest APV 16 KiB pages are decoupled from Linux host 4 KiB VM pages; legacy x86 4 KiB Reims and GPU-less Portable VMApple are retained as controls.

Proven: native restore, normal-OS cold boot, account and desktop, file persistence across reboots, guest-initiated shutdown, Safari page rendering (Run89, 94–97), sound device binding, and fixed WebGL2 readback (Run128). Still missing: Safari first load within the original 3 s deadline, causal relationship of RAM capacity on first load, proof of filesystem-clean shutdown, broad application and Metal coverage, physical latency, and performance SLOs. Run90/93 Vulkan DeviceLost remains tracked as an independent runtime issue.
