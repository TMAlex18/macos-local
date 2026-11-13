# M3 Run102 — desktop recovered, sampling interrupted

Run `m3-gpu-window-sample-20261106-102` started at 2026-11-06 14:57:36 UTC
with the existing 4 GiB, eight-vCPU, Intel native-window, QARMA5/PAC memo
profile. It explicitly reused Run101's interrupted storage after the new
dead-owner recovery checks. Normal installed macOS boot is established.

## Login and application boundary

The first ordinary input attempt and a subsequent slower attempt did not
establish login; the kernel recorded user-key-store failures `-5` and `-8`.
Those codes alone do not identify their cause. In particular, the successful
Run100 also contains `-5` and the same preceding zero-boot-group diagnostic.

After Escape, `r102-login-escape.png` visibly confirms an empty password
field. Slower private input then produced `user unlock successful (0)` at
kernel elapsed time 3073.379416, followed by the desktop. This sequence is a
working recovery observation, not proof that input loss caused the earlier
failures or that a general key-store defect is fixed.

The PID-bound native capture `r102-desktop-check.png` at 16:03:14 UTC shows
Finder/Dock, restored Terminal windows and Safari displaying the authored RGB
page. The owned host file-service log records HTTP 200 requests for
`/get/render.html` at 15:55:41 and 15:56:50 UTC, distinct from the initial host
readiness request. These establish this run's desktop, displayed page and
guest HTTP fetch. The browser was restored from the prior session; no fresh
Safari exit/launch or watchdog timing was captured, so this is **not a
first-load stability result**.

The attempted Terminal command and later wake screenshots do not establish
execution of `caffeinate`, an echo receipt, or the diagnostic script.
`guest-uploads/` remains empty. There is no watcher archive, GPU trigger,
stack report or new initialization-latency conclusion from this run.

## Interruption and storage recovery

Controller PID 294068 and QEMU PID 294079 subsequently disappeared. The QEMU
log ends with signal 15 from the controller; the final collector record
remained `observing` without an exit code or completed cleanup. That saved
state does not establish a live VM. Guest shutdown, original-owner reap and
filesystem cleanliness remain unobserved.

At 17:22:45 UTC `recover-abandoned.py` verified the recorded processes were
absent, the exact retained inodes and both exclusive locks, and removed only
the known temporary aliases. The retention manifest preserves
`qemu_reaped=false`, `qemu_exit_code=null` and
`teardown=abandoned_owner_exit_unobserved`; the original collector result is
unchanged. Explicit `--allow-unclean-storage` remains necessary for reuse.

The subsequent [Run103](M3-RUN103-EARLY-SAMPLE-CONTROLS.md) used the independent
user service `macos-local-run103.service` with a 3600-second observation
window. This changed controller lifetime, not the guest platform, RAM or GPU
profile. Current work is tracked in [STATUS](../STATUS.md).

Evidence: [`results.json`](../../runs/e05-installed-boot/m3-gpu-window-sample-20261106-102/results.json),
[`retained-storage.json`](../../runs/e05-installed-boot/m3-gpu-window-sample-20261106-102/retained-storage.json),
`ui-actions.jsonl`, `kernel-boot.log`, `qemu-stderr.log`, `guest-files.log`,
and the named native captures in the same run directory.
