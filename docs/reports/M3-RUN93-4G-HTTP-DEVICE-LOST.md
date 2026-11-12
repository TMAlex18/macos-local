# M3 Run93 — 4 GiB HTTP control stopped by Vulkan DeviceLost

Run `m3-memory-4g-http-20261105-93` used the promoted binary
`0efae78918564459ce8449f3e698271daba6861215c3e018b2a83b2034269d34` with the
Run89 profile: 4 GiB, 8 vCPUs, Intel native window, QARMA5, exact 24 MHz,
PAC memo, exact half blend and user-mode NAT.

The guest reached normal macOS and obtained `en1=10.0.2.15/24` with gateway
`10.0.2.2`; `r93pre.txt` records `hw.memsize=4284481536`. A manual Safari
navigation to the host-served 185-byte RGB fixture produced a native capture
with the expected red, green and blue bars (`r93-http-ui.png`). The first
automated `safari-probe` attempt is retained separately: `open -a Safari`
returned LaunchServices error `-600`, and its 896-byte watcher archive has no
page or GPU-process evidence.

After the Terminal Apple Events permission was granted, a later Safari reopen
sequence triggered Reims/Vulkan `DeviceLost`; QEMU exited `-6` without a guest
shutdown event. Host-only Intel/NVIDIA E02 render/readback passed afterward,
so this run is retained as a guest/Reims runtime boundary and is not evidence
for RAM or Safari causality.
