# M3 Run101 — interrupted before GPU sample collection

Run `m3-gpu-window-sample-20261106-101` started at 2026-11-06 10:57:34 UTC
with the existing 4 GiB, eight-vCPU, Intel native-window, QARMA5/PAC memo
profile. The boot identity checks establish normal installed macOS.

The intended ownership-triggered 45-second GPU sample was **not obtained**.
The retained native capture `r101-resume-state.png` shows the login/lock
screen, not a desktop or Safari trial. The UI journal records password input
and Return at 11:28:33–36 UTC; kernel elapsed time 1863.563963 records a
user-key-store `unlock failed (-5)`. This is an observed login boundary, not
proof of its cause or of an incorrect password. Earlier kernel entries also
contain this error, so it is not unique to the recorded input attempt.

At 14:15 UTC the controller (259424), QEMU (259436), and owned file service
(259427) were all absent from `/proc`. The QEMU log ends with termination
on signal 15 from controller PID 259424. The final collector write was at
13:02:13 UTC. The collector and retention records remained unfinished;
their `observing` state does not describe a live process now. Neither an
exit status, successful reap by the original owner, guest-origin shutdown,
nor filesystem cleanliness can be recovered from this evidence.

`guest-uploads/` is empty and `guest-files.log` contains only the host's
initial GET/PUT readiness checks. No guest connectivity, watcher execution,
Safari event, GPU stack, or fresh-page result is established. This run must
not be counted as a Safari failure or a negative GPU sample.

The next run must explicitly reuse the interrupted storage after verifying
that its owners are gone and the exact retained inodes are unlocked. Recovery
must preserve the unknown exit/reap boundary rather than label it clean.

Evidence is retained in
[`results.json`](../../runs/e05-installed-boot/m3-gpu-window-sample-20261106-101/results.json),
`ui-actions.jsonl`, `kernel-boot.log`, `qemu-stderr.log`, `guest-files.log`,
and the native capture in the same run directory.
