> **Language:** **English** | [简体中文](MASTER-PLAN.md)

# Local macOS-Compatible Execution Platform: Full Product & R&D Master Plan v3

**Alignment Date: 2026-10-14 (UTC) | Host 1: Existing Linux Laptop | Subsequent Host: Desktop PC (to be incorporated after empirical validation).**

**Unchanged Goal: Run a genuine contemporary macOS kernel, userspace, desktop, and applications locally; over the long term, approach a native operating system in interactivity, throughput, device experience, and resource efficiency. No delivery deadline; no promised probability of success.**

This document is the master plan entry point, superseding v2 as the baseline for all subsequent planning. Current completion status, test samples, and blockers are recorded in a single source of truth, `PLAN-STATE.json`, with `STATUS.en.md` as its human-readable view; verified outcomes and evidence are consolidated in `RESULTS.en.md`; `reports/` holds dated per-topic experiment reports (including historical snapshots `reports/M0-RESULTS.en.md` and `reports/M1-ARM-REIMS.en.md`), rather than a competing master plan. The full v2 text and evidence register remain preserved in the local historical archive; historical files are not rewritten in this revision.

## 0. Alignment Conclusions at v3 Drafting (Historical)

The items below preserve the decision context at the time v3 was drafted; see [STATUS.en.md](STATUS.en.md) for current completion status.

1. **Product goals and milestones M0–M8 are neither scaled back nor renumbered.** At drafting time, M0 established the experimental baseline on the existing host without yet verifying blank-host reconstruction in a fresh second environment; M1 was in progress, with minimal single-core timer/GIC and 16 KiB MMU paths passing while gaps remained in Apple HVC/PAuth and multi-core address spaces. Those initial graphics experiments must not be represented as passing M2 or M4.
2. **The primary integration line formally adopts the ARM64/TCG port worktree of modern Reims QEMU.** The earlier Portable QEMU and headless VMApple trees serve as reference controls and fault-isolation tools; repairing the legacy Reims Cargo ABI is no longer a prerequisite for continuing.
3. **E11 is a newly added cross-ISA graphics contract experiment and does not replace E01–E10.** ARM64 guest 16 KiB APV pages and Linux host 4 KiB VM pages must remain distinct; addresses, page numbers, lengths, and alignments across layers must not be globally substituted. Continuing under v3, host-pointer alignment fixes and guest device-query writeback were completed; see the status table for concrete results.
4. **Build, device realization, register R/W, FIFO creation, command execution, actual rendering, and system GUI are accepted layer by layer.** Partial success across the first four to five layers cannot stand in for untested higher layers.
5. **Observed single-threaded and parallel test failures are recorded separately.** Shared-logger interference is neither counted as an equal number of product defects, nor are all failures dismissed as test-harness issues. Current results are governed by actual logs and exact source revisions.
6. **Backfill already-implemented patches, build steps, and experiment entry points that had not yet been persisted.** Reproducible modifications belong in the integration repository; build outputs, OS images, and run dumps stay out of Git.

## 1. Product Definition and Scope

The deliverable is neither a desktop theme, a remote Mac client, a Darling-style partial application compatibility layer, nor an older OS reskinned with a newer UI. Every run must identify and record the genuine guest kernel, userspace, dyld shared cache, OS build, and virtual platform in use. Transparent compatibility patches are permitted, provided modified behavior is never passed off as unmodified stock behavior.

Two operating modes share the same core: a research-workstation mode running inside the existing Ubuntu desktop for convenient debugging, and a dedicated-session mode booting directly into an isolated macOS session once the core matures, with Linux hosting hardware drivers and processes underneath. **Bare-metal execution is not treated as a prerequisite for a native-grade experience, nor do we build a custom Linux distribution first.**

Five mutually non-substitutable acceptance dimensions apply: interactive responsiveness, real-workload performance, semantic correctness / feature coverage, devices and lifecycle, and resource efficiency. They may be met incrementally, but smooth host-cursor motion, repeated frames, an idle desktop, skipped work, or undisclosed reduced precision must never be substituted for genuine attainment.

The exact desktop configuration is not yet finalized; other connected machines are not automatically treated as Host 2. Windows and Linux serve as user-experience benchmarks, which does not imply supporting Windows as a host; Linux is currently the sole primary host.

## 2. Facts, Target Versions, and Evidence Standards

Host 1's established hardware inventory is an Intel Core Ultra 7 255HX (20 cores/threads), ~30 GiB RAM, Intel ARL integrated graphics, and an NVIDIA RTX 5070 Ti Laptop GPU (~12 GiB VRAM), running Ubuntu 26.04.1 on Linux kernel 7.0.0-38 with a 2560×1600, 240 Hz internal panel wired to the Intel iGPU. Mutable host attributes—software packages, driver versions, free disk space—are governed by the most recent inspection timestamp rather than frozen as permanent facts from stale snapshots.

Initial planning adopts the "macOS 27 ARM" release line as the target version track, plus one required ARM reference control and an optional macOS 26 x86 control. The OS name/version table in v2 was a **2026-10-13 historical external survey**, not OS artifacts already acquired by this project. Apple's software catalogs and in-IPSW `BuildManifest` / `Restore.plist` metadata have confirmed `VirtualMac2,1` 27.0.1 (`26A434`) as the exact research candidate. Acquisition of stock materials, signed restore, and live execution evidence are maintained centrally in [RESULTS.en.md](RESULTS.en.md); current status of the complete boot bundle, AUX storage, machine identity / hardware model, and installed disks is governed by [STATUS.en.md](STATUS.en.md). Acquiring components, entering firmware Recovery, and completing full OS restore are accepted separately.

Evidence falls strictly into four classes: `O` = directly executed or read locally, `D` = static fact from pinned source code or documentation, `R` = third-party report not yet reproduced locally, `H` = hypothesis or design target. Multiple projects sharing the same fork do not automatically constitute independent validation. Raw logs, source files, exact builds, and exact experiment commands substantiate specific conclusions—never project names or reputation.

### Locally Established Minimal Baseline

- Self-authored ARM64 TCG serial payload; VMApple configuration reads; PSCI `CPU_ON` and secondary-vCPU RAM notification.
- Standalone Vulkan compute and 64×64 offscreen triangle render/readback on both Intel and NVIDIA GPUs.
- Modern Reims C/Vulkan libraries, distribution artifacts, and x86 QEMU PCI device realization.
- In-process initialization of modern VMApple + TCG + Reims MMIO, ARM guest register access, and 16 KiB Root/FIFO differential tests; v3 added closed-loop guest device-query command writeback and negative controls.
- One specific 4096-byte host-visible DMA-BUF scheme failed in both directions; this must not be overgeneralized into claiming that all cross-GPU paths are infeasible.

Subsequent platform, boot-chain, and graphics outcomes are consolidated in `RESULTS.en.md`, with detailed current status in `STATUS.en.md`.

## 3. Product Standards: Full v2 Targets Retained

All numerical figures below are planning thresholds, not host performance predictions. Measurements must pin application version, content, resolution, scaling, refresh policy, power profile, cold/warm cache state, background load, and timing boundaries. Low-resolution staging experiments must never be passed off as native-resolution results.

| Dimension | Baseline Product Target | Long-Term Stretch & Measurement Boundary |
|---|---|---|
| Display | Includes native-pixel 2560×1600 scenarios; 60 FPS first | 10-minute steady-state missed-frame rate <1%; then investigate 120/240 FPS; count genuinely new guest content only |
| Input-to-present | p95 ≤ 35 ms, p99 ≤ 60 ms at 60 FPS | High-refresh p95 ≤ 20 ms, p99 ≤ 35 ms; measured from host input receipt to actual presentation of the corresponding frame, never misrepresented as physical input-to-photon |
| Tail stalls | Zero unexplained >100 ms pauses in warm state | Attribute cold compilation, I/O, saturation, and synchronization separately; drive down events exceeding two target frame intervals |
| App launch/exit | Report absolute latency plus cold/warm distributions per action | Compare identical versions and content; account separately for cross-ISA, compiler, hardware, and API differences |
| CPU throughput | Start from same-host portable workloads and bare-metal micro-payload baselines | Aim for ≥80% of native x86 on representative workloads; no blanket guarantee across all closed-source macOS apps |
| GPU | Correct render/compute with explicitly bounded numerical error | Aim for ≤20% overhead on representative scenes; a passing Vulkan control does not imply automatic semantic equivalence with Metal |
| Reliability | 100 boots and a 24-hour mixed workload with zero data corruption | 100 sleep/wake/reconnect cycles on supported configurations; finite sample counts are not proof of ultra-low failure rates |
| Power | Measure delta at matched brightness, refresh rate, and activity level | Aim for ≤20% whole-machine power overhead; without hardware sensors, report observable proxies only and never fabricate wattages |

Frame budgets at 60/120/240 FPS are approximately 16.67 / 8.33 / 4.17 ms. A full-resolution 120 Hz display mode has not previously been observed on this laptop; 120 FPS can initially be targeted as a 1-in-2 frame pacing cadence on the 240 Hz panel.

Native baselines prioritize Linux implementations on the exact same machine; an Apple Silicon reference machine serves behavioral comparison only—timings from different hardware are never treated as zero-overhead baselines for this host. Relaxing any metric requires documenting the reason; failed samples must never be deleted, nor may the definition of "pass" be silently altered.

## 4. Architecture and Primary Implementation Line

```text
Physical Linux laptop / subsequent desktop
  ├─ Linux drivers: GPU, DRM/KMS, display, audio, networking, input, power, storage
  ├─ Modern Reims QEMU (pinned commit + project TCG/VMApple patch series)
  │    ├─ ARM64 CPU execution: TCG baseline → profile-driven optimization / optional tiered compilation
  │    ├─ VMApple: exceptions, MMU, HVC, timer, GIC, PSCI, virtual devices
  │    ├─ Reims apple-gfx-mmio + IOSurface + guest ABI
  │    └─ Genuine macOS kernel / userspace / WindowServer / apps (running; see STATUS.en.md for verified scope)
  ├─ Linux x86_64 Reims: explicit ARM64 guest page contract → metal2vulkan → Vulkan
  ├─ Display: existing native window → dedicated Wayland session → direct scanout where eligible
  └─ Thin management layer, build/image/cache tooling, diagnostics, and recovery
```

### Active Worktree Responsibilities

| Role | Source / Build | Non-Confusable Boundary |
|---|---|---|
| Platform controls | `sources/qemu-portable`, `sources/qemu-headless` | Retain verified UART/PSCI baselines; no longer required to carry a modern GPU ABI rewrite up front |
| Early platform / graphics control | `sources/qemu-reims-arm` | Linux/TCG port of modern QEMU; HVF hooks conditionalized; adds a default-off HVC software reference model (not equivalent to Apple hardware cryptography or target ABI) |
| Active restore / installed-OS mainline | `sources/qemu-bdif-usb`, `build/qemu-bdif-usb/qemu-system-aarch64` | Same modern QEMU baseline with patch series recorded through 0032; built via `build-qemu-bdif-usb`, serving stock restore and normal OS/application consumers. See [STATUS.en.md](STATUS.en.md) for active build/profile and verified boundaries |
| x86 graphics control | `sources/reims-build`, `build/reims-target` | Retains 4 KiB guest ABI; must not be overwritten by ARM fixes |
| ARM graphics mainline | `sources/reims-arm-guest`, `build/reims-arm-target` | Host x86_64 + ARM guest 16 KiB ABI; layer-by-layer guest/host page contracts |
| Minimal negative control | `build/qemu-reims-arm` | Deliberately pairs ARM firmware with the original 4 KiB ABI to reproduce mapping failure |
| Self-authored ARM graphics test binary | `build/qemu-reims-arm16k/qemu-system-aarch64` | Supports self-authored MMIO/FIFO/device-info tests; not a validated macOS platform |

Compile-time guest ABI selection with separate build trees is retained initially. Multi-guest runtime dispatch, ABI self-reporting, and init-time mismatch rejection belong to separate future interface work rather than an upfront repository-wide refactor into a generic platform.

Diagnostic, compatibility, and performance profiles share identical semantics and test suites; per-instruction tracing, `icount`, or single-threaded diagnostic timings are never reported as product performance. Hardware enablement stays in Linux while the guest sees versioned virtual devices; emulating an entire Apple SoC, SEP, or NPU is not assumed to be mandatory.

## 5. CPU and Platform R&D

### 5.1 Parallel Correctness and Performance Baselines

TCG is already a dynamic binary translator, so "add a JIT" is not a meaningful task definition. First decompose execution cost across translation/codegen, generated code execution, helpers, register spills, software address translation, exceptions/HVC, device accesses, lock/scheduler contention, and idle polling.

Minimal self-authored payloads sequentially cover: ARM64 ABI / general registers / FP / SIMD; exception levels and exception returns; guest MMU / page permissions / TLB / ASID; timer IRQ / GIC / masking and secondary cores; LL/SC and LSE atomics plus cross-core forward progress; instruction-cache invalidation after self-modifying code; and PAuth instruction results alongside Apple key/context lifecycle. **Existing UART and `CPU_ON` successes do not substitute for these contract tests.**

### 5.2 Optimization Roadmap

Start from measured profiles to improve hot instruction selection, register/flag liveness, helpers, SIMD lowering, TLB/memory fast paths, notifications, and idle waiting. Every fast path requires explicit preconditions, fallback paths, and precise exception recovery; guest privilege or address-space semantics must never be stripped to inflate benchmark scores.

Invest in fast first-pass translation plus hot-region optimizing compilation only when justified by profiling evidence. Preserve guest-PC-to-host-PC mappings, precise exception state, cross-page/context guards, SMC invalidation, and multi-core code publication; LLVM and Dynarmic are candidate backends or reference points, not drop-in replacements for a complete Apple platform.

Any persistent translated-code cache must key on code bytes, OS/image identity, translator version, CPU feature flags, and platform semantics; relocation, ASLR, and invalidation require explicit handling, never caching live PAC keys, raw host pointers, or stale permissions.

Recompiling owned or source-available components and selective host execution may be investigated, provided ABI, TLS, callbacks, pointer widths/tags, and cross-boundary marshaling costs are accounted for. Native x86 kernel + ARM userspace hybrids or non-Apple ARM hosts remain high-cost future experiments that do not replace the current laptop target; XNU's open-source kernel release does not imply that closed-source macOS userspace can be recompiled.

## 6. Graphics, Address Spaces, and Memory Protocols

### 6.1 Layered Completion Criteria

A. Library and QEMU build; B. Device realization; C. Guest CPU MMIO R/W; D. Root mapping / FIFO lifecycle; E. Guest command submission, host consumption, and memory writeback; F. Actual host GPU render/compute with verified readback; G. WindowServer and real applications; H. Long-haul stability, synchronization, error paths, and performance.

**Host Vulkan tests in E02 and each guest-protocol, GPU-execution, and presentation layer in E11 are accepted independently; see [STATUS.en.md](STATUS.en.md) for current evidence.** Experiments testing only command completion or counters without draw/compute work must not claim guest GPU rendering; self-authored guest presentation likewise does not substitute for WindowServer and real macOS applications.

### 6.2 Page and Address Contracts

Maintain strict separation among guest physical address, guest virtual address, guest APV page number, host virtual pointer, host `mmap`/page granule, and GPU device address. ARM guest APV page size is 16 KiB; Linux host VM page size remains 4 KiB and needs no kernel change. Audit Root/FIFO rings, display pages, IOSurface page tables / descriptors, task memory, isolated-task memory ranges, and mapping lengths layer by layer.

Whether a host pointer requires 16 KiB alignment must be determined from the host mapping and platform contract—neither globally mandated merely because guest pages are 16 KiB, nor blindly relaxed just to silence test failures. Construct positive and negative controls for the same guest physical address and verify actual memory mappings rather than register readbacks alone.

Guest RAM uses the established shared-`memfd` backing path; `hugetlb`, `MAP_FIXED`, whole-process `mlock`, and blanket hugepages are not universal speedup switches. Any page-alignment fix must prove it preserves host API alignment invariants.

### 6.3 Data Flow, Synchronization, and Accuracy

The closed loop must enforce: guest write → host visibility → GPU execution → result visibility → guest read / IRQ. Audit buffer/texture lifetimes, aliasing, cross-queue dependencies, completion callbacks, reuse-after-free boundaries, and concurrency. Prevent deadlocks from mutual waiting across vCPU threads, the main loop, and GPU completion, and forbid unbounded frame queuing; stale display states may be coalesced, whereas compute, synchronization, and resource-release commands must never be dropped.

AIR→SPIR-V shader translation and Metal resource/object/command semantics are two distinct layers. Reuse strict vs. fast paths in Reims and `metal2vulkan`, documenting affected APIs, trigger conditions, numerical error bounds, and application impact; display-only approximations may be opted into explicitly, whereas compute, storage, and synchronization must never fake completion.

### 6.4 Dual-GPU Topologies and Caching

Maintain Intel-render + Intel-internal-panel, NVIDIA-render + Intel-internal-panel, and future single-GPU direct-connected controls side by side. The specific host-visible DMA-BUF CPU-mapping failure in E03 refutes only that single mechanism; continue evaluating device-local buffers, DRM format modifiers, GPU-to-GPU copy engines, external semaphores, explicit sync, and presentation overhead. Eliminating CPU readback does not imply zero physical memory traffic, and nominal "zero-copy" is not an end in itself.

Cache AIR translation, shader compilation, and Vulkan pipelines at their respective layers. Cache keys must cover shader/pipeline content, semantic flags, and device/driver compatibility—never claiming eternal cross-driver validity. Measure cold boot, first-seen shader compilation, and warm steady state separately, without dropping frames to hide compilation stalls.

## 7. Display, Input, and Responsiveness

Instrument the end-to-end chain from host input arrival through guest event dispatch/layout, graphics command submission, GPU execution, and host presentation to an actual updated frame. Latency analysis follows the critical dependency path while throughput analysis targets bottlenecks—never summing elapsed times across parallel threads.

Reuse the native Reims window / Vulkan swapchain path first, avoiding default per-frame CPU readback over VNC. Subsequently reduce copies/compositing hops, bound queue depth and backpressure explicitly, and evaluate dedicated Wayland sessions, explicit synchronization, and direct scanout, while permitting measurable fallbacks whenever pixel format, scaling, or monitor constraints prevent scanout.

Keyboard verification covers `Cmd`/`Ctrl` mapping, layouts, focus loss, key-release delivery, absolute vs. relative pointer modes, scrolling, and gesture phases. CJK input methods, candidate windows, dead keys / composed characters, clipboard sharing, and the host-IME vs. guest-IME boundary must be explicitly defined. Unify timestamps onto a common clock across layers with documented conversion error bounds; QMP command round-trip time must never be misrepresented as input-to-photon latency.

## 8. Boot Chain and Version Management

E05 stands formally independent of the graphics track: audit restore materials, hardware model, machine identifier, auxiliary storage, firmware, disk images, and the restore/personalization flow. A complete boot bundle must record provenance, OS build, architecture, and mutual consistency—never splicing arbitrary AUX and root images together or slicing binaries at hardcoded offsets copied from outdated tutorials.

Where an Apple Silicon reference environment is available, it may be used to establish a known-good behavioral control; otherwise investigate everything achievable on Linux and enumerate exact external prerequisites. Reference hardware output is not counted as a local result, and opaque prebuilt third-party disk images do not qualify as reproducible initialization. Initialization blockers do not halt independent CPU or GPU experiments; see [STATUS.en.md](STATUS.en.md) for current native-restore and cold-boot evidence plus remaining M2 boundaries.

Start with one target ARM build, one required ARM reference control, and an optional macOS 26 x86 control. Test OS upgrades in cloned bundles without mutating known-good baselines. Record guest on-disk modifications, runtime code patches, and device-tree / capability overrides separately, documenting each patch's applicable builds, mechanism, behavioral delta, and rollback procedure. Prefer fixing the virtual platform over patching the guest, without turning "zero guest patches" into rigid dogma.

## 9. Storage, Audio, Networking, Sleep, and System Integration

Storage validation covers flush semantics, write ordering, durability across clean shutdowns and abrupt host kills, and subsequent reboots; `raw`/`pflash` backing follows what the active device path genuinely supports rather than forcing everything into `qcow2`. Offline snapshots encompass AUX storage, machine identity, and required persistent state. Shared-folder designs must explicitly specify case sensitivity, Unicode normalization, permissions, extended attributes (`xattr`), file locks, and atomic `rename`.

Sleep/wake requires coordinating vCPUs, I/O queues, GPU resources, host suspend, timekeeping, and device re-initialization; pausing the QEMU process or saving guest RAM is not equivalent to restoring live GPU state. Clean shutdown/reboot is acceptable during research phases, provided it is not credited toward final sleep/wake acceptance. Full VM checkpointing and live migration are tracked as separate capabilities.

Audio testing covers sustained playback/capture, device switching, underrun/blocking behavior, clock drift, and A/V sync. Networking bridges a virtual NIC to Linux and verifies DNS, host network transitions, LAN reachability, port forwarding/binding, and post-wake recovery, without automatically claiming Apple Continuity or cloud-account services. VideoToolbox / media hardware acceleration is decoupled from Metal: establish correct software decoding/encoding first before building bridges where justified by real demand.

Once the core matures, product packaging adds host capability preflight checks, material import/provisioning, start/stop orchestration, structured failure reporting, rollback, and dedicated-session launch—reusing QMP and Reims tooling rather than building a general-purpose VM manager up front.

## 10. Testing, Evidence, and Current Quality Standards

The verification hierarchy remains: host smoke → CPU/platform → virtual device → OS boot → graphics → applications → whole-system integration. Reuse upstream QEMU/TCG/`qtest` and Reims test suites; run targeted checks around active edits, cross-module integration tests across boundaries, and the full test matrix for release candidates.

Every experimental run records at minimum: source commits / patch stack, host / profile configuration, guest bundle or self-authored payload, exact command line, inputs, exit status, pass/fail verdict / log paths, and observation boundaries. Process exit `0`, harness pass, guest assertion pass, and expected failure on a negative control are distinct fields; a test wrapper that unconditionally exits `0` must never be treated as a passing test of the underlying subject.

Because several Reims Core unit tests share process-wide log capture, **deterministic regression runs execute on a single test thread** (`--test-threads=1`); parallel test execution is used separately to surface concurrency races, with log-capture crosstalk analyzed independently. Failing tests must never simply be deleted or marked `#[ignore]`. Full serial runs, targeted fix runs, and live QEMU runs each carry their own timestamps and are never stitched together into an unexecuted "all-green" claim.

QEMU and derived test harnesses are not automatically independent oracles. Without separate physical ARM hardware, prioritize test expectations derivable independently from architecture specifications, analytical math, or source inspection. Enable verbose tracing only where required for an active diagnosis, keeping trace-heavy runs strictly separate from performance measurements. Unprivileged user execution, local UNIX sockets, and explicit sharing boundaries form the initial operational perimeter; TCG is never represented as a hardened security sandbox.

## 11. Repository Layout, Reproducibility, and Change Management

Integration repository `/workspace/macos-local`: `sources/` holds pinned third-party checkouts/worktrees, `patches/` stores reconstructible patch series, `tools/` provides build and runner entry points, `experiments/` contains per-experiment harnesses, self-authored firmware, and offline unit tests, `docs/` houses plans, status, and consolidated results, `docs/reports/` stores dated technical reports, `private/` holds restricted OS/identity materials, and `build/`, `runs/`, and `cache/` are Git-ignored.

Pinned source commits alone do not guarantee reproducible binaries. At minimum, every subsystem must provide clean worktree patch application, deterministic build scripts, and matching experiment entry points; blank-host reconstruction, dependency installation manifests, and missing-material inventories remain open M0 deliverables. Fixes must be exported into `patches/` rather than stranded inside ad-hoc worktrees.

QEMU work is written primarily in C, Reims in Rust, and orchestration harnesses in Python and POSIX shell. Three parallelizable tracks—CPU/platform, graphics/display, and lifecycle/integration—run concurrently only when concurrency yields genuine wall-clock speedup. Shared foundational changes converge through a single integration owner to avoid conflicting edits to core abstractions. Upstream-suitable bug fixes are structured cleanly for eventual submission, without pushing publicly or filing upstream issues on the user's behalf unprompted.

## 12. Full M0–M8 Milestones and Current Standing

Milestone definitions and acceptance criteria preserve the 2026-10-15 baseline plan. The most recently completed run is Run142 (2026-11-12): under the default-off 30-second diagnostic, Safari's first GPU-connection initialization time is invariant to PAC algorithm and guest RAM size (3.4–3.8 s on the first launch under load); for roughly 20 minutes after login, all 8 vCPUs remain saturated by background activity, and on a quiet guest initialization takes 3.249 s while still saturating the initialization window; when the guest is busy, vCPU threads spend 14%–30% of their time waiting on QEMU's global lock and exclusive sections. The most recent run yielding a new functional conclusion is Run128 (fixed WebGL2 readback). Passing under the original 3-second deadline, hitting diagnostic `site1`, general Metal compatibility, physical latency/frame-rate metrics, and clean filesystem unmounting at shutdown all remain unproven. Live experiments resumed on 2026-11-10. See [STATUS.en.md](STATUS.en.md) for the active work queue; this section does not alter the M0–M8 targets.

| Milestone | Required Deliverables | Current Standing & Non-Substitutable Conditions |
|---|---|---|
| M0 Experimental Baseline | Pinned dependencies, reconstructible build steps, host execution, material inventory | Baseline established on Host 1; a fresh clone rebuilds from scratch on the same host (RESULTS 7.2), but a rebuild on a second, blank host is untested; material gaps still being inventoried |
| M1 ARM Platform | UP/SMP, timer/GIC, MMU/exceptions, atomics, PAuth/HVC platform contracts | Partial platform contracts pass and unmodified macOS runs across 8 vCPUs; genuine Apple PAC/HVC cryptography and broad SMP correctness/performance remain unverified |
| M2 Boot Closed Loop | Matched boot bundle; reproducible execution from firmware into early XNU | Full native restore and cold boot of the installed OS verified |
| M3 Real OS | Genuine target kernel/userspace, shell, processes, networking, persistent files | Login, desktop, networking, persistent files, apps, and guest-initiated shutdown empirically observed; Run128 completed first GPU initialization and fixed WebGL2 readbacks under the 30-second diagnostic; ordinary 3-second first load, long-haul stability, and clean filesystem shutdown remain unproven |
| M4 Graphics Closed Loop | Guest GPU resources/commands, WindowServer, input, real windows/text/files | Established GPU / IOSurface / native-window pixel path retained; both fixed WebGL2 readbacks in Run128 passed byte-for-byte; diagnostic `site1`, general WebGL/Metal coverage, and physical presentation performance remain unverified |
| M5 Baseline Product | 60 FPS scenarios, input/tail-latency bounds, apps & stability under explicit profiles | Functionally usable; 60 FPS, input/tail-latency thresholds, resource efficiency, and reliability targets not yet met |
| M6 Native-Grade Performance | CPU hotspots, memory, lock contention, presentation, and power approaching targets | Unmeasured; no premature JIT rewrite, though baseline profiling harnesses can be built now |
| M7 Whole-System Experience | Sleep/wake, audio, IME, hotplug, updates/rollback, and data integrity | Bit-exact PCM verified in Run108, 114, and 121 while other runs dropped frames; Run128 inventoried output devices without a reference PCM check; fixing the sound-queue ring layout does not prove audio fidelity, causality, or physical speaker quality |
| M8 Second Platform | User-confirmed desktop hardware: clean rebuild, execution, and regression pass | Hardware not yet selected; passing on two machines does not generalize to arbitrary PCs |

Milestone dependencies govern completion claims rather than imposing a rigid waterfall: E02/E04/E05/E06/E10 plus platform and graphics experiments may advance concurrently in interleaved order.


## 13. E01–E11 Experiment Queue: Original Numbering Plus Extended Scope

Work-package definitions remain unchanged; the current standing summaries below are governed by the detailed evidence and scope boundaries in [STATUS.en.md](STATUS.en.md).

| ID | Full Work Package | Verified Standing / Remaining Scope |
|---|---|---|
| E01 | Pin dual QEMU trees, modern Reims, and reconstructible integration | Build / PCI / MMIO partially verified; clean reconstruction and patch series actively maintained |
| E02 | Dual-GPU render/compute/readback and backend boundaries | Minimal smoke samples pass; neither a performance benchmark nor full driver certification |
| E03 | Bidirectional sharing, handles/formats/modifiers/sync, and fallback cost | One host-visible DMA-BUF scheme failed; device-local sharing and explicit sync remain untested |
| E04 | Self-authored ARM micro-payloads vs. same-host x86 execution cost | UART smoke passes; per-class CPU, memory, and throughput profiling not yet executed |
| E05 | Restore materials, VMApple metadata, and complete boot bundle | Current evidence for material acquisition, stock signed restore, and full AUX / identity / system-disk state is consolidated under the boot-chain and AUX sections of [RESULTS.en.md](RESULTS.en.md) |
| E06 | Headless VMApple platform; exception/timer/MMU micro-payloads | Exact 24 MHz clock, timer/MMU/exceptions, full 32-register SIMD, dual-CPU EL0 migration, and 2/4/8-vCPU isolation partially verified; unmodified 8-vCPU macOS OS/desktop running. Genuine Apple contracts and broad concurrency/performance remain open; see [STATUS.en.md](STATUS.en.md). |
| E07 | Genuine macOS 26 x86 Reims guest control | Virtual GPU device init alone does not count; VT-x is disabled in BIOS and guest media is absent—does not block ARM |
| E08 | ARM reference control and first boot of target OS | Firmware Recovery and live XNU execution are accepted separately; see [STATUS.en.md](STATUS.en.md) for current furthest execution point and remaining conditions |
| E09 | CPU hotspot, MMU/helper, and device/lock contention decomposition | Owned host profiling samples and self-authored standalone consumer/cost benchmarks for semantics-preserving PAC optimizations exist; not yet a full OS/device wait attribution or native-speed baseline—see [RESULTS.en.md](RESULTS.en.md). |
| E10 | Native-window input-to-present pipeline and physical output topology baseline | Self-authored window-input-to-guest-draw loop plus unmodified macOS keyboard/pointer input and native-window pixels verified; physical input-to-present latency and FPS baselines remain open. |
| E11 (Added) | Cross-ISA Reims device / page / command / IOSurface contracts | Genuine GPU compute/draw/IOSurface execution plus unmodified Setup Assistant, desktop, and Terminal rendering retained; Run128 fixed WebGL2 readback passed, without substituting for general Metal/API compatibility, precision, or performance certification. |

### Next Executable Actions (No Date Commitments)

The active work queue is governed by `next_actions` in `PLAN-STATE.json`, rendered in human-readable form under the "Next Actions" section of `STATUS.en.md`; a duplicate queue is no longer maintained here. Upon completing each item, update canonical state before selecting the next; individual conversation turns are not mechanically mapped to exhausting an entire multi-item queue.

## 14. Key Risks and Pivot Triggers

| Unknown | Discriminating Evidence Required | Sound Adaptation (Instead of Premature Abandonment) |
|---|---|---|
| Non-reproducible provisioning | Pinpoint exact restore / AUX / platform step and missing material | Use reference environment, isolate provisioning research, keep advancing sibling subsystems |
| Newer Apple ABI divergence | First divergence point between older and newer guests on identical platform | Introduce versioned machine profiles instead of ad-hoc hybrid images |
| Excessive CPU translation cost | Breakdown across instructions, MMU, helpers, locks, and device waits | Targeted fast paths, tiered DBT, and bounded host-native execution |
| GPU semantic mismatches | Minimal reproducer isolating resource, command, aliasing, sync, and readback | Strict translation mode, honest feature caps, localized software fallbacks |
| High cross-GPU transfer overhead | Handle / modifier / sync compatibility and measured stall times | Intel iGPU render path, async GPU copy engine, direct-connected display control |
| Page-granule contract confusion | Causal differential test feeding identical guest inputs into 4K vs. 16K ABIs | Explicit layered guest/host page types rather than global constant replacement |
| Lifecycle / suspend failure | Dependency map across host, guest, and live GPU state | Deterministic resource reconstruction and standalone checkpoints; never fake resume |
| Test-harness distortion | Single-test vs. serial vs. parallel runs cross-checked against source invariants | Fix test isolation rather than mistaking false greens for product correctness |
| Upstream churn / scope creep | Pinned-commit regression bisect and real application dependency audit | Small rebasable patch stacks and layered capability flags—neither freezing forever nor emulating an entire SoC |

## 15. Learning and Long-Term Collaboration

Completing a full OS-kernel curriculum up front is unnecessary. Build targeted depth around active experiments—ARM64 ABI/EL transitions, page-table permissions, atomics/interrupts, MMIO/DMA, TCG IR and register allocation, Vulkan resource synchronization, and storage durability—backed by small runnable examples for each topic. Exploring instructive side branches is welcome without requiring every step to advance boot progression, provided unverified workarounds remain explicitly labeled as unproven.

Experiment boldly, iterate after failures, and retain clean reference controls alongside reversible patches. Rigor governs evidence and scope claims—never degenerating into bureaucratic approval rituals, repetitive full-disk hashing, or redundant defensive checks.

## 16. Resources, Power, and Cross-Machine Maintenance

Initial main VMs allocate 8–12 GiB RAM and a modest vCPU count; synthetic contract tests run in 512 MiB and scale across 2/4/6/8 vCPUs incrementally rather than assigning all 20 host threads at once. Builds typically use 8–10 parallel jobs adjusted to host activity; P/E-core affinity pinning is retained only if backed by measured gains. Idle waiting vs. busy polling is treated as an explicit design variable—never burning continuous peak power just to mask desktop scheduling latency.

Disk artifact budget starts at 150–250 GiB, separating irreplaceable stock materials, known-good binaries, and reconstructible build caches / traces. `.gitignore` alone is not a storage-management policy, and user-supplied materials are never deleted without permission. Host 2 adaptation relies on capability profiles rather than DMI model-string quirks, empirically validating Intel vs. AMD CPUs, GPU vendors, and hybrid Optimus/MUX vs. direct display topologies individually.

## 17. Licensing, Distribution, and Identity Materials

Technical R&D remains strictly separated from media licensing and distribution, continuing the original project policy. Prior external surveys of software license terms do not substitute for legal review of current target media prior to any deployment or distribution. This repository stores only original code, patch series, and test harnesses—never packaging Apple OS images, firmware, AUX files, genuine machine identities, or account credentials, and never publishing artifacts publicly on the user's behalf.

## 18. Reproduction Entry Points and Evidence Index

See the repository-root `README.md` for the full command matrix classified by network access, `private/` material prerequisites, and root privileges. Common entry points:

```bash
cd /workspace/macos-local
make doctor
make smoke                       # Minimal established host + ARM platform smoke tests
make offline-tests               # Established offline test aggregate; parts require private/ materials or metadata
make platform-contracts          # Timer/GIC, MMU, combined contracts, and negative controls (no network)
make smp-mmu-tests               # Dual-vCPU MMU / ASID / TLBI / atomics suite
make reims-arm16k-smoke          # MMIO / page-mapping / FIFO 4K-vs-16K differential suite
make reims-fifo-roundtrip        # Closed-loop guest device-query command + writeback + negative controls
make reims-core-serial           # Standalone full Reims Core serial regression suite
make check-research-patches      # Verify byte-accurate worktree reconstruction from patch stacks
make boot-metadata               # Explicit small network query for catalog metadata only (never downloads full IPSW)
```

For reproducing cold boots of the installed OS, see the [corresponding README](../experiments/E05-installed-boot/README.md); commands listed here serve as an entry-point index rather than launching live VM runs during documentation maintenance. Newly added test targets are governed by `Makefile` and `PLAN-STATE.json`. Negative controls must explicitly declare their expected failure mode so process exit codes are never confused with compatibility passes.

Historical external reading references remain unchanged: QEMU system/TCG/ARM/testing documentation, Reims `docs/qemu.md` and accuracy ledger, `metal2vulkan`, Linux DRM, Vulkan synchronization and external memory specifications, and Apple virtualization/restore documentation. Dynamic web pages provide directional context only while execution pins exact commits; local logs and pinned source trees form our primary evidence, never treating stale external web pages as current local verification.

Original v2 master plan: `product-research-master-plan-v2-zh.md` (local historical archive); original v2 evidence register: `evidence-and-experiment-register-v2.json` (local historical archive). Active master plan: `docs/MASTER-PLAN.en.md` (Chinese: `docs/MASTER-PLAN.md`); single source of truth for project state: `docs/PLAN-STATE.json` (human-readable view: `docs/STATUS.en.md` / `docs/STATUS.md`); consolidated results and evidence: `docs/RESULTS.en.md` (`docs/RESULTS.md`); per-topic technical reports: `docs/reports/` (where `M0-RESULTS.en.md` and `M1-ARM-REIMS.en.md` are historical snapshots); pre-v3 alignment snapshots: `docs/archive/`. Subsequent updates must keep these document roles sharply separated without spawning competing "latest completion tables."

### Progress Log Note

Turn-by-turn progress summaries appended here between 2026-10-14 and 2026-10-15 (PAC/HVC, timer contracts, AVP MMIO, exact 24 MHz clock, BDIF USB, stock AVPBooter, kernel USB/IP, signed iBSS, iBSS handoff, Stage1 Recovery, Stage1 configuration and bulk-OUT, iBEC prerequisites, IM4M and certificate constraints, and dual-vCPU MMU) have been consolidated into `RESULTS.en.md`; the original per-turn write-ups remain in their respective `reports/` files and Git history. All subsequent results are recorded in `PLAN-STATE.json`, `reports/`, and `RESULTS.en.md`, and are no longer appended to this file.
