# M3 Run106 — same-binary audio-off network control

Run `m3-sound-off-network-control-20261106-106` used the same QEMU binary
`93f6e7ea1338de0bf3a748d866ae9dfa22f0c08974c228e0ba4f77d0b921db91`
and 4 GiB/eight-vCPU/Intel profile as Run105, with `--virtio-sound` omitted.
Normal installed macOS, login and the native desktop were verified.

## Automatic network readiness and absent audio inventory

The guest's `r106net.txt` records an automatic DHCP ACK for `10.0.2.15/24`,
default gateway `10.0.2.2`, and an active `en1`. Its actual HTTP PUT reached
the host file service. No manual IP-address change was performed.

In `r106audio.tgz`, both `ioreg` and `system_profiler` exited 0. The VirtIO
sound registry output and CoreAudio device `_items` list are empty. This is
a successful runtime audio-off inventory, rather than an inference from
the omitted command-line option.

The restored Safari window shows the authored RGB page in
`r106-terminal-ready.png`. No fresh navigation/launch test was performed;
the capture does not establish first-load stability. One audio-off success
after Run105's audio-on network failure is not an audio-causality result.

## Corrected observation path and the actual queue boundary

The initial `net-initial.json` reported an unknown PCI identity because the
collector compared the complete `id` dictionary and rejected valid subsystem
fields. `net-identity-diagnostic.json` confirmed the intended device remained
`1af4:1041`. This was an observation-path fault, not a changed guest NIC.

After the identity comparison was corrected, the actual consumer produced
`net-initial-fixed.json` with state `observed`:

| Network queue | Used index | In use |
|---|---:|---:|
| RX | 263 | 0 |
| TX | 272 | 0 |
| Control | 10 | 0 |

MSI-X was enabled, function/vector masks were clear, and PBA pending bits
were zero at observation time. The unchanged queue selector exposed queue 1
and vector 1; the control queue's vector remains unknown because the
collector does not write selectors. The latest completed control head had
been reused, so an ACK cannot be recovered from its current contents.

These bounded reads neither pause the VM nor write selectors, read ISR MMIO,
or use the queue-element command. They are not an atomic snapshot and do not
prove past interrupt delivery. The existing ten-case offline collector suite
and this real consumer result are retained separately; the tests were not
rerun for this report.

## Guest-origin shutdown

Apple-menu shutdown completed at 21:27:45 UTC. The owned QMP connection
recorded `guest=true`, `reason=guest-shutdown`; QEMU exited 0/reaped without
a host quit request, and the file service stopped and exited 0/reaped.
The collector remains false because final capture was unavailable after the
natural guest exit, with `errors=[]`. Filesystem cleanliness remains unproven.

No confirmed toggle of the dialog's reopen-windows checkbox was obtained
before exit. This run does not establish that restored windows were disabled.
Current controls and follow-up work are tracked in [STATUS](../STATUS.md).

Evidence is in
[`application-observation.json`](../../runs/e05-installed-boot/m3-sound-off-network-control-20261106-106/application-observation.json),
[`results.json`](../../runs/e05-installed-boot/m3-sound-off-network-control-20261106-106/results.json),
`guest-uploads/r106net.txt`, `guest-uploads/r106audio.tgz`,
`net-initial.json`, `net-identity-diagnostic.json`, `net-initial-fixed.json`,
the named native captures, and `qmp-lifecycle.json` in the same directory.
