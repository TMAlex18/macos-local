# M3 Safari GPU takeover — Runs81–82

**Run:** `m3-safari-gpu-takeover-20261102-81`  
**Window:** 2026-11-02 19:14:25–19:28:23 UTC  
**Purpose:** continue the blocked Run71 GUI follow-up with the explicitly authorized project test account, launch the installed macOS desktop without the crashing native-window presentation path, and verify Safari through real guest input and screenshots.

## Configuration

- 8 vCPUs, QEMU TCG, USB keyboard/tablet input.
- user-mode NAT requested (`virtio-net-pci`); guest connectivity remained unverified.
- `native_window=false`, no explicit GPU/ICD, and no `exact_half_blend`; no FramebufferFetch fatal was observed on this path. Native-window failures were observed in takeover Runs73–75/77; Run76 was a no-native-window control.
- Interactive observation; host QMP quit used after evidence capture.

## Result

- Normal installed macOS boot completed and reached the graphical login screen.
- The existing project test account was selected and the password was entered through `guest_ui.py type-secret`; the secret value was never printed or included in the UI journal.
- The guest reached the logged-in desktop. Evidence: `ui-desktop-81b.png`.
- Safari was launched from the Dock. Evidence: `ui-safari-81b.png` shows the Safari menu bar, Safari window, address bar, and a live Safari tab.
- Safari accepted address-bar input and navigation. Evidence: `ui-iana-81.png` shows the resulting Safari window and the page-load failure UI.
- No new `fatal`, `FramebufferFetch`, `SIGABRT`, or watchdog marker was present in `qemu-stderr.log` during this no-native-window Safari run.
- The raw observer completed normally with QEMU exit code 0 and reaping. The final report is marked `state=failed`/`passed=false` only because interactive mode was intentionally terminated by host QMP quit after evidence capture; `errors=[]`.

## Interpretation

This closes the previous login blocker and proves that the no-native-window path can boot the installed system, accept authenticated GUI input, launch Safari, and keep the Safari window alive long enough for address-bar interaction. It does not establish that the native-window path is fixed: takeover Runs73–75/77 aborted in the Reims/metal2vulkan FramebufferFetch pipeline path on the older build.

The attempted `https://www.iana.org/` navigation displayed Safari's “Failed to open page” state. The run metadata records network connectivity as unknown, so this is evidence of a failed guest page load, not a diagnosis of DNS, DHCP, HTTPS, or Safari networking.

## Evidence

Raw artifacts: `runs/e05-installed-boot/m3-safari-gpu-takeover-20261102-81/`

Key screenshots:

- `ui-check-81d.png`: graphical login screen.
- `ui-login-selected-81b.png`: password field visible.
- `ui-desktop-81b.png`: logged-in desktop with Dock.
- `ui-safari-81b.png`: Safari window opened.
- `ui-iana-81.png`: Safari address-bar navigation result.

## Run82 follow-up and candidate build

Reviewed from existing artifacts on 2026-11-04; no new VM was launched for this review.

- **Run:** `m3-safari-gpu-takeover-20261102-82`, 2026-11-02 19:37:05–19:53:43 UTC, 998.393 seconds total.
- The same 8-vCPU/no-native-window/user-NAT profile proved another normal installed-OS boot. Manual review of `ui-after-login-82b.png` shows a logged-in desktop and Terminal. The visible Terminal output is restored content and is not new GPUProcess timing or network evidence; raw `gui_verified=false` remains unchanged.
- Collection failed: `passed=false`, `collection_success=false`, `errors=["ValueError: Owned QEMU process identity changed"]`. This is a failed collection sample, separate from the positive boot and screenshot evidence.
- QMP records host-origin `host-qmp-quit`; QEMU exited 0 and was reaped. No guest shutdown or filesystem-clean shutdown was proven. The recorded network state remains `guest_connectivity=unknown`.
- No FramebufferFetch fatal was found in this run's QEMU stderr. That no-native-window observation does not validate native presentation or WebKit stability.

During Run82, the existing metal2vulkan patch was extended to support BGRA8Unorm premultiplied source-over in the ordered storage form, with new storage-form and pipeline test cases. Saved reconstruction reports for metal2vulkan, Reims ARM and QEMU record success. The inspected run logs do not record execution results for the new BGRA8 test cases.

The main executable was relinked at 19:53:32 UTC (build-file metadata), while Run82 was still active:

| Artifact | SHA-256 / evidence |
|---|---|
| Binary used when Run82 launched | `21635e42cbf618d2fb0d44277a95b04f6ef2ed235a09d111500261286e44a89c` in Run82 `results.json` |
| Current candidate binary | `14ce3b403abfd311316abf7424fe8a6ffb76f881a4fd1eb46aa99335a6413704` in `runs/qemu-bdif-usb-build.json`; current file hash checked on 2026-11-04 |
| Current metal2vulkan patch | `5536b16ceefc113884eee614b7427becd544658bcaa55382f224de4be993adc3`; matches the build record |

The candidate therefore has build and source-reconstruction evidence, but no live guest result. Run82 cannot validate a binary built after it launched.

The concurrent relink is a plausible explanation for the identity error: `experiments/E05-ibec/kernel_boot_observation.py::_validate_session` compares both command-line bytes and `/proc/PID/exe` with the expected executable pathname. The error record does not identify which comparison failed, and no failure-time process-identity snapshot is retained, so the cause remains a hypothesis.

Run82 evidence: `runs/e05-installed-boot/m3-safari-gpu-takeover-20261102-82/{results.json,qmp-lifecycle.json,ui-after-login-82b.png,qemu-stderr.log}`. Build evidence: `runs/qemu-bdif-usb-build.{json,log}` and `runs/patch-reconstruction-{metal2vulkan-research,reims-arm-guest,qemu-bdif-usb}.json`.

## Later validation

On 2026-11-04, the two BGRA8 target tests executed successfully, and Run83 used the new binary to reach login and the desktop in the NVIDIA native window. See [Run83](M3-BGRA8-NATIVE-VALIDATION-RUN83.md) for current evidence and application/network follow-up; the Run81–82 observations above remain historical.

## Remaining work at Run82 closeout

1. Resolve the Run82 collection-identity uncertainty before relying on further observer results.
2. Record the BGRA8 candidate's targeted test results and validate native-window behavior on the new build.
3. Capture GPUProcess launch-to-ready timing and current-process samples while Safari opens, then compare with the Run69 WebKit watchdog window.
4. Validate guest DHCP/DNS/HTTPS independently of the Safari UI.
5. Keep FPS, input latency, full filesystem-clean shutdown, and broad Metal compatibility as unmeasured.
