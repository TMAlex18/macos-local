# M3 Run107 — audio/network coexistence and owner-separated watchdogs

Run `m3-virtio-sound-repeat-20261106-107` repeated the existing
`93f6e7ea…b921db91` binary and 4 GiB/eight-vCPU/Intel profile with standard
VirtIO sound enabled and the silent `none` backend. Normal installed macOS,
login and the native desktop were verified.

## Audio and automatic network readiness coexisted

`r107net.txt` records an automatic DHCP ACK for `10.0.2.15/24`, default
gateway `10.0.2.2`, and actual guest HTTP exchange without a manual address
change. `r107audio.tgz` records successful `ioreg`/`system_profiler` exits,
active AppleVirtIOSound/coreaudiod binding, and Apple Virtual Sound Device
as the default input, output and system output, with two input/output
channels at 48 kHz. This proves coexistence in this run; it does not explain
or invalidate Run105's intermittent network stall.

Both `net-boot.json` and `net-after-init.json` completed the read-only
capture. Initially queue 2 was selected, so its control vector 2 was known;
MSI-X was enabled and unmasked. The later snapshot recorded RX/TX/control
used indices 259/267/11. The snapshots establish observed configuration
and queue progress, not an atomic history of interrupt delivery.

An initial public Terminal command was only partially entered. After
clearing that line, explicitly focusing Terminal and adding a 0.2-second
gap after each key stroke, the full nonce echo and actual uploads succeeded.
Focus/restoration and timing changed together, so neither is isolated as
the cause of the earlier partial input.

## Fresh Safari still failed, with two different owners in the log

The `r107fresh` preparation confirmed Safari exit, then ran a fresh trial
without stack sampling. The retained stream contains:

| Requesting owner | GPU watchdog termination requests | Log lines |
|---|---:|---|
| Safari | 2 | 1514, 3445 |
| nsattributedstringagent | 7 | 636, 781, 876, 1838, 3001, 3319, 3871 |

These are **two Safari requests**, not nine. `r107-safari-result.png`
shows an error page rather than the authored RGB page. No default-audio-device
absence error lines occur in this retained stream. Device/default selection
therefore did not make this fresh trial succeed, but the remaining GPU
initialization cause is unproven. The older Run100/103/104 counts remain
their own Safari-only observations.

## Shutdown and subsequent PCM preparation

The reopen-windows checkbox was visibly unchecked in
`r107-reopen-unchecked.png`. Terminal/caffeinate then blocked shutdown;
the initial attempt was cancelled, the owned keepawake was terminated,
and a later attempt did not finish before the runner deadline. Checkbox
persistence or suppression of restored windows requires a subsequent boot;
neither is established here.

At 22:34:09 UTC the 3600-second runner ended by host QMP quit. QEMU and
the file service exited 0/reaped; collection succeeded with `errors=[]`.
Guest-origin shutdown and filesystem cleanliness remain unproven for this
run. The preceding verified guest-origin shutdown is Run106.

Separate preparation for WAV capture passed 32 runner offline cases and
3 paused/diskless device cases. In the WAV case, standard SET_PARAMS,
PREPARE and RELEASE produced a valid **44-byte header with zero data**;
this is not PCM output. A self-authored two-second, 48 kHz signed-16-bit
stereo reference (left 440 Hz, right 660 Hz, peak amplitude 0.2) was generated
and checked, including zero crossings. It is only reference input at this
boundary, not a playback result. These completed checks are reused, not
rerun for this report. Current execution is tracked in [STATUS](../STATUS.md).

Evidence: `application-observation.json`,
`results.json`,
the named snapshots/captures and `guest-uploads/r107fresh.tgz`,
`r107audio.tgz`, `r107net.txt` in the same directory. Separate preparation
records are `WAV device results`
and `tone metadata`.
