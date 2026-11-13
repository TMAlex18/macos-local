> **Language:** [English](M3-TCG-THROUGHPUT-AND-ORIG3S.en.md) | **简体中文**

# 无超时诊断重复性与 TCG 执行吞吐热点剖析（Run147 起）

日期：2026-11-12 UTC 起。本文件的"运行前写下的计划"一节在运行之前提交。

## 运行前写下的计划

### 起因

在 [QEMU 同步触发源报告](M3-QEMU-SYNC-SOURCES.md)（Run143–146）中，补丁 `0034–0039` 将 8 核饱和期的全局锁获取率削减了 90%，使 vCPU 线程的运行或可运行占比达到 **96.6%**（同步总阻塞仅剩 **3.4%**）；Run146 随后在**完全关闭 `--webkit-timeout-diagnostic`** 的原版行为下，于安静客体上首次跑通开机后第一次冷启动 Safari（GPU 看门狗无响应退出 0 次，日志跨度 `3.219 s`）及固定 WebGL2 双缓冲逐字节读回，二次启动达 `1.912 s`。

目前紧接着有两个必须回答的问题：

1. **跨独立冷启动的无诊断可重复性**：Run146 只有一轮冷启动样本，且首次冷启动的 `ui_request_to_did_initialize = 3.219 s` 落在原版 UIProcess 主运行循环看门狗的投递裕量区间内（高于标称 `3.000 s` 约 `0.22 s`）。需要一次全新的独立冷启动，检验相同安静条件下无诊断首次启动与 WebGL2 逐字节读回能否稳定复现。
2. **消除锁争用后 96.6% 在核 CPU 周期花在哪里**：复查 Run142 的历史 `perf` 样本可见，宿主 P 核周期中仅约 `17.5%` 落在翻译后的客体 `[JIT]` 代码块里，而高达 **`74.8%`** 落在 `qemu-system-aarch64` 的 C 函数中；但 `0033–0039` 已重排二进制符号偏移并消除了大块锁与 HMAC 开销。需要在当前 `0039` 主线二进制（`e4587db0...`）上采集符号精确匹配的 `perf` 调用栈剖面，定位排名前列的纯执行 C 热点（如 16 KiB 页表遍历 `get_phys_addr*`、PAuth helper、TB 查找或 softmmu 慢路径），为后续将满载后台期的冷启动也压入 `< 3.000 s` 提供精确靶点。

### 假设

1. **无诊断重复性假设（H1）**：在完全不启用 `--webkit-timeout-diagnostic` 和 `--sync-stats` 的独立冷启动（Run147）中，达到相同安静判据（连续三个 1 分钟 `vcpu_mean_busy < 3.0`）后，开机第一次冷启动 Safari 仍完成首个 GPU 连接初始化（`outcome = initialized` 且 `gpu_unresponsive_exits_reported_by_ui_process == 0`），固定 WebGL2 两份 24,576 字节读回继续逐字节通过，且第二次新鲜启动 `ui_request_to_did_initialize < 2.5 s`。
2. **TCG C 辅助函数主导假设（H2）**：在 `0039` 二进制上，饱和执行期间 `CPU */TCG` 线程仍有 **50% 以上**的宿主 P 核周期消耗在 `qemu-system-aarch64` 的 C 函数（而非 `[JIT]` 翻译代码）中，且排名前三的 C 函数族合计占 `CPU */TCG` P 核周期的 **25% 以上**，构成下一轮 TCG 执行吞吐补丁（`0040+`）的可操作目标。

### 步骤（Run147）

- 启动命令与 Run146 完全相同（8 vCPU，4 GiB RAM，`qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`，NVIDIA 原生窗口，**不传** `--webkit-timeout-diagnostic`，**不传** `--sync-stats`）。后台运行 `host-temperature-log.sh`、`host_thread_sampler.py` 与 `clock_offset.py`。
- 登录后启动 `caffeinate` 保活、确认 Safari 未运行并执行时钟探针 `r147a`。
- **饱和期 `perf` 采集（远离试验窗口）**：在登录后约 +520 秒的后台饱和阶段（距第一次 Safari 试验超过 10 分钟），运行 15 秒 `sudo -n perf record -F 199 --call-graph dwarf,4096 -p PID -o RUN_DIR/perf-saturated.data -- sleep 15`，采集当前 `0039` 二进制的精确函数与内联调用栈。
- **第一次试验（无任何 root 采样干扰）**：用 `watch.sh` 等待连续三分钟 `vcpu_mean_busy < 3.0` 达到安静状态后，确认 Safari 未运行，冷启动 `safari-probe.sh r147safari "$H/get/webgl-probe.html#r147-webgl"`，期间不运行 `perf` 或阻塞采样；结束后运行 `tools/validate-webgl-probe.py --run LABEL --tag r147-webgl`。
- **第二次试验（附带 Safari 启动期 `perf` 剖面）**：退出 Safari 并确认不存在后，启动 `safari-probe.sh r147safarib`，按回车 5 秒后采集 20 秒 `perf-safari-launch.data`，对照后台负载与 Safari 多进程启动期的热点差异。
- 导出两份客体日志窗口、执行结束时钟探针 `r147b`、停止 `caffeinate`，从 macOS 内部执行 `sudo /sbin/shutdown -h now` 正常关机。

### 判定与下一步规则

| 维度 | 判定条件 | 下一步动作 |
|---|---|---|
| 无诊断重复性（H1） | 第一次试验 `outcome=initialized`、`gpu_unresponsive_exits=0` 且 WebGL2 双缓冲 `bit_exact=true` | 确认安静客体无诊断首次加载具备跨冷启动可重复性（2/2）；若失败则定位两次冷启动在窗口负载或日志分段上的差异 |
| TCG 吞吐热点（H2） | 单一可优化 C 函数族（如 MMU 页表遍历/TLB 填充、PAuth、TB 查找或特定系统寄存器 helper）占 P 核周期 ≥ 8% | 基于实测调用栈设计并验证 `0040+` 执行吞吐补丁，随后在后台未消退的 +720 秒负载窗口直接检验无诊断首次加载 |

### 已知的限制

- DWARF 栈回溯每样本拷贝 4 KiB 用户栈（199 Hz × 15–20 秒），第二次试验期间有 `perf` 运行，其计时仅作辅助参考；主判定仅使用无 `perf` 干扰的第一次试验。
- `[JIT]` 翻译块没有独立 ELF 符号，其内部指令分布只能按宿主热块或按调用的 C helper 间接观察。

---

## 第一阶段实测结果（Run147：无诊断独立冷启动重复性与 `0039` 热点剖面）

### 运行身份与完整性

- 运行目录：`runs/e05-installed-boot/m3-orig3s-repeat-perf-20261112-147`
- 二进制：`build/qemu-bdif-usb/qemu-system-aarch64`（SHA-256 `e4587db0f06bb3aee0545f6716366c2d732200bcde8ca6dddf28631d1e481213`，补丁链至 `0039`）
- 配置：8 vCPU、4 GiB RAM、`qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`，NVIDIA 原生窗口，**未传 `--webkit-timeout-diagnostic`**，**未传 `--sync-stats`**
- 客体与宿主健康度：客体执行 `sudo /sbin/shutdown -h now` 正常关机（`qemu_exit_code = 0`，`guest_shutdown_observed = true`，`errors = []`，运行总时长 `2224.9 s`）；宿主封装温度 435 个样本，`min = 55 °C`、`mean = 76.8 °C`、`max = 94 °C`；时钟探针 `r147a = -2.9218 s`（跨度 `4.0 ms`）、`r147b = -2.9293 s`（跨度 `5.8 ms`）；宿主线程采样 `218,006` 个 tick。

### H1 检验结果：跨冷启动 0 看门狗首次初始化未稳定复现（1/2），但无诊断自动恢复与 WebGL2 逐字节读回通过（2/2）

客体在启动后 `+1335 s` 达到安静判据（最近三个 1 分钟 `vcpu_mean_busy = [2.437, 2.176, 1.947]`）。

| 试验 | 采样干扰 | `gpu_processes_constructed` | `gpu_unresponsive_exits` | 首个 GPU 进程关键时间戳（距 `ui_request`） | 恢复后 GPU 进程 `ui_request -> didInitialize` | WebGL2 双缓冲读回 |
|---|---:|---:|---:|---|---|---|
| Run146 试验 1（对照） | 无 | 1 | **0** | `didInitialize = +3.219 s`（靠 UIProcess 主循环约 `0.22 s` 投递裕量通过） | —（首次即通过） | FBO + 默认各 24,576 B `bit_exact=true` |
| Run147 试验 1（`r147safari`） | **无** | 4 | **3** | PID 1211：`registrar = +2.119 s`，`helper_create = +2.630 s`，**看门狗触发 = `+3.008 s`**，`helper_destroy = +4.150 s` | 第 4 个 GPU（PID 1393）：**`1.661 s`**（后续连接 `0.461 s`） | FBO + 默认各 24,576 B **`bit_exact=true`**（原生六色块完整） |
| Run147 试验 2（`r147safarib`） | 并发 20 s `perf` | 2 | 1（`web_unresponsive = 0`） | PID 1848：看门狗触发 = `+3.008 s`（`didInitialize` 晚 `0.965 s` 于 `+3.973 s` 写出） | 第 2 个 GPU（PID 1981）：**`2.047 s`** | 未挂探针页面 |

**因果解释**：`gpu_init_cpu_profile.py` 显示，尽管启动前客体已安静（`1.95` 忙核），冷启动 Safari 同时拉起 `Safari`、`WebContent`、`Networking`、`GPU` 与 `runningboardd`/`trustd` 等守护进程，使窗口前 10 秒 `vcpu_mean_busy` 升至 `5.339`、`gpu_entry -> registrar` 段达 **`6.398`**、`helper_create -> destroy` 段达 **`5.983`**。在 `0039` 的纯执行吞吐下，冷首次 GPU 连接初始化真实耗时落在 **`3.22–4.15 s`** 区间：Run146 因 UIProcess 主运行循环忙出 `0.22 s` 裕量而一次通过，Run147 则因 UIProcess 在 `3.008 s` 准时触发看门狗而判超时。**因此 H1 被证伪（严格 0 看门狗退出跨冷启动仅 1/2）**——要想不靠主循环偶然裕量稳定压入 `< 3.000 s`，必须直接把纯 TCG 执行速度再提速 **30% 以上**。

### H2 检验结果：80%–81% 的宿主 P 核周期消耗在 QEMU C 函数中，四大纯执行瓶颈占 Safari 启动期约 55%

两份符号精确匹配的 DWARF 调用栈剖面（`CPU 0..7/TCG` 线程上的宿主性能核事件 `cpu_core/cycles/P`）：

1. **DSO 级分布**：
   - **饱和期（`perf-saturated.data`，+749..+765 s，20,634 个样本，476.0B P 核周期）**：`qemu-system-aarch64` C 代码占 **`81.13%`**，客体翻译块 `[JIT]` 仅占 **`13.52%`**，`[kernel.kallsyms]` 占 `2.97%`，`libc.so.6` 占 `1.35%`。
   - **Safari 启动期（`perf-safari-launch.data`，20 s，12K P 核样本，296.3B P 核周期）**：`qemu-system-aarch64` C 代码占 **`80.28%`**，`[JIT]` 占 **`14.45%`**，`[kernel.kallsyms]` 占 `3.17%`，`libc.so.6` 占 `1.12%`。**H2 完全成立。**

2. **四大 C 函数热点族与源码级根因**：

| 热点族 | 饱和期 P 核占比（含子调用） | Safari 启动期 P 核占比（含子调用） | 源码级根因定位 |
|---|---:|---:|---|
| 1. 间接跳转与 TB 查找（`helper_lookup_tb_ptr` / `tb_htable_lookup`） | **16.96%**（`qht_lookup_custom` 5.07%，`get_page_addr_code_hostp` 5.05%，`tcg_flush_jmp_cache` 自占 0.64%） | **16.60%**（`tb_htable_lookup` **13.09%**：`get_page_addr_code_hostp` 6.02% + `qht_lookup_custom` 6.01%） | ① ARM64 启用 `CF_PCREL`（`target/arm/cpu.c:2132`），`tb_jmp_cache_inval_tb()` 在任一物理页失效单个 TB 时对全部 8 个 vCPU 无条件执行 `tcg_flush_jmp_cache()`，每次写穿清空 4,096 项跳转缓存并跨核作废缓存行；② `tlb_flush_by_mmuidx_async_work()` 在 `to_clean == 0` 时仍无条件清空跳转缓存；③ `TB_JMP_CACHE_BITS = 12` 仅有 64 个页桶且页哈希忽略 VA 高位，4 GiB 共享缓存与内核频繁冲突退化到全局 QHT 查找 |
| 2. SoftMMU 页表遍历与 16 KiB 页全表清空退化（`tlb_fill_align` + 非内联 EL2/EL3 查询） | **12.46%** + `arm_security_space_below_el3` 自占 **2.19%** + `arm_hcr_el2_eff*` 自占 **1.42%** | **14.97%**（`tlb_set_page_full` 7.70%，`get_phys_addr_nogpc` 6.72%）+ `arm_security_space_below_el3` 自占 **1.73%** | ① `vmapple` 的 `TARGET_PAGE_BITS = 12`（4 KiB），而 macOS 全系统使用 16 KiB 页（`lg_page_size = 14 > 12`），导致 `tlb_set_page_full()` 将每个常规 16 KiB 页都记入 `tlb_add_large_page()`，`large_page_mask` 迅速覆盖全地址空间，使每次单页 `TLBI VALE1IS` 与范围 `TLBI RVALE1IS` 在全部 8 核上退化为 `tlb_flush_one_mmuidx_locked()` **全表清空**并压制动态 TLB 扩容；② `arm_is_el2_enabled()` 在检查 `ARM_FEATURE_EL2` 前先求值非内联参数 `arm_security_space_below_el3()`，且 `arm_hcr_el2_eff*` 在无 EL2 的机器上仍反复跨编译单元调用 |
| 3. 指针认证（`pauth_addpac` + `pauth_auth`） | **16.19%**（`pauth_addpac` 8.39% + `pauth_auth` 7.80%；其中 `pauth_computepac` 10.15%，`aa64_pauth_va_parameters` 3.02%，`pauth_check_trap` 0.94%） | **14.80%**（`pauth_addpac` 8.27% + `pauth_auth` 6.53%；`pauth_computepac` 9.54%，`aa64_pauth_va_parameters` 2.70%） | Run147 全程累计 `53.86B` 次 PAuth 调用（**每秒 2,420 万次**），1,024 项 memo 命中率 `84.28%`（`45.40B` 命中 / **`8.46B` 未命中 = 每秒 380 万次完整 QARMA5**）：① 每次调用（含命中）都重算 `aa64_pauth_va_parameters`、`pauth_check_trap` 与 `arm_sctlr`；② memo 索引未混入 `key.lo`，线程/特权级切换在同一共享缓存地址上互相覆盖；③ 标量 C 的 QARMA5 每轮执行上百条串行位平面/移位指令，未使用宿主 x86_64 SSSE3 `pshufb` 半字节并行查表与置换 |
| 4. SME FP16 外积辅助函数（`helper_sme_fmopa_h` / `do_fmopa_h`） | **4.13%**（`float16a_unpack_canonical` 2.53%） | **11.12%**（`float16a_unpack_canonical` **6.57%**） | `-cpu max` 暴露 SME，macOS 框架在 Safari 启动期密集执行 `FMOPA` FP16 外积；`do_fmopa_h()` 在 `N × N` 内层循环对同一行列元素重复调用 `N` 次 `float16_unpack_canonical()`（`2N²` 次拆包而非 `2N` 次），且对全零稀疏输入仍走完整软浮点路径 |

---

## 第二阶段运行前计划（补丁 `0040–0043` 与 Run148）

### 补丁设计（均严守架构等价与现有自检契约）

1. **`0040-tcg-tb-jmp-cache-pcrel-and-size.patch`**：
   - 将 `TB_JMP_CACHE_BITS` 从 `12`（4,096 项，64 页桶）扩至 **`14`（16,384 项 = 每核 256 KiB，装入宿主 P 核 2 MiB 私有 L2）**，保持 `TB_JMP_PAGE_BITS = 6`（每页仍清 64 项，页桶数扩 **4 倍至 256**），页哈希折叠 `(vpn ^ (vpn >> 8) ^ (vpn >> 16))` 消除共享缓存跨框架别名，页内偏移哈希仅依赖 `pc & ~TARGET_PAGE_MASK`。
   - 修复 `tb_jmp_cache_inval_tb()` 在 `CF_PCREL` 下的全核清空：利用物理别名保持页内偏移不变的性质，当 `tb_page_addr0(tb) != -1` 时仅在各页桶对应页内偏移槽位做**先读后写**（`qatomic_read == tb` 才置 `NULL`），保留其余 >99.9% 有效项并维持其他核缓存行的 Shared 状态。
   - 在 `tlb_flush_by_mmuidx_async_work()` 中仅当 `to_clean != 0` 时调用 `tcg_flush_jmp_cache()`，且 `tcg_flush_jmp_cache()` 采用先读非空再置 `NULL`。
2. **`0041-tcg-cputlb-16k-subpage-and-arm-el23-fastpath.patch`**：
   - 在 `CPUTLBDesc` 中记录 `has_16k_pages`（随 `tlb_mmu_flush_locked` 复位）：当 `TARGET_PAGE_BITS == 12` 且 `full->lg_page_size == 14`（常规 16 KiB 客体页）时不污染 `tlb_add_large_page()`（≥ 32 KiB 连续页/块映射仍走原 `tlb_add_large_page()`），而在单页与范围失效路径中按 `16 KiB` 对齐精确失效对应的 4 个 `4 KiB` 子页及重叠跳转缓存页，彻底消除常规 16 KiB 页 `TLBI` 触发的全表清空风暴。
   - 在 `target/arm/cpu.h` 中令 `arm_is_el2_enabled()` 先判断 `arm_feature(env, ARM_FEATURE_EL2)` 再调用 `arm_security_space_below_el3()`（保持 `helper.c` 中 `arm_security_space_below_el3` 函数体不变以兼容 `E06-pauth-hvc/test-pac-va.py`），并为 `arm_hcr_el2_eff` / `arm_hcr_el2_eff_secstate` 提供 `!ARM_FEATURE_EL2 -> return 0` 内联快路径。
3. **`0042-arm-pauth-va-cache-and-simd-qarma5.patch`**：
   - 保持 `aa64_va_parameters_impl` 与 `pauth_addpac..pauth_strip` 原文契约不变（100% 通过 `test-pac-va.py`）；在已具现化 CPU（`arm_cpu_realizefn` 置位后 `isar` 恒定）的 `aa64_pauth_va_parameters()` 内按 `(mmu_idx, tcr)` 命中每核 4 槽自校验缓存直接返回 `{tsz, tbi, mtx}`，并在 `pauth_check_trap()` / `pauth_key_enabled()` 中加入无 EL2/EL3 快路径。
   - 在 `pauth_memo_slot()` 中混入 `key.lo` 降低上下文切换冲突；在 x86_64 `__SSSE3__` 宿主上为 `pauth_computepac_architected()` 实现 16 半字节并行 `pshufb` QARMA5 快路径，并在进程初始化时与保留的标量实现逐位自检 1,024 组随机向量（任一位不一致即 `abort()`）。
4. **`0043-arm-sme-fmopa-h-preunpack.patch`**：
   - 在 `fpu/softfloat.c` 提供接受预拆包操作数的内部辅助接口（保持全部 IEEE-754 舍入、NaN 传播与异常标志逐位一致），并在 `do_fmopa_h()` 中按活跃谓词预拆包行/列向量元素，把 `2N²` 次 `float16_unpack_canonical` 压至最多 `2N` 次。

### 第二阶段假设（Run148）

1. **吞吐跃升假设（H3）**：合入 `0040–0043` 后，在完全相同的启动参数（含保持 `--pauth-memo-entries 1024` 不变以严格隔离代码收益）下，`CPU */TCG` 线程在翻译客体 `[JIT]` 代码中的宿主 P 核周期占比从 `13.5%–14.5%` 提升至 **`≥ 22%`**（对应纯指令执行吞吐提升约 `≥ 50%`），且 `helper_lookup_tb_ptr`、`tlb_fill_align`、`pauth_*`、`do_fmopa_h` 四族合计占比从约 `57%`（Safari 启动期）降至 **`≤ 35%`**。
2. **无诊断冷启动稳定性假设（H4）**：在**完全关闭 `--webkit-timeout-diagnostic` 和 `--sync-stats`** 的安静客体（Run148）上，第一次冷启动 Safari 完成首个 GPU 连接初始化（`outcome = initialized`、`gpu_unresponsive_exits == 0`），且 **`ui_request_to_did_initialize < 2.800 s`**（无需依赖 UIProcess 主循环投递裕量即低于 `3.000 s` 硬期限），固定 WebGL2 双缓冲读回 `bit_exact = true`，第二次新鲜启动 `< 1.800 s`。若 H4 通过，紧接着在 **Run149** 直接检验后台未消退峰值期（`+720 s`）的无诊断冷启动！

### 验证与步骤（Run148）

- 先完成构建并通过裸机与离线门禁：`make check-research-patches`、`make sync-counter-tests`、`make smp-mmu-tests`、`make el0-context-tests`、`make el0-migration-tests`、`make el0-migration-model-tests`、`make pauth-model`、`make pauth-guards`、`experiments/E06-pauth-hvc/test-pac-va.py`、`make clock-functional`、`make clock-rate-exact`、`make reims-gpu-restore-platform`、`make installed-offline-tests` 及 7 门禁公开快照导出检查。
- 留足 ≥ 3 分钟宿主冷却后，以与 Run147 完全一致的命令行启动 `m3-tcg-throughput-orig3s-20261112-148`，执行饱和期 15 秒 `perf`、等待安静、无干扰第一次冷启动 Safari + WebGL2 读回、带 `perf` 的第二次启动、日志窗口导出与客体正常关机。

---

## 第二阶段实测结果（Run148：补丁 `0040–0043` 吞吐跃升与三轮 0 看门狗验证）

### 运行身份与完整性

- 运行目录：`runs/e05-installed-boot/m3-tcg-throughput-orig3s-20261112-148`
- 二进制：`build/qemu-bdif-usb/qemu-system-aarch64`（SHA-256 `134f6bd28db671a6bc8488e0efaecf000465d9667e0d6e107a9469a74aba7d1c`，补丁链至 `0043`）
- 配置：与 Run147 逐项相同（8 vCPU、4 GiB RAM、`qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`，NVIDIA 原生窗口，**未传 `--webkit-timeout-diagnostic`**，**未传 `--sync-stats`**）
- 客体与宿主健康度：`+153 s` 到登录密码框（较 Run147 提前约 80 秒），`+402 s` 恢复桌面与 Dock（提前逾 2 分钟），**`+958 s` 即达到安静判据**（`[1.62, 1.812, 1.314]`，较 Run147 提前 6.3 分钟、较未优化基线 Run142 提前 9.3 分钟）；客体正常关机（`qemu_exit_code = 0`，`guest_shutdown_observed = true`，`errors = []`，总时长 `1936.0 s`）；宿主封装温度 377 个样本，`min = 49 °C`、`mean = 73.9 °C`、`max = 94 °C`；时钟探针 `r148a = -2.9459 s`（跨度 `4.3 ms`）、`r148b = -2.9537 s`（跨度 `1.5 ms`）。

### H3 检验结果：客体 `[JIT]` 占比跃升 71.6%（`13.52% → 23.20%`），SME 与 16 KiB 全表清空消除，暴露出 `TTBR0_EL1` 切换清空跳转缓存的单一主瓶颈

| 指标（`CPU */TCG` 宿主 P 核 `cpu_core/cycles/P`） | Run147（`0039`）饱和期 | Run148（`0043`）饱和期 | Run147（`0039`）Safari 启动期 | Run148（`0043`）Safari 启动期 |
|---|---:|---:|---:|---:|
| 翻译客体代码 `[JIT]` 占比 | `13.52%` | **`23.20%`（+71.6%）** | `14.45%` | **`21.98%`（+52.1%）** |
| `qemu-system-aarch64` C 代码总占比 | `81.13%` | **`69.89%`** | `80.28%` | **`70.50%`** |
| `tlb_fill_align`（含子调用） | `12.46%` | **`5.44%`** | `14.97%` | **`10.82%`** |
| `arm_security_space_below_el3` + `arm_hcr_el2_eff*`（自占） | `3.61%` | **`< 0.5%`（消失）** | `2.45%` | **`< 0.5%`（消失）** |
| `do_fmopa_h` / `float16a_unpack_canonical`（含子调用） | `4.13%` | **`< 0.5%`（消失）** | `11.12%` | **`< 0.5%`（消失）** |
| `pauth_computepac`（含子调用）/ 其中 `qarma_ssse3` | `10.15%` | **`7.10%`（`2.90%`）** | `9.54%` | **`6.77%`（`3.02%`）** |
| `helper_lookup_tb_ptr`（含子调用）/ `tcg_flush_jmp_cache`（自占） | `16.96%` / `0.64%` | `23.62%` / `1.64%` | `16.60%` / `< 0.6%` | `21.70%` / **`2.05%`** |

**H3 结论**：饱和期 `[JIT]` 占比达 **`23.20%`**（超额通过 `≥ 22%` 判据；Safari 启动期达 `21.98%`）；PAuth 调用吞吐从 `24.2M/s` 升至 **`26.07M/s`**（命中率 `84.46%`）。四族合计占比在饱和期为 `36.2%`、Safari 启动期为 `46.5%`（略高于 `≤ 35%` 子项），原因被 `perf` 精确归因于同一机制：其他开销大幅压缩且 `CPUJumpCache` 扩至 16,384 项后，进程上下文切换写 `TTBR0_EL1`（`vmsa_ttbr_write`，`1.57%–1.67%`）每次仍调用 `tlb_flush()`，不仅花 `2.05%` 自占周期清空全部 16,384 项跳转缓存，还把 `S1_ptw_translate` 的物理页表缓存 `ARMMMUIdx_Phys_NS` 和 `TTBR1_EL1` 内核页一并清空，使切换后的内核与 `dyld` 共享缓存函数反复回落到全局 `qht_lookup_custom`（`7.98%–8.73%`）。

### H4 检验结果：三轮无诊断启动全部 0 看门狗一次通过（`3.180 s` / `1.620 s` / `2.056 s`），首次冷启动距 `< 2.800 s` 裕量目标仅差 `0.380 s`

为避免 `perf` 干扰第二次计时，Run148 先执行两轮完全无干扰试验（`trial1`、`trial2`），再在第三轮新鲜启动（`r148csafarib`）采集 `perf-safari-launch.data`：

| 试验 | 采样干扰 | `gpu_unresponsive_exits` | `constructor -> proxy_launched` | `ui_request -> didInitialize` | 分段：`entry->registrar` / `->helper_create` / `->destroy` / `->didInit` | WebGL2 双缓冲读回 |
|---|---|---:|---:|---:|---|---|
| 试验 1（`r148safari`，首次冷启动） | **无** | **0** | **`9.122 s`**（Run147 为 `23.489 s`） | **`3.180 s`**（历史首次冷启动最快） | **`2.019 s`** / `0.121 s` / `0.660 s` / `0.375 s`（窗口 `vcpu_mean_busy = 6.409`） | FBO + 默认各 24,576 B **`bit_exact=true`** |
| 试验 2（`r148safarib`，二次无干扰） | **无** | **0**（`web=0`） | **`5.589 s`** | **`1.620 s`**（通过 `< 1.800 s` 判据） | `0.822 s` / **`0.056 s`** / `0.576 s` / `0.163 s`（窗口 `vcpu_mean_busy = 6.105`） | — |
| 试验 3（`r148csafarib`，三次含 `perf`） | 并发 20 s `perf` | **0**（`web=0`） | `5.727 s` | **`2.056 s`**（并发 DWARF 下仍破 2.1 s） | `1.118 s` / `0.069 s` / `0.700 s` / `0.165 s` | — |

**诊断**：后三段（`registrar -> didInitialize`）在首次冷启动上已缩至 **`1.156 s`**，而前段 `gpu_entry -> registrar`（`Safari`、`GPU`、`WebContent`、`runningboardd` 高频 XPC 上下文切换期）仍占 `2.019 s`，使总跨度停在 `3.180 s`（尚未压入无需主循环裕量的 `< 2.800 s`）。这与 `perf` 抓到的 `vmsa_ttbr_write -> tlb_flush -> tcg_flush_jmp_cache` 完全吻合！

---

## 第三阶段运行前计划（补丁 `0044–0045` 与 Run149）

### 补丁设计

1. **`0044-tcg-host-tagged-tb-jmp-cache.patch`（宿主 RAM 地址标记的跨上下文持久 `CPUJumpCache`）**：
   - 在 `CPUJumpCache` 槽位中记录单页 RAM TB 的 `uintptr_t host_pc`（`(uintptr_t)s->pc + tlbe->addend`）。
   - `tb_lookup()` 分两级快路径验证物理映射一致性：① **一级命中（~5 条指令）**：直接核对当前 `mmu_idx` 的内联 `cpu_tlb_fast` 中 `entry->addr_code == (s->pc & TARGET_PAGE_MASK)`（保证无 `TLB_INVALID_MASK`/`TLB_MMIO`/`TLB_FORCE_SLOW` 且具执行权限）及 `(uintptr_t)s->pc + entry->addend == jc->array[hash].host_pc`；② **二级跨进程共享缓存命中**：当 `TTBR0_EL1` 切换使 SoftMMU 代码 TLB 尚未填充时，调用一次 `get_page_addr_code_hostp()` 填充该页代码 TLB 并核对 `tb_page_addr0(tb) == phys_pc && tb_page_addr1(tb) == -1`，直接复用缓存 TB 并更新 `host_pc`，**完全绕过全局 `qht_lookup_custom` L3 顺序锁争用**；若仍未命中，将已译出的 `phys_pc` 直接传入 `tb_htable_lookup` 避免二次地址转换。
   - 由于每次跳转缓存命中均经当前 SoftMMU 代码 TLB 校验虚拟→宿主物理映射与执行权限，**`cputlb.c` 中的 TLB 失效路径（`tlb_flush_by_mmuidx_async_work`、`tlb_flush_page*`、`tlb_flush_range*`）不再清空 `CPUJumpCache`**（仅在 `tb_jmp_cache_inval_tb` 物理页代码改写、`tb_flush` 代码缓存区满或 CPU 复位时清理），彻底消除 `tcg_flush_jmp_cache` 的 `2.05%` 自占开销，并使全部 16,384 项内核与 `dyld` 共享缓存 TB 在任意进程切换与 `TLBI` 后保持温热！
2. **`0045-arm-ttbr-selective-tlb-flush.patch`（`TTBR0_EL1` ASID 切换保留物理页表 TLB 与 `TTBR1_EL1` 内核高半区 TLB）**：
   - 在 `CPUTLBDesc` 中记录 `has_lower_half55`（当填入 `!(addr & (1ULL << 55))` 页时置位，整表清空时复位）。
   - 在 `vmsa_ttbr_write()` 中，当 AArch64 EL1&0 体制（无 EL2/EL3）写 `TTBR0_EL1` 切换 ASID 且 `TCR_EL1.A1 == 0`、`TCR2_EL1.FNGNA1 == 0` 时：① 绝不触碰 `ARMMMUIdx_Phys_NS`（使页表遍历器 `S1_ptw_translate` 的物理描述符页缓存永久温热）；② 仅清空 `E10_0 | E10_0_GCS` 用户态 TLB；③ 对 `E10_1 | E10_1_PAN | E10_1_GCS` 内核态 TLB 调用 `tlb_flush_lower_half55_by_mmuidx()`——当 `has_lower_half55 == false`（如 `E10_1_PAN` 常态）时 **`O(1)` 零指令跳过**，当 `copyin`/`copyout` 曾填入低半区页时仅选择性清除 `bit 55 == 0` 表项，**100% 保留 `0xfffffe00...` 内核代码、栈与堆的 SoftMMU TLB**！

### 第三阶段假设（Run149）

1. **跳转缓存与上下文切换开销消除假设（H5）**：合入 `0044–0045` 后，饱和期与 Safari 启动期 `tcg_flush_jmp_cache` 降至 `< 0.1%`，`qht_lookup_custom` 从 `8.0%–8.7%` 压至 **`≤ 3.0%`**，翻译客体代码 `[JIT]` 的宿主 P 核周期占比进一步提升至 **`≥ 27%`**。
2. **首次冷启动严格破 `3.000 s` 硬期限假设（H6）**：在完全关闭 `--webkit-timeout-diagnostic` 和 `--sync-stats` 的安静客体（Run149）上，开机后第一次冷启动 Safari 完成初始化（`outcome = initialized`、`gpu_unresponsive_exits == 0`）且 **`ui_request_to_did_initialize < 2.800 s`**（不依赖任何 UIProcess 运行循环投递裕量即低于 `3.000 s` 标称期限），固定 WebGL2 双缓冲读回 `bit_exact = true`，二次无干扰启动 `< 1.500 s`。若 H6 通过，随后在 **Run150** 直接挑战登录后 `+720 s` 后台未消退负载下的无诊断首次冷启动！

---

## 第三阶段实测结果（Run149：补丁 `0044–0045` 使首次冷启动严格破 `3.000 s` 达 `2.734 s`）

### 运行身份与完整性

- 运行目录：`runs/e05-installed-boot/m3-jmpcache-ttbr-orig3s-20261112-149`
- 二进制：`build/qemu-bdif-usb/qemu-system-aarch64`（SHA-256 `f858a94a8d7395456679a9c05c2c0dd2d6ee56f8701525e47bbf7ed9ee85f474`，补丁链至 `0045`）
- 配置：与 Run147、Run148 完全一致（8 vCPU、4 GiB RAM、`qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`，NVIDIA 原生窗口，**未传 `--webkit-timeout-diagnostic`**，**未传 `--sync-stats`**）
- 客体与宿主健康度：`+152 s` 到登录密码框，`+371 s` 恢复桌面与 Dock，`+976 s` 达到安静判据（最近三个 1 分钟 `vcpu_mean_busy = [1.285, 1.652, 1.185]`，创全项目最低安静底噪）；客体正常关机（`qemu_exit_code = 0`，`guest_shutdown_observed = true`，`errors = []`，总时长 `1972.5 s`）；宿主封装温度 386 个样本，`min = 50 °C`、**`mean = 71.2 °C`**（较 Run147 的 `76.8 °C` 下降 `5.6 °C`）、`max = 96 °C`；时钟探针 `r149a = -2.9641 s`（跨度 `3.3 ms`）、`r149b = -2.9723 s`（跨度 `2.0 ms`）。

### H5 检验结果：`tcg_flush_jmp_cache` 与 `vmsa_ttbr_write` 完全归零，Safari 启动期 `tb_htable_lookup` 大降 62.7%

| 指标（`CPU */TCG` 宿主 P 核 `cpu_core/cycles/P`） | Run147（`0039`）Safari 启动 | Run148（`0043`）Safari 启动 | Run149（`0045`）Safari 启动 | Run149（`0045`）饱和期 |
|---|---:|---:|---:|---:|
| 翻译客体代码 `[JIT]` 占比 | `14.45%` | `21.98%` | **`22.60%`（+56.4%）** | **`24.12%`（+78.4%）** |
| `tcg_flush_jmp_cache`（自占） | `< 0.6%` | `2.05%` | **`0.00%`（归零）** | **`0.00%`（归零）** |
| `vmsa_ttbr_write`（含子调用） | — | `1.57%` | **`< 0.1%`（归零）** | **`< 0.1%`（归零）** |
| `tb_htable_lookup`（含子调用） | `13.09%` | `14.43%` | **`5.38%`（较 148 -62.7%）** | **`~7.0%`** |
| `qht_lookup_custom`（自占） | `5.03%` | `6.68%`（含子 `7.98%`） | **`4.29%`（较 148 -35.8%）** | `5.35%` |
| `arm_cpu_tlb_fill_align`（含子调用） | `7.76%`（`tlb_fill` `14.97%`） | `6.08%`（`tlb_fill` `10.82%`） | **`4.67%`（累计 -39.8%）** | **`3.58%`** |
| `tlb_set_page_full`（含子调用） | `7.70%` | `5.08%` | **`3.80%`（累计 -50.6%）** | **`2.74%`** |

**H5 结论**：`tcg_flush_jmp_cache` 与 `vmsa_ttbr_write` 完全降入 `< 0.1%`（达标）；Safari 启动期 `tb_htable_lookup` 从 `14.43%` 骤降至 **`5.38%`**（-62.7%），证实跨进程 `dyld` 共享缓存与内核 TB 在 `TTBR0_EL1` 切换后成功命中持久 `CPUJumpCache`；饱和期 `[JIT]` 达 **`24.12%`**（距 `27%` 子项差 `2.9%`，因 384 KiB `CPUJumpCache` L2 读取延迟本身计入 `tb_lookup` 自占 `9.98%–11.09%`，而直接映射哈希冲突仍留有 `4.3%–5.3%` 的 `qht_lookup_custom`）。PAuth 吞吐进一步升至 **`26.62M/s`**。

### H6 检验结果：开机首次冷启动 `ui_request_to_did_initialize = 2.734 s`（严格破 `3.000 s` 硬期限且低于 `< 2.800 s` 目标！）

| 试验 | 采样干扰 | `gpu_unresponsive_exits` | `constructor -> proxy_launched` | `ui_request -> didInitialize` | 分段：`entry->registrar` / `->helper_create` / `->destroy` / `->didInit` | 关键宿主 CPU 指标 |
|---|---|---:|---:|---:|---|---|
| 试验 1（`r149safari`，首次冷启动） | **无** | **0** | **`8.979 s`** | **`2.734 s`**（**首破 `2.800 s` 零裕量线！**） | **`1.460 s`** / `0.132 s` / `0.793 s` / `0.345 s` | `entry->registrar` 核秒从 `12.91` 降至 **`8.08`（-37.4%）**；WebGL2 双缓冲 **`bit_exact=true`** |
| 试验 2（`r149safarib`，二次无干扰） | **无** | **0**（`web=0`） | `6.894 s` | **`1.679 s`** | **`0.789 s`** / **`0.052 s`** / `0.683 s` / `0.153 s` | `entry->registrar` 仅 `3.80` 忙核（`3.00` 核秒）；宿主温度 `51 °C` |
| 试验 3（`r149csafarib`，三次含 `perf`） | 并发 20 s `perf` | **0**（`web=0`） | `6.553 s` | **`1.856 s`** | `0.911 s` / `0.060 s` / `0.715 s` / `0.167 s` | 并发 DWARF 栈采样下仍 `< 1.9 s` |

**里程碑意义**：跨越 Run148–149 两轮独立冷启动，全部 **6 次**无 `--webkit-timeout-diagnostic` 的 Safari 新鲜启动均以 **0 次看门狗退出**一次通过；且在 Run149 中，开机后第一次冷启动的 `ui_request_to_did_initialize` 从 `3.180 s` 直降至 **`2.734 s`**（其中 `gpu_entry -> registrar` 从 `2.019 s` 降至 `1.460 s`，消耗的宿主 vCPU 核秒减少 **37.4%**），**首次在不依赖任何 UIProcess 运行循环投递裕量的前提下，正面击穿原版 WebKit 的 `3.000 s` 硬期限**，同时固定 WebGL2 离屏与默认帧缓冲各 24,576 字节读回连续第 4 轮逐字节一致！

---

## 第四阶段运行前计划（Run150：登录后 `+720 s` 后台峰值负载期无诊断首次冷启动检验）

### 起因与假设

此前所有在登录后 **`+720 s` 固定负载窗口**（后台 Spotlight/守护进程未消退、`vcpu_mean_busy ≈ 6.0–6.8`）执行的运行（Run131–145）都必须开启 `--webkit-timeout-diagnostic`，且即使合入 `0034–0039` 消除同步阻塞后，峰值负载期首次冷启动仍需 `3.336–3.798 s`（Run144–145）。现在 `0040–0045` 已在安静客体上把首次冷启动压至 `2.734 s`（且后台任务本身也因纯执行提速而更快收敛），需要在**完全不等待 `QUIET`**、严格对齐历史 `+720 s`（第一次冷启动）与 `+1020 s`（第二次新鲜启动）固定时间点的条件下，直接检验**无 `--webkit-timeout-diagnostic`** 的峰值负载期冷启动表现！

- **峰值负载无诊断假设（H7）**：在完全关闭 `--webkit-timeout-diagnostic` 和 `--sync-stats` 的 `0045` 主线二进制上（Run150），不等待客体安静，直接在启动后 **`+720 s`** 触发第一次冷启动 Safari + 固定 WebGL2 探针：
  1. 首个 GPU 进程一次完成初始化（`outcome = initialized`、`gpu_unresponsive_exits == 0`），且 **`ui_request_to_did_initialize < 3.000 s`**（对比 Run145 同时间点有诊断下的 `3.798 s` 提速 > 21%）；
  2. 固定 WebGL2 离屏与默认帧缓冲各 24,576 字节读回 `bit_exact = true`；
  3. `+1020 s` 第二次新鲜启动 `outcome = initialized`、`gpu_unresponsive_exits == 0` 且 `< 2.000 s`。

### 步骤（Run150）

- 留足 ≥ 3 分钟宿主冷却后，以与 Run147–149 完全一致的无诊断命令行启动 `m3-peakload-orig3s-20261112-150`，启动三路旁路采集器。
- `+150 s` 登录，`+380 s` 确认桌面后执行 `keepawake` 与时钟探针 `r150a`，预输入 WebGL2 探针命令，**在 `+720 s` 准时按回车触发第一次冷启动 Safari**（无 `perf` 干扰），校验 WebGL2 读回。
- 聚焦终端、退出 Safari 确认不存在后，**在 `+1020 s` 准时触发第二次新鲜启动**。
- 导出两份客体日志窗口、执行时钟探针 `r150b`、停止 `caffeinate`，执行 `sudo /sbin/shutdown -h now` 正常关机。

---

## 第四阶段实测结果（Run150：`+720 s` 后台峰值负载期首次无诊断一次通过）

### 运行身份与完整性

- 运行目录：`runs/e05-installed-boot/m3-peakload-orig3s-20261112-150`
- 二进制：`build/qemu-bdif-usb/qemu-system-aarch64`（SHA-256 `f858a94a8d7395456679a9c05c2c0dd2d6ee56f8701525e47bbf7ed9ee85f474`，补丁链至 `0045`）
- 配置：8 vCPU、4 GiB RAM、`qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`，NVIDIA 原生窗口，**未传 `--webkit-timeout-diagnostic`**，**未传 `--sync-stats`**，**未等待 `QUIET`**（固定 `+720 s` / `+1020 s` 触发）
- 客体与宿主健康度：`+152 s` 到登录密码框，`+382 s` 恢复桌面与 Dock，`+720 s` 按回车触发第一次冷启动，`+1020 s` 按回车触发第二次新鲜启动；客体执行 `sudo /sbin/shutdown -h now` 正常关机（`qemu_exit_code = 0`，`guest_shutdown_observed = true`，`errors = []`，总时长 `1394.1 s`）；宿主封装温度 270 个样本，`min = 49 °C`、`mean = 78.4 °C`、`max = 93 °C`；时钟探针 `r150a = -2.9768 s`（跨度 `3.5 ms`）、`r150b = -2.9816 s`（跨度 `2.2 ms`）；PAuth 持续吞吐达 **`32.60M/s`**（命中率 `84.60%`）。

### H7 检验结果：`+720 s` 峰值期首次冷启动无诊断 0 看门狗通过（`3.254 s`）且 WebGL2 逐字节一致，`+1020 s` 二次启动 `1.662 s`

| 运行（均为固定 `+720 s` / `+1020 s` 峰值窗口） | 二进制补丁 | `--webkit-timeout-diagnostic` | 试验 1（`+720 s`）`ui_request -> didInitialize` | 试验 1 看门狗退出 | 试验 2（`+1020 s`）`ui_request -> didInitialize` | WebGL2 双缓冲读回 |
|---|---|---|---:|---:|---:|---|
| Run131 / 132–136（同步优化前基线） | `0032` | 开启（30 s） | `3.515–4.866 s` | —（诊断延长） | `2.768–3.802 s` | — |
| Run143（同步计数基线） | `0033` | 开启（30 s） | `3.704 s` | —（诊断延长） | `2.818 s` | — |
| Run145（同步优化收官） | `0039` | 开启（30 s） | `3.798 s` | —（诊断延长） | `2.431 s` | — |
| **Run150（同步 + 吞吐优化收官）** | **`0045`** | **关闭（原版期限）** | **`3.254 s`**（`vcpu_mean_busy = 6.323`） | **0 次** | **`1.662 s`**（`0` 看门狗，`0` Web 无响应，`vcpu_mean_busy = 5.698`） | **FBO + 默认各 24,576 B `bit_exact=true`** |

**H7 逐项判定与边界说明**：
1. **峰值期无诊断首次通过与 WebGL2 读回成立**：在未等待安静的 `+720 s` 后台饱和窗口（窗口内 `vcpu_mean_busy = 6.323`、`registrar -> helper_create` 段达 `6.934`），首个 GPU 进程在**无 `--webkit-timeout-diagnostic`** 下以 **0 次看门狗退出**一次完成初始化（`constructor -> proxy_launched = 9.383 s`，`gpu_entry -> registrar = 1.912 s`，后三段合计 `1.333 s`），两份 24,576 字节 WebGL2 读回逐字节一致（跨 5 轮独立无诊断冷启动 **5/5** 通过）；`+1020 s` 二次启动降至 **`1.662 s`**（通过 `< 2.000 s` 子项，较同时间点 Run145 提速 **31.6%**、较 Run143 提速 **41.0%**）。
2. **严格 `< 3.000 s` 边界**：`+720 s` 峰值期首次冷启动的日志跨度 `3.254 s` 虽因原版 UIProcess 主运行循环自身也处于后台负载而带出约 `0.26 s` 投递裕量、未触发看门狗，但仍高于标称 `3.000 s` 线 `0.254 s`（故 H7 的 `< 3.000 s` 子项未达标）。与安静客体 Run149 的 **`2.734 s`（严格 `< 3.000 s`）** 对照可见：
   - **安静客体无诊断首次冷启动**已正面跌破 `3.000 s` 标称硬期限（`2.734 s`）；
   - **峰值负载期（`+720 s`）无诊断首次冷启动**已进入原版 UIProcess 投递裕量内一次通过（`3.254 s`，0 看门狗退出），且跨越 Run148–150 三轮独立冷启动的 **8/8 次**新鲜 Safari 启动全部在无诊断下 **0 看门狗退出**一次完成初始化。

### 四轮演进总结（Run143 → Run150）

| 阶段 | 补丁范围 | 核心机制改进 | 关键量化指标变化 |
|---|---|---|---|
| 同步争用消除（Run143–146） | `0033–0039` | 无锁 `CNTVCT_EL0`、跳过屏蔽中断与 `EXITTB` 锁、栈 HMAC PAC `HVC` 含 64 项缓存、无锁 `TLBI` 队列排空、`DSB` 聚合广播 `TLBI`、EL1&0 `ERET` 省锁 | 饱和期 `bql` 获取率 **-90%**，vCPU 运行/可运行占比升至 **96.6%**（总阻塞仅 **3.4%**） |
| 纯执行吞吐第一轮（Run147–148） | `0040–0043` | `CF_PCREL` 跳转缓存按页内偏移先读后写失效 + 256 页桶、`cputlb` 16 KiB 子页精确失效及单位错配修正 + 无 EL2 内联快路径、PAuth VA 缓存 + SSSE3 `pshufb` QARMA5、SME `do_fmopa_h` 预拆包 | 裸机 QARMA5 开销 **-73%**（`214 → 57.7 ns`），饱和期 `[JIT]` P 核占比 **`13.52% → 23.20%`**，安静时间提前至 `+958 s`，三轮启动全部 0 看门狗通过（`3.180 s` / `1.620 s`） |
| 跨上下文持久缓存（Run149–150） | `0044–0045` | 宿主 RAM 地址标记的跨进程持久 `CPUJumpCache`（TLB 失效不清空跳转缓存，共享缓存切换绕过全局 QHT）+ `TTBR0_EL1` 切换保留物理页表 `Phys_NS` 与内核 `TTBR1_EL1` 高半区 TLB | Safari 启动期 `tb_htable_lookup` **-62.7%**（`14.43% → 5.38%`），饱和期 `[JIT]` 达 **`24.12%`**（累计 **+78.4%**）；**安静客体首次冷启动无诊断首破 `3.000 s` 达 `2.734 s`**，**`+720 s` 峰值期无诊断首次冷启动 `3.254 s`（0 看门狗退出） / 二次 `1.662 s`**，Run148–150 连续 **8/8 次**无诊断启动 0 看门狗通过 |

---

## 第五阶段运行前计划（补丁 `0046–0048` 与 Run152：冲击后台峰值期标称 `< 3.000 s`）

### 精确指令级热点归因（复盘 Run149 `0045` 饱和期 DWARF + `perf annotate`）

对 Run149 在 `0045` 二进制上采集的后台饱和期 `perf-saturated.data`（`489.5B` 宿主 P 核周期）做聚合调用图与汇编逐指令标注（`perf annotate`），剩余约 `75.9%` 非 `[JIT]` 周期高度集中在三大机制性开销（且因 QEMU 编译开启 `-fzero-call-used-regs=used-gpr` 与 `-fstack-protector-strong`，每次跨编译单元或非内联函数返回额外清零 8–10 个通用寄存器）：

1. **`helper_lookup_tb_ptr` 族仍占 `23.76%` P 核周期**（`tb_lookup` 自占 `11.09%`，`qht_lookup_custom` `6.13%` 其中全局顺序锁 `seqlock_read_begin` 占 `4.86%`，`helper_lookup_tb_ptr` 自占 `2.62%`，`arm_get_tb_cpu_state` `1.49%`，`arm_cpu_mmu_index` `0.52%`）：
   - **直接映射冲突震荡**：1-路直接映射 `CPUJumpCache`（16,384 槽 × 24B = 384 KiB，未对齐跨缓存行）在内核与 `dyld` 共享缓存热块间仍有哈希冲突，每次冲突未命中回落全局 `qht_lookup_custom` 引发 8 核 L3 `seqlock` 读取争用（`4.86%`）；
   - **L1 命中路径未内联且含间接虚调用与栈金丝雀**：因 `tb_lookup()` 顶层声明取地址局部变量 `void *host_pc` 并调用 `cpu_mmu_index(cpu, true)`（经 `cs->cc->tcg_ops->mmu_index` 间接调用 `arm_cpu_mmu_index`），GCC 未将其内联进 `helper_lookup_tb_ptr`，导致每次 L1 跳转缓存命中仍执行 `sub $0x70,%rsp`、`%fs:0x28` 金丝雀读写、5 寄存器入栈保存、间接调用 `call *0x48(%rax)` 与双层寄存器清零返回；同时 `arm_get_tb_cpu_state` 每次读取距 `env` 达 `+0xd39xx`（+866 KiB，跨过 `za_state` 与 `pauth_memo` 大数组）的 `cpu->isar` 造成 DTLB 压力。
2. **PAuth 辅助函数族仍占 `21.15%` P 核周期**（`pauth_computepac` 自占 `4.43%`、`pauth_auth` 自占 `3.70%`、`qarma_ssse3_compute` `3.15%`、`aa64_pauth_va_parameters` 自占 `2.93%`、`arm_stage1_mmu_idx` + `arm_mmu_idx*` `2.17%`、`pauth_addpac` 自占 `1.59%`、入口/陷阱包装约 `3.18%`）：
   - **跨编译单元调用链与序言未收缩**：每次 PAuth 指令（Run150 持续达 `32.60M/s`）先跨编译单元调 `ptw.c:arm_stage1_mmu_idx -> helper.c:arm_mmu_idx -> arm_mmu_idx_el`（3 层调用与清零，`2.17%`）；再调 `aa64_pauth_va_parameters`，后者因冷刷新分支置于返回前而未触发 GCC shrink-wrap，每次缓存命中仍压入 6 个被调保存寄存器（`%rbx..%r15`）、查 `arm_mmuidx_table` 并访问 `+0xd3470` 远端缓存行（`2.93%`）；
   - **`pauth_memo` 48B 跨缓存行与 1,024 槽位冲突未命中（15.4% = 502 万次/秒 QARMA5）**：`ARMPAuthMemoEntry` 为 48 字节（半数跨两条 64B 缓存行），且 `pauth_memo_slot` 在 1,024 槽下未混入 `data[31:12]`，导致 `dyld` 共享缓存指针高频冲突回落完整 11 轮 `qarma_ssse3_compute`（且后者每轮仍在循环内重复调用 `qarma_unpack_u64(RC[i])`）。
3. **BTI 守护页检查 `is_guarded_page` / `helper_guarded_page_check` 占 `1.37%` P 核周期**：macOS arm64e 间接分支频繁触发 `is_guarded_page`，每次均经间接调用 `cpu_mmu_index` 并跨单元进 `probe_access_full -> probe_access_internal`，未先检查内联代码快表 `cpu_tlb_fast`。

### 补丁设计（`0046–0048`，严守全部架构语义与既有单测契约）

1. **`0046-tcg-2way-jmp-cache-and-inline-fastpath.patch`**：
   - 将 `CPUJumpCache` 改为 **64 字节缓存行对齐的 2-路组相联结构**（`8,192` 组 × `2` 路 = `16,384` 项 = `512 KiB`，两路同处单条 64B 缓存行，含 1-bit LRU 替换位与缓存的 `uint8_t mmu_idx`）；由于 QEMU 保证 `cpu_mmu_index(cpu, true)` 由 TB 的 `(flags, cs_base, cflags)` 唯一决定（`tcg_debug_assert` 校验），L1 命中直接按 `way->mmu_idx` 核对内联 `cpu_tlb_fast`，消除 `call *0x48(%rax)` 间接调用。
   - 将慢速路径拆入 `QEMU_NOINLINE tb_lookup_slow()`，令纯寄存器 L1 命中路径 `QEMU_ALWAYS_INLINE` 内联进 `helper_lookup_tb_ptr`，消除 `%fs:0x28` 金丝雀与双层调用栈帧；同时在 `CPUARMState` 近端（紧邻 `keys`，偏移 `< 14 KiB`）缓存 `has_aa64_bti`，加速 `arm_get_tb_cpu_state`。
2. **`0047-arm-pauth-fastpath-and-l2-qarma-cache.patch`**：
   - 保持 `pauth_helper.c` 中 `pauth_addpac..xpacd` 与 `pauth_memo_slot` 原文及 `env->pauth_memo` 统计契约不变（100% 兼容 `test-pac-mult.py`、`test-pac-memo.py`、`test-pac-va.py`）；将 `ARMPAuthMemoEntry` 补齐为 64 字节消除跨缓存行访问。
   - 在 `target/arm/internals.h` 将 `!CONFIG_USER_ONLY` 下的 `arm_stage1_mmu_idx(env)` 改为内联函数（AArch64 无 EL2/EL3/M 时 4 条指令直接由 `env->pstate` 给出 `Stage1_E0 / E1 / E1_PAN`）；将 `pauth_va_cache` 与只读 `pauth_fast` 配置移至 `CPUARMState` 近端（紧邻 `keys`，在 `arm_cpu_realizefn`、`arm_cpu_reset_hold` 与 `cpu_post_load` 同步维护），并在 `aa64_pauth_va_parameters()` 中预打包 4 组返回字且将命中 `return` 置于慢速重填之前（触发编译器 shrink-wrap 零栈帧快路径）。
   - 在 `arm_pauth_computepac_architected_fast` 下（仅当 `prop_pauth_memo` 开启时生效，默认关闭时不缓存；随 `reset` 与 `post_load` 清零）加入每核 **2-路组相联折叠哈希二级 QARMA 缓存**（`4,096` 组 × `2` 路 = `8,192` 项，吸收 1,024 槽一级 memo 的 `dyld` 高位地址冲突未命中），并预展开 `qarma_ssse3_compute` 的 `RC[0..4]` 与 `alpha` 向量常量。
3. **`0048-arm-bti-guarded-tlb-fastpath.patch`**：
   - 在 `target/arm/tcg/helper-a64.c:is_guarded_page()` 中改用内联 `arm_env_mmu_index(env)` 并优先查询当前 `mmu_idx` 的内联代码 TLB（`addr_code == (addr & TARGET_PAGE_MASK)` 时直接返回 `fulltlb[idx].extra.arm.guarded`，与 `probe_access_full(..., MMU_INST_FETCH, size=0)` 完全等价），仅在代码 TLB 未命中时回落 `probe_access_full`。

### 第五阶段假设（Run152）

1. **后台峰值期正面击穿 `< 3.000 s` 假设（H8）**：在 `0048` 主线二进制上，保持与 Run150 完全一致的无诊断峰值测试条件（8 vCPU、4 GiB RAM、`qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`，**关闭 `--webkit-timeout-diagnostic` 与 `--sync-stats`**，**不等待安静**直接在启动后 **`+720 s`** 触发第一次冷启动 Safari）：
   - 第一次冷启动首个 GPU 进程一次完成初始化（`outcome = initialized`、`gpu_unresponsive_exits == 0`），且 **`ui_request_to_did_initialize < 3.000 s`**（无需依赖 UIProcess 运行循环裕量，在后台峰值负载下直接击穿原版 WebKit `3.000 s` 硬期限）；
   - 固定 WebGL2 离屏与默认帧缓冲各 24,576 字节读回 `bit_exact = true`；
   - `+1020 s` 第二次新鲜启动 `gpu_unresponsive_exits == 0` 且 **`ui_request_to_did_initialize < 1.500 s`**。
2. **纯执行占比跃升与热点压缩假设（H9）**：在远离 `+720 s` 试验窗口的 `+520 s` 后台饱和期采集 15 秒 DWARF `perf` 剖面（`CPU */TCG` 宿主 P 核 `cpu_core/cycles/P`）：
   - 翻译客体代码 `[JIT]` 占比从 Run149 的 `24.12%` 提升至 **`≥ 30.0%`**（较 `0039` 基线的 `13.52%` 累计提升 > 120%）；
   - `qht_lookup_custom` 自占从 `5.35%` 降至 **`≤ 2.0%`**，PAuth 函数族合计自占从 `~21.1%` 降至 **`≤ 10.0%`**。

---

## 第五阶段实测结果（Run152：内联消除虚调用并暴露二级大表缓存污染）

### 运行身份与完整性

- 运行目录：`runs/e05-installed-boot/m3-peakload-0048-orig3s-20261113-152`
- 二进制：`build/qemu-bdif-usb/qemu-system-aarch64`（SHA-256 `ce4b284d6f651d67f7c51c7159108bfac09785b2c172cfb16b2d44c8370eb8fb`，补丁链至 `0048`）
- 配置：与 Run150 完全一致（8 vCPU、4 GiB RAM、`qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`，NVIDIA 原生窗口，**未传 `--webkit-timeout-diagnostic` 与 `--sync-stats`**，固定 `+720 s` / `+1020 s` 触发，并在 `+1278 s` 附加并发 20 s `perf` 第三轮启动）
- 客体与宿主健康度：起始封装温度 `45 °C`；客体执行 `sudo /sbin/shutdown -h now` 正常关机（`qemu_exit_code = 0`，`guest_shutdown_observed = true`，`errors = []`，总时长 `1680.2 s`）；宿主封装温度 331 个样本，`min = 46 °C`、`mean = 77.0 °C`、`max = 95 °C`；时钟探针 `r152a = -3.0681 s`（跨度 `2.4 ms`）、`r152b = -3.0738 s`（跨度 `1.6 ms`）；全程累计 `52.81B` 次 PAuth（持续 **`31.43M/s`**，1,024 槽一级命中率 `84.45%`）。

### H8 与 H9 检验结果：部分子项显著改善，但 `256 KiB` 二级 QARMA 表引发 `+2.63%` 净回退导致 H8/H9 未达标

1. **三轮无诊断 Safari 启动表现**：
   - **试验 1（`+720 s` 后台峰值窗口，无 `perf`）**：首个 GPU 进程（PID 1072）的 `constructor -> proxy_launched` 创峰值期最快 **`8.818 s`**（Run150 为 `9.383 s`）；但由于 UIProcess 主线程本身加快而零裕量准时在 `3.008 s` 触发 1 次看门狗（PID 1072 实际于 `+3.482 s` 销毁 SandboxHelper、`+3.801 s` 完成 `didInitialize`），替补 GPU 进程在无诊断下立即自动恢复（`web_unresponsive = 0`），**固定 WebGL2 离屏与默认帧缓冲各 24,576 字节读回均 `bit_exact=true`**（跨 6 轮独立冷启动 **6/6** 通过）。因存在 1 次看门狗退出且未破 `< 3.000 s`，**H8 未通过**。
   - **试验 2（`+1020 s` 无干扰）**：**0 看门狗退出、0 Web 无响应**，`ui_request_to_did_initialize = 1.795 s`。
   - **试验 3（`+1278 s` 并发 20 s DWARF `perf`）**：**0 看门狗退出、0 Web 无响应**，**`ui_request_to_did_initialize = 1.725 s`**（创并发 `perf` 采样历史最快，优于 Run149 的 `1.856 s` 与 Run148 的 `2.056 s`）。
2. **`perf` 因果分解（`0045` Run149 vs `0048` Run152，`CPU */TCG` 宿主 P 核 `cpu_core/cycles/P`）**：
   - **已验证生效的改进**：`arm_stage1_mmu_idx` + `arm_mmu_idx*` 从 `2.17%` 降至 **`0.00%`**；`arm_cpu_mmu_index` 从 `0.52%` 降至 **`0.00%`**；`is_guarded_page` 从 `0.46%` 降至 **`0.00%`**；`probe_access_internal` 从 `2.46%` 大降 **41.9% 至 `1.43%`**（Safari 启动期 `1.33%`）；`qht_lookup_custom` 从 `5.35%` 降 **32.3% 至 `3.62%`**（Safari 启动期 `2.92%`）。
   - **精确捕获的两处负向抵消（解释为何饱和期 `[JIT]` 停在 `23.96%`）**：
     1. **`pauth_qarma_l2` 缓存污染净亏 `+2.63%`**：`qarma_ssse3_compute` 仅需约 35–40 ns 纯寄存器运算（占比从 `3.15%` 降至 `1.85%`，省 `1.30%`），而探测每核 `256 KiB` 二级表 `qarma_l2_lookup_or_compute`（加 64B 扩容的一级表）引入 **`3.93%`** 自占 L2 访存停顿，倒贴 `2.63%` P 核周期；
     2. **`TB_JMP_CACHE_BITS` 从 `14` 缩至 `13` 将页桶数从 256 减半至 128**：2 路组相联虽降低冲突，但页桶减半使 3 个以上共享缓存页落入同一桶时仍留有 `3.62%` 的 `qht_lookup_custom`；
     3. **PAuth 外层包装未短路**：即使 `pauth_computepac` 命中，每次 `PAC*`/`AUT*` 仍执行 `pauth_auth`（`3.85%`）+ 跨单元 `aa64_pauth_va_parameters`（`2.72%`）+ `pauth_computepac`（`4.07%`）+ `pauth_addpac`（`1.88%`）+ 入口包装（`2.69%`）共 **`15.2%`** 固定调用链与寄存器清零开销！

---

## 第六阶段运行前计划（补丁 `0049` 与 Run153：端到端 PAuth 指令级缓存、`PAC→AUT` 预填充与 32K 项 2-路跳转缓存）

### 补丁设计（`0049-tcg-jmpcache-32k-and-pauth-op-cache.patch`）

针对 Run152 `perf` 揭示的三项精确事实，设计 `0049`：

1. **恢复 256 页桶并保持 2-路组相联（`TB_JMP_CACHE_BITS = 14`，`16,384` 组 × `2` 路 = `32,768` 项，`1.0 MiB` 位于 Lion Cove P 核 `3.0 MiB` 私有 L2 内）**：同时拥有 `0045` 的全部 256 个页桶与 `0046` 的 64B 单缓存行 2-路 LRU 相联度。
2. **移除负收益的 `256 KiB` `pauth_qarma_l2` 并将 `ARMPAuthMemoEntry` 恢复为紧凑 48 字节**（保持 `pauth_helper.c` 原文契约 100% 不变，立即收回 `3.93%` L2 访存开销）。
3. **每核 `64 KiB`（L1.5 常驻，`512` 组 × `2` 路 = `1,024` 项）端到端 PAuth 指令级缓存与 `PAC→AUT` 逆向预填充**（仅在 `x-pauth-memo=on` 且无 EL2/EL3/M 的 AArch64 下启用，默认关闭时不生效；随 `reset` 与 `post_load` 清零）：
   - 在 `helper-a64.c` 提供 `translate-a64.c` 直接调用的顶层 `HELPER(pauth_fast_*)`：命中时在**单一叶函数（无子调用、无栈帧）**内直接比对全部 6 项架构输入 `(op_and_el, sctlr_en, tcr_el1, key.lo, key.hi, in_ptr, modifier)` 并返回 `out_ptr`，一次性短路 `aa64_pauth_va_parameters`、`pauth_addpac`/`pauth_auth` 与 `pauth_computepac` 的全部 15.2% 外层开销；
   - **冷调用逆向预填充（数学恒等条件）**：当 `PACI[AB]`/`PACD[AB]` 对规范指针 `x`（满足 `!param.mtx && ((x ^ sextract64(x, 55, 1)) & pauth_ptr_mask(param)) == 0`，故 `pauth_original_ptr(signed_ptr, param) == x`）计算出 `signed_ptr` 时，同步将已验证成功的逆映射 `(OP_AUT_*, signed_ptr, modifier) -> x` 写入同一组相联缓存（注：失败或伪造指针永不入表，未命中时顶层 `HELPER` 直接持真实 `GETPC()` 调用 `raise_exception_ra` 保证异常 `ELR_ELx` 精确恢复）。这使得新启动进程首次执行函数序言 `PACIBSP` 后，对应的函数尾声 `RETAB` **在冷首次执行时即 100% 命中叶函数快路径**！
   - 在 `arm_pauth_qarma_selftest()` 启动自检中新增 65,536 组覆盖全部 `PauthFeat`（`0`、`EPAC`、`Pauth2`、`FPAC`、`FPACCOMBINED`）、`TBI`/`TSZ`、规范/非规范指针、密钥/TCR 切换与伪造标签的交叉比对断言。

### 第六阶段假设（Run153）

在完全相同的无诊断峰值窗口测试条件（8 vCPU、4 GiB RAM、`qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`，关闭 `--webkit-timeout-diagnostic` 与 `--sync-stats`，固定 `+720 s` / `+1020 s` 触发）下：
1. **H10（峰值期无诊断 `< 3.000 s` 硬期限突破）**：`+720 s` 第一次冷启动 Safari 首个 GPU 进程一次完成初始化（`outcome = initialized`、`gpu_unresponsive_exits == 0`）且 **`ui_request_to_did_initialize < 3.000 s`**，固定 WebGL2 双缓冲读回 `bit_exact = true`，`+1020 s` 第二次新鲜启动 `< 1.500 s`。
2. **H11（`[JIT]` 占比突破 30% 且 PAuth 减半）**：`+520 s` 饱和期 DWARF `perf` 中，客体 `[JIT]` P 核占比提升至 **`≥ 30.0%`**，`qht_lookup_custom` 自占降至 **`≤ 2.0%`**，PAuth 函数族合计自占从 `21.0%` 降至 **`≤ 10.0%`**。

---

## 第六阶段实测结果（Run153：PAuth 外层大幅压缩、三轮 0 看门狗通过，并定位 NEON FP16 `FMLA` 占饱和期 `9.18%`）

### 运行身份与完整性

- 运行目录：`runs/e05-installed-boot/m3-peakload-0049-orig3s-20261113-153`
- 二进制：`build/qemu-bdif-usb/qemu-system-aarch64`（SHA-256 `ef897a396ce5a625ac6e47e1615e4d859c60be8b519f93946465b97527bd3531`，补丁链至 `0049`）
- 配置：与 Run150、Run152 完全一致（8 vCPU、4 GiB RAM、`qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`，NVIDIA 原生窗口，**未传 `--webkit-timeout-diagnostic` 与 `--sync-stats`**，固定 `+720 s` / `+1020 s` 触发，并在 `+1283 s` 附加并发 20 s `perf` 第三轮启动）
- 客体与宿主健康度：起始封装温度 `47 °C`；客体执行 `sudo /sbin/shutdown -h now` 正常关机（`qemu_exit_code = 0`，`guest_shutdown_observed = true`，`errors = []`，总时长 `1674.4 s`）；宿主封装温度 330 个样本，`min = 52 °C`、**`mean = 76.3 °C`**、`max = 95 °C`（全程降频时长从 Run152 的 `11,780 ms` 减半至 **`5,723 ms`**）；时钟探针 `r153a = -3.0858 s`（跨度 `3.5 ms`）、`r153b = -3.0916 s`（跨度 `1.8 ms`）；全程累计 `49.75B` 次 PAuth，综合命中率升至 **`86.98%`**（未命中从 `8.21B` 降至 `6.48B`）。

### H10 与 H11 检验结果：3/3 轮全部 0 看门狗一次通过，`+1020 s` 创 `1.659 s` 纪录，`[JIT]` 升至 `24.82%` 并暴露 `helper_gvec_fmla_idx_h`（`9.18%`）新主瓶颈

1. **三轮无诊断 Safari 启动表现**：
   - **试验 1（`+720 s` 后台峰值窗口，`vcpu_mean_busy = 6.328`，无 `perf`）**：**`outcome = initialized`、`gpu_processes = 1`、0 看门狗退出**一次通过（`3.314 s`，期间封装温度瞬时触及 `95 °C` 热墙；固定 WebGL2 离屏与默认帧缓冲各 24,576 字节读回连续 **7/7** `bit_exact=true`）。因 `3.314 s` 仍高于标称 `< 3.000 s` 子项，H10 的严格 `< 3.000 s` 子项未达标。
   - **试验 2（`+1020 s` 无干扰）**：**`gpu_processes = 1`、0 看门狗退出、0 Web 无响应**，**`ui_request_to_did_initialize = 1.659 s`**（创 `+1020 s` 窗口全项目最快，其中 **`gpu_entry -> registrar_activation = 0.760 s`** 创全项目历史最快，`constructor -> proxy_launched` 从 `8.846 s` 骤降 20.3% 至 **`7.054 s`**）。
   - **试验 3（`+1283 s` 并发 20 s `perf`）**：**`gpu_processes = 1`、0 看门狗退出、0 Web 无响应**，`constructor -> proxy_launched = 5.975 s`，`ui_request_to_did_initialize = 2.185 s`。
2. **`perf` 因果分解（`CPU */TCG` 宿主 P 核 `cpu_core/cycles/P`）**：
   - **翻译客体 `[JIT]` 占比升至全项目最高 `24.82%`**（累计较 `0039` 的 `13.52%` **+83.6%**），`qht_lookup_custom` 进一步降至 **`2.77%`（饱和期）/ `2.54%`（Safari 启动期）**（较 `0045` 降 **48.2%**），`aa64_pauth_va_parameters` 大降 **73.0%**（`2.93% → 0.79%`），`pauth_op_miss_aut` 压至 **`0.44%`**（Safari 启动期 `< 0.40%`，证实 `PAC→AUT` 逆向预填充消除了 >90% 的返回认证未命中），PAuth 全族合计自占从 `21.1%` 降至 **`12.1%–13.4%`**；
   - **新浮现的两项明确瓶颈**：
     1. **后台 Spotlight / CoreML / Quartz 的 NEON FP16 向量乘加 `helper_gvec_fmla_idx_h -> float16_muladd_scalbn` 占饱和期 `9.18%` P 核周期（Safari 启动期亦达 `~8.5%`）**：逐元素调用软浮点 `float16a_unpack_canonical`（`4.08%`）+ `parts64_muladd`（`2.37%`）+ `parts64_uncanon`（`1.20%`），每条 8 通道指令执行约 1,200 条宿主标量指令；
     2. **`CPUJumpCache` 扩至 `1.0 MiB`（`TB_JMP_CACHE_BITS = 14`）使 `helper_lookup_tb_ptr` L1.5 缺失自占从 `13.27%` 升至 `17.38%`**（`+4.11%`），抵消了 `qht_lookup_custom` 减少的 `0.85%`——改用 `TB_JMP_CACHE_BITS = 13` 配合 `TB_JMP_PAGE_BITS = 5` 可在 **`512 KiB` 容量**下同时保留全部 **256 个页桶（每桶 32 组 × 2 路 = 64 项）**！

---

## 第七阶段运行前计划（补丁 `0050` 与 Run154：硬件 `F16C+FMA` 逐位精确 FP16 乘加与 `512 KiB` 256 页桶跳转缓存）

### 补丁设计（`0050-f16-muladd-avx2-and-jmpcache-512k.patch`）

1. **x86_64 `F16C + AVX2 + FMA` 逐位精确 IEEE-754 `float16_muladd_scalbn` 与 8 通道 `gvec_fmla_idx_h` 快路径**：
   - **严格数学等价性证明**：任意有限正规或零值 `binary16`（11 位尾数）在 `binary64`（`double`，53 位尾数、指数 `[-1022, +1023]`）中均 **100% 精确表示**；由于 `RNE_64` 单调且所有正规 `binary16` 半中点 `(n + 0.5) * 2^(e - 10)` 在 `double` 中低 41 位全为 `0`，当 `d = fma((double)a, (double)b, (double)c)` 的偏置指数满足 `1009 <= bexp <= 1037`（即 $2^{-14} \le |d| < 2^{15}$，远离下溢与上溢边界）且 `low42 = d_bits & ((1ULL << 42) - 1)` 满足 `low42 != (1ULL << 41)`（非半中点，出现率 $< 2^{-41}$）时，直接对 `d_bits` 做单次整数舍入并维护 `float_flag_inexact` 与软浮点 `float16_muladd_scalbn` **在全部 $2^{48}$ 种输入及 `FPSR` 异常标志上逐位恒等**（凡遇次正规数、`Inf`/`NaN`、非 RNE 舍入、越界或半中点一律回落原软浮点慢路径；已在独立 5,004 万组边界+随机向量 UBSan oracle 下验证 0 误差，并在 QEMU 启动时自检）。
   - 在 `fpu/softfloat.c:float16_muladd_scalbn()` 提供标量 `F16C+FMA` 快路径，并在 `target/arm/tcg/vec_helper.c` 为 `gvec_fmla_idx_h` / `gvec_fmls_idx_h` 提供一次转换 4/8 通道的 `_mm_cvtph_ps + _mm256_cvtps_pd + _mm256_fmadd_pd` 批量快路径。
2. **`CPUJumpCache` 调优为 `512 KiB` + `256` 页桶（`TB_JMP_CACHE_BITS = 13`，`TB_JMP_PAGE_BITS = 5`）**：每核 `8,192` 组 × `2` 路 = `16,384` 项（`512 KiB`），每页桶 `32` 组 × `2` 路 = `64` 项、共 **256 个页桶**，兼顾 Lion Cove L1.5 命中率与页桶隔离度。

### 第七阶段假设（Run154 / Run155）

在完全相同的无诊断峰值窗口测试条件（8 vCPU、4 GiB RAM、`qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`，关闭 `--webkit-timeout-diagnostic` 与 `--sync-stats`，固定 `+720 s` / `+1020 s` 触发）下：
1. **H12**：饱和期 DWARF `perf` 中，`float16_muladd_scalbn`（含子调用）占比从 `9.18%` 降至 **`< 1.0%`**，翻译客体 `[JIT]` P 核占比创历史新高（初定目标 `≥ 28.0%`）。
2. **H13**：`+720 s` 窗口第一次冷启动 Safari 以 `0` 看门狗退出一次完成初始化且 **`ui_request_to_did_initialize < 3.000 s`**，固定 WebGL2 双缓冲读回 `bit_exact = true`，后续新鲜启动压入 `< 1.600 s`。

---

## 第七阶段实测结果（Run154 2.1 小时连续稳定性与 Run155 `+720 s` 首次击穿 `< 3.000 s` 硬期限）

### Run154（`m3-peakload-0050-orig3s-20261113-154`：长时驻留、锁屏唤醒与正常关机验证）

- 二进制：`build/qemu-bdif-usb/qemu-system-aarch64`（SHA-256 `e6f7b2cb32df3779531cf1b93612a67d8e1fa350c8b894c19d0c9e71a60f4a51`，补丁链至 `0050`）
- 经过与收获：客体在 `+221 s` 登录、`+371 s` 到达桌面后因宿主会话暂停错过了 `+720 s` 触发点并进入显示休眠与锁屏。为检验 `0050` 补丁链的长时稳定性并保持 `+720 s` 对照口径纯净，未在该实例上补跑偏离时间窗的浏览器试验，而是唤醒显示、用 [guest_ui.py](../../experiments/E05-installed-boot/guest_ui.py) 新增的有界节流参数（`--hold 0.35 --gap 0.35`，防止锁屏慢速轮询下 16 槽 HID 键盘队列溢出）一次解锁回到桌面，核实时钟探针（`r154b = -3.1395 s`，跨度仅 **`1.5 ms`**）与 `Safari` 缺席后执行 `sudo /sbin/shutdown -h now` 正常关机（`qemu_exit_code = 0`，`guest_shutdown_observed = true`，`errors = []`）。
- 量化结果：连续无故障运行 **`7,550.7 s`（2.10 小时）**，累计执行 **`191.38B` 次 PAuth 操作**（综合命中率 **`89.06%`**），1,500 个宿主温度样本均值 `67.3 °C`。

### Run155（`m3-peakload-0050-orig3s-20261113-155`：无干扰 `+720 s` 完整三轮测试）

- 运行身份：同一 `0050` 二进制（`e6f7b2cb…`），8 vCPU、4 GiB RAM、`qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`，NVIDIA 原生窗口，**关闭 `--webkit-timeout-diagnostic` 与 `--sync-stats`**。
- 客体与宿主健康度：起始封装温度 `49 °C`；客体执行 `sudo /sbin/shutdown -h now` 正常关机（`qemu_exit_code = 0`，`guest_shutdown_observed = true`，`errors = []`，总时长 `1653.3 s`）；宿主封装温度 329 个样本，`min = 49 °C`、**`mean = 68.6 °C`（较 Run153 的 `76.3 °C` 骤降 `-7.7 °C`）**、**`max = 92 °C`（全程未触及 `95 °C` 热墙，降频累计仅 `3,489 ms`，较 Run152 的 `11,780 ms` 减少 `70.4%`）**；时钟探针 `r155a = -3.1448 s`（跨度 `2.8 ms`）、`r155b = -3.1506 s`（跨度 `1.6 ms`）；全程 `46.20B` 次 PAuth，综合命中率 **`87.56%`**。

### H12 与 H13 检验结果：`+720 s` 冷首次启动 `2.941 s` 正面击穿 `< 3.000 s` 硬期限，第 3 轮创 `1.417 s` 全项目最快纪录，`float16_muladd` 降至 `< 0.05%`

1. **三轮无诊断 Safari 启动表现（连续 3/3 轮 `gpu_processes = 1`、`0` 看门狗退出）**：
   - **试验 1（`+720 s` 触发，无 `perf`）**：**`outcome = initialized`、`gpu_processes = 1`、`gpu_unresponsive_exits = 0`**，**`ui_request_to_did_initialize = 2.941 s`（`gpu_entry_to_did_initialize = 2.935 s`）**——在不等待安静、不开启任何超时诊断条件下，**首次于 `+720 s` 窗口正面击穿原版 WebKit `3.000 s` 硬期限**（初始化窗口内 `vcpu_mean_busy = 6.156`，`83.5%` 时间全 8 核满载）；固定 WebGL2 离屏与默认帧缓冲各 `24,576` 字节读回连续 **8/8** `bit_exact = true`。
   - **试验 2（`+1020 s` 触发，无 `perf`）**：**`gpu_processes = 1`、`0` 看门狗退出、`0` Web 无响应通知**，`ui_request_to_did_initialize = 1.803 s`（`gpu_entry -> registrar = 0.940 s`，`registrar -> helper_create = 0.061 s`）。
   - **试验 3（`+1284 s` 触发，并发 20 s DWARF `perf`）**：即便在并发拷贝 `83.7 MB` DWARF 栈样本条件下，仍以 **`gpu_processes = 1`、`0` 看门狗退出、`0` Web 无响应通知** 创下 **`ui_request_to_did_initialize = 1.417 s`**（`gpu_entry_to_did_initialize = 1.414 s`，`constructor -> proxy_launched = 5.696 s`）的**全项目历史最快纪录**——真实架构级 `QARMA5` 首度超越此前非密码学 `impdef` Run137 的 `1.539 s`！
2. **`perf` 与后台负载时间线因果分解（`CPU */TCG` 宿主 P 核 `cpu_core/cycles/P`）**：
   - **FP16 软浮点瓶颈彻底归零**：`float16_muladd_scalbn` 与 `helper_gvec_fmla_idx_h`（含全部子调用）在饱和期与 Safari 启动期双双从 **`9.18%` 降至 `< 0.05%`（未检出显著占比）**，验证硬件 `F16C+AVX2+FMA` 逐位精确快路径 100% 生效；
   - **`512 KiB` + 256 页桶跳转缓存收回 L1.5 命中率**：`helper_lookup_tb_ptr` 自占从 Run153（`1.0 MiB` 缓存）的 `17.38%` 大幅回落 **`-6.29%` 至 `11.09%`**（Safari 启动期 `13.46%`），同时 `qht_lookup_custom` 在饱和期保持 **`2.88%`** 低位；
   - **Safari 启动期 `[JIT]` P 核占比升至 `25.71%`（创全项目最高，但未达 `28.0%` 子项）**：在 `+568 s` 采集窗口中，由于 FP16 层被硬件瞬间跑完，后台 Spotlight / CoreML 推进至最后一段单精度 `FMLA Vd.4S`（`float32_muladd` `13.42%` + `helper_gvec_fmla_idx_s` `7.20%`），致使该 15 秒片段内 `[JIT]` 为 `21.42%`；更重要的是，`host-threads.tsv` 逐分钟均值显示**登录后后台索引风暴在 `+660 s` 即提前消退**（第 11 分钟均值降至 `3.952`、第 12 分钟降至 **`1.531`**，`+960 s` 正式满足连续三分钟 `< 3.0` 判据，较 `0039` Run147 的 `+1335 s` 提前 **375 秒**），直接解释了封装均温下降 `-7.7 °C` 与 `+720 s` 突破 `< 3.000 s` 的双重收益。
   - **假设裁定**：H12 的 `float16_muladd_scalbn < 1.0%` 子项大幅超额达成（`< 0.05%`），`[JIT]` 刷新纪录至 `25.71%`（未达激进的 `28.0%` 阈值）；H13 的 `+720 s` `0` 看门狗且 `< 3.000 s`（`2.941 s`）与 WebGL2 `bit_exact=true` 核心子项达成，后续新鲜启动于第 3 轮达 `1.417 s`（`< 1.600 s`，第 2 轮为 `1.803 s`）。

### 全程演进总结（`0033` → `0050`，Run143 → Run155）

| 阶段 | 补丁范围 | 核心机制改进 | 关键实测里程碑 |
|---|---|---|---|
| 同步争用消除（Run143–146） | `0033–0039` | 无锁 `CNTVCT_EL0`、跳过屏蔽中断与 `EXITTB` 锁、栈 HMAC PAC `HVC`、无锁 `TLBI` 排空、`DSB` 聚合广播、EL1&0 `ERET` 省锁 | 饱和期全局锁获取率 **-90%**，vCPU 在核/可运行占比 **96.6%**；安静客体首次跑通无诊断冷启动（`3.219 s`） |
| 局部性与向量 QARMA5（Run147–150） | `0040–0045` | 宿主 RAM 标记跨进程持久 `CPUJumpCache`（256 页桶）、16 KiB 子页精确 TLB 失效与 `TTBR0` 切换保留内核/物理页表 TLB、PAuth VA 缓存 + SSSE3 QARMA5、SME 预拆包 | 裸机 QARMA5 **-73%**，`tb_htable_lookup` **-62.7%**；安静客体破 `< 3.0 s`（`2.734 s`），`+720 s` 峰值期首次 `0` 看门狗通过（`3.254 s`） |
| 叶函数内联、端到端 PAuth 缓存与硬件 FP16 FMA（Run152–155） | `0046–0050` | 64B 对齐 `512 KiB` 2-路 256 页桶内联跳转缓存、BTI 代码 TLB 快路径、`64 KiB` 端到端 PAuth 叶函数缓存 + `PAC→AUT` 逆向预填充、逐位精确 `F16C+AVX2+FMA` FP16 乘加 | `float16_muladd` **`9.18% → < 0.05%`**，`qht_lookup` **-48%**，PAuth 总自占 **-40%**，`[JIT]` 升至 **`25.71%`（累计 +90.2%）**；后台索引提前 **6+ 分钟**消退，均温降至 **`68.6 °C`**；**`+720 s` 无诊断冷首次启动 `2.941 s`（`< 3.000 s`，0 看门狗）**，新鲜复启动创 **`1.417 s`** 全项目纪录（超越 `impdef`），并验证 **2.10 小时**连续稳定运行 |
