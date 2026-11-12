# M3 Run91 — 4 GiB local-file fresh page control

Run `m3-memory-4g-20261105-91` reused the same target inodes with the explicit
unclean-reuse flag after Run90. It requested 4 GiB with the same eight-vCPU,
Intel native, QARMA5, exact-clock, PAC-memo and display settings. The observer
verified a 4 GiB memfd and normal OS/desktop evidence. The guest later shut
down through System Events; QMP recorded a guest-origin shutdown and QEMU exit
0/reaped.

The guest network was not usable in this session: `en1` was active but had no
IPv4 address and `route -n get default` returned no table. Host-served HTTP
scripts therefore could not be fetched, and no HTTP comparison is claimed.
To keep the page workload local and inspectable, the exact 185-byte authored
HTML was written to `/tmp/r91.html` through Terminal and opened with Safari as
`file:///tmp/r91.html`.

The fresh local-file Safari log recorded one WebProcess unresponsive event but
no `GPUProcessProxy::didBecomeUnresponsive` line. The main frame finished and
the timing record reported `firstMeaningfulPaint=3.876` seconds. The native
window capture `native-r91-local-final.png` shows the heading and exact red,
green and blue bars. This is a positive 4 GiB local-file observation with a
clean guest-origin shutdown; it is not an HTTP/network-equivalent replay of
Run89. The structured manual review is retained at
`runs/e05-installed-boot/m3-memory-4g-20261105-91/application-observation-manual.json`.
The next discriminating action is to restore the 4 GiB guest route and repeat
the HTTP workload before attributing any difference to RAM.
