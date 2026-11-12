> **Language:** [English](MASTER-PLAN.en.md) | **简体中文**

# 本地 macOS 兼容执行平台：完整产品与研发总计划 v3

**对齐日期：2026-10-14（UTC）｜第一宿主：现有 Linux 笔记本｜后续：实测后纳入的台式机。**

**目标不变：在本机运行真正的现代 macOS 内核、用户态、桌面与应用；长期逼近原生操作系统的交互、吞吐、设备体验和资源效率。没有交付期限，不承诺成功概率。**

本文件是总计划入口，替代 v2 作为后续规划基准。当前完成状态、测试样本和阻塞项统一记录在 `PLAN-STATE.json`，`STATUS.md` 是其可读视图；已验证成果及证据汇总在 `RESULTS.md`；`reports/` 下是有日期的逐项实验报告（含历史快照 `reports/M0-RESULTS.md`、`reports/M1-ARM-REIMS.md`），不是另一份现行总计划。v2 完整原文与证据文件仍保留在本地历史归档中，本次不改写历史文件。

## 0. v3 制定时的对齐结论（历史）

以下保留制定时的决策背景，当前完成状态以[STATUS.md](STATUS.md)为准。

1. **产品目标与 M0—M8 不缩水、不重新编号。** 目前 M0 建成了现有宿主上的实验基线，但尚未在空白第二环境验证重建；M1 正在进行，单核timer/GIC及16KiB MMU最小路径已通过，Apple HVC/PAuth、多核地址空间等仍有缺口。不得把这几轮图形实验写成 M2/M4 已通过。
2. **集成主线正式选用现代 Reims QEMU 的 ARM64/TCG 移植工作树。** 原 Portable QEMU 和无 GPU VMApple 是对照与定位工具，不再要求先把老式 Reims Cargo ABI 修好再继续。
3. **E11 是新增的跨架构图形契约实验，不代替 E01—E10。** ARM64 客体 16 KiB APV 页与 Linux 宿主 4 KiB VM 页必须分开；不同层的地址、页号、长度、对齐不能全局替换。v3继续执行已完成host-pointer对齐修复和guest设备查询回写；具体结果见状态表。
4. **构建、设备实例化、寄存器读写、FIFO 创建、命令执行、真正渲染、系统 GUI 分层验收。** 已有前四至五层的局部成功不能覆盖后续未测层。
5. **测得的单线程/并行测试失败分别记录。** 不把共享日志干扰算成同数目的产品缺陷，也不把全部失败归类为测试问题。当前结果以实际日志和源码版本为准。
6. **补齐已经实施但遗漏持久化的补丁、构建步骤及实验入口。** 能复现的修改进入整合仓库；构建输出、系统镜像和运行转储不进入 Git。

## 1. 产品定义与范围

交付对象不是主题、远程 Mac 客户端、Darling 式部分应用兼容层，也不是把旧系统换上新版界面。要求辨识并记录真实客体 kernel、用户态、dyld cache、系统 build 与所用虚拟平台。可以有透明的兼容补丁，不将修改后行为冒充原始行为。

两种形态共用同一内核：研究工作站模式在现有 Ubuntu 桌面里运行，便于调试；独立会话模式在核心成熟后启动到专用 macOS 会话，由 Linux 负责硬件与进程承载。**不把裸机执行设为原生体验的必要条件，也不先开发 Linux 发行版。**

五个互不替代的验收维度：交互响应、实际工作负载性能、语义正确性/功能覆盖、设备与生命周期、资源效率。可逐项达标；不得用宿主鼠标流畅、重复帧、空闲桌面、跳过工作或未说明的低精度替代。

台式机具体配置尚未确认，不把其他已连接设备自动当作第二宿主。Windows/Linux 是体验参照，不意味着同时支持 Windows 宿主；当前 Linux 是唯一主宿主。

## 2. 事实、目标版本与证据口径

第一宿主既有调查为 Core Ultra 7 255HX（20 核/线程）、约 30 GiB RAM、Intel ARL 核显和 RTX 5070 Ti Laptop（约 12 GiB VRAM）。Ubuntu 26.04.1、内核 7.0.0-38；内屏 2560×1600、240 Hz，连接 Intel。宿主软件、驱动、磁盘余量等可变字段以最近检查时间为准，不把旧值写成永久事实。

初始规划采用“macOS 27 ARM”为目标版本线、一个必要 ARM 对照及可选 macOS 26 x86 对照。v2 的系统名称/版本表是 **2026-10-13 的历史外部调查**，不是本项目已经取得的系统制品。Apple目录及IPSW内BuildManifest/Restore.plist已确认VirtualMac2,1的27.0.1（26A434）为精确研究候选。原始材料的获取、签名恢复及实际执行证据统一维护在 [RESULTS.md](RESULTS.md)；完整bundle、AUX、身份/硬件模型和已安装磁盘的当前状态以 [STATUS.md](STATUS.md) 为准。取得组件、进入固件Recovery与完成系统恢复分别验收。

证据仅分四类：O=直接运行/读取，D=固定源码或文档静态事实，R=他人报告尚未复现，H=假设/设计目标。多个项目共享同一 fork 不自动成为独立验证。原始日志、源文件、精确 build 和实验命令支持具体结论，不以项目名或声誉代替。

### 本机已建立的最小基线

- ARM64 TCG 自制串口载荷；VMApple 配置读取；PSCI CPU_ON 及第二 vCPU RAM 通知。
- Intel/NVIDIA 各自 Vulkan compute 和 64×64 离屏 triangle/readback。
- 现代 Reims C/Vulkan 库、分发产物、x86 QEMU PCI 设备实例化。
- 现代 VMApple+TCG+Reims MMIO 同进程初始化、ARM 客体寄存器访问与 16 KiB Root/FIFO 差分；v3新增guest设备查询命令回写闭环及负对照。
- 某一 4096 字节 host-visible DMA-BUF 方案双向均失败；不能扩张为全部跨 GPU 路径不可行。

此后的平台、启动链与图形成果汇总在 `RESULTS.md`，详细当前状态在 `STATUS.md`。

## 3. 产品标准：保留 v2 的完整目标

所有数值均是规划阈值，不是本机性能预测。测试须固定应用版本、内容、分辨率、缩放、刷新策略、电源、冷/热缓存、后台负载及计时边界。低分辨率阶段实验不能冒充原生分辨率成绩。

| 维度 | 基础产品目标 | 长期挑战与测量边界 |
|---|---|---|
| 显示 | 包含 2560×1600 原生像素场景；先 60 FPS | 10 分钟稳态 missed-frame <1%；再研究 120/240 FPS；仅统计客体新内容 |
| 输入至实际呈现 | 60 FPS 场景 p95≤35 ms、p99≤60 ms | 高刷新 p95≤20 ms、p99≤35 ms；宿主收到输入至对应画面实际呈现，不冒充物理 input-to-photon |
| 长尾卡顿 | 热态无不可解释的 >100 ms 停顿 | 冷编译、I/O、饱和、同步分别归因；降低超过两个目标帧周期的事件 |
| 应用启停 | 每项动作报告绝对时间与冷/热分布 | 同版本/内容比较；跨 ISA、编译器、硬件和 API 差异分别交代 |
| CPU 吞吐 | 先同宿主可移植负载与裸机小载荷基线 | 代表性负载争取原生 x86 的 ≥80%；不承诺所有闭源 macOS 应用 |
| GPU | 正确 render/compute、结果误差有定义 | 代表场景额外耗时争取 ≤20%；Vulkan 对照不等于 Metal 语义天然等价 |
| 可靠性 | 100 次启动、24 小时混合负载，无数据错误 | 支持配置上 100 次睡眠/唤醒/重连；样本不是极低故障率证明 |
| 功耗 | 测同亮度/刷新/活动状态下的增量 | 整机增量争取 ≤20%；无传感器只报告可观察代理量，不编造瓦数 |

60/120/240 FPS 的帧预算分别约 16.67/8.33/4.17 ms。本机此前未观察到完整分辨率 120 Hz 模式；120 FPS 可先作为 240 Hz 下的更新节奏目标。

原生参照优先同一电脑的 Linux 实现；Apple Silicon 参考机用于行为对照，不把不同硬件时间当作同宿主零开销。降低某项指标必须记录原因，不能删除失败样本或修改“通过”的含义。

## 4. 架构与实现主线

```text
物理 Linux laptop / 后续 desktop
  ├─ Linux 驱动：GPU、DRM/KMS、显示、音频、网络、输入、电源、存储
  ├─ 现代 Reims QEMU（固定提交 + 本项目 TCG/VMApple 补丁）
  │    ├─ ARM64 CPU 执行：TCG 基线 → 按剖析优化/可选分层编译
  │    ├─ VMApple：异常、MMU、HVC、timer、GIC、PSCI、虚拟设备
  │    ├─ Reims apple-gfx-mmio + IOSurface + 客体 ABI
  │    └─ 真正 macOS kernel / 用户态 / WindowServer / 应用（已运行；验证范围见 STATUS.md）
  ├─ Linux x86_64 Reims：显式 ARM64 guest 页契约 → metal2vulkan → Vulkan
  ├─ 显示：已有原生窗口 → 专用 Wayland 会话 → 条件允许的直接扫描
  └─ 薄管理层、构建/映像/缓存、诊断与恢复
```

### 实际分支职责

| 角色 | 源码/构建 | 不能混淆的边界 |
|---|---|---|
| 平台对照 | `sources/qemu-portable`、`sources/qemu-headless` | 保留已测串口/PSCI；不再承担现代 GPU ABI 重写的前置要求 |
| 早期平台/图形对照 | `sources/qemu-reims-arm` | 现代 QEMU 的 Linux/TCG 移植；HVF hook被条件化；新增默认关闭的HVC软件参考模型，不是Apple硬件加密/目标ABI等价 |
| 当前恢复/已安装OS集成主线 | `sources/qemu-bdif-usb`、`build/qemu-bdif-usb/qemu-system-aarch64` | 同一现代QEMU基线与记录至0032的补丁链；使用`build-qemu-bdif-usb`构建，含原版恢复和正常OS/应用消费者。实际build/profile与验证边界见[STATUS.md](STATUS.md) |
| x86 图形对照 | `sources/reims-build`、`build/reims-target` | 4 KiB guest ABI 保留；不因 ARM 修复而覆盖 |
| ARM 图形主线 | `sources/reims-arm-guest`、`build/reims-arm-target` | host x86_64 + ARM guest 16 KiB ABI；逐层 guest/host 页契约 |
| 最小负对照 | `build/qemu-reims-arm` | 对 ARM 固件故意使用原 4 KiB ABI，以复现映射失败 |
| 自制ARM图形实验二进制 | `build/qemu-reims-arm16k/qemu-system-aarch64` | 已支持自制MMIO/FIFO/device-info测试，不是已验证macOS平台 |

初期保留编译时 guest ABI 选择，分别构建。未来多 guest 类型、ABI 自报与初始化不匹配拒绝属于独立接口工作，不先把全仓重构成通用平台。

诊断、兼容、性能三个 profile 共用语义与测试；不把逐指令 trace、icount 或单线程诊断时间当作产品性能。硬件支持交由 Linux，客体获得版本化虚拟设备；不预设必须仿真全部 Apple SoC、SEP 或 NPU。

## 5. CPU 与平台研发

### 5.1 正确性与性能基线并行

TCG 本来就是动态二进制翻译器，“加 JIT”不是有效任务定义。先分解翻译编译、已编译代码、helper、寄存器溢出、软件地址转换、异常/HVC、设备访问、锁/调度与空闲轮询的成本。

最小载荷依次覆盖：ARM64 ABI/寄存器/浮点/SIMD；EL 与异常返回；guest MMU/页权限/TLB/ASID；timer IRQ/GIC/屏蔽与副核；LL/SC/LSE 原子和跨核进展；修改已执行代码后的缓存失效；PAuth 指令结果及 Apple 密钥/上下文生命周期。**现有 UART 与 CPU_ON 成功不替代这些测试。**

### 5.2 优化路线

先据测量改善热点指令选择、寄存器/flags、helper、SIMD 映射、TLB/内存快路径、通知和空闲等待。所有快路径有明确前提、失败回退及异常恢复；不可删除 guest 权限/地址空间等语义以提高分数。

有证据才投入快速首译+热点优化编译。保留 guest PC/host PC 映射、精确异常状态、跨页/上下文 guard、SMC 失效、多核代码发布；LLVM/Dynarmic 是候选或对照，不是现成完整 Apple 平台替代品。

持久化代码缓存必须包含代码内容、OS/映像、翻译器、CPU 特征和平台语义；地址重定位、ASLR 和失效另行处理，不缓存活跃 PAC 密钥、宿主指针或旧权限。

自有/可得源码的重编译、局部宿主执行可以研究，但须解释 ABI、TLS、回调、指针和跨界成本。原生 x86 内核+ARM 用户态、非 Apple ARM 宿主是后续高成本实验，不偷换现有 laptop 目标；不能用 XNU 开源推导整个闭源 macOS 可重新编译。

## 6. 图形、地址空间与内存协议

### 6.1 分层完成条件

A. 库与 QEMU 构建；B. 设备实例化；C. guest CPU MMIO 读写；D. root mapping/FIFO 生命周期；E. guest 写入命令、host 消费及内存回写；F. host GPU 实际 render/compute 与正确回读；G. WindowServer/真实应用；H. 长时间、同步、错误路径和性能。

**E02 的宿主 Vulkan 测试与 E11 的客体协议、GPU执行、呈现各层独立验收；当前证据见 [STATUS.md](STATUS.md)。** 只测试命令完成/计数而无 draw/compute 的实验，不能宣称完成客体GPU渲染；自制guest呈现也不替代WindowServer/真实应用。

### 6.2 页与地址契约

明确区分 guest physical address、guest virtual address、guest APV 页号、host virtual pointer、host mmap/page granule、GPU device address。ARM guest APV=16 KiB；host Linux VM=4 KiB 不需改变。逐层审计 Root/FIFO、display page、IOSurface 页表/descriptor、task memory、isolated-task memory range 与映射长度。

host pointer 是否需要 16 KiB 对齐必须从宿主映射/平台契约判断，不能因为 guest 页为 16 KiB 就全局要求；也不能为消除测试失败盲目放宽。为同一 guest PA 构造正/负对照，检查真正映射而非只读回寄存器。

客体 RAM 使用已有共享 memfd 路线；hugetlb、MAP_FIXED、整个进程锁页与全量 hugepages 不是通用提速开关。页对齐修复需证明没有破坏 host API 对齐条件。

### 6.3 数据、同步与准确性

闭环必须是 guest 写入→host可见→GPU执行→结果可见→guest读取/IRQ。审计 buffer/texture 生命周期、别名、跨队列、完成回调、释放后重用与并发。不让 vCPU/主循环/GPU 完成互相等待构成死锁，不无界堆帧；可合并过期显示状态，不丢弃计算/同步/释放。

AIR→SPIR-V 与 Metal 资源/对象/命令语义是两层。复用 Reims 与 metal2vulkan 的严格/快速路径，记录 API、触发条件、误差与应用影响；显示近似可明确选用，计算/存储/同步不能靠虚报成功。

### 6.4 双 GPU 与缓存

同时保留 Intel渲染+Intel内屏、NVIDIA渲染+Intel内屏，以及后续同GPU直连对照。E03 的具体 DMA-BUF CPU映射失败只否定那个方案；继续 device-local buffer、DRM modifier、GPU copy、外部 semaphore、显式同步和呈现成本。无CPU回读不等于无物理搬运，名义 zero-copy 不是终极指标。

分别缓存 AIR 转换、shader 编译、Vulkan pipeline。key 包括内容和语义、设备/驱动兼容性；不要宣称跨驱动永久有效。冷启动、首次shader、热态分别测，不能跳过画面以掩盖编译。

## 7. 显示、输入与响应

建立从宿主收到输入，经客体事件/布局、图形命令、GPU、呈现到实际新画面的链。单响应用依赖关键路径，吞吐用瓶颈分析，不相加所有并行线程时间。

先复用原生 Reims 窗口/Vulkan swapchain，避免默认每帧 CPU回读+VNC。后续减少复制/合成、明确队列深度与反压，评估专用 Wayland、显式同步和直接扫描；格式、缩放、显示器条件不满足时允许可测 fallback。

键盘要测 Cmd/Ctrl、布局、焦点丢失、按键释放、绝对/相对指针、滚动和手势阶段。中文输入法、候选窗、组合字符、剪贴板与 host IME/guest IME 边界必须明确。跨层统一时钟并记录转换误差；QMP响应时间不冒充 input-to-photon。

## 8. 启动与版本管理

E05 正式独立于图形支线：审计 restore materials、hardware model、machine identifier、auxiliary storage、firmware、磁盘及恢复/personalization 过程。完整 bundle 必须记录来源、build、架构和一致关系，不任意拼接 aux/root，也不按旧教程固定偏移裁剪。

有 Apple Silicon 参考环境可用来建立已知好对照；没有则研究 Linux 可完成部分，并列出确切外部条件。参考机不是本地成果，未知来源成品镜像不是可重建初始化。初始化受阻时不停止独立 CPU/GPU 实验；当前原生还原与冷启动证据及 M2 剩余边界见 [STATUS.md](STATUS.md)。

最初一个 ARM目标build、一个必要ARM对照、可选26 x86对照。升级在新 bundle 中测试，不破坏已知好。guest磁盘修改、运行时代码修改、设备树/能力声明分开记录；每项补丁有适用build、机制、差异及撤销办法。优先修平台而非改客体，但不将绝对零补丁当教条。

## 9. 存储、音频、网络、睡眠与系统集成

存储验证 flush、顺序、持久化、正常关机及异常终止后再启动；raw/pflash 支持以实际路径为准，不强行全部转 qcow2。离线快照覆盖 aux、身份和必要持久状态。共享目录单独定义大小写、Unicode、权限、xattr、锁、原子rename。

睡眠需协调 vCPU、I/O、GPU、host sleep、时钟、设备重建；暂停进程或保存RAM不等于 GPU状态恢复。研究阶段可关机重启，不能因此通过最终睡眠验收。full checkpoint/live migration 另列能力。

音频测持续播放/输入、切换、阻塞、时钟漂移与 AV sync。网络由虚拟网卡接 Linux，测 DNS、网络切换、局域网、端口、唤醒恢复；不自动涵盖 Apple 互联与在线服务。VideoToolbox/媒体硬件加速独立于 Metal，先软件正确再做有需求的桥接。

产品包装在核心成熟后提供能力检查、材料导入/初始化、启停、失败报告、回退和专用会话入口，复用 QMP/Reims工具，不先造通用VM管理器。

## 10. 测试、证据与当前质量标准

测试层次仍是 host smoke→CPU/平台→设备→OS→图形→应用→整机。复用已有 QEMU/TCG/qtest 和 Reims测试；围绕当前修改做相关验证，跨模块做整合，候选交付再做完整矩阵。

每次 run 至少记录：源码/补丁、host/profile、guest bundle或自制载荷、命令、输入、退出状态、判定/日志及观察边界。process退出0、test通过、guest通过和负对照符合预期是不同字段。不能把测试程序始终退出0当作被测方案通过。

Reims Core 的部分测试共享进程级日志捕获，**确定性回归使用单测试线程**；并行模式用于额外暴露竞态，日志串扰单独分析。不得只删掉失败测试或改成忽略。当前串行全量、局部修复和实际QEMU测试各有日期，不拼成未运行过的“全绿”。

QEMU/派生测试工具不一定相互独立。没有独立ARM硬件时，优先构造规范/数学/源码能独立判断的预期。trace只在当前问题需要时扩大；保持日志和真实性能测量分开。普通用户运行、本地socket、明确共享范围是初始边界，不把TCG视作绝对安全沙箱。

## 11. 代码组织、复现与变更管理

整合仓库 `/workspace/macos-local`；`sources/`是固定第三方checkout/worktree，`patches/`是可重建差分；`tools/`放构建与运行入口，`experiments/`放各实验代码、自制固件及其离线测试；`docs/`放计划、状态、成果汇总，`docs/reports/`放逐项报告；`private/`放系统与身份材料；`build/`、`runs/`、`cache/`忽略入Git。

源码固定不等于二进制可复现。至少提供干净worktree套补丁、构建、对应实验入口；空白宿主恢复、依赖安装说明和缺失材料是 M0剩余工作。修复不能只留在临时worktree而漏进patch链。

QEMU以C、Reims以Rust、编排以Python/shell为主。三个可并行工作流是平台CPU、图形显示、生命周期整合；只在实质提速时并行。公共底层修改由一个整合点收口，避免多分支同时改相同核心。优先上游化通用修复，但不先公开推送或替用户发issue。

## 12. M0—M8 完整里程碑与当前定位

里程碑定义与验收内容保留 2026-10-15 的计划。最新完成的运行是 Run142（2026-11-12）：在默认关闭的 30 秒诊断下，Safari 首个 GPU 连接的初始化时间不随 PAC 算法和客体内存大小变化（负载下第一次启动 3.4–3.8 秒）；登录后约 20 分钟内 8 个 vCPU 被后台工作占满，在安静的客体上是 3.249 秒，窗口内仍然饱和；客体忙的时候 vCPU 线程 14%–30% 的时间在等 QEMU 的全局锁和独占段。最近一次有新功能结论的运行是 Run128（固定 WebGL2 读回）。原始 3 秒期限、诊断的 site1、一般 Metal、物理指标和文件系统干净关机都没有证明。项目自 2026-11-10 恢复了实验。当前队列见 [STATUS.md](STATUS.md)；本节不改变 M0—M8 的目标。

| 里程碑 | 必须完成的内容 | 当前定位/不能替代的条件 |
|---|---|---|
| M0 实验基线 | 固定依赖、可重建步骤、host执行、材料清单 | 现机基线已建立；同机全新克隆可从零重建（RESULTS 7.2），另一台空白环境的重建未测；材料缺口需清单化 |
| M1 ARM平台 | 单核/多核、timer/GIC、MMU/异常、原子、PAuth/HVC平台边界 | 平台功能局部通过，原版 macOS 已在 8 vCPU 上运行；Apple PAC/HVC 真实语义与多核正确性/性能未验证 |
| M2 启动闭环 | 匹配bundle；固件至早期XNU可复现 | 完整原生还原与已安装系统冷启动已验证 |
| M3 真实系统 | 真实目标kernel/用户态、shell、进程、网络、持久文件 | 登录、桌面、网络、持久文件、应用和客体关机都有实测；Run128 在 30 秒诊断下完成首个 GPU 初始化和固定 WebGL2 读回；普通首次加载、稳定性和文件系统干净关机仍待证明 |
| M4 图形闭环 | guest GPU资源/命令、WindowServer、输入、真实窗口/文本/文件 | 既有 GPU/IOSurface/原生像素路径保留；Run128 固定 WebGL2 的两份读回逐字节通过；诊断的 site1、一般 WebGL/Metal 和物理呈现性能仍待验证 |
| M5 基础产品 | 60FPS场景、输入/长尾、应用与稳定性，明确配置 | 功能可用；60 FPS、输入长尾、资源效率与稳定性标准未达 |
| M6 原生级性能 | CPU热点/内存/锁/呈现/能耗分项逼近目标 | 未测；不先重写JIT，但性能基线可现在建设 |
| M7 整机体验 | 睡眠、音频、IME、热插拔、更新回退与数据完整性 | Run108、114、121 的 PCM 完整，其他运行缺帧；Run128 只清点了输出设备，没有做参考 PCM；声卡布局修复不能证明保真度、因果或物理音频体验 |
| M8 第二平台 | 用户确认的desktop硬件，重建/运行/回归 | 硬件未定；两台成功不等于所有PC |

依赖关系不是流水线禁令：E02/E04/E05/E06/E10及平台、图形实验可交叉推进；完成级别只约束成果声明。


## 13. E01—E11 实验队列：保留原编号与新增范围

工作包定义保留；下列当前摘要以[STATUS.md](STATUS.md)中的具体证据与范围为准。

| ID | 完整工作包 | 当前已验证/剩余 |
|---|---|---|
| E01 | 固定两路QEMU、现代Reims、可重建集成 | 构建/PCI/MMIO局部通过；干净重建与patch链持续维护 |
| E02 | 双GPU render/compute/回读与后端边界 | 最小样例通过；非性能基准/完整驱动认证 |
| E03 | 双向共享、handle/format/modifier/同步/fallback成本 | 一个host-visible DMA-BUF方案失败；device-local与显式同步待测 |
| E04 | ARM小载荷对比同宿主x86执行代价 | UART smoke通过；CPU类别、内存和吞吐剖析未做 |
| E05 | 恢复材料与VMApple metadata，完整boot bundle | 材料获取、正常签名恢复及完整AUX/身份/系统盘的当前证据统一见[RESULTS.md](RESULTS.md)的启动链与AUX条目 |
| E06 | 无GPU VMApple平台、异常/timer/MMU小载荷 | 精确时钟、timer/MMU/异常、完整SIMD、双CPU EL0迁移及2/4/8隔离局部通过；原版8CPU OS/桌面已运行。Apple契约和广泛一致性/性能仍待，见[STATUS.md](STATUS.md)。 |
| E07 | 真正26 x86 Reims客体对照 | 虚拟显卡初始化不算；VT-x关闭且缺材料，不阻塞ARM |
| E08 | ARM对照与目标OS首次启动 | 固件Recovery与实际XNU执行分别验收；当前最远运行位置及剩余条件见[STATUS.md](STATUS.md) |
| E09 | CPU热点、MMU/helper、设备/锁等待分解 | 已有owned host剖析和PAC等价优化的独立自制consumer/成本样本；不是完整OS/设备等待归因或native性能，见[RESULTS.md](RESULTS.md)。 |
| E10 | 原生窗口输入→呈现与实际输出拓扑基准 | 自制窗口输入到guest绘制链路及原版macOS键鼠/native-window像素已证；物理输入/呈现延迟与性能基准仍待。 |
| E11（新增） | 跨ISA Reims设备/页/命令/IOSurface契约 | 真实 GPU compute/draw/IOSurface 以及原版设置助理、桌面、Terminal 的结果保留；Run128 固定 WebGL2 读回通过，但不能代替一般 Metal/API 兼容性、精度或性能验证。 |

### 下一组可执行工作（不设日期承诺）

当前队列以 `PLAN-STATE.json` 的 `next_actions` 为准，可读版本见 `STATUS.md`“下一步”一节；本文件不再另存一份。每项完成写回统一状态，再选择下一项；不把某一轮对话机械映射为必须全部完成的队列。

## 14. 关键风险与改线条件

| 未知 | 需要什么证据 | 合理调整而非直接放弃 |
|---|---|---|
| 初始化不可复现 | 某个restore/aux/platform步骤及材料缺口 | 参考环境、独立初始化研究、继续其他子系统 |
| 新版Apple ABI改变 | 同平台旧/新guest首个分歧 | 版本化profile，不随机拼系统 |
| CPU成本过高 | 指令/MMU/helper/锁与设备占比 | 局部快路径、分层DBT、受控宿主执行 |
| GPU语义错误 | 最小资源、命令、别名、同步与回读 | 严格路径、真实feature边界、局部软件实现 |
| 跨GPU代价高 | handle/modifier/sync与实际等待 | Intel路径、异步GPU copy、直连对照 |
| 页契约混淆 | 同guest输入在两个ABI下的因果差分 | 层次化guest/host接口，不全局替换常量 |
| 生命周期失败 | host/guest/GPU状态依赖 | 资源重建、独立checkpoint，别虚报恢复 |
| 测试自身失真 | 单独/串行/并行及源码预期 | 修测试隔离，不以假绿代替正确性 |
| 上游变化/范围膨胀 | 固定版本相关回归、应用真实依赖 | 小补丁重基、能力分层，不无限冻结或复制全部SoC |

## 15. 学习与长期协作

不要求先学完整套内核课程。围绕当前实验补足 ARM ABI/EL、页表权限、原子/中断、MMIO/DMA、IR/寄存器分配、Vulkan资源同步及持久化机制；每部分有可运行小例子。可深入有趣分支，不要求每次都提高启动率；但未理解的workaround仍标注未证。

可大胆试验、失败后再改，保留有效对照与可撤销修改。严谨用于结果与边界，不变成审批、频繁全量hash或重复防御检查。

## 16. 资源、功耗与跨机器维护

初期主VM 8—12 GiB、少量vCPU；synthetic测试可512MiB，逐步测2/4/6/8核，不一次给满20核。构建通常8—10路，按宿主活动调整；P/E affinity必须有测量收益才固化。空闲等待与轮询是显式变量，不能长期满功耗换桌面表面顺滑。

制品预算150—250GiB起步，区分原始材料、已知好binary、可重建缓存/trace。Gitignore不是完整空间策略，不擅自删除用户材料。第二宿主用能力profile而非堆DMI特判；Intel/AMD、GPU厂商、混合/直连输出各自实测。

## 17. 授权、分发与身份材料

沿用原计划的技术研究与授权分离原则。此前介质许可调查不等于本轮核实当前目标条款，正式部署/分发前需核对具体介质与依赖。代码仓库只保存自写代码、补丁和测试；不打包Apple镜像、firmware、aux、真实身份或账号材料，不替用户公开发布。

## 18. 复现入口与证据索引

完整命令表（按是否联网、是否需要 `private/` 材料或 root 分类）见仓库根目录 `README.md`。常用入口：

```bash
cd /workspace/macos-local
make doctor
make smoke                       # 已有host/ARM平台最小测试
make offline-tests               # 既有离线聚合子集；部分依赖私有材料/metadata
make platform-contracts          # timer/GIC、MMU、组合与负对照（无网络）
make smp-mmu-tests               # 双vCPU MMU/ASID/TLBI/原子
make reims-arm16k-smoke          # MMIO/页映射/FIFO差分
make reims-fifo-roundtrip        # guest设备查询命令+回写+负对照
make reims-core-serial           # 独立执行完整Core串行回归
make check-research-patches      # 补丁链可逐字节重建工作树
make boot-metadata               # 显式联网，仅metadata，不下载整个IPSW
```

当前installed OS复现入口见[对应README](../experiments/E05-installed-boot/README.md)；本节命令是入口索引，不表示维护阶段启动新的运行。新增测试命令以 `Makefile` 与 `PLAN-STATE.json` 为准。负对照必须标明预期失败，避免把流程退出码当兼容通过。

历史外部阅读入口保留：QEMU system/TCG/ARM/testing、Reims docs/qemu.md 和准确性账本、metal2vulkan、Linux DRM、Vulkan同步及external memory、Apple虚拟化恢复资料。动态页面只提供方向，执行用固定提交；本轮主证据为本机日志和源码，不把旧网站状态当当前复验。

原始总规划：`product-research-master-plan-v2-zh.md`（本地历史归档）；原始证据：`evidence-and-experiment-register-v2.json`（本地历史归档）。现行总计划路径 `docs/MASTER-PLAN.md`；状态唯一数据源 `docs/PLAN-STATE.json`（可读视图 `docs/STATUS.md`）；成果与证据汇总 `docs/RESULTS.md`；逐项实验报告 `docs/reports/`（其中 `M0-RESULTS.md`、`M1-ARM-REIMS.md` 为历史快照）；v3 对齐前状态 `docs/archive/`。后续记录必须保持这些角色清楚，不产生多份互相矛盾的“最新完成表”。

### 进展记录

2026-10-14 至 2026-10-15 期间逐轮追加在此处的进展摘要（PAC/HVC、时间契约、AVP MMIO、精确时钟、BDIF USB、原始AVPBooter、内核USB/IP、签名iBSS、iBSS交接、Stage1 Recovery、Stage1配置与bulk-OUT、iBEC前置、IM4M与证书约束、双vCPU MMU）已合并进 `RESULTS.md`；逐轮原文见 `reports/` 下对应报告及Git历史。今后的新结果写入 `PLAN-STATE.json`、`reports/` 与 `RESULTS.md`，不再追加到本文件。
