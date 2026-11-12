# M3 Safari GPU follow-up — Run70 idle stability window

**Run:** `m3-safari-gpu-followup-qarma5-20261101-70`
**Window:** 2026-11-01 18:28:43–19:28:47 UTC
**Purpose:** bounded one-hour post-Run69 observation with the current installed normal OS, native-window/NVIDIA presentation configuration, current-process sampling, and periodic screenshots. This was an idle-stability follow-up, not a new Safari-trigger reproduction.

## Result

- Observation completed for 3600.321 seconds; collector passed; no runtime errors.
- Normal installed macOS boot was proven. Recovery boot was not observed.
- QEMU exited 0 and was reaped after the requested duration. Teardown was host QMP `quit`; no guest shutdown event was observed, so filesystem-clean shutdown remains unproven.
- 236 observer captures were retained, including 118 current-process samples over all 8 vCPUs and 61 periodic screenshots.
- The native-window/NVIDIA/30 Hz configuration was requested and recorded. GUI semantic verification was not performed (`gui_verified=false`); this run does not prove Safari behavior or FPS.
- No Safari UI action or GPUProcess failure was triggered in this run. Therefore Run70 neither confirms nor refutes the Run69 WebKit-watchdog diagnosis.

## Evidence and limits

- The normal OS identity, source volume/snapshot binding, kernel banner, and no-panic state were verified in `results.json`.
- Current-process sampling remained read-only and guest-writes were false.
- The final lifecycle record is `qmp-lifecycle.json`: host requested QMP quit, `guest_shutdown_observed=false`.
- The run used a fresh RAM/QEMU process and retained the installed target; it did not perform a restore upload.
- Run69 remains the causal Safari evidence. Run70 is only a bounded idle-stability and observer-collection result.

## Next step

Use a shorter, explicitly targeted GUI run to open Safari after login settles, capture GPUProcess launch-to-ready timing and thread/process observations, and preserve the same no-write/controlled-teardown boundaries.