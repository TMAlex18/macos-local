# E05 BDIF / USB control transport research

This is a real **synthetic ARM guest** data-path test on Linux/x86 TCG.
It does not execute AVPBooter, enumerate an Apple DFU device, attach Linux
vhci-hcd, obtain signatures, initialize macOS AUX, or install a guest OS.

## Source chain

QEMU pin: 1a72df984914fb9929d35b1217619178133d65a4.
Isolated worktree sources/qemu-bdif-usb, binary build/qemu-bdif-usb/qemu-system-aarch64.
Apply in order: cumulative VMApple/PAC patch 0003, exact-clock patch 0005,
original steelbrain USB chardev reference patch, local transport fixes 0006.
This branch does not include AVP zero-register stubs (0004). Existing
qemu-reims-arm and qemu-clock-exact branches are untouched.

Original transport: steelbrain/experiment-macOS-arm64-on-asahi-linux-arm64,
657bb2423ec8d572b22a97dd33563dbba1680e6a; authored by Anees Iqbal.
The exact reference patch is retained at patches/upstream-reference/;
QEMU/patch code retains its GPL-2.0-or-later provenance. We test and extend
only its transport, not its claims about other platforms or guest releases.

## Reproduction

    make build-qemu-bdif-usb
    make bdif-usb-tests
    make bdif-usb-backpressure
    make bdif-usb-control

Baseline reproduction needs the retained build/qemu-bdif-usb/qemu-system-aarch64.candidate;
that ignored binary was copied before local fixes and is not a deliverable.
It can be rebuilt from the first three patches, without local patch 0006.

    python3 experiments/E05-bdif-usb/run.py --phase baseline-rerun \
      --binary build/qemu-bdif-usb/qemu-system-aarch64.candidate \
      --cases small,fragmented,maximum,descriptor-boundary,oversize-for-buffer,disconnect-partial
    python3 experiments/E05-bdif-usb/run-backpressure.py --candidate

A baseline failure is expected and recorded, not relabeled as a pass.

## Runtime boundaries

- RX frame: little-endian 32-bit length + at most 128KiB payload;
  the bounded receive window includes the four-byte header.
- TX frame: at most 4096-byte payload; one outstanding staged write.
- Guest descriptor: 16-byte address/length/next/flags record. Only the first
  descriptor is supported; no scatter/gather/descriptor chaining promise.
- Completion and reconnect behavior are this research profile's tested
  contract; they are not claimed as a verified macOS27 booter ABI.
- Full malformed stream lengths close the peer; a complete packet exceeding
  the current guest RX buffer is discarded while the next valid frame can
  proceed. Disconnect clears partial frame state.
- Nonblocking TX retains a bounded frame and polls the socket rather than
  blocking QEMU's main loop. Invalid addresses/lengths do not produce data.
- Mid-stream full-VM reset, peer-generation coordination, migration and
  asynchronous cancellation have NOT been qualified. A live restore needs
  further lifecycle design, not merely a zeroed software buffer.

## Test structure

run.py: 16 isolated QEMU processes; actual guest reads/writes, sentinels,
completion, zero/max frame, fragmentation/coalescing, boundary descriptors,
invalid DMA/TX, no-data negative, disconnect/reconnect, ROOT/AUX reads.

run-backpressure.py: actual ARM guest sends 256 frames (4096 bytes payload
per frame); the peer intentionally stops reading and issues QMP query-status.
Original blocking write prevents control response; fixed implementation
responds, then all 1,049,600 framed bytes are checked after peer resumes.

run-control.py: USB/IP user-space client -> socketpair gateway -> local
AVP framed Unix stream -> BDIF MMIO -> real synthetic ARM guest. Device
identity comes from self-authored firmware, NOT an invented Apple device.
Three scenarios check control IN/OUT, guest-backed data, status bytes,
genuine empty responses, STALL, malformed reply and timeout. No DFU status
is manufactured from an empty response or timeout.

The gateway implements sequential import and endpoint-zero control only;
DEVLIST, other endpoints, isochronous transfers, pipelining and UNLINK are
not implemented. No kernel module or actual USB device is attached.

Full evidence is under runs/e05-bdif-usb and summarized in
 docs/reports/M1-BDIF-USB-RESULTS.json and docs/reports/M1-BDIF-USB-TRANSPORT.md.


## Original-ROM teardown regression

The suite now has17 cases. `queue-control` verifies observed USB 0x404
startup/readback, disable, rejection of disabled TX, clearing stale RX,
reenable and actual transfer. Oldbinary fails; patchedbinary passes.
Source delta: patches/qemu-platform/0008-bdif-usb-queue-control.patch.
Backpressure harness startup and pressure deadlines are separate (15s and
250ms), with phase and per-run labels for interpreting a failed startup.
