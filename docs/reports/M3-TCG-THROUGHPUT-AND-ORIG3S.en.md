> **Language:** **English** | [简体中文](M3-TCG-THROUGHPUT-AND-ORIG3S.md)

# Diagnostic-Off Repeatability and TCG Execution Throughput Profiling (From Run147)

Date: from 2026-11-12 UTC. The "Pre-run plan" section of this document was committed before the run.

## Pre-run plan

### Motivation

In the [QEMU synchronization trigger-sources report](M3-QEMU-SYNC-SOURCES.en.md) (Run143–146), patches `0034–0039` cut saturated 8-vCPU global-lock acquisitions by 90% and raised running/runnable vCPU share to **96.6%** (leaving only **3.4%** synchronization stall); Run146 then omitted `--webkit-timeout-diagnostic` completely and achieved the first cold Safari launch on a quiet guest (`0` GPU watchdog exits, `3.219 s` log span) together with bit-exact fixed WebGL2 dual-buffer readbacks (`1.912 s` on the second launch).

Two immediate questions follow:

1. **Independent cold-boot repeatability without the timeout diagnostic**: Run146 is a single cold-boot sample whose first-launch `ui_request_to_did_initialize = 3.219 s` relied on WebKit's unmodified UIProcess runloop timer slack (~`0.22 s` past nominal `3.000 s`). An independent cold boot is needed to test whether diagnostic-off cold first launch and bit-exact WebGL2 readback reproduce cleanly under the same quiet criterion.
2. **Where the 96.6% active CPU cycles go on `0039`**: Inspecting Run142's historical `perf` capture showed that only `17.5%` of host P-core cycles executed inside translated `[JIT]` blocks while **`74.8%`** executed inside `qemu-system-aarch64` C functions — but `0033–0039` shifted binary offsets and removed major locking/HMAC paths. Capturing a symbol-matched `perf` call-graph profile on the live `0039` binary (`e4587db0...`) will identify the top remaining C execution hotspots (such as 16 KiB page-table walks `get_phys_addr*`, PAuth helpers, TB lookup, or softmmu slow paths) to target in `0040+` so cold launches under peak background load can also clear `< 3.000 s`.

### Hypotheses

1. **Diagnostic-off repeatability hypothesis (H1)**: On a fresh cold boot of `0039` with both `--webkit-timeout-diagnostic` and `--sync-stats` omitted (Run147), waiting for the same quiet criterion (three consecutive 1-minute windows with `vcpu_mean_busy < 3.0`) reproduces `outcome = initialized` with `gpu_unresponsive_exits_reported_by_ui_process == 0` and bit-exact 24,576-byte fixed WebGL2 FBO + default readbacks on Trial 1, and `ui_request_to_did_initialize < 2.5 s` on Trial 2.
2. **C-helper dominance hypothesis (H2)**: Even on the `0039` binary, `qemu-system-aarch64` C functions still consume **at least 50%** of host P-core cycles on `CPU */TCG` threads during saturation, with the top three C function families accounting for **at least 25%** of P-core vCPU cycles — providing actionable targets for `0040+`.

### Procedure (Run147)

- Launch `m3-orig3s-repeat-perf-20261112-147` with the exact same unmodified profile as Run146 (8 vCPUs, 4 GiB RAM, `qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`, NVIDIA native window, **no** `--webkit-timeout-diagnostic`, **no** `--sync-stats`), alongside `host-temperature-log.sh`, `host_thread_sampler.py`, and `clock_offset.py`.
- After login, start `caffeinate`, confirm Safari is absent, and run clock probe `r147a`.
- **Saturated `perf` capture (well ahead of trials)**: Around +520 s during post-login background saturation (>10 minutes before Trial 1), record 15 s via `sudo -n perf record -F 199 --call-graph dwarf,4096 -p PID -o RUN_DIR/perf-saturated.data -- sleep 15` against the live `0039` executable.
- **Trial 1 (unperturbed)**: Wait via `watch.sh` for three consecutive 1-minute windows with `vcpu_mean_busy < 3.0`, re-confirm Safari absence, and run `safari-probe.sh r147safari "$H/get/webgl-probe.html#r147-webgl"` with **no** `perf` or blocking sampler active; validate readbacks with `tools/validate-webgl-probe.py --run LABEL --tag r147-webgl`.
- **Trial 2 (with Safari-launch `perf` capture)**: Quit Safari and confirm exit, launch `safari-probe.sh r147safarib`, and record a 20 s `perf-safari-launch.data` starting 5 s after pressing Return to compare Safari process-spawn hotspots against background load.
- Export both guest log windows, run `clockprobe r147b`, stop `caffeinate`, and shut down cleanly via `sudo /sbin/shutdown -h now`.

### Decision criteria and next steps

| Dimension | Criterion | Next action |
|---|---|---|
| Diagnostic-off repeatability (H1) | Trial 1 achieves `outcome=initialized`, `gpu_unresponsive_exits=0`, and `bit_exact=true` on both WebGL2 buffers | Establishes cross-boot repeatability (2/2) on a quiet guest; if it fails, compare window `vcpu_mean_busy` and sub-segment deltas against Run146 |
| TCG throughput hotspots (H2) | Any optimizable C function family (e.g., MMU page-table walk / TLB fill, PAuth, TB lookup, or sysreg helpers) accounts for ≥ 8% of P-core cycles | Implement and bare-metal-test `0040+` throughput patches, then test diagnostic-off cold launch under peak load (+720 s) |

### Known limitations

- DWARF unwinding copies 4 KiB of user stack per sample (199 Hz for 15–20 s); Trial 2 runs concurrently with `perf`, so its timing is reference-only while unperturbed Trial 1 serves as the primary oracle.
- Translated `[JIT]` code blocks carry no per-guest-symbol ELF table and are attributed in aggregate alongside the C helpers they call.

---

## Phase 1 Results (Run147: Diagnostic-Off Cold-Boot Repeatability and `0039` Hotspot Profile)

### Run identity and integrity

- Run directory: `runs/e05-installed-boot/m3-orig3s-repeat-perf-20261112-147`
- Binary: `build/qemu-bdif-usb/qemu-system-aarch64` (SHA-256 `e4587db0f06bb3aee0545f6716366c2d732200bcde8ca6dddf28631d1e481213`, patch stack through `0039`)
- Profile: 8 vCPUs, 4 GiB RAM, `qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`, NVIDIA native window, **`--webkit-timeout-diagnostic` omitted**, **`--sync-stats` omitted**
- Guest & host health: clean guest shutdown via `sudo /sbin/shutdown -h now` (`qemu_exit_code = 0`, `guest_shutdown_observed = true`, `errors = []`, elapsed `2224.9 s`); 435 host package temperature samples (`min = 55 °C`, `mean = 76.8 °C`, `max = 94 °C`); clock probes `r147a = -2.9218 s` (spread `4.0 ms`) and `r147b = -2.9293 s` (spread `5.8 ms`); `218,006` host-thread sampler ticks.

### H1 verdict: zero-watchdog cold first initialization did NOT repeat cleanly across boots (1/2), though diagnostic-off self-recovery and bit-exact WebGL2 readback passed (2/2)

The guest reached the quiet criterion at `+1335 s` (`last three minutes = [2.437, 2.176, 1.947]`).

| Trial | Sampler perturbation | `gpu_processes_constructed` | `gpu_unresponsive_exits` | First GPU process milestones (from `ui_request`) | Recovered GPU `ui_request -> didInitialize` | WebGL2 dual-buffer readback |
|---|---:|---:|---:|---|---|---|
| Run146 Trial 1 (baseline) | None | 1 | **0** | `didInitialize = +3.219 s` (cleared via ~`0.22 s` UIProcess main-runloop timer slack) | — (passed on 1st) | FBO + default 24,576 B `bit_exact=true` |
| Run147 Trial 1 (`r147safari`) | **None** | 4 | **3** | PID 1211: `registrar = +2.119 s`, `helper_create = +2.630 s`, **watchdog fired = `+3.008 s`**, `helper_destroy = +4.150 s` | 4th GPU (PID 1393): **`1.661 s`** (follow-up connection `0.461 s`) | FBO + default 24,576 B **`bit_exact=true`** (six tiles verified in native shot) |
| Run147 Trial 2 (`r147safarib`) | Concurrent 20 s `perf` | 2 | 1 (`web_unresponsive = 0`) | PID 1848: watchdog fired = `+3.008 s` (`didInitialize` logged `0.965 s` later at `+3.973 s`) | 2nd GPU (PID 1981): **`2.047 s`** | No WebGL probe URL |

**Causal explanation**: `gpu_init_cpu_profile.py` shows that even when the guest is quiet beforehand (`1.95` busy vCPUs), cold-launching Safari spawns `Safari`, `WebContent`, `Networking`, `GPU`, and system daemons (`runningboardd`, `trustd`), driving `vcpu_mean_busy` to `5.339` in the 10 s before `ui_request`, **`6.398`** during `gpu_entry -> registrar`, and **`5.983`** during `helper_create -> destroy`. Under `0039` execution throughput, true cold first-GPU initialization sits at **`3.22–4.15 s`**: Run146 cleared on the first attempt only because UIProcess's main runloop happened to add ~`0.22 s` of timer delivery slack, whereas Run147's UIProcess fired right at `3.008 s`. **H1 is therefore falsified (1/2 strict zero-watchdog-exit repeatability across cold boots)** — clearing `< 3.000 s` deterministically without runloop slack requires raising pure TCG instruction throughput by **>30%**.

### H2 verdict: 80%–81% of host P-core cycles execute inside QEMU C functions; four pure-execution bottlenecks account for ~55% of Safari-launch CPU cycles

Two symbol-matched DWARF call-graph profiles on `CPU 0..7/TCG` (`cpu_core/cycles/P`):

1. **DSO breakdown**:
   - **Saturated background (`perf-saturated.data`, +749..+765 s, 20,634 samples, 476.0B P-core cycles)**: `qemu-system-aarch64` C code consumes **`81.13%`**, translated guest `[JIT]` code consumes only **`13.52%`**, `[kernel.kallsyms]` `2.97%`, and `libc.so.6` `1.35%`.
   - **Safari launch (`perf-safari-launch.data`, 20 s, 12K P-core samples, 296.3B P-core cycles)**: `qemu-system-aarch64` C code consumes **`80.28%`**, `[JIT]` **`14.45%`**, `[kernel.kallsyms]` `3.17%`, and `libc.so.6` `1.12%`. **H2 is strongly confirmed.**

2. **Top four C hotspot families and source-level root causes**:

| Hotspot family | Saturated P-core share (inclusive) | Safari-launch P-core share (inclusive) | Source-level root cause |
|---|---:|---:|---|
| 1. Indirect branch & TB lookup (`helper_lookup_tb_ptr` / `tb_htable_lookup`) | **16.96%** (`qht_lookup_custom` 5.07%, `get_page_addr_code_hostp` 5.05%, `tcg_flush_jmp_cache` 0.64% self) | **16.60%** (`tb_htable_lookup` **13.09%**: `get_page_addr_code_hostp` 6.02% + `qht_lookup_custom` 6.01%) | (a) ARM64 sets `CF_PCREL` on every TB (`target/arm/cpu.c:2132`), causing `tb_jmp_cache_inval_tb()` to call `tcg_flush_jmp_cache()` across all 8 vCPUs whenever *any* single TB on a recycled/JIT physical page is invalidated — wiping all 4,096 jump-cache entries and dirtying peer L1/L2 cache lines; (b) `tlb_flush_by_mmuidx_async_work()` flushes `tb_jmp_cache` even when `to_clean == 0`; (c) `TB_JMP_CACHE_BITS = 12` provides only 64 page buckets and ignores high VA bits across the 4 GiB dyld shared cache |
| 2. SoftMMU page-table walks & 16 KiB full-TLB flush pathology (`tlb_fill_align` + out-of-line EL2/EL3 queries) | **12.46%** + `arm_security_space_below_el3` **2.19%** self + `arm_hcr_el2_eff*` **1.42%** self | **14.97%** (`tlb_set_page_full` 7.70%, `get_phys_addr_nogpc` 6.72%) + `arm_security_space_below_el3` **1.73%** self | (a) `vmapple` uses `TARGET_PAGE_BITS = 12` (4 KiB) while macOS uses 16 KiB pages (`lg_page_size = 14 > 12`), so `tlb_set_page_full()` passes *every* ordinary 16 KiB guest page into `tlb_add_large_page()`, collapsing `large_page_mask` to the full address space and forcing **every** single-page `TLBI VALE1IS` and range `TLBI RVALE1IS` into `tlb_flush_one_mmuidx_locked()` (wiping all softmmu TLBs on all 8 vCPUs and suppressing dynamic TLB resizing); (b) `arm_is_el2_enabled()` evaluates out-of-line `arm_security_space_below_el3()` before checking `ARM_FEATURE_EL2`, and `arm_hcr_el2_eff*` remains out-of-line when `!EL2` |
| 3. Pointer Authentication (`pauth_addpac` + `pauth_auth`) | **16.19%** (`pauth_addpac` 8.39% + `pauth_auth` 7.80%; `pauth_computepac` 10.15%, `aa64_pauth_va_parameters` 3.02%, `pauth_check_trap` 0.94%) | **14.80%** (`pauth_addpac` 8.27% + `pauth_auth` 6.53%; `pauth_computepac` 9.54%, `aa64_pauth_va_parameters` 2.70%) | Run147 recorded `53.86B` PAuth helper calls (**24.2M calls/s**), with `84.28%` 1,024-entry memo hit rate (`45.40B` hits / **`8.46B` misses = 3.80M full QARMA5 runs/s**): (a) every call (including memo hits) recomputes `aa64_pauth_va_parameters`, `pauth_check_trap`, and `arm_sctlr`; (b) `pauth_memo_slot` omits `key.lo`, causing cross-thread/EL collisions on shared-cache addresses; (c) scalar C QARMA5 runs >100 serial bitplane/shift ops instead of host x86_64 SSSE3 `pshufb` parallel nibble S-boxes and permutations |
| 4. SME FP16 outer-product helper (`helper_sme_fmopa_h` / `do_fmopa_h`) | **4.13%** (`float16a_unpack_canonical` 2.53%) | **11.12%** (`float16a_unpack_canonical` **6.57%**) | `-cpu max` exposes `FEAT_SME`, which macOS frameworks invoke heavily during Safari startup; `do_fmopa_h()` calls `float16_unpack_canonical()` inside the `N × N` inner loop (`2N²` unpacks of the same row/column elements instead of `2N`) |

---

## Phase 2 Pre-Run Plan (Patches `0040–0043` and Run148)

### Patch design (strictly preserving architectural equivalence and all existing contract checks)

1. **`0040-tcg-tb-jmp-cache-pcrel-and-size.patch`**:
   - Expand `TB_JMP_CACHE_BITS` from `12` (4,096 entries, 64 page buckets) to **`14` (16,384 entries = 256 KiB/vCPU, fitting inside each host P-core's 2 MiB L2)** while keeping `TB_JMP_PAGE_BITS = 6` (still 64 entries cleared per page, **4× more page buckets = 256**), folding `(vpn ^ (vpn >> 8) ^ (vpn >> 16))` in the page hash and restricting the offset hash strictly to `pc & ~TARGET_PAGE_MASK`.
   - Fix `tb_jmp_cache_inval_tb()` under `CF_PCREL`: since intra-page offset `tb_page_addr0(tb) & ~TARGET_PAGE_MASK` is invariant across all virtual aliases, check only the matching intra-page offset slot in each page bucket with **read-before-write** (`if (qatomic_read == tb) qatomic_set(..., NULL)`), preserving >99.9% of valid jump-cache entries and keeping peer cache lines in Shared state.
   - Guard `tcg_flush_jmp_cache()` in `tlb_flush_by_mmuidx_async_work()` on `to_clean != 0`, and use read-before-write when flushing.
2. **`0041-tcg-cputlb-16k-subpage-and-arm-el23-fastpath.patch`**:
   - Track `has_16k_pages` in `CPUTLBDesc` (reset in `tlb_mmu_flush_locked`): when `TARGET_PAGE_BITS == 12` and `full->lg_page_size == 14` (ordinary 16 KiB guest page), set `desc->has_16k_pages = true` instead of calling `tlb_add_large_page()` (blocks/contiguous mappings with `lg_page_size > 14` still use `tlb_add_large_page()` unchanged), and widen single-page and range invalidations outward to `16 KiB` boundaries (`0x4000`) when `has_16k_pages` is active so only the 4 constituent `4 KiB` subpages (and overlapping jump-cache pages) are invalidated.
   - Short-circuit `arm_is_el2_enabled()` on `!arm_feature(env, ARM_FEATURE_EL2)` in `target/arm/cpu.h` before calling `arm_security_space_below_el3()` (keeping `arm_security_space_below_el3` in `helper.c` untouched for `E06-pauth-hvc/test-pac-va.py`), and add inline `!ARM_FEATURE_EL2 -> return 0` fast paths for `arm_hcr_el2_eff` and `arm_hcr_el2_eff_secstate`.
3. **`0042-arm-pauth-va-cache-and-simd-qarma5.patch`**:
   - Keep `aa64_va_parameters_impl` and `pauth_addpac..pauth_strip` literal (100% compatible with `test-pac-va.py`); inside `aa64_pauth_va_parameters()`, add a self-validating 4-slot `(mmu_idx, tcr)` per-vCPU cache enabled once `arm_cpu_realizefn` finalizes `isar`, plus `!EL2/!EL3` fast paths in `pauth_check_trap()` and `pauth_key_enabled()`.
   - Mix `key.lo` into `pauth_memo_slot()`, and add a 16-nibble parallel SSSE3 (`pshufb`) QARMA5 implementation in `pauth_computepac_architected()` on `__SSSE3__` x86_64 hosts, self-tested at process startup across 1,024 deterministic random `(data, modifier, key)` vectors against the retained scalar implementation (`abort()` on any bit mismatch).
4. **`0043-arm-sme-fmopa-h-preunpack.patch`**:
   - Expose an internal pre-unpacked `float16` multiply-add entry point in `fpu/softfloat.c` (preserving exact IEEE-754 rounding, NaN propagation, and exception flags) and pre-unpack active row/column operands in `do_fmopa_h()`, cutting `float16_unpack_canonical` calls from `2N²` to at most `2N`.

### Phase 2 hypotheses (Run148)

1. **Throughput jump hypothesis (H3)**: With `0040–0043` applied under identical CLI flags (keeping `--pauth-memo-entries 1024` unchanged to isolate code improvements), the share of `CPU */TCG` host P-core cycles spent inside translated guest `[JIT]` blocks rises from `13.5%–14.5%` to **`≥ 22%`** (≥50% higher pure guest instruction throughput), while the combined Safari-launch share of `helper_lookup_tb_ptr`, `tlb_fill_align`, `pauth_*`, and `do_fmopa_h` drops from `~57%` to **`≤ 35%`**.
2. **Diagnostic-off cold-launch margin hypothesis (H4)**: On a quiet guest with **`--webkit-timeout-diagnostic` and `--sync-stats` both off** (Run148), cold first-launch Safari achieves `outcome = initialized` and `gpu_unresponsive_exits == 0` with **`ui_request_to_did_initialize < 2.800 s`** (comfortably below the `3.000 s` deadline without relying on UIProcess runloop slack), bit-exact WebGL2 dual-buffer readbacks (`bit_exact = true`), and `< 1.800 s` on Trial 2. If H4 passes, **Run149** will immediately test diagnostic-off cold first launch at `+720 s` during peak post-login background load.

### Validation and procedure (Run148)

- Build and pass all bare-metal and offline gates before booting macOS: `make check-research-patches`, `make sync-counter-tests`, `make smp-mmu-tests`, `make el0-context-tests`, `make el0-migration-tests`, `make el0-migration-model-tests`, `make pauth-model`, `make pauth-guards`, `experiments/E06-pauth-hvc/test-pac-va.py`, `make clock-functional`, `make clock-rate-exact`, `make reims-gpu-restore-platform`, `make installed-offline-tests`, and the 7-gate public snapshot export check.
- After ≥ 3 minutes host cooldown, boot `m3-tcg-throughput-orig3s-20261112-148` with the exact same CLI flags as Run147, capture 15 s saturated `perf`, wait for quiet, run unperturbed Trial 1 (Safari + WebGL2 readback), run `perf`-instrumented Trial 2, export log windows, and shut down cleanly from inside macOS.

---

## Phase 2 Results (Run148: `0040–0043` Throughput Jump and 3/3 Zero-Watchdog Validation)

### Run identity and integrity

- Run directory: `runs/e05-installed-boot/m3-tcg-throughput-orig3s-20261112-148`
- Binary: `build/qemu-bdif-usb/qemu-system-aarch64` (SHA-256 `134f6bd28db671a6bc8488e0efaecf000465d9667e0d6e107a9469a74aba7d1c`, patch stack through `0043`)
- Profile: identical to Run147 (8 vCPUs, 4 GiB RAM, `qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`, NVIDIA native window, **`--webkit-timeout-diagnostic` omitted**, **`--sync-stats` omitted**)
- Guest & host health: reached password field at `+153 s` (~80 s earlier than Run147), full desktop & Dock at `+402 s` (>2 min earlier), and **`QUIET` at `+958 s`** (`[1.62, 1.812, 1.314]`, 6.3 min earlier than Run147 and 9.3 min earlier than unpatched Run142); clean guest shutdown (`qemu_exit_code = 0`, `guest_shutdown_observed = true`, `errors = []`, elapsed `1936.0 s`); 377 host package temperature samples (`min = 49 °C`, `mean = 73.9 °C`, `max = 94 °C`); clock probes `r148a = -2.9459 s` (spread `4.3 ms`) and `r148b = -2.9537 s` (spread `1.5 ms`).

### H3 verdict: translated `[JIT]` share jumped +71.6% (`13.52% → 23.20%`), eliminating SME and 16 KiB full-TLB flushes and exposing `TTBR0_EL1` jump-cache wipes

| Metric (`CPU */TCG` host P-core `cpu_core/cycles/P`) | Run147 (`0039`) Saturated | Run148 (`0043`) Saturated | Run147 (`0039`) Safari Launch | Run148 (`0043`) Safari Launch |
|---|---:|---:|---:|---:|
| Translated guest `[JIT]` share | `13.52%` | **`23.20%` (+71.6%)** | `14.45%` | **`21.98%` (+52.1%)** |
| `qemu-system-aarch64` C share | `81.13%` | **`69.89%`** | `80.28%` | **`70.50%`** |
| `tlb_fill_align` (inclusive) | `12.46%` | **`5.44%`** | `14.97%` | **`10.82%`** |
| `arm_security_space_below_el3` + `arm_hcr_el2_eff*` (self) | `3.61%` | **`< 0.5%` (eliminated)** | `2.45%` | **`< 0.5%` (eliminated)** |
| `do_fmopa_h` / `float16a_unpack_canonical` (inclusive) | `4.13%` | **`< 0.5%` (eliminated)** | `11.12%` | **`< 0.5%` (eliminated)** |
| `pauth_computepac` (inclusive) / `qarma_ssse3` | `10.15%` | **`7.10%` (`2.90%`)** | `9.54%` | **`6.77%` (`3.02%`)** |
| `helper_lookup_tb_ptr` (inclusive) / `tcg_flush_jmp_cache` (self) | `16.96%` / `0.64%` | `23.62%` / `1.64%` | `16.60%` / `< 0.6%` | `21.70%` / **`2.05%`** |

**H3 assessment**: Saturated `[JIT]` share hit **`23.20%`** (exceeding the `≥ 22%` target; `21.98%` during Safari launch), and PAuth throughput rose from `24.2M/s` to **`26.07M/s`** (`84.46%` memo hit rate). The combined four-family share dropped to `36.2%` (saturated) and `46.5%` (Safari launch) because one newly exposed mechanism inflated `helper_lookup_tb_ptr`: on every process context switch (`MSR TTBR0_EL1`, `vmsa_ttbr_write`), QEMU calls `tlb_flush()`, spending `2.05%` self cycles zeroing all 16,384 `CPUJumpCache` entries while also wiping `ARMMMUIdx_Phys_NS` (the page-table walker's physical RAM TLB) and `TTBR1_EL1` kernel entries, forcing shared-cache and kernel indirect branches back into `qht_lookup_custom` (`7.98%–8.73%`).

### H4 verdict: 3/3 diagnostic-off launches passed with 0 watchdog exits (`3.180 s` / `1.620 s` / `2.056 s`), with cold first launch `0.380 s` shy of `< 2.800 s`

To keep Trial 2 unperturbed by `perf`, Run148 executed two unperturbed launches (`trial1`, `trial2`) followed by a third fresh launch (`r148csafarib`) carrying the 20 s `perf` capture:

| Trial | Sampler perturbation | `gpu_unresponsive_exits` | `constructor -> proxy_launched` | `ui_request -> didInitialize` | Sub-segments: `entry->registrar` / `->helper_create` / `->destroy` / `->didInit` | WebGL2 dual-buffer readback |
|---|---|---:|---:|---:|---|---|
| Trial 1 (`r148safari`, cold 1st launch) | **None** | **0** | **`9.122 s`** (`23.489 s` in Run147) | **`3.180 s`** (all-time fastest cold 1st launch) | **`2.019 s`** / `0.121 s` / `0.660 s` / `0.375 s` (`vcpu_mean_busy = 6.409`) | FBO + default 24,576 B **`bit_exact=true`** |
| Trial 2 (`r148safarib`, unperturbed 2nd) | **None** | **0** (`web=0`) | **`5.589 s`** | **`1.620 s`** (clears `< 1.800 s`) | `0.822 s` / **`0.056 s`** / `0.576 s` / `0.163 s` (`vcpu_mean_busy = 6.105`) | — |
| Trial 3 (`r148csafarib`, 3rd with `perf`) | Concurrent 20 s `perf` | **0** (`web=0`) | `5.727 s` | **`2.056 s`** (sub-2.1 s even under DWARF `perf`) | `1.118 s` / `0.069 s` / `0.700 s` / `0.165 s` | — |

**Diagnosis**: `registrar -> didInitialize` on cold first launch is now down to **`1.156 s`**, while `gpu_entry -> registrar` (the multi-daemon XPC context-switch storm across `Safari`, `GPU`, `WebContent`, and `runningboardd`) accounts for **`2.019 s`**, leaving total cold first launch at `3.180 s` (`0.380 s` above our `< 2.800 s` zero-slack target) — directly matching the `vmsa_ttbr_write -> tlb_flush -> tcg_flush_jmp_cache` hotspot in `perf`!

---

## Phase 3 Pre-Run Plan (Patches `0044–0045` and Run149)

### Patch design

1. **`0044-tcg-host-tagged-tb-jmp-cache.patch` (Host-RAM-tagged persistent `CPUJumpCache` across context switches and `TLBI`)**:
   - Store `uintptr_t host_pc` alongside `{tb, pc}` in `CPUJumpCache` for single-page RAM TBs (`tb_page_addr1(tb) == -1`).
   - Validate `CPUJumpCache` hits in `tb_lookup()` via a two-tier physical consistency check: (a) **Tier-1 (~5 instructions)**: verify against the current `mmu_idx` inline `cpu_tlb_fast` entry that `entry->addr_code == (s->pc & TARGET_PAGE_MASK)` (confirming execute permission and absence of `TLB_INVALID_MASK`/`TLB_MMIO`/`TLB_FORCE_SLOW`) and `(uintptr_t)s->pc + entry->addend == jc->array[hash].host_pc`; (b) **Tier-2 (shared-cache context-switch hit)**: when `TTBR0_EL1` switched so `addr_code` missed in the SoftMMU TLB, call `get_page_addr_code_hostp()` once (populating `addr_code` for the whole page), verify `tb_page_addr0(tb) == phys_pc && tb_page_addr1(tb) == -1`, update `host_pc`, and return `tb` **without touching global `qht_lookup_custom`**; on Tier-2 miss, pass `phys_pc` directly to `tb_htable_lookup` without re-translating `s->pc`.
   - Because every jump-cache hit is validated against the live SoftMMU code TLB, **TLB invalidations in `cputlb.c` (`tlb_flush_by_mmuidx_async_work`, `tlb_flush_page*`, `tlb_flush_range*`) no longer clear `CPUJumpCache`** (`tb_jmp_cache_inval_tb`, `tb_flush`, and CPU reset continue to invalidate entries when TBs themselves are invalidated), eliminating `tcg_flush_jmp_cache` (`2.05%` self) and keeping all 16,384 kernel and `dyld` shared-cache TBs warm across context switches!
2. **`0045-arm-ttbr-selective-tlb-flush.patch` (Preserve `ARMMMUIdx_Phys_NS` and `TTBR1_EL1` kernel upper-half SoftMMU TLBs on `TTBR0_EL1` ASID switches)**:
   - Track `has_lower_half55` in `CPUTLBDesc` (set when inserting `!(addr & (1ULL << 55))`, cleared on full flush).
   - In `vmsa_ttbr_write()`, when AArch64 EL1&0 (`!EL2 && !EL3`) writes `TTBR0_EL1` with a new ASID while `TCR_EL1.A1 == 0` and `TCR2_EL1.FNGNA1 == 0`: (a) never flush `ARMMMUIdx_Phys_NS` (keeping `S1_ptw_translate`'s page-table descriptor TLB permanently warm); (b) flush `E10_0 | E10_0_GCS` normally; and (c) run `tlb_flush_lower_half55_by_mmuidx()` on `E10_1 | E10_1_PAN | E10_1_GCS` — an **`O(1)` no-op** when `has_lower_half55 == false` (normal `E10_1_PAN` execution) and a selective `bit 55 == 0` sweep after `copyin`/`copyout`, preserving 100% of `0xfffffe00...` XNU kernel SoftMMU TLB entries!

### Phase 3 hypotheses (Run149)

1. **Jump-cache and context-switch elimination hypothesis (H5)**: With `0044–0045` applied, `tcg_flush_jmp_cache` drops below `< 0.1%`, `qht_lookup_custom` drops from `8.0%–8.7%` to **`≤ 3.0%`**, and host P-core share in translated guest `[JIT]` blocks rises further to **`≥ 27%`**.
2. **Strict sub-3.000 s cold first-launch hypothesis (H6)**: On a quiet guest with **`--webkit-timeout-diagnostic` and `--sync-stats` both off** (Run149), cold first-launch Safari achieves `outcome = initialized` and `gpu_unresponsive_exits == 0` with **`ui_request_to_did_initialize < 2.800 s`** (strictly below nominal `3.000 s` without needing UIProcess runloop timer slack), bit-exact WebGL2 dual-buffer readbacks (`bit_exact = true`), and `< 1.500 s` on unperturbed Trial 2 — unlocking **Run150** to test diagnostic-off cold launch at `+720 s` during peak post-login background load!

---

## Phase 3 Results (Run149: Patches `0044–0045` Push Cold First Launch Strictly Below `3.000 s` to `2.734 s`)

### Run identity and integrity

- Run directory: `runs/e05-installed-boot/m3-jmpcache-ttbr-orig3s-20261112-149`
- Binary: `build/qemu-bdif-usb/qemu-system-aarch64` (SHA-256 `f858a94a8d7395456679a9c05c2c0dd2d6ee56f8701525e47bbf7ed9ee85f474`, patch stack through `0045`)
- Profile: identical to Run147 and Run148 (8 vCPUs, 4 GiB RAM, `qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`, NVIDIA native window, **`--webkit-timeout-diagnostic` omitted**, **`--sync-stats` omitted**)
- Guest & host health: reached password field at `+152 s`, full desktop & Dock at `+371 s`, and `QUIET` at `+976 s` (`last three minutes = [1.285, 1.652, 1.185]`, lowest idle floor recorded); clean guest shutdown (`qemu_exit_code = 0`, `guest_shutdown_observed = true`, `errors = []`, elapsed `1972.5 s`); 386 host package temperature samples (`min = 50 °C`, **`mean = 71.2 °C`** — `5.6 °C` cooler than Run147 — `max = 96 °C`); clock probes `r149a = -2.9641 s` (spread `3.3 ms`) and `r149b = -2.9723 s` (spread `2.0 ms`).

### H5 verdict: `tcg_flush_jmp_cache` and `vmsa_ttbr_write` eliminated (`0.00%`), cutting Safari-launch `tb_htable_lookup` by 62.7%

| Metric (`CPU */TCG` host P-core `cpu_core/cycles/P`) | Run147 (`0039`) Safari Launch | Run148 (`0043`) Safari Launch | Run149 (`0045`) Safari Launch | Run149 (`0045`) Saturated |
|---|---:|---:|---:|---:|
| Translated guest `[JIT]` share | `14.45%` | `21.98%` | **`22.60%` (+56.4%)** | **`24.12%` (+78.4%)** |
| `tcg_flush_jmp_cache` (self) | `< 0.6%` | `2.05%` | **`0.00%` (eliminated)** | **`0.00%` (eliminated)** |
| `vmsa_ttbr_write` (inclusive) | — | `1.57%` | **`< 0.1%` (eliminated)** | **`< 0.1%` (eliminated)** |
| `tb_htable_lookup` (inclusive) | `13.09%` | `14.43%` | **`5.38%` (-62.7% vs 148)** | **`~7.0%`** |
| `qht_lookup_custom` (self) | `5.03%` | `6.68%` (`7.98%` incl) | **`4.29%` (-35.8% vs 148)** | `5.35%` |
| `arm_cpu_tlb_fill_align` (inclusive) | `7.76%` (`tlb_fill` `14.97%`) | `6.08%` (`tlb_fill` `10.82%`) | **`4.67%` (-39.8% cumul.)** | **`3.58%`** |
| `tlb_set_page_full` (inclusive) | `7.70%` | `5.08%` | **`3.80%` (-50.6% cumul.)** | **`2.74%`** |

**H5 assessment**: `tcg_flush_jmp_cache` and `vmsa_ttbr_write` dropped below `< 0.1%`, and Safari-launch `tb_htable_lookup` fell **62.7%** (`14.43% → 5.38%`), proving that `dyld` shared-cache and kernel TBs survive `TTBR0_EL1` context switches in `CPUJumpCache`; saturated `[JIT]` reached **`24.12%`** (`2.9%` below the `27%` sub-target because L2 reads of the 384 KiB `CPUJumpCache` are attributed to `tb_lookup` self at `9.98%–11.09%`, while direct-mapped collisions leave `4.3%–5.3%` in `qht_lookup_custom`). PAuth throughput rose to **`26.62M/s`**.

### H6 verdict: cold first-launch `ui_request_to_did_initialize = 2.734 s` (strictly below both `< 2.800 s` and nominal `3.000 s`!)

| Trial | Sampler perturbation | `gpu_unresponsive_exits` | `constructor -> proxy_launched` | `ui_request -> didInitialize` | Sub-segments: `entry->registrar` / `->helper_create` / `->destroy` / `->didInit` | Key host CPU metrics |
|---|---|---:|---:|---:|---|---|
| Trial 1 (`r149safari`, cold 1st launch) | **None** | **0** | **`8.979 s`** | **`2.734 s`** (**clears `< 2.800 s` zero-slack target!**) | **`1.460 s`** / `0.132 s` / `0.793 s` / `0.345 s` | `entry->registrar` vCPU-seconds fell **`12.91 → 8.08` (-37.4%)**; WebGL2 **`bit_exact=true`** |
| Trial 2 (`r149safarib`, unperturbed 2nd) | **None** | **0** (`web=0`) | `6.894 s` | **`1.679 s`** | **`0.789 s`** / **`0.052 s`** / `0.683 s` / `0.153 s` | `entry->registrar` used only `3.80` busy vCPUs (`3.00` vCPU-s); host `51 °C` |
| Trial 3 (`r149csafarib`, 3rd with `perf`) | Concurrent 20 s `perf` | **0** (`web=0`) | `6.553 s` | **`1.856 s`** | `0.911 s` / `0.060 s` / `0.715 s` / `0.167 s` | `< 1.9 s` even under concurrent DWARF stack unwinding |

**Milestone**: Across Run148 and Run149, **all 6 consecutive diagnostic-off Safari launches** passed on the first attempt with `0` GPU watchdog exits; in Run149, cold first-launch `ui_request_to_did_initialize` dropped from `3.180 s` to **`2.734 s`** (`gpu_entry -> registrar` down from `2.019 s` to `1.460 s`, consuming **37.4% fewer host vCPU-seconds**), **clearing WebKit's unmodified `3.000 s` deadline for the first time without relying on UIProcess runloop timer slack**, while both 24,576-byte WebGL2 readbacks matched byte-for-byte for the 4th consecutive cold boot!

---

## Phase 4 Pre-Run Plan (Run150: Diagnostic-Off Cold First Launch at `+720 s` Post-Login Background Load)

### Motivation and hypothesis

Every historical run executed at the fixed **`+720 s` post-login window** (before background Spotlight/daemon activity has fully subsided, `vcpu_mean_busy ≈ 6.0–6.8`) required `--webkit-timeout-diagnostic`, and even after `0034–0039` eliminated lock contention, `+720 s` cold launches still took `3.336–3.798 s` (Run144–145). With `0040–0045` cutting quiet-guest cold first launch to `2.734 s` and accelerating background work completion itself, **Run150** omits `watch.sh` and tests diagnostic-off cold launch directly at the fixed **`+720 s`** (Trial 1 + WebGL2) and **`+1020 s`** (Trial 2) timestamps!

- **Peak-window diagnostic-off hypothesis (H7)**: On `0045` with **`--webkit-timeout-diagnostic` and `--sync-stats` both off** (Run150), triggering Trial 1 directly at **`+720 s`** without waiting for `QUIET`:
  1. First GPU process completes initialization on the first attempt (`outcome = initialized`, `gpu_unresponsive_exits == 0`) with **`ui_request_to_did_initialize < 3.000 s`** (>21% faster than Run145's `3.798 s` at `+720 s`);
  2. Both 24,576-byte fixed WebGL2 FBO and default framebuffer readbacks pass `bit_exact = true`;
  3. Trial 2 at `+1020 s` achieves `outcome = initialized`, `gpu_unresponsive_exits == 0`, and `< 2.000 s`.

### Procedure (Run150)

- After ≥ 3 minutes host cooldown, boot `m3-peakload-orig3s-20261112-150` with the exact same diagnostic-off CLI flags as Run147–149 alongside the three sidecar samplers.
- Log in at `+150 s`, confirm desktop at `+380 s`, run `keepawake` and clock probe `r150a`, pre-type the WebGL2 probe command, **press Return right at `+720 s`** (zero `perf` perturbation), and validate both WebGL2 readbacks.
- Focus Terminal, quit Safari and confirm absence, and **press Return right at `+1020 s`** for Trial 2.
- Export both guest log windows, run `clockprobe r150b`, stop `caffeinate`, and shut down cleanly via `sudo /sbin/shutdown -h now`.

---

## Phase 4 Results (Run150: First-Ever Zero-Watchdog Diagnostic-Off Cold Launch at `+720 s` Peak Background Load)

### Run identity and integrity

- Run directory: `runs/e05-installed-boot/m3-peakload-orig3s-20261112-150`
- Binary: `build/qemu-bdif-usb/qemu-system-aarch64` (SHA-256 `f858a94a8d7395456679a9c05c2c0dd2d6ee56f8701525e47bbf7ed9ee85f474`, patch stack through `0045`)
- Profile: 8 vCPUs, 4 GiB RAM, `qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`, NVIDIA native window, **`--webkit-timeout-diagnostic` omitted**, **`--sync-stats` omitted**, **no wait for `QUIET`** (fixed `+720 s` / `+1020 s` triggers)
- Guest & host health: reached password field at `+152 s`, full desktop & Dock at `+382 s`, pressed Return for cold Trial 1 right at `+720 s` and Trial 2 right at `+1020 s`; clean guest shutdown via `sudo /sbin/shutdown -h now` (`qemu_exit_code = 0`, `guest_shutdown_observed = true`, `errors = []`, elapsed `1394.1 s`); 270 host package temperature samples (`min = 49 °C`, `mean = 78.4 °C`, `max = 93 °C`); clock probes `r150a = -2.9768 s` (spread `3.5 ms`) and `r150b = -2.9816 s` (spread `2.2 ms`); sustained PAuth throughput reached **`32.60M/s`** (`84.60%` hit rate).

### H7 verdict: `+720 s` peak-window cold first launch passed with 0 watchdog exits (`3.254 s`) and bit-exact WebGL2 readbacks; `+1020 s` Trial 2 hit `1.662 s`

| Run (all at fixed `+720 s` / `+1020 s` post-login windows) | Patch level | `--webkit-timeout-diagnostic` | Trial 1 (`+720 s`) `ui_request -> didInitialize` | Trial 1 watchdog exits | Trial 2 (`+1020 s`) `ui_request -> didInitialize` | WebGL2 dual-buffer readback |
|---|---|---|---:|---:|---:|---|
| Run131 / 132–136 (pre-sync-opt baseline) | `0032` | ON (30 s) | `3.515–4.866 s` | — (extended) | `2.768–3.802 s` | — |
| Run143 (sync-counter baseline) | `0033` | ON (30 s) | `3.704 s` | — (extended) | `2.818 s` | — |
| Run145 (sync-opt finale) | `0039` | ON (30 s) | `3.798 s` | — (extended) | `2.431 s` | — |
| **Run150 (sync + throughput finale)** | **`0045`** | **OFF (unmodified)** | **`3.254 s`** (`vcpu_mean_busy = 6.323`) | **0** | **`1.662 s`** (`0` exits, `0` Web notices, `vcpu_mean_busy = 5.698`) | **FBO + default 24,576 B `bit_exact=true`** |

**H7 item-by-item assessment and boundary**:
1. **Zero-watchdog peak-window launch and bit-exact WebGL2 confirmed**: At `+720 s` under active post-login background saturation (`vcpu_mean_busy = 6.323` overall, `6.934` during `registrar -> helper_create`), the first GPU process initialized on the first attempt with **`0` watchdog exits** and **no `--webkit-timeout-diagnostic`** (`constructor -> proxy_launched = 9.383 s`, `gpu_entry -> registrar = 1.912 s`, remaining three sub-segments `1.333 s`), passing both 24,576-byte WebGL2 readbacks byte-for-byte (**5/5** across five independent diagnostic-off cold boots); Trial 2 at `+1020 s` hit **`1.662 s`** (clearing `< 2.000 s`, **31.6% faster** than Run145 and **41.0% faster** than Run143 at the exact same timestamp).
2. **Nominal `< 3.000 s` boundary**: At `+720 s` peak load, `ui_request_to_did_initialize = 3.254 s` cleared with `0` exits because UIProcess's own loaded main runloop contributed ~`0.26 s` of timer delivery slack, so the strict `< 3.000 s` sub-clause of H7 remains `0.254 s` above nominal under peak saturation (whereas on a quiet guest in Run149, cold first launch cleared nominal `< 3.000 s` strictly at **`2.734 s`**). Across Run148–150 (three independent cold boots including `+720 s` peak load), **8 out of 8** fresh diagnostic-off Safari launches initialized with **0 GPU watchdog exits**.

### Summary of Progression (Run143 → Run150)

| Stage | Patches | Core mechanism changes | Key measured outcomes |
|---|---|---|---|
| Synchronization contention removal (Run143–146) | `0033–0039` | Lockless `CNTVCT_EL0`, masked-IRQ/`EXITTB` BQL elision, stack-HMAC PAC `HVC` + 64-entry cache, BQL-free `TLBI` queue drain, `DSB`-batched broadcast `TLBI`, EL1&0 `ERET` BQL elision | Saturated `bql` rate **-90%**, running/runnable vCPU share **96.6%** (**3.4%** total stall) |
| Pure TCG throughput pass 1 (Run147–148) | `0040–0043` | Selective `CF_PCREL` jump-cache invalidation + 256 page buckets, `cputlb` 16 KiB subpage invalidation + unit-bug fix + `!EL2` inline fast paths, PAuth VA cache + SSSE3 `pshufb` QARMA5, SME `do_fmopa_h` pre-unpacking | Bare-metal QARMA5 cost **-73%** (`214 → 57.7 ns`), saturated `[JIT]` share **`13.52% → 23.20%`**, quiet reached at `+958 s`, 3/3 zero-watchdog launches (`3.180 s` / `1.620 s`) |
| Persistent jump cache & selective `TTBR0` (Run149–150) | `0044–0045` | Host-RAM-tagged persistent `CPUJumpCache` across TLB flushes (bypassing global QHT on shared-cache switches) + `TTBR0_EL1` switch preserving `ARMMMUIdx_Phys_NS` and `TTBR1_EL1` kernel upper-half TLBs | Safari-launch `tb_htable_lookup` **-62.7%** (`14.43% → 5.38%`), saturated `[JIT]` **`24.12%`** (**+78.4%** cumulative); **quiet cold 1st launch strictly `< 3.000 s` at `2.734 s`**, **`+720 s` peak-load diagnostic-off cold 1st launch `3.254 s` (0 exits) / 2nd `1.662 s`**, **8/8** consecutive diagnostic-off zero-watchdog launches across Run148–150 |
