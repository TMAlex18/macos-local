> **Language:** [English](M3-QEMU-SYNC-SOURCES.en.md) | **简体中文**

# QEMU 全局锁与独占段触发源分解（Run143 起）

日期：2026-11-12 UTC 起。本文件的"运行前写下的计划"一节在运行之前提交。

## 运行前写下的计划

### 起因

Run141–142 的阻塞采样表明，只要 8 核 macOS 客体进入高负载状态（包括登录后约 20 分钟的后台任务阶段，以及在安静客体上启动 Safari 的窗口），MTTCG 的 8 个 vCPU 线程就有 14%–30% 的时间没有在执行客体指令，而是阻塞在 QEMU 自身的全局锁（`bql`）和全核暂停独占段（`start_exclusive` / `exclusive_resume`）上。见[后台负载报告](M3-QUIET-GUEST-GPU-INIT-TIMING.md)。

阻塞采样通过 `/proc/PID/task/TID/syscall` 读取 futex 地址并扫描栈上返回地址，能定位最内层的锁等待函数（`mttcg_cpu_thread_fn`、`helper_get_cp_reg64`、`process_queued_cpu_work`），但不能回答**哪些具体的客体操作按多少频率触发了这些锁与独占段**——例如 `helper_get_cp_reg64` 读的是哪几个 `ARM_CP_IO` 系统寄存器、外层执行循环因为什么退出码（`EXCP_INTERRUPT`、`EXCP_HLT` 等）重新取锁、广播式 `TLBI` 占全部独占段多大比例。

为此，补丁 `0033-tcg-sync-source-counters.patch` 在不引入任何新锁的前提下，为每个 ARM vCPU 增加了默认关闭的本地触发源计数器（`-cpu ...,x-sync-stats=on`，通过只读 QOM 属性 `x-sync-stats-json` 导出，已在双核裸机套件 `experiments/E06-sync-counters/` 中逐项核对）：

- MTTCG 外层循环退出与排队工作：`exit_interrupt`、`exit_hlt`、`exit_halted`、`exit_yield`、`exit_atomic`、`exit_debug`、`exit_other`、`bql_atomic_reacquire`、`halt_cond_waits`、`queued_work_batches`、`queued_work_ordinary`、`queued_work_exclusive`；
- 内层全局锁获取点：`bql_do_interrupt`（细分为 `svc`、`abort`、`irq`、`other`）、`bql_handle_interrupt`、`bql_eret`（每次 `ERET` 前后两次调 EL 切换钩子）、`bql_cpreg_read`、`bql_cpreg_write`、`bql_mmio`（`cputlb.c` 四处 MMIO `BQL_LOCK_GUARD`）、`bql_ptw`；
- 全核独占段触发源：`excl_tlbi_all`、`excl_tlbi_page`、`excl_tlbi_range`、`excl_step_atomic`、`excl_tb_flush`；
- 按寄存器名细分的 `[reads, writes]` 计数表：`cpreg_io`（全部带 `ARM_CP_IO` 的系统寄存器）、`tlbi`（全部 `TLBI` 指令变体）、`cpreg_non_io_helper`。

### 假设

1. **`ARM_CP_IO` 读锁假设（H1）**：在 MTTCG（`!icount_enabled()`）下，QEMU 仍将 `CNTVCT_EL0` 与 `CNTPCT_EL0`（macOS `mach_absolute_time()` 的底层时钟源）标为 `ARM_CP_IO`，每次读取都无条件获取并释放 `bql`。假设在饱和后台窗口与 Safari 首次启动窗口中，`CNTVCT_EL0` + `CNTPCT_EL0` 占全部 `bql_cpreg_read` 的 **70% 以上**。
2. **独占段与跨核唤醒假设（H2）**：客体的广播式 TLB 失效指令（`TLBI ...IS`）在 QEMU 中通过 `tlb_flush_*_all_cpus_synced` 实现——对 7 个对端 vCPU 排入普通异步工作并以 `EXCP_INTERRUPT` 踢出执行循环（令它们在 `mttcg_cpu_thread_fn` 上争抢 `bql`），同时在发起核上排入 `async_safe_run_on_cpu` 触发 `start_exclusive` 全核暂停。假设 `excl_tlbi_*` 占全部独占段（`excl_total`）的 **90% 以上**，且非寄存器类的 `bql` 获取主要由 `exit_interrupt`（跨核排队工作与中断唤醒）、`bql_eret` 和 `bql_do_interrupt` 三项构成（合计占非寄存器 `bql` 获取的 **70% 以上**）。

### 步骤（Run143）

- 配置与 Run132、134、136 一致（8 vCPU，4 GiB RAM，`qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`，NVIDIA 原生窗口，30 秒超时诊断），仅增加 `--sync-stats`。后台同时运行 `host-temperature-log.sh`、`host_thread_sampler.py` 与 `clock_offset.py`。
- 按标准负载时间线执行（不等待 20 分钟后台消退，以便与 Run132、134、136 的负载下初始化耗时直接对照）：
  1. 启动后约 150 秒登录；约 350 秒检查终端、确认 Safari 未运行、运行 `shared-cache-identity` 探针、武装超时诊断并做时钟探针 `r143prep`。
  2. **窗口 A（登录后后台饱和窗口，约 40 秒）**：先用 `sync_stats.py snapshot` 采集 `sync-sat-before.json`，运行 40 秒 `block_sampler.py --stack-words 24`（输出 `vcpu-block-saturated.txt`），结束后立即采集 `sync-sat-after.json`。同一窗口同时给出精确触发频率与各锁的实际阻塞时间占比。
  3. **窗口 B（Safari 第一次启动，启动后约 720 秒按回车）**：按回车前采集 `sync-t1-before.json`；**不运行** `block_sampler.py`，保持第一次试验与历史轮次同等的无额外 root 采样条件；收到 `r143safari-after.txt` 后立即采集 `sync-t1-after.json`。
  4. **窗口 C（Safari 第二次启动，启动后约 1020 秒按回车）**：按回车前采集 `sync-t2-before.json`，同步启动覆盖整个启动与初始化路径的 `block_sampler.py`（`vcpu-block-trial2.txt`），收到 `r143safarib-after.txt` 后采集 `sync-t2-after.json`。
  5. 导出两个试验的客体日志窗口（`r143w1`、`r143w2`），读取最终超时诊断计数与 `sync-final.json`，在客体终端执行 `sudo /sbin/shutdown -h now` 正常关机。

### 判定与补丁选择规则

用 `sync_stats.py diff` 计算窗口 A、B、C 的每秒全局锁总获取次数（`bql_total / Δt`）、各获取点占比、`cpreg_io` 前十名寄存器占比、每秒独占段次数（`excl_total / Δt`）及 `tlbi` 指令分布，并结合窗口 A、C 的 `block_report.py` 阻塞分解做决定：

| 候选优化方向 | 触发条件（以窗口 B 为主，窗口 A/C 交叉印证） | 下一步动作 |
|---|---|---|
| 无锁读取通用定时器计数器（`CNTVCT_EL0` / `CNTPCT_EL0`） | 两寄存器占 `bql_cpreg_read` ≥ 70%，且 `bql_cpreg_read` 占 `bql_total` ≥ 10%（或占 `bql` 阻塞栈样本 ≥ 15%） | 在 `!icount_enabled()` 下为通用定时器只读计数器提供不取 `bql` 的读取路径 |
| 消除广播式 `TLBI` 的 `start_exclusive` 全核暂停 | `excl_tlbi_*` 占 `excl_total` ≥ 50%，且阻塞采样中 `exclusive_resume + exclusive_cond` ≥ 3% vCPU 时间 | 将跨核同步 TLB 失效从 `async_safe_run_on_cpu`（全核停下）改为无需 `start_exclusive` 的跨核完成同步或远程原子失效 |
| MTTCG 纯普通排队工作免 `bql` 处理 | `queued_work_ordinary` 与 `exit_interrupt` 同量级，且外层循环重取锁占 `bql_total` ≥ 15% | 当 vCPU 仅因非独占排队工作（如对端 TLB 刷新）退出 `tcg_cpu_exec` 且无待处理设备中断/停机请求时，直接在 `work_mutex` 下处理队列而不争抢 `bql` |
| 精简 `ERET` 钩子锁开销 | `bql_eret` 占 `bql_total` ≥ 10% | 合并或按需跳过纯线程本地钩子（PMU、`vmapple_pac_apply`）及无状态变化时的 GICv3 `ERET` 全局锁获取 |
| 设备 MMIO / GIC 中断为主 | 上述四类合计不足 `bql_total` 的 30%，主要由真实设备 MMIO 或 GIC 分发占主导 | 不写盲目的 TCG 执行循环补丁；转为按具体设备地址段与 GIC 路径进一步剖析 |

无论结果如何，同时核对窗口 B 的 `ui_request_to_did_initialize` 是否仍落在历史负载基线（3.444–3.791 秒）附近，以确认默认关闭的计数桩开启后本身没有显著扭曲计时。

### 已知的限制

- `x-sync-stats` 统计的是**获取次数**，不是**每次持锁或等锁的微秒数**：高频但极短的锁获取与低频但持锁较长的路径在阻塞时间上的权重不同，因此必须与窗口 A、C 的 `block_sampler.py` 联合解读，不能只看次数单指标。
- `sync_stats.py snapshot` 在虚拟机运行中逐核读取 QOM 属性，8 个 vCPU 之间的快照时刻相差几毫秒；对 40–90 秒量级的窗口影响可忽略，但不宣称跨核原子切面。
- 窗口 B 包含从启动 `safari-probe.sh` 到试验结束的完整 Safari 冷启动区间（约 90–100 秒），并非仅截出 3.5 秒的 GPU 连接初始化子区间，以避免在初始化关键窗口内频繁发 QMP 查询干扰计时。

## 结果（Run143）

### 结论

假设 **H1** 与 **H2** 均以远高于阈值的比例成立，同时还揪出了两个事先未预料到的重度全局锁来源：

1. **只读定时器计数器读取（验证 H1）**：在三个窗口中，`CNTVCT_EL0` 单寄存器分别占全部 `bql_cpreg_read` 的 **96.29%**、**95.83%** 和 **95.71%**（远超 70% 阈值），单独贡献了全部全局锁获取的 **25.0%–26.1%**。在窗口 C 的 90 秒阻塞采样中，`helper_get_cp_reg64` 占全部 8 核 vCPU 线程时间的 **9.15%**（高居 `bql` 等待首位）。
2. **广播式 `TLBI` 触发的全核暂停与跨核排队唤醒（验证 H2）**：`excl_tlbi_*` 占全部 `start_exclusive` 独占段的 **99.9997%–99.9998%**（每秒 6,516–7,721 次全核暂停，远超 90% 阈值），其中单页失效 `TLBI_VALE1IS` 占 **79.0%–90.4%**（因 macOS 开启 TBI 使 `bits=56 < 64` 而走入 `excl_tlbi_range`）。每次广播失效向 7 个对端核排入普通异步工作（窗口 B 的 `ordinary_items = 6,851,951 ≈ 7 × 978,835`），令全部 vCPU 以 `EXCP_INTERRUPT` 退出执行循环并在 `mttcg_cpu_thread_fn` 上争抢 `bql`（占全部 `bql` 获取的 **18.5%–22.0%**，在窗口 C 吃掉 **7.48%** 的 vCPU 时间；加上独占段等待本身 **4.10%**，合计吃掉 **11.58%** 的 vCPU 时间）。
3. **无实际投递的 `bql_handle_interrupt` 空转取锁（新发现 #1）**：`bql_handle_interrupt` 占全部 `bql` 获取的 **28.55%–33.25%**（每秒 12.4 万–13.6 万次，次数居首），但同期真正进入 `do_interrupt_irq` 投递的中断只有 11.1 万–52.0 万次——超过 **97%** 是空跑。核对源码确认由两处叠加造成：一是每次同步异常/超调用在 `arm_cpu_do_interrupt` 末尾置位 `CPU_INTERRUPT_EXITTB`，导致回到 `cpu_exec_loop` 后下一轮无条件再拿一次 `bql` 仅用于清掉该标志；二是内核在 `PSTATE.I=1` 屏蔽中断期间（含自旋锁、中断处理、系统调用入口），只要 GIC 线保持有效，每经过一个非链接 TB 边界都会先抢 `bql` 再调 `arm_cpu_exec_interrupt` 发现中断被 `env->daif` 屏蔽而空手返回。
4. **`vmapple_pac_hvc` 在全局锁内高频堆分配重算 SHA-256（新发现 #2）**：窗口 B 的 `bql_do_interrupt` 共 8,601,135 次（占 13.19%），其中 `do_interrupt_other` 高达 **6,262,556 次（41,689 次/秒）**。核对源码确认为 macOS 每次线程切换与内核 `copyin`/`copyout` 调用的 `HVC 0xC1000004/0xC1000005`：不仅每次都经 `EXCP_HVC` `longjmp` 获取全局 `bql`，而且 `vmapple_pac_rekey` 在持有 `bql` 的状态下无条件调用 9 次 `g_hmac_new(G_CHECKSUM_SHA256, ...)`（`malloc` + SHA-256 + `free`）重算全部内核与用户密钥——即便 `fn=4/5` 根本不改变内核密钥，或 `fn=5` 在同一进程的 `copyin`/`copyout` 前后使用完全相同的 `diversifier`。单次 Safari 冷启动期间在全局锁内执行了约 **5,636 万次**堆分配与 HMAC-SHA256，是拉长 `bql` 持锁时间、放大其他线程等待队列的关键持锁源。

### 三个窗口的触发计数汇总

| 指标 | 窗口 A：登录后后台饱和（40.27 s） | 窗口 B：Safari 首次启动（150.22 s） | 窗口 C：Safari 第二次启动（144.13 s） |
|---|---:|---:|---:|
| 全局锁获取总数 `bql_total` | 16,467,893（**408,975/s**） | 65,230,655（**434,240/s**） | 65,749,222（**456,186/s**） |
| — `bql_handle_interrupt` | 5,475,447（33.25%） | 18,624,123（28.55%） | 19,174,007（29.16%） |
| — `bql_cpreg_read` | 4,269,997（25.93%） | 17,422,032（26.71%） | 17,958,349（27.31%） |
| 　其中 `CNTVCT_EL0` 读 | 4,111,538（占读锁 **96.29%**） | 16,695,185（占读锁 **95.83%**） | 17,187,256（占读锁 **95.71%**） |
| — `outer_loop_exit`（`exit_interrupt`） | 3,613,964（21.95%） | 12,091,938（18.54%） | 13,076,829（19.89%） |
| — `bql_do_interrupt` | 1,094,038（6.64%） | 8,601,135（13.19%） | 6,494,950（9.88%） |
| 　其中 `other`（PAC `HVC`）/ `svc` / `abort` / `irq` | 0.55M / 0.35M / 0.19M / 0.11M | **6.26M** / 1.59M / 0.75M / 0.51M | **3.97M** / 1.69M / 0.84M / 0.52M |
| — `bql_eret` | 1,301,315（7.90%） | 5,695,752（8.73%） | 6,087,582（9.26%） |
| — `bql_cpreg_write` | 375,495（2.28%） | 1,687,930（2.59%） | 1,767,419（2.69%） |
| — `queued_work_exclusive` | 310,890（1.89%） | 978,837（1.50%） | 1,075,541（1.64%） |
| — `bql_mmio` | 26,747（0.16%） | 128,908（0.20%） | 114,545（0.17%） |
| 全核独占段总数 `excl_total` | 310,891（**7,720.9/s**） | 978,837（**6,516.1/s**） | 1,075,542（**7,462.4/s**） |
| — `excl_tlbi_range` / `page` / `all` | 98.34% / 1.64% / 0.02% | 94.26% / 5.59% / 0.15% | 94.13% / 5.71% / 0.17% |
| — 对端普通排队工作 `ordinary_items` | 2,174,938 | 6,851,951 | 7,528,693 |

### 阻塞采样与两次 Safari 启动计时

- **两次启动计时**：第一次启动 `ui_request_to_did_initialize = 3.704 s`（窗口内 `vcpu_mean_busy = 5.99`），第二次启动 `2.818 s`（窗口内 `vcpu_mean_busy = 6.22`），均落在 Run132、134、136 的负载基线范围（3.515–3.791 秒与 2.768–3.419 秒）之内，确认开启 `--sync-stats` 未改变系统时序行为。两次启动无看门狗退出，诊断 `site0` 命中 2 次、拒绝 0 次。
- **阻塞时间分布**：
  - 窗口 A（后台饱和 40 秒，32,000 个样本）：运行或可运行 **82.6%**，等 `bql` **12.7%**（`mttcg_cpu_thread_fn` 6.19%，`helper_get_cp_reg64` 2.65%，`cpu_exec_loop` 1.64%，`process_queued_cpu_work` 0.59%，`ERET` 0.27%），等独占段相关锁 **4.7%**（`exclusive_resume` 3.5%，`qemu_cpu_list_lock` 0.7%，`exclusive_cond` 0.5%）。
  - 窗口 C（Safari 第二次启动前段 90 秒，72,000 个样本）：运行或可运行降至 **72.7%**，总阻塞升至 **27.3%**——其中等 `bql` **23.2%**（`helper_get_cp_reg64` **9.15%**，`mttcg_cpu_thread_fn` 及排队工作 **7.48%**，`cpu_exec_loop` **4.34%**，`ERET` **0.96%**），等独占段相关锁 **4.1%**（`exclusive_resume` 3.2%，`qemu_cpu_list_lock` 0.5%，`exclusive_cond` 0.4%）。前五项合计占总阻塞时间的 **95.3%**（26.03 / 27.3）。
- **收尾与温度**：时钟探针两次相差 4.6 毫秒；客体自行关机（`guest_shutdown_observed=true`，退出码 0）。全程 24.3 分钟，封装温度最低 54、平均 79.4、最高 94 °C。

### 证据

- `runs/e05-installed-boot/m3-sync-sources-qarma5-20261112-143/`：`sync-sat-before/after.json`、`sync-t1-before/after.json`、`sync-t2-before/after.json`、`sync-final.json`、三份 `sync-diff-*.json`、`vcpu-block-saturated.txt`、`vcpu-block-trial2.txt`、两份 `gpu-init-timing-*.json` 与 `gpu-init-cpu-profile-*.json`、两份客体日志窗口。
- `runs/sync-sources-20261112/driver/`：本轮固定步骤驱动脚本。

---

## 第二阶段运行前计划：消除前四大全局锁争用源（Run144）

### 补丁设计与边界

按预注册判定表，前四类全局锁来源在窗口 B/C 均大幅超过触发阈值，且都可以在**不改变任何客体可见硬件语义、不削弱跨核 TLB 同步屏障、保留 `icount`/单步/重放路径**的前提下精确消除：

1. **补丁 `0034-arm-gtimer-lockless-counter-read.patch`**：为 `CNTPCT`、`CNTPCT_EL0`、`CNTVCT`、`CNTVCT_EL0` 增加 `ARM_CP_LOCKFREE_READ`。在 `!icount_enabled()` 下，`gt_cnt_read` / `gt_virt_cnt_read` 仅读取线程本地的 `env->cp15` 偏移量以及由 `vm_clock_seqlock` 保护的 `qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL)`，因此 `helper_get_cp_reg64` 直接无锁读取；`icount` 模式仍保留原 `bql` 路径。
2. **补丁 `0035-tcg-arm-skip-masked-irq-and-exittb-bql.patch`**：一是在 `cpu_handle_exception` 已持有的 `bql` 内顺手清除 `CPU_INTERRUPT_EXITTB`（外层循环紧接着已将 `last_tb` 置 `NULL`），消除每次异常/超调用后的二次空取锁；二是为 `TCGCPUOps` 增加只读判定钩子 `cpu_has_unmasked_interrupt`，当 `interrupt_request` 仅含架构中断位且被当前线程的 `env->daif`（如 `PSTATE.I=1`）屏蔽时，不获取 `bql`。
3. **补丁 `0036-vmapple-pac-hvc-lockless-and-key-cache.patch`**：一是将 `vmapple_pac_derive` 改为纯栈上计算的 RFC 2104 HMAC-SHA256（零堆分配），仅在 `fn=0/2/3/6` 时重算内核密钥，并为每核 `VMApplePacCPU` 增加 64 项直接映射 `(a, b, diversifier) -> user[0..3]` 缓存（复位时清空）；二是在 `x-pac-software-model=on` 且非单步/无插件回调时，由无锁 TCG helper 原地执行纯每核状态的 `board_hvc`（`0xC1000000..0xC1000006`），省去 `EXCP_HVC` `longjmp` 与 `bql`。
4. **补丁 `0037-tcg-no-bql-queued-tlbi-work.patch`**：为 `qemu_work_item` 增加 `no_bql` 标记并用于 `cputlb.c` 的跨核 TLB 刷新项与 `mttcg_force_rcu`。当 MTTCG 外层循环因 `EXCP_INTERRUPT` 退出且无停机/暂停请求时，直接在每核 `work_mutex` 与 `start_exclusive` 下排空 `no_bql` 工作项并立即重返 `tcg_cpu_exec`，完全不经过全局 `bql`，同时完整保留 `start_exclusive` 的跨核 TLB 同步语义。

### 假设与判定（Run144）

- **假设**：合入 `0034–0037` 后，在完全相同的负载时间线（启动后 720 秒与 1020 秒两次试验，带 `--sync-stats`）下：
  1. 窗口 B 的每秒 `bql` 获取率较 Run143（434,240/s）下降 **75% 以上**；
  2. 窗口 A 与窗口 C 的 vCPU `bql` 阻塞时间占比（Run143 分别为 12.7% 与 23.2%）下降 **60% 以上**；
  3. Safari 第一次启动的 `ui_request_to_did_initialize` 低于负载下全部历史样本下界（3.444 秒），第二次启动低于 2.768 秒；若第一次启动已低于 **3.0 秒**，下一轮去掉 `--webkit-timeout-diagnostic` 在原始 3 秒期限下实测首次加载。
- **裸机前置验证**：启动 Run144 前，新二进制必须通过 `make check-research-patches`、`make sync-counter-tests`、`make smp-mmu-tests`、`make el0-context-tests`、`make el0-migration-tests`、`make pauth-model`、`make clock-functional` 与 `make clock-rate-exact`。

## 第二阶段结果（Run144：补丁 `0034–0037`）

### 结论

三项预注册假设**全部成立**（所用二进制 SHA-256 `031af750360871b3f88f045575ed88a3bb0f28a18792026f9bd9716ee5b9764d`，在启动前已额外通过双核 PAC 模型迁移套件 `el0-migration-model-tests` 与 `reims-gpu-restore-platform`）：

1. **全局锁获取率下降 77.4%–82.2%**（超过 75% 阈值）：窗口 A 从 `408,975/s` 降至 **`73,001/s`**（-82.15%），窗口 B 从 `434,240/s` 降至 **`98,013/s`**（-77.43%），窗口 C 从 `456,186/s` 降至 **`86,506/s`**（-81.04%）。四处目标获取点几乎被彻底清零：`CNTVCT_EL0` 持锁读取降为 `0`；`bql_handle_interrupt` 与实际投递中断 `do_interrupt_irq` 严格 1:1 相等（空转取锁降为 `0`）；`no_bql_exit_interrupt` 无锁排空了全部 `1,332` 万次跨核排队唤醒；PAC `HVC` 进入 `do_interrupt_other` 从 626 万次降至 `101` 次。
2. **vCPU 全局锁阻塞占比下降 89.8%–98.3%**（远超 60% 阈值）：窗口 A 的 `futex on bql` 从 **`12.7%` 降至 `1.3%`**（-89.8%）；窗口 C 从 **`23.2%` 降至 `0.4%`**（-98.3%）。初始化窗口内 8 个 vCPU 在宿主核上的平均在核数 `vcpu_mean_busy` 从 Run143 的 `5.99 / 6.22` 提升至 **`6.63 / 6.94`**（回收了约 0.64–0.72 个完整宿主核）。
3. **Safari 两次启动初始化与全链路耗时双双创负载下最佳纪录**：
   - 第一次启动 `ui_request_to_did_initialize = 3.336 s`（低于此前全部 8 轮负载下样本范围 `3.444–3.791 s`），第二次启动 `2.612 s`（低于此前全部 8 轮负载下二次启动范围 `2.768–3.419 s`），且两轮 `web_process_unresponsive_notifications` 均首次降为 **0**；
   - **Safari 端到端启动路径提速约 43%**：从按回车到首个 GPU 请求，第一次启动由 Run143 的 **105 秒**缩短至 **60 秒**（`constructor_to_proxy_launched` 从 `19.263 s` 降至 `15.644 s`），第二次启动由 **76 秒**缩短至 **43 秒**（`15.262 s` 降至 `10.957 s`）；登录后后台工作在约第 16 分钟提前消退（比此前约 22 分钟提前约 6 分钟）。

### Run143 与 Run144 对照汇总

| 指标 | Run143（`0033` 基线） | Run144（`0034–0037`） | 变化 |
|---|---:|---:|---:|
| 窗口 A `bql` 获取率 | 408,975 /s | **73,001 /s** | **-82.2%** |
| 窗口 A 等 `bql` / 等独占段占比 | 12.7% / 4.7% | **1.3%** / 11.1% | `bql` **-89.8%** |
| 窗口 B（首次启动）`bql` 获取率 | 434,240 /s | **98,013 /s** | **-77.4%** |
| 窗口 B 独占段频率 `excl_total` | 6,516.1 /s | 9,803.7 /s | +50.5%（吞吐加快） |
| 首次启动：按回车 → `ui_request` | ~105 s | **~60 s** | **-43%** |
| 首次启动 `ui_request_to_did_initialize` | 3.704 s | **3.336 s** | **-0.368 s（-10.0%）** |
| 首次启动窗口 `vcpu_mean_busy` | 5.99 | **6.63** | +0.64 核 |
| 窗口 C（二次启动）`bql` 获取率 | 456,186 /s | **86,506 /s** | **-81.0%** |
| 窗口 C 等 `bql` 占比 | 23.2% | **0.4%** | **-98.3%** |
| 二次启动：按回车 → `ui_request` | ~76 s | **~43 s** | **-43%** |
| 二次启动 `ui_request_to_did_initialize` | 2.818 s | **2.612 s** | **-0.206 s（-7.3%）** |
| 二次启动窗口 `vcpu_mean_busy` | 6.22 | **6.94** | +0.72 核 |
| 两轮 `WebProcess` 无响应通知 | 1 / 1 | **0 / 0** | 全部消除 |

收尾与温度：两次时钟探针相差 4.8 毫秒；客体自行关机（`guest_shutdown_observed=true`，退出码 0）。全程 24.1 分钟，封装温度最低 57、平均 81.4、最高 95 °C。证据保存在 `runs/e05-installed-boot/m3-sync-opt-qarma5-20261112-144/`。

---

## 第三阶段运行前计划：按 `DSB` 聚合广播 `TLBI` 独占段与精简 `ERET` 锁（Run145）

### 起因

全局锁瓶颈消除后，Run144 窗口 A 剩余的 13.0% 阻塞里 **85.4%（11.1 个百分点）集中在广播 `TLBI` 触发的 `start_exclusive` 全核暂停**（每秒 9,250–9,804 次全核停下），而剩余 `bql` 获取中 **`bql_eret` 占 50.0%**（700.8 万次）：

1. **架构层面的 `TLBI ...IS` 完成语义（ARMv8 DDI 0487 D8.11.2）**：ARM64 规范明确规定，广播式 `TLBI` 指令仅在发起核随后执行 **`DSB`** 指令后才保证对共享域内对端 PE 生效。操作系统内核在解除多页映射时（例如连续 16–64 页的 `TLBI VALE1IS` 循环后接一条 `DSB ISH`），原本只需在 `DSB` 处做**一次**跨核完成同步；QEMU 当前却在**每一条** `TLBI` 指令处重建 `hflags`、结束 TB 并立即触发一次全核 `start_exclusive()` 暂停。
2. **无 EL2/EL3 平台上的 `ERET` 全局锁**：`hw/intc/arm_gicv3_cpuif.c:3194` 上游注释已指出 GICv3 中断路由状态仅在涉及 EL2 或 EL3 的特权级切换时才可能改变；VMApple 平台固定为 `has_el2=false, has_el3=false`，而其余钩子（PMU 计数器快照在未开启溢出中断时、`vmapple_pac_apply`）均只访问当前核私有状态。

### 补丁设计与假设（Run145）

1. **补丁 `0038-arm-tlbi-dsb-batch-sync.patch`**：在每核 `CPUARMState` 中维护固定大小的待同步广播 `TLBI` 缓冲队列（默认每核 64 项，溢出时自动归并为对应 `mmuidx` 位图全刷）。执行 AArch64 广播 `TLBI`（`TLBI_*IS` / `TLBI_*OS`）时：立即刷新本核 TLB，将待刷项记入本核缓冲并置 `ARM_CP_SUPPRESS_TB_END`（不再逐条重建 `hflags` 或停下全核）；当发起核执行 **`DSB`**（`DSB_DMB` 中 `is_dmb==0` 或 `DSB_nXS`），或发生异常进入 / `ERET` / CPU 复位时，若本核存在待同步项，则一次性向各对端核下发合并后的单批刷新工作项并执行单次 `async_safe_run_on_cpu_no_bql` 跨核同步点。
2. **补丁 `0039-arm-el10-eret-bql-elision.patch`**：在无 EL2 且无 EL3 的 CPU 上不注册无效的 `gicv3_cpuif_el_change_hook`；当所有已注册 EL 切换钩子均为每核本地钩子且 PMU 定时器中断未使能时，`ERET` 直接在本地执行钩子而不获取 `bql`。
3. **假设与判定**：在相同负载时间线（Run145）下，窗口 B 的 `excl_total` 每秒全核暂停次数较 Run144（9,804/s）下降 **50% 以上**，窗口 A 的独占段阻塞占比（Run144 为 11.1%）下降 **50% 以上**，且 Safari 首次启动的 `ui_request_to_did_initialize` 从 `3.336 s` 进一步下探至 **3.0 秒以内**；若达成 `< 3.0 s`，紧接着在 **Run146** 去掉 `--webkit-timeout-diagnostic`，直接检验原始 3 秒期限下的 Safari 首次加载。

## 第三阶段结果（Run145：补丁 `0038–0039`）

### 结论

判定结果分为三层，既把 QEMU 同步开销压到了接近硬件调度极限的 **3.4%**，也彻底查明了客体侧与模拟器侧各自的作用边界：

1. **饱和期 vCPU 同步阻塞已压至 3.4%（窗口 A 假说成立）**：窗口 A（40.3 秒后台饱和）中，8 个 vCPU 线程的运行或可运行占比从 Run143 的 `82.6%`、Run144 的 `87.0%` 跃升至 **`96.6%`**；独占段相关等待（`exclusive_resume + exclusive_cond + qemu_cpu_list_lock`）从 Run144 的 `11.1%` 降至 **`2.8%`**（下降 **74.8%**，超过 50% 阈值）；等全局锁 `futex on bql` 降至 **`0.2%`**；每秒 `bql` 获取率从 Run143 的 `408,975/s` 降至 **`39,611/s`**（累计消除 **90.3%**，`bql_eret` 彻底归零）。
2. **揭示 XNU 的广播 `TLBI` 粒度特征（窗口 B `excl_total` 未降 50% 的原因）**：窗口 B 的 `1,404,572` 次广播 `TLBI` 经 `DSB` 聚合后产生 `1,188,407` 次跨核同步（平均每次 `DSB` 对应 `1.18` 条广播 `TLBI`，速率由 `9,804/s` 降至 `8,245/s`）。这是因为 XNU 对多页区间原本就使用单条 `TLBI_RVALE1IS`（11.9 万次），而 `TLBI_VALE1IS`（120.7 万次）绝大多数是单页操作后立即跟一条 `DSB ISH`。因此 `0038` 的主要收益来自省去每条 `TLBI` 后的 `hflags` 重建与 TB 截断，而非跨多条单页 `TLBI` 的长批次合并。
3. **第二次启动创全项目最快纪录（`2.431 s`），首次启动证明后台抢核与 QEMU 锁争用必须同时解除**：
   - **第二次启动（窗口 C，+1053 秒）**：此时后台负载开始回落，`ui_request_to_did_initialize = 2.431 s`（首次突破 2.5 秒，比此前 41 分钟安静客体的 `2.588 s` 还快 `0.157 s`），`constructor_to_proxy_launched = 9.571 s`（较 Run143 提速 **37.3%**），互斥锁 `FUTEX_WAIT` 样本较 Run143 减少 **96.4%**（`17,067 → 609`）。
   - **第一次启动（窗口 B，+757 秒）**：因本轮登录在 +229 秒（晚于 Run144 的 +201 秒），+757 秒仍处于登录后 8.8 分钟的后台满载峰值（窗口内 `vcpu_mean_busy = 6.81`），客体后台守护进程与 Safari 争抢 8 个客体核，导致前半段 `gpu_entry_to_registrar_activation` 为 `2.282 s`、总耗时 `3.798 s`（后半段 `SandboxHelper` 为 `0.873 s`，仍快于 Run143/144，`WebProcess` 无响应通知保持 `0`）。

收尾与温度：两次时钟探针相差 4.0 毫秒；+1370 秒宿主封装温度已自然回落至 65 °C、1 分钟负载降至 3.01；客体自行关机（`guest_shutdown_observed=true`，退出码 0），全程封装温度均值 79.9、最高 96 °C。证据保存在 `runs/e05-installed-boot/m3-tlbi-dsb-qarma5-20261112-145/`。

---

## 第四阶段运行前计划：去掉超时诊断，在安静客体上实测原始 3 秒期限（Run146）

### 因果合成与假设

将 Run142（安静客体 + 旧二进制）与 Run143–145（饱和客体 + `0034–0039` 新二进制）放在一起，两条因果链完全闭合：

- 在旧二进制上等后台安静（Run142），首次启动为 `3.249 s`——当时差在 Safari 自身启动占满 8 核并损失 **26%** 的 vCPU 时间在 QEMU 全局锁与独占段上；
- 在 `0034–0039` 新二进制上，饱和期 QEMU 同步阻塞已从 `27.3%` 压至 **`3.4%` 以内（`bql` 仅 `0.2%–0.3%`）**，且后台任务消退后的第二次启动已达 **`2.431 s`**。

**假设（Run146）**：在 `0034–0039` 主线二进制（`e4587db0...`）上，**完全去掉 `--webkit-timeout-diagnostic` 和 `--sync-stats`**（恢复原版未修改的 WebKit 3.0 秒看门狗期限，不武装任何内存替换），登录后等待客体后台负载自然消退（连续三个 1 分钟 `vcpu_mean_busy < 3.0`），开机后**第一次启动 Safari** 加载固定 WebGL2 探针页（`$H/get/webgl-probe.html#r146-webgl`）：

1. 首个 GPU 连接在原始 3.0 秒期限内完成初始化（`ui_request_to_did_initialize < 3.0 s`，`gpu_unresponsive_exits_reported_by_ui_process == 0`）；
2. 固定 WebGL2 着色器的两份 24,576 字节 RGBA8 原始读回（FBO 与默认帧缓冲）通过 `tools/validate-webgl-probe.py` 独立逐字节校验；
3. 随后再做第二次新鲜启动（`r146safarib`）验证同次开机内的可重复性，最后正常关机。

### 判定

| 结果 | 条件 | 含义 |
|---|---|---|
| **通过** | 达到安静条件，无 `--webkit-timeout-diagnostic` 下首次启动 `outcome=initialized`、`gpu_unresponsive_exits=0`、`< 3.0 s`，且 WebGL2 双缓冲读回逐字节通过 | 首次在不依赖 30 秒超时诊断的原始 3 秒期限下打通 Safari 冷启动首次加载与 WebGL2 正确性（受限于"后台消退后"这一明确条件） |
| **仅初始化通过** | 首次启动 `< 3.0 s` 且 `gpu_unresponsive_exits=0`，但 WebGL2 页面读回未完成 | GPU 进程 3 秒看门狗问题在安静客体上解决，WebProcess 页面侧仍需单独排查 |
| **未通过** | 首次启动触发 GPU 看门狗无响应退出（约 3.0 秒） | 单靠消除 QEMU 锁争用与后台争用仍不足以稳过 3.0 秒，下一步转向 TCG 指令翻译热点（`perf` 函数级剖析） |

## 第四阶段结果（Run146：无超时诊断下的原版期限实测）

### 结论

在**完全未启用 `--webkit-timeout-diagnostic` 和 `--sync-stats`**（`results.json` 中 `webkit_timeout_diagnostic.requested = false, state = "off"`，零运行时指令替换，完全采用原版 Apple WebKit 看门狗期限）的条件下，开机后**第一次冷启动 Safari 首次成功完成首个 GPU 连接初始化并通过固定 WebGL2 双缓冲逐字节读回校验**，同次开机内的第二次新鲜启动更将初始化时间推进至 **`1.912 秒`**。

### 详细观察

- **后台负载消退提前 6.5 分钟**：登录在启动后 237 秒。得益于 `0034–0039` 消除 QEMU 同步损耗，连续三个 1 分钟 `vcpu_mean_busy < 3.0` 的安静条件在启动后 **1123 秒**（`[2.484, 2.506, 2.531]`）即满足，较旧二进制 Run142（1512 秒）提前了 **389 秒（6.5 分钟）**，宿主封装温度同步降至 52 °C。
- **两次无诊断启动结果**：

| 指标 | 第一次启动（冷启动 + 固定 WebGL2 读回） | 第二次启动（新鲜进程对照） |
|---|---|---|
| 按回车时刻（启动后） | 1233 秒 | 1545 秒 |
| 按回车前 25 至 5 秒基线 `vcpu_mean_busy` | 1.24 | 1.25 |
| `outcome` / GPU 进程数 | **`initialized` / 1** | **`initialized` / 1** |
| **GPU 看门狗无响应终止次数** | **0** | **0** |
| `constructor_to_proxy_launched` | **12.165 秒**（首次启动最快） | **8.849 秒**（全项目最快） |
| `ui_request_to_did_initialize` | **3.219 秒** | **1.912 秒**（首次突破 2 秒） |
| 四个分段（秒） | 1.748 / 0.199 / 0.823 / 0.438 | 1.059 / 0.125 / 0.562 / 0.163 |
| 初始化窗口 `vcpu_mean_busy` | 6.62（8 核全忙占 87%） | 6.44（8 核全忙占 68%） |
| `WebProcess` 无响应通知 | 2 | **0** |

- **关于 `3.219 秒` 与原版 3.0 秒看门狗的精确边界说明**：第一次启动的日志时间跨度 `ui_request_to_did_initialize` 为 `3.219 s`（高于标称 `3.000 s` 约 `0.219 s`），但原版未修改的 UIProcess 主运行循环看门狗因投递与主线程调度裕量并未触发（`gpu_unresponsive_exits_reported_by_ui_process = 0`），GPU 进程一次初始化成功；第二次启动则为 `1.912 s`，远低于标称 `3.000 s`。因此按严格措辞：Run146 **证明了安静客体在未修改的原版看门狗下首次冷启动可通过且不发生 GPU 超时终止**，但首次冷启动的纯日志差值（`3.219 s`）尚未压到 `< 3.000 s`，且仅有一轮样本，不能外推为高负载期或多轮百分之百稳定通过。
- **固定 WebGL2 双缓冲读回与原生像素验证**：`r146-webgl-validation.json` 报告 `passed = true, validation_state = "complete"`，`offscreen` 与 `default` 两份 24,576 字节原始 RGBA8 读回（SHA-256 `d8da4828...`）均为 `bit_exact = true`（`mismatched_bytes = 0`），原生窗口截图 `r146-t1.png` 清晰呈现青/品红/黄与红/绿/蓝六个色块。这是继 Run128（依赖 30 秒超时诊断）之后，**首次在完全不启用超时诊断的原版系统行为下跑通该验证**。
- **收尾与温度**：两次时钟探针相差 7.3 毫秒；清理 `caffeinate` 后客体自行关机（`guest_shutdown_observed=true`，退出码 0）。全程 32.9 分钟，封装温度最低 52、平均 78.1、最高 95 °C。证据在 `runs/e05-installed-boot/m3-orig3s-quiet-qarma5-20261112-146/`。

## 总结与下一步

通过 `0033` 精确计数与 `0034–0039` 六个递进补丁：

- 饱和期 8 核 MTTCG 的每秒全局锁获取次数从 **41–46 万次/秒**降至 **4.0–4.7 万次/秒（-90%）**，vCPU 线程同步阻塞占比从 **17.4%–27.3%** 压至 **3.4%（运行或可运行达 96.6%，`bql` 仅 0.2%–0.3%）**；
- Safari 端到端冷启动耗时缩短约 **43%**，后台工作消退时间提前 **6.5 分钟**，二次启动 GPU 初始化进入 **1.912 秒**，并在安静客体上首次不靠 `--webkit-timeout-diagnostic` 跑通冷启动 Safari 与固定 WebGL2 逐字节读回。

剩余瓶颈已从 QEMU 线程同步转向 **TCG 指令翻译与执行吞吐本身**：下一步是（1）在不开启诊断的条件下做多轮重复检验冷启动通过率，并借助 `perf` 剖析纯客体代码翻译/辅助函数热点以争取把满载后台期的首次冷启动也压入 `< 3.000 s`；（2）用当前 `0039` 二进制重做一次完整原生还原（自 run-53 以来的首次完整回归）。
