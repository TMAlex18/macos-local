> **Language:** **English** | [简体中文](M3-PAC-ALGORITHM-GPU-INIT-TIMING.md)

# Effect of the PAC Algorithm on Safari's First GPU Connection Initialization Time (From Run130)

Date: from 2026-11-10 UTC. The "Pre-run plan" section of this document was committed before the first run.

## Pre-run plan

### Problem

In a freshly launched Safari, initializing the first GPU connection misses WebKit's 3 s deadline. Under the default-off 30 s diagnostic, Run127 took 4.081 s and Run128 took 3.781 s. That means being 20% to 30% faster would suffice to finish within the original deadline.

Host profiling shows that even with PAC memo enabled, PAC emulation still accounts for roughly 23% of vCPU time (Run114). QEMU's impdef algorithm adds 47.6 ns of overhead per call, compared with roughly 214 ns for optimized QARMA5. impdef was only tried early in the restore phase on 2026-10-29 and has never been measured on application workloads in the installed system.

### Hypothesis

Switching the PAC algorithm from QARMA5 plus memo to impdef will shorten the initialization time of the first GPU connection.

### Metric

`ui_request_to_did_initialize`: seconds elapsed from Safari logging "web process requested a GPU connection" to its logging `GPUProcessConnection::didInitialize`, taken from the first GPU process of the first Safari launch after boot. Computed from the guest-uploaded log bundle by `experiments/E05-installed-boot/gpu_init_timing.py`, which produces 4.081 and 3.781 on the archived data from Run127 and Run128.

Sub-segment durations, unresponsive termination counts, WebProcess unresponsive notification counts, host CPU package temperature, and thermal throttle counts are also recorded.

### Two configurations

| Label | PAC settings | Everything else |
|---|---|---|
| A | `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats` (QARMA5) | Same as Run128, with `--webkit-timeout-diagnostic` |
| B | `--pauth-algorithm impdef` (cannot be combined with memo) | Same as above |

The binary is unchanged (SHA-256 `4a433616…8a6b4d66`).

### Per-run procedure

1. Boot, log in, and wait for session restoration to complete.
2. Confirm Safari is not running (`pgrep -x Safari` exit code 1). If it was automatically reopened, void this run's sample and treat the run strictly as a rehearsal.
3. Run the identity probe in the guest, arm the diagnostic on the host, and save counter snapshots before and after arming.
4. Execute `safari-probe.sh` at roughly 12 minutes after boot using the default local RGB page.
5. After the collection bundle uploads, read the final counters and compute the metric with `gpu_init_timing.py`.
6. Quit Safari and confirm it has exited before shutting down, so the next run does not automatically reopen Safari.

### Order and sample size

Safari was open when Run129 shut down, so it will be automatically reopened at login in Run130, consuming the cold-launch condition. Run130 uses configuration A to rehearse the full workflow, and its sample is excluded from the comparison. Thereafter, collect 2 samples of each in the order A, B, A, B.

### Decision criteria

- **Clear effect:** both B samples are smaller than both A samples.
- **Sufficient:** both B samples are below 3.0 s. In that case, append one additional B run without the diagnostic to test whether the first load passes under the original deadline.
- **No effect:** the ranges of the two groups overlap. First add 1 sample to each group; if they still overlap, conclude that the PAC algorithm is not the deciding factor and move on to the next item from host profiling (MMU, TB lookup).

### Known limitations

- With only 2 samples per configuration, only large differences can be detected.
- Diagnostic hit counters cannot be attributed to specific processes; the metric comes from WebKit's own log timestamps with millisecond precision.
- Booting a system restored under QARMA5 on impdef is itself a first attempt; if it fails to boot, that too is a result.
- Host temperature reached 96 °C in Run129, and thermal throttling could affect timing, so throttle counts are recorded on every run.

## Pre-run revision (2026-11-11 UTC, before any counted sample)

### Run130 interrupted; rehearsal moved to Run131

Run130 was interrupted 728 s after boot: the session owning the controller ended, and the controller received SIGTERM and terminated QEMU. Login had completed, no probe had been executed yet, and no sample was produced. Because the guest did not shut down from within, the next boot requires `--allow-unclean-storage`.

Accordingly, the rehearsal moves to Run131 (configuration A, plus `--allow-unclean-storage`), and the counted samples move to Run132 (A), Run133 (B), Run134 (A), and Run135 (B). The order and decision rules remain unchanged.

### Baseline condition differs from this experiment's primary metric

Re-examining the file-server logs from Run127 and Run128 revealed that in both runs Safari had been automatically reopened at login: it first fetched the previous page, was then quit and confirmed exited, and only afterward did the trial begin (starting at roughly 16.9 and 14.7 minutes after boot, respectively). Thus 4.081 s and 3.781 s are figures for the *second* Safari launch within a boot, not the first launch after boot.

This experiment's primary metric remains the first launch after boot (without Safari having been automatically reopened). That condition is colder, so its numbers are expected to exceed the baseline and cannot be compared directly against 4.081 and 3.781.

In the rehearsal run, Safari will be automatically reopened, matching the condition of Run127 and Run128. Its figure is excluded from the A-vs.-B comparison, but serves to check whether the workflow reproduces the baseline (expected around 3.8 to 4.1 s).

### Adding a secondary metric

After the first trial finishes in each counted run, `safari-probe.sh` is executed a second time within the same boot (with suffix `b` on the tag). The script quits Safari and confirms it has exited before launching a fresh Safari instance. Each run thus yields an additional "second launch" figure under the same condition as the baseline.

The decision criteria still use only the first-launch figures; second-launch figures are reported separately. If both second-launch samples of B are below 3.0 s, the follow-up diagnostic-off B run will also include a second launch.

### Trigger time

The trigger time for the first trial is fixed at a constant number of seconds after boot across all four counted runs. The planned value is 720 s. If the rehearsal shows the preparation steps cannot finish in time, it will be updated to a later fixed value before the first counted run and recorded here.

### Procedure finalized after the rehearsal (Run131)

The rehearsal started at 2026-11-11 02:19 UTC and ended cleanly via guest shutdown after 1955 s. It yielded two configuration-A samples under the same condition as the baseline:

| Trial | Trigger time (after boot) | `ui_request_to_did_initialize` | Unresponsive terminations |
|---|---|---|---|
| `r131safari` | 693 s | 4.866 s | 0 |
| `r131safaric` | 1647 s | 3.802 s | 0 |

Together with the baseline's 4.081 and 3.781, configuration A spans 3.78 to 4.87 s under this condition. Samples from the same configuration can differ by 1 s, so 2 samples per group can only detect differences larger than that.

The rehearsal also settled three operational details:

- Preparation finishes by roughly 690 s after boot. The trigger time is fixed at 720 s for the first trial and 1020 s for the second.
- The second trial cannot rely on the script itself to quit Safari. During the rehearsal, the script polled for the process while Safari was still exiting, exceeded its own 3 s limit, and aborted by design (`r131safarib`, no sample). Instead, Safari is quit manually and `pgrep -x Safari` exit code 1 is confirmed before invoking the script, matching the baseline procedure.
- Leave at least 3 minutes between runs for the host to cool down, and record thermal throttle counts before and after every run. During the rehearsal, package temperature peaked at 96 °C and the throttle counter increased by 9891; one minute after VM exit, temperature returned to roughly 50 °C.

One operator error occurred during the rehearsal. After the first trial, Safari was in the foreground, and a command intended for Terminal was typed into Safari's address bar, triggering a Google search whose query was that literal command (`sh /tmp/safari-probe.sh r131safarib`), containing no private information. From then on, before typing any command after Safari has launched, Terminal is clicked first and the menu bar is verified on a screenshot.

## Results

### Conclusions

1. **The PAC algorithm is not the deciding factor in first GPU connection initialization time.** On the first Safari launch after boot, the three samples for configuration A (QARMA5 plus memo) are 3.791, 3.515, and 3.727 s, and for configuration B (impdef) are 3.726, 3.693, and 3.515 s. The two ranges overlap and their means differ by only 0.03 s. Under the pre-registered rules, the hypothesis does not hold.
2. **Not a single first-launch sample fell below 3.0 s.** All six samples lie between 3.515 and 3.791 s, still 15% to 20% short of WebKit's original deadline.
3. **A second launch within the same boot is faster and lands near the 3 s mark.** Configuration A gave 3.348, 3.419, and 2.768 s; B gave 2.907, 3.018, and 3.009 s — again overlapping. The 2.907 s (Run133) and 2.768 s (Run136) readings are the first sub-3 s initialization times measured in this project.
4. **impdef boots and runs a system restored under QARMA5 cleanly.** All three impdef runs booted, logged in, ran Safari, and shut down cleanly — the first time the installed system has run under impdef.
5. **impdef speeds up Safari's overall launch path by 11% to 21%, while leaving the ~3.5 s initialization window untouched.** This finding comes from post-hoc analysis outside the pre-run plan; see "Post-hoc analysis" below.

All 14 trials completed initialization under the 30 s diagnostic, with zero unresponsive GPU process terminations reported by Safari. All figures were obtained under the diagnostic and do not represent passes under the original 3 s deadline.

### Data

Both primary and secondary metrics are `ui_request_to_did_initialize` in seconds. The first trial was triggered at 720 s after boot and the second at 1020 s (except Run131).

| Run | Config | Boot (UTC, 11-11) | First launch | Second launch | site0 hits | Package temp min / mean / max °C | Throttle count delta | Teardown |
|---|---|---|---|---|---|---|---|---|
| 131 | A (rehearsal) | 02:19:19 | 4.866 (693 s trigger, baseline condition) | 3.802 (1647 s trigger) | 2 | 58 / 80.2 / 96 | 9891 | Guest shutdown |
| 132 | A | 02:54:57 | 3.791 | 3.348 | 2 | 52 / 79.9 / 92 | 1996 | Guest shutdown |
| 133 | B | 03:19:40 | 3.726 | 2.907 | 2 | 57 / 79.7 / 93 | 1096 | Guest shutdown (record misclassified; see below) |
| 134 | A | 03:44:38 | 3.515 | 3.419 | 2 | 58 / 80.4 / 95 | 1439 | Guest shutdown |
| 135 | B | 04:09:15 | 3.693 | 3.018 | 2 | 56 / 80.0 / 93 | 2385 | Guest shutdown (record misclassified) |
| 136 | A | 04:34:14 | 3.727 | 2.768 | 2 | 51 / 80.7 / 95 | 9542 | Guest shutdown |
| 137 | B | 04:59:10 | 3.515 | 3.009 | 2 | 50 / 80.5 / 95 | 3367 | Guest shutdown (record misclassified) |

Every trial constructed exactly 1 GPU process, with 0 unresponsive terminations, 0 site1 hits, and 0 diagnostic rejections. WebProcess unresponsive notifications occurred 3 times in Run131's first trial, 0 times in Run136's second trial, and 1 time in every other trial. In every counted run, Safari was confirmed not to have been automatically reopened after login (`pgrep -x Safari` exit code 1).

| Summary statistics (Run132–137) | A | B |
|---|---|---|
| First launch: mean (range) | 3.678 (3.515–3.791) | 3.645 (3.515–3.726) |
| Second launch: mean (range) | 3.178 (2.768–3.419) | 2.978 (2.907–3.018) |

### Decision walkthrough

- After the first four counted runs, first-launch times were A: 3.791, 3.515 and B: 3.726, 3.693. Because B's 3.726 fell inside A's range, "Clear effect" did not hold.
- Following the rule, 1 sample was added to each group (Run136, Run137). With those included, A spanned 3.515–3.791 and B spanned 3.515–3.726 — still overlapping. Conclusion: the PAC algorithm is not the deciding factor.
- The "Sufficient" condition (both B samples below 3.0 s) did not occur on the primary metric. On the secondary metric, B's first two samples were 2.907 and 3.018, which also failed "both below 3.0 s", so no diagnostic-off run was appended.
- On the secondary metric, the first four runs appeared to show a separation (both B values below both A values), which the third A sample (2.768) refuted — illustrating the exact false positive risk of taking only 2 samples per group.

### Sub-segments

Mean duration of each sub-segment, in seconds:

| Sub-segment | First launch A | First launch B | Second launch A | Second launch B |
|---|---|---|---|---|
| GPU process receives connection request → activates AudioComponentRegistrar connection | 1.845 | 1.753 | 1.738 | 1.544 |
| → creates SandboxHelper connection | 0.248 | 0.228 | 0.201 | 0.158 |
| → destroys SandboxHelper connection | 0.998 | 1.055 | 0.950 | 0.945 |
| → `didInitialize` | 0.579 | 0.602 | 0.284 | 0.324 |

- SandboxHelper connection creation to destruction ranged from 0.771 to 1.084 s across all 14 trials, averaging 0.992 s, with 13 of 14 trials between 0.90 and 1.09 s. It varies neither with the PAC algorithm nor with launch order.
- Second launches are faster than first launches mainly in the final sub-segment (roughly 0.58 → 0.30 s).
- The first sub-segment is slightly shorter under impdef, by less than run-to-run variation.

### Post-hoc analysis: overall launch path (unplanned)

The timeline inside each collection bundle logs when the observation script started (1 s resolution), and WebKit logs record the timestamps of subsequent events, both on the guest clock. This yields two intervals, in seconds:

| Interval | Launch order | A (mean) | B (mean) | B vs. A |
|---|---|---|---|---|
| Observation script start → GPU process construction | First | 52.7, 53.9, 53.0 (53.2) | 46.5, 46.6, 47.2 (46.8) | −12% |
| | Second | 32.4, 35.1, 34.7 (34.1) | 27.9, 27.2, 25.5 (26.9) | −21% |
| Observation script start → first GPU connection request | First | 88.7, 92.5, 95.2 (92.1) | 78.4, 76.3, 78.5 (77.7) | −16% |
| | Second | 59.8, 58.6, 57.4 (58.6) | 53.1, 53.0, 50.6 (52.2) | −11% |

Across all four comparisons, every one of B's three samples is smaller than every one of A's three samples. Because these intervals include the observation script's own fixed sleeps, the relative speedup on the compute portion is larger than the percentages shown in the table.

Taken together with the primary metric: the path that spawns Safari, WebContent, and the GPU process is sensitive to PAC overhead, whereas the initialization window is not. One plausible explanation is that time inside the initialization window is spent mostly waiting (helper process startup, cross-process round-trips) rather than in PAC-heavy computation. That remains a hypothesis not measured by this experiment.

Because this analysis was performed after inspecting the data using unplanned metrics, it serves only as a lead for the next step.

### Deviations and issues during execution

- **Run130 interruption** and **Run131 rehearsal** are covered under "Pre-run revision" above.
- **The controller misclassified three clean guest shutdowns as host-requested termination (Run133, Run135, Run137).** In all three runs, the guest emitted a shutdown event and QEMU exited with status 0. There is a brief window between QEMU closing the QMP socket and process exit; the controller sends a polling request every 15 s, and if that send lands inside the window, sending fails while the guest's SHUTDOWN event is still unread in the receive buffer. Teardown logic therefore did not know the guest had already shut itself down, took the host-termination path instead, and recorded `teardown=terminate`, `guest_shutdown_observed=false`. As a consequence, the following run had to pass `--allow-unclean-storage` (added in Run134 and Run136, and likewise required after Run137). All three occurrences happened on impdef runs and none on the four configuration-A runs; both configurations execute the exact same controller code, and this correlation is currently unexplained.

  This bug was fixed after the experiment concluded: when sending a request fails, the session first drains whatever QEMU already sent before closing the socket (without sending anything further); if a guest shutdown event is present, it waits up to 5 s for QEMU to exit on its own and records the run as a clean guest shutdown. When no shutdown event is present, it still treats the failure as an error. Regression tests live in `test-qmp-lifecycle.py` and `test-offline.py`, and fail if the fix is reverted. The raw records of Run133, Run135, and Run137 were left unmodified.
- **Desktop progress at the same boot age varied across runs.** At 345 s after boot, the wallpaper had finished loading in some runs while remaining black in others; this occurred under both configurations and cannot be used to compare speed.
- **Host temperature.** Peak package temperature reached 92–96 °C in every run, with means around 80 °C. Across roughly 2.7 hours of continuous VM runs from Run131 through Run137, the host did not reset. With a 3-minute cooldown gap between runs, pre-boot package temperature ranged from 48 to 61 °C.

### Limitations

- 3 samples per group. Samples within the same configuration vary by 0.3 to 0.65 s, so smaller effects cannot be resolved.
- All figures were collected under the 30 s diagnostic. Completing initialization in under 3 s with the diagnostic enabled does not guarantee avoiding the watchdog under the original deadline.
- The six counted runs were executed back-to-back on a single night alternating A and B; host conditions were comparable, though the order was not randomized.
- Second-launch and full-launch-path numbers are influenced by caches warmed during the first trial and are comparable only within the same launch order.

### Next steps

1. The hypothesis that switching PAC algorithms would bring the first load within the 3 s deadline has been refuted. As planned, pivot to host profiling, starting with a narrower question: where does QEMU spend those 3.5 s during initialization? Profile QEMU from the host during a trial, slice out the initialization window using WebKit log timestamps, and compare it against the profile of the overall launch path. If address translation or code translation dominates the window, optimize those; if vCPUs are mostly idle, the guest is waiting and a different approach is needed.
2. The SandboxHelper segment consistently sits around 1 s and warrants isolating whether it reflects helper process startup cost or a fixed timeout.
3. Future performance experiments can use impdef directly: it boots the installed system cleanly, accelerates the launch path, and requires no memo table. Experiments that must compare directly against earlier figures should continue using QARMA5 plus memo.

### Evidence

Per-run directories are `runs/e05-installed-boot/m3-gpuinit-*-2026111[01]-13[0-7]/`. Within each, `guest-uploads/` holds the trial collection bundles, preparation logs, and Safari-not-running checks; `gpu-init-timing-*.json` holds computed timing results; `webkit-timeout-*.json` holds diagnostic counters across phases; and `host-temperature.tsv` holds host temperatures. Summary tables, driver scripts, and run notes are in `runs/pac-gpu-init-timing-20261111/`.
