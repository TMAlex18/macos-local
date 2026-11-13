# M3 Run97 — matched 8 GiB HTTP page control

Run `m3-memory-8g-http-20261105-97` used the same 8-vCPU Intel/native-window,
QARMA5, exact-clock, PAC-memo and user-NAT profile as the 4 GiB HTTP runs, with
fresh RAM requested at 8 GiB. The observer verified one owned 8 GiB memfd
(`8,589,934,592` bytes).

Safari displayed the same host-served authored RGB fixture. The periodic QMP
capture `screen-0017.ppm` shows the heading and all three color bars; exact
primary-color counts are red 78,517, green 78,480 and blue 78,480, matching
Runs 94–96. Native screenshot control was unavailable under the busy guest,
and no watcher archive was received, so this is a page/pixel control rather
than a WebKit timing result.

The bounded runner reached its 1800-second limit and performed host-QMP
teardown, QEMU exited 0/reaped, and no guest-origin shutdown was observed.
The matched 8 GiB/4 GiB page observations strengthen the functional control,
but do not establish RAM causality, first-load stability, or application
speedup.
