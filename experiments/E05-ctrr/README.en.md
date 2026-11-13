> **Language:** **English** | [简体中文](README.md)

# CTRR Local Contract and RAM-Cached Execution Path

The current opt-in QEMU model preserves normal RAM read and instruction-fetch
caching while retaining ordinary CPU/DMA write rejection on ranges with
`READ_ONLY` enabled. Patch
[`0016-vmapple-ctrr-shared-romd-ram.patch`](../../patches/qemu-platform/0016-vmapple-ctrr-shared-romd-ram.patch)
replaces pure MMIO overlays with a single ROMD view covering the underlying RAM
together with per-range aliases:

- The ROMD view borrows the same RAMBlock, host bytes, and TB/SMC address
  identity while holding a reference to the backing owner; it allocates no
  second RAMBlock, copies no code, and registers no duplicate RAM migration
  entry.
- With `ram=false`, ordinary writes still enter the callback and return
  `MEMTX_ERROR`, unlike readonly RAM paths that silently drop CPU writes.
  Ordinary reads and instruction fetches take the standard direct-RAM path.
- Enabled ranges without `READ_ONLY` keep the ordinary RAM path; CPU writes and
  SMC invalidations use the underlying RAM's native mechanism. Active
  permission updates, locking, and range validation retain the existing
  contract.
- Reset/unrealize removes all range mappings; the ROMD leaf and aliases persist
  until final owner destruction before releasing borrowed references, avoiding
  dangling stale RCU views. Re-realize against the same backing is allowed;
  rebinding an already-initialized view to a different RAM is forbidden.

QEMU's administrative debug/ROM-loading writes continue to follow standard ROMD
privileged semantics and can modify the backing directly; this lies outside the
ordinary guest CPU/DMA CTRR boundary. The local fixture explicitly uses that
privileged path to verify shared bytes and SMC invalidation while separately
verifying guest CPU write faults and real BDIF address-space DMA write
failures. Neither PAuth, Apple inputs, nor host protection permissions were
modified, nor was migration support expanded. This local research
implementation does not constitute a proof of equivalence to Apple hardware
semantics.

## Verification Entry Points

```sh
make build-qemu-bdif-usb
make ctrr-contract-tests
make ctrr-qemu-guest-tests
```

The 15 pure-Python tests in `contract_model.py` cover 16 ranges, alignment, RAM
containment, overlap, locking, read-only permissions, and control-state
transitions. The model defaults to a 16 KiB granule; the QEMU device's existing
granule is reported by register `0x10`—4 KiB on the current Linux host—and this
behavior was not changed here.

`guest.c` is a self-authored EL1 program. `run.py` executes it on the current
research binary using isolated temporary AUX/root files and exclusive QMP/RSP
sockets, without Apple firmware, TSS, or retained disks. The full fixture
verifies:

- Repeated execution of a RAM function inside a protected range while retaining
  raw `exec,nochain,in_asm` TCG logs; caching is judged by actual JIT TB
  address reuse and `CF_COUNT_MASK` rather than timing thresholds.
- A CPU write triggers a synchronous external write abort with all 512 bytes
  unchanged; an ordinary BDIF DMA write receives a failure status while
  ordinary DMA reads and disk readbacks from the same region match
  byte-for-byte.
- Overlapping, unaligned, inverted, and out-of-RAM ranges are rejected while
  preserving register and lock rules.
- Both disable→legal modification→re-protect and modification under enabled RW
  execute the new instructions; identical code values across RAM/ROMD mappings
  continue to reuse TBs.
- Privileged debug modification becomes visible to protected reads and code
  updates leave no stale TBs; original RAM data persists across
  unrealize/re-realize; machine reset clears all ranges/locks and permits
  re-protection and write faulting again.
- `info ramblock` across three stages matches the pre-launch state identically,
  adding or replacing no RAMBlock; `info jit` and `info mtree -f` are also
  saved for review.

Default results are written to `runs/e05-ctrr-qemu-guest/results.json`.
`--output-dir` selects an isolated output directory; `--baseline` runs only the
short code/fault/DMA subset. `--expect-cache uncached` is used for baseline
checks against legacy MMIO binaries; the current fixed build requires cached
execution by default.

## Observed Results

| Sample | Executions of Same RAM Entry | Distinct JIT Entry TBs | Forced Single-Insn Entries |
|---|---:|---:|---:|
| Legacy MMIO baseline | 65 | 65 | 64 |
| Post-fix identical-byte short fixture | 65 | 1 | 0 |
| Full lifecycle / SMC fixture | 516 | 6 (six code versions) | 0 |

Both short fixtures shared the same input binary digest. The full sample exited
0 with CPU faults, DMA errors, and shared-RAM/lifecycle checks all passing; it
validates only the self-authored guest device contract, while the stock restore
path continues to be validated separately on the main line. Summaries, build
metadata, and raw logs reside in
`runs/m2-ctrr-ram-cache-20261030/`.
