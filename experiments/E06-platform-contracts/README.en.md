> **Language:** **English** | [简体中文](README.md)

# E06 platform contracts: virtual timer, GICv3 and EL1 stage-1 MMU

This extends E06 rather than introducing another project milestone. The
payloads are self-authored C and AArch64 assembly, built with host Clang/LLD.
They run at guest EL1 with a real exception vector table, a guest stack and
saved general-register/ELR/SPSR frames; no Apple firmware or OS is involved.

## Reproduce

    python3 experiments/E06-platform-contracts/run.py --suite all --target all

Narrow selections: --suite timer|mmu|combined, --target headless|reims.
The runner builds each payload once and uses that same binary on both the
portable GPU-less VMApple and modern Reims/VMApple ARM16K integration.

## Tested behaviors

- CNTV virtual counter/timer, connected by the pinned VMApple board to GICv3
  PPI 27. Configure Redistributor Group1/priority/enable and CPU interface.
- Expired timer while DAIF.I is masked remains pending without executing ISR;
  unmasking enters IRQ, observes INTID 27, clears source, EOIs and ERETs.
- Rearm timer; WFI wakes on pending IRQ while DAIF.I masked, then interrupt
  is delivered after unmask; no further IRQ after disabling the source.
- Negative case disables PPI routing: timer pending exists, but ISR does not
  run. Guest exits 1; the harness requires this exact expected refusal.
- Guest stage-1 16KiB translation, TCR_EL1.TG0=16K with 32-bit input VA space,
  L2 block identity maps and L3 pages mapping a high alias to a RAM frame.
- RW alias, RO read, denied RO store (EC=0x25/DFSC=0xf/WnR=1), missing page
  (DFSC=7), missing access flag (DFSC=0xb), exact FAR and faulting PC.
- Handler skips only the known faulting LDR/STR and returns via ERET; denied
  store does not alter its physical backing. AF correction is observable.
- Break-before-make + DSB/TLBI/DSB/ISB switches a used VA from frame A to B.
- Negative RO case deliberately grants write permission: missing expected
  abort is detected, rather than silently counted as a passing fault test.
- Combined case performs the timer/GIC/exception test with MMU/caches enabled.

## Observed result, 2026-10-14

10/10 **scenario expectations** met: 6 positive guest exits (0) and 4 negative
controls (guest exit 1). 5 distinct payloads each run on both targets. This
is NOT ten independent platform implementations. The two QEMU forks share
substantial underlying code. Exact raw results and firmware digests are in
runs/e06-contracts-all-all-results.json; all individual UART logs are retained.

## Limits

Only one active vCPU per new scenario. Earlier PSCI two-vCPU test remains a
separate check. No 48-bit address-space/ASID/multicore TLB validation, no EL0
privilege transition, no nested IRQ/FIQ/LPI test, no Apple proprietary HVC or
PAuth key lifecycle, no real macOS. Guest timer frequency is not host CPU
frequency and the runner's wall times are NOT performance benchmarks.

The QEMU/Reims implementations were **not modified** in this step; this adds
missing executable contract tests for capabilities that already exist.

## Primary references (plus fixed local source)

- Arm GIC configuration: https://support.arm.com/documentation/198123/0302/Configuring-the-Arm-GIC
- Arm translation granules: https://support.arm.com/documentation/101811/0105/Translation-granule
- Arm ESR_EL1: https://support.arm.com/documentation/111107/2026-06/AArch64-Registers/ESR-EL1--Exception-Syndrome-Register--EL1-
- Fixed QEMU VMApple map/timer wiring: sources/qemu-reims-arm/hw/vmapple/vmapple.c
- Fixed GIC implementation: sources/qemu-reims-arm/hw/intc/gicv3_internal.h
  and arm_gicv3_common.c. No claim that source agreement is independent proof.

## Counter frequency differential test

run-clock.py compiles clock.c and compares three guest CNTFRQ modes
against host monotonic elapsed time. The official macOS 27 VirtualMac2,1
DeviceTree declares 24MHz on 32 CPUs, while QEMU max defaults to 1GHz.
This QEMU TCG build's integer nanosecond period leads to ~+1.61% guest
clock rate at the requested 24MHz (3 repeats). No macOS code is run.
See docs/reports/M1-RESTORE-FIRMWARE-ADT-CLOCK.md.

## Dedicated Probe for Ordinary User-Supplied AES-128 ECB Keys, DMA, and IRQ

```sh
python3 experiments/E06-platform-contracts/run-aes.py
```

`aes.c` reuses the EL1 exception vectors and GIC conventions in this directory's
`start.S` and `runtime.c`; the existing `run.py` entry point remains unchanged.
The dedicated runner uses the binary pinned in `runs/qemu-bdif-usb-build.json`,
verifying its actual SHA-256 digest before running two bounded self-authored
firmware payloads without loading Apple code or modifying devices or libraries.
The 4GiB RAM span covers `0x70000000..0x170000000`; two CPUs are configured with
execution confined to CPU0, retaining 24MHz exact timing and the existing
research platform configuration, while protection-model semantics remain outside
the scope of this test.

The sole key material comes from the public test vector in
[NIST SP 800-38A](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-38a.pdf)
Section F.1.1 `ECB-AES128.Encrypt` Block #1 (printed page 24 / PDF page 31):
key=`2b7e151628aed2a6abf7158809cf4f3c`, plaintext=
`6bc1bee22e409f96e93d7e117393172a`, ciphertext=
`3ad77bb40d7a3660a89ecaf32466ef97`. The guest submits only KEY selector 0 /
context 0, AES-128, encrypt, ECB, and ordinary DATA/FLAG commands; it accesses
neither the second AES MMIO region nor other key selectors, and involves no
hardware secrets, key derivation, storage wrapping, or Apple boot protection.

Positive requirements: the first DATA command uses `0x72000100→0x72004100`, and
the second uses valid high RAM `0x110000100→0x110004100` whose FIFO address word
`0x00010001` preserves the upper source/destination address bits. Each operation
encrypts exactly 16 bytes, and the guest independently verifies byte-for-byte
that the entire 16KiB input page is unchanged and checks both the ciphertext and
guards across the entire 16KiB output page. Normal completion requires actually
reading IAR 50, FLAG IRQ status bit 5, and info advancing `1→2`; the ISR clears
the level-triggered source via W1C before writing EOIR, and the second operation
must raise a fresh notification. The negative control masking GIC SPI 50
requires ciphertext, FLAG, and GIC pending state to become ready while ISR count
remains 0, exiting 1; the in-guest counter wait is bounded to 2 seconds and a
15-second host timeout always fails. Because the device processes its FIFO
synchronously, reset `STATUS`/`BUSY` reads cannot substitute for this evidence.
The runner additionally cross-checks the live FIFO trace against the complete
expected command sequence; all test keys are supplied by the guest, and the host
never writes expected ciphertext into guest memory.

The first live execution on 2026-10-23 halted at an **ordinary build-dependency
gap**: the
sole raw result
and
UART/stderr trace
showed the KEY command consumed in full, followed by `success=0` on the valid
low-RAM DATA request, leaving the FIFO stalled at DATA with FLAG unconsumed and
the guest exiting 1 with `AES_FLAG_NOT_READY`.

The prior binary
`0273d0cccbb82554f6753cf54d70c5703e5627e52cc4fdbac8d64c4f49b08973` had none of
`CONFIG_GCRYPT`, `CONFIG_NETTLE`, `CONFIG_GNUTLS_CRYPTO`, or `CONFIG_AF_ALG`
enabled; cipher.c therefore fell
back to cipher-stub.c.inc,
causing ordinary `qcrypto_cipher_ctx_new` calls to return a missing-cipher-library
error. That initial failure is retained as evidence of the missing build
prerequisite.

After installing `nettle-dev 3.10.2`, both research builds explicitly enabled
the standard Nettle cipher backend without modifying the AES device
implementation or key selectors. The resulting BDIF binary SHA-256 was
`b0123d45ebc127f6e1b46727c937174100ddd45b0d9adc17e2ce0e7c52ce91da`;
runs/qemu-bdif-usb-build.json records the
binary, backend, version, and configuration summary.

The
canonical passing result
passed both low- and high-address DMA operations above: both produced the exact
NIST ciphertext, full 16KiB input/output page checks passed, and observed IAR
50 / status 32 / info `1→2`, IRQ count `1→2`, and final status 0 confirmed
normal ack and rearm behavior. The positive case exited 0; the
masked-SPI negative-control raw log
confirmed ciphertext/guards and FLAG/GIC pending ready with ISR count 0, exiting
1 as expected; neither case hit a host timeout. The QEMU `log` trace backend
emitted FIFO traces to stderr, preserved alongside UART in each case's unique
raw log, and FIFO commands plus the high-address word were independently checked
by the runner.

This establishes the ordinary user-supplied AES-128 ECB data/DMA/IRQ contract on
the current binary; it does not test other key paths, the second MMIO region,
hardware secrets, or Apple code, nor does it diagnose an Apple
`IOConfigThread_'aes'` spin or root boot stall.

## VirtIO Net Control Queue Capacity (0032)

Local research patch `0032-virtio-net-control-queue-size.patch` adds the
standard device property `ctrl_queue_size` (`uint16`). The default of 64 is
unchanged; an explicit value must be a power of two from 1 through
`VIRTQUEUE_MAX_SIZE` (currently 1024). Both initial `realize` and multiqueue
(MQ) queue reconstruction consume the same field without altering RX/TX
capacities, feature bits, control ACK values, or guest addresses.

```sh
python3 -B experiments/E06-platform-contracts/test-net-control.py --output runs/UNUSED-NET-CONTROL-DIRECTORY
```

This test reuses the QMP, PCI memory, and descriptor transport helpers from the
existing PCM qtest harness without instantiating audio devices, executing Apple
code, or touching host TAP or physical networking. Default, explicit 64, and 512
cases each verify QOM capacity, PCI queue-size advertisement, and actual
selected size while RX/TX remain 256. The 512 case submits 515 control requests,
wrapping past the end of the `avail`/`used` rings and exercising high descriptor
head indices; both direct and indirect SG requests check read-only inputs plus
pre-/post-ACK and used-ring canaries, presetting the ACK byte to 255 before
requiring `RX_ALLMULTI` to return 0 and an invalid class to return 1, each with
`used.len = 1`. A socket peer additionally verifies 60B Ethernet frame TX/RX
byte-for-byte, and host features match across all three capacities. Invalid
sizes 0, 3, 2048, 65535, and `uint16` overflow 65536 are all rejected.

MQ coverage uses a bounded local handshake peer speaking the existing upstream
`vhost-user-test` protocol and advertising two queue pairs while keeping link
down and starting no offloaded dataplane. Live feature negotiation cycling
`MQ on→off→on` transitions total queue count through `3→5→3→5`; in every
generation the trailing control queue reports size 512 and completes live
`RX_ALLMULTI` and invalid-class ACKs while data queues remain 256. This
validates QEMU queue reconstruction and control-queue consumers without claiming
`vhost-user` packet dataplane coverage.

Main binary
`4a433616dc04e90ba41d720fe0dfb8925294799eff94a6eef24b52108a6b4d66` passed all
checks above with every QEMU process and handshake thread cleanly exited and
reaped, and all 42 patched source files reconstructed identically from the
pinned patch chain. Results are stored in
`runs/e06-net-control-20261108/final/results.json`.
Run 126 did exhibit control-ring address overlap alongside an inactive guest
interface, but the guest's internal `used` VA has not yet been translated; this
test treats neither that root-cause hypothesis nor any macOS fix from size 512
as proven—live OS validation is conducted separately.
