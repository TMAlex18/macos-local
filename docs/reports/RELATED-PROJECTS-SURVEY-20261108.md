> **Language:** [English](RELATED-PROJECTS-SURVEY-20261108.en.md) | **简体中文**

# 相关项目调查：在非 Apple 硬件上运行 arm64 macOS 的公开先例

调查时间：2026-11-08 UTC。方法：网页搜索，读取各项目的 GitHub 页面、GitHub API 元数据、issue 和 PR。没有下载、构建或运行这些项目的代码。

证据口径沿用 [MASTER-PLAN](../MASTER-PLAN.md) 第 2 节：

- **O**：从 GitHub 页面、API 或本仓库直接读到的事实，例如仓库创建时间、提交号。
- **R**：各项目自述的功能、耗时和问题，本项目未复现。
- **H**：本文提出的改进方向，尚未验证。

这些项目在 2026 年下半年变化很快，星标数和提交数只代表调查当时。

## 结论

1. **"在 x86-64 Linux 上用 TCG 跑 arm64 macOS"和"跑出桌面"都有更早的公开先例。** steelbrain 的 27on86 仓库创建于 2026-08-14（O），自述 macOS 13 无头启动、SSH 和干净关机（R）。orchard 创建于 2026-09-19（O），自述 macOS 13 桌面（R）。本项目的第一个提交是 2026-10-13（O）。GoldenGate-VM 于 2026-10-31 公开了 Windows WSL2 上的 macOS 27 桌面（O/R），与本项目 run-63 首次进入桌面是同一天。
2. **就公开资料而言，本项目仍有几点不同：** 只用 Linux 从 IPSW 完成原生还原并实时取得 TSS 个性化签名；使用未修改的原版 AVPBooter；PAC 实现了 Apple HVC 的密钥状态切换；已验证的系统功能最广。详见下文。
3. **最值得验证的借鉴是 PAC 算法（H）。** orchard 在已安装的 macOS 上使用 QEMU 的 `pauth-impdef`（R）；本项目主线仍用 QARMA5，而 E06 测得两者的调用开销相差约 11 倍（O）。

## 时间线

| 日期（UTC） | 项目 | 事件 | 口径 |
|---|---|---|---|
| 2021-05 | BlackBerry 研究团队 | 在 Intel Linux 主机的 QEMU 中启动 macOS 11 arm64 内核，用于安全研究 | R |
| 2023-06 至 2025-01 | QEMU 上游 vmapple | Alexander Graf、Phil Dennis-Jordan 的 vmapple 机型补丁系列（v1 至 v17），现已在上游文档中 | O |
| 2026-08-14 | steelbrain/experiment-macOS-arm64-on-linux-x86（27on86） | 仓库创建 | O |
| 2026-08-18 | steelbrain/experiment-macOS-arm64-on-asahi-linux-arm64 | 提交 657bb24；本项目于 2026-10-14 取得其 USB 传输补丁 | O |
| 2026-09-19 | yaelliethy/orchard | 仓库创建，最后一次推送为 2026-09-25 | O |
| 2026-10-13 | 本项目 | 第一个提交 | O |
| 2026-10-21 | losos-project/desktop PR #272 | 在 27on86 上加入 M1 CPU 模型和 PAC hypercall，后由 #276 合并 | O |
| 2026-10-30 | 本项目 | -53 完成原生还原，-54 独立冷启动 | O |
| 2026-10-31 | 本项目、JeanxPereira/GoldenGate-VM | 本项目 -63 进入桌面；GoldenGate-VM 仓库创建并公开 macOS 27 桌面 | O |
| 2026-11-01 | ChefKissInc/Inferno | 仓库归档 | O |
| 2026-11-02 | YahiaAngelo/vphone-linux issue #20 | Linux 上的 macOS 27 恢复盘卡在 SSV authenticator | O/R |

## 逐项对比

除"本项目"一列外，表中内容均为各项目自述（R）。

| | 27on86 | Asahi 实验 | orchard | GoldenGate-VM | 本项目 |
|---|---|---|---|---|---|
| 宿主 | x86-64 Linux | Asahi Linux，M2 Pro，KVM | x86-64 Linux，AMD Renoir 核显（RADV） | Windows 11 + WSL2，Ryzen 7 5700X + RX 6750 XT | Ubuntu 26.04.1，Core Ultra 7 255HX，Intel 核显 + RTX 5070 Ti Laptop |
| 客体 | macOS 13 | macOS 13.6（22G120） | macOS 13 | macOS 27（26A428） | macOS 27.0.1（26A434） |
| 系统盘来源 | 未说明 | 在 Mac 上用 macosvm 安装 | Cirrus Labs 的 Tart 镜像 `macos-ventura-base` | Tart 镜像 `macos-golden-gate-vanilla:27.0` | 在 Linux 上用 IPSW 原生还原，实时 TSS |
| AVPBooter | 未说明 | 从 Virtualization.framework 复制 | NyanSatan 补丁（偏移 0x2314） | NyanSatan 补丁（偏移 0x2370） | 原版，未修改 |
| QEMU 基础 | vmapple 改为可脱离 HVF 运行（分支 `vmapple-tcg`） | 上游 vmapple，另加 KVM 内核补丁 | 上游 master，加自有 `apple-vm` 机型 | orchard 的 `gg-macos27` 分支 | steelbrain/qemu-reims-vgpu 加 32 个补丁 |
| PAC | 据 losos-project PR，接受所有签名 | 硬件 PAC，KVM 保存 APCTL/KERNKEY | `pauth-impdef`；忽略按进程切换，所有进程共用一套密钥 | 未说明 | QARMA5 加 HVC 密钥状态模型，memo 可选 |
| vCPU | 8 | 未说明 | 12 | 12 | 8 |
| GPU 栈 | 无（无头） | Reims + Vulkan（llvmpipe） | reims-vgpu + metal2vulkan，RADV | reims-vgpu + metal2vulkan，Mesa Dozen | Reims ARM 16 KiB 移植 + metal2vulkan，NVIDIA 和 Intel |
| 自述进展 | 无头启动、SSH、重启、干净关机 | 图形设置助理 | 约 75 秒到桌面（12 vCPU；首次启动到登录界面需数分钟）、键盘、SSH | 约 15 分钟出现键盘设置助理，20–25 分钟出 Dock，30–35 分钟 SSH 可用 | 设置助理、登录、键鼠、Finder/Dock/Terminal、Safari、TextEdit、curl、网络、音频、WebGL2 读回、guest 关机 |
| 自述问题 | 更新版本（直到 macOS 27）列为后续里程碑 | Linux 原生安装未完成 | 指针输入到不了客体、圆角区域填黑、偶发 GPU panic、新镜像需重启 2–5 次 | 鼠标未验证、Liquid Glass 没有模糊效果、很慢 | 见 [STATUS](../STATUS.md)：Safari 首载、PCM 重复性、FS clean、性能 |

## 本项目的不同之处（附本项目证据）

1. **只用 Linux 完成原生还原。** -53 用原版组件和实时 TSS 票据完成还原，-54 独立冷启动（[RESULTS](../RESULTS.md) 第 1 节第 9–10 行，M2-PAC-HVC-CTRR-LIVE-FOLLOWUP）。对照（R）：
   - Asahi 实验在 Linux 上直接安装的尝试走到了真实的 DFU 传输，但没有得到可用的系统盘；
   - vphone-linux 停在 SSV authenticator；
   - orchard 和 GoldenGate-VM 使用在 Mac 上制作的 Tart 镜像。
2. **原版 AVPBooter。** 本项目从官方 IPSW 解出 AVPBooter，不加修改即可执行并应答 DFU（M2-ORIGINAL-AVPBOOTER）。orchard 和 GoldenGate-VM 用 NyanSatan 的补丁，把一个在 Apple hypervisor 之外不返回成功的例程改成直接返回 0（R）。
3. **PAC 实现了 HVC 密钥状态切换。** [0003 补丁](../../patches/qemu-platform/0003-vmapple-tcg-pac-model-stack.patch)用每个 VM 的随机主密钥，经 HMAC-SHA256 派生内核和用户密钥，并响应 diversifier 切换；客体的架构认证保持开启（O，另见 [E06 README](../../experiments/E06-pauth-hvc/README.md)）。对照（R）：orchard 忽略按进程切换，所有进程共用一套密钥；27on86 据 losos-project PR 接受所有签名。
4. **已验证的系统功能最广。** 本项目已验证的功能有：
   - 设置助理、密码登录、键盘和指针、Finder/Dock/Terminal、文件跨重启保留、guest 发起关机、curl HTTPS、Safari 和 TextEdit（RESULTS 第 1 节第 11–15 行）；
   - VirtIO 声卡进入 CoreAudio，参考 PCM 逐字节一致（Run105/107/108）；
   - 实际 Safari 中 WebGL2 的两份读回逐字节一致（Run128）。

   其余项目自述最多到桌面、键盘和 SSH（R）。
5. **宿主 GPU 不同。** 本项目在 NVIDIA 和 Intel 上验证过；orchard 用 AMD 的 RADV，GoldenGate-VM 在 AMD 上用 Dozen（R）。

## 可借鉴、待验证的方向（H）

1. **在已安装的系统上试 `pauth-impdef`。**
   - 依据：E06 在自写客体上测得，每次带 PACIBSP/RETAB 的调用比普通调用多出 47.6 ns（impdef）、370.6 ns（QARMA3）或 553.5 ns（QARMA5），并证明 HVC 密钥状态模型不依赖 PAC 算法（O，[E06 README](../../experiments/E06-pauth-hvc/README.md)）。Run85 中 PAC 约占 vCPU 采样的 42.61%（O）。impdef 只在还原阶段的 run 24/39 用过，从未用于已安装的系统（O，M2-PAC-HVC-CTRR-LIVE-FOLLOWUP-RESULTS.json）。orchard 自述已安装的 macOS 13 在 impdef 下运行（R）。
   - 做法：同一 binary、同一配置下做 impdef 与 QARMA5 的配对对照，测开机到桌面的耗时和 Safari 首个 GPU 连接的初始化耗时。
   - 代价：impdef 不是密码学算法，客体内 PAC 的防护强度会下降。研究用途可以接受，但应作为单独的 profile 记录。
2. **vCPU 数量。** orchard 自述 12 个 vCPU 在其宿主上效果最好（R）；本项目一直用 8 个，而宿主有 20 核。可以做 8 与 12 的配对对照。
3. **补一个可比的启动指标。** orchard 自述约 75 秒到桌面（R），本项目没有可以直接比较的"开机到桌面"计时。
4. **对照 Reims 的内存接法。** orchard 自述对 reims-vgpu 只改了一处，整块客体 RAM 通过 `VK_EXT_external_memory_host` 导入；又因为 amdgpu 拒绝共享文件映射，改用匿名内存（R）。本项目用 memfd、单独的 16 KiB 客体页移植和 7 个 Reims 补丁。
5. **orchard 的"圆角填黑"可能与本项目 metal2vulkan 0001 修复的 framebuffer fetch／混合问题同类。** 未验证。

## 其他相关项目

- **QEMU 上游 vmapple 机型**：文档要求 Apple silicon 上的 macOS 主机，示例使用 HVF；客体只支持 macOS 12，并且需要事先用 Virtualization.framework 装好的 VM（O，读取文档）。
- **steelbrain/reims-vgpu**：上述项目共用的 GPU 栈，自述为 Beta。官方列出三条路径：Linux KVM 上的 x86 macOS，以及 Apple silicon 上的 arm64 macOS（走 Metal，或经 MoltenVK 走 Vulkan）。没有"x86 宿主跑 arm64 客体"这条（R）。
- **imbushuo/qemu**：README 自述是基于 Alex 的 HVF 补丁的 Apple silicon 支持（R）；本项目只把它用作无 GPU 的 VMApple 对照。
- **losos-project/desktop PR #272/#276**：基于 27on86 的 509a4ce，加入 `apple-m1` CPU 和 PAC hypercall；作者注明尚未启动过 macOS（R）。
- **ChefKissInc/Inferno**（原名 QEMUAppleSilicon，源自 qemu-t8030）：模拟 iPhone 11 运行 iOS。维护者 2026-07 表示将移除 vmapple 支持，仓库 2026-11-01 归档（O）。GoldenGate-VM 自述 orchard 早期的 PAC 模拟源自 Inferno（R）。
- **NyanSatan/Virtual-iBoot-Fun**：orchard 和 GoldenGate-VM 所用 AVPBooter 补丁的来源（R）。
- **BlackBerry（2021）**：只到内核层，用于安全研究（R）。
- **路线不同的项目**：OSX-KVM / Docker-OSX（在 KVM 上跑 x86 macOS）、Darling（用户态兼容层）、UTM / Tart（只在 Apple silicon 宿主上运行）。本次没有逐一核对。

## 局限

- 除本项目的证据外，所有功能和耗时都来自各项目自述，既没有复现，也没有审阅其代码。
- Asahi 实验的仓库创建时间未取得（GitHub API 请求失败），表中用的是本项目 [references.json](../../experiments/E06-pauth-hvc/references.json) 记录的提交日期。
- 只调查了网页搜索能找到的公开项目，论坛、聊天群和未公开的工作不在范围内。

## 来源

- [steelbrain/experiment-macOS-arm64-on-linux-x86](https://github.com/steelbrain/experiment-macOS-arm64-on-linux-x86)
- [steelbrain/experiment-macOS-arm64-on-asahi-linux-arm64](https://github.com/steelbrain/experiment-macOS-arm64-on-asahi-linux-arm64)
- [yaelliethy/orchard](https://github.com/yaelliethy/orchard)，[orchard TECHNICAL.md](https://raw.githubusercontent.com/yaelliethy/orchard/main/TECHNICAL.md)
- [JeanxPereira/GoldenGate-VM](https://github.com/JeanxPereira/GoldenGate-VM)
- [YahiaAngelo/vphone-linux issue #20](https://github.com/YahiaAngelo/vphone-linux/issues/20)
- [losos-project/desktop PR #272](https://github.com/losos-project/desktop/pull/272)，[PR #276](https://github.com/losos-project/desktop/pull/276)
- [ChefKissInc/Inferno issue #303](https://github.com/ChefKissInc/Inferno/issues/303)
- [steelbrain/reims-vgpu](https://github.com/steelbrain/reims-vgpu)
- [QEMU vmapple 文档](https://www.qemu.org/docs/master/system/arm/vmapple.html)
- [vmapple 补丁系列 v17](https://patchew.org/QEMU/20250112210056.16658-1-phil@philjordan.eu/20250112210056.16658-12-phil@philjordan.eu)
- [imbushuo/qemu](https://github.com/imbushuo/qemu)
- [BlackBerry 2021 博客](https://blogs.blackberry.com/en/2021/05/strong-arming-with-macos-adventures-in-cross-platform-emulation)
