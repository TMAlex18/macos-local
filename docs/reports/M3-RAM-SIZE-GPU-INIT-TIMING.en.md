> **Language:** **English** | [简体中文](M3-RAM-SIZE-GPU-INIT-TIMING.md)

# Effect of Guest RAM Size on Safari's First GPU Connection Initialization Time (From Run139)

Date: from 2026-11-11 UTC. The "Pre-run plan" section of this document was committed before the first run.

## Pre-run plan

### Motivation

Run138 ([host CPU sampling](M3-GPU-INIT-HOST-CPU-USE.en.md)) was interrupted before login and produced no trial data. Before rerunning it, previously unused guest-side records from the 14 trial archives of Run131–137 (`runs/ram-size-gpu-init-20261111/trials-4gib-runs131-137.json`) were compiled, yielding three findings:

1. **Guest memory is constantly full.** Before every trial, the 4 GiB guest had only 57–68 MiB of free pages left (210 MiB in one case), with 1.3–2.2 GiB stored in the memory compressor. Across a single ~145 s trial, the kernel added 34,000 to 106,000 page compressions (16 KiB per page, or 0.5–1.6 GiB), 31,000 to 68,000 decompressions, and 13,000 to 63,000 page-ins from disk. Swap files were unused.
2. **Initialization time covaries with compression count.** Across the 12 trials of Run132–137, the correlation coefficient between initialization time and compressions during the trial is 0.81, and with decompressions is 0.73. This is a post-hoc observation and is confounded with launch order: second launches are both faster and involve fewer compressions.
3. **Inside the window, the GPU process itself does very little computation.** Taking Run136's first trial as an example, across its 3.727 s window the GPU process's main thread initializes audio step by step: roughly 0.2 s preparing CoreAudio and voice processing, 0.2 s querying tccd for microphone permission, 0.9 s in CoreAudio HAL, 0.45 s in preferences and CoreServices, 0.27 s connecting to AudioComponentRegistrar, and 1.06 s from creating to destroying the SandboxHelper connection, followed 0.6 s later by Safari logging `didInitialize`. Meanwhile, `ps -M` snapshots recorded every few seconds by the probe show that over a 13–44 s span bracketing the window, that main thread used only 0.76–1.17 s of CPU in total — equivalent to 18%–39% of the window length. Thus for most of the window, the GPU process is waiting on replies from other processes.

When memory is tight, pages belonging to long-idle service daemons get compressed and must be decompressed when woken; memory needed by newly spawned processes (such as SandboxHelper) also has to be reclaimed by compressing other pages. Both compression and decompression are expensive under TCG. Memory pressure is therefore a concrete, directly testable candidate explanation for why waiting on other processes takes so long.

Two earlier 8 GiB controls (Run89, Run97) only checked whether the page rendered and did not measure this interval. Run89 succeeded on its first load under the original 3 s deadline, though the report at the time noted that a single observation could not establish the cause.

### Hypothesis

Memory pressure at 4 GiB lengthens initialization of the first GPU connection. Increasing guest RAM to 8 GiB eliminates heavy compression during the trial and noticeably shortens initialization time.

### Metrics and decision criteria

The primary metric remains `ui_request_to_did_initialize`, taken from the first Safari launch after boot. The 4 GiB reference consists of the six samples from Run132–137: 3.515–3.791 s. Because the two PAC algorithms showed no difference on this metric, they are pooled.

Run two 8 GiB rounds first (Run139, Run140).

| Outcome | Condition | Next step |
|---|---|---|
| Supported | First launch is below 3.515 s in both runs. Under the null of no RAM-size effect, the probability of this occurring by chance is 1/28 | If both runs are below 3.0 s, disable the diagnostic in the next 8 GiB run and test first load under the original 3 s deadline; otherwise report the reduction and return to host CPU analysis |
| Refuted | Neither run is below 3.515 s | RAM size is not the deciding factor. Return to 4 GiB and execute the host-sampled run |
| Inconclusive | One run is below and one is not | Run one more 8 GiB round and one same-day 4 GiB control |

Two manipulation checks apply: `hw.memsize` recorded in the trial must be roughly 8.58×10⁹; and compressions during the first trial must be below 7578 — one-tenth of the 4 GiB first-launch minimum of 75776. If the guest still compresses heavily at 8 GiB, the run fails to test the hypothesis and is reported as an invalid manipulation rather than evaluated against the table above.

Secondary metrics: second launch (4 GiB reference: 2.768–3.419 s), the four sub-segments of the window, time from observation script start to first GPU request, upper bound on GPU process main-thread CPU time, and host-side `vcpu_mean_busy`.

### Configuration and procedure

Configuration matches Run132, Run134, and Run136 (QARMA5 plus memo, with the 30 s diagnostic), changing only `--ram-gib 4` to `--ram-gib 8`. Run139 also passes `--allow-unclean-storage` because Run138 was terminated by signal. The procedure is likewise identical: first trial triggered at 720 s after boot, second at 1020 s.

Three additional differences relative to the 4 GiB reference are unrelated to RAM size:

- The host thread sampler and clock listener run in the background. The sampler only reads `/proc`.
- One clock probe runs after arming the diagnostic and another runs after the second trial. Each probe is a ~3 s TCP exchange, and the first finishes at least one minute before the first trial.
- After the second trial finishes and before shutdown, the guest uses `guest-diag/log-window.sh` to export unified system logs across all processes for both initialization windows, corresponding slices of launchd logs, a process list, and a memory-pressure summary. Each window waits at most 220 s before proceeding to normal shutdown if incomplete. Because this step runs after both trials have finished, it does not affect the metrics.

### Known limitations

- The 4 GiB reference was collected roughly 12 hours earlier rather than interleaved with 8 GiB. Host temperature and load differences are recorded rather than controlled.
- Sample counts per condition are very small; the decision rules resolve only large effects.
- All figures are collected under the 30 s diagnostic and do not equate to behavior under the original deadline.
- Moving to 8 GiB changes more than compression alone: the file cache is larger and background system activity may also shift. A change in the metric shows only that RAM size matters; identifying the mechanism relies on memory counters and logs.

### Pre-run revision (2026-11-11)

Run number Run139 did not produce a run. The controller refused before launching QEMU because temporary symlinks left behind when Run138 was interrupted by signal had not yet been cleaned up; the disk images were never opened. After cleaning up via `recover-abandoned.py --run m3-gpuinit-hostcpu-qarma5-20261111-138`, the two 8 GiB rounds were renumbered to Run140 and Run141 with everything else unchanged.

## Results

### Conclusion

8 GiB did not noticeably shorten initialization; memory pressure is not the main cause of those 3.5 s. This conclusion comes from a single run (Run140), which under the pre-registered clause qualifies as an invalid manipulation: at 8 GiB the guest was still compressing memory, merely much less. Accordingly, the decision table was not applied and no second round was run.

### Figures from Run140

| | 4 GiB (Run132–137) | 8 GiB (Run140) |
|---|---|---|
| First-launch initialization time | 3.515–3.791 s (6 samples) | 3.444 s |
| Second-launch initialization time | 2.768–3.419 s (6 samples) | 3.061 s |
| Page compressions during first trial | 75776–105748 | 30381 |
| Page decompressions during first trial | 49450–62632 | 11410 |
| Pages read in from disk during first trial | 32618–38322 | 29168 |
| Compressions / decompressions during second trial | 34145–85631 / 30898–60466 | 18023 / 5914 |
| Data in compressor before trial | 1.3–2.0 GiB | 0.26 GiB, 0.52 GiB |
| Free pages before trial | 57–68 MiB (once 210 MiB) | 66 MiB, 63 MiB |

- `hw.memsize` was 8579448832, confirming the guest saw 8 GiB. `memory_pressure` reported 88% system-wide memory free percentage. Free pages still sat just above 60 MiB because macOS uses the extra RAM as file cache.
- Compressions during the first trial totaled 30381, exceeding the manipulation-check ceiling of 7578, so this run does not represent a clean "zero-compression" condition. Even so, compressions dropped by 60%–71% and decompressions dropped by 77%–82%, while initialization time fell by only 0.07 s below the 4 GiB minimum and 0.22 s below the 4 GiB mean (3.66 s). Had compression and decompression been the main driver, initialization time would have dropped substantially alongside them. The 0.81 correlation noted under "Motivation" was mainly capturing the difference between first and second launches.
- Four sub-segments (first / second launch): GPU process receiving request to activating AudioComponentRegistrar connection 1.830 / 1.774 s; to creating SandboxHelper connection 0.132 / 0.150 s; SandboxHelper connection creation to destruction 0.887 / 0.880 s; destruction to `didInitialize` 0.579 / 0.252 s. All sit near the 4 GiB ranges.
- Observation script start to GPU process appearance took 47.8 / 32.2 s, and to first GPU request took 80.1 / 55.0 s, compared with 4 GiB same-configuration means (Run132, Run134, Run136) of 53.2 / 34.1 s and 92.1 / 58.6 s. The launch path sped up by roughly 10%, while the initialization window did not follow.
- Over the 25 s and 22 s spans bracketing the two windows, the GPU process main thread used at most 1.04 and 1.48 s of CPU (versus 0.76–1.17 s at 4 GiB).
- Both trials completed initialization with 0 unresponsive terminations, 2 diagnostic site0 hits, and 0 site1 hits.
- Run140 shut down from inside the guest and was recorded by the controller as a clean guest shutdown (exit code 0) — the first live-run verification of the controller fix (`be94290`). Host package temperature was min 53, mean 80.5, and max 92 °C.

### Two additional findings from this run

1. **The CPU is saturated inside the window.** Host sampling shows all 8 vCPUs are busy for roughly 90% of the initialization window. See "Results" in the [host CPU report](M3-GPU-INIT-HOST-CPU-USE.en.md) for details.
2. **The ~0.9 s SandboxHelper segment is the cost of spawning a new process, not a fixed wait.** launchd logs and system logs reconstruct this segment during the first trial (guest time 08:22:43):

   | Timestamp (s) | Event |
   |---|---|
   | 43.031 | GPU process creates SandboxHelper connection |
   | 43.040 | launchd decides to launch `com.apple.audio.SandboxHelper` |
   | 43.054 | `xpcproxy` process spawned |
   | 43.188 | Replaced by SandboxHelper binary; launchd logs "spawned" |
   | 43.469 | SandboxHelper writes its first log entry (dynamic linking and system library initialization complete) |
   | 43.566 | Begins compiling sandbox profile |
   | 43.805 | Kernel applies sandbox |
   | 43.815–43.875 | XPC listener and connection ready |
   | 43.918 | GPU process receives reply and destroys connection |

   In the process list, each SandboxHelper instance consumes 0.58–1.11 s of total lifetime CPU (0.75 s for the second trial's instance), matching the duration of this segment — showing that this segment is spent computing rather than waiting. Every process that uses audio components spawns its own SandboxHelper; 14 instances were present in the system by the end of the run.

### Evidence

- Run directory: `runs/e05-installed-boot/m3-gpuinit-ram8g-qarma5-20261111-140/`. Within it, `guest-uploads/r140logw1.tgz` and `r140logw2.tgz` contain the all-process logs for both windows, launchd log slices, process lists, and `memory_pressure` output.
- Summaries: `runs/ram-size-gpu-init-20261111/trials-4gib-runs131-137.json` and `trials-8gib-run140.json`, generated by `driver/mine.py` in the same directory.

### Limitations

- Only one run at 8 GiB, yielding two samples. "Did not noticeably shorten" means no difference large enough to alter the conclusion appeared; a small effect of 0.1–0.2 s is neither confirmed nor ruled out.
- The 4 GiB reference was collected roughly 13 hours earlier.
- A completely compression-free condition was not achieved. The controller accepts only 4 and 8 GiB.
