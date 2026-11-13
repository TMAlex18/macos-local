# M3 Run105 — VirtIO sound binding and network boundary

Run `m3-virtio-sound-20261106-105` used QEMU binary
`93f6e7ea1338de0bf3a748d866ae9dfa22f0c08974c228e0ba4f77d0b921db91`
with the existing 4 GiB, eight-vCPU, Intel native-window profile. The new
`--virtio-sound` option remains off by default; this run enabled the standard
`virtio-sound-pci` device at PCI slot 6 with a silent `none` backend. Normal
installed macOS, login and the native desktop were verified.

## Actual driver and CoreAudio integration

`r105-ioreg-local.png` shows `AppleVirtIOSound` registered, matched and active,
with a user client owned by `coreaudiod` PID 283. `r105-coreaudio-local.png`
shows **Apple Virtual Sound Device** as the default input, output and system
output, with two input channels, two output channels and a 48 kHz sample rate.

The independent read-only `audio-qmp-state.json` records `FEATURES_OK` and
`DRIVER_OK`, `broken=false`, and these completed queue indices:

| Queue | Used index | In use |
|---|---:|---:|
| Control | 13 | 0 |
| TX | 10 | 0 |
| RX | 12 | 0 |

This proves guest binding, CoreAudio default-device selection and descriptor
completion for the standard device. It does **not** verify PCM waveform
fidelity, a continuous audio stream or audible playback; the backend is `none`.

## Network configuration prevented the Safari comparison

The guest's `en1` was active but had no IPv4 address or default route. The
owned host file service was live and an actual host GET returned 200, yet no
guest script download or upload was recorded. `sudo ipconfig set en1 DHCP`
and the later static-address command had no visibly verified return or
configuration result. They are recorded as requests, not successful changes.
There is **no fresh Safari comparison result** from this run.

`network-qmp-state.json`, `network-control-ring-reads.json` and
`network-control-descriptor.json` establish a completed network control
descriptor: class 0, command 1, ACK 0, control used index 1/in-use 0. Network
TX used index remained 0. Those observations do not prove that MSI-X reached
the guest, identify the completion-notification failure, or establish any
causal relationship to the newly enabled audio device.

The UI helper's newly allowed Ctrl+C chord passed its existing 31-case
offline regression. The live post-command screenshot was black, so the
foreground command's interruption is unverified. Current controls and the
next discriminating network observation are tracked in [STATUS](../STATUS.md).

## Validation and lifecycle boundaries

The pre-run validation is kept as separate evidence scopes: **28 runner
offline cases**, and **2 real paused/diskless device cases** for sound off/on.
The latter checked the standard PCI/VirtIO identity, capability ordering,
four queues, `none` backend and cleanup without executing an Apple guest.
The 27-file patch reconstruction is a separate result. These completed
checks were reused, not rerun for this report; Run105 supplies the actual
Apple driver/CoreAudio integration evidence.

At 20:32:50 UTC the runner's 3600-second window ended with host QMP quit.
QEMU exited 0 and was reaped; the file service stopped and exited 0/reaped.
`collection_success=true` and `errors=[]`. The QMP shutdown event explicitly
has `guest=false`, `reason=host-qmp-quit`. **Guest-origin shutdown and full
filesystem cleanliness are not established by Run105.** Run105's most recent preceding verified guest-origin shutdown was Run104.

Authoritative details are in
[`application-observation.json`](../../runs/e05-installed-boot/m3-virtio-sound-20261106-105/application-observation.json),
[`results.json`](../../runs/e05-installed-boot/m3-virtio-sound-20261106-105/results.json),
the named screenshots and QMP/descriptor JSON files in the same directory.
The paused-device evidence is
[`results.json`](../../runs/e05-virtio-sound/sound-opt-in-20261106/results.json).
