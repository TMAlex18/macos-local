# Guarded timeout diagnostic

This is a local, default-off diagnostic, not an upstream contribution or a
performance fix. Patch 0030 introduced a private two-site AArch64 FMOV diagnostic.
Patch 0031 keeps its fixed 3.0 → 30.0 values and current-address-space identity
checks, but derives each candidate image base from the **executing PC minus the
site offset**. A metadata probe's image base is not a universal process base:
the same image UUID can occur at different slides.

The profile is fixed at CPU creation. `x-webkit-timeout-armed` is a read/write
boolean that can be published only while the whole VM is paused or in prelaunch;
the former `x-webkit-timeout-base` property is removed. Publishing either boolean
value increments the per-CPU epoch. Reset revokes arming and clears counters;
migration remains blocked whenever the diagnostic profile is configured.

Eligible instructions retain the runtime-PC guard required by ARM `CF_PCREL`.
Candidate bases must be nonzero, 4 KiB aligned, within the lower 48-bit user range,
and leave room for the complete configured image without subtraction underflow
or range overflow. The existing RAM-only reader then checks the current mapping's
Mach-O header, LC_UUID and full 64-byte instruction window twice, with translation
context checks around the reads. Unsupported regimes and unreadable mappings
retain 3.0 without guest faults, MMIO reads, TLB fills or page-table updates.
Guest code, clocks, FPSR and unrelated FP registers are not modified.

`x-webkit-timeout-site0-hits` and `site1-hits` are cumulative per-CPU counters.
The new read-only `x-webkit-timeout-rejections` replaces `failures`: it counts
eligible candidate guard rejections, including unrelated images and code-only
aliases. It is **not** an application error counter. Neither hits nor rejections
identify a process, and live QOM snapshots are not atomic. Compare deltas only
within the same epoch. A hit in another process does not prove Safari exercised
a changed deadline.

## Actual synthetic consumer

Run on the matching, reconstructed main binary:

```sh
python3 -B experiments/E06-webkit-timeout/run.py --output runs/UNUSED-DIRECTORY
```

The fixture contains only self-authored code and synthetic identities. It runs
two real AArch64 TCG CPUs with 4 KiB translation tables. The same complete physical
image at two virtual bases must return 30.0 at both sites. Both alias orders are
covered, including after reset with the other VA creating the hot TB first, and
a second TTBR using the same ASID. Code-only aliases, wrong UUIDs, instructions
and neighboring bytes retain 3.0. Exact counter assertions accompany the values.
The retained checks also cover hot arming/disarming, reset revocation, running-VM
publication rejection, read-only counters, migration rejection, FP access traps,
FPSR/register preservation, unchanged code bytes, unsupported EL1, unmapped
headers, AF preservation, and RAM-only rejection of both payload and PTE MMIO.
An intentional UART data read verifies the MMIO trace observation channel.

The 0031 main build
`b4d85fe0d7e4e10d7b1c99f95ecfd54d8079a3ecb8665245c47db8e514a2774a`
passed all five cases (matching has 19 phases), with every QEMU exiting 0 and
reaped. Evidence is
[`runtime-slide-final/results.json`](../../runs/e06-webkit-timeout-20261108/runtime-slide-final/results.json).
All 40 changed QEMU source files reconstructed exactly from the pinned patch
chain. These tests do not validate macOS's 16 KiB mappings or Safari attribution;
those require the next actual installed-OS consumer. Run125 used the earlier
fixed-base 0030 build and remains historical evidence, unchanged by 0031.
