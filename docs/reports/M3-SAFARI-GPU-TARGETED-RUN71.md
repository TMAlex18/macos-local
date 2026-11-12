# M3 Safari GPU targeted follow-up — Run71

**Run:** `m3-safari-gpu-targeted-qarma5-20261101-71`
**Window:** 2026-11-01 21:39:27–21:59:32 UTC
**Purpose:** start the next targeted GUI run after Run70 with 8 vCPU, NVIDIA native window, user-NAT, exact `half_blend`, and read-only current-process sampling.

## Result

- Fresh QEMU/8-vCPU normal-OS boot completed and the bounded 1200-second observation passed.
- 40 read-only current-process samples and 21 screenshots were collected; the NVIDIA/native-window/user-NAT configuration was recorded.
- QEMU exited 0 and was reaped via host QMP quit. No guest shutdown event was observed, so filesystem-clean shutdown remains unproven.
- `gui_verified=false`: no semantic GUI review was completed.
- The run did not reach a logged-in user session or trigger Safari. The targeted Safari diagnosis therefore remains pending.

## Blocker

The next required step is login with the existing project test account. Reading `private/initial-setup.json` was blocked by the security gate pending explicit, current authorization to use those credentials for Run71. No credential was output or transmitted.

## Evidence and limits

Raw artifacts are under `runs/e05-installed-boot/m3-safari-gpu-targeted-qarma5-20261101-71/`. This run proves another bounded normal-OS boot and stable collection under the intended 8-vCPU/NVIDIA profile; it does not prove Safari stability, GPUProcess timing, networking, FPS, or clean shutdown.