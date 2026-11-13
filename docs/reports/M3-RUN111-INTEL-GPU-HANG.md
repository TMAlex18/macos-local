# M3 Run111 — host Intel GPU hang before system-wide collection

Run `m3-gpu-system-stackshot-20261107-111` used the existing
`93f6e7ea…b921db91` binary and 4 GiB/eight-vCPU/Intel/WAV-two-stream profile
after explicit unclean reuse of Run110's recovered storage. Normal macOS,
the native desktop and a current `R111_SHELL_READY` Terminal echo were reached.

At **2026-11-07 02:38:39 UTC**, the host kernel recorded
`i915 GPU HANG: ecode 12:1:85def5fb` in QEMU PID 28361, followed by a reset
of that context. Reims reported Vulkan `DeviceLost` and QEMU aborted with
exit -6. The host stayed running. This is a recorded host GPU-context failure,
separate from Run110's whole-host reboot; their relationship and the triggering
shader/command remain unknown. The complete 4,216,719-byte error capture is
kept privately, with its hash and kernel messages in `host-gpu-error.json`.

The intended guest helper, local spindump help and system-wide capture did
not run. An initial restored-Terminal command was partial/failed; after the
successful nonce echo, the next command lost its connection while being
typed, before Return was sent. There is no proof of help or `caffeinate`/keepawake
execution. `guest-uploads/` is empty. `net-help.json` records RX/TX/control
used indices 275/315/12, which establish queue progress, not guest HTTP.

The runner finalized at **02:38:41.046358 UTC** with QEMU -6/reaped,
controller exit 1, `errors=[]`, file-service exit 0/reaped/stopped and
temporary links removed. No QMP lifecycle events were recorded. Collection
failed; guest-origin shutdown and filesystem cleanliness are unproven.
This finalized failure is the latest completed run; the last verified
guest-origin shutdown remains Run109.

Evidence is in
[`application-observation.json`](../../runs/e05-installed-boot/m3-gpu-system-stackshot-20261107-111/application-observation.json),
[`results.json`](../../runs/e05-installed-boot/m3-gpu-system-stackshot-20261107-111/results.json),
[`host-gpu-error.json`](../../runs/e05-installed-boot/m3-gpu-system-stackshot-20261107-111/host-gpu-error.json),
`net-help.json`, `ui-actions.jsonl`, and the named captures in the same run
directory. The subsequent NVIDIA control and current work are tracked in
[STATUS](../STATUS.md); changing the GPU is not yet a verified fix.
