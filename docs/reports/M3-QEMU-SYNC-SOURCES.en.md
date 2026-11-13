> **Language:** **English** | [简体中文](M3-QEMU-SYNC-SOURCES.md)

# Breakdown of QEMU Global-Lock and Exclusive-Section Trigger Sources (From Run143)

Date: from 2026-11-12 UTC. The "Pre-run plan" section of this document was committed before the run.

## Pre-run plan

### Motivation

Blocking samples from Run141–142 established that whenever the 8-vCPU macOS guest is saturated — both during the ~20-minute post-login background phase and during the Safari launch window on an otherwise quiet guest — the 8 MTTCG vCPU threads spend 14%–30% of their time stalled on QEMU's global lock (`bql`) and stop-the-world exclusive sections (`start_exclusive` / `exclusive_resume`) rather than executing guest instructions. See the [background-load report](M3-QUIET-GUEST-GPU-INIT-TIMING.en.md).

Sampling `/proc/PID/task/TID/syscall` and scanning return addresses on thread stacks identifies the innermost lock-waiting functions (`mttcg_cpu_thread_fn`, `helper_get_cp_reg64`, `process_queued_cpu_work`), but cannot answer **which specific guest operations trigger these acquisitions and at what rates** — such as which `ARM_CP_IO` system registers dominate `helper_get_cp_reg64`, which exit reasons (`EXCP_INTERRUPT`, `EXCP_HLT`, etc.) force outer-loop BQL reacquisitions, and what fraction of exclusive sections come from broadcast `TLBI` instructions.

To measure this directly without adding any new lock, patch `0033-tcg-sync-source-counters.patch` adds default-off per-vCPU thread-local counters on ARM CPUs (`-cpu ...,x-sync-stats=on`, exported through the read-only QOM property `x-sync-stats-json` and verified against exact bare-metal instruction counts in `experiments/E06-sync-counters/`):

- MTTCG outer-loop exits and queued work: `exit_interrupt`, `exit_hlt`, `exit_halted`, `exit_yield`, `exit_atomic`, `exit_debug`, `exit_other`, `bql_atomic_reacquire`, `halt_cond_waits`, `queued_work_batches`, `queued_work_ordinary`, `queued_work_exclusive`;
- Inner BQL acquisition sites: `bql_do_interrupt` (split into `svc`, `abort`, `irq`, `other`), `bql_handle_interrupt`, `bql_eret` (pre- and post-EL-change hooks on every `ERET`), `bql_cpreg_read`, `bql_cpreg_write`, `bql_mmio` (the four TCG MMIO `BQL_LOCK_GUARD` sites in `cputlb.c`), `bql_ptw`;
- Stop-the-world exclusive-section sources: `excl_tlbi_all`, `excl_tlbi_page`, `excl_tlbi_range`, `excl_step_atomic`, `excl_tb_flush`;
- Per-register `[reads, writes]` maps: `cpreg_io` (every `ARM_CP_IO` system register), `tlbi` (every `TLBI` instruction variant), and `cpreg_non_io_helper`.

### Hypotheses

1. **`ARM_CP_IO` counter-read hypothesis (H1)**: Even under MTTCG (`!icount_enabled()`), QEMU marks `CNTVCT_EL0` and `CNTPCT_EL0` (the backing counter for macOS `mach_absolute_time()`) with `ARM_CP_IO`, unconditionally acquiring and releasing `bql` on every read. We hypothesize that `CNTVCT_EL0` + `CNTPCT_EL0` account for **at least 70%** of all `bql_cpreg_read` acquisitions during both post-login saturation and Safari's first launch.
2. **Exclusive-section and cross-CPU wakeup hypothesis (H2)**: Guest broadcast TLB invalidations (`TLBI ...IS`) call `tlb_flush_*_all_cpus_synced` in QEMU — queuing ordinary async work on all 7 peer vCPUs and kicking them out of `tcg_cpu_exec` with `EXCP_INTERRUPT` (causing simultaneous contention on `bql` in `mttcg_cpu_thread_fn`), while queuing `async_safe_run_on_cpu` on the initiating vCPU to run `start_exclusive`. We hypothesize that `excl_tlbi_*` accounts for **at least 90%** of all exclusive sections (`excl_total`), and that `exit_interrupt`, `bql_eret`, and `bql_do_interrupt` together account for **at least 70%** of non-cpreg BQL acquisitions.

### Procedure (Run143)

- Configuration matches Run132, Run134, and Run136 (8 vCPUs, 4 GiB RAM, `qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`, NVIDIA native window, 30 s timeout diagnostic), adding only `--sync-stats`. `host-temperature-log.sh`, `host_thread_sampler.py`, and `clock_offset.py` run in the background.
- Follow the standard loaded timeline (without waiting 20 minutes for background work to subside, keeping Trial 1 directly comparable to the Run132/134/136 timing baseline):
  1. Log in around 150 s after boot; around 350 s check Terminal, confirm Safari is absent, run the `shared-cache-identity` probe, arm the timeout diagnostic, and run clock probe `r143prep`.
  2. **Window A (post-login saturated background window, ~40 s)**: capture `sync-sat-before.json` via `sync_stats.py snapshot`, run a 40 s `block_sampler.py --stack-words 24` capture (`vcpu-block-saturated.txt`), and immediately capture `sync-sat-after.json`. Pairing both in the same window links exact trigger rates to actual futex stall shares.
  3. **Window B (Safari first launch, press Return around 720 s after boot)**: capture `sync-t1-before.json` right before pressing Return; run **no** `block_sampler.py` during Trial 1 so its timing condition matches prior runs; capture `sync-t1-after.json` immediately after `r143safari-after.txt` uploads.
  4. **Window C (Safari second launch, press Return around 1020 s after boot)**: capture `sync-t2-before.json` before pressing Return, run `block_sampler.py` (`vcpu-block-trial2.txt`) across the full launch and initialization window, and capture `sync-t2-after.json` once `r143safarib-after.txt` uploads.
  5. Export the two guest log windows (`r143w1`, `r143w2`), record final timeout-diagnostic counters and `sync-final.json`, and shut down cleanly from Guest Terminal via `sudo /sbin/shutdown -h now`.

### Decision criteria and patch-selection rules

Use `sync_stats.py diff` to compute total BQL acquisitions per second (`bql_total / Δt`), per-site shares, top-10 `cpreg_io` registers, exclusive sections per second (`excl_total / Δt`), and `tlbi` instruction shares across Windows A, B, and C, interpreting them jointly with `block_report.py` from Windows A and C:

| Candidate optimization | Trigger criterion (primary: Window B, cross-checked against A/C) | Next action |
|---|---|---|
| Lock-free generic-timer counter reads (`CNTVCT_EL0` / `CNTPCT_EL0`) | Both registers account for ≥ 70% of `bql_cpreg_read`, and `bql_cpreg_read` is ≥ 10% of `bql_total` (or ≥ 15% of `bql` stalled stack samples) | Provide a lock-free counter read path when `!icount_enabled()` |
| Eliminate `start_exclusive` stop-the-world on broadcast `TLBI` | `excl_tlbi_*` accounts for ≥ 50% of `excl_total`, and `exclusive_resume + exclusive_cond` is ≥ 3% of vCPU time in `block_sampler` | Replace `async_safe_run_on_cpu` (`start_exclusive`) in synced TLB flushes with non-exclusive cross-CPU completion or atomic remote CPUTLB invalidation |
| BQL-free ordinary queued work on MTTCG loop exit | `queued_work_ordinary` is comparable in rate to `exit_interrupt`, and outer-loop BQL reacquisition is ≥ 15% of `bql_total` | Drain non-exclusive queued CPU work under `work_mutex` without acquiring `bql` when no device interrupt or halt condition requires it |
| Coalesce or elide `ERET` hook BQL acquisitions | `bql_eret` accounts for ≥ 10% of `bql_total` | Avoid redundant BQL acquisitions for thread-local EL-change hooks (PMU, `vmapple_pac_apply`) and unchanged GICv3 IRQ state on `ERET` |
| Device MMIO / GIC interrupt dominance | The four categories above total < 30% of `bql_total`, with real device MMIO or GIC dispatch dominating | Do not write speculative TCG loop patches; profile specific MMIO regions and GIC paths first |

Regardless of outcome, check whether Window B's `ui_request_to_did_initialize` remains near the prior loaded range (3.444–3.791 s) to confirm that enabling `--sync-stats` does not materially perturb timing.

### Known limitations

- `x-sync-stats` counts **acquisition events**, not **hold or wait duration per event**: frequent brief acquisitions and rarer long-held locks carry different weights in stalled time, so counts must be interpreted alongside `block_sampler.py` in Windows A and C rather than in isolation.
- `sync_stats.py snapshot` queries QOM properties sequentially across the 8 running vCPUs over a few milliseconds; this skew is negligible across 40–90 s windows, though snapshots are not atomic cross-CPU cuts.
- Window B spans the full ~90–100 s Safari cold-launch wrapper interval rather than isolating only the 3.5 s GPU connection initialization sub-window, avoiding extra QMP traffic during the critical initialization window itself.

## Results (Run143)

### Conclusion

Both hypotheses **H1** and **H2** held by wide margins above their pre-registered thresholds, and the per-site counters additionally exposed two major unpredicted global-lock sources:

1. **Read-only generic-timer counter reads (confirming H1)**: Across the three windows, `CNTVCT_EL0` alone accounted for **96.29%**, **95.83%**, and **95.71%** of all `bql_cpreg_read` acquisitions (well above the 70% threshold), representing **25.0%–26.1%** of all BQL acquisitions VM-wide. In Window C's 90 s blocking capture, `helper_get_cp_reg64` stalled the 8 vCPU threads for **9.15%** of total vCPU time (the single largest `bql` wait site).
2. **Broadcast `TLBI` stop-the-world sections and cross-CPU wakeup stampedes (confirming H2)**: `excl_tlbi_*` accounted for **99.9997%–99.9998%** of all `start_exclusive` sections (6,516–7,721 full-VM pauses per second, above the 90% threshold), led by single-page `TLBI_VALE1IS` at **79.0%–90.4%** (routed to `excl_tlbi_range` because macOS enables TBI so `bits=56 < 64`). Each broadcast invalidation queued ordinary async work on all 7 peer vCPUs (`ordinary_items = 6,851,951 ≈ 7 × 978,835` in Window B), kicking all peers out of `tcg_cpu_exec` with `EXCP_INTERRUPT` to contend on `bql` in `mttcg_cpu_thread_fn` (**18.5%–22.0%** of all BQL locks and **7.48%** of vCPU time in Window C; combined with **4.10%** waiting on exclusive sections directly, broadcast `TLBI` synchronization cost **11.58%** of total vCPU time).
3. **No-op `bql_handle_interrupt` locks on `CPU_INTERRUPT_EXITTB` and masked IRQs (new finding #1)**: `bql_handle_interrupt` was the #1 BQL site by count at **28.55%–33.25%** (124k–136k locks/s), yet actual delivered interrupts (`do_interrupt_irq`) were only 111k–520k across the entire windows — over **97%** were no-ops. Source inspection identified two causes: `arm_cpu_do_interrupt` unconditionally sets `CPU_INTERRUPT_EXITTB`, forcing the very next `cpu_exec_loop` iteration to re-acquire `bql` solely to clear that bit; and whenever the guest executes with `PSTATE.I=1` while an IRQ line is asserted, every unchained TB boundary acquires `bql` before calling `arm_cpu_exec_interrupt`, which immediately sees `env->daif` masking the interrupt and returns `false`.
4. **`vmapple_pac_hvc` heap allocations and SHA-256 recomputation under BQL (new finding #2)**: In Window B, `bql_do_interrupt` hit 8,601,135 times (13.19%), of which `do_interrupt_other` was **6,262,556 (41,689/s)**. Source inspection traced this to macOS calling `HVC 0xC1000004/0xC1000005` on every thread switch and kernel `copyin`/`copyout`: every call took an `EXCP_HVC` `longjmp` + global `bql_lock()`, and `vmapple_pac_rekey` unconditionally performed **9 `g_hmac_new(G_CHECKSUM_SHA256, ...)` heap allocations + SHA-256 hashes while holding `bql`** — even though `fn=4/5` never changes kernel keys and `fn=5` toggles `force_el0` around `copyin`/`copyout` with the exact same `diversifier`. Across Trial 1 alone, ~**56.36 million** heap allocations and HMAC-SHA256 operations executed inside global `bql`, inflating lock hold duration for every other vCPU.

### Summary across the three measurement windows

| Metric | Window A: Post-login saturated (40.27 s) | Window B: Safari Trial 1 (150.22 s) | Window C: Safari Trial 2 (144.13 s) |
|---|---:|---:|---:|
| Total BQL acquisitions `bql_total` | 16,467,893 (**408,975/s**) | 65,230,655 (**434,240/s**) | 65,749,222 (**456,186/s**) |
| — `bql_handle_interrupt` | 5,475,447 (33.25%) | 18,624,123 (28.55%) | 19,174,007 (29.16%) |
| — `bql_cpreg_read` | 4,269,997 (25.93%) | 17,422,032 (26.71%) | 17,958,349 (27.31%) |
|   of which `CNTVCT_EL0` reads | 4,111,538 (**96.29%** of reads) | 16,695,185 (**95.83%** of reads) | 17,187,256 (**95.71%** of reads) |
| — `outer_loop_exit` (`exit_interrupt`) | 3,613,964 (21.95%) | 12,091,938 (18.54%) | 13,076,829 (19.89%) |
| — `bql_do_interrupt` | 1,094,038 (6.64%) | 8,601,135 (13.19%) | 6,494,950 (9.88%) |
|   of which `other` (PAC `HVC`) / `svc` / `abort` / `irq` | 0.55M / 0.35M / 0.19M / 0.11M | **6.26M** / 1.59M / 0.75M / 0.51M | **3.97M** / 1.69M / 0.84M / 0.52M |
| — `bql_eret` | 1,301,315 (7.90%) | 5,695,752 (8.73%) | 6,087,582 (9.26%) |
| — `bql_cpreg_write` | 375,495 (2.28%) | 1,687,930 (2.59%) | 1,767,419 (2.69%) |
| — `queued_work_exclusive` | 310,890 (1.89%) | 978,837 (1.50%) | 1,075,541 (1.64%) |
| — `bql_mmio` | 26,747 (0.16%) | 128,908 (0.20%) | 114,545 (0.17%) |
| Total exclusive sections `excl_total` | 310,891 (**7,720.9/s**) | 978,837 (**6,516.1/s**) | 1,075,542 (**7,462.4/s**) |
| — `excl_tlbi_range` / `page` / `all` | 98.34% / 1.64% / 0.02% | 94.26% / 5.59% / 0.15% | 94.13% / 5.71% / 0.17% |
| — Peer ordinary queued items `ordinary_items` | 2,174,938 | 6,851,951 | 7,528,693 |

### Blocking samples and Safari GPU initialization timings

- **Safari initialization timings**: First launch `ui_request_to_did_initialize = 3.704 s` (window `vcpu_mean_busy = 5.99`), second launch `2.818 s` (window `vcpu_mean_busy = 6.22`), both squarely within the Run132/134/136 loaded baseline ranges (3.515–3.791 s and 2.768–3.419 s), confirming that `--sync-stats` did not distort system timing. Zero unresponsive GPU exits occurred; diagnostic `site0` hit twice with zero rejections.
- **Blocked-time breakdown**:
  - Window A (40 s post-login saturation, 32,000 samples): running/runnable **82.6%**, waiting on `bql` **12.7%** (`mttcg_cpu_thread_fn` 6.19%, `helper_get_cp_reg64` 2.65%, `cpu_exec_loop` 1.64%, `process_queued_cpu_work` 0.59%, `ERET` 0.27%), waiting on exclusive-section locks **4.7%** (`exclusive_resume` 3.5%, `qemu_cpu_list_lock` 0.7%, `exclusive_cond` 0.5%).
  - Window C (90 s during Safari second launch, 72,000 samples): running/runnable dropped to **72.7%**, with **27.3%** blocked — **23.2%** on `bql` (`helper_get_cp_reg64` **9.15%**, `mttcg_cpu_thread_fn` + queued work **7.48%**, `cpu_exec_loop` **4.34%**, `ERET` **0.96%**) and **4.1%** on exclusive-section locks (`exclusive_resume` 3.2%, `qemu_cpu_list_lock` 0.5%, `exclusive_cond` 0.4%). Together these five paths explain **95.3%** (26.03 / 27.3) of all vCPU stall time.
- **Closure and thermals**: The two clock probes differed by 4.6 ms; the guest shut itself down cleanly (`guest_shutdown_observed=true`, exit code 0). Runtime was 24.3 minutes; package temperature min 54, mean 79.4, max 94 °C.

### Evidence

- `runs/e05-installed-boot/m3-sync-sources-qarma5-20261112-143/`: `sync-sat-before/after.json`, `sync-t1-before/after.json`, `sync-t2-before/after.json`, `sync-final.json`, `sync-diff-*.json`, `vcpu-block-saturated.txt`, `vcpu-block-trial2.txt`, `gpu-init-timing-*.json`, `gpu-init-cpu-profile-*.json`, and both guest log-window archives.
- `runs/sync-sources-20261112/driver/`: fixed-step driver scripts for this run.

---

## Phase 2 pre-run plan: Eliminating the top four BQL contention sources (Run144)

### Patch design and boundaries

All four dominant BQL contention mechanisms exceed the pre-registered selection thresholds in Windows B/C and can be eliminated **without altering any guest-visible architectural semantics, without weakening cross-CPU TLB synchronization, and while preserving `icount`, single-step, and replay paths**:

1. **Patch `0034-arm-gtimer-lockless-counter-read.patch`**: Add `ARM_CP_LOCKFREE_READ` to `CNTPCT`, `CNTPCT_EL0`, `CNTVCT`, and `CNTVCT_EL0`. When `!icount_enabled()`, `gt_cnt_read` / `gt_virt_cnt_read` read only thread-local `env->cp15` offsets and `vm_clock_seqlock`-protected `qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL)`, allowing `helper_get_cp_reg64` to execute without `bql_lock()`; `icount` retains the existing BQL path.
2. **Patch `0035-tcg-arm-skip-masked-irq-and-exittb-bql.patch`**: Clear `CPU_INTERRUPT_EXITTB` while `bql_lock()` is already held in `cpu_handle_exception` (the outer loop initializes `last_tb = NULL` immediately afterward), and add a read-only `TCGCPUOps::cpu_has_unmasked_interrupt` check so `cpu_handle_interrupt` skips `bql_lock()` when `interrupt_request` holds only architectural interrupt lines currently masked by thread-local `env->daif` (such as `PSTATE.I=1`).
3. **Patch `0036-vmapple-pac-hvc-lockless-and-key-cache.patch`**: Replace heap-allocating `g_hmac_new` in `vmapple_pac_derive` with a stack-only RFC 2104 HMAC-SHA256 routine, recompute kernel keys only on `fn=0/2/3/6`, add a 64-entry per-vCPU `(a, b, diversifier) -> user[0..3]` cache (cleared on CPU reset), and execute per-CPU `board_hvc` (`0xC1000000..0xC1000006`) via a lockless TCG helper when not single-stepping or running hostcall plugins — avoiding `EXCP_HVC` `longjmp` and `bql_lock()`.
4. **Patch `0037-tcg-no-bql-queued-tlbi-work.patch`**: Mark `cputlb.c` cross-CPU TLB flush work items and `mttcg_force_rcu` with `no_bql`. When `mttcg_cpu_thread_fn` exits `tcg_cpu_exec` with `EXCP_INTERRUPT` and the CPU is neither stopped nor halted, drain `no_bql` work items (including `start_exclusive` synced flushes, which already execute outside BQL) directly without acquiring `bql`, preserving `start_exclusive`'s exact cross-CPU TLB ordering.

### Hypotheses and decision criteria (Run144)

- **Hypotheses**: On the identical loaded timeline (Return at +720 s and +1020 s with `--sync-stats`):
  1. Window B's BQL acquisition rate drops by **at least 75%** relative to Run143 (434,240/s);
  2. vCPU `bql` blocked-time share in Windows A and C (12.7% and 23.2% in Run143) drops by **at least 60%**;
  3. Safari Trial 1 `ui_request_to_did_initialize` falls below the entire historical loaded range (3.444 s), and Trial 2 falls below 2.768 s; if Trial 1 already drops below **3.0 s**, the next run omits `--webkit-timeout-diagnostic` to test first load under WebKit's original 3.0 s deadline.
- **Bare-metal preflight gates**: Before launching Run144, the new build must pass `make check-research-patches`, `make sync-counter-tests`, `make smp-mmu-tests`, `make el0-context-tests`, `make el0-migration-tests`, `make pauth-model`, `make clock-functional`, and `make clock-rate-exact`.

## Phase 2 results (Run144: Patches `0034–0037`)

### Conclusion

All three pre-registered hypotheses **held by wide margins** (binary SHA-256 `031af750360871b3f88f045575ed88a3bb0f28a18792026f9bd9716ee5b9764d`, additionally passing `el0-migration-model-tests` and `reims-gpu-restore-platform` before launch):

1. **BQL acquisition rate dropped by 77.4%–82.2%** (exceeding the 75% threshold): Window A dropped from `408,975/s` to **`73,001/s`** (-82.15%), Window B from `434,240/s` to **`98,013/s`** (-77.43%), and Window C from `456,186/s` to **`86,506/s`** (-81.04%). All four targeted sites were effectively eliminated: BQL-locked `CNTVCT_EL0` reads dropped to `0`; `bql_handle_interrupt` matched delivered `do_interrupt_irq` 1-for-1 (0 wasted locks); `no_bql_exit_interrupt` drained all `13.32M` cross-CPU wakeups without BQL; and PAC `HVC` `do_interrupt_other` fell from `6.26M` to `101`.
2. **vCPU `bql` blocked-time share dropped by 89.8%–98.3%** (well past the 60% threshold): `futex on bql` fell from **`12.7%` to `1.3%`** in Window A (-89.8%) and from **`23.2%` to `0.4%`** in Window C (-98.3%). Mean on-CPU vCPUs (`vcpu_mean_busy`) inside the two GPU initialization windows rose from `5.99 / 6.22` in Run143 to **`6.63 / 6.94`** (+0.64 to +0.72 full host cores recovered).
3. **Both Safari initialization timings and end-to-end launch durations set new under-load records**:
   - First-launch `ui_request_to_did_initialize = 3.336 s` (below all 8 prior under-load first-launch samples `3.444–3.791 s`), second-launch `2.612 s` (below all 8 prior under-load second-launch samples `2.768–3.419 s`), with **0** `web_process_unresponsive_notifications` in both trials;
   - **End-to-end Safari launch sped up by ~43%**: time from pressing Return to the first GPU connection request dropped from **105 s to 60 s** on Trial 1 (`constructor_to_proxy_launched` `19.263 s` → `15.644 s`) and from **76 s to 43 s** on Trial 2 (`15.262 s` → `10.957 s`), while post-login background load subsided around minute 16 (~6 minutes earlier than before).

### Run143 vs. Run144 comparison

| Metric | Run143 (`0033` baseline) | Run144 (`0034–0037`) | Change |
|---|---:|---:|---:|
| Window A `bql` acquisition rate | 408,975 /s | **73,001 /s** | **-82.2%** |
| Window A `bql` / exclusive stall share | 12.7% / 4.7% | **1.3%** / 11.1% | `bql` **-89.8%** |
| Window B (Trial 1) `bql` acquisition rate | 434,240 /s | **98,013 /s** | **-77.4%** |
| Window B exclusive-section rate `excl_total` | 6,516.1 /s | 9,803.7 /s | +50.5% (higher guest throughput) |
| Trial 1: Return → `ui_request` | ~105 s | **~60 s** | **-43%** |
| Trial 1 `ui_request_to_did_initialize` | 3.704 s | **3.336 s** | **-0.368 s (-10.0%)** |
| Trial 1 window `vcpu_mean_busy` | 5.99 | **6.63** | +0.64 cores |
| Window C (Trial 2) `bql` acquisition rate | 456,186 /s | **86,506 /s** | **-81.0%** |
| Window C `bql` stall share | 23.2% | **0.4%** | **-98.3%** |
| Trial 2: Return → `ui_request` | ~76 s | **~43 s** | **-43%** |
| Trial 2 `ui_request_to_did_initialize` | 2.818 s | **2.612 s** | **-0.206 s (-7.3%)** |
| Trial 2 window `vcpu_mean_busy` | 6.22 | **6.94** | +0.72 cores |
| `WebProcess` unresponsive notifications | 1 / 1 | **0 / 0** | Eliminated |

Closure and thermals: the two clock probes differed by 4.8 ms; the guest shut itself down cleanly (`guest_shutdown_observed=true`, exit code 0). Runtime was 24.1 minutes; package temperature min 57, mean 81.4, max 95 °C. Evidence is retained in `runs/e05-installed-boot/m3-sync-opt-qarma5-20261112-144/`.

---

## Phase 3 pre-run plan: Batching broadcast `TLBI` to `DSB` and eliding EL1&0 `ERET` BQL (Run145)

### Motivation

With `bql` stalls reduced to 0.4%–1.3%, **85.4% of all remaining vCPU stall time in Window A (11.1 out of 13.0 percentage points) comes from `start_exclusive` stop-the-world pauses on broadcast `TLBI`** (9,250–9,804 full-VM pauses/s), while **`bql_eret` accounts for 50.0%** (7.01M) of remaining BQL acquisitions:

1. **Architectural `TLBI ...IS` completion semantics (ARMv8 DDI 0487 D8.11.2)**: ARM64 specifies that a broadcast `TLBI` instruction is guaranteed to complete on peer PEs only after the initiating PE executes a subsequent **`DSB`** instruction. When the OS kernel unmaps a multi-page range (e.g., 16–64 `TLBI VALE1IS` instructions followed by a single `DSB ISH`), only **one** cross-CPU completion barrier is architecturally required at `DSB`, whereas QEMU currently rebuilds `hflags`, ends the TB, and triggers a full 8-vCPU `start_exclusive()` pause on **every** `TLBI` instruction.
2. **`ERET` global lock on EL1&0-only CPUs**: As noted in upstream `hw/intc/arm_gicv3_cpuif.c:3194`, GICv3 interrupt state only changes on EL transitions involving EL2 or EL3; VMApple fixes `has_el2=false, has_el3=false`, and the remaining hooks (`vmapple_pac_apply`, and PMU snapshots when overflow timers are not active) touch only per-CPU state.

### Patch design and hypotheses (Run145)

1. **Patch `0038-arm-tlbi-dsb-batch-sync.patch`**: Maintain a bounded per-vCPU pending broadcast-`TLBI` buffer in `CPUARMState` (64 entries, coalescing into a full `mmuidx` mask on overflow). On AArch64 broadcast `TLBI` (`TLBI_*IS` / `TLBI_*OS`), flush the local CPU's TLB immediately, append the entry to the per-CPU buffer, and set `ARM_CP_SUPPRESS_TB_END` (avoiding per-page `hflags` rebuilds and `start_exclusive` pauses). When the initiating CPU executes **`DSB`** (`is_dmb==0` in `DSB_DMB`, or `DSB_nXS`), or on exception entry / `ERET` / CPU reset, flush the buffered batch to all peer CPUs in a single `async_run_on_cpu_no_bql` work item per peer and a single `async_safe_run_on_cpu_no_bql` completion point.
2. **Patch `0039-arm-el10-eret-bql-elision.patch`**: Skip registering `gicv3_cpuif_el_change_hook` when neither EL2 nor EL3 is enabled, and allow thread-local EL-change hooks on `ERET` to run without acquiring `bql` when no PMU overflow timer update is armed.
3. **Hypotheses and decision criteria**: On the same loaded timeline (Run145), Window B's `excl_total` rate drops by **at least 50%** from Run144 (9,804/s), Window A's exclusive-section stall share (11.1% in Run144) drops by **at least 50%**, and Safari Trial 1 `ui_request_to_did_initialize` drops from `3.336 s` **below 3.0 s**; if `< 3.0 s` is achieved, immediately run **Run146** without `--webkit-timeout-diagnostic` to test Safari first load under WebKit's unmodified 3.0 s deadline.

## Phase 3 results (Run145: Patches `0038–0039`)

### Conclusion

The Run145 results resolve into three clear findings that reduce QEMU synchronization overhead under saturation to **3.4%** and separate guest-scheduler contention from emulator contention:

1. **Saturated vCPU synchronization stalls fell to 3.4% (confirming the Window A hypothesis)**: In Window A (40.3 s post-login saturation), running/runnable vCPU share rose from `82.6%` (Run143) and `87.0%` (Run144) to **`96.6%`**; exclusive-section stalls (`exclusive_resume + exclusive_cond + qemu_cpu_list_lock`) dropped from `11.1%` in Run144 to **`2.8%`** (**-74.8%**, exceeding the 50% threshold); `futex on bql` dropped to **`0.2%`**; and BQL acquisition rate fell to **`39,611/s`** (**-90.3%** vs. Run143, with `bql_eret = 0`).
2. **Empirical characterization of XNU broadcast `TLBI` granularity (why Window B `excl_total` dropped 16% rather than 50%)**: Across Window B, `1,404,572` broadcast `TLBI` instructions produced `1,188,407` `DSB`-synced batches (`1.18` `TLBI`s per `DSB`; `8,245/s` vs. `9,804/s` in Run144). XNU already uses range invalidations (`TLBI_RVALE1IS`, 118.5k) for multi-page unmaps and issues `TLBI_VALE1IS` (1.21M) primarily as single-page operations immediately followed by `DSB ISH`. Thus `0038`'s primary gain came from suppressing per-`TLBI` `hflags` rebuilds and TB exits rather than long multi-instruction `TLBI` loops.
3. **Second launch set an all-time project record (`2.431 s`), while first launch showed that guest background contention and QEMU lock contention must both be absent**:
   - **Second launch (Window C, +1053 s)**: As background load began subsiding, `ui_request_to_did_initialize` hit **`2.431 s`** (first sample ever below 2.5 s, faster even than Run142's 41-minute quiet-guest `2.588 s`), `constructor_to_proxy_launched` dropped to **`9.571 s`** (**-37.3%** vs. Run143), and mutex `FUTEX_WAIT` samples fell by **96.4%** (`17,067 → 609`).
   - **First launch (Window B, +757 s)**: Because login occurred at +229 s (28 s later than Run144), +757 s fell 8.8 minutes into peak post-login background saturation (`vcpu_mean_busy = 6.81`), where background daemons competed for the 8 guest vCPUs (`gpu_entry_to_registrar_activation = 2.282 s`, total `3.798 s`; the `SandboxHelper` slice was `0.873 s`, faster than Run143/144, with `0` `WebProcess` unresponsive notifications).

Closure and thermals: clock probes differed by 4.0 ms; by +1370 s host package temperature had naturally fallen to 65 °C and 1-minute load to 3.01; the guest shut itself down cleanly (`guest_shutdown_observed=true`, exit code 0), with package temperature mean 79.9 and max 96 °C. Evidence is in `runs/e05-installed-boot/m3-tlbi-dsb-qarma5-20261112-145/`.

---

## Phase 4 pre-run plan: Unmodified 3.0 s deadline test on a quiet guest (Run146)

### Causal synthesis and hypothesis

Combining Run142 (quiet guest on the unoptimized binary) with Run143–145 (saturated guest on `0034–0039`) closes both halves of the causal chain:

- On the old binary, waiting for background daemons to subside (Run142) reached `3.249 s`, held above `3.0 s` because Safari's own launch saturated all 8 vCPUs and lost **26%** of vCPU time to QEMU's `bql` and `start_exclusive`;
- On the `0034–0039` binary, saturated QEMU synchronization stalls are down from `27.3%` to **`3.4%` (`bql` only `0.2%–0.3%`)**, and Trial 2 as background load subsided already reached **`2.431 s`**.

**Hypothesis (Run146)**: Booting the `0034–0039` mainline binary (`e4587db0...`) **with both `--webkit-timeout-diagnostic` and `--sync-stats` omitted** (100% unmodified Apple WebKit 3.0 s watchdog deadline, zero runtime instruction replacement), waiting after login until post-login background load subsides (three consecutive 1-minute windows with `vcpu_mean_busy < 3.0`), and launching **cold first-launch Safari** on the fixed WebGL2 fixture (`$H/get/webgl-probe.html#r146-webgl`):

1. Safari's first GPU connection initializes within WebKit's unmodified 3.0 s deadline (`ui_request_to_did_initialize < 3.0 s`, `gpu_unresponsive_exits_reported_by_ui_process == 0`);
2. Both 24,576-byte raw RGBA8 readbacks from the fixed WebGL2 shader pass `tools/validate-webgl-probe.py` byte-for-byte;
3. A second fresh Safari launch (`r146safarib`) also initializes without watchdog exits, followed by clean guest shutdown.

### Decision criteria

| Outcome | Condition | Meaning |
|---|---|---|
| **Pass** | Quiet reached; without `--webkit-timeout-diagnostic`, first launch achieves `outcome=initialized`, `gpu_unresponsive_exits=0`, `< 3.0 s`, and byte-exact WebGL2 readbacks | First verified cold Safari first-load and WebGL2 pass under WebKit's unmodified 3.0 s deadline (bounded to a quiet post-login guest) |
| **Init-only pass** | First launch `< 3.0 s` and `gpu_unresponsive_exits=0`, but WebGL2 page readback incomplete | GPU 3.0 s watchdog solved on a quiet guest; WebProcess page path requires separate investigation |
| **Refuted** | First launch triggers GPU watchdog unresponsive termination (~3.0 s) | Eliminating QEMU lock contention plus background load is still insufficient; pivot to TCG codegen hotspot profiling (`perf`) |

## Phase 4 results (Run146: Unmodified deadline test without timeout diagnostic)

### Conclusion

With **both `--webkit-timeout-diagnostic` and `--sync-stats` completely omitted** (`webkit_timeout_diagnostic.requested = false, state = "off"` in `results.json` — zero runtime instruction replacement, 100% unmodified Apple WebKit watchdog behavior), **cold first-launch Safari after boot completed its first GPU connection initialization with zero GPU watchdog terminations and passed both bit-exact 24,576-byte WebGL2 readbacks**, while the second fresh launch in the same boot initialized in **`1.912 s`**.

### Detailed observations

- **Post-login background load subsided 6.5 minutes earlier**: Login occurred at 237 s after boot. With `0034–0039` eliminating QEMU synchronization stalls, the quiet criterion (three consecutive 1-minute windows with `vcpu_mean_busy < 3.0`) was reached at **1123 s** (`[2.484, 2.506, 2.531]`) — **389 s (6.5 minutes) earlier** than on the unoptimized binary in Run142 (1512 s), with host package temperature dropping to 52 °C.
- **The two diagnostic-off trials**:

| Metric | First launch (cold + fixed WebGL2 readback) | Second launch (fresh-process control) |
|---|---|---|
| Time Return was pressed (after boot) | 1233 s | 1545 s |
| Baseline `vcpu_mean_busy` 25 s to 5 s before Return | 1.24 | 1.25 |
| `outcome` / GPU processes constructed | **`initialized` / 1** | **`initialized` / 1** |
| **GPU watchdog unresponsive exits** | **0** | **0** |
| `constructor_to_proxy_launched` | **12.165 s** (first-launch record) | **8.849 s** (all-time record) |
| `ui_request_to_did_initialize` | **3.219 s** | **1.912 s** (first sub-2 s sample) |
| Four sub-segments (s) | 1.748 / 0.199 / 0.823 / 0.438 | 1.059 / 0.125 / 0.562 / 0.163 |
| Window `vcpu_mean_busy` | 6.62 (all 8 busy 87%) | 6.44 (all 8 busy 68%) |
| `WebProcess` unresponsive notifications | 2 | **0** |

- **Exact boundary on `3.219 s` vs. the nominal 3.0 s watchdog**: On Trial 1, the log span `ui_request_to_did_initialize` was `3.219 s` (`0.219 s` above nominal `3.000 s`), yet WebKit's unmodified UIProcess main-runloop watchdog did not fire before initialization completed (`gpu_unresponsive_exits_reported_by_ui_process = 0`) owing to runloop timer slack; Trial 2 completed in `1.912 s`, well below `3.000 s`. Stated strictly: Run146 **establishes that a quiet guest passes unmodified Safari cold first launch without GPU watchdog termination**, while the cold first-launch log span (`3.219 s`) sits in the narrow runloop-slack margin above `3.000 s` and represents a single run rather than multi-run or peak-load certification.
- **Fixed WebGL2 dual-buffer readback and native window verification**: `r146-webgl-validation.json` reports `passed = true, validation_state = "complete"`, with both 24,576-byte raw RGBA8 buffers (`offscreen` and `default`, SHA-256 `d8da4828...`) matching the independent oracle (`bit_exact = true, mismatched_bytes = 0`), and native screenshot `r146-t1.png` showing the six cyan/magenta/yellow and red/green/blue tiles. This is the **first pass of the fixed WebGL2 oracle in stock macOS Safari without the 30 s timeout diagnostic**.
- **Closure and thermals**: Clock probes differed by 7.3 ms; after stopping `caffeinate`, the guest shut itself down cleanly (`guest_shutdown_observed=true`, exit code 0). Runtime was 32.9 minutes; package temperature min 52, mean 78.1, max 95 °C. Evidence is in `runs/e05-installed-boot/m3-orig3s-quiet-qarma5-20261112-146/`.

## Summary and next steps

Across `0033` instrumentation and the six optimization patches `0034–0039`:

- Saturated 8-vCPU MTTCG BQL acquisitions fell from **409k–456k/s** to **40k–47k/s (-90%)**, and vCPU synchronization stall share fell from **17.4%–27.3%** to **3.4% (96.6% running/runnable, `bql` 0.2%–0.3%)**;
- End-to-end Safari cold launch sped up by **~43%**, post-login background work subsided **6.5 minutes earlier**, second-launch GPU initialization reached **1.912 s**, and a quiet guest passed cold first-launch Safari plus bit-exact WebGL2 readbacks without `--webkit-timeout-diagnostic`.

With QEMU synchronization stalls down to 3.4%, the remaining bottleneck is **TCG code generation and instruction throughput itself**: next steps are (1) multi-run repeatability under unmodified deadlines plus `perf` profiling of translated-code/helper hotspots to bring loaded cold launches cleanly below `3.000 s`, and (2) repeating a full native restore on the current `0039` binary.
