# M3 Run94 — 4 GiB HTTP page control

Run `m3-memory-4g-http-20261105-94` reused the Run93 target with the same
binary and profile: 4 GiB, 8 vCPUs, Intel native window, QARMA5, exact 24 MHz,
PAC memo, exact half blend and user-mode NAT. The observer verified normal
macOS and the guest saw `hw.memsize=4284481536`.

The guest network was ready when queried: `en1=10.0.2.15/24`, default gateway
`10.0.2.2`, DHCP ACK, and host loopback file exchange all succeeded. The
pre-page snapshot is `guest-uploads/r94pre.txt`; the network record is
`guest-uploads/r94net.txt`.

Safari was launched into a new blank window and navigated through the address
bar to `http://10.0.2.2:18769/get/render.html`. Native capture
`r94-http4.png` shows the authored heading and all three RGB bars. Exact
primary-color counts in the 1920×1080 client capture are red 78,517, green
78,480 and blue 78,480. This is a positive 4 GiB HTTP page observation; no
WebKit watcher was attached, so it does not establish GPU watchdog timing or
first-load stability.

The guest was then shut down through System Events. QMP recorded
`SHUTDOWN guest=true/reason=guest-shutdown`, QEMU exited 0 and was reaped.
The runner's raw collection remains failed because the final post-exit capture
was unavailable; full filesystem cleanliness is not claimed. Together with
Run89 this supplies one positive HTTP observation at each capacity, but it is
still insufficient to attribute the result to RAM or to claim application
speedup.
