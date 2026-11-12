# M3 BGRA8 native-window validation — Run83

**Run:** `m3-bgra8-native-validation-20261104-83`  
**Started:** 2026-11-04 11:22:10 UTC  
**Finished:** 2026-11-04 17:06:49 UTC, 20,678.355 seconds total.  
**Follow-up:** [STATUS.md](../STATUS.md); Run85 continues application/network validation on Intel.

## Result established so far

The current BGRA8 source-over candidate boots the installed normal macOS, accepts password login, and displays the desktop in the NVIDIA native window. The previous FramebufferFetch fatal has not appeared in the inspected Run83 stderr. This is positive evidence for the tested profile; it does not certify every Metal pipeline or GPU configuration.

The profile is 8 vCPUs, QARMA5/TCG, USB keyboard/tablet, user-mode NAT, NVIDIA native window, exact half blend and a requested 30 Hz diagnostic refresh. The executable SHA-256 is `14ce3b403abfd311316abf7424fe8a6ffb76f881a4fd1eb46aa99335a6413704`. QEMU PID 97092 and start ticks 9242536 bind the screenshots to this live run. The executable is not rebuilt while the VM is running.

## Independent native-window evidence

All paths below are under `runs/e05-installed-boot/m3-bgra8-native-validation-20261104-83/`.

| Evidence | Observation | Boundary |
|---|---|---|
| `ui-login-2.png` | Account/password login screen | QMP pixels, not host-window capture |
| `native-desktop.png` / `.json` | Logged-in desktop, restored Terminal windows, Dock and wallpaper | Unique PID-bound X11 client pixels, 1920×1080 |
| `native-spotlight.png` / `.json` | Desktop, fresh Terminal prompt, Spotlight and restored Safari window | Restored tabs do not prove a new successful navigation |
| `results.json`, `current-process.jsonl` | Normal-OS identity established; owned collection retained through controller cleanup | Raw `gui_verified` remains distinct from this manual review |

`native-desktop.png` SHA-256: `7afda926e55df0384ea459b9e25d051f5f971cfa1c9143f0cf7ee51ccb5fabd4`.

The new `guest_ui.py native-screenshot` command captures only the unique X11 client with the selected run's QEMU PID. It reuses process/argv/start-ticks/inspection-peer validation before and after capture, refuses ambiguous windows, and records PNG hash and geometry. It does not capture the host desktop or establish physical scanout, FPS or input latency. The delivery validation reported 25/25 offline UI-helper checks; the actual Run83 consumer then produced the native screenshots above.

## Targeted BGRA8 tests

Existing patch: `patches/metal2vulkan/0001-half-float-fetch-source-over.patch`, SHA-256 `5536b16ceefc113884eee614b7427becd544658bcaa55382f224de4be993adc3`.

Two targeted tests passed: BGRA8 ordered storage-form selection, and ordered pipeline compilation beside depth/stencil. The execution trace proves the public `stage_io` and `exf_fetch_blend` fixtures were opened; `llvm-as` ran twice and `spirv-val` ran eight times, all successfully. Coverage includes default/exact policies, vertex/fragment stages and Vulkan 1.2/1.4 validator environments. These are default-features debug functional tests, not a performance benchmark or full suite.

Evidence: test results, test output, tool trace.

## Run82 observation failure

An independent harmless-process experiment reproduced the relevant Linux behavior: replacing the running executable leaves its process, argv and start ticks intact but changes `/proc/PID/exe` to the deleted original pathname. The existing observer's executable-path comparison then fails. This matches Run82's successful identity check → relink → identity error sequence, but Run82 did not retain the two comparison results, so its historical cause remains a strongly supported explanation rather than a proven branch diagnosis. No ownership guard was weakened; this run excludes concurrent relinking.

## Application follow-up and closeout

The initial Terminal windows were restored content before new shells were ready. An early typed network command therefore did not establish guest execution. The first host file-exchange process also exited; a replacement listener was verified at 127.0.0.1:18769 before further testing. Neither absence of uploads is treated as proof of guest network failure.

The session subsequently auto-locked. A later desktop transition was confirmed with byte-identical QMP/native RGB, but submitted shell commands again lacked execution evidence. A bounded pointer-only keepalive and three-minute observation retained twelve identical lock-screen frames. Native-window Return input produced a waiting frame and a cleared password field; this rules out treating the entire path as completely input-insensitive, but does not establish the authentication or wait cause. See `application-observation.json`, `native-return-probe.json` and their screenshots in the run directory.

The 14:00–14:20 UTC sample window contains 249 of 312 CPU slots at `0xfffffe0030919434`, with median host vCPU consumption of 0.93 cores and graphics consumption at most 0.03 cores. Offline decoding maps that PC to the instruction after WFI in the fixed kernel, consistent with QEMU's WFI halt behavior. A branch can also skip WFI to the same PC, so these are WFI-successor samples rather than measured sleep time. This evidence does not support overall CPU saturation as the explanation. See `cpu-occupancy-1400-1420.json`.

After the bounded attempts ceased adding discriminating evidence, the controller received SIGINT and performed host QMP quit. QEMU exited 0 and was reaped; raw `passed=false`/`interrupted=true` are retained. No guest-origin or filesystem-clean shutdown is claimed, and the duration is not a mixed-workload stability pass.

Run84 attempted a fresh NVIDIA control but stopped before starting QEMU: Vulkan enumeration failed because the host's loaded kernel module was 595.91.07 while user-space libraries had been updated to 595.99.02. That host preflight failure is separate from Run83's guest observations. Run85 used the independently enumerated Intel ICD. Its requested host pointer keepalive was later found ineffective (invalid CLI action); guest caffeinate was independently verified. The matching project-local NVIDIA runtime subsequently passed NVML, Vulkan and the existing host compute/render consumers. Application first-load stability, networking, complete filesystem cleanliness, physical latency, FPS and broad compatibility remain unproven.
