# M3 Run109 — single-stream counterexample and stackshot controls

Run `m3-output-only-audio-spindump-20261106-109` used the unchanged
`93f6e7ea…b921db91` binary and 4 GiB/eight-vCPU/Intel profile, with the
single-output WAV configuration. Normal installed macOS, login, the native
desktop and actual guest HTTP exchange were verified.

## Paused protocol success did not establish Apple audio compatibility

`r109audio.tgz` shows `AppleVirtIOSound` active but **not registered or
matched**, no attached UserClient, and an empty CoreAudio device list.
QMP reports `FEATURES_OK`/`DRIVER_OK`, but only one completed control
descriptor and zero TX/RX completions. No WAV was created and no PCM trial
was attempted. This is one actual cold-boot counterexample to the proposed
single-stream configuration, not proof that every Apple configuration
necessarily requires two streams.

The production launcher was reverted to the previously guest-validated
two-stream layout, with metadata separating advertised output/input from
`input_backend_available=false`. None mode is unchanged and no binary or
new flag was introduced. Its 32 offline checks passed; the restored layout's
new paused preflight and next guest run remained separate pending work.
The code rollback did not change Run109's already-running single-stream argv.

`audio-control-current-bytes.json` contains only 80 bytes of bounded RAM
reads: a current indirect descriptor with JACK_INFO words `[1,0,0,24]`
and response `0x8002`. Completed buffers may already have been reused.
These current contents neither identify the registration failure's cause
nor establish a universal two-stream requirement.

## Native stackshots retain real stacks for stable and early-exit targets

The installed macOS 27.0.1/26A434 `/usr/share/man/man8/spindump.8` was read
before testing its documented interfaces. The first `r109spin` invocation
returned **64 before sampling**, because `-onlyTarget` plus
`-sampleWithoutTarget` is rejected with fewer than two targets. This is an
argument error, not evidence that stack collection failed on the target.

Corrected root-owned controls then produced final reports with
`Data Source: Stackshots` and actual user/kernel frames:

| Control | Actual sampling window, UTC | Retained target stacks | Result |
|---|---|---|---|
| Stable `r109spin2` | 00:23:12.812–00:23:15.810 on November 7 | sleep 1173: 30 samples, requested 3 s / 100 ms | exit 0, complete report |
| Early exit `r109early` | 00:27:20.275–00:27:30.276 on November 7 | long sleep 1378: 83; short sleep 1380: 57 | exit 0, complete report |

The short child was `/bin/sleep 6` and exited naturally with status 0.
Its report ends at 00:27:25.881 UTC, inside the ten-second sampling window;
all 57 recorded user/kernel stacks remain in the final report after exit.
The long target includes a **1941 ms gap with no samples**, so the requested
100 ms interval is not a guarantee of continuous coverage.

Collector start-to-finish wall times were about 85 and 76 seconds. These
include the three/ten-second sampling windows and symbol processing; they
are not the sampling duration or normal application performance. Both runs
emitted `spindump-stuck` processing warnings but ultimately exited 0 with
complete reports. This validates the control observation path and retention
of a known early-exit target. **No real Safari GPU initialization stack has
yet been obtained with this path.**

The planned opt-in GPU integration keeps Safari under the ordinary guest
user and uses a bounded privileged collector, establishing readiness before
launch.
Its real consumer validation remains separate from these controls. No
formal fresh Safari trial was performed in Run109; restored windows do not
count as one. Current integration and execution are tracked in
[STATUS](../STATUS.md).

## Independent evidence-preservation validation

The expanded WAV preservation tool passed five tests, including real writers
that create an initially absent output and close/reopen/truncate it, plus
path/type and finalized-header checks. This reuses completed validation;
Run109 started no WAV preservation monitor and supplies no PCM result.
Run108's two complete reference matches remain the positive audio baseline.


## Lifecycle

The QMP event timestamp is 2026-11-07 00:42:08.083231 UTC with
`guest=true`, `reason=guest-shutdown`; the runner finished at 00:42:08.985882.
QEMU exited 0/reaped without host quit, and the file service stopped and
exited 0/reaped. The retained target inodes matched and temporary links were
removed. The collector remains false and its controller exits 1 because
natural guest exit prevented the final screen capture; `errors=[]`.
Filesystem cleanliness remains unproven. No VM remains active; the latest
completed run and last verified guest-origin shutdown are both Run109.

Authoritative details are in
[`application-observation.json`](../../runs/e05-installed-boot/m3-output-only-audio-spindump-20261106-109/application-observation.json),
[`results.json`](../../runs/e05-installed-boot/m3-output-only-audio-spindump-20261106-109/results.json),
`audio-output-only-qmp.json`, `audio-control-current-bytes.json`, and
`guest-uploads/r109audio.tgz`, `r109caps.tgz`, `r109spin.tgz`,
`r109spin2.tgz`, `r109early.tgz` in the same directory. Report hashes,
target identity, sample counts and exact time ranges are retained in the
application observation record.
