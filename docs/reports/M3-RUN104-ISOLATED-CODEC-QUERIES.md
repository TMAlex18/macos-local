# M3 Run104 — isolated codec queries and fresh Safari control

Run `m3-isolated-opus-query-20261106-104` used the existing 4 GiB,
eight-vCPU, Intel native-window, QARMA5/PAC memo profile. The optional
periodic owner-stop/current-process observer was disabled. Normal installed
macOS, login and the native desktop were verified.

## A persistent self-authored probe reached the actual query

The self-authored arm64 Mach-O executable was cross-built without an Apple
SDK. Guest `codesign` verification and actual loading/execution succeeded.
Version 1 performed an Opus `AudioComponentFindNext` query; version 2 queried
VP9 and AV1 hardware capability, then Opus and Vorbis components in that order.

| Ordinary-process workload | First wall time | First CPU time | Repeated wall time |
|---|---:|---:|---:|
| Framework load only | 0.512785791 s | 0.393507 s | — |
| Opus lookup, no sampling | 0.463943750 s | 0.239736 s | 0.251333 ms |
| Opus lookup, sampled | 0.354031458 s | 0.162494 s | 0.154125 ms |
| VP9 → AV1 → Opus → Vorbis, total | 0.662734916 s | 0.571154 s | 0.849291 ms |

Both Opus trials found the component. The sampled trial returned probe/sample
exit 0 and contains eight `AudioComponentFindNext` samples covering registrar
connection and registry deserialization. Unlike the earlier dying-GPU trials,
this provides real query-window stack evidence in a process kept alive for
observation.

The four-query version returned hardware capability false for VP9 and AV1,
and component-found true for Opus and Vorbis. Hardware false is a valid
unsupported result, not a failed query. These are **first-per-process**
measurements with warm global/server caches. The probe does not reproduce
`enableMediaPlayback`, the Safari GPU sandbox or the full GPU initialization
sequence. Sampling is an intervention; the two Opus timings are not a causal
comparison. The results neither establish nor exclude an audio-related cause
of Safari's timeout.

Binary SHA-256 values are retained with source hashes and each trial in
`application-observation.json`: version 1 `5fb098b1…fb96584`, version 2
`db739949…5d5472`. The exact v1 source, wrapper and build inputs are retained
under `probe-source-v1/`; the v2 build metadata is in
`build.json`. Each recorded build
verified 13 ad-hoc CodeDirectory page hashes. The six-case wrapper validation
is a reused Linux orchestration baseline with probe/sample/codesign/HTTP
substitutes; guest loader and query claims come from this run's real archives.

## Fresh Safari still failed without the periodic stop observer

`r104fresh` confirmed Safari exit before relaunch and used neither stack
sampling nor the periodic owner-stop observer. It recorded six GPU watchdog
termination requests, and `r104-fresh-safari-result.png` shows an error page
rather than the authored RGB page. This proves the failure can occur without
that optional observer. It does not measure the observer's quantitative
effect or identify an audio-only cause.

`guest-uploads/r104-virtio.txt:135–141` contains the installed
`AppleVirtIOSound` personality, provider `AppleVirtIOTransport`, user client
`AppleVirtIOSoundUserClient`, and `IOVirtIOPrimaryMatch=0x00191af4`, matching
standard VirtIO device type 25. Run104 had no VirtIO sound device and did
not establish driver binding or a CoreAudio default device. The subsequent
[Run105](M3-RUN105-VIRTIO-SOUND-BINDING.md) records that separate integration
result; current work is tracked in [STATUS](../STATUS.md).

## Lifecycle and evidence

At 19:22:31 UTC the owned QMP connection recorded guest-origin shutdown.
QEMU exited 0 and was reaped, with no host quit request; the file service
stopped and exited 0/reaped. The collector remains false because its final
capture was unavailable after natural guest exit, with `errors=[]`.
Full filesystem cleanliness remains unproven.

Authoritative trial events, hashes and scopes are in
`application-observation.json`.
Lifecycle evidence is in
`results.json`
and `qmp-lifecycle.json`. The same directory's `guest-uploads/` retains
`r104load.tgz`, `r104query.tgz`, `r104sample.tgz`, `r104codecs.tgz`,
`r104fresh.tgz` and `r104-virtio.txt`; the saved v1 build is
`probe-source-v1/build.json`.
