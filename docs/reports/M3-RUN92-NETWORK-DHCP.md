# M3 Run92 — delayed DHCP control

Run `m3-network-dhcp-20261105-92` used the same 4 GiB, eight-vCPU Intel
profile and the retained target inodes. It was a network diagnostic only; no
Safari fresh-load trial was started.

At the first Terminal check, `en1` was link-up but had no IPv4 address and
`route -n get default` returned no table. `sudo -n ipconfig set en1 DHCP`
reported that a password was required, so no privileged network change was
made. After waiting in the same guest session, `en1` acquired `10.0.2.15/24`
and the default route through `10.0.2.2`; the guest then fetched `probe.sh`
over host HTTP and uploaded `r92pre.txt`. The snapshot reports
`hw.memsize=4284481536`, and swap I/O was zero.

The guest was shut down through System Events. QMP recorded a guest-origin
shutdown, QEMU exited 0 and was reaped. This establishes that Run91's missing
route was a delayed DHCP observation in the reused 4 GiB session, not a
permanent host-forward failure. It does not provide Safari or RAM causality
evidence. The next active run can repeat the Run89 HTTP workload after waiting
for this route readiness condition.
