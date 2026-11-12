# M3 Run108 — complete guest-to-WAV reference PCM matches

Run `m3-virtio-pcm-capture-20261106-108` uses the existing
`93f6e7ea…b921db91` binary and 4 GiB/eight-vCPU/Intel profile with standard
VirtIO sound and the WAV backend. Normal installed macOS, login, the native
desktop, automatic DHCP/HTTP and active CoreAudio default output are verified.
The run completed with guest-origin shutdown. Current follow-up work is
tracked in [STATUS](../STATUS.md).

## Two real afplay transfers preserve every reference PCM byte

Both successful guest trials verified the authored input SHA-256 and returned
`afplay_exit=0`. Each finalized capture contains the complete, contiguous
**96,000-frame / 384,000-byte** interleaved reference, byte for byte: stereo
signed 16-bit PCM at 48 kHz, with a two-second 440 Hz left/660 Hz right tone.
No sample deletion, reordering, gain correction or header repair was used.

| Trial | Finalized evidence | Capture frames | Reference starts at frame | Leading / trailing zero frames |
|---|---|---:|---:|---:|
| Sample requested | `pcm-evidence-sampled/voice-2.wav` | 122,880 | 9,728 | 9,728 / 17,152 |
| Plain | `pcm-evidence-plain/voice-1.wav` | 141,312 | 5,120 | 5,120 / 40,192 |

All surrounding samples are zero. Both channels reach peak 6553, with rising
zero-crossing measurements approximately 440/660 Hz. Capture padding and
file durations are not measurements of physical playback latency.

The plain trial ran after sound tracing was restored to off, with no guest
stack sampling or periodic owner-stop sampler. In the sampled trial, `sample`
returned 0 but yielded zero effective thread/stack rows: **no stack evidence
was acquired**. The independent PCM comparison, not sample success, establishes
the output result.

The raw `guest-audio.wav` path can be reopened and truncated when output
voices change. The validation therefore uses preserved finalized voice
copies, with PID/start-tick/argv binding, recorded before/after file identity
and closed-source-descriptor observations in the manifests. Those checks are
not a writer lock; the immutable evidence hashes and full reference comparison
are retained separately.

## Failure and unrelated output remain separate

The initial plain `r108pcm` attempt timed out: collector 124, afplay 143.
The WAV source remained unchanged and no new voice was captured. A Terminal
microphone-access prompt became visible later and “Don't Allow” was selected
before the successful attempts. The initial cause remains unresolved: TCC
state, warm caches and sampling differed afterward. Neither the modal nor
the input-backend limitation below is established as that timeout's cause.

The earlier sampled-manifest `voice-1.wav` contains 16.096 seconds of output
whose source is unassigned. Its complete-reference test is false. It is not
the authored tone; the positive evidence is sampled `voice-2.wav` and plain
`voice-1.wav`. The shorter preserved startup output is also separate.

## Proven output boundary and remaining input gap

This proves two complete reference transfers through this guest-to-file
output path. It does not prove physical speaker output, real input/output
latency, continuous long-running audio, microphone capture or filesystem
cleanliness. CoreAudio advertises a default two-channel input, but
`qemu-stderr.log:545` reports that `virtio-sound.in` cannot open because
there is no host audio driver. WAV supplies no input voice: advertised input
must not be treated as functional capture.

The guest UI helper also fixed a first-send timeout that previously attributed
local preparation delay to a paused VM without a `VM not running` rejection.
The 33-case regression passed; later actual public commands, downloads and
playback interactions succeeded. The local delay source remains unresolved
among journal/fsync, binding verification and scheduling. A fresh desktop
without old Terminal/Safari windows was observed, but it cannot isolate
checkbox persistence from the apps already closed at Run107's end.

## Ordinary fresh Safari still failed after PCM output worked

The ordinary `r108fresh` trial confirmed Safari exit and used no guest stack
sampling or periodic owner-stop sampler. Its watcher exited 0 and retained
three Safari-owned GPU watchdog termination requests (GPU1691, GPU1835,
GPU1887), zero default-audio-device absence error lines, and a native error
page in `r108-safari-result.png`; no authored RGB success is claimed.

Nine bounded `ps` and two `pgrep` observations timed out, but the WebKit stream
and timeline were retained. Missing PID snapshots are not evidence of absent
processes. Working reference PCM output did not make this fresh Safari trial
succeed, and it does not identify the remaining initialization cause.

The voice-preservation tool independently passed three tests, including a
real writer closing/reopening/truncating the same path; the two actual guest
PCM comparisons above remain a separate validation scope. All three capture
monitors have exited.

## Lifecycle

At 23:49:08.746906 UTC the owned QMP connection recorded `SHUTDOWN` with
`guest=true`, `reason=guest-shutdown`; the runner finished at 23:49:09.427386.
QEMU exited 0/reaped with no host quit request, and the file service stopped
and exited 0/reaped. Their process disappearance was independently checked.
The collector remains false and its controller exits 1 because the final
screen capture was unavailable after natural guest exit; `errors=[]`.
Filesystem cleanliness remains unproven. The latest completed run and last
verified guest-origin shutdown are both Run108.

Authoritative evidence:
[`application-observation.json`](../../runs/e05-installed-boot/m3-virtio-pcm-capture-20261106-108/application-observation.json),
[`sampled validation`](../../runs/e05-installed-boot/m3-virtio-pcm-capture-20261106-108/pcm-sampled-validation.json),
[`plain validation`](../../runs/e05-installed-boot/m3-virtio-pcm-capture-20261106-108/pcm-plain-validation.json),
the corresponding `pcm-evidence*/manifest.json`,
`pcm-preplay-negative-control.json`, `pcm-inputs/startup-output.json`,
and `guest-uploads/r108pcm.tgz`, `r108pcms.tgz`, `r108pcmplain.tgz` in the
same run directory. Earlier WAV-header-only tests remain a separate boundary.
