> **Language:** **English** | [简体中文](M3-RUN129-SMOKE.md)

# Run129: Smoke Run After Resuming Experiments (As-Is Boot, Login, and Shutdown)

Date: 2026-11-10 UTC. Tag: `m3-smoke-20261110-129`.

## Pre-run expectations

- **Purpose.** First boot of the installed system by a new operator after project handoff, to verify that the end-to-end workflow is operable. No new functionality is tested.
- **Configuration.** Identical to Run128, except without `--webkit-timeout-diagnostic` (i.e., using the normal 3 s deadline). Neither the runner nor the QEMU binary was modified (SHA-256 `4a433616…8a6b4d66`).
- **Expected outcome.** Boot to the login window; enter the password to reach the desktop; initiate shutdown inside the guest; `qmp-lifecycle.json` records a guest-originated shutdown and QEMU exits with status 0.
- **Figures to record.** Time from boot to login window (138 s in Run128), time from boot to desktop (225 s in Run128), and peak host CPU package temperature during the run.
- **Pre-run safeguard.** `root.raw` and `aux.raw` were copied to `private/safety-copies/20261110-before-run129/` and verified byte-for-byte against the originals.

## Results

All expectations held. The run started at 16:15:32 UTC and lasted 613 s.

| Stage | First observed at | Notes |
|---|---|---|
| Login window | 150.5 s | Still showing Apple logo and progress bar at 140.3 s. Run128 was 138 s |
| Password submitted | 165 s | Entered via `type-secret` |
| Dock and windows from previous session | 235 s | Run128 was 225 s |
| Menu bar | 373 s | Not yet present at 285 s and 345 s |
| Terminal restoration complete | 484 s | Showed a loading spinner inside the window prior to this |
| `sudo` password prompt | 556 s | Shutdown command typed after 516 s |
| Guest-originated SHUTDOWN event | 613 s | 44 s after confirming password |

- **Shutdown.** `qmp-lifecycle.json` recorded `SHUTDOWN` with `guest=true` and reason `guest-shutdown`; QEMU exited with status 0 and was reaped without any host-requested quit.
- **`results.json` still reports `passed=false`.** The error list is empty and the controller exited with code 1, for the same reason as Run125–128: a final screenshot cannot be captured after the guest has already shut down.
- **Host temperature.** Sampled every 5 s across 122 samples: CPU package temperature min 53 °C, mean 78.7 °C, max 96 °C, with 3 samples reaching or exceeding 90 °C. Recorded in `host-temperature.tsv` inside the run directory.

## Caveats

- **Timings are only roughly comparable.** Times in the table reflect when screenshots were taken rather than exact event timestamps. In addition, during the first 345 s after boot, the wait script contained a busy-wait loop that saturated one logical core on the host (out of 20); it has since been replaced with a blocking wait.
- **Safari was not tested.** The Safari window from the previous session was automatically reopened and remained a white page from 345 s until shutdown. No WebKit logs were collected in this run, so the cause cannot be attributed.
- **Clean filesystem unmount remains unproven.**

## Implications for subsequent experiments

- After login, it takes roughly 6 minutes before the restored Terminal accepts input. Session restoration consumed most of this run's duration.
- Safari is automatically reopened on every boot, right when the system is busiest. Before running Safari first-load timing experiments, Safari should be prevented from reopening automatically and the system should be allowed to settle first.
