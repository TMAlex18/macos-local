# M3 Run99 — watcher service precondition failure

Run `m3-watcher-4g-20261106-99` completed its bounded 4 GiB normal-OS
session, but the host `guest_files.py` service was not listening on port
18769 before `start-gpuwatch.sh` was invoked. The guest could not download the
watcher, so no Safari navigation, page capture, or watcher archive was
obtained.

QEMU completed host cleanup with exit 0/reaped. This is a host setup
precondition failure and is excluded from RAM, GPU, Safari, and watchdog
conclusions. Future live watcher runs must verify the listener before typing
the guest launch command.
