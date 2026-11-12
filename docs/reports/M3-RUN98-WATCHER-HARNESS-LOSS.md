# M3 Run98 — watcher harness loss

Run `m3-watcher-4g-20261106-98` used the promoted 4 GiB Intel/native-window
profile with the bounded `gpuwatch.sh` path. Normal macOS boot and the bounded
runner completed with QEMU exit 0/reaped after the 900-second limit.

The guest-side watcher command occupied the restored Terminal session. The
subsequent guest UI operations could not reliably focus Safari or deliver a
new URL, so this run has no new authored RGB page capture and no watcher
archive. It is a harness/focus failure, not a RAM, GPU, Safari or watchdog
result, and is retained separately from the positive 4/8 GiB page controls.
