> **Language:** **English** | [简体中文](README.zh-CN.md)
>
> **Plan:** [`docs/MASTER-PLAN.en.md`](docs/MASTER-PLAN.en.md) ([中文](docs/MASTER-PLAN.md)) (v3) | **Status:** [`docs/STATUS.en.md`](docs/STATUS.en.md) ([中文](docs/STATUS.md)) | **Results:** [`docs/RESULTS.en.md`](docs/RESULTS.en.md) ([中文](docs/RESULTS.md)) | **License:** [`LICENSE`](LICENSE)

# macos-local — ARM macOS on a Linux/x86_64 laptop (research PoC)

> **Research Proof-of-Concept (PoC) — Source-Available, Non-Commercial:** This
> repository is an experimental systems-virtualization and Metal→Vulkan
> interoperability PoC, **not** an end-user product, turnkey installer, or
> security sandbox.
> - **Split licensing (source-available + upstream FOSS):** Original harnesses,
>   self-authored probes, tools, and documentation outside `patches/` are
>   source-available under [`PolyForm-Noncommercial-1.0.0`](LICENSE); no third
>   party is authorized to sell, commercially bundle, or commercially host those
>   PolyForm-licensed original materials. Upstream patches in `patches/` remain
>   strictly under their respective upstream `GPL-2.0-only` / `GPL-2.0-or-later`
>   / `LGPL-3.0-or-later` licenses with no additional restrictions.
> - **Zero Apple code or binaries (BYOF):** Public research snapshots contain
>   zero firmware, OS images, keys, tickets, or SDK files. Any user supplying
>   external OS media is solely responsible for complying with applicable
>   software license terms. Not affiliated with or endorsed by Apple Inc.

Personal research workspace: boot contemporary ARM macOS under QEMU TCG on a
Linux/x86_64 host, using a VMApple-derived platform and Reims (Metal→Vulkan)
graphics. No deadline; the long-term stretch goal is native-like experience.

Host 1: Linux/x86_64 laptop, Intel Core Ultra 7 255HX, NVIDIA RTX 5070 Ti Laptop +
Intel iGPU, Ubuntu 26.04.1. Host 2 (desktop) is not yet chosen.

## Where things stand

- **Reached:** original macOS 27.0.1 (26A434) is restored, cold-booted and
  usable under QEMU TCG, using Apple's own signed boot chain (AVPBooter → iBSS
  → iBEC → restore, with live TSS tickets):
  - run-53 completes a native restore; run-54 cold-boots the installed system;
  - run-63 finishes Setup Assistant, password login, the Finder/Dock desktop
    and Terminal on eight vCPUs;
  - run-64 shows a file surviving a cold boot and an Apple-menu shutdown that
    ends QEMU normally;
  - run-65 runs guest curl over HTTPS, Safari with local and remote pages, and
    TextEdit edit/save.
- **Since then (Runs 70–128):** native-window pixels on both the NVIDIA and
  Intel GPUs, automatic DHCP and HTTP, the VirtIO sound device as the
  CoreAudio default device with bit-exact PCM output in some runs, and a fixed
  WebGL2 shader whose raw readback matches an independent oracle byte for byte.
- **Latest runs with new results:** [Run149 and Run150](docs/reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md)
  omit the 30-second timeout diagnostic completely (`webkit_timeout_diagnostic`
  off) on the `0033–0045` synchronization- and throughput-optimized QEMU build:
  - On a quiet guest (**Run149**, `+976 s`), cold first-launch Safari initializes
    its first GPU connection in **`2.734 s` (`0` GPU watchdog exits — strictly
    below WebKit's unmodified `3.000 s` deadline without needing UIProcess
    runloop slack)** and passes both 96×64 RGBA8 WebGL2 readbacks (24,576 bytes
    each) byte for byte (`1.679 s` on the second launch).
  - Even without waiting for quiet (**Run150**, triggered directly at **`+720 s`**
    during post-login background saturation at `vcpu_mean_busy = 6.32`), cold
    first-launch Safari initializes with **`0` GPU watchdog exits** (`3.254 s`
    log span, within unmodified UIProcess runloop delivery) and passes both
    WebGL2 readbacks byte for byte (**5/5** across five diagnostic-off cold boots;
    **8/8** consecutive zero-watchdog fresh launches across Run148–150; `1.662 s`
    at `+1020 s`). Both runs shut down cleanly from inside macOS; filesystem
    cleanliness remains unproven.
- **Current work:** active again from 2026-11-10.
  Runs 130–142 localized Safari's GPU-initialization delay to guest saturation
  plus 14–30% vCPU stall time on QEMU's global lock (`bql`) and stop-the-world
  sections ([PAC algorithm](docs/reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.en.md),
  [guest RAM size](docs/reports/M3-RAM-SIZE-GPU-INIT-TIMING.en.md),
  [host CPU sampling](docs/reports/M3-GPU-INIT-HOST-CPU-USE.en.md),
  [quiet guest](docs/reports/M3-QUIET-GUEST-GPU-INIT-TIMING.en.md)).
  Runs 143–146 ([sync-sources report](docs/reports/M3-QEMU-SYNC-SOURCES.en.md))
  counted exact BQL/exclusive trigger sources (`0033`) and landed six patches
  (`0034–0039`), cutting saturated BQL acquisitions by 90% and raising vCPU
  running/runnable share to **96.6%** (3.4% blocked). Runs 147–150
  ([throughput report](docs/reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md))
  profiled the remaining 80–81% C-helper execution cycles (`perf`) and landed
  six pure-throughput patches (`0040–0045`: selective `CF_PCREL` jump-cache
  invalidation + 256 page buckets, 16 KiB subpage SoftMMU invalidation + `!EL2`
  inline fast paths, PAuth VA cache + SSSE3 `pshufb` QARMA5, SME `do_fmopa_h`
  pre-unpacking, host-RAM-tagged persistent `CPUJumpCache` across context
  switches, and selective `TTBR0_EL1` flushes preserving `Phys_NS` and `TTBR1`
  kernel TLBs), raising guest `[JIT]` P-core share by **78.4%** (`13.52% → 24.12%`),
  pushing quiet-guest diagnostic-off cold launch strictly below `3.000 s`
  (`2.734 s`), and passing `+720 s` peak-load diagnostic-off cold launch with
  `0` watchdog exits (`3.254 s`). Next is repeating a full native restore on the
  current `0045` mainline binary (requires root) and broader application/Metal
  validation.
  Mainline is `build/qemu-bdif-usb/qemu-system-aarch64`, SHA256
  `f858a94a8d7395456679a9c05c2c0dd2d6ee56f8701525e47bbf7ed9ee85f474`, with
  the patch chain through 0045; see the
  build manifest and [STATUS](docs/STATUS.en.md).
- **Open problems** (evidence and a per-run table are in
  [RESULTS section 2](docs/RESULTS.en.md)):
  - *Peak-load nominal `< 3.000 s` margin.* Quiet-guest diagnostic-off cold
    first launch strictly clears `< 3.000 s` (`2.734 s`, Run149), and `+720 s`
    peak-load diagnostic-off cold launch passes with `0` watchdog exits (`3.254 s`,
    Run150, 2 WebProcess notices), though its log span still relies on ~`0.26 s`
    of UIProcess runloop timer slack under heavy background load.
  - *Audio.* PCM output is complete in some runs and drops frames in others;
    the cause is unknown.
  - *Host side.* Vulkan `DeviceLost`, one i915 GPU hang and two unexpected
    host reboots occurred during runs.
- **Not yet:** proof of a filesystem-clean shutdown, broad app and Metal
  coverage, physical input latency, and every performance target (M5–M7).
- **Platform and graphics research:** self-authored ARM firmware validates the
  timer/GIC, 16 KiB MMU, SMP, EL0 context, an exact 24 MHz clock and a PAC HVC
  model; self-authored guests drive Reims compute, draw, present and input on
  NVIDIA and Intel GPUs.
- **Static Apple analysis:** IM4M signature chain, sealed SystemOS parsing and
  the Virtualization AUX/UID-key format (never executed).
- **Related work:** steelbrain's experiment (2026-08, headless macOS 13) and
  orchard (2026-09, macOS 13 desktop) ran arm64 macOS under TCG on x86 Linux
  before this project began; GoldenGate-VM showed a macOS 27 desktop under WSL2
  on 2026-10-31. orchard and GoldenGate-VM patch AVPBooter and use prebuilt Tart
  images, and no public project reports a completed native restore on Linux. See
  the [related-projects survey](docs/reports/RELATED-PROJECTS-SURVEY-20261108.en.md)
  (2026-11-08; other projects' claims are not reproduced).

Details, failed runs and dated limits: [RESULTS](docs/RESULTS.en.md) and [reports](docs/reports/).

## Repository layout

| Path | Contents | Git |
|---|---|---|
| `docs/` | MASTER-PLAN, STATUS, TEST-HISTORY and RESULTS (bilingual English & Chinese) | tracked |
| `docs/reports/` | per-experiment reports with their sanitized JSON results | tracked |
| `experiments/E01…E11/` | experiment code, self-written guest firmware and offline tests; each has a README | tracked |
| `patches/` | QEMU, Reims and metal2vulkan patches on pinned upstreams | tracked |
| `tools/` | build scripts, live-run wrappers, patch and status helpers | tracked |
| `sources.lock.json` | pinned upstream revisions | tracked |
| `sources/` | pinned third-party checkouts and patched worktrees | ignored |
| `build/`, `cache/`, `runs/` | build outputs, download cache, raw run logs | ignored |
| `private/` | Apple firmware, keys, tickets, nonces, guest disks and credentials | ignored, never commit or share |

## Commands

Run everything from the repository root with `make <target>`. Most checks need
the matching `build-*` output first. Installed-system runs (cold boot, GUI,
apps) use `experiments/E05-installed-boot/run.py`; see the
[installed-boot README](experiments/E05-installed-boot/README.md) for the current
profile and private prerequisites. Tags:

- **net**: fetches pinned open-source upstreams from GitHub.
- **private**: needs user-supplied material under `private/`.
- **root**: uses `sudo -n` to load `vhci_hcd`, attach/detach USB/IP and run
  USB clients as root.

### Builds

| Target | Output |
|---|---|
| `fetch-sources` | clone/verify pinned upstream checkouts (`sources.lock.json`) and fetch the external reference USB patch |
| `build-armvirt` | diagnostic AArch64 QEMU (ARM virt, not VMApple) |
| `build-vmapple-headless` | GPU-less VMApple control build |
| `build-reims-vulkan`, `build-reims-arm64-vulkan` | Reims Vulkan library for x86 4 KiB / ARM 16 KiB guest pages |
| `build-reims-qemu-x86` | modern QEMU with apple-gfx-pci (needs `build-reims-vulkan`) |
| `build-reims-qemu-arm`, `build-reims-qemu-arm16k` | VMApple + TCG + Reims: 4 KiB negative control / 16 KiB |
| `build-qemu-clock-exact` | opt-in exact 24 MHz counter/deadline build |
| `build-qemu-bdif-usb` | mainline build used with original firmware |
| `build-shared-cache-identity` | self-authored arm64 Mach-O shared-cache identity probe (no SDK) |

### Self-contained checks (no network, no Apple code)

| Target | What it runs |
|---|---|
| `doctor` | host inventory |
| `smoke` | `gpu-compute`, `gpu-render`, `arm-uart`, `vmapple-cfg`, `vmapple-psci` |
| `gpu-compute`, `gpu-render`, `gpu-external-caps`, `gpu-external-import` | E02/E03 host Vulkan on both GPUs |
| `reims-pci-realize` | paused apple-gfx-pci realization on x86 QEMU |
| `reims-arm16k-smoke`, `reims-arm-compare`, `reims-fifo-roundtrip`, `reims-core-serial` | E11 ARM guest ↔ Reims protocol |
| `reims-gpu-compute`, `reims-gpu-draw` | E11 guest compute / offscreen draw on NVIDIA and Intel, with no-submit controls |
| `reims-gpu-irq`, `reims-gpu-restore-platform` | E11 completion interrupt and masked-SPI negative; coexistence with the restore build |
| `platform-contracts`, `platform-timer`, `platform-mmu` | E06 timer/GIC and 16 KiB MMU |
| `smp-mmu-tests`, `smp-mmu-extended` | E06 two-vCPU MMU/ASID/TLBI/atomics |
| `el0-context-tests`, `el0-context-extended` | E06 one-vCPU, two-EL0 MMU/ASID/PAuth/IRQ with negatives |
| `el0-migration-tests`, `el0-migration-extended` | E06 two-vCPU EL0 context migration under both TCG modes |
| `pauth-baseline`, `pauth-model`, `pauth-model-impdef`, `pauth-guards` | E06 PAC and opt-in HVC software model |
| `pauth-cost-bench` | E06 per-call TCG cost of PACIBSP/RETAB |
| `sync-counter-tests` | E06 per-vCPU TCG BQL and exclusive-section trigger counters (`0033`) |
| `fp16-fma-tests` | E06 exact-reference check of the float16 FMA fast paths in `0050` (host only) |
| `clock-math`, `clock-functional`, `clock-rate-exact`, `clock-check`, `clock-rate-baseline` | E06 clock |
| `bdif-usb-tests`, `bdif-usb-backpressure`, `bdif-usb-control`, `bdif-block-tests` | E05 transport with a self-written guest |
| `config-mailbox-tests`, `avp-rtc-tests` | self-written guests for the configuration DMA and RTC contracts |
| `check-research-patches` | patched worktrees reproduce exactly from pins + patches |

### Offline unit suites (no network, QEMU or root)

| Target | What it runs |
|---|---|
| `installed-offline-tests` | offline regression suites for the installed-boot controller, UI/file services, CPU profiling, QMP lifecycle, storage retention and RSP helpers |
| `kernel-usbip-adapter-tests`, `virtio-usb-control-tests`, `storage-retention-tests` | synthetic wire, control and storage unit suites |

## Working rules

- Apple images, firmware, keys, tickets, nonces, AUX, guest disks and
  credentials stay under `private/`. Never commit or redistribute them.
- Negative controls are expected to fail. A zero exit code is a statement about
  the tested boundary, not a compatibility claim.

## Prior art, acknowledgments & licensing

- **Upstream foundations:** QEMU's `vmapple` machine series (Alexander Graf,
  Phil Dennis-Jordan), `steelbrain/reims-vgpu`, `steelbrain/metal2vulkan`, and
  `steelbrain/experiment-macOS-arm64-on-asahi-linux-arm64` /
  `steelbrain/experiment-macOS-arm64-on-linux-x86` (including Anees Iqbal's
  reference USB chardev patch at commit `657bb24`), `imbushuo/qemu`,
  `libimobiledevice/idevicerestore`, `libtatsu`, `blacktop/ipsw`, and
  `blacktop/go-apfs`. See the dated
  [related-projects survey](docs/reports/RELATED-PROJECTS-SURVEY-20261108.en.md)
  ([中文](docs/reports/RELATED-PROJECTS-SURVEY-20261108.md)) for prior art
  (`27on86`, `orchard`, `GoldenGate-VM`) and scope boundaries.
- **Licensing:** Split-licensed — upstream patches under
  `patches/` retain their upstream `GPL-2.0-only` / `GPL-2.0-or-later` /
  `LGPL-3.0-or-later` terms, while original PoC code, bare-metal probes, tools,
  and documentation outside `patches/` are source-available (non-commercial)
  under [`PolyForm-Noncommercial-1.0.0`](LICENSE) (full texts in [`LICENSES/`](LICENSES/)).
