# E05 — Installed-target cold-boot observation

This entry starts a fresh VM from the two retained targets of a
**completed native installation**. The source must have successful native
client exit and verified `Status: Restore Finished` log evidence, followed by
normal QMP teardown, reaping and matching retained ROOT/AUX inode metadata.

## Current Run128 profile

The current completed reference is
[`m3-webgl2-readback-netctrl512-20261108-128`](../../runs/e05-installed-boot/m3-webgl2-readback-netctrl512-20261108-128/results.json).
It used QEMU SHA-256
`4a433616dc04e90ba41d720fe0dfb8925294799eff94a6eef24b52108a6b4d66`
and the enabled **30-second diagnostic**, not the ordinary 3-second deadlines.
Its raw WebGL oracle result
passes both fixed readbacks. Its timeout/application analysis
observes site0 but not site1, and keeps `original_deadline_performance_pass=false`.
These are bounded results for one fixture, not general WebGL/Metal or performance certification.

Run128 is closed. The following recipe is a future run template after maintenance,
with a new label and new upload tags; it does not rerun or overwrite Run128.
All host commands below are issued from the repository root.

Before launching:

1. Complete the [top-level setup/build workflow](../../README.md) and retain the
   private 26A434 inputs plus a verified completed-install source. Source
   `m2-native-install-observed-finish-qarma5-20261030-53` is the provenance used by
   Run128. Its ROOT/AUX aliases contain later writes; identical flags do not
   restore identical disk, login or cache history.
2. Inspect [`runs/qemu-bdif-usb-build.json`](../../runs/qemu-bdif-usb-build.json).
   The selected binary and current patch hashes must agree. The current command
   needs memoization/statistics/capacity (`0022`/`0024`), PCM/control tracing (`0023`/`0027`),
   sound queue capacity (`0028`), and net control queue capacity (`0032`). The
   diagnostic additionally needs `0030` and `0031`. The reference binary also
   contains `0029` and retains its fixed 16,384-entry memo arrays while active
   capacity is 1,024. Rebuild through the existing build chain when required;
   do not replace an executable owned by a live VM.
3. For the diagnostic, build the self-authored metadata probe **before** the file
   service/VM starts, or reuse outputs matching
   [`build/macos-shared-cache-identity/build.json`](../../build/macos-shared-cache-identity/build.json):

   ```sh
   make build-shared-cache-identity
   ```

   This calls [`tools/build-macos-shared-cache-identity.sh`](../../tools/build-macos-shared-cache-identity.sh)
   and creates the served binary/checksum links. The service checks their pinned
   identity and source/build record; it generates `shared-cache-identity.sh` in
   memory only for the requested diagnostic service. A missing/mismatched probe
   is a preflight failure, not a reason to substitute an old runtime address.

Choose the deadline profile explicitly:

| Profile | Startup selection | Guest/host action | Interpretation |
| --- | --- | --- | --- |
| Ordinary original-deadline control | Omit `--webkit-timeout-diagnostic` | Skip identity/arming/counter steps; use the same workload/collection route | Original 3-second behavior. Run128 does not establish that this profile passes. |
| Run128 diagnostic configuration | Include `--webkit-timeout-diagnostic` as below | Metadata-only probe, then paused per-vCPU boolean arming before the workload | Fixed guarded 30-second sites; always diagnostic-only. |

These are the Run128 runner arguments/device profile, with only the output label replaced:

```sh
python3 -B experiments/E05-installed-boot/run.py \
  --source-run m2-native-install-observed-finish-qarma5-20261030-53 \
  --label NEW_DIAGNOSTIC_LABEL --interactive --vcpus 8 --ram-gib 4 \
  --pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats \
  --usb-input --native-window --gpu nvidia \
  --exact-half-blend --display-refresh-hz 30 \
  --user-network --net-control-queue-size 512 --guest-files \
  --virtio-sound --sound-backend wav --sound-queue-size 512 --sound-streams 1 \
  --sound-pcm-trace --kernel-observation boot-only \
  --webkit-timeout-diagnostic
```

Use another unused label and omit the last flag for an ordinary control. Run128
did not request vCPU affinity, current-process sampling, or unclean-storage
reuse. Its audio flags configure evidence collection; this WebGL recipe adds
no authored-tone or codec-lookup trial. The offered 30 Hz refresh is not measured FPS.

Keep the interactive controller in its owning terminal/service. After login,
follow [probe/boolean arming](#fixed-webkit-timeout-diagnostic-default-off), then
[the fixed WebGL workload and oracle](#fixed-webgl2-readback), then
[collection and shutdown](#collection-and-shutdown). The runner's host GET/PUT
readiness does not establish guest network access. Boot-only RAM observation
stops only after the recorded normal-boot proof; it does not monitor subsequent
kernel state, while QMP lifecycle reporting continues.

For code changes, `make installed-offline-tests` runs 14 selected offline
suites, not every repository suite. Budget several minutes: the
recorded maintenance run
passed 257 methods without skips in about 327 seconds, mostly the bounded
watcher tests. It starts no QEMU, VM, Chrome or external-network workload;
it does use loopback, short owned subprocesses and clang/UBSan probe fixtures.
Private sound cases may skip when their saved inputs are absent. This is not
macOS runtime validation. `make webgl-fixture-host-check` is a
**separate explicit Chrome/SwiftShader run**, not part of that offline target
and not a required repeat of the completed Run128 oracle.

## Defaults and other controls

These minimal launch examples are alternatives for the original default
profile, not the current Run128 recipe:

```sh
python3 experiments/E05-installed-boot/run.py \
  --source-run COMPLETED_NATIVE_INSTALL_RUN --label installed-cold-boot --seconds 600
python3 experiments/E05-installed-boot/run.py \
  --source-run COMPLETED_NATIVE_INSTALL_RUN --label installed-presentation \
  --seconds 1800 --usb-input --native-window --gpu nvidia
```

The default profile is 4 GiB, two vCPUs, QARMA5, multi-threaded TCG, exact
24 MHz counter, PAC/CTRR models, UUID 1 and the existing ROOT/AUX dual
frontends. `FirmwareSession` creates new RAM and holds the existing storage
flocks. Only ROOT/AUX target inodes persist; their new retained links are
aliases, not snapshots. Later guest writes also change earlier aliases.
`--vcpus 4` or `--vcpus 8` explicitly selects additional CPUs; only 2, 4 or 8
are accepted, with 2 still the default. The completed
native-install source remains the verified two-CPU profile. Current CPU indices
and MTTCG host-thread counts are independently verified before initial continue.

`--vcpu-affinity 0-7` optionally restricts only the owned TCG vCPU host threads
to explicitly selected logical CPUs. Lists and ranges such as `0,2,4-7` are
accepted only when every CPU is in the controller's current allowed set. The
runner does not infer core types. After the initial QMP thread check and before
the first `cont`, it verifies thread ownership, applies the masks, reads them
back, and checks that other QEMU and controller thread masks stayed unchanged.
Any failure aborts startup; it does not silently continue without the requested
setting. Omitting the option performs no affinity reads or writes and retains
the existing scheduler behavior. Execution metadata records requested and
actual masks with a startup-only verification scope; it does not measure later
CPU placement, CPU time, or performance. QEMU arguments and binary are unchanged.
Threads created later may inherit their creator's affinity; their masks are
outside this startup snapshot.

`--ram-gib 8` explicitly selects an 8 GiB fresh-RAM diagnostic profile;
omitting it retains 4 GiB. Only 4 and 8 are accepted. The completed native-install
source remains the verified 4 GiB profile, independently of the current request.
The current `-m` value and memfd backend must both match the selected capacity.
Execution metadata records the requested bytes; the boot observer separately
checks the owned RAM memfd's actual size and uses it for boot-argument mappings.
Its scan/read budget remains the first 256 MiB. A matching capacity request and
memfd do not establish guest-visible RAM or application performance; those need
an actual boot and guest observations.

`--pauth-memo` explicitly selects QEMU's default-off `x-pauth-memo` experiment
on a compatible build. It retains QARMA5, the 24 MHz clock and the same platform
profile. The current argv must match the requested setting, which is recorded
separately from the original installation source. Use omission/addition of this
option on the same binary for application comparisons; configuration and
microbenchmark gains are not OS/GUI performance results.

On a build containing matching patch `0024`, `--pauth-memo-entries 64|1024`
selects an explicit capacity while `--pauth-memo-stats` enables aggregate
per-vCPU hit/miss observations. Both require `--pauth-memo`; omission retains
the original 64-entry argument behavior and disables statistics. The runner
checks the new patch's recorded build hash before creating targets. Statistics
use read-only QOM requests at the existing 15-second observer cadence, retain
first/latest samples in `results.json` and each observation in
`pauth-memo-statistics.jsonl`. Counters reset with CPU reset/post-load and
individual reads are not atomic pairs. Use the same binary and instrumentation
setting for capacity comparisons; no guest clock, watchdog or PAuth semantics
are changed.

`--pauth-memo-entries 16384` additionally requires matching built patch `0029`.
The existing 64/1024 preflight still accepts a matching 0024 build. Default
capacity remains 64 and the full data/modifier/key/algorithm tags and hash are
unchanged. The 0029 binary reserves 16,384 entries for every vCPU even when
memoization is off or a smaller active capacity is selected: 48 bytes per entry,
768 KiB per vCPU, versus the previous 48 KiB allocation. Eight vCPUs therefore
reserve 6 MiB for these arrays. CPU reset and migration post-load clear the
entire array, so its clear cost is 768 KiB of writes per vCPU regardless of the
active capacity; no reset-duration claim is made. Use 1024 and 16384 on the same
binary for attribution. More capacity is an experiment, not a measured OS/GUI
speedup or watchdog fix.

`--interactive` is mutually exclusive with `--seconds` and has no automatic
deadline. Guest exit, native-window close or controller SIGINT/SIGTERM still
uses the existing cleanup. It remains a diagnostic session, not an automatic
account-setup workflow. User interruption records incomplete collection rather
than GUI success; screenshot retention remains capped at 16 files. Without this
option, the existing 600-second default and 1..3600-second range remain.
The source selects installation-completion provenance, not frozen disk bytes.
Current inode metadata and matching later cold-boot retained manifests are
recorded, so a later boot may include writes made by preceding cold boots.
Before linking the targets, the session checks the newest owned retention
record for both exact inodes across restore and cold-boot runs. An older
successful installation record cannot conceal a later aborted boot.
`--allow-unclean-storage` explicitly permits reuse after that recorded abort;
the actual exit, source manifest and reuse decision are preserved. Active or
unreaped targets are still rejected unless the explicit abandoned-owner recovery
below has established a dead session. This option does not claim a clean guest
shutdown or reset any target bytes.

If a controller vanished before saving cleanup, `recover-abandoned.py --run
LABEL` can reconcile an installed run with retained QEMU SIGTERM/controller
evidence. It requires all recorded process PIDs to be absent, both target
flocks, matching retained inodes and the exact saved temporary aliases. It
removes only those aliases and their otherwise empty directory, then records
`abandoned_recovery` in the same retention manifest. The original results stay
unchanged; exit code remains unknown and `qemu_reaped` remains false. The next
run still requires `--allow-unclean-storage`, and rechecks the bound evidence,
PID absence, removed directory and target locks before linking the storage.

```sh
python3 experiments/E05-installed-boot/recover-abandoned.py --run LABEL
```

The owned QMP connection persists a bounded whitelist of lifecycle events in
`qmp-lifecycle.json`. If the latest shutdown/reset event reports guest shutdown
while the owned process is still exiting and no host quit has been requested,
cleanup first waits up to five seconds for natural exit. This covers QMP
disconnecting before process exit; the wait outcome is recorded separately.
QEMU can also close QMP completely before its process exits, so that the next
periodic request fails on send with the guest's `SHUTDOWN` event still unread.
The session then reads, without sending, what QEMU had sent before closing and
records it as `send_failure_drain`; a guest shutdown found there reaches the
same bounded wait. The observation loop ends such a run as
`qemu_process_exited` and names the transport error in
`qmp_closed_before_process_exit`; without a guest shutdown event the error
still fails the run. Runs 133, 135 and 137 predate this and keep their
`terminate` records, which is why the run after each needed
`--allow-unclean-storage`.
If the wait expires, ordinary host cleanup still applies. A reset, host-origin
event or disconnected socket alone does not qualify for that wait.
Once QEMU is reaped, cleanup reads buffered tail events
before closing that connection, with a 250 ms / 64 KiB bound and no commands.
`guest_shutdown_observed` requires a PID-bound `SHUTDOWN` event with
`guest: true`, `reason: guest-shutdown`, and natural exit 0 without a host quit
request. It does not prove full filesystem cleanliness. Latest retained-target
reuse accepts that evidence or the existing normal `qmp_quit` baseline; an exit
code alone still requires the explicit unclean-state override. Final screenshot
and collection-success conditions remain unchanged, including an unavailable
final capture after natural exit. Historical runs without recorded events are
not retroactively classified from exit codes or guest log messages.

Offline parser/reuse checks are in `E05-usbip-kernel/test-qmp-lifecycle.py` and
`test-storage-retention.py`. `run-qmp-lifecycle.py` exercises the same owner
cleanup using diskless generic ARM `virt`: self-authored PSCI SYSTEM_OFF versus
host QMP quit, plus concurrent queries across guest shutdown and QMP disconnect,
without Apple code, retained disks, GPU or networking.

`--usb-input` adds VM-local xHCI keyboard/tablet devices at PCI slot 4.
`--native-window` requests the Reims presentation window. `--gpu nvidia` or
`--gpu intel` selects a single local hardware ICD through `FirmwareSession`.
The old source need not contain these new presentation devices; source and
current profiles remain distinct. Requested settings and GPU inventory do not
prove a working GUI or successful user input.

`--display-refresh-hz 30` is an explicit diagnostic workload setting for a
native window. Without an override, Reims offers the host screen's highest
refresh rate. The requested rate and effective `REIMS_VGPU_WINDOW_REFRESH`
value are recorded; neither is a measured FPS or latency result. Lowering the
rate for functional diagnosis does not satisfy the project's performance SLOs.

`--exact-half-blend` explicitly adds `half_blend` to the runtime's
`REIMS_VGPU_EXACT` policies. Other inherited policies are preserved and the
effective value is recorded in execution/presentation metadata. This selects
the existing half-precision rounding model for supported safe-math half4
blends; it does not disable strict refusals or certify general Metal precision.

`--user-network` explicitly enables QEMU user-mode NAT with one standard
modern `virtio-net-pci` at PCI slot 5. There are no host forwards or physical
bridges. The default argv remains `-nic none`; networking is never enabled by
the GUI helper or by implicit setup retries. The device profile must be rebuilt
with `CONFIG_VIRTIO_NET=y` before using this option.
The VMApple network profile also explicitly sets
`x-ascending-capabilities=on` from QEMU patch 0019. This default-off generic
property only relinks existing conventional PCI capability head/next bytes,
matching the previously established VMApple block/USB ordering convention.
Capability positions, payloads, BARs, MSI-X and feature negotiation remain
unchanged; unrelated machines/devices retain their default ordering.

`--net-control-queue-size 64|512` optionally sets only the network control
queue's `ctrl_queue_size` property. It requires `--user-network` and a build
manifest matching patch `0032-virtio-net-control-queue-size.patch`. Omission
adds no property and preserves the original 64-entry control default and argv.
RX/TX queue settings and feature bits retain their existing values. Metadata
separates the requested value, default/configured capacity and recorded device
argv; guest-selected size, ring layout, ACK consumption and connectivity remain
unverified until the actual guest run.

This uses the existing transport contract: the pinned original kernel's
`AppleVirtIOPCITransport` matches vendor `1af4`, and `AppleVirtIONetwork`
matches VirtIO device type 1 (`IOVirtIOPrimaryMatch=0x00011af4`) from that
transport. VMApple's existing NIC factory also names `virtio-net-pci`; no
custom Apple network device is introduced. Execution/profile metadata records
the requested backend, slot and absence of forwards. Those configuration fields
alone do not verify guest driver binding, DHCP, DNS or connectivity; use the
separate evidence from the relevant OS run.
The bounded device-only check is
`python3 experiments/E05-usbip-kernel/run-user-network.py --label UNIQUE_LABEL`.
It exercises no-NIC plus default/off/on capability-order controls on a paused
generic ARM `virt` machine, checks modern PCI ID `1af4:1041` at slot 5, compares
all 256 configuration bytes and feature state, and checks normal cleanup.
It runs no Apple firmware, GPU device or guest packet workload.

`--virtio-sound` explicitly adds the standard `virtio-sound-pci` device at
PCI slot 6 with `x-ascending-capabilities=on` and its own
`-audiodev none,id=guestsound` by default. Omitting the flag adds neither a
sound device nor an audio backend. The `none` backend accesses no host
microphone or speaker; it is a device-binding/CoreAudio diagnostic, not
audible playback.
The runner checks the exact current device/backend arguments against the
request, records `virtio_sound_requested` and `execution.audio`, and keeps
the original installation source's no-audio profile separate.

`--virtio-sound --sound-backend wav` selects file capture instead. Its output
is fixed to `RUN_DIR/guest-audio.wav`, with a 48 kHz, two-channel, signed
16-bit WAV format; there is no arbitrary output-path option. The runner
rejects `wav` without `--virtio-sound` and refuses an existing output file or
symlink before launching QEMU. `sound_backend_requested` and the requested
`execution.audio.output_file`/`capture_format` describe configuration only.
Omitting `--sound-streams` retains the existing two-stream device arguments.
Optional `--sound-streams 1` explicitly advertises one output and zero input
streams; `--sound-streams 2` explicitly retains one of each. Both require
`--virtio-sound`. The prior automatic `streams=1` change was withdrawn after
Run109 failed to register; that trial preceded the used-ring mismatch diagnosis
and does not establish a two-stream requirement. Queue size is independent:
the controlled output-only retest also selects `--sound-queue-size 512`.
Metadata records configured `advertised_streams`/output/input counts and
`streams.requested`, with `streams.runtime_verified=false` until separate live
evidence is collected. WAV's `input_backend_available=false` remains distinct
from enumeration; no physical input support is claimed. These use the standard
built-in `streams` property and existing build contract, without a new QEMU
patch or binary rebuild. Guest registration and PCM/Safari outcomes are recorded
by the relevant consumer observations, not inferred from this configuration.
After QEMU is reaped, `audio_capture` separately records header validity,
file/data byte counts and format. A missing file can mean that no output
voice was opened; an empty RIFF header or `data_present=true` does not prove
correct guest PCM or audible playback. Both backends avoid host speaker and
microphone access. Waveform validation belongs to the actual guest playback
experiment.

The WAV backend can reopen this path with `wb` when an output voice is
released and prepared again. Arm `tools/freeze-audio-voices.py` before a
short playback experiment:

```sh
python3 tools/freeze-audio-voices.py --run LABEL --expected-pid QEMU_PID --receipt TAG.tgz --evidence-name pcm-evidence
```

The monitor binds the current PID/argv and fixed WAV path, ignores the
initial file as a baseline, and runs for at most 240 seconds.
If the output has not yet been created, it records `baseline=null` and
`baseline_exists=false`, stays `pending_not_created`, and waits without
creating a placeholder WAV. The same ownership/header/closure checks apply
when the actual output appears. Only new
finalized 48 kHz/stereo/S16 files with no matching QEMU inode FD and unchanged
source stat across copying become `voice-N.wav`. At most four copies of up
to 64 MiB are retained without overwriting; their SHA256, headers, source
stats and UTC times are recorded in the directory's `manifest.json`.
`--evidence-name` accepts only a restricted basename under the current run
and defaults to `pcm-evidence`; the directory must not already exist.
Open/unfinalized data remain pending. The original WAV is never changed or
its header repaired, and neither a guest receipt nor a valid WAV proves the
target waveform. Start a new explicitly named monitor only for a new trial,
after inspecting the previous result. It uses no QMP or GUI calls.

`python3 tools/test-freeze-audio-voices.py` exercises a real writer process
through open/write/close/reopen-truncate and checks that earlier evidence
copies survive; it also checks final header lengths and directory bounds.

For the self-authored two-second fixture, run `python3 tools/build-audio-test-tone.py`
on the host before the trial. With the matching evidence monitor ready, fetch
the guest launcher and choose a unique tag:

```sh
curl -fsS -m 30 http://10.0.2.2:18769/get/audio-play.sh -o /tmp/audio-play.sh
sh /tmp/audio-play.sh TAG plain
```

The launcher verifies the downloaded fixture hash, owns the actual `afplay`
child, records its exit status under a 60-second observation limit, and returns
`TAG.tgz`. Explicit `TAG sample` additionally starts a 10-second/20 ms sample;
sample exit and nonempty-stack coverage are recorded separately from playback.
Do not overlap trials or reuse tags. After selecting a finalized voice copy,
validate its full contents on the host:

```sh
python3 tools/validate-audio-test-tone.py --capture RUN_DIR/pcm-evidence/voice-1.wav --output RUN_DIR/pcm-validation.json
```

The validator requires the pinned 48 kHz/stereo/S16 reference and finds the
complete contiguous 96,000-frame interleaved payload without deleting samples,
changing gain, reordering channels or repairing the WAV header. It separately
reports padding and left/right 440/660 Hz measurements. Run108's sampled and
subsequent plain trials both contained the complete bit-exact tone; see
plain validation
and sampled validation.
The sampled arm had an empty stack report. A pre-play voice failed the tone
comparison and is retained as a negative observation. These results verify
guest-to-WAV PCM output, not host speaker output, microphone capture or
physical timing.

The two-stream WAV profile advertises an input stream even though QEMU's WAV
backend has no input voice implementation. Run108 logged that
`virtio-sound.in` could not open; the none backend instead supplies timed
silence on input. This gap remains explicit and is not an established cause
of the first playback timeout or Safari failures. Run108 also encountered a Terminal
microphone permission dialog; it was denied before the successful trials, and
permission state, caches and diagnostic sampling changed.

The single-stream candidate passed paused generic-device tests but failed the
actual Run109 boundary: AppleVirtIOSound was active yet not registered/matched,
CoreAudio listed no devices, and no WAV was created. The controller had completed
VirtIO negotiation and one control request, which did not establish Apple audio
registration. See Run109 guest evidence.
The production enumeration was restored for the cold-boot A-B-A check. Run110
restored Apple driver registration and CoreAudio devices, but its captured PCM
failed the complete-reference match: 764 reference frames were replaced by a
512-frame block containing silence and an old fade prefix. The remaining two
reference regions were byte-exact; this does not locate the faulty layer. See
the original content analysis.
This does not establish a universal requirement for two VirtIO streams;
the reason this target did not accept the single-stream candidate remains open.
Original execution records and the candidate's historical preflight stay intact.

The installed Run104 `AppleVirtIO.kext/Contents/Info.plist` advertises
`AppleVirtIOSound` with `IOVirtIOPrimaryMatch=0x00191af4`: standard VirtIO
device type 25. This establishes a matching driver personality, not successful
negotiation, CoreAudio defaults, PCM transfer or a Safari improvement.
`build-qemu-bdif-usb` enables the existing upstream device through a generated
BDIF-only profile (`CONFIG_VIRTIO_SND=y`); the shared ARM profile and other
research builds remain unchanged. The build manifest records the template,
generated profile/configuration hashes and actual device registration.

Run the device-only preflight after building:

```sh
python3 experiments/E05-usbip-kernel/run-virtio-sound.py --label UNIQUE_LABEL
```

It reuses the production argument generator on paused diskless ARM `virt`,
checking default absence, `none` and `wav`, modern PCI `1af4:1059`, slot 6,
the ascending capability chain, VirtIO id 25/four queues, and owner cleanup.
For WAV only, bounded qtest writes configure the standard control queue and
verify the restored two-stream configuration. `PCM_INFO` must report stream 0
as output and stream 1 as input; this descriptor check does not test an input
voice or input PCM. The subsequent output-stream
`PCM_SET_PARAMS`, `PCM_PREPARE` and `PCM_RELEASE` must all return OK.
This opens the output voice and verifies a finalized RIFF header with zero
data bytes after close. No guest CPU, Apple firmware, host audio hardware,
PCM sample data or audible output is exercised.

`--virtio-sound --sound-backend wav --sound-pcm-trace` additionally requests a
default-off diagnostic at the VirtIO TX boundary. The path is fixed to
`RUN_DIR/guest-audio-boundary.bin`; no caller-selected destination is accepted.
The runner rejects trace without sound, existing files/symlinks, mismatched
device arguments, and a binary manifest without the matching `0023` patch.
The old binary remains eligible for ordinary trace-off runs. Requested limits,
path, format and unknown completion/cause are in `execution.audio.pcm_trace`;
none of these configuration fields asserts PCM correctness.

The exclusive file has an 8 MiB total limit and records packet sequence,
stream generation, negotiated format/rate/channels, mapped SG addresses,
raw PCM at TX pop and at the existing first lazy populate, each backend write's
requested/accepted byte count and offset, and buffer return reason. Pop evidence
never replaces the lazy source read. Terminal status distinguishes closed,
capped and capture-error traces; a missing terminal record is incomplete.
Synchronous evidence I/O can change timing. The trace observes PCM boundaries;
it does not repair audio or establish the cause of Run110 or Safari failures.

Decode a closed trace into a new directory, retaining packet metadata and
separate pop/populate/accepted PCM per stream generation:

```sh
python3 experiments/E05-usbip-kernel/pcm_boundary_trace.py \
  --trace RUN_DIR/guest-audio-boundary.bin --output-dir RUN_DIR/pcm-boundary-decoded
```

Compare these byte sequences with the independently frozen WAV voice. A changed
pop/populate pair identifies a change while the descriptor was held; intact
accepted bytes followed by a different WAV localizes the next investigation
after device acceptance. An accepted write is not a claim that all its bytes
reached the WAV: the STOP/RELEASE control accepted 4096 bytes but finalized only
1920 bytes. Preserve this difference instead of dropping or reordering samples
to make a comparison pass.

The focused candidate harness uses a nonperiodic interleaved frame counter and
qtest virtual time, with no guest CPU, Apple firmware or host audio hardware.
It covers split headers/cross-page SG, repeated partial consumption, deliberate
guest RAM mutation after pop, STOP/RELEASE tail discard, the hard capture cap,
exclusive open, and a real trace write failure via a child-only file-size limit.
The production `FirmwareSession.sound_arguments()` caller also runs against
the independent candidate. That candidate validation left the original
executable and objects unchanged; its two-object relink is not a clean full
build or Apple consumer validation.

```sh
# Coordinate any build/test CPU use with an active VM's owner first.
python3 experiments/E05-usbip-kernel/build-pcm-trace-candidate.py --output build/NEW_PCM_CANDIDATE
python3 experiments/E05-usbip-kernel/test-pcm-boundary.py \
  --binary build/NEW_PCM_CANDIDATE/qemu-system-aarch64 --output runs/e05-virtio-sound/NEW_PCM_TEST
```

After promotion, `--mainline-integration` permits the exact recorded main
binary only after checking its build/0023 hashes and absence of live owners.
The seven cases, including production arguments, passed on main build
`a931ea0beb1ff11ef09379789b9ae72739228b2307981be31c6558a665f5fb16`;
all QEMU children exited 0 and were reaped. The trace-cap and injected-write
failure cases intentionally retain incomplete trace status. Evidence is
`runs/e05-virtio-sound/pcm-boundary-main-20261107/results.json`.
This is diskless qtest integration; an Apple playback trace is still required.

`--sample-current-process` optionally adds a private local Unix GDB endpoint
and samples the current threads on all verified configured CPUs every 30 seconds. Source code
is decoded and checked before starting QEMU. During an owned stop, a unique
256-byte EL1 core-code match establishes slide; the live core UUID and eight
getter ranges must then match before TPIDR_EL1 and the checked proc fields are
read. No EL1 match or failed guards remain unknown. This does not enumerate
sleeping processes, identify current EL0 code, or inspect wait objects.

The sampler keeps the same 2-second read budget and 4096-byte memory-read bound
at every CPU count; unsampled CPUs remain unknown when either bound is reached. It
closes RSP and restores the original running/paused state through the existing
QMP connection, without breakpoints, stepping, D/continue, or guest writes.
Read-only is a restriction of this sampling client: the local QEMU GDB server
still implements the full debug protocol. Ordinary RSP observation errors
disable/skip the sampler; uncertain owner-QMP framing errors propagate.

No signing consumer, firmware/RamDisk upload or restore boot arguments are
invoked. A DFU/recovery result does not trigger another restore. The existing
session owns QMP shutdown and cleanup; QMP exit is not proof of a clean guest
shutdown.

Outputs are under `runs/e05-installed-boot/LABEL` and
`private/installed-boot/LABEL`: a compact result, the existing boot observer's
state/log/raw structures, original UART/device trace and retained storage.
BDIF backend calls and QMP frontend counters are reported separately, without
double-counting shared targets.

The owner thread uses standard QMP `screendump` at startup, about every 60
seconds, and before normal teardown. Original P6 PPM images retain dimensions,
hash and capture time. Exact duplicates reference an existing image; up to 16
files are retained, with a slot reserved for the final sample. Old retained
samples are not overwritten. Complete QMP command errors are recorded; timeout,
EOF and framing failure propagate because the owner connection cannot then be
safely reused.

`graphics_observation.collection_success` requires a valid final capture. It
does not infer GUI success from file existence, resolution changes or frame
events. `gui_verified` remains false and `manual_review` stays `not_reviewed`;
actual visual confirmation belongs in a separate review with image hashes.

The boot observer scans only the first 256 MiB and a specific candidate ABI.
Empty observations mean **unknown**. A Darwin banner, missing `-restore`, or
non-`md0` BSD root does not identify normal installed macOS. This collector
keeps `boot_classification.environment=unknown` unless the conservative joint
identity contract passes: the completed source's volume group and snapshot
match the authenticated system-root mount, followed by successful ordered
BaseSystem/normal pivots and later explicit normal-OS/non-Recovery flags.
With continuous monitoring, later contradictory flags revoke that classification. A different startup
sequence remains unknown, not automatically RecoveryOS. Only whitelisted
source-log identity fields are copied, with source hashes and line locators.
No restore-RamDisk userland UUID/header contract is reused.

`--kernel-observation continuous` is the default and retains the existing
256 MiB RAM capture/scan at the sampling cadence. Optional `--kernel-observation
boot-only` stops that RAM observer only after a successful capture passes the
joint normal-OS identity contract and has no observed kernel panic. Without
that proof it continues capturing; read errors retain the existing failure and
cleanup behavior. QMP status, block counters, requested memo counters, file
service, screenshots, optional current-process sampling and lifecycle handling
continue independently. The retained kernel log, last state and boot proof are
preserved, with last-capture times/count and `monitoring_active=false`,
`state=stopped_after_normal_boot` recorded in the report and observer state.
Those runtime/panic fields and classification are then historical. Later
panics, resets or reclassification are unobserved by this RAM observer, which
does not automatically resume even if QMP records a reset. This is an optional
diagnostic-overhead control with unchanged guest configuration and workload;
it does not select affinity or change any defaults.

WindowServer execution cues come from kernel messages naming its PID and a
VM unnest operation. They are reported separately from current liveness and
visual GUI confirmation. A post-run identity derivation must not overwrite
the historical result or claim that an earlier collector made that judgment.

Exit 0, `collection_success=true` and `passed=true` mean evidence collection,
final screen capture and cleanup completed;
`normal_os_boot_proven` and `recovery_os_boot_proven` remain separate. GUI and
WindowServer evidence are separate. Defaults stay `-display none` and
`REIMS_VGPU_WINDOW=0`; requesting a native window does not itself verify GUI.

## PAuth algorithm and GPU connection timing

`--pauth-algorithm {qarma5,qarma3,impdef}` selects QEMU's PAuth algorithm for
one boot. Omitting it keeps `qarma5` and the argv of every earlier run. `impdef`
is QEMU's non-cryptographic algorithm; it cannot be combined with
`--pauth-memo`, and the runner rejects that combination before it creates a
run directory. The installed system was restored and first booted under
QARMA5; booting it under another algorithm is an experiment, not a default.

`gpu_init_timing.py ARCHIVE.tgz [--out FILE]` reads the `webkit-stream.log` in a
`safari-probe.sh` archive and times the first GPU-process connection of the
Safari it finds: from the UI process's connection request, and from the GPU
process's `createGPUConnectionToWebProcess`, to `GPUProcessConnection::didInitialize`,
or to the unresponsive exit when the connection never initialises. It also
counts unresponsive GPU exits and WebProcess unresponsive notifications. On the
retained archives it gives 3.781 s for Run128 and 4.081 s for Run127, and an
unresponsive exit about 3.0 s after the request for Runs 121 and 123-125.

## Host CPU use during a trial

`perf` is closed to ordinary users on this host (`perf_event_paranoid=4`), so
host-side timing uses the scheduler's own per-thread counters, which any
process may read for threads of the same user.

`host_thread_sampler.py --wait-for qemu-system-aar --out RUN_DIR/host-threads.tsv`
records, about 100 times a second, the cumulative on-CPU time and run-queue
wait of every QEMU thread, and the host CPU a busy thread last ran on. Start
it as a background command next to the temperature logger; it ends when QEMU
exits. It does not attach to QEMU or change it.

`clock_offset.py serve --wait-for qemu-system-aar --out RUN_DIR/clock-offset.jsonl`
listens on 127.0.0.1:18771 and :18772 while QEMU lives. In the guest,
`curl -fsS -m 10 http://10.0.2.2:18771/clock.zsh | zsh -s TAG` sends twelve
wall-clock stamps over one TCP connection. The smallest arrival-minus-stamp
difference is the difference between the two clocks, good to a few
milliseconds; `clock_offset.py estimate FILE` prints it per tag.

`gpu_init_cpu_profile.py --timing gpu-init-timing-TAG.json --samples
host-threads.tsv --clock clock-offset.jsonl --clock-tag TAG --results
results.json` maps the guest log times of one trial onto the host samples. For
the initialisation window, each of its segments and the ten seconds on either
side it reports the mean number of vCPU threads on a CPU, the share of that
time on efficiency cores, the run-queue wait, and what the other QEMU threads
did. It sees host threads, not guest processes: a busy vCPU may be running
anything in the guest, and an idle guest vCPU sleeps in the host. An idle
guest is not an idle host either: at the login window about 2.3 of 8 vCPUs
were busy on average (Run138).

For the guest's side of the same window, run `guest-diag/log-window.sh` in the
guest after the trials are over:
`sh /tmp/log-window.sh TAG 'YYYY-MM-DD HH:MM:SS' 'YYYY-MM-DD HH:MM:SS'` (guest
local time). It uploads `TAG.tgz` with what every process logged in that
window (`log show`, stopped after 120 seconds), launchd's own log lines for
it, one process list with start times and CPU times, and the output of
`memory_pressure`. It launches nothing and changes no setting.

When `sudo -n` is available, `sudo -n python3 experiments/E05-installed-boot/block_sampler.py
--pid PID --comm-prefix 'CPU ' --seconds 40 --stack-words 24 --out FILE`
samples `/proc/PID/task/TID/syscall` without stopping the threads, recording
whether each vCPU thread is running or blocked on a specific futex address.
`python3 experiments/E05-installed-boot/block_report.py FILE --binary
build/qemu-bdif-usb/qemu-system-aarch64 [--from US --to US] [--json]` resolves
futex addresses against the executable's data symbols (`bql`,
`exclusive_resume`, `exclusive_cond`, `qemu_cpu_list_lock`) and scans optional
stack words for the innermost caller nearest the lock (outer frames are raw
stack-word hints, not DWARF unwinds).

On a QEMU build containing matching patch `0033`, `--sync-stats` adds
`x-sync-stats=on` to `-cpu` without changing default argv when omitted. Each
vCPU then counts MTTCG outer-loop exits, inner BQL acquisitions (`do_interrupt`,
`handle_interrupt`, `ERET` hooks, `ARM_CP_IO` reads/writes, TCG MMIO, page-table
walks), exclusive-section sources (`tlbi` all/page/range, atomic step, TB flush),
and per-register `ARM_CP_IO`/`TLBI` operations in thread-local counters with no
additional locks. `python3 experiments/E05-installed-boot/sync_stats.py snapshot
--run LABEL --out FILE.json` reads `x-sync-stats-json` from every vCPU over the
authenticated read-only `inspect.sock` without pausing the VM, and
`sync_stats.py diff BEFORE.json AFTER.json [--out DIFF.json]` computes exact
window deltas and ranked register/TLBI tables. Bare-metal dual-vCPU counter
coverage is in [`experiments/E06-sync-counters/`](../E06-sync-counters/README.md)
(`make sync-counter-tests`).

## Initial-setup input helper

`guest_ui.py` binds an observing run to its actual QEMU PID, executable,
complete argv, process start ticks and the Unix peer PID of its independent
`inspect.sock`. It never starts a VM or uses the runner's owner QMP connection.

```sh
python3 experiments/E05-installed-boot/guest_ui.py --run RUN screenshot --name ui-before
python3 experiments/E05-installed-boot/guest_ui.py --run RUN native-screenshot --name native-before
python3 experiments/E05-installed-boot/guest_ui.py --run RUN click 100 200 --frame ui-before.png
python3 experiments/E05-installed-boot/guest_ui.py --run RUN key down
python3 experiments/E05-installed-boot/guest_ui.py --run RUN release-known-keys
python3 experiments/E05-installed-boot/guest_ui.py --run RUN chord meta_l+alt+esc
python3 experiments/E05-installed-boot/guest_ui.py --run RUN type-ascii local-test
python3 experiments/E05-installed-boot/guest_ui.py --run RUN --keyboard-drain type-ascii 'echo ready'
python3 experiments/E05-installed-boot/guest_ui.py --run RUN type-secret \
  --file private/initial-setup.json --key password
python3 experiments/E05-installed-boot/test-guest-ui.py
```

Screenshots preserve new PPM and losslessly derived PNG files; existing names
are refused. Pointer coordinates must lie within a selected-run screenshot.
`native-screenshot` independently reads the unique X11 client window whose
`_NET_WM_PID` matches the authenticated run, rechecks process/start-ticks/socket
peer and window identity after capture, and writes PNG plus JSON metadata. It
never captures the root/host desktop; native pixels do not establish physical
scanout, FPS or latency. It requires an active native X11 window.
Keys/buttons receive paired down/up requests; accepted input is only host
acceptance, and its visible effect needs a subsequent screenshot. The small
chord allowlist includes Command-Option-Escape, Command-A, Command-N
(a fresh app window), Command-Space (Spotlight for ordinary desktop app launching)
Shift-Tab, and Control-C for interrupting a foreground Terminal diagnostic.

Every ordinary request is recorded in `ui-actions.jsonl` before sending and
immediately after its reply, with flush/fsync. Broken connections retain an
unknown-effect receipt. A complete command error still permits a release;
when connection loss prevents release, that state is explicitly unknown.
The helper exposes no arbitrary QMP, register or memory-writing command.

For public keyboard diagnostics, `--keyboard-drain` temporarily enables only
the payload-free `hid_kbd_queue_full` and `hid_kbd_queue_empty` events in the
run's existing trace log. Each key request waits for a fresh drain observation;
overflow, timeout or a changed trace file stops the text without replaying an
accepted action. Original trace switches are restored on exit. Queue drain is
not proof that the application executed a command: verify pixels or returned
guest output. `type-secret` cannot use this diagnostic mode. The default
`release-known-keys` sends modifiers first and releases keys individually with
spacing, rather than overflowing a 16-scancode queue with one recovery burst.

Only a complete `input-send-event` rejection whose description is exactly
`VM not running` permits a retry. The helper polls read-only `query-status`
until the temporarily paused VM is running (or suspended, which QEMU accepts
for input), then resends that rejected event list under the same 2-second
deadline, starting at the first complete rejection. The initial request's
2-second wire budget starts after journal persistence, binding checks and
keyboard trace preparation. A local retry deadline reports
`input_retry_timeout`; `vm_paused_timeout` requires an observed paused status.
Key/button release uses the same bounded handling. It never sends
`cont` or changes VM run state; persistent pauses fail. Accepted events,
other rejections and uncertain transport results are never replayed.
`release-known-keys` sends a constant list of key-up events for all supported
keys; it does not infer private input or clear a field.

Printable ASCII uses physical US-layout qcodes; focus and guest input-method
behavior remain matters for visual confirmation. Secret text is read only
from a small owned 0600 regular JSON file inside workspace `private/`.
Its value, length and per-key requests are omitted from stdout, journal and
errors. Only an aggregate secret-action before/after receipt, a fixed safe
failure class when needed, release confirmation and the source file/key
locator are retained. Pause polling/retries also omit per-key secret receipts
and arbitrary server messages. The helper does not generate account details or
passwords or advance a setup workflow automatically. Secret typing does not
automatically capture the screen.

## Offline CPU occupancy

`cpu_occupancy.py` summarizes a retained run without starting QEMU or writing
into the run directory:

```sh
python3 experiments/E05-installed-boot/cpu_occupancy.py RUN_DIR \
  --window 2026-11-01T00:19:30Z 2026-11-01T00:29:00Z --output OUT.json
python3 experiments/E05-installed-boot/test-cpu-occupancy.py
```

Host utilization comes from per-interval deltas of the cumulative `TIME`
column in `host-qemu-thread-samples.log`; its `%CPU` column is a lifetime
average and is not used. Guest occupancy tallies `current-process.jsonl`,
treating the most common `kernel_task` PC as the idle loop. Both inputs are
~30 s point samples, so results are statistical evidence, not a trace.

## Live diagnosis helpers

`network_state.py` reads the live network device through the same PID, argv,
start-ticks and socket-peer binding as `guest_ui.py`:

```sh
python3 experiments/E05-installed-boot/network_state.py --run LABEL --name net-before
python3 experiments/E05-installed-boot/network_state.py --run LABEL --name net-after
python3 experiments/E05-installed-boot/test-network-state.py
```

It checks VMApple slot 5 / PCI `1af4:1041`, reads net status and three queue
states, and collects bounded split-ring indices plus the latest completed
control descriptor/header/ACK. PCI capability parsing reuses the storage
validator. The ECAM/RAM/PCI-window addresses come from the pinned VMApple
machine map. Live PCI configuration reads deliberately skip type-5
`pci_cfg_data`, whose read can forward to a selected BAR/ISR.

After validating memory BAR bounds, the collector records MSI-X enable and
function mask, up to eight table entries and the PBA, plus the common
configuration's **currently selected** queue/vector. It never writes queue
selection, reads ISR MMIO, pauses/resumes the VM, or invokes the queue-element
command (which updates a cached avail index). Total physical reads are capped
at 4096 bytes and each request at 256 bytes; invalid/unavailable layouts are
marked unknown. Binding or transport failures refuse collection.
Use a new output name for each snapshot. These unpaused reads are not atomic;
completed descriptor buffers may have been reused, and current state cannot
prove that a past interrupt reached the guest.

`host-thread-sampler.sh QEMU_PID RUN_DIR/host-qemu-thread-samples.log` appends
`ps -L` blocks every 30 seconds until QEMU exits; it is the host input for
`cpu_occupancy.py`.

`guest_files.py` is a loopback-only file exchange for `--user-network` runs:
QEMU maps the guest's 10.0.2.2 to host 127.0.0.1, so the guest can fetch
`guest-diag/` scripts (`GET /get/NAME`) and upload bounded text or binary evidence
(`PUT /up/NAME`, single path component, Content-Length required, no
overwrite) into `RUN_DIR/guest-uploads/`:

```sh
python3 experiments/E05-installed-boot/guest_files.py \
  --serve experiments/E05-installed-boot/guest-diag \
  --uploads runs/e05-installed-boot/LABEL/guest-uploads
```

For a new diagnostic run, add `--user-network --guest-files` to `run.py`.
The runner binds `127.0.0.1:18769`, verifies the actual script/fixture GET bytes
and an upload roundtrip before starting QEMU, records the result in
`guest_files_service`, and closes the service during runner teardown. An occupied
port or failed exchange prevents VM startup. Host readiness does not prove
the guest has obtained DHCP or can reach the service.

For a separately managed existing run, `start-guest-files.sh LABEL` starts
the same verified service in the foreground. Stop this process when its run
ends; do not also use it when the runner owns the service:

```sh
experiments/E05-installed-boot/start-guest-files.sh LABEL
```

That convenience script starts the ordinary resource mode. For an existing
diagnostic run managed with a separate service, use the Python service command
above with `--shared-cache-identity` appended to register and verify the generated
identity wrapper. This does not enable the CPU diagnostic; its startup flag is
still required. Do not start either standalone service alongside the runner's
`--guest-files` owner.

In the guest: `curl -s http://10.0.2.2:18769/get/probe.sh -o /tmp/probe.sh &&
sh /tmp/probe.sh TAG`. `gpuwatch.sh` follows WebKit GPU processes and records
their PID discovery and process/thread tables with per-observation deadlines;
an outer collection deadline also forces partial-log packaging if the guest
shell or a system utility wedges. `load.sh` adds synthetic CPU
load. Run69 could not use the exchange because
the guest network was down; it worked in Run65.

`network.sh TAG` performs bounded host-HTTP and certificate-checked public
HTTPS requests and uploads the result. `render.html` is the exact authored
185-byte RGB fixture used in Run65. `safari-probe.sh TAG [URL]` fetches that
fixture, requests ordinary Safari quit, confirms exit, and runs the existing
bounded GPU watcher on a new launch. It uploads timestamped `hw.memsize` and
`vm_stat` observations before and after the trial. Ordinary watcher mode runs
without `sample` or `spindump`; use the explicit `sample` mode for a separate
diagnostic intervention. `gpu-sample.sh TAG` obtains one-second
`sample` stacks for up to three existing WebKit GPU processes; sampling is a
diagnostic intervention, not a performance measurement.

### Fixed WebGL2 readback

For a new fixed self-authored WebGL2 check, first complete diagnostic arming
when that profile was selected. Then use the ordinary Safari observer, with a
fresh observer tag and page-result tag. Run128 used `r128safari` and `r128-webgl`;
the examples below deliberately use different tags:

```sh
# Guest Terminal, ordinary login user:
export H=http://10.0.2.2:18769
curl -fsS -m 30 "$H/get/safari-probe.sh" -o /tmp/safari-probe.sh &&
  sh /tmp/safari-probe.sh safari-watch-01 "$H/get/webgl-probe.html#webgl-01"
```

The new fixed-origin/path branch requires a fragment matching
`[A-Za-z0-9][A-Za-z0-9_-]{0,47}`. It prefetches and hashes `webgl-probe.html`
without the fragment, then preserves the full URL when forwarding it to the
watcher/Safari. Existing render/local/other URL behavior is unchanged. The file
service verifies the named page through GET when it is present; custom legacy
fixture directories remain usable without that optional page.

The page uploads `webgl-01-offscreen.bin`, `webgl-01-default.bin` (each 96×64×4
= 24,576 raw RGBA8 bytes, bottom-left row origin) and `webgl-01.json` through the
current-run bounded PUT endpoint. Wait for the original observer to finish and
for `safari-watch-01.tgz` plus `safari-watch-01-after.txt`; do not reload the page
or start another observer while it runs. Its nominal 120-second observation is
not an end-to-end deadline: preparation, bounded probes and cleanup also take
time. Run128's three page outputs arrived before that original observer ended;
no extra completion window or repeat trigger was used.

At that collection boundary, inspect the three page files and run the raw oracle
once for the **new** label/tag, whether the inputs are complete or a bounded
failure must be retained:

```sh
# Host, from the repository root:
python3 -B tools/validate-webgl-probe.py --run NEW_DIAGNOSTIC_LABEL --tag webgl-01
```

The validator writes `RUN_DIR/webgl-01-validation.json` and refuses to overwrite
it. Missing input, wrong byte counts or failed page metadata remain unavailable,
incomplete or failed evidence; do not repair pixels/headers or overwrite the
result to make a check pass. It compares both complete raw buffers with an
independently generated six-tile oracle and checks the reported WebGL2 execution
metadata. Compare its `current_fixture_source.sha256` with the run's served GET
identity and guest prepare hash; a current host file alone does not prove which
version the browser received. Inspect the linked existing Run128 validation
instead of rerunning its oracle. HTTP readiness, the visual page and raw readback
equality are separate evidence; none proves physical scanout, FPS, latency,
general shader coverage or original Safari deadlines.

For a separate initialization diagnostic, `safari-probe.sh TAG URL
sample-connection` consumes the same live WebKit log stream and samples once
at the first GPU connection event with a preceding Safari ownership assertion.
It retains both source log lines, the observed guest time, and the sample's
actual Date/Time; `TAG-connection-trigger.txt` is uploaded separately so the
host receipt time can be compared. The requested sample lasts one second with
a 20-second outer bound. A trigger alone does not prove the sample landed
inside initialization; check its actual timestamp and stacks.

`sample-burst` triggers on the first GPU constructor event in that same log
stream, before ownership is known. It records ownership as pending and keeps
the subsequent Safari assertion for post-collection PID attribution. It saves
up to four separate one-second, 20 ms, `-mayDie` reports within a 30-second
wall-clock budget (each process deadline is at most 12 seconds and the remaining
budget), stopping if the target disappears. Each segment retains argv, actual
report Date/Time, start/end times, exit status, thread rows and stack-frame rows.
Only a nonempty call graph counts as collected stacks; still check whether the
samples cover connection initialization. Exit 0 or a Binary Images list alone
does not establish coverage. The mode is a diagnostic intervention.

For the next fresh GPU initialization observation, use the opt-in `stackshot`
mode. Run109's installed macOS 27.0.1 / 26A434 `spindump` retained real user and
kernel stacks from a six-second `sleep` after it exited during a ten-second
collection (raw control evidence).
This establishes the observer's early-exit capability, not Safari coverage or
normal performance. In the **ordinary login user's Terminal**, authenticate
with `sudo -v`, then run with a new tag:

```sh
curl -fsS http://10.0.2.2:18769/get/safari-probe.sh -o /tmp/safari-probe.sh
sh /tmp/safari-probe.sh r110stack2 http://10.0.2.2:18769/get/render.html stackshot
```

The helper confirms Safari has quit, starts `gpuwatch.sh` through `sudo -n`
with URL `-`, and opens Safari as the ordinary user only after a unique public
canary event has passed through the live stream, watcher FIFO and parser.
The watcher uses the installed `log emit --type default --subsystem
local.e05.gpuwatch --category readiness --public NONCE`; the WebKit predicate
adds only that exact subsystem/category/message. An emit exit code, live PID,
Filtering line or Timestamp header alone does not establish readiness.
It waits for the root watcher to finish; do not
use a root GUI session or the background `start-gpuwatch.sh` for this mode.
The optional fourth argument sets the observation window (default 120 seconds,
range 1–600). A tag cannot be reused, including a failed sudo preflight.

Preparation owns and bounds each external command. Every Safari `pgrep` has
a three-second limit and is reaped before its result is used; only exit 1 with
empty stdout/stderr confirms absence. Errors, contradictory output and timeouts
record `SAFARI_EXIT_UNKNOWN` and prevent a new launch. An initial bounded
process check means an already-absent Safari skips AppleScript
entirely, and only a confirmed present process receives the quit request.
Subsequent quit polling uses a
30-second wall-clock window, not 30 potentially slow queries; an AppleEvent
timeout followed by a present Safari records `SAFARI_QUIT_PENDING`. Neither
condition establishes a hung `pgrep`. Interrupting preparation reaps the
current child and uploads the partial preparation log, including its signal
and command output, before exiting.

Run110's first `r110stack` attempt stopped before opening Safari because the
Timestamp header was absent; it is observer-readiness evidence, not a GPU
failure (original archive).
The installed `log.1` documents `emit`, and the separate root `r110canary`
probe subsequently captured its one real nonce event through a live FIFO and
parser; the header appeared with that event
(raw canary evidence).
`log-canary.sh TAG capabilities` reads installed interfaces, while its root
`probe` mode repeats this bounded observer check without Safari, stack sampling
or logging configuration changes. Canary readiness does not establish GPU
stack coverage. The ordinary-user launcher itself is unchanged and downloads
the current watcher on every new invocation.

The first successful **Safari** GPU ownership assertion triggers one collection
without a `ps`/`pgrep` preflight. The root watcher owns a long-lived `/bin/sleep`
anchor and calls `spindump ANCHOR 10 100 -onlyTarget -proc GPU_PID
-sampleWithoutTarget -timelimit 90 -noProcessingWhileSampling -timeline -o REPORT`.
These are the two-target options validated in Run109; no `-noIPC` or Safari
watchdog setting is added. The watcher retains the live WebKit stream during
sampling and symbol processing. It directly terminates and reaps its sampler,
anchor, logger and parser before archiving, including a late trigger that needs
up to 120 additional seconds before sampler timeout. Ordinary and `sample`
modes keep their existing behavior.

Use the explicit `stackshot-system` mode when the same sampling window must
include both a GPU client and the tasks serving its synchronous requests.
It retains the same canary handshake, Safari ownership trigger, 10-second /
100 ms sampling window and long-lived anchor, but omits `-onlyTarget` and
`-proc` so spindump samples all processes. It does not query process names
before sampling or assume an executable identity from a Mach service name.
This wider collection is a diagnostic intervention, not normal timing.
Download the updated ordinary-user launcher before using the new mode:

```sh
curl -fsS http://10.0.2.2:18769/get/safari-probe.sh -o /tmp/safari-probe.sh
sh /tmp/safari-probe.sh r111system http://10.0.2.2:18769/get/render.html stackshot-system
```

The full report remains intact. `stackshot-task-index.txt` lists each reported
task's process/path/UID and thread IDs with sample ranges for post-collection
turnstile correlation; the GPU assessment still counts only its exact PID.
Server identity and the wait chain remain unverified until matching actual
thread IDs and time ranges in the full report. Both modes retain the 120-second
outer sampler bound: a report saved after TERM with spindump exit 0 still
records `outer_timeout=true` and watcher exit 124. Report completeness remains
explicitly unverified; no report is truncated to fit the upload limit.
The baseline system mode does not add `-noIPC`. The separate opt-in mode
`stackshot-system-noipc` adds only that option to the same system-wide sampler
argv and records `collector_daemon_ipc=noIPC`. Run112's installed command help
defines it as “Do not IPC out to any daemons (more expensive, but more reliable)”
(actual help output).
This controls the collector's daemon IPC; it does not promise faster processing,
lower CPU use, or removal of all observer effects. Use the updated launcher
with `stackshot-system-noipc` as its mode argument. The original targeted
`stackshot` and baseline `stackshot-system` retain their previous sampler argv;
all three retain the same timing bounds, raw report and upload-size checks.
`spindump-help.sh TAG` preserves installed help, argv, actual exit status and
errors with a ten-second bound; its Run112 exit 64 was the help/usage path.

The additional `stackshot-system-raw` mode separates capture from formatting.
It keeps the same GPU ownership trigger, all-process/noIPC scope and 10 seconds /
100 ms sampling, adding `-noText -noSymbolicate`. Only this GPU raw mode uses
a 270-second internal / 300-second outer generation limit and a ten-second TERM
save grace; its anchor lives for the observation window plus 370 seconds.
This allowance follows the actual three-second raw control and decode success,
and the ten-second run's completed sampling followed by a zero-byte output at
the former 120-second generation cutoff. It does not imply capture failure or
deadlock, and it does not shorten or repeat the ten-second sampling window.
Native exit 0 after a timeout-triggered TERM still retains `outer_timeout=true`
and watcher exit 124. All non-raw modes and the three-second raw control retain
their previous limits.
Its output is `spindump-raw.spindump`, explicitly a binary-only spindump input.
The archive records its byte count and hash; it does not run text frame counting
or create a task index. Target stacks, coverage and raw completeness remain
unverified until decoding. A missing or empty raw output returns a nonzero
status even when spindump exits 0; existing sampler failures/timeouts remain
visible. Re-download the launcher to use this new mode.

Validate the raw path independently with `spindump-control.sh TAG system-raw`
under root: it captures all tasks for **3 seconds at 100 ms**, without a Safari
or process-query prerequisite. Then run `spindump-control.sh NEW_TAG reprocess
SOURCE_TAG` under root to copy the existing
`/tmp/SOURCE_TAG/spindump-raw.spindump` into the new result, record its reference
and hashes, and call spindump with `-i`, `-timeline` and `-noIPC`. The original
input is unchanged, and no new sampling is performed. Reprocessing has its own
270-second internal / 300-second outer limits and a ten-second TERM save grace;
this does not extend the live collection window. Timeout status is retained
even if a useful report is saved. Validate the three-second control's nonempty
raw output and decode result before collecting a fresh GPU raw window.

For the existing processor diagnostic only,
`spindump-control.sh NEW_TAG export-stuck SOURCE_TAG` accepts the exact stuck
buffer pathname printed in that owned source's stderr and matching its recorded
spindump PID. It copies and hashes that file as `owned-stuck-stackshot.buf`;
it neither scans `/var/tmp` nor accepts an arbitrary path. This is a snapshot
for investigating the stuck processor, **not the original sampling window**.
The exported buffer can be decoded with
`spindump-control.sh NEW_TAG reprocess EXPORT_TAG stuck`. All these actions
retain complete outputs and enforce the same 32 MiB single-upload limit.

For one all-task snapshot of an already-running state without quitting or
launching Safari, use `sudo -n /bin/sh spindump-control.sh TAG system-noipc`.
This explicit mode owns an anchor and samples all processes for 10 seconds at
100 ms with `-noIPC`; it performs no `pgrep`, AppleEvent or ownership preflight.
It retains the existing 120-second outer bound, child cleanup, complete report
and 32 MiB upload check. It is a diagnostic intervention; inspect real task and
thread identities before attributing a service or a process-query wait.

`TAG.tgz` retains `collector.sh`, launcher source, exact argv, input hashes,
root/ordinary UID and PID records, target ownership event, start/end/status and
timeout metadata, canary nonce/emitter status/raw event and stream argv,
raw stdout/stderr, the full `spindump-report.txt` (including
its reparseable binary representation), `target-events.txt` and
`stackshot-assessment.txt`. Inspect target PID, actual report Date/Time / End
time, sample ranges and frames together with ownership/connection logs before
claiming initialization coverage. Only the target GPU's process section,
threads and real frame rows can set `target_stack_acquired=true`; an anchor
stack, an empty report or exit 0 cannot. Missing/non-GPU target stacks and
initialization coverage remain `unknown`. SIGABRT, permissions and timeout
statuses remain explicit even if a partial report exists. Archives above the
file service's 32 MiB single-file limit are retained locally and rejected
explicitly, rather than silently dropped. The ordinary launcher separately
uploads `TAG-prepare.txt`, `TAG-launch.txt`, `TAG-root-launch.log` and
`TAG-after.txt`.

For an interactive GUI run, start the watcher through
`start-gpuwatch.sh TAG SECONDS - [sample]`; it downloads the same watcher,
launches it with `nohup`, and returns the Terminal prompt immediately so later
Safari focus/navigation remains usable. A failed script download records
`guest_files_preflight=failed` in `/tmp/TAG-gpuwatch-launch.log` and exits 12.
A successful launch uploads `TAG-started.txt`; the watcher uploads `TAG.tgz`
when its bounded window ends. Check these separate receipts before interpreting
an absent GPU event. HTTP upload failures return nonzero instead of being
reported as successful uploads.

`test-guest-files.py` exercises real loopback GET/PUT, startup conflicts and
signal cleanup. `test-gpuwatch-offline.py` also runs the actual background
launcher, downloader, archiver and upload against a loopback server, substituting
only guest-specific observers. Stackshot checks also exercise ordinary/root
launch ordering (with simulated UIDs and sudo), a deferred stream header,
missing canary events despite emit success, failed/stuck canary emitters,
empty/error/abort reports and late or stuck sampler reaping before a real
archive PUT. System-mode checks also retain multiple task identities and
preserve an outer timeout when TERM saves a report with exit 0. Timeout tests
shorten deadlines in their private script copies;
Linux mawk uses its streaming option for the BSD awk consumer contract. These
checks do not establish actual sudo privileges, macOS stack collection or
Safari initialization coverage; the next guest run must validate that boundary.

### Standalone Opus component lookup

`tools/build-macos-audio-query.sh` cross-builds the self-authored
`guest-diag/audio-query.c` with installed Clang/LLD. It uses explicit public
Darwin ABI declarations and a text-only libSystem import stub, without an Apple
SDK or copied Apple implementation. The arm64 Mach-O is at
`build/macos-audio-query/audio-query`; exact ignored links in `guest-diag/`
make it and its SHA-256 available through the existing file service.
`build/macos-audio-query/build.json` records tool/source/binary identities and
static checks of entrypoint, imports and every ad-hoc CodeDirectory SHA-256 page.
These checks do not establish acceptance by the guest loader.

The probe loads the guest's installed AudioToolbox, resolves
`AudioComponentFindNext`, flushes `READY`, waits 15 seconds, performs the
first and repeated Opus decoder lookup, then stays alive for 60 seconds.
The descriptor is `adec` / `opus` / `appl`, flags 2, mask 0, matching
the public WebKit lookup shape. It does not register, instantiate or alter a
component. `load-only` performs the same load and symbol resolution but skips
both lookups. Output includes epoch markers, Mach ticks/timebase, wall
nanoseconds and process CPU clock ticks (Darwin unsigned-long `clock_t`;
UNIX03 defines 1,000,000 ticks per second). Failed CPU readings are marked
invalid. This ordinary standalone process does not reproduce Safari's GPU
sandbox, full initialization or first-page stability.

Probe version 2 adds `codecs`: after loading both frameworks and the same
15-second READY lead-in, it queries VP9 (`vp09`) and AV1 (`av01`) with
`VTIsHardwareDecodeSupported`, then Opus (`opus`) and Vorbis (`vorb`)
with `AudioComponentFindNext`. It repeats that complete sequence once.
This order follows WebKit commit
`47fa426c169f32f8fc3dedd2c28bd04af887713d`,
`GPUConnectionToWebProcess.cpp:370–385`. Each call has its own wall/CPU
measurement; `first_codecs_total` and `repeat_codecs_total` include the
four calls and timer reads, with no output/flush between them. Framework loading
is measured separately before READY. The VideoToolbox return is the public
one-byte `Boolean` ABI and is reported as `hardware_decode_supported`;
false is a normal unsupported-hardware result. Audio lookup results remain
`component_found`. No decoder registration, actual decode or
`enableMediaPlaybackIfNecessary` is performed.

ABI/value references: [Apple VideoToolbox API](https://developer.apple.com/documentation/videotoolbox/vtishardwaredecodesupported(_:)),
[Apple Boolean definition](https://github.com/apple-oss-distributions/CF/blob/main/CFBase.h),
[WebKit codec sequence](https://github.com/WebKit/WebKit/blob/47fa426c169f32f8fc3dedd2c28bd04af887713d/Source/WebKit/GPUProcess/GPUConnectionToWebProcess.cpp),
[WebKit AV1 query](https://github.com/WebKit/WebKit/blob/47fa426c169f32f8fc3dedd2c28bd04af887713d/Source/WebCore/platform/graphics/cocoa/AV1UtilitiesCocoa.mm),
[WebKit Vorbis lookup](https://github.com/WebKit/WebKit/blob/47fa426c169f32f8fc3dedd2c28bd04af887713d/Source/WebCore/platform/graphics/cocoa/WebMAudioUtilitiesCocoa.mm),
and [FFmpeg's VideoToolbox FourCC compatibility constants](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/videotoolbox.c).

Build on the host, then download the wrapper in the guest:

```sh
tools/build-macos-audio-query.sh
# Guest Terminal, with the existing --guest-files service:
curl -fsS -m 30 http://10.0.2.2:18769/get/audio-query.sh -o /tmp/audio-query.sh
nohup sh /tmp/audio-query.sh aq-load load-only plain >/tmp/aq-load.log 2>&1 </dev/null &
# Run separately, with a new tag for each trial:
nohup sh /tmp/audio-query.sh aq-plain query plain >/tmp/aq-plain.log 2>&1 </dev/null &
nohup sh /tmp/audio-query.sh aq-sampled query sample >/tmp/aq-sampled.log 2>&1 </dev/null &
nohup sh /tmp/audio-query.sh aq-codecs codecs plain >/tmp/aq-codecs.log 2>&1 </dev/null &
```

Do not overlap trials. The wrapper verifies download hashes and the existing
ad-hoc signature using guest `codesign`; it does not re-sign or change the
probe. It uploads `TAG-ready.jsonl` separately and `TAG.tgz` with program
output, errors, signature/hash verification and true process exit statuses.
Explicit `sample` mode starts one 45-second/20-ms `-mayDie` sample when
READY is observed and checks actual thread/stack rows. The sample's real time
must still be compared with query markers. The wrapper's 240-second collection
deadline retains partial evidence on timeout. Sampled timings are interventions;
use plain trials for timing. Cross-process trials can still share system caches.

NVIDIA runs use the system runtime selected by the production
`FirmwareSession` environment. On 2026-11-07 the loaded driver was 595.99.02:
Vulkan enumeration passed with the system environment and failed with the old
project-local 595.91.07 library override. The environment control is recorded in
`runs/e06-pauth-va-20261107/vulkan-test-environment.json`.

The project-local 595.91.07 runtime was a workaround for the loaded 595.91.07
module on 2026-11-04. Its NVML/Vulkan/compute/render evidence remains in
`runs/host-nvidia-runtime-20261104/results.json`; that historical override is
not part of the current launch command. Recheck the loaded driver and Vulkan
environment after host driver changes.

### Fixed WebKit timeout diagnostic (default off)

`--webkit-timeout-diagnostic` adds the fixed private 26A434 two-site profile to
the CPU argument. It requires a binary whose build manifest contains the exact
`0030-arm-timeout-diagnostic.patch` and `0031-arm-timeout-runtime-slide.patch`
hashes, plus the pinned private profile hash.
Omission leaves the CPU argv unchanged. Enabling the profile starts it unarmed;
neither a static guard nor a host fixture is evidence of runtime replacement.
The profile changes only the two guarded 3-second immediates to 30 seconds.
Every result remains diagnostic-only with
`original_deadline_performance_pass=false`.

Use the [current profile's prerequisite/build and launch sequence](#current-run128-profile),
including `--user-network --guest-files` and the diagnostic flag. Before any
arming, quit older Safari windows through the ordinary guest UI and confirm
`pgrep -x Safari` exits 1 with no stdout or stderr. A successful quit request
alone is not that confirmation. Run128 retained
[`r128-safari-exited.txt`](../../runs/e05-installed-boot/m3-webgl2-readback-netctrl512-20261108-128/guest-uploads/r128-safari-exited.txt)
before its probe and publication. If Safari remains present or the query fails,
stop this sequence rather than attributing subsequent global counters to a new
first launch.

The file service verifies the previously built
`shared-cache-identity` binary, checksum and source/build identity before QEMU
starts. It serves a generated fixed `shared-cache-identity.sh` wrapper; the private
instruction/context guard bytes are not served. The wrapper verifies the
download hash and existing ad-hoc signature, runs the metadata-only probe with
the fixed cache/image identifiers, and uploads its JSON, exit receipt and stderr.
It does not load WebKit, call `dlopen`, re-sign the probe or write guest code.

```sh
# Guest Terminal, ordinary login user, after the Safari-absence check.
# Pick a fresh short tag: the wrapper refuses an existing /tmp directory.
export H=http://10.0.2.2:18769
curl -fsS -m 30 "$H/get/shared-cache-identity.sh" -o /tmp/cache-identity.sh &&
  sh /tmp/cache-identity.sh identity-01
```

Require wrapper exit 0, `identity-01-receipt.txt` with successful hash/signature
checks and `probe_exit=0 timeout=false`, and `guest-uploads/identity-01.json`
with `state=verified`. The probe is metadata-only and validates its own process,
not Safari's mapping. Then, in a **separate host terminal** while the original
controller still owns the observing VM:

```sh
RUN_DIR=runs/e05-installed-boot/NEW_DIAGNOSTIC_LABEL
python3 -B experiments/E05-installed-boot/timeout_diagnostic.py \
  --run NEW_DIAGNOSTIC_LABEL --action arm --probe identity-01.json &&
  test ! -e "$RUN_DIR/webkit-timeout-publication.json" &&
  cp "$RUN_DIR/webkit-timeout-diagnostic.json" "$RUN_DIR/webkit-timeout-publication.json"
python3 -B experiments/E05-installed-boot/timeout_diagnostic.py \
  --run NEW_DIAGNOSTIC_LABEL --action status &&
  test ! -e "$RUN_DIR/webkit-timeout-before-trigger.json" &&
  cp "$RUN_DIR/webkit-timeout-diagnostic.json" "$RUN_DIR/webkit-timeout-before-trigger.json"
```

Inspect all vCPU rows and the publication receipt. Compare each CPU's epoch with
its own baseline/publication; epochs need not have one common numeric value.
Zero site hits at publication are expected and do not prove replacement.
These named phase records preserve temporal evidence while the canonical helper
report is updated; do not overwrite an existing phase record. Record the
before-trigger boundary **before** invoking the Safari wrapper, then run the
[WebGL workload](#fixed-webgl2-readback). An after-prepare snapshot may already
be after the wrapper's Safari launch; it is not a substitute for a prelaunch
baseline. Run128's after-prepare snapshot had exactly that later scope.

The helper accepts only a current-run owned JSON basename. It checks strict
types, fixed UUIDs/offsets, address bounds/alignment and both independent slide
equations. Those checks identify the probe's mapping only; matching cache/image
UUIDs do not imply that Safari, GPU, WebContent and the probe share a slide.
The host saves the probe's own base/slide privately as evidence and never sends
them to QEMU. QEMU derives each candidate image base from the actual executing
PC minus the fixed site offset, then checks the current address space's UUID,
original instruction and full 64-byte window. Rejections retain original behavior.

The dedicated QMP capability reuses `GuestUI` PID/executable/full-argv/start-time
and socket-peer binding without widening ordinary UI or read-only operations.
It inspects existing state, pauses the entire VM, sets
`x-webkit-timeout-armed=true` on every observed vCPU, then checks
armed/status/epoch and unchanged counters before continuing an originally running
VM. Revocation sets that same boolean to false. There is no host base setter.
A receipt for the same VM/profile/protocol and each vCPU publication epoch
prevents duplicate setters, even when a new valid probe has a different slide.
A failure attempts to clear every vCPU and leaves
the VM paused; an uncertain transport is never automatically resumed or retried.
`--action disarm` explicitly clears all vCPUs using the same procedure. A VM that
was already paused remains paused, including during recovery.

`webkit-timeout-diagnostic.json` is the current public scope/hash/counter record;
private probe identity/publication state is kept under the run's private directory.
The current report/receipt schema is 2, with protocol
`runtime-pc-slide-armed-bool-v2`; old global-base receipts are rejected, not
migrated or overwritten. Run125's historical schema-1 records retain their
original meanings and are not evidence for this runtime-PC protocol.
Raw diagnostic uploads use private permissions. Both site counters have
per-vCPU baseline, publication and final snapshots. Current cumulative hits do
not identify a Safari PID or first GPU process: unchanged-epoch deltas bound an
interval, not a process. Record contamination if earlier Safari processes were
still alive, and retain independent process evidence for attribution.
`rejections` counts eligible candidate guard rejections, not Safari failures or
watchdog events; the old `failures` property is not consumed. QOM reads while
running are not atomic. The offline
`test-timeout-diagnostic.py`, the loopback `test-guest-files.py` path and runner
fixtures do not establish macOS probe execution or actual guarded site hits;
each new guest trial needs those runtime observations. Run128 provides a
successful probe and observed site0 guard; site1 remains unobserved in that
trial, despite its successful fixed-page readbacks.

## Collection and shutdown

Finish the ordinary observer and retain its archive/prepare/after receipts plus
the WebGL report, both raw buffers and the one-shot oracle result. For a
diagnostic run, take final counters while QEMU is still alive and the run is
still `observing`:

```sh
# Host; do this before initiating guest shutdown.
RUN_DIR=runs/e05-installed-boot/NEW_DIAGNOSTIC_LABEL
python3 -B experiments/E05-installed-boot/timeout_diagnostic.py \
  --run NEW_DIAGNOSTIC_LABEL --action final &&
  test ! -e "$RUN_DIR/webkit-timeout-before-shutdown.json" &&
  cp "$RUN_DIR/webkit-timeout-diagnostic.json" "$RUN_DIR/webkit-timeout-before-shutdown.json"
```

Then use ordinary guest shutdown and let the owning controller reap QEMU and the
file service. A dead VM cannot supply a new final counter read; retain an
unavailable boundary instead of promoting an earlier snapshot. Do not launch a
second controller, force a host quit, or rewrite a lifecycle record to obtain a
preferred shutdown classification. The existing five-second natural-exit wait
and later fallback cleanup remain distinct recorded outcomes.

Read each result at its own boundary:

| Evidence | Meaning and Run128 example |
| --- | --- |
| `results.json` configuration / `guest_files_service` | Requested profile and startup GET/PUT verification. `configured_unarmed`, `runtime_site_hits_observed=false` and `guest_executed=false` here are startup-only fields, not later live readouts. |
| `webkit-timeout-diagnostic.json` and phase records | Per-vCPU runtime counters, boolean publication and epochs. Global site hits do not by themselves identify Safari or prove both sites ran. Run128 observes site0, not site1. |
| `TAG-validation.json` | Full raw equality and page execution metadata for the fixed WebGL fixture. Run128 passes both 24,576-byte buffers; this is not scanout/FPS/general compatibility or original-deadline performance. |
| `results.json` `passed` / `collection_success` | Controller collection/cleanup contract, not the application oracle. Run128 remains false because its final screenshot was unavailable after QEMU exited, even though the WebGL oracle passes and raw errors are empty. |
| `qmp-lifecycle.json` / retained storage | Guest-origin event plus natural exit/reaping/no host quit can establish strict `guest_shutdown_observed`; it does not establish filesystem cleanliness. Run128 has strict guest shutdown and exit0/reaped, while `guest_clean_shutdown_proven=false` remains correct. |

Inspect the actual retained-target and latest-run evidence before any later
reuse. Never infer filesystem cleanliness from a complete trace, successful
upload, pixel equality or exit code alone. The completed Run128 record and its
bounded limitations are retained for maintenance; this guide does not authorize
an additional feature trial.
