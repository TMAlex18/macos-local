# M3 Run95 — repeated 4 GiB HTTP page control

Run `m3-memory-4g-http-20261105-95` used the same promoted binary and profile
as Run94: 4 GiB, 8 vCPUs, Intel native window, QARMA5, exact 24 MHz, PAC memo,
exact half blend and user-mode NAT.

The guest reached normal macOS and the retained session had the expected
10.0.2.15/10.0.2.2 network setup. A new Safari window navigated to the same
host-served 185-byte RGB fixture. Native capture `r95-http.png` shows the
authored heading and all three bars. Exact primary-color counts are red
78,517, green 78,480 and blue 78,480, matching Run94's capture.

The bounded runner reached its 1800-second limit, QMP requested host teardown,
and QEMU exited 0/reaped. Collection and final capture succeeded, but no
guest-origin shutdown was observed and the watcher archive did not arrive
before teardown. This is a repeated 4 GiB HTTP page positive, not a watchdog
timing result, clean filesystem result, RAM causality proof or application
speedup measurement.
