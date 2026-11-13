> **Language:** **English** | [简体中文](M3-EXPANDED-VALIDATION-RUN156.md)

# M3 Expanded Installed-System Validation (Run156: Multi-Pass WebGL2/Metal Pipeline, Stock App & Framework Suite, VirtIO Sound Boundary Trace, and APFS Clean-Unmount Verification)

Updated: `2026-11-13 UTC`

## Scope and motivation

Following patches `0033–0050` (eliminating multi-vCPU TCG synchronization contention and accelerating pure guest execution), this experiment verifies that the mainline fast QEMU binary (`build/qemu-bdif-usb/qemu-system-aarch64`, SHA-256 `e6f7b2cb32df3779531cf1b93612a67d8e1fa350c8b894c19d0c9e71a60f4a51`, with **`--webkit-timeout-diagnostic` and `--sync-stats` both off**) preserves bit-exact and algebraically deterministic correctness across broader stock macOS 27.0.1 (`26A434`, `arm64e`) workloads, and resolves the `filesystem_clean_unmount_proven` boundary tracked in `docs/PLAN-STATE.json`.

Four orthogonal subsystems and their independent host validators are exercised:

1. **Multi-pass WebGL2 -> Metal -> Reims -> `metal2vulkan` -> host Vulkan rendering pipeline**:
   - Guest fixture: [`webgl-complex-probe.html`](../../experiments/E05-installed-boot/guest-diag/webgl-complex-probe.html)
   - Host pixel oracle: [`validate-webgl-complex.py`](../../tools/validate-webgl-complex.py) (offline unit suite: [`test-validate-webgl-complex.py`](../../experiments/E05-installed-boot/test-validate-webgl-complex.py); isolated host SwiftShader preflight: [`test-webgl-complex.py`](../../experiments/E05-installed-boot/test-webgl-complex.py))
   - Capabilities exercised: `128 x 96` offscreen `RGBA8` color texture + `DEPTH24_STENCIL8` renderbuffer FBO, `std140` Uniform Buffer Object (`UBO`) bound across two linked shader programs, `4 x 4` procedural 2D texture (`REPEAT` + `NEAREST`), stencil masking (`STENCIL_TEST` `REPLACE` / `EQUAL`), depth occlusion (`DEPTH_TEST` `LEQUAL`), 3-instance `drawArraysInstanced` per-instance attribute streams, exact rational `SRC_ALPHA / ONE_MINUS_SRC_ALPHA` blending ($\alpha = 51/255 = 1/5$, exercising Reims `0007` and `metal2vulkan 0001` blend state serialization), and a second-pass default-framebuffer `texelFetch` composite.
2. **Stock macOS `arm64e` GUI application and userland framework suite**:
   - Guest probe: [`app-suite-probe.sh`](../../experiments/E05-installed-boot/guest-diag/app-suite-probe.sh)
   - Independent host validator: [`validate-app-suite.py`](../../tools/validate-app-suite.py) (offline unit suite: [`test-app-suite.py`](../../experiments/E05-installed-boot/test-app-suite.py))
   - Capabilities exercised:
     - `JavaScriptCore` (`jsc`): Sieve of Eratosthenes up to `100,000` (prime count and sum), `16 x 16` `Float64Array` matrix multiplication (trace and squared Frobenius norm), and 4096-exponent `BigInt` modular exponentiation;
     - `CoreGraphics` / `ImageIO` (`sips`): `64 x 48` four-quadrant lossless BMP -> PNG encoding (verified pixel-for-pixel across all 3,072 pixels by host `Pillow` with 0 mismatches) and `32 x 24` downsampling;
     - `SQLite3`: `WAL` journal mode, 250-row recursive-CTE ACID commit, explicit `ROLLBACK` isolation, `SUM(...) OVER (...)` window aggregation, and `VACUUM INTO` database export verified on the host via Python `sqlite3` `PRAGMA integrity_check` and aggregate queries;
     - `CoreFoundation` property lists (`plutil`): XML -> `bplist00` binary format -> JSON round-trip verified on the host via `plistlib`;
     - Archive compression and cryptographic digest (`gzip -9` + `shasum -a 256`) alongside `sw_vers` / `uname` architecture checks;
     - Concurrent launch, `lsappinfo` LaunchServices foreground registration, host native-window screenshot capture, and clean `osascript` quit (`pgrep_exit = 1` confirmed prior to shutdown) of `Calculator.app`, `TextEdit.app`, and `System Information.app`.
3. **VirtIO sound (`afplay`) dual-voice bit-exactness and `pcm_boundary_trace` causal check**:
   - Combines [`freeze-audio-voices.py`](../../tools/freeze-audio-voices.py), [`validate-audio-test-tone.py`](../../tools/validate-audio-test-tone.py), and [`pcm_boundary_trace.py`](../../experiments/E05-usbip-kernel/pcm_boundary_trace.py) on the `0050` binary to verify bit-exact 48 kHz stereo 96,000-frame PCM delivery across two independent `afplay` runs and compare internal zero-filled WAV underrun blocks against the Run108 baseline (`18` zero blocks).
4. **Read-only APFS container checkpoint ring and clean-unmount verification**:
   - Host read-only inspector: [`check-apfs-clean-unmount.py`](../../tools/check-apfs-clean-unmount.py) (offline unit suite: [`test-apfs-clean-unmount.py`](../../tools/test-apfs-clean-unmount.py))
   - Implemented from Apple's public *Apple File System Reference* ("Mounting an Apple File System Container") and XNU `apfs.kext` (`nx_mount:1482–1509`): while mounted, an APFS container appends checkpoints only inside its `nx_xp_desc` ring while leaving container block 0 at an older `o_xid`; only a clean container unmount flushes the final committed `nx_superblock_t` to container block 0 so **`block0.o_xid == latest_checkpoint.o_xid`** (mismatch causes `nx_mount: reloading after unclean unmount, checkpoint xid C, superblock xid S` on the next boot). The inspector opens the image strictly `O_RDONLY`, confirms no owned host process holds the inode open, inspects all three Apple GPT APFS partition types (`Apple_APFS`, `Apple_APFS_ISC`, `Apple_APFS_Recovery`), and verifies Fletcher-64 checksums across block 0, the highest-`o_xid` complete checkpoint slice, the container `OMAP` B-tree, and every `apfs_superblock_t` (`APSB`) without emitting UUIDs or volume names.

---

## Pre-run hypotheses and decision criteria (Run156)

Executed as `m3-expanded-system-validation-20261113-156` on the `0050` binary (8 vCPUs, 4 GiB RAM, `qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`, NVIDIA native window, VirtIO network and VirtIO sound enabled, **`--webkit-timeout-diagnostic` and `--sync-stats` omitted**):

1. **H14 (Multi-pass WebGL2/Metal bit-exactness)**: Cold first-launch Safari directly opens `webgl-complex-probe.html`, initializes its first GPU process with `0` watchdog exits under the unmodified 3.0 s deadline, and uploads `49,152`-byte `offscreen` and `default` RGBA8 readbacks that both pass `validate-webgl-complex.py` with **`bit_exact = true` (0 mismatched bytes across all 98,304 bytes)**.
2. **H15 (Stock application & framework suite)**: `app-suite-probe.sh` exits `0` and passes all **7 independent host checks** in `validate-app-suite.py` (`summary_identity`, `javascriptcore_exact`, `sips_coregraphics_png`, `sqlite3_wal_acid`, `plutil_bplist_roundtrip`, `gzip_and_sha256`, `gui_apps_lifecycle`), with all three GUI applications confirmed exited (`pgrep_exit = 1`) before shutdown.
3. **H16 (VirtIO sound bit-exactness & underrun reduction)**: Both closed WAV voices frozen by `freeze-audio-voices.py` pass `validate-audio-test-tone.py` (`full_tone_bit_exact = true`, `96,000` contiguous frames with all-zero surrounding samples), `pcm_boundary_trace.py` decodes a complete terminal trace (`complete = true`, `errors = []`), and internal WAV zero-block count drops materially below the Run108 baseline (`18` blocks).
4. **H17 (APFS container clean-unmount proof)**: After writing a persistent verification file on the Data volume and executing `sudo /sbin/shutdown -h now` (`qemu_exit_code = 0`, `guest_shutdown_observed = true`), read-only `check-apfs-clean-unmount.py` verifies intact Fletcher-64 checksums and **`block0_xid == latest_checkpoint.xid`** (`main_os_container_cleanly_unmounted = true`) on both the main installed macOS container (`Apple_APFS` `disk0s2`, all 6 volumes: `SYSTEM`, `DATA`, `PREBOOT`, `RECOVERY`, `VM`, `UPDATE`) and `Apple_APFS_ISC` (`disk0s1`), matching zero `reloading after unclean unmount` notices for those containers in `kernel-boot.log` (while explicitly documenting the untouched `Apple_APFS_Recovery` partition `disk0s3` retained from initial `qmp_quit` restore).

---

## Observed results (Run156: All four hypotheses H14–H17 confirmed)

### Run identity and host/guest health

- Run directory: `runs/e05-installed-boot/m3-expanded-system-validation-20261113-156`
- Binary: `build/qemu-bdif-usb/qemu-system-aarch64` (SHA-256 `e6f7b2cb32df3779531cf1b93612a67d8e1fa350c8b894c19d0c9e71a60f4a51`, patch stack through `0050`)
- Profile: 8 vCPUs, 4 GiB RAM, `qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`, NVIDIA native window, VirtIO network (`--net-control-queue-size 512`) and VirtIO sound (`--sound-backend wav --sound-queue-size 512 --sound-streams 1 --sound-pcm-trace`), **`--webkit-timeout-diagnostic` and `--sync-stats` omitted**.
- Lifecycle and thermals: starting package temp `45 °C`; login window at `+151 s`, login at `+193 s`, desktop at `+362 s`; after completing all four validation stages and confirming `Safari` and all three test GUI apps had exited (`pgrep_exit = 1`), the guest shut down cleanly via `sudo /sbin/shutdown -h now` (`qemu_exit_code = 0`, `qemu_reaped = true`, `host_quit_requested = false`, `guest_shutdown_observed = true`, QMP event `SHUTDOWN {'guest': True, 'reason': 'guest-shutdown'}`, `errors = []`, elapsed `1,429.6 s`); 284 host package temperature samples with `min = 50 °C`, **`mean = 68.6 °C`**, `max = 95 °C`; clock probes `r156a = -3.1615 s` (spread `2.5 ms`) and `r156b = -3.1665 s` (spread **`0.69 ms`**); **`42.02B` architectural QARMA5 PAuth operations** executed at an **`87.53%`** overall hit rate.

### H14 verdict (Passed): `+720 s` cold first launch repeated `< 3.000 s` at `2.163 s`, with `0` mismatched bytes across `98,304` multi-pass WebGL2/Metal bytes

1. **Cross-boot repeatability at the `+720 s` diagnostic-off cold window (2/2)**:
   - Triggered directly at `+720 s` (zero `perf` perturbation) into `webgl-complex-probe.html#r156-webgl-complex`:
     - `outcome = initialized`, `gpu_processes_constructed = 1`, **`gpu_unresponsive_exits = 0`**;
     - **`ui_request_to_did_initialize = 2.163 s`** (`gpu_entry_to_did_initialize = 2.160 s`, `gpu_entry -> registrar_activation = 1.085 s`, `registrar -> helper_create = 0.089 s`, `helper_create -> destroy = 0.664 s`, `helper_destroy -> did_initialize = 0.322 s`; window `vcpu_mean_busy = 5.544`);
     - Combined with Run155's `2.941 s`, the `0050` mainline has cleared WebKit's unmodified `3.000 s` hard deadline at `+720 s` across **2/2 consecutive cold boots** without any timeout diagnostic.
2. **Multi-pass WebGL2 -> Metal -> Reims -> `metal2vulkan` -> NVIDIA Vulkan pixel oracle**:
   - WebKit reported `Apple Inc. / Apple GPU` (`WebGL 2.0`, `WebGL GLSL ES 3.00`, `depth = true`, `stencil = true`), compiling all 4 shaders, linking both programs, and completing all 4 passes (`pass1a_textured_stencil`, `pass1b_instanced_depth_stencil`, `pass1c_alpha_blend`, `pass2_default_composite`) with zero GL errors;
   - Host `validate-webgl-complex.py` compared both raw `128 x 96` `RGBA8` readbacks byte-for-byte against the pure-Python software reference rasterizer:
     - Offscreen FBO (`r156-webgl-complex-offscreen.bin`, `49,152` bytes, SHA-256 `9603e435…`): **`bit_exact = true`, `mismatched_bytes = 0`, `max_channel_abs_diff = 0`**;
     - Default framebuffer (`r156-webgl-complex-default.bin`, `49,152` bytes, SHA-256 `98a456fd…`): **`bit_exact = true`, `mismatched_bytes = 0`, `max_channel_abs_diff = 0`**;
   - Native window capture `r156-t1.png` visually confirms the navy UBO border, stencil-clipped left boundary, repeating 4x4 procedural texture, depth occlusion ordering across instanced quads, and translucent alpha-blended band. **H14 is confirmed.**

### H15 verdict (Passed): All 6 native `arm64e` userland framework checks and 3 stock GUI applications passed host validation (7/7)

`app-suite-probe.sh r156apps 18` completed all 5 CLI framework workloads and launched all 3 GUI apps within 17 seconds, held the GUI windows for 18 seconds while `r156-gui-apps.png` was captured, and cleanly quit all 3 apps before uploading `r156apps.tgz` (SHA-256 `cf6b9e04…`). Host `validate-app-suite.py` passed **7/7 checks**:

| Check | Guest component | Independent host verification |
|---|---|---|
| `summary_identity` | `sw_vers`, `uname` | Verified `macOS 27.0.1` (`26A434`), `RELEASE_ARM64_VMAPPLE arm64`, `collector_exit = 0` |
| `javascriptcore_exact` | `JavaScriptCore.framework/.../jsc` | `100,000` prime sieve (`9,592` primes, sum `454,396,537`), `16 x 16` `Float64` matrix product (`trace = 83,224`, `frob_sq = 8,168,844,416`), and `3^4096 mod 1000000007 = 451694074` matched host oracle exactly |
| `sips_coregraphics_png` | `/usr/bin/sips` (CoreGraphics / ImageIO) | Lossless `64 x 48` PNG matched all `3,072` pixels with `0` channel error; `32 x 24` downsampled quadrant centers verified via host `Pillow` |
| `sqlite3_wal_acid` | `/usr/bin/sqlite3` | `WAL` mode, 250-row recursive CTE commit, `ROLLBACK` isolation, window sums, and exported `compact.db` `PRAGMA integrity_check = ok` verified via host Python `sqlite3` |
| `plutil_bplist_roundtrip` | `/usr/bin/plutil` (CoreFoundation) | Valid `bplist00` binary plist and JSON output matched expected dictionary structure via host `plistlib` |
| `gzip_and_sha256` | `/usr/bin/gzip`, `shasum -a 256` | Compressed 1,024-line deterministic stream decompressed identically on the host with matching SHA-256 digests |
| `gui_apps_lifecycle` | `Calculator.app`, `TextEdit.app`, `System Information.app`, `lsappinfo` | Concurrent live PIDs and LaunchServices foreground registrations (`com.apple.calculator`, `com.apple.TextEdit`, `com.apple.SystemProfiler`) verified alongside native window capture (`TextEdit` document window, `System Information` menu bar, Dock running dots), followed by confirmed clean exit (`pgrep_exit = 1`) |

### H16 verdict (Passed): Both `afplay` voices passed `full_tone_bit_exact = true` with `0` zero-block underruns in `VSNDPCM1`

1. **Dual frozen WAV voice validation**:
   - Voice 1 (`pcm-voice1/voice-1.wav`, SHA-256 `42fe0ea1…`, `126,464` frames): `full_tone_bit_exact = true`, `96,000` contiguous reference frames (`2.000 s`) at `[14848, 110848)`, `surrounding_samples_all_zero = true`, measured rising-zero-crossing frequencies `439.999996 Hz` (left) and `660.000001 Hz` (right);
   - Voice 2 (`pcm-voice2/voice-1.wav`, SHA-256 `900af42e…`, `137,728` frames): `full_tone_bit_exact = true`, `96,000` contiguous reference frames at `[2560, 98560)`, `surrounding_samples_all_zero = true`.
2. **Post-exit `pcm_boundary_trace.py` causal check**:
   - Decoded `guest-audio-boundary.bin` with `complete = true`, `terminal = 1` (clean file close), `errors = []`, `reconstruction_errors = []` across `465` records;
   - Guest `coreaudiod` negotiated stereo `48 kHz` `VIRTIO_SND_PCM_FMT_FLOAT` (enum `19`, 8 bytes/frame): both `generation 1` (`1,011,712 B = 126,464 x 8 B`, SHA-256 `6646d44a…`) and `generation 2` (`1,101,824 B = 137,728 x 8 B`, SHA-256 `a7fc7308…`) had byte-identical `pop`, `populate`, and `accepted` streams with **`0` internal 4 KiB all-zero underrun blocks** across the entire `768,000`-byte (`96,000`-frame) tone span in both runs (compared against `18` zero blocks on Voice 1 in Run108). **H16 is confirmed.**

### H17 verdict (Passed): Clean unmount of the main macOS and ISC APFS containers proven by both on-disk superblocks and XNU `nx_mount`

1. **Cross-boot Data-volume persistence**: `Desktop/macos-local-persistence.txt` (created in Run63) retained its exact SHA-256 `bc1d2eab496a732ba15ffcc27e5bbeeee788cb12dfd900f0a1891dddf0d2a98a`, and newly written + `sync`ed `Desktop/r156-persist.txt` matched SHA-256 `dc9f70f39b61577510f9322fdebf589fb4268ae7fbad9faf25ca8c73cea52f82`.
2. **Post-exit read-only on-disk inspection (`check-apfs-clean-unmount.py`)**: Executed strictly `O_RDONLY` after verifying zero open host file descriptors (`errors = []`):
   - Partition 0 (`Apple_APFS_ISC`, `disk0s1`): container block 0 and the highest ring checkpoint both held **`xid = 555`** (`block0_xid == latest_checkpoint.xid`), all 4 `APSB` volumes passed Fletcher-64, and writable volumes recorded `2026-11-13T08:07:45Z` unmount timestamps — **`container_cleanly_unmounted = true`**;
   - Partition 1 (`Apple_APFS` main installed OS container, `disk0s2`): container block 0 and the highest checkpoint across `140` valid ring superblocks both held **`xid = 88420`** (`block0_xid == latest_checkpoint.xid`), the container `OMAP` and all **6 volumes** (`SYSTEM`, `PREBOOT`, `RECOVERY`, `UPDATE`, `DATA`, `VM`) passed Fletcher-64, and `DATA`, `PREBOOT`, `UPDATE`, and `VM` recorded fresh unmount timestamps at `2026-11-13T08:07:45.033Z–08:07:45.514Z` — **`main_os_container_cleanly_unmounted = true`**;
   - Partition 2 (`Apple_APFS_Recovery`, `disk0s3`): retained `block0_xid = 52` vs `latest_checkpoint.xid = 55` from the initial `qmp_quit` restore session because normal macOS boots never mount the standalone Recovery partition read-write;
   - **Independent XNU `apfs.kext` oracle**: In `kernel-boot.log` of both Run155 (booted after Run154's clean shutdown) and Run156 (booted after Run155's clean shutdown at `xid = 87646`), `nx_mount` logged `stable checkpoint indices` with **zero `reloading after unclean unmount` warnings** on `disk0s1`/`disk1` and `disk0s2`/`disk3` (emitting `reloading after unclean unmount` only on untouched `disk0s3`, whereas post-restore cold boot Run54 had logged `disk0s2 reloading after unclean unmount, checkpoint xid 720, superblock xid 718`). Clean APFS container unmount on `shutdown -h now` is therefore established by both on-disk structure and the XNU kernel mount path.

### Scope boundaries

- Multi-pass WebGL2 verification proves byte-exact offscreen FBO, depth/stencil, UBO, 2D texture, instanced draw, and rational source-over alpha-blend execution for this pipeline, not arbitrary third-party 3D engines, compute shaders, or physical scanout FPS/latency.
- Dual-voice VirtIO sound verification proved zero underrun blocks across two 2-second `afplay` runs on `0050`, not multi-hour continuous streaming, microphone capture, or physical speaker latency.
- Standalone `Apple_APFS_Recovery` (`disk0s3`) retains its initial `qmp_quit` restore `xid 52 / 55` gap because normal OS boots do not mount it read-write, whereas both active containers (`Apple_APFS` `disk0s2` and `Apple_APFS_ISC` `disk0s1`) flush block 0 cleanly on every guest shutdown.

