# M3 Safari GPU process — Run69 cause analysis

**Run:** `m3-safari-gpu-cause-qarma5-20261101-69`, resumed at the user's request on 2026-11-01.
**Profile:** same as Run68 (8 vCPU, QARMA5, multi-threaded TCG, NVIDIA native window at 30 Hz, `half_blend`, user network, current-process sampler). Storage came from Run68's observed guest shutdown, without the unclean-storage override.
**Added tooling:** `host-thread-sampler.sh` (host per-thread CPU), `guest_files.py` plus `guest-diag/` (loopback file exchange; unused here because the guest network failed, see below).

## Conclusion

The Safari failures come from **WebKit's own responsiveness watchdog firing during slow process transitions under TCG**. They are not a Reims rendering fault, and background CPU load is an aggravating factor rather than the cause:

1. A WebContent process becomes unresponsive, either from slow page work or while waking a background tab that was throttled to priority 4.
2. Safari then checks the GPU process. It does not answer within WebKit's short timeout and is terminated (`reason=Unresponsive`).
3. The replacement GPU process is checked while it is still starting up and is terminated too. Three exits within 30 s trigger `GPUProcessCrashedTooManyTimes`; WebKit terminates the WebContent processes and Safari shows "This webpage was reloaded because a problem occurred".

Once a GPU process survives, rendering works: a local page rendered correctly in Run69, as the remote page did after the reload in Run65. Post-login load lengthens every transition, so failures cluster in the first ~40 minutes after login, but they also occur on a mostly idle guest.

## Evidence

- **Idle-guest reproduction (Run69, UTC):** host vCPU use had fallen to 1.6–3.8 cores and the guest was ~60% idle. Switching to the restored background tab (WebContent 785, priority 4) gave:
  - 16:32:17.98: WebContent 785 unresponsive;
  - 16:32:27.46: GPU process 1316 terminated as unresponsive;
  - 16:32:29.88: first log line of its replacement, GPU process 1566 (user lookup, preferences);
  - 16:32:33.79: GPU process 1566 terminated, about 4 s after it first logged, still in startup;
  - 16:32:42: GPU process 1569 started; it survived and rendered the local page (`LOCAL69 OK`, green).
- **Same sequence in Run65:** the exported Safari log shows WebContent unresponsive, then the GPU process terminated 3–7 s later, with replacements terminated 5–9 s apart. The second burst (19:08:50–19:09:20) fell between guest samples with 5–7 of 8 CPUs idle.
- **The GPU process waits rather than computes:** GPU process 782 stayed at priority 20 with CPU time fixed at 2.58 s for 37 minutes; Run68's PID 630 was also sleeping at 2.39 s.
- **Host graphics is not the bottleneck:** Reims stderr has no errors near any termination, and host graphics threads stayed near idle. The long Reims window frame gaps that the Run68 report called a stall also occur on static screens in Runs 64/65 (the window only redraws when the guest presents), so they are not evidence of a vGPU stall.

## Other findings

- **Login restores Safari:** "Reopen windows when logging in" relaunched Safari and Terminal during the post-login peak. All 232 guest CPU samples from login to about 16:05 were busy, and Terminal took about 8 minutes to give a usable shell.
- **spindump aborts in the guest:** 4 crash reports between 15:58 and 15:59 (`EXC_CRASH`/SIGABRT, `abort() called`, consecutive crash count 3) after spindump had used 1 min 35 s of CPU. `sample`, which depends on it, never returned, so no GPU-process stacks were captured.
- **Guest network was down in this run:** curl to the host (10.0.2.2) failed with exit 7, DNS for iana.org failed with exit 6, and `ifconfig` sat in uninterruptible wait (state U) for over 10 minutes. Remote pages failed to open and guest uploads never reached the host. Run65's network worked; the cause is open.

## Limits

- The exact wait inside the GPU process is unknown, because no stacks could be captured. WebKit's timeout value was not measured, only bounded by the log intervals.
- The guest-side process lists and logs were read from screenshots in the run directory, not exported.

## Next steps

1. Get GPU-process stacks: make spindump work in the guest, or extend the read-only current-process reader to walk a chosen process's threads.
2. Measure GPU-process launch-to-ready time against WebKit's timeout, and look at what dominates launch under TCG.
3. Mitigation to try: turn off "Reopen windows when logging in" and open Safari only after post-login load settles.
4. Investigate the Run69 network failure (`ifconfig` in state U, DNS failing).
