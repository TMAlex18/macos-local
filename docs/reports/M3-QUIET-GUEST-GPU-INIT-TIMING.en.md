> **Language:** **English** | [简体中文](M3-QUIET-GUEST-GPU-INIT-TIMING.md)

# Effect of Background Load on Safari's First GPU Connection Initialization Time (From Run141)

Date: from 2026-11-11 UTC. The "Pre-run plan" section of this document was committed before the run.

## Pre-run plan

### Motivation

Run140's host sampling showed that from 90 s after boot until shutdown, the guest's 8 vCPUs are almost continuously all busy, driven mainly by post-login user-session background work; without logging in (Run138), an average of only 2.3 vCPUs were busy. All 16 initialization-time samples collected to date were obtained under that load. See the [host CPU report](M3-GPU-INIT-HOST-CPU-USE.en.md).

### Hypothesis

Initialization time is stretched by post-login background work. Waiting for the guest to quiet down before launching Safari will bring the initialization time of the first launch after boot below 3.0 s.

### Procedure

- Configuration matches Run132, Run134, and Run136: QARMA5 plus memo, 4 GiB, with the 30 s diagnostic. The host thread sampler and clock listener run in the background.
- After login, run `caffeinate -dimsu -t 5400 &` in Terminal to prevent screen lock during the long unattended wait.
- Once per minute, compute `vcpu_mean_busy` over the preceding minute. Three consecutive 1-minute windows below 3.0 count as "quiet". The time limit is 2700 s after boot.
- Once quiet is reached or the time limit expires: execute the same pre-trial preparation as before (confirm Safari is not running, verify identity, arm the diagnostic, run the clock probe, and pre-type the command), press Return 240 s after preparation finishes for the first trial; wait another 300 s for the second trial; then export the log windows and shut down.

### Decision criteria

The primary metric is `ui_request_to_did_initialize` on the first trial. All previous first-launch samples lie between 3.444 and 3.791 s (7 samples, including the 8 GiB one).

| Case | Condition | Meaning and next step |
|---|---|---|
| Supported | Quiet reached, baseline 25 s to 5 s before pressing Return is below 3.0, and initialization is below 3.0 s | Background contention is the main cause. Next run: disable the diagnostic on a quiet guest and test first load under the original 3 s deadline |
| Partial | Quiet reached, baseline below 3.0, and initialization is between 3.0 and 3.444 s | Background load contributes, but does not explain everything. Decide next step together with `vcpu_mean_busy` inside the window and blocking samples |
| Refuted | Quiet reached, baseline below 3.0, and initialization is at least 3.444 s | Background contention is not the main cause. Pivot to the QEMU side: vCPU-thread blocked time and function-level profiling |
| Untested | Still not quiet at the time limit, or baseline is at least 3.0 | Run the trials anyway as a sampled 4 GiB reference; report how long background load lasted at minimum. Next step: consider suspending background processes during trials |

Regardless of outcome, report: background load over time, time taken to reach quiet, and `vcpu_mean_busy` inside the window.

### Exploratory recordings (excluded from decision)

In Run140, even "all-busy" vCPU threads spent roughly 22% of their time blocked for unknown reasons. Because `sudo -n` is available in this session, also record:

- Blocking samples: read `/proc/PID/task/TID/syscall` across all 8 vCPU threads as root every 10 ms to determine whether each thread is running or blocked in a given syscall and futex address, mapping addresses via the QEMU binary's symbol table to `bql`, `exclusive_resume`, and so on. This interface does not pause threads. Capture 40 s during the saturated phase while waiting, 40 s after quiet is reached, and 70 s spanning the second trial's initialization window.
- perf samples: 15 s during the saturated phase and 15 s after quiet is reached (`perf record -F 499 -g -p PID`).

Neither capture runs during the first trial; only the same thread-counter sampler as Run140 runs then.

### Known limitations

- Only one run and a single primary sample.
- "Quiet" cannot be separated from "longer uptime": waiting longer warms file caches further and alters memory state.
- Root-level sampling runs during the second trial, so its timing figures are for reference only.
- The guest sustains heavy load throughout the waiting period; host temperature is recorded as usual.

### Pre-run revision (2026-11-12, after Run141 interruption and before any trial)

Run141 was terminated 672 s after boot with no trials. One of the operator commands was interrupted, causing the controller to receive SIGTERM and terminate QEMU (`teardown=terminate`, QEMU exit code 0). Only 6.5 minutes had elapsed since guest login, and the guest had remained saturated throughout (per-minute `vcpu_mean_busy` of 5.1–5.8). The rerun uses Run142 with `--allow-unclean-storage`, keeping everything else unchanged.

Before termination, two of the planned exploratory captures had already completed during the saturated phase. Sample distribution across the 8 vCPU threads:

| | Capture 1 (490–530 s after boot, 40 s) | Capture 2 (657–672 s, 15.5 s, with call stacks) |
|---|---|---|
| Running or runnable | 78.4% | 74.3% |
| Waiting for QEMU global lock (`bql`) | 16.6% | 8.8% |
| Waiting for another vCPU's exclusive section to end (`exclusive_resume`) | 4.0% | 14.3% |
| Initiating an exclusive section, waiting for other vCPUs to stop (`exclusive_cond`) | 0.5% | 2.1% |
| Waiting for `qemu_cpu_list_lock` | 0.6% | 0.5% |
| Idle (parked on own halt condition variable) | 0.0% | 0.0% |

This explains Run140's observation that even when all busy, each vCPU spent only about 76% of the time on a host CPU: the remaining time is not idle, but stalled on two internal QEMU synchronization mechanisms. Call stacks show that exclusive sections are initiated almost entirely by queued "safe work" (`process_queued_cpu_work` → `start_exclusive`), with TLB range flush functions appearing on the stack alongside them — pointing to guest broadcast TLB invalidation instructions, which QEMU implements by stopping all vCPUs. Global-lock waits occur primarily when a vCPU thread reacquires the lock after exiting the execution loop (`mttcg_cpu_thread_fn`), followed by reads of IO-marked system registers (`helper_get_cp_reg64`) and interrupt handling. Although the split between the two lock types fluctuates substantially over time, their combined share is 21%–26% in both captures.

These exploratory findings do not alter the hypothesis or decision criteria of this plan. Capture files are in `runs/e05-installed-boot/m3-gpuinit-quiet-qarma5-20261111-141/` (`vcpu-block-saturated.txt`, `vcpu-block-saturated-stacks.txt`, `perf-saturated.data`).

## Results (Run142)

### Conclusion

By the decision table, the result is **partial**: once the guest quieted down, first-launch Safari initialization after boot took 3.249 s — lower than every previous first-launch sample (3.444–3.791 s), but still not below 3.0 s.

More instructive is why: **the guest was quiet before pressing Return, yet the initialization window saturated anyway**. Launching Safari saturates all 8 vCPUs by itself. Waiting for background work to subside therefore removes only part of the contention, not the contention generated by the launch itself. And under that saturation, vCPU threads spend 10% to 25% of their time stalled on QEMU's own global lock.

### How long background load takes to subside

Login occurred at 162 s after boot. Thereafter, per-minute `vcpu_mean_busy` stayed between 4.9 and 6.1 through minute 20, dropped to 3.6 in minute 21, and settled at 1.3–2.4 from minute 22 onward. The criterion of three consecutive minutes below 3.0 was satisfied at 1512 s after boot. In other words, post-login background work runs for roughly 20 minutes at this emulation speed. Host package temperature dropped accordingly from 80–87 °C to 52–54 °C.

Every trial in Run131–140 (12 to 28 minutes after boot) fell inside or just past this period.

### The two trials

| | First launch | Second launch |
|---|---|---|
| Time Return was pressed (after boot) | 1836 s | 2136 s |
| Baseline `vcpu_mean_busy` 25 s to 5 s before pressing Return | 1.28 (92% of the time only one or zero vCPUs busy) | 1.47 |
| `ui_request_to_did_initialize` | **3.249 s** | **2.588 s** |
| Previous range of comparable samples (under load) | 3.444–3.791 s (7 samples) | 2.768–3.419 s (7 samples) |
| Four sub-segments | 1.788 / 0.253 / 0.762 / 0.390 s | 1.437 / 0.108 / 0.829 / 0.210 s |
| `vcpu_mean_busy` within window | 5.96 (all 8 busy 89%) | 6.13 (91%) |
| GPU process appearance to connection request (~34 s) | 5.15 (all-busy 67%) | 5.06 (all-busy 66%) |
| Observation script start to GPU process appearance / to first GPU request | 51.6 / 85.4 s | 27.8 / 61.3 s |
| Page compressions / decompressions during trial | 68496 / 48781 | 24427 / 23851 |

- Both trials completed initialization with 0 unresponsive terminations of Safari's GPU process.
- Three GPU processes existed during each trial: alongside Safari's, two `nsattributedstringagent` instances were also using WebKit, each spawning its own web process and GPU process whose GPU connection initialization took 2.7–4.3 s. Trials in Run131–140 had only Safari's single GPU process. Those background tasks appear to have waited until the system became idle to start, ending up overlapping Safari's launch. Diagnostic site0 consequently hit 6 times in total.
- The launch path (observation script start to first GPU request) was only 7% faster than under load (85.4 s vs. a mean of 92.1 s), further confirming that the launch itself is CPU-bound.
- The guest shut itself down cleanly and the controller recorded a guest shutdown (exit code 0). Total runtime was 41 minutes, with package temperature min 52, mean 77.1, and max 91 °C.

### Where vCPU threads stall (exploratory)

| Interval | Running or runnable | Idle | Waiting on global lock `bql` | Waiting on exclusive section |
|---|---|---|---|---|
| Saturated phase (419–459 s after boot) | 81.0% | 0.0% | 14.8% | 3.6% |
| Quiet phase (after preparation, 40 s) | 20.0% | 78.7% | 0.8% | 0.5% |
| Second trial: 20 s before connection request | 70.5% | 0.0% | 26.0% | 3.1% |
| Second trial: initialization window (2.6 s) | 86.4% | 0.0% | 10.2% | 2.8% |

Combined with the two captures from Run141 (see revision above), the picture is consistent: whenever the guest is busy, vCPU threads spend 14%–30% of their time waiting on QEMU synchronization, with the global lock usually accounting for the bulk of it. In the call stacks, global-lock waits occur, in descending order, when a vCPU thread reacquires the lock upon exiting the execution loop (`mttcg_cpu_thread_fn`, roughly half), when reading IO-marked system registers (`helper_get_cp_reg64`), and when handling interrupts and queued work. Exclusive sections come from queued "safe work", which QEMU uses to implement guest broadcast TLB invalidations (`tlb_flush_*_all_cpus_synced`): every execution stops all vCPUs.

This fraction peaks during Safari launch (26%). Launching spawns many processes, and process creation and exit are exactly when TLB invalidations, inter-processor interrupts, and system-register accesses are most concentrated.

### Incidental fix

`gpu_init_timing.py` previously used "appearance of the next GPU process" as the cutoff for events belonging to the first GPU process, regardless of which client owned that next process. In Run142 another client's GPU process appeared before Safari's connection request, causing the script to report "no connection request". Now only a subsequent GPU process with the same owner acts as a cutoff, with a regression test added. The two pre-fix outputs are preserved as `superseded-timing-*.json`.

### Evidence

- `runs/e05-installed-boot/m3-gpuinit-quiet-qarma5-20261112-142/`: thread samples, clock probes (the two probes differed by 4.4 ms), `gpu-init-cpu-profile-*.json`, `vcpu-block-saturated.txt`, `vcpu-block-quiet.txt`, `vcpu-block-trial2.txt`, `perf-saturated.data`, `perf-quiet.data`, and both log windows.
- `runs/quiet-guest-gpu-init-20261112/`: `trials-run142.json` and driver scripts (`driver/`, including the blocking sampler `block_sampler.py` and summary script `block_report.py`).

### Limitations

- Only one run. 3.249 and 2.588 s are single samples; although below the prior ranges, the margin is modest and insufficient on its own to quantify how much background load mattered.
- "Quiet" remains inseparable from "longer uptime".
- Both trials overlapped WebKit activity from `nsattributedstringagent`, so no truly uncontested window was observed.
- Call stacks from the blocking sampler were obtained by scanning return addresses on thread stacks: the innermost function nearest the lock is reliable, whereas outer frames may contain false matches. The blocking sampler and summary script remain in the evidence directory and have not yet been checked into the repository or unit-tested.
- Root-level sampling was active during the second trial.

### Next steps

Because the initialization window is saturated either way, the largest recoverable loss under saturation lies inside QEMU itself. The next step is to count which guest operations trigger global-lock acquisitions and exclusive sections (broken down by system register, exit reason, and TLB invalidation instruction), then patch the top contributors so broadcast TLB invalidations do not have to stop all vCPUs and common exit reasons do not have to take the global lock.
