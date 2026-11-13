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
- **Latest runs with new results (Run151–156, on the `0046–0050` build):**
  - **Run151** repeats the full signed native restore on a fresh blank disk
    with the `0045` build in 43.6 min (2,613.93 s), 2.57× faster than run-53.
  - **Run155** and **Run156**, with the 30-second timeout diagnostic off,
    start Safari cold at `+720 s` after launch, while post-login background load
    is still high; its first GPU connection initializes in **2.941 s** and
    **2.163 s**, below WebKit's unmodified 3.000 s watchdog deadline, with 0
    watchdog exits. A fresh relaunch took 1.417 s.
  - **Run154** stays up for 2.1 hours and unlocks the lock screen. **Run156**
    passes a multi-pass WebGL2→Metal→Reims→Vulkan readback (98,304 bytes, 0
    errors), a stock app and framework suite (Calculator, TextEdit, System
    Information, jsc, sips, sqlite3, plutil, gzip: 7/7) and two bit-exact
    `afplay` captures with no zero blocks, and shows from the disk and the
    kernel log that the main APFS container unmounts cleanly at guest shutdown
    ([Run156 report](docs/reports/M3-EXPANDED-VALIDATION-RUN156.en.md)).
- **Current work:** active again from 2026-11-10.
  Runs 130–142 traced Safari's slow GPU initialization to guest CPU saturation
  plus 14–30% vCPU stall time on QEMU's global lock (`bql`) and stop-the-world
  sections ([PAC algorithm](docs/reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.en.md),
  [guest RAM size](docs/reports/M3-RAM-SIZE-GPU-INIT-TIMING.en.md),
  [host CPU sampling](docs/reports/M3-GPU-INIT-HOST-CPU-USE.en.md),
  [quiet guest](docs/reports/M3-QUIET-GUEST-GPU-INIT-TIMING.en.md)).
  Runs 143–146 ([sync-sources report](docs/reports/M3-QEMU-SYNC-SOURCES.en.md))
  counted the lock and exclusive-section trigger sources (`0033`) and landed
  `0034–0039`, cutting saturated BQL acquisitions by 90% (3.4% blocked).
  Runs 147–156 ([throughput report](docs/reports/M3-TCG-THROUGHPUT-AND-ORIG3S.en.md))
  profiled the remaining helper hot spots with `perf` and landed `0040–0050`
  (jump cache, SoftMMU flushes, PAuth and FP16 fast paths), raising the share of
  guest time in translated code (`[JIT]`) to 25.7% and bringing the cold launch
  under 3 s. Next: keep a copy of the disk Run151 restored and cold-boot it on
  the `0050` build, recheck on the Intel iGPU, and run longer and broader
  workloads.
  Mainline is `build/qemu-bdif-usb/qemu-system-aarch64`, SHA256
  `e6f7b2cb32df3779531cf1b93612a67d8e1fa350c8b894c19d0c9e71a60f4a51`, with
  the patch chain through 0050; see the
  build manifest and [STATUS](docs/STATUS.en.md).
- **Open problems** (evidence and a per-run table are in
  [RESULTS section 2](docs/RESULTS.en.md)):
  - *Margin under the 3 s deadline.* The last two cold boots (Run155–156)
    passed; earlier builds still saw watchdog kills (Run147 on `0039`, the
    first trial of Run152 on `0048`), so more cold boots are needed before
    calling it stable.
  - *Audio.* Earlier runs dropped PCM frames in some trials. Run156's two
    captures on `0050` were bit-exact with no zero blocks; long streams and the
    cause of the earlier drops are untested.
  - *Host side.* Vulkan `DeviceLost`, one i915 GPU hang and two unexpected
    host reboots occurred during runs.
- **Not yet:** broad third-party app and Metal coverage, multi-day stability,
  physical input latency, sleep/wake and input methods, and every performance
  target (M5–M7).
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
