> **Language:** **English** | [简体中文](RELATED-PROJECTS-SURVEY-20261108.md)

# Survey of Related Projects: Public Precedents for Running arm64 macOS on Non-Apple Hardware

Survey date: 2026-11-08 UTC. Method: web search, reading each project's GitHub pages, GitHub API metadata, issues, and PRs. None of these projects' code was downloaded, built, or executed.

Evidence categories follow Section 2 of [MASTER-PLAN](../MASTER-PLAN.en.md):

- **O**: Facts read directly from GitHub pages, APIs, or this repository, such as repository creation dates and commit hashes.
- **R**: Features, timings, and issues self-reported by each project, not reproduced in this project.
- **H**: Candidate improvement directions proposed here, not yet verified.

These projects evolved rapidly in the second half of 2026; star counts and commit counts reflect only the time of this survey.

## Conclusions

1. **Both "running arm64 macOS on x86-64 Linux under TCG" and "reaching the desktop" have earlier public precedents.** steelbrain's 27on86 repository was created on 2026-08-14 (O) and self-reports headless macOS 13 boot, SSH, and clean shutdown (R). orchard was created on 2026-09-19 (O) and self-reports a macOS 13 desktop (R). This project's first commit is 2026-10-13 (O). GoldenGate-VM published a macOS 27 desktop on Windows WSL2 on 2026-10-31 (O/R), the same day this project first reached the desktop in run-63.
2. **Based on public records, this project still differs in several ways:** completing a native restore from IPSW using Linux alone with live TSS personalization signatures; executing an unmodified stock AVPBooter; implementing Apple HVC key-state switching for PAC; and verifying the widest range of system functionality. Details follow below.
3. **The most promising idea to test from related work is the PAC algorithm (H).** orchard uses QEMU's `pauth-impdef` on an installed macOS system (R), whereas this project's mainline still uses QARMA5, and E06 measured roughly an 11× difference in per-call overhead between the two (O).

## Timeline

| Date (UTC) | Project | Event | Category |
|---|---|---|---|
| 2021-05 | BlackBerry Research | Booted the macOS 11 arm64 kernel in QEMU on an Intel Linux host for security research | R |
| 2023-06 to 2025-01 | Upstream QEMU vmapple | Alexander Graf and Phil Dennis-Jordan's vmapple machine patch series (v1 through v17), now in upstream documentation | O |
| 2026-08-14 | steelbrain/experiment-macOS-arm64-on-linux-x86 (27on86) | Repository created | O |
| 2026-08-18 | steelbrain/experiment-macOS-arm64-on-asahi-linux-arm64 | Commit 657bb24; this project adopted its USB transfer patch on 2026-10-14 | O |
| 2026-09-19 | yaelliethy/orchard | Repository created; most recent push on 2026-09-25 | O |
| 2026-10-13 | This project | First commit | O |
| 2026-10-21 | losos-project/desktop PR #272 | Added M1 CPU model and PAC hypercalls on top of 27on86, later merged via #276 | O |
| 2026-10-30 | This project | -53 completed native restore; -54 standalone cold boot | O |
| 2026-10-31 | This project, JeanxPereira/GoldenGate-VM | This project reached desktop in -63; GoldenGate-VM repository created and published macOS 27 desktop | O |
| 2026-11-01 | ChefKissInc/Inferno | Repository archived | O |
| 2026-11-02 | YahiaAngelo/vphone-linux issue #20 | macOS 27 recovery image on Linux stuck at SSV authenticator | O/R |

## Item-by-item comparison

Except for the "This project" column, all entries in the table are self-reported by the respective projects (R).

| | 27on86 | Asahi experiment | orchard | GoldenGate-VM | This project |
|---|---|---|---|---|---|
| Host | x86-64 Linux | Asahi Linux, M2 Pro, KVM | x86-64 Linux, AMD Renoir iGPU (RADV) | Windows 11 + WSL2, Ryzen 7 5700X + RX 6750 XT | Ubuntu 26.04.1, Core Ultra 7 255HX, Intel iGPU + RTX 5070 Ti Laptop |
| Guest | macOS 13 | macOS 13.6 (22G120) | macOS 13 | macOS 27 (26A428) | macOS 27.0.1 (26A434) |
| System disk source | Unspecified | Installed on a Mac via macosvm | Cirrus Labs Tart image `macos-ventura-base` | Tart image `macos-golden-gate-vanilla:27.0` | Native restore from IPSW on Linux, live TSS |
| AVPBooter | Unspecified | Copied from Virtualization.framework | NyanSatan patch (offset 0x2314) | NyanSatan patch (offset 0x2370) | Stock, unmodified |
| QEMU base | vmapple adapted to run without HVF (branch `vmapple-tcg`) | Upstream vmapple plus KVM kernel patches | Upstream master plus custom `apple-vm` machine | orchard's `gg-macos27` branch | steelbrain/qemu-reims-vgpu plus 32 patches |
| PAC | Accepts all signatures per losos-project PR | Hardware PAC, KVM saves APCTL/KERNKEY | `pauth-impdef`; ignores per-process switching, all processes share one key set | Unspecified | QARMA5 plus HVC key-state model, optional memo |
| vCPUs | 8 | Unspecified | 12 | 12 | 8 |
| GPU stack | None (headless) | Reims + Vulkan (llvmpipe) | reims-vgpu + metal2vulkan, RADV | reims-vgpu + metal2vulkan, Mesa Dozen | Reims ARM 16 KiB port + metal2vulkan, NVIDIA and Intel |
| Self-reported progress | Headless boot, SSH, reboot, clean shutdown | Graphical Setup Assistant | ~75 s to desktop (12 vCPUs; first boot to login takes several minutes), keyboard, SSH | ~15 min to Keyboard Setup Assistant, 20–25 min to Dock, 30–35 min to SSH | Setup Assistant, login, keyboard/pointer, Finder/Dock/Terminal, Safari, TextEdit, curl, networking, audio, WebGL2 readback, guest shutdown |
| Self-reported issues | Newer versions (up to macOS 27) listed as future milestones | Linux-native installation unfinished | Pointer input does not reach guest, rounded corners filled black, occasional GPU panics, fresh images need 2–5 reboots | Mouse untested, Liquid Glass lacks blur, very slow | See [STATUS](../STATUS.en.md): Safari first load, PCM repeatability, FS clean, performance |

## How this project differs (with project evidence)

1. **Native restore completed using Linux alone.** Run -53 completed restore using stock Apple components and live TSS tickets, and -54 cold-booted independently ([RESULTS](../RESULTS.en.md) Section 1 rows 9–10, M2-PAC-HVC-CTRR-LIVE-FOLLOWUP). By contrast (R):
   - The Asahi experiment's attempt at direct Linux installation reached real DFU transfers without producing a bootable system disk;
   - vphone-linux stalled at the SSV authenticator;
   - orchard and GoldenGate-VM use pre-built Tart images created on a Mac.
2. **Unmodified stock AVPBooter.** This project extracts AVPBooter from the official IPSW and executes it unmodified through DFU response (M2-ORIGINAL-AVPBOOTER). orchard and GoldenGate-VM apply NyanSatan's patch, rewriting a routine that fails outside Apple's hypervisor so that it returns 0 unconditionally (R).
3. **PAC implements HVC key-state switching.** [Patch 0003](../../patches/qemu-platform/0003-vmapple-tcg-pac-model-stack.patch) derives kernel and user keys via HMAC-SHA256 from a per-VM random master secret and honors diversifier switches while keeping architectural pointer authentication active in the guest (O; see also [E06 README](../../experiments/E06-pauth-hvc/README.md)). By contrast (R): orchard ignores per-process switching so all processes share one key set, and 27on86 accepts all signatures according to the losos-project PR.
4. **Broadest verified system functionality.** Verified capabilities in this project include:
   - Setup Assistant, password login, keyboard and pointer, Finder/Dock/Terminal, file persistence across reboots, guest-initiated shutdown, curl HTTPS, Safari, and TextEdit (RESULTS Section 1 rows 11–15);
   - VirtIO sound card enumerated in CoreAudio with byte-identical reference PCM capture (Run105/107/108);
   - Byte-identical dual WebGL2 readbacks inside real Safari (Run128).

   Other projects self-report at most desktop rendering, keyboard input, and SSH (R).
5. **Different host GPU drivers verified.** This project has verified presentation and compute on both NVIDIA and Intel; orchard uses AMD RADV, and GoldenGate-VM uses Dozen on AMD (R).

## Candidate directions to borrow and verify (H)

1. **Test `pauth-impdef` on the installed system.**
   - Basis: E06 measured on a self-authored guest that each call with PACIBSP/RETAB adds 47.6 ns (impdef), 370.6 ns (QARMA3), or 553.5 ns (QARMA5) over an ordinary call, and proved that the HVC key-state model does not depend on the PAC cipher (O, [E06 README](../../experiments/E06-pauth-hvc/README.md)). In Run85, PAC accounted for roughly 42.61% of vCPU samples (O). impdef was used only during restore-stage runs 24/39 and never on the installed OS (O, M2-PAC-HVC-CTRR-LIVE-FOLLOWUP-RESULTS.json). orchard self-reports running installed macOS 13 under impdef (R).
   - Approach: Run a paired comparison of impdef vs. QARMA5 on the exact same binary and configuration, measuring boot-to-desktop time and Safari's first GPU connection initialization time.
   - Trade-off: impdef is non-cryptographic and weakens PAC security guarantees inside the guest. That is acceptable for research use, provided it is recorded as a distinct profile.
2. **vCPU count.** orchard self-reports that 12 vCPUs work best on its host (R); this project has consistently used 8 vCPUs on a 20-core host. A paired 8-vs.-12 comparison can be tested.
3. **Add a comparable boot metric.** orchard self-reports ~75 s to desktop (R), whereas this project lacks a directly comparable "boot to desktop" timing metric.
4. **Compare Reims memory integration.** orchard self-reports modifying only one site in reims-vgpu, importing the entire guest RAM allocation via `VK_EXT_external_memory_host`, and switching to anonymous memory because amdgpu rejects shared file-backed mappings (R). This project uses memfd, a dedicated 16 KiB guest-page port, and 7 Reims patches.
5. **orchard's "black-filled rounded corners" may be in the same family as the framebuffer-fetch / blending issue fixed by this project's metal2vulkan patch 0001.** Unverified.

## Other related projects

- **Upstream QEMU vmapple machine**: documentation requires a macOS host on Apple silicon using HVF in its examples; the guest supports only macOS 12 and requires a VM pre-installed via Virtualization.framework (O, read from documentation).
- **steelbrain/reims-vgpu**: the GPU stack shared across the projects above, self-described as Beta. Upstream lists three supported paths: x86 macOS on Linux KVM, and arm64 macOS on Apple silicon (via Metal, or via Vulkan over MoltenVK). It lists no "arm64 guest on x86 host" path (R).
- **imbushuo/qemu**: README describes Apple silicon support based on Alex's HVF patches (R); this project uses it solely as a headless VMApple control without GPU.
- **losos-project/desktop PR #272/#276**: builds on 27on86 commit 509a4ce, adding an `apple-m1` CPU and PAC hypercalls; the author notes it has not yet booted macOS (R).
- **ChefKissInc/Inferno** (formerly QEMUAppleSilicon, derived from qemu-t8030): emulates iPhone 11 to run iOS. Maintainers stated in 2026-07 that vmapple support would be removed, and the repository was archived on 2026-11-01 (O). GoldenGate-VM reports that orchard's early PAC emulation originated from Inferno (R).
- **NyanSatan/Virtual-iBoot-Fun**: source of the AVPBooter patches used by orchard and GoldenGate-VM (R).
- **BlackBerry (2021)**: kernel-only boot for security research (R).
- **Projects taking different architectural routes**: OSX-KVM / Docker-OSX (x86 macOS on KVM), Darling (user-space compatibility layer), UTM / Tart (run only on Apple silicon hosts). Not audited individually in this survey.

## Limitations

- Apart from this project's own evidence, all features and timings are self-reported by the respective projects without independent reproduction or code review here.
- The repository creation timestamp for the Asahi experiment could not be retrieved (GitHub API request failed); the timeline uses the commit date recorded in this project's [references.json](../../experiments/E06-pauth-hvc/references.json).
- Scope is limited to public projects discoverable via web search; forums, chat groups, and unpublished work are excluded.

## Sources

- [steelbrain/experiment-macOS-arm64-on-linux-x86](https://github.com/steelbrain/experiment-macOS-arm64-on-linux-x86)
- [steelbrain/experiment-macOS-arm64-on-asahi-linux-arm64](https://github.com/steelbrain/experiment-macOS-arm64-on-asahi-linux-arm64)
- [yaelliethy/orchard](https://github.com/yaelliethy/orchard), [orchard TECHNICAL.md](https://raw.githubusercontent.com/yaelliethy/orchard/main/TECHNICAL.md)
- [JeanxPereira/GoldenGate-VM](https://github.com/JeanxPereira/GoldenGate-VM)
- [YahiaAngelo/vphone-linux issue #20](https://github.com/YahiaAngelo/vphone-linux/issues/20)
- [losos-project/desktop PR #272](https://github.com/losos-project/desktop/pull/272), [PR #276](https://github.com/losos-project/desktop/pull/276)
- [ChefKissInc/Inferno issue #303](https://github.com/ChefKissInc/Inferno/issues/303)
- [steelbrain/reims-vgpu](https://github.com/steelbrain/reims-vgpu)
- [QEMU vmapple documentation](https://www.qemu.org/docs/master/system/arm/vmapple.html)
- [vmapple patch series v17](https://patchew.org/QEMU/20250112210056.16658-1-phil@philjordan.eu/20250112210056.16658-12-phil@philjordan.eu)
- [imbushuo/qemu](https://github.com/imbushuo/qemu)
- [BlackBerry 2021 blog](https://blogs.blackberry.com/en/2021/05/strong-arming-with-macos-adventures-in-cross-platform-emulation)
