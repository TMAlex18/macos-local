# E06: PAuth instructions and an opt-in VMApple HVC reference model

This experiment has two separate questions:

1. Do actual AArch64 PAC/AUT instructions, key changes and bad signatures work?
2. Does a board HVC service update state and honor privilege transitions?

Neither proves Apple's implementation-defined cipher/key derivation or
compatibility with macOS 27.0.1. No Apple firmware or XNU is executed.

## Commands

    make pauth-baseline
    make pauth-model
    make pauth-model-impdef
    make pauth-cost-bench
    make pauth-guards
    python3 experiments/E06-pauth-hvc/test-pac-mult.py
    python3 experiments/E06-pauth-hvc/run-smp4.py --vcpus 2 4 8

`pauth-baseline` executes self-authored firmware on both the headless
VMApple build and the modern Reims VMApple build, model off. It checks all
four address-key PAC/AUT pairs, wrong modifier/key authentication failure,
key restoration, XPACI, PACGA and the current HVC returns. The baseline uses
`-cpu max`: its default implementation-defined PAC algorithm is explicitly
non-cryptographic. It is an instruction/state baseline, not a security test.

`pauth-model` uses `-cpu max,pauth-qarma5=on` and
`-M vmapple,...,x-pac-software-model=on`. The flag defaults OFF.
The firmware verifies initialization/defaults, A/B/G rekey and restoration,
negative authentication, explicit EL1 diversifier override, actual ERET to
EL0 and SVC back, automatic key-bank selection, EL0 HVC rejection, invalid
and unknown service calls, two vCPUs, independent per-CPU keys, and CPU_OFF /
CPU_ON clearing the second CPU's service state.

`pauth-model-impdef` builds the same firmware with `-DMODEL_ACCEPT_IMPDEF`, which
only widens the ID-register precondition to accept the impdef `API` field, and
runs every model check under `-cpu max,pauth-impdef=on`. Its log and results go
to `runs/e06-pauth-hvc-model-impdef*`; the QARMA5 results are not touched. A
pass shows the HVC key-state model does not depend on the PAC algorithm.

`pauth-cost-bench` times 20,000,000 calls to one leaf function on `-machine
virt` with the live-run QEMU binary. On 2026-10-29 a PACIBSP/RETAB call cost
47.6 ns (impdef), 370.6 ns (QARMA3) and 553.5 ns (QARMA5) more than a plain
call, which itself took about 11 ns. This is emulation cost only, from a
self-authored guest; it is not Apple's algorithm or a macOS boot measurement.

`pauth-guards` verifies non-PAuth CPU refusal, no-migration refusal and that
the unchanged model-off profile does not silently provide the service.
It relies on the preceding model run's exact command and compiled firmware.

## Packed matrix equivalence

Patch `0018-pauth-packed-mult.patch` replaces only the scalar `pac_mult`
matrix with unsigned packed nibble rotations; QARMA rounds, substitutions,
keys and algorithm selection stay identical. For four 16-bit rows, the
matrix's nibble rotation counts are `[0,1,2,1]`, `[1,0,1,2]`, `[2,1,0,1]`,
`[1,2,1,0]`, where zero means no contribution. Rotating each nibble by one
bit and moving it by either adjacent row, then XORing the opposite row's
two-bit nibble rotation, implements that same matrix.

`test-pac-mult.py` extracts the original complete ComputePAC implementation
from pinned commit `1a72df984914fb9929d35b1217619178133d65a4` and the candidate
from the actual worktree. It checks the matrix, QARMA5 substitutions,
cell/tweak permutations and their scalar helpers independently. QARMA3's
`pac_sub1`, rounds, PAC insertion and authentication must remain byte-identical.
When the optional memo wrapper is present, the same command also invokes
`test-pac-memo.py` against the pinned scalar ComputePAC oracle; a memo failure
fails the combined check. An independent scalar array/matrix oracle checks zero
and all 64 basis vectors (the complete linear map), every bit pair, every
possible four-cell column at each position, and one million random inputs.
Full 64-bit ComputePAC comparisons cover QARMA3 and QARMA5, independent bit
changes in data/modifier/both key halves, edge combinations, and 100,000
random tuples per algorithm. The upstream `pauth-3.c` QARMA5 vector is
checked both at its original high-32-bit boundary and at the full literal
answer `c003b93999b33765`.

The 2026-10-31 native check passed 1,264,297 matrix cases and 208,705 complete
ComputePAC comparisons with GCC 15.2, `-O2` and UBSan. Source hashes, compiler
command and generated source are retained under
`runs/e06-pauth-packed-mult/20261031T073039Z/`. This is arithmetic equivalence
evidence only.

The actual BDIF binary was then rebuilt and tested using the same self-authored
guests on both the retained pre-0018 binary and the new canonical binary:

    python3 experiments/E06-pauth-hvc/compare-pac-consumers.py \
      --before build/e06-pauth-packed-mult/qemu-system-aarch64-before-0018 --cpu 3

Both binaries passed QARMA5 and QARMA3 instruction tests (IA/IB/DA/DB,
negative modifier/key, restoration, XPACI, PACGA and model-off HVC) and the
QARMA5 HVC model's EL0/EL1 transitions, rekey, privilege checks, two-CPU
isolation and CPU_OFF/ON state reset. This runner explicitly selects the
BDIF binaries; the older `run.py` and `run-model.py` use other builds.

Three paired rounds in AB/BA/AB order used the identical 20,000,000-call
EL1 guest, fixed host CPU 3 on an Intel Core Ultra 7 255HX, and the existing
`powersave` governor without changing host settings. Median costs were:

| Guest | Before process time | After process time | Extra ns/call before / after | Extra-cost reduction |
| --- | ---: | ---: | ---: | ---: |
| Plain RET | 0.181681 s | 0.182345 s | — | — |
| PACIBSP/RETAB, QARMA5 | 11.191638 s | 7.262949 s | 550.50 / 354.03 | 35.69% |
| PACIBSP/RETAB, QARMA3 | 7.543270 s | 5.045584 s | 368.08 / 243.16 | 33.94% |

The extra cost subtracts each binary's own plain median. Whole-process
QARMA5/QARMA3 speedups were 1.541x/1.495x. Every sample, command, compiled
guest hash, binary hash and resolved dynamic dependency hash is recorded in
`runs/e06-pauth-packed-mult/20261031T074612Z-consumers/results.json`.
The before binary is a retained performance comparison input, with provenance
in `build/e06-pauth-packed-mult/before-0018.json`; its SHA-256 starts `4c11efbf`.
That comparison's rebuilt binary starts `6731911f`; the result above retains
its exact hash. `runs/qemu-bdif-usb-build.json` describes the current build.
Existing patch hashes, device profile and Nettle configuration stayed identical;
only patch 0018 was added.
The source chain reconstructed all 25 affected files exactly. These measured
improvements apply to self-authored guest emulation cost; installed macOS/GUI
progress remains a separate consumer measurement.

## Packed QARMA5 substitutions

Patch `0020-pauth-packed-qarma5-sbox.patch` replaces the two
QARMA5 substitution loops with four packed bit planes, three shared
intermediates and four factored Boolean output expressions per function.
The comments retain the exact original truth tables. Bitwise complements
are always bounded by another masked plane; no operation mixes cells.
QARMA3's `pac_sub1`, shuffle, rounds, keys and authentication are unchanged.

The same `test-pac-mult.py` command now also compares both substitutions
against the pinned scalar table functions and checks both inverse
compositions. Every lane sees all 16 values with zero, all-one and mixed
neighbors; additional inputs cover all bit pairs, exhaustive columns,
edges and one million random words. The 2026-10-31 run passed 1,265,081
S-box cases, 1,264,297 matrix cases and 208,705 full 64-bit ComputePAC
comparisons, including QARMA3/5 and the upstream known answer, under
GCC 15.2 `-O2` with UBSan. Exact source reconstruction passed for 26 files.
Evidence and generated source are in
`runs/e06-pauth-packed-mult/20261031T122038Z/`.

A bounded native screening probe used 20,000 dependent ComputePAC calls
per sample, three AB/BA/AB pairs per algorithm, CPU 19 and nice 19. Both
sides include patch 0018; only the QARMA5 S-boxes differ. The whole probe's
measured calls took 35.95 ms. Median QARMA5 sample time was 3.902 ms before
and 3.017 ms after (22.67% less); QARMA3 was 2.522 ms and 2.532 ms (+0.39%).
The short samples cannot distinguish that small change from measurement noise.
All final digests matched. `native-cost.json`
records the exact C source hash, compiler command and individual samples.
This remains only a native screening result, with scheduling/frequency noise.

After the separate macOS run ended, the actual BDIF consumer comparison used:

    python3 experiments/E06-pauth-hvc/compare-pac-consumers.py \
      --before build/e06-pauth-packed-sbox/qemu-system-aarch64-before-0020 --cpu 3

All six before/after QARMA3/5 instruction and QARMA5 two-CPU HVC cases passed,
including negative modifier/key, EL0/EL1 transitions, rekey and CPU_OFF/ON.
The retained before binary starts `cdaa7a94` (0018 + 0019); the after binary
starts `880163e9` (adding only 0020). Both retain the same Reims 0007 library
(`libreims_vgpu.a` SHA-256 starts `44d3fe9e`), existing compiler patch set,
device profile and Nettle configuration. Only `pauth_helper.c` was recompiled
before relinking QEMU.

The same CPU 3, 20M-call guest and three AB/BA/AB rounds gave these medians:

| Guest | Before process time | After process time | Extra ns/call before / after | Extra-cost change |
| --- | ---: | ---: | ---: | ---: |
| Plain RET | 0.183074 s | 0.188802 s | — | — |
| PACIBSP/RETAB, QARMA5 | 7.247623 s | 5.609718 s | 353.23 / 271.05 | -23.27% |
| PACIBSP/RETAB, QARMA3 | 5.028514 s | 5.073331 s | 242.27 / 244.23 | +0.81% |

QARMA5 whole-process speedup was 1.292x. QARMA3 remained functionally correct
but all three pairs were slightly slower, so this is an observed small cost
increase, not a demonstrated timing-noise explanation. A bounded follow-up
PMU measurement was refused by `perf_event_paranoid=4` / missing capabilities;
no permission settings were changed and the cause remains unresolved.

The complete 18 samples, exact commands, guest/binary hashes and 55
`ldd`-resolved ELF dependency hashes are in
`runs/e06-pauth-packed-sbox/20261031T130649Z-consumers/results.json`; the PMU
refusal is recorded alongside it in `qarma3-pmu.json`. The dependency scope
excludes kernel vDSO and runtime-loaded graphics drivers. The old comparison
binary's build manifest and Reims archive/DSO hashes are retained in
`build/e06-pauth-packed-sbox/before-0020.json`. Generated object files and
blank drives were removed; guest ELF/bin files and logs remain. These are
self-authored guest CPU costs, not an installed macOS or GUI improvement claim.

## Grouped cell and tweak permutations

Patch `0021-pauth-grouped-permutations.patch` is the sole active source for the
grouped permutation change. It merges cell displacements modulo 64 into 11
masked rotation groups for each cell shuffle and six for each tweak shuffle.
The original feedback is applied to exactly the same seven tweak cells with
packed masks; the unused scalar feedback helpers are removed. All operations
are portable unsigned integer operations, without lookup tables or ISA-specific
instructions. Algorithm choice, rounds, keys and authentication are unchanged.

`test-pac-mult.py` now includes four-helper scalar comparisons and both inverse
compositions. Each helper passes zero, all 64 basis vectors, all bit pairs,
exhaustive columns and random words: 1,264,297 inputs per helper. The existing
matrix/S-box cases, 208,705 full QARMA3/5 ComputePAC comparisons and upstream
known answer also pass with GCC 15.2 `-O2` and UBSan. The integrated result is
`runs/e06-pauth-packed-mult/20261031T160436Z/results.json`; the fixed patch chain
reconstructs all 26 affected files exactly.

The earlier CPU19 screening evidence remains unchanged under
`runs/e06-pauth-permutation/20261031T144504Z/`, including the generated full
candidate, `check.c`, `cost.c`, raw candidate patch, compiler commands and all
hashes. Its pending-candidate descriptions record that historical stage.
The obsolete fixed-before probe script and duplicate experimental patch were
removed after their checks were integrated into `test-pac-mult.py`; current
consumers use the formal 0021 patch and the actual QEMU results below.

The retained before binary is `build/e06-pauth-permutation/qemu-system-aarch64-before-0021`
(SHA-256 starts `880163e9`), with its build and dependency provenance in
`build/e06-pauth-permutation/before-0021.json`. The after binary starts `21635e42`.
Only `pauth_helper.c` was rebuilt before linking; existing patches, Nettle/device
configuration, Reims `44d3fe9e` archive and compiler patch provenance stayed
identical. The comparison command is:

    python3 experiments/E06-pauth-hvc/compare-pac-consumers.py \
      --before build/e06-pauth-permutation/qemu-system-aarch64-before-0021 --cpu 3

All six QARMA3/5 instruction and two-CPU HVC consumers passed. Fixed CPU 3,
the same 20M-call guest and three AB/BA/AB rounds produced these medians:

| Guest | Before process time | After process time | Extra ns/call before / after | Extra-cost reduction |
| --- | ---: | ---: | ---: | ---: |
| Plain RET | 0.184950 s | 0.188498 s | — | — |
| PACIBSP/RETAB, QARMA5 | 5.626310 s | 4.461454 s | 272.07 / 213.65 | 21.47% |
| PACIBSP/RETAB, QARMA3 | 5.095589 s | 4.217601 s | 245.53 / 201.46 | 17.95% |

Whole-process speedups were 1.261x and 1.208x respectively. The complete
18 samples, commands, guest/binary hashes and 55 resolved ELF dependency hashes
are in `runs/e06-pauth-permutation/20261031T160552Z-consumers/results.json`.
The dependency scope excludes kernel vDSO and runtime-loaded graphics drivers.
These are self-authored CPU emulation costs; no installed OS or GUI benefit
is established by these measurements.

## Multi-CPU PSCI, PAC and EL0 acceptance

`run-smp4.py` retains its historical filename but now accepts `--vcpus 2 4 8`
and compiles `model-smp4.c` with the corresponding `CPUS` value. Without that
option it runs 2 and 4 CPUs. Every selected size uses the same stronger per-CPU
contract, QARMA5 and `tcg,thread=multi`. The binary hash must match the current
build manifest, or an explicit `--expected-sha256` with `--qemu` for a retained
comparator. The actual hash and manifest are recorded. It does not rebuild QEMU
or use Apple disks. The older two-CPU `model.c` remains a separate control in
`compare-pac-consumers.py`.

The guest gives each CPU independent EL1/EL0 stacks, exception state,
signature slots and counters. Each CPU actually accesses its EL0 stack,
executes all five PAC signing operations, returns through SVC, and receives
an exception for an EL0 HVC. Five bounded release/acquire barriers per
generation hold distinct IA/DA/IB/DB/G keys and user diversifiers concurrently and
check that peer rekeys and EL changes do not alter another CPU's registers.
Negative key/modifier checks and all four address-key PAC/AUT pairs run on
every CPU. Initial defaults are expected to match across CPUs.

Every secondary CPU (1 through N-1) executes two generations. CPU0 observes PSCI OFF
before each CPU_ON and after each CPU_OFF; every secondary reentry verifies
the HVC pre-init result is `-3`. CPU0 remains the coordinator but also executes
both generations' EL0/SVC/HVC checks. Success requires all 2*N distinct
CPU/generation records, including per-secondary successful CPU_ON and confirmed
CPU_OFF counts. Configuring eight CPUs while executing fewer fails.

Before patch 0021, the `880163e9` binary passed the new 8CPU fixture with all
16 records: `runs/e06-pauth-hvc-smp/20261031T160318Z/results.json`. After the
patch, `21635e42` passed 2/4/8 CPUs with 4/8/16 records respectively:
`runs/e06-pauth-hvc-smp/20261031T160536Z/results.json`. The original two-CPU
control/four-CPU acceptance remains historical evidence at
`runs/e06-pauth-hvc-smp4/20261031T143113Z/results.json`.

These directories retain UART/stderr logs, per-CPU stack/MPIDR/exception/counter
records, source/firmware hashes and compile/run commands. All processes exited
0 and were reaped; the selected binary stayed unchanged during each run.
Generated objects and blank drives were removed. VMApple necessarily initializes
its headless Reims/Vulkan device with `REIMS_VGPU_WINDOW=0`; the guests submit
no graphics commands. This checks CPU/model semantics, not installed macOS
scaling or GUI throughput.

## Default-off per-vCPU ComputePAC memoization candidate

Local research patch `0022-pauth-per-vcpu-memo.patch` adds the experimental CPU
property `x-pauth-memo`, default **off**. The same candidate binary accepts
`max,pauth-qarma5=on,x-pauth-memo=off` and `...x-pauth-memo=on`; QARMA3 is also
covered. The inexpensive implementation-defined algorithm stays uncached.

The original 0022 candidate uses 64 entries (3072 bytes) per vCPU. The index uses only data/modifier bits;
every hit compares all 64 data bits, all 64 modifier bits, both 64-bit key
halves and the selected QARMA algorithm. Collisions replace one entry. Only
the pure ComputePAC result is reused: PAC insertion, authentication checks,
key selection and HVC key semantics are unchanged. No tuple, key or result
is logged by the candidate. Reset clears the cache; it is not serialized, and
post-load clears it. The latter is a structural check, not a migration claim.

The candidate is `build/qemu-pauth-memo-20261104/qemu-system-aarch64`, SHA-256
`7737c21e5a38a354388b0926b84ef0faa308f7abc14eb9812fbd955b575bc2f8`.
Its build manifest is `runs/e06-pauth-memo-20261104/build.json`. It was built
in an independent directory with four jobs, reading the existing Reims archive;
the active main binary, Reims archive and main build record were unchanged.
All 27 changed QEMU source files reconstruct from the pinned baseline and
recorded patches including 0022.

`test-pac-memo.py --output NEW_DIRECTORY` extracts the actual wrapper and cache
layout and checks its results against pinned scalar QARMA3/5 code. The accepted
run has 401817 checks, including 200647 hits and 201170 misses, known answers,
each input/key bit, forced collisions, algorithm changes, per-vCPU isolation,
reset, default-off and impdef paths. These counts are generated by the native
test harness; that original candidate has no production counter or key-reading interface.
The existing primitive suite and its 208705 complete ComputePAC comparisons
also pass. Evidence: `runs/e06-pauth-memo-20261104/arithmetic-and-memo/`.

The standalone consumer runner is:

```sh
python3 experiments/E06-pauth-hvc/run-pac-memo.py \
  --qemu build/qemu-pauth-memo-20261104/qemu-system-aarch64 \
  --output NEW_DIRECTORY --cpu 3
```

It builds only self-authored test guests, never QEMU. Eight baseline/HVC cases
passed with memoization off/on, including all three algorithms, wrong key and
modifier rejection, restoration, EL0 transitions and the two-CPU HVC model.
The existing `run-smp4.py` now accepts optional `--pauth-memo off|on`; omitting
it preserves the original CPU argument. Both modes passed 2/4/8 CPUs with all
4/8/16 CPU-generation records and PSCI OFF/ON key-state isolation.

The baseline also now flips one bit added by PAC while leaving the raw
data/modifier/key tuple unchanged. This exercises AUT rejection even when
the memo entry from signing can hit. All three algorithms passed this stronger
baseline with the candidate off/on (six cases), including fault-counter deltas
for each independent negative. Current evidence is
`runs/e06-pauth-memo-20261104/same-tuple-auth/`; it supersedes the earlier six
baseline cases in the combined consumer run, without invalidating its HVC,
SMP or timing results.

On 2026-11-04, the same candidate ran 20 million iterations per sample on host
CPU 3, three off/on pairs in AB/BA/AB order (48 samples including plain controls).
Median whole-process times were:

| Algorithm / self-authored workload | Off | On | Off/on ratio |
|---|---:|---:|---:|
| QARMA5 repeated PACIBSP/RETAB leaf | 4.5468 s | 0.9164 s | 4.962x |
| QARMA5 changing SP, paired PAC/AUT | 4.5367 s | 2.7868 s | 1.628x |
| QARMA5 all-miss PACGA stream | 1.8765 s | 1.8915 s | 0.992x |
| QARMA3 repeated PACIBSP/RETAB leaf | 4.2846 s | 0.9189 s | 4.663x |
| QARMA3 changing SP, paired PAC/AUT | 4.2943 s | 2.6748 s | 1.605x |
| QARMA3 all-miss PACGA stream | 1.7777 s | 1.7851 s | 0.996x |

The repeated leaf is favorable cache reuse; changing SP retains sign/auth
reuse; the stream measures overhead when no tuple repeats. UART accumulators
match across off/on runs. Plain controls were about 0.187 seconds in both modes.
Run85 remained live during these measurements, so background load was not
isolated. These paired microbenchmarks include process startup and establish
no macOS, Safari, watchdog or overall OS speedup. Real workload acceptance
remains separate. All commands, inputs and samples are retained under
`runs/e06-pauth-memo-20261104/consumers/`.

### Opt-in capacity and aggregate statistics (0024, 0029)

`0024-pauth-memo-capacity-stats.patch` keeps the active capacity at 64 by
default and adds `x-pauth-memo-entries=64|1024`. Both configurations use the
same 1024-slot allocation (48 KiB per vCPU) in the 0024 implementation and
identical complete tags;
only the index mask changes. The fixed allocation also keeps CPU layout
identical for an A/B comparison on one binary. PAC insertion, authentication,
keys, QARMA and clocks are unchanged. Reset and migration post-load discard
the derived entries.

`x-pauth-memo-stats=on`, separately default off, enables aggregate per-vCPU
`x-pauth-memo-hits` and `x-pauth-memo-misses` read-only QOM properties. The
vCPU is the single counter writer; atomic loads/stores allow asynchronous
QOM reads without locked read-modify-write operations. Reset/post-load clears
the counters with atomic stores. The two property reads are not an atomic
pair. No key, pointer, PAC input or cached result is exposed. Capacity and
statistics require `x-pauth-memo=on`.

`0029-pauth-memo-16384.patch` additionally accepts 16384, preserving the default
64 and existing 1024 setting. It extends the fixed array to 16,384 slots without
changing its hash, full tags or crypto. With 48-byte entries the array is now
768 KiB per vCPU in **every** mode, including memo off and smaller capacities;
eight vCPUs reserve 6 MiB. The cache portion of each reset/post-load clear also
writes 768 KiB, a 720 KiB increase per vCPU from 0024. These are storage/clear
byte counts, not measured duration or performance. Same-binary 1024/16384
comparisons share this allocation and layout.

The native scalar oracle covers all three capacities, optional counters,
full-tag collisions and aligned PC/modifier streams. Such streams establish
the low-bit collision mechanism but do not establish the macOS miss rate or
speedup. Actual CPU/QOM integration and same-binary installed-OS comparisons
remain separate from the scalar checks.

After building 0029, the current diskless actual-CPU/QOM check is:

```sh
python3 experiments/E06-pauth-hvc/test-pac-capacity-qmp.py \
  --qemu build/qemu-bdif-usb/qemu-system-aarch64 --output NEW_DIRECTORY
```

Its self-authored guest makes two passes through 16 aligned tuples, then
waits. The check observes exact aggregate counts, an idle secondary CPU,
read-only counter properties, statistics disabled, and a second generation
after system reset. A second stride drives indices 1024 apart, demonstrating
misses at 1024 and exact reuse at 16384, including entries above the previous
allocation boundary. The native check also fills and clears every active slot.
The existing `run-pac-memo.py --functional-only` and
`run-smp4.py --pauth-memo on` accept `--pauth-memo-entries 64|1024|16384` for
the existing PAuth/HVC/EL0/PSCI consumers. These are functional controls;
they do not measure application startup.

Main build `a931ea0beb1ff11ef09379789b9ae72739228b2307981be31c6558a665f5fb16`
passed the actual diskless QOM check: omitted capacity defaults to 64 with
statistics disabled; explicit 64 has 0 hits/32 misses and explicit 1024 has
16 hits/16 misses in each of two reset generations. CPU1 stays at zero;
counter writes and three invalid configurations are rejected. All three
successful QEMU instances exited 0 and were reaped. Evidence:
`runs/e06-pauth-memo-capacity-20261107/main-binary-qom/results.json`.
The earlier 807734 native scalar-oracle checks remain separate evidence;
no installed-OS miss-rate or speedup conclusion follows from these controls.

The 0029 main build is
`a96b3f9750da901ac4d23093f923e817046351050c7ea60c4535fc0571c45fa1`.
Its native oracle
passed 1,275,159 checks across all three capacities under UBSan. The
actual CPU/QOM cases
passed both reset generations, read-only counter and invalid-capacity checks;
the high-slot pattern produced 0 hits/32 misses at 1024 and 16 hits/16 misses
at 16384. The linked main-binary PAuth/HVC/EL0 and 2/4/8-CPU cases passed with
memo off and with 16384 enabled. The previous actual 8f0b build passed the old
capacity preflight and rejected 16384; the rebuilt main binary passes all
three preflights. These establish correctness and capacity behavior only;
installed macOS/application benefit is still unverified.

The subsequent saved-tweak/native-frame experiment was **closed without a
mainline patch**. Saving the forward schedule gave only about 0.8–0.9% native
QARMA5 off/miss improvement, and an inlined array added a stack canary before
memo lookup. A smaller noinline-only boundary improved native hot-call timing,
but the real TCG A/B/B/A comparison
did not justify promotion: QARMA5 off was neutral, ordinary hits were about
1.1% slower, and the stats-hit gain was only about 0.6%; QARMA3 showed no
meaningful improvement. All 64 self-authored guest runs returned matching
checksums and exited normally. The annotation and provisional 0030 registration
were withdrawn, and the normal build reproduced the exact a96b hash above.
Raw native/TCG measurements, generated oracle sources and both measured QEMU
executables are retained as experiment evidence; the maintained round-body
guard and oracle interfaces are restored. No macOS run used this candidate.

## What was implemented

Cumulative patch `patches/qemu-platform/0003-vmapple-tcg-pac-model-stack.patch`
replaces architecture-only patch 0002 for current build scripts; DO NOT apply
both patches sequentially. The original 0002 remains a historical control.

- Optional board-owned HVC/reset callbacks on ARMCPU; no callback = original PSCI.
- Recognizes only the configured EL1/AArch64 HVC CPU-service range.
- Implements function IDs 0..6 and 0xf0, with no guest binary patching.
- Fresh per-VM secret and default input keys from qcrypto_random_bytes.
- Domain-separated HMAC-SHA256 (truncated to 128 bits) derives local-model
  IA/DA/IB/DB/G key banks. This KDF is OUR choice, not reverse-engineered Apple.
- EL0 uses a diversifier for A/B; G is not diversified. A controlled override
  enables the EL0 bank while executing EL1. Actual EL transitions invoke a hook.
- Per-vCPU reset clears service state; callers explicitly initialize each CPU.
- Unknown calls return -1, pre-init operations -3, invalid boolean -2. The
  latter two errors are explicit MODEL behavior, not asserted Apple ABI facts.
- Migration is refused while enabled because no state format was implemented.

There is no Apple hardware root secret, native Apple cipher, persistent PAC
identity, VM-state migration, OEM clock/boot-session/WFK service, or OS boot
claim. Direct guest writes to PAC registers after initializing the managed
service are outside the tested contract; subsequent managed transitions
reapply the service's bank. No production security guarantee is asserted.

## Sources and provenance

The service IDs are confirmed in Apple's public XNU hv_hvc.h at
f6217f891ac0bb64f3d375211650a4c1ff8ca1ea and xnu-8796.141.3. This does NOT prove
that the selected macOS 27 kernel uses exactly the same full contract.

The older prose doc/vmapple_pac.md was available in the matteyeux mirror,
but was NOT present at the checked official XNU tag/main paths. Treat that
prose as a legacy semantic reference with this provenance limit. Do not say
it was downloaded from the current official tree or independently confirmed.

QEMU documents that its default impdef PAC is non-cryptographic and that
QARMA3/5 are distinct optional algorithms. See references.json.

Logs and downloaded reference material are under runs/e06-pac-* (ignored).
Self-written firmware, tests, source URLs and patch metadata are versioned.
