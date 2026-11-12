# M3 Run110 — GPU initialization wait captured, PCM discontinuity, host reboot

Run `m3-restored-audio-gpu-stackshot-20261107-110` used the unchanged
`93f6e7ea…b921db91` binary and 4 GiB/eight-vCPU/Intel profile with the
restored two-stream WAV configuration. Normal installed macOS, login,
AppleVirtIOSound/coreaudiod binding, CoreAudio defaults and guest HTTP were
verified. The host later rebooted before the controller finalized its run.

## The actual initial GPU was captured across its watchdog window

The first `r110stack` attempt returned watcher 12 before opening Safari:
the readiness predicate did not accept the available stream-header output.
This was an observation-path failure, not a new Safari trial.

`r110canary.tgz` then verified a unique event emitted through the real unified
log subscription, FIFO and parser. In `r110stack2`, root watcher readiness
was recorded at 01:40:45 UTC, before the ordinary user (UID501) opened Safari.
The preparation records `SAFARI_EXIT_CONFIRMED`. Readiness verification did
not itself change or fix Safari behavior.

`r110stack2.tgz` contains a real Stackshots report for the first Safari-owned
GPU1941: **99 target samples over the 9.99-second report window**, with
actual initialization frames and user/kernel waits. The report records one
suspended sample and one terminated/zombie sample. Relevant UTC times on
November 7 are:

| Event | Time |
|---|---|
| Safari ownership assertion | 01:41:58.878 |
| Actual report window | 01:42:02.339–01:42:12.333 |
| GPU connection initialization | 01:42:08.338 |
| AudioComponentRegistrar connection | 01:42:10.644 |
| SandboxHelper connection creation | 01:42:10.934 |
| Safari watchdog termination request for GPU1941 | 01:42:11.338 |
| GPU1941 report end | 01:42:12.149 |

The connection-to-watchdog interval is exactly three seconds in the retained
log. The main thread progresses through `enableMediaPlaybackIfNecessary`,
CoreAudio setup and component registration. Samples 87–98 show synchronous
reply waiting through `ACSandboxHelper::Impl::sendMessageToSandboxHelper`.
The kernel waits name port `com.apple.audio.SandboxHelper`, but the receiving
tasks are **UNKNOWN TASK**, with thread IDs `0x7986`, `0x7c7b`, `0x7c7e`.
This establishes the client's observed wait, not the server's work, a permanent
deadlock or the root cause. System-wide GPU/server capture is the next
discriminating observation.

Native spindump eventually returned 0 and saved the report, but its outer
deadline fired: `outer_timeout=true`, watcher exit 124. Owned children were
reaped and the archive returned. Valid target stacks and an exceeded wrapper
deadline are separate results. The same stream contains two Safari watchdog
requests, for GPU1941 and GPU2106.

At 01:44:29.957500 UTC the PID-bound native window displayed the authored
RGB page. The three CSS-color interior ROIs contain **173,400 exact pixels**.
This is later rendering after the diagnostic trial; it does not prove the
first GPU succeeded, ordinary first-load stability, physical scanout or
general Metal fidelity.

## Restored audio binds, but this PCM repeat is not bit-exact

The restored two-stream device registered and supplied CoreAudio defaults.
Plain `afplay` PID840 exited 0, yet the complete-reference comparison failed.
`pcm-content-analysis.json` exactly reconstructs the captured bytes:

- Reference frames `[0,11776)` and `[12540,96000)` remain byte-exact.
- The missing 764 reference frames `[11776,12540)` are replaced by a
  512-frame block: 454 zero frames plus an exact 58-frame repetition of
  reference `[2,60)`.
- The resulting tone extent is 252 frames shorter; outer frames are silence.

This is not merely inserted silence or a trimmed tail. The identified
retained blocks do not support a gain, quantization or channel-swap change.
The responsible guest/CoreAudio/VirtIO/QEMU mixer/WAV layer remains unknown.
Run108's two full-reference matches remain valid historical controls, while
Run110 demonstrates that repeatable exact fidelity is not established.

## Host interruption and honest recovery

The previous host boot's journal ends at 02:04:20 UTC; a new boot starts at
02:08:14. The user reported that the restart was not intentional. By 02:22
the Run110 service was inactive and controller/QEMU PIDs79750/79764 were
absent, while `results.json` still said `observing`. No final exit/reap or
guest-shutdown result was saved. The current boot's firmware BERT record
has fatal severity, 11 sections and 42,716 bytes; the physical component,
trigger and any causal link to QEMU remain unresolved. Raw firmware payload
stays private.

Schema-2 reboot-evidence recovery completed at 02:28:04.898265 UTC after
checking boot/service provenance, absent recorded processes, both locked
retained inodes and owned temporary aliases/sockets. It preserves unknown
exit code, `qemu_reaped=false` and explicit unclean reuse. Recovery is not
guest shutdown or filesystem-clean evidence. The raw collector bytes were
not edited. The latest finalized run and last verified guest-origin shutdown
remain Run109; Run110 is the latest interrupted diagnostic result.

Evidence: [`application-observation.json`](../../runs/e05-installed-boot/m3-restored-audio-gpu-stackshot-20261107-110/application-observation.json),
`guest-uploads/r110stack2.tgz` (report lines 77–139 and 576–590; WebKit stream
lines 669/1026), `r110canary.tgz`,
[`RGB validation`](../../runs/e05-installed-boot/m3-restored-audio-gpu-stackshot-20261107-110/r110-stack2-rgb-validation.json),
[`PCM analysis`](../../runs/e05-installed-boot/m3-restored-audio-gpu-stackshot-20261107-110/pcm-content-analysis.json),
[`host-reboot evidence`](../../runs/e05-installed-boot/m3-restored-audio-gpu-stackshot-20261107-110/host-reboot-evidence.json)
and [`retained-storage.json`](../../runs/e05-installed-boot/m3-restored-audio-gpu-stackshot-20261107-110/retained-storage.json).
Current execution is tracked in [STATUS](../STATUS.md).
