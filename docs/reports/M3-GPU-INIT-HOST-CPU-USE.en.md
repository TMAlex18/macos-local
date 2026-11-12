> **Language:** **English** | [简体中文](M3-GPU-INIT-HOST-CPU-USE.md)

# What the Host CPU Is Doing During Safari's First GPU Connection Initialization (From Run138)

Date: from 2026-11-11 UTC. The "Pre-run plan" section of this document was committed before the run.

## Pre-run plan

### Problem

Run132–137 showed that on the first Safari launch after boot, initializing the first GPU connection takes 3.5 to 3.8 s and does not vary with the PAC algorithm, even though the faster algorithm sped up the overall launch path by 11% to 21% (see [PAC algorithm comparison](M3-PAC-ALGORITHM-GPU-INIT-TIMING.en.md)). To decide what to optimize next, we first need to know whether the system spends those 3.5 s computing or waiting.

### Method

On this host, perf is disabled for unprivileged users (`perf_event_paranoid=4`) and this session does not have root access, so instead of perf we use the scheduler's own per-thread counters:

- `host_thread_sampler.py` reads every 10 ms the cumulative on-CPU time, runqueue wait time, and most recent host CPU of all QEMU threads. It only reads `/proc` and does not attach to QEMU.
- `clock_offset.py` has the guest send its clock readings to the host, giving the offset between the two clocks to within a few milliseconds.
- `gpu_init_cpu_profile.py` uses this clock offset to align event timestamps from the WebKit logs with the host samples.

When a guest vCPU is idle, its corresponding host thread sleeps, so vCPU-thread on-CPU time reflects whether the guest is computing. The report also includes figures for the 10 s before and 10 s after the window for reference.

### Metric

`vcpu_mean_busy`: the average number of vCPU threads running on a host CPU over an interval (0 to 8 across 8 vCPUs). The primary metric covers the initialization window (from Safari logging the connection request to its logging `didInitialize`) and the three longer sub-segments inside the window, taken from the first-launch-after-boot trial. The second-launch trial serves as a replicate.

### Decision criteria

| Case | Condition | Meaning and next step |
|---|---|---|
| Mostly waiting | At least one sub-segment ≥ 0.5 s in the window has `vcpu_mean_busy` < 0.5 | The guest is largely idle during that segment and spending its time waiting. Next step: identify what it is waiting on |
| Serial compute | Overall window is between 0.5 and 2.0, with no sub-segment below 0.5 | A serial chain is executing, limited by single-thread emulation speed. Next step: check how much of that chain lands on E-cores and obtain a function-level profile |
| Parallel, unsaturated | Overall window is between 2.0 and 6.0 | Multiple threads are running concurrently. Decide based on E-core share and runqueue wait |
| Saturated | Overall window is 6.0 or above | Not enough vCPUs. Next step: try more vCPUs |

The three longer sub-segments are: GPU process receiving the request to activating the AudioComponentRegistrar connection (previously 1.6–2.0 s), SandboxHelper connection creation to destruction (about 1.0 s), and destruction to `didInitialize` (about 0.55–0.7 s on first launch).

### Also recorded

- Fraction of vCPU on-CPU time spent on E-cores. The host has 8 P-cores (CPUs 0–7) and 12 E-cores (CPUs 8–19); QEMU threads are not pinned.
- Time vCPU threads were runnable without getting a host CPU.
- On-CPU time of the main thread and other QEMU threads.
- Maximum gap between samples, to check for sampler stalls.
- Difference between the two clock probes (before the first trial and after the second trial).

### Configuration and procedure

Configuration matches Run132, Run134, and Run136: QARMA5 plus memo, with the 30 s diagnostic. Also pass `--allow-unclean-storage`, because Run137's teardown record was written before the controller fix.

The procedure is also identical (first trial triggered 720 s after boot, second at 1020 s), adding only three things: the sampler and clock listener run in the background; one clock probe runs after arming the diagnostic; and a second probe runs after the second trial.

This run also adds one pair of initialization-time samples to this configuration. The previous ranges were 3.515–3.791 s for first launch and 2.768–3.419 s for second launch. If the new samples fall clearly outside those ranges, sampling or probing perturbed the target and the results must be discounted.

### Known limitations

- Sampling observes host threads, not guest processes. This method cannot distinguish whether busy vCPUs inside the window are running the GPU process, helper processes, or unrelated background work.
- Only one run with two trials. The figures are used to choose among the cases above, not to compare small differences.
- Scheduler accounting resolution is 1 ms and the sampling interval is 10 ms. For the shortest sub-segment (about 0.2 s), error can reach 5%.
- The clock-offset estimate includes minimum network transit delay, with a bias within a few milliseconds.

## Pre-run revision (2026-11-11, after Run138 interruption and before any trial)

### Run138 produced no trial data

Run138 was terminated 662 s after boot: the previous session ended, the controller, QEMU, and the three background recorders all received signals, and `qmp-lifecycle.json` recorded `SHUTDOWN reason=host-signal`. At that time the guest was sitting at the login window; it had not logged in and no trial had been executed. The next run still requires `--allow-unclean-storage`.

### What those 11 minutes of sampling showed

- The tooling works. Across 65,830 samples, the median interval was 10.0 ms and the maximum was 11.9 ms; the 8 vCPU threads identified by thread name matched the thread IDs recorded by the controller.
- The guest is not idle even when untouched. During the 5 minutes sitting at the login window (300–600 s after boot), `vcpu_mean_busy` was 2.3, with individual 10 s slices reaching 2.6. During the boot phase (60–150 s) it was 5.5, with all 8 vCPUs busy 74% of the time.
- When load is low, vCPUs spend half their time on E-cores: E-cores accounted for 51% of vCPU on-CPU time at the login window, compared with 12% during boot.

### Flaw in the decision table and revision

The original decision table assumed vCPUs would be nearly idle when the guest was idle. In practice, background work alone occupies more than two vCPUs, and during Safari launch both the Safari main process and the web process perform their own computation. Consequently, `vcpu_mean_busy` inside the window cannot drop as low as 0.5, and the "Mostly waiting" and "Serial compute" rows would never occur under the original thresholds. Host sampling also cannot tell who is busy, as already noted under limitations in the original plan.

After revision, host sampling answers only the question it can answer: whether CPU supply is sufficient during the window.

| Case | Condition | Meaning and next step |
|---|---|---|
| Saturated | Overall window `vcpu_mean_busy` is 6.0 or above, or 7 or more vCPUs are busy for at least half of the window | CPU supply is a limit. Next step: try more vCPUs, pin vCPUs to P-cores, or reduce background work |
| Unsaturated | All other cases | CPU supply is not the limit. Initialization is a cross-process serial chain; continue investigating on the guest side what each step is waiting for |

Also report the pre-trial baseline: `vcpu_mean_busy` from 25 s to 5 s before pressing Return (taken by host time, independent of the clock probe), as well as the E-core share for each interval.

"Computing vs. waiting" is answered instead by guest-side records. Existing archives already contain cumulative per-thread CPU times for the GPU process, establishing that within the window the GPU process spends most of its time waiting on other processes; see the "Motivation" section of the [RAM size comparison](M3-RAM-SIZE-GPU-INIT-TIMING.en.md).

### Order adjustment

Existing archives also show that 4 GiB of guest memory is continually under pressure. Therefore the next run first performs the 8 GiB memory comparison (from Run139), keeping the sampler and clock probes running as planned and reporting against the table above. Whether a sampled 4 GiB run is still needed will be decided once the memory comparison reaches a conclusion.

## Results (Run140, 8 GiB)

### Conclusion

By the revised decision table, the result is **saturated**: all 8 vCPUs are busy for roughly 90% of the initialization window. Moreover, this is not limited to the window — from 90 s after boot until shutdown, the guest remains in nearly this state almost continuously, including the several minutes on the desktop with no user input while waiting to press Return. CPU supply is a limit.

This run used 8 GiB (see [RAM size comparison](M3-RAM-SIZE-GPU-INIT-TIMING.en.md)); no sampled run was performed at 4 GiB.

### Figures

| Interval | First launch | Second launch |
|---|---|---|
| Initialization window length | 3.444 s | 3.061 s |
| Window `vcpu_mean_busy` | 6.03 | 6.16 |
| Fraction of window with all 8 vCPUs busy | 90% | 92% |
| On-CPU time per vCPU within window | 2.54–2.65 s | 2.29–2.44 s |
| E-core share of vCPU time within window | 6% | 2% |
| vCPU runqueue wait time for host CPU within window (sum across 8) | 0.66 s | 0.94 s |
| Baseline 25 s to 5 s before pressing Return | 6.19 (all-busy 91%) | 6.08 (all-busy 66%) |
| 10 s before request | 4.31 | 4.90 |
| 10 s after `didInitialize` | 5.42 | 5.86 |

Sub-segments within the window all lie between 6.1 and 6.5, except the final sub-segment (SandboxHelper connection destruction to `didInitialize`), which is 4.9.

Viewed in 30 s bins across the entire run: from 90 s after boot through the end of the second trial (around 1160 s), `vcpu_mean_busy` mostly stays between 5.3 and 6.4, with all 8 vCPUs busy 55%–96% of the time. Only three intervals dip below this level: around login (180–240 s, 4.9–5.3); the half-minute right after Safari starts at the beginning of each trial (dropping as low as 2.8–3.6 in 2 s bins, during which about half the time only one or zero vCPUs are busy); and from the end of the second trial until shutdown (4.6–5.6, all-busy 21%–60%).

### How to read these figures

- **Even when "all busy", each vCPU spends only about 76% of the time on a host CPU.** Runqueue wait for a host CPU accounts for only about 2%, so during the remaining ~22% the vCPU threads are blocked rather than starved of host CPUs. Sampling cannot tell what blocks them. Around 6.0 `vcpu_mean_busy` is therefore the ceiling for 8 vCPUs, not an indication that two vCPUs are sitting idle.
- **When saturated, vCPUs are already on P-cores.** Inside the window, E-cores account for only 2%–6%; they rise to 30%–50% only under lighter load (33% in the 10 s before the request, and 51% at Run138's login window). Pinning vCPUs to P-cores would therefore help little during the saturated window.
- **What keeps them busy is mainly background activity.** Nearly 600 processes still alive at the end of the run (20 minutes after boot) had accumulated roughly 7300 s of CPU time, excluding exited processes and kernel threads. That time is spread across hundreds of processes: WindowServer 337 s, launchd 278 s, Siri AI 250 s, dasd 197 s, Terminal 183 s, mobileassetd 161 s, softwareupdated 140 s, mediaanalysisd 132 s, corespotlightd 119 s, and others. By contrast, Run138 never logged in and stayed at 2.3 while sitting at the login window. In other words, post-login user-session background work continuously saturates all 8 vCPUs under TCG for at least 20 minutes.
- **Initialization itself is a cross-process serial chain.** The GPU process's main thread uses at most half the window length in CPU time itself (an upper bound; see the RAM comparison report for the calculation), spending the rest of the time waiting on tccd, coreaudiod, AudioComponentRegistrar, and the newly spawned SandboxHelper; the last of these is pure computation (see the RAM comparison report). This chain executes on top of a guest already saturated by background work.

### Tooling verification

- 135,463 samples covering 1354.6 s; the longest inter-sample gap inside the windows was 10.3 ms.
- The two clock probes (after arming the diagnostic and after the second trial, 14 minutes apart) gave clock offsets of −1.615117 and −1.619839 s, differing by 4.7 ms; the spread across each probe's 12 readings was 7.6 and 2.0 ms. One diagnostic counter read (which briefly pauses the VM) ran in between without causing any visible drift.
- Initialization times (3.444 and 3.061 s) fell near the previous ranges, with no sign that sampling or probing perturbed the measured target.

### Evidence

`runs/e05-installed-boot/m3-gpuinit-ram8g-qarma5-20261111-140/`: `host-threads.tsv` (samples), `clock-offset.jsonl`, `gpu-init-cpu-profile-r140safari.json` and `-r140safarib.json` (complete figures across intervals), `driver-return1.txt` and `driver-return2.txt` (host timestamps when Return was pressed). Run138's samples are in its own run directory.

### Limitations

- Only one run, and at 8 GiB. Background load at 4 GiB is at least as heavy, though it was not measured directly.
- Host sampling cannot see guest processes. The "saturated by background work" conclusion rests on cumulative per-process CPU times at the end of the run, not per-process sampling inside the window.
- The cause of the ~22% blocked time is unknown from these counters alone and requires function-level or scheduler-event profiling.

### Next steps

Saturation splits the problem into two independently testable directions:

1. Reduce contention for CPU. First find out how long post-login background work takes to subside, and what the initialization time is when Safari is launched after the guest becomes quiet.
2. Identify where that 22% vCPU-thread blocked time goes. This requires perf; `sudo -n` is available in this session.

Both were carried out in Run141 and Run142; see the [quiet-guest report](M3-QUIET-GUEST-GPU-INIT-TIMING.en.md): background load subsides after roughly 20 minutes, but launching Safari saturates the window by itself, and the blocked time is spent on QEMU's global lock and exclusive sections.
