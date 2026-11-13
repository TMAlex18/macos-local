> **语言：** [English](README.md) | **简体中文**
>
> **总计划：** [`docs/MASTER-PLAN.md`](docs/MASTER-PLAN.md) ([English](docs/MASTER-PLAN.en.md)) (v3) | **当前状态：** [`docs/STATUS.md`](docs/STATUS.md) ([English](docs/STATUS.en.md)) | **成果汇总：** [`docs/RESULTS.md`](docs/RESULTS.md) ([English](docs/RESULTS.en.md)) | **许可证：** [`LICENSE`](LICENSE)

# macos-local — Linux/x86_64 笔记本上的 ARM macOS 跨架构虚拟化研究（PoC）

> **研究概念验证（Research PoC）— 源码可见、禁止商用（Source-Available, Non-Commercial）：**
> 本仓库是跨指令集系统虚拟化与 Metal→Vulkan 图形协议互操作的实验性研究快照，**不是**面向终端用户的成品软件、一键安装器或安全隔离沙箱。
> - **分层许可（源码可见非商用 + 上游自由软件协议）：** `patches/` 之外的全部自写测试载荷、运行器、工具脚本与文档采用 [`PolyForm-Noncommercial-1.0.0`](LICENSE) 授权（源码可见、禁止商用）；未授权任何第三方对上述 PolyForm 许可的原创材料进行商业销售、商业捆绑或商业算力托管。`patches/` 下的上游补丁严格沿用各自上游项目的 `GPL-2.0-only` / `GPL-2.0-or-later` / `LGPL-3.0-or-later` 许可证，不附加额外限制。
> - **零 Apple 代码与二进制（BYOF 自备介质）：** 公开研究快照不包含任何 Apple 固件、系统镜像、密钥、签名票据或 SDK 头文件。任何自行提供外部操作系统介质的用户须独立承担遵守相应软件许可协议的责任。本项目与 Apple Inc. 无关，亦未获其背书。

个人研究工作区：在 Linux/x86_64 宿主的 QEMU TCG 上运行现代 ARM macOS，采用基于 VMApple 派生的平台模型与 Reims（Metal→Vulkan）跨架构图形协议。无交付期限；长期远景目标是逼近原生系统的交互与性能体验。

宿主 1：Linux/x86_64 笔记本，Intel Core Ultra 7 255HX，NVIDIA RTX 5070 Ti Laptop + Intel iGPU，Ubuntu 26.04.1。宿主 2（台式机）尚未选定。

## 当前进展

- **已跑通的里程碑：** 原版 macOS 27.0.1（26A434）已在 QEMU TCG 下通过 Apple 原版签名启动链（AVPBooter → iBSS → iBEC → restore，配合实时 TSS 票据）完成完整还原、独立冷启动与日常桌面使用：
  - run-53 完成完整原生还原；run-54 独立冷启动已安装系统；
  - run-63 在 8 个 vCPU 上完成设置助理、密码登录、Finder/Dock 桌面与 Terminal；
  - run-64 验证文件跨冷启动持久化保留，以及通过 Apple 菜单正常关机并使 QEMU 正常退出；
  - run-65 验证客体 `curl` HTTPS 访问、Safari 本地与远程网页浏览，以及 TextEdit 编辑保存。
- **后续进展（Runs 70–128）：** NVIDIA 与 Intel 双 GPU 原生窗口像素呈现、自动 DHCP 与 HTTP 文件服务、VirtIO 声卡绑定为 CoreAudio 默认输出设备（部分轮次实现逐字节一致的 PCM 输出），以及固定 WebGL2 着色器两份原始像素读回与独立参考实现逐字节一致。
- **最近有新结论的运行（Run151–156，`0046–0050` 构建）：**
  - **Run151** 用 `0045` 构建在全新空白磁盘上重跑完整签名原生还原，用时 43.6 分钟（2,613.93 秒），比 run-53 快 2.57 倍。
  - **Run155** 与 **Run156** 完全关闭 30 秒超时诊断，在启动后 `+720 s`（登录后的后台负载仍高）冷启动 Safari，首个 GPU 连接分别在 **2.941 秒**和 **2.163 秒**完成初始化，低于 WebKit 原版 3.000 秒看门狗期限，0 次看门狗退出；新鲜复启动最快 1.417 秒。
  - **Run154** 连续运行 2.1 小时并解锁锁屏；**Run156** 通过多通道 WebGL2→Metal→Reims→Vulkan 读回（98,304 字节 0 误差）、内置应用与框架套件（计算器、TextEdit、系统信息、jsc、sips、sqlite3、plutil、gzip：7/7）和两轮逐位精确、没有零块的 `afplay` 采集，并用盘面结构和内核日志证明客体关机时主 APFS 容器干净卸载（[Run156 报告](docs/reports/M3-EXPANDED-VALIDATION-RUN156.md)）。
- **近期工作（2026-11-10 起）：**
  Runs 130–142 将 Safari GPU 初始化慢定位到客体 CPU 饱和与 14%–30% 的 QEMU 全局锁（`bql`）和全核暂停等待（[PAC 算法](docs/reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.md)、[客体内存](docs/reports/M3-RAM-SIZE-GPU-INIT-TIMING.md)、[宿主线程采样](docs/reports/M3-GPU-INIT-HOST-CPU-USE.md)、[安静客体](docs/reports/M3-QUIET-GUEST-GPU-INIT-TIMING.md)）。Runs 143–146（[同步触发源报告](docs/reports/M3-QEMU-SYNC-SOURCES.md)）用每核计数（`0033`）查清触发源并合入 `0034–0039`，饱和期全局锁获取减少 90%（总阻塞 3.4%）。Runs 147–156（[执行吞吐报告](docs/reports/M3-TCG-THROUGHPUT-AND-ORIG3S.md)）用 `perf` 剖析剩余 helper 热点并合入 `0040–0050`（跳转缓存、SoftMMU 刷新、PAuth 与 FP16 快路径），客体时间里翻译代码（`[JIT]`）的占比升到 25.7%，冷启动压到 3 秒以内。下一步：先保留 Run151 新装磁盘的副本，再用 `0050` 构建独立冷启动它；在 Intel 核显上复核；做更长、更广的负载测试。
  当前主线二进制为 `build/qemu-bdif-usb/qemu-system-aarch64`，SHA-256 `e6f7b2cb32df3779531cf1b93612a67d8e1fa350c8b894c19d0c9e71a60f4a51`，补丁链记录至 `0050`；详见 构建记录 与 [当前状态](docs/STATUS.md)。
- **主要未决问题**（证据与逐轮运行表见 [RESULTS 第 2 节](docs/RESULTS.md)）：
  - *3 秒期限的余量：* 最近两次冷启动（Run155–156）都通过；更早的构建仍出现过看门狗终止（Run147 的 `0039`、Run152 首轮的 `0048`），还需要更多冷启动才能说稳定。
  - *音频：* 更早的运行在部分轮次缺帧；Run156 在 `0050` 上的两轮采集逐位精确、没有零块，但长时音频和更早缺帧的原因都还没测。
  - *宿主侧：* 运行期间曾出现过 Vulkan `DeviceLost`、一次宿主 i915 GPU 挂起以及两次意外宿主重启。
- **尚未验证：** 大型第三方应用与更广的 Metal 覆盖、数天级稳定性、物理输入/呈现延迟、睡眠唤醒与输入法，以及 M5–M7 性能指标。
- **平台与图形自写验证：** 自写 ARM64 裸机固件验证了定时器/GIC、16 KiB MMU、多核同步、EL0 完整上下文与迁移、24 MHz 精确时钟及 PAC HVC 软件模型；自写客体在 NVIDIA 与 Intel GPU 上驱动了 Reims 计算、绘制、呈现与窗口输入链路。
- **Apple 组件静态分析：** IM4M 签名链、密封 SystemOS 解析以及 Virtualization AUX/UID 密钥格式（从未执行 Apple 代码）。
- **相关工作调研：** steelbrain 的实验仓库（2026-08，无头 macOS 13）与 orchard（2026-09，macOS 13 桌面）在本项目之前已在 x86 Linux 的 TCG 上运行了 arm64 macOS；GoldenGate-VM 于 2026-10-31 展示了 WSL2 下的 macOS 27 桌面。orchard 与 GoldenGate-VM 均修补了 AVPBooter 并使用预制 Tart 镜像，目前尚无公开项目报告在纯 Linux 下完成原版原生还原。详见 [相关项目调研](docs/reports/RELATED-PROJECTS-SURVEY-20261108.md)（2026-11-08；未复现外部项目声明）。

详细数据、失败样本与边界说明见 [RESULTS](docs/RESULTS.md) 与 [实验报告](docs/reports/)。

## 仓库结构

| 路径 | 内容 | Git 状态 |
|---|---|---|
| `docs/` | MASTER-PLAN（总计划）、STATUS（当前状态）、TEST-HISTORY（测试历史）与 RESULTS（成果汇总），均配中英双语 | 跟踪 |
| `docs/reports/` | 分专题实验报告及脱敏 JSON 结果（中文报告均配 `.en.md` 英文版） | 跟踪 |
| `experiments/E01…E11/` | 实验驱动代码、自写裸机固件与离线单元测试；各目录均附 README | 跟踪 |
| `patches/` | 基于锁定上游提交的 QEMU、Reims、metal2vulkan 等补丁链 | 跟踪 |
| `tools/` | 源码拉取、构建脚本、补丁重建核验与状态工具 | 跟踪 |
| `sources.lock.json` | 锁定的上游 Git 仓库版本 | 跟踪 |
| `sources/` | 检出的第三方源码与打补丁工作树 | 忽略 |
| `build/`、`cache/`、`runs/` | 构建产物、缓存与原始运行日志 | 忽略 |
| `private/` | 用户自备固件、密钥、票据、客体磁盘与凭据 | 忽略，严禁提交或外发 |

## 常用命令

在仓库根目录执行 `make <target>`。多数目标需先拉取锁定源码（`make fetch-sources`）并完成对应的 `build-*` 构建。已安装系统运行使用 `experiments/E05-installed-boot/run.py`，详见 [installed-boot 说明](experiments/E05-installed-boot/README.md)。标签含义：

- **net**：访问 GitHub 或外部服务器拉取源码/元数据。
- **private**：需要 `private/` 下已准备好的用户自备材料。
- **root**：使用 `sudo -n` 加载 `vhci_hcd` 或执行 USB/IP 操作。

### 源码准备与构建

| 目标 | 产物 / 作用 |
|---|---|
| `fetch-sources` | 按 `sources.lock.json` 克隆/核验上游源码，并拉取外部参考 USB 补丁 |
| `build-armvirt` | 诊断用 AArch64 QEMU（ARM `virt` 机器，不含 VMApple） |
| `build-vmapple-headless` | 无 GPU 的 VMApple 对照构建 |
| `build-reims-vulkan`、`build-reims-arm64-vulkan` | 面向 x86 4 KiB / ARM 16 KiB 客体页的 Reims Vulkan 库 |
| `build-reims-qemu-x86` | 带 `apple-gfx-pci` 的现代 x86 QEMU（需先执行 `build-reims-vulkan`） |
| `build-reims-qemu-arm`、`build-reims-qemu-arm16k` | VMApple + TCG + Reims：4 KiB 负例对照 / 16 KiB |
| `build-qemu-clock-exact` | 默认关闭的 24 MHz 精确计数器/deadline 构建 |
| `build-qemu-bdif-usb` | 当前主线构建 |
| `build-shared-cache-identity` | 自写无 SDK 的 arm64 Mach-O 共享缓存身份探针 |

### 自包含硬件契约与图形检查（无需联网，不含 Apple 代码）

| 目标 | 检查内容 |
|---|---|
| `doctor` | 宿主环境与工具链清点 |
| `smoke` | `gpu-compute`、`gpu-render`、`arm-uart`、`vmapple-cfg`、`vmapple-psci` |
| `gpu-compute`、`gpu-render`、`gpu-external-caps`、`gpu-external-import` | E02/E03 双 GPU Vulkan 计算、离屏渲染与跨 GPU 导入测试 |
| `reims-pci-realize` | x86 QEMU 下 `apple-gfx-pci` 设备实例化 |
| `reims-arm16k-smoke`、`reims-arm-compare`、`reims-fifo-roundtrip`、`reims-core-serial` | E11 ARM 客体 ↔ Reims 跨页大小协议测试 |
| `reims-gpu-compute`、`reims-gpu-draw` | E11 自写客体在 NVIDIA 与 Intel GPU 上的计算/绘制及负例对照 |
| `reims-gpu-irq`、`reims-gpu-restore-platform` | E11 完成中断、屏蔽负例及与主线平台的共存验证 |
| `platform-contracts`、`platform-timer`、`platform-mmu` | E06 定时器/GIC 与 16 KiB MMU 契约 |
| `smp-mmu-tests`、`smp-mmu-extended` | E06 双 vCPU MMU/ASID/TLBI/原子操作 |
| `el0-context-tests`、`el0-context-extended` | E06 单 vCPU 双 EL0 MMU/ASID/PAuth/IRQ 抢占与负例 |
| `el0-migration-tests`、`el0-migration-extended` | E06 两种 TCG 模式下的双 vCPU EL0 跨核迁移 |
| `pauth-baseline`、`pauth-model`、`pauth-model-impdef`、`pauth-guards` | E06 PAC 基线与默认关闭的 HVC 软件模型 |
| `pauth-cost-bench` | E06 `PACIBSP`/`RETAB` 单次 TCG 模拟开销微基准 |
| `sync-counter-tests` | E06 每 vCPU 的 TCG 全局锁与独占段触发源计数测试（`0033`） |
| `fp16-fma-tests` | E06 对 `0050` FP16 乘加快路径的精确参照校验（只在宿主运行） |
| `clock-math`、`clock-functional`、`clock-rate-exact`、`clock-check`、`clock-rate-baseline` | E06 24 MHz 时钟契约测试 |
| `bdif-usb-tests`、`bdif-usb-backpressure`、`bdif-usb-control`、`bdif-block-tests` | E05 自写客体下的 BDIF USB 与块传输测试 |
| `config-mailbox-tests`、`avp-rtc-tests`、`ctrr-contract-tests`、`ctrr-qemu-guest-tests`、`virtio-usb-tests` | E05 配置邮箱、AVP RTC、CTRR 与 VirtIO USB 自写客体契约 |
| `check-research-patches` | 验证全部工作树可由锁定基线 + 补丁链逐字节重建 |
| `installed-offline-tests` | 已安装系统控制器、UI/文件服务、采样剖析与协议辅助模块的纯离线回归测试（无需 QEMU、联网或私有材料） |


## 工作守则

- 外部固件、系统镜像、密钥、票据、nonce、AUX、客体磁盘与凭据一律保存在被 Git 忽略的 `private/` 目录中，严禁提交或分发。
- 负例对照预期表现为拒绝或失败；退出码为 `0` 仅代表所测边界符合预期，不等同于完整系统兼容性声明。

## 先前工作致谢与许可说明

- **上游基础：** QEMU `vmapple` 机型系列（Alexander Graf、Phil Dennis-Jordan）、`steelbrain/reims-vgpu`、`steelbrain/metal2vulkan`、`steelbrain/experiment-macOS-arm64-on-asahi-linux-arm64` / `steelbrain/experiment-macOS-arm64-on-linux-x86`（含 Anees Iqbal 在提交 `657bb24` 中的参考 USB chardev 补丁）、`imbushuo/qemu`、`libimobiledevice/idevicerestore`、`libtatsu`、`blacktop/ipsw` 与 `blacktop/go-apfs`。关于先例项目（`27on86`、`orchard`、`GoldenGate-VM`）的对比与范围边界见 [相关项目调研](docs/reports/RELATED-PROJECTS-SURVEY-20261108.md)（[English](docs/reports/RELATED-PROJECTS-SURVEY-20261108.en.md)）。
- **分层许可证：** `patches/` 下的上游补丁保留各自上游的 `GPL-2.0-only` / `GPL-2.0-or-later` / `LGPL-3.0-or-later` 许可条款；`patches/` 以外的全部自写 PoC 代码、裸机探针、工具与文档按 [`PolyForm-Noncommercial-1.0.0`](LICENSE) 以源码可见（source-available）、非商用方式授权（许可证全文见 [`LICENSES/`](LICENSES/)）。
