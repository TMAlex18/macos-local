# M3 Run90 — 4 GiB retry stopped by Vulkan device loss

Run `m3-memory-4g-20261105-90` reused Run89's target inodes under the explicit
`--allow-unclean-storage` contract. It held the Run89 CPU/GPU/PAuth/native
profile constant and requested 4 GiB. The boot observer verified one owned
4 GiB memfd and normal Darwin/PCI/root/launchd evidence; the guest reached the
login/desktop path.

Before any fresh Safari trial or guest upload, Reims reported FIFO waits and
then Vulkan `DeviceLost`. QEMU terminated with exit `-6`; the controller kept
`BrokenPipeError: [Errno 32] Broken pipe`, and no guest-origin shutdown was
observed. The host Intel Vulkan runtime was independently rechecked afterward
in [`host-intel-e02-recheck-20261105.json`](../../runs/host-intel-e02-recheck-20261105.json):
the existing E02 64×64 Intel and NVIDIA render/readback tests both passed with
zero pixel mismatches. Therefore this run is a guest/Reims runtime failure
sample, not evidence that 4 GiB causes the Safari watchdog.

The raw QEMU stderr, failed result, and retained-storage record remain under
the Run90 directory. A later 4 GiB retry was required before drawing any
capacity conclusion.
