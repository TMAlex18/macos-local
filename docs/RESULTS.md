> **Language:** [English](RESULTS.en.md) | **简体中文**

# 研究成果汇总

最后整理：2026-11-13 UTC。结果截至 Run156；最近有新结论的运行是 Run152–156（2026-11-13：补丁 0046–0050 在完全关闭 30 秒超时诊断的原版期限下，将 +720 秒后台峰值窗口首次冷启动 Safari 连续两轮压入 `< 3.000 s` 硬期限达 **2.941 秒** 与 **2.163 秒**、新鲜复启动创全项目纪录 **1.417 秒**，验证连续 **2.10 小时**稳定驻留与锁屏步进解锁，并在 Run156 一次通过多通道 WebGL2/Metal 98,304 字节零误差读回、原生应用与 `arm64e` 框架 7/7 套件、双轮 0 零块欠载 `afplay` PCM 及 APFS 主容器盘面+内核双重干净卸载证明）。

| 需要什么 | 看哪里 |
|---|---|
| 当前阶段、剩余项、下一步 | [STATUS.md](STATUS.md) |
| 目标、架构与里程碑定义 | [MASTER-PLAN.md](MASTER-PLAN.md) |
| 每次测试的通过/失败数 | [TEST-HISTORY.md](TEST-HISTORY.md) |
| 实验过程、失败样本与边界 | [reports/](reports/) |

## 结论

在 Linux x86_64 笔记本的 QEMU TCG 上，**原版 macOS 27.0.1（26A434，VirtualMac2,1）已经完整还原、冷启动并进入可用桌面**：

- 启动链全部使用 Apple 原版组件和签名：AVPBooter → iBSS → iBEC → restore，票据来自实时 TSS 请求。
- -53 完成原生还原；-54 从安装好的磁盘独立冷启动；-63 在 8 vCPU 上完成设置助理、登录、桌面和 Terminal；-64 验证文件跨重启保留和正常关机；-65 验证 curl HTTPS、Safari 和 TextEdit。
- 图形经 Reims 跨架构协议由宿主 GPU 用 Vulkan 渲染，NVIDIA 和 Intel 上都已进入原生桌面；键盘、鼠标、网络和声卡都是标准虚拟设备。
- Run70–128 新增：自动 DHCP 与 HTTP、VirtIO 声卡成为 CoreAudio 默认设备、部分运行中逐字节一致的 PCM 输出，以及 Run128 的固定 WebGL2 读回（与独立 oracle 逐字节一致）。

**Safari 首次加载突破、TCG 吞吐深挖与跨子系统扩展验证（Run143–156）：** 此前全新 GPU 进程赶不上 WebKit 的 3 秒看门狗。Run143 通过每核无锁计数（补丁 0033）查明饱和期每秒 41–46 万次全局锁获取与 6516–7721 次全核暂停的触发源；合入补丁 0034–0039 后，饱和期 `bql` 获取率下降 90%，vCPU 运行或可运行占比达 **96.6%**（总同步阻塞仅 3.4%，见[同步触发源报告](reports/M3-QEMU-SYNC-SOURCES.md)）。随后针对 DWARF `perf` 剖析出的 C helper 热点分两批合入纯执行吞吐补丁 0040–0050（`CF_PCREL` 跳转缓存按页先读后写失效、16 KiB 子页精确失效、PAuth VA 缓存与 SSSE3 QARMA5、SME 预拆包、宿主 RAM 地址标记的 64B 对齐 `512 KiB` 2-路 256 页桶内联持久跳转缓存、选择性 `TTBR0_EL1` 刷新、BTI 代码 TLB 快路径、`64 KiB` 端到端 PAuth 叶函数缓存含 `PAC→AUT` 逆向预填充、以及硬件 `F16C+AVX2+FMA` 逐位精确 FP16 乘加），消除 `9.18%` 软浮点 FP16 瓶颈并将 `[JIT]` P 核占比提升至 **25.71%**（累计 **+90.2%**），使登录后后台索引提前 6 分钟以上消退、宿主均温降至 **68.6 °C**。在**完全关闭 30 秒超时诊断**的原版行为下：`+720 s` 窗口首次冷启动连续两轮以 **2.941 秒**（Run155）与 **2.163 秒**（Run156）严格击穿 `< 3.000 s` 硬期限（0 看门狗退出），新鲜复启动创全项目最快 **1.417 秒**（首次超越非密码学 `impdef`，见[吞吐报告](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.md)）。Run151 在 `0045` 主线上以 **2,614 秒**（较 `-53` 提速 **2.57×**）复验完整签名原生还原；Run156 进一步在 `0050` 主线上验证多通道 WebGL2/Metal 管线（离屏 FBO、深度/模板、UBO、纹理、实例化、source-over alpha 混合共 98,304 字节 0 误差）、3 款内置 GUI 应用与 `jsc`/`sips`/`sqlite3`/`plutil`/`gzip` 框架套件（7/7 通过）、双轮 `0` 零块欠载逐位精确 `afplay` PCM，并通过只读盘面检查点与内核 `nx_mount` 日志双向证明已安装主系统 APFS 容器干净卸载（见[扩展验证报告](reports/M3-EXPANDED-VALIDATION-RUN156.md)）。其余未达成项目见第 8 节。

## 1. 启动链与系统里程碑

| # | 阶段 | 结果 | 证据 |
|---|---|---|---|
| 1 | 官方材料 | VirtualMac2,1 27.0.1（26A434）完整 IPSW（26,637,307,067 B）及 5 个小型 IM4P 组件，均核对 CRC/SHA-256 | M1-RESTORE-FIRMWARE-ADT-CLOCK |
| 2 | AVPBooter | 从官方 OS 镜像（AEA 加密 APFS）按需解密提取，334,960 B，SHA-256 `6d010540…a845` | M2-ORIGINAL-AVPBOOTER |
| 3 | 原版 ROM 与 DFU | 未修改的 ROM 执行并应答 DFU（05ac:1227）；修复 BDIF 初始 TX-ready（0007） | 同上 |
| 4 | Linux USB 通道 | ROM → BDIF → USB/IP → vhci_hcd → 未修改的 irecovery/libusb，3 轮挂接/查询/卸载 | M2-KERNEL-USBIP |
| 5 | 实时签名与 iBSS | 同一会话取 nonce → TSS 返回票据 → 353,151 B IMG4 分 173 块传完 | M2-SIGNED-IBSS-TRANSFER |
| 6 | iBSS 与 Stage1 Recovery | 入口交接（0008）；修复 AUX 容量、BDIF 写入和 RX 容量（0009）后进入 Recovery（05ac:1281） | M2-IBSS-HANDOFF、M2-IBSS-RECOVERY |
| 7 | Stage1 配置与数据 | alternate 切换；bulk-OUT 最大 65,539 B 与 guest RAM 逐字节一致 | M2-GATEWAY-SELECTION、M2-STAGE1-BULK-OUT |
| 8 | iBEC / Stage2 | LocalPolicy 获签；原版 iBEC 入口与 Stage2 Recovery；配置 DMA（0010）、RTC（0011）、CTRR（0012/0016） | M2-NATIVE-RESTORE-CONTRACT |
| 9 | 完整原生还原 | -53（0017）：6,707 s，Restore Finished，seal_system_volume / create_system_snapshot 成功；Run151（0045）：**2,614 s**（提速 2.57×，`bootx`→USB 激活 17.3 s），产出全新已封印干净磁盘基线；此前 -45…-52 的失败与修复见报告 | -53 结果、Run151、M2-PAC-HVC-CTRR-LIVE-FOLLOWUP |
| 10 | 已安装系统冷启动 | -54：新 QEMU/RAM、无恢复上传；卷组/封印快照/正常 SystemOS 切换组成身份链，loginwindow 与 WindowServer 执行 | -54 结果 |
| 11 | 初始 GUI 与键鼠 | -57/-58：语言选择页可见，键盘和指针有界面反馈（需 Reims 0006 修复 heap alias）；随后的 FramebufferFetch 中止由 Reims 0007 + metal2vulkan 0001 修复 | -58 审阅 |
| 12 | 网络 | -60：AppleVirtIONetwork 驱动就绪，双向 TCP 443 数据（需 0019 的 PCI cap 升序） | -60 网络 |
| 13 | 账户与桌面 | -63（8 vCPU）：完成设置、密码登录、Finder/Dock/Terminal；终端确认 27.0.1/26A434、arm64、8 CPU | -63 审阅 |
| 14 | 持久化与关机 | -64：-63 写入的文件跨冷启动内容和 SHA-256 不变；Apple 菜单关机产生 guest 发起的 SHUTDOWN，QEMU 正常退出 | -64 核验 |
| 15 | 代表性应用 | -65：curl HTTPS 200（证书校验通过）、Safari 本地页面像素精确与远程页面、TextEdit 保存后字节一致 | -65 应用 |

## 2. 已安装系统与主线复验进展（Run70–156）

这里只保留结论。每轮运行的完整记录在各自的报告。

### 2.1 已验证的能力

| 能力 | 结果 | 证据 |
|---|---|---|
| 原生窗口像素 | 旧构建在 native-window 下以 `FramebufferFetch / NotImplemented` 中止（Run73–75、77）。把 metal2vulkan 补丁扩展到 BGRA8Unorm source-over 后，NVIDIA（Run83）和 Intel（Run85、88、89、91）的原生像素都已验证 | [Run83](reports/M3-BGRA8-NATIVE-VALIDATION-RUN83.md)、[Run85](reports/M3-SAFARI-GPU-INPUT-RUN85.md) |
| 网络 | 自动 DHCP（10.0.2.15/24）、DNS、HTTPS，以及客体的 HTTP GET/PUT | [Run85](reports/M3-SAFARI-GPU-INPUT-RUN85.md)、[Run92](reports/M3-RUN92-NETWORK-DHCP.md)、[Run106](reports/M3-RUN106-AUDIO-OFF-NETWORK-CONTROL.md) |
| Safari 页面与原版 3 秒首载 | 暖启动与 4/8 GiB HTTP 页面像素正确；在 `0050` 主线上关闭 30 秒诊断后，`+720 s` 冷首次启动连续两轮以 **2.941 s**（Run155）与 **2.163 s**（Run156）严格低于 `3.000 s` 硬期限（0 看门狗退出），新鲜复启动达 **1.417 s** | [吞吐报告](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.md)、[扩展验证](reports/M3-EXPANDED-VALIDATION-RUN156.md) |
| 声卡设备 | AppleVirtIOSound 绑定并成为 CoreAudio 默认输入/输出设备（双声道，48 kHz），可与网络共存 | [Run105](reports/M3-RUN105-VIRTIO-SOUND-BINDING.md)、[Run107](reports/M3-RUN107-AUDIO-NETWORK-COEXISTENCE.md) |
| PCM 输出 | `afplay` 的输出与 96,000 帧参考 PCM 逐字节一致：Run108、114、121，以及 `0050` 主线上的 **Run156 双轮连续播放**（`VSNDPCM1` 边界追踪确认 0 个内部零块欠载）。早期旧构建部分运行缺帧（见 2.3） | [Run108](reports/M3-RUN108-PCM-BIT-EXACT.md)、[Run156](reports/M3-EXPANDED-VALIDATION-RUN156.md) |
| WebGL2 / Metal 跨架构管线 | 固定六色块双缓冲读回跨 8 轮独立冷启动 **8/8** 逐字节通过；Run156 多通道管线（128×96 `RGBA8`+`DEPTH24_STENCIL8` FBO、`std140` UBO、二维 `REPEAT` 纹理、模板裁剪、深度遮挡、3 实例 `drawArraysInstanced`、source-over alpha 混合及默认缓冲 `texelFetch` 合成，共 98,304 B）**0 误差逐字节通过** | [Run156](reports/M3-EXPANDED-VALIDATION-RUN156.md) |
| 原生 GUI 应用与框架套件 | Run156：`Calculator`、`TextEdit`、`System Information` 并发启动、前台注册、原生截图与干净退出；`JavaScriptCore`（`jsc`）、`sips` 无损 PNG 编码/缩放、`sqlite3` WAL ACID、`plutil` `bplist00`、`gzip`/SHA-256 全部通过宿主独立裁判（7/7） | [Run156](reports/M3-EXPANDED-VALIDATION-RUN156.md) |
| 客体关机与 APFS 干净卸载 | 客体执行 `shutdown -h now` 后 QEMU 以 0 退出；`check-apfs-clean-unmount.py` 只读校验确认 `Apple_APFS` 主系统容器（`disk0s2` 全部 6 卷）与 `Apple_APFS_ISC`（`disk0s1`）均满足 Fletcher-64 完整且 `block0_xid == latest_checkpoint.xid`，后续开机 `apfs.kext` `nx_mount` 零 `reloading after unclean unmount`（独立恢复分区 `disk0s3` 因常规启动不写挂载而保留初始还原状态） | [Run156](reports/M3-EXPANDED-VALIDATION-RUN156.md) |

### 2.2 Safari 首次加载（已在 `0050` 主线解决并验证跨启动可重复）

**现象。** 全新启动的 Safari 第一次打开页面时常是白页或错误页，随后提示页面因出错被重新加载；重载后或暖启动时能正常显示。

**机制（Run69 起多次复现）。** WebKit 给 GPU 进程的响应期限是 3 秒。在 TCG 下，新的 GPU 进程来不及完成初始化就被判为无响应并终止，替补的 GPU 进程在启动途中又被终止；30 秒内超过 2 次后 WebKit 终止所有页面进程。见 [Run69](reports/M3-SAFARI-GPU-CAUSE-RUN69.md)。

**已经查明的：**

- 看门狗触发时，GPU 进程的主线程在同步等待 `com.apple.audio.SandboxHelper`，而这个辅助进程当时还在启动（xpcproxy → exec → dyld/ObjC 等初始化）。见 [Run110](reports/M3-RUN110-GPU-INITIALIZATION-STACKSHOT.md)、[Run112](reports/M3-RUN112-GPU-SANDBOXHELPER-STARTUP.md)。这是观察到的等待点，不是已证明的唯一原因。
- 从连接建立到看门狗触发稳定在约 3 秒（Run114–124 为 2.96–3.00 秒）。把期限放宽到 30 秒后，初始化在 4.081 秒（Run127）和 3.781 秒（Run128）完成，GPU 看门狗 0 次。那两轮的 Safari 是登录时被自动恢复、退出后再启动的，所以这是同一次开机内第二次启动的数字。
- 用固定流程重复测量（Run131–137，14 次试验，全部在 30 秒诊断下完成，无响应退出 0 次）：开机后第一次启动的初始化是 3.515–3.791 秒（6 个样本），第二次启动是 2.768–3.419 秒（6 个样本）。同一配置的样本之间能差 0.3 到 0.65 秒。Run133 的 2.907 秒和 Run136 的 2.768 秒是第一次测到低于 3 秒。
- 初始化可以分成四段。其中 SandboxHelper 连接从创建到销毁这一段在 14 次试验里都是 1 秒左右（0.771–1.084 秒，平均 0.992 秒），不随 PAC 算法和启动次序变化。见 [PAC 算法对比](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.md)。
- SandboxHelper 那一段是启动一个新进程的开销，不是固定等待（Run140）：launchd 决定启动到 xpcproxy 换成本体约 0.15 秒，动态链接和系统库初始化约 0.3 秒，编译并应用沙箱配置约 0.25 秒，之后不到 0.1 秒就答复。每个 SandboxHelper 实例一生只用 0.6–1.1 秒 CPU。见[内存大小对照](reports/M3-RAM-SIZE-GPU-INIT-TIMING.md)。
- 初始化窗口里 GPU 进程自己很少在计算：它的主线程用的 CPU 不超过窗口长度的一半（Run131–137 为 18%–39%），其余时间在等 tccd、coreaudiod、AudioComponentRegistrar 和 SandboxHelper 答复。窗口的内容是音频子系统的初始化。
- 窗口里 CPU 是饱和的（Run140）：8 个 vCPU 有约 90% 的时间全部在忙。从启动后 90 秒到关机，客体几乎一直如此，包括桌面上无人操作的时候；没有登录时（Run138）平均只有 2.3 个 vCPU 在忙。开机 20 分钟里，几百个后台进程累计用了约 7300 秒 CPU。"全忙"时每个 vCPU 线程也只有约 76% 的时间在宿主 CPU 上，约 22% 被阻塞，原因未查明。见[宿主 CPU 报告](reports/M3-GPU-INIT-HOST-CPU-USE.md)。
- 登录后的后台负载持续约 20 分钟（Run142）：每分钟平均 4.9–6.1 个 vCPU 在忙，第 22 分钟起降到 1.3–2.4。在安静的客体上启动 Safari，按回车前只有 1.3 个 vCPU 在忙，初始化窗口内却仍是 6.0（8 个全忙占 89%）：启动本身就会占满 CPU。初始化用了 3.249 秒（第一次启动）和 2.588 秒（第二次启动），都低于负载下的范围，但第一次仍高于 3 秒。见[安静客体](reports/M3-QUIET-GUEST-GPU-INIT-TIMING.md)。
- 客体忙的时候，vCPU 线程曾有 14%–30% 的时间卡在 QEMU 自己的同步上（Run141、142 的阻塞采样）。Run143–145 用每核计数器（0033）定位并合入六项补丁（0034–0039）后，饱和期每秒全局锁获取下降 90%，vCPU 运行或可运行占比升至 **96.6%**（等 `bql` 仅 0.2%–0.3%，等独占段降至 2.8%）。随后合入 `0040–0050`（跨进程持久 2-路 256 页桶 `512 KiB` 内联跳转缓存、16 KiB 子页精确 TLB 与选择性 `TTBR0` 刷新、BTI 快路径、`64 KiB` 端到端 PAuth 叶函数缓存含 `PAC→AUT` 逆向预填充、SSSE3 QARMA5、以及硬件 `F16C+AVX2+FMA` FP16 乘加），消除 `9.18%` 软浮点 FP16 热点并将 `[JIT]` P 核占比提升至 **25.71%**，使登录后后台负载在 `+660 s` 即提前消退。在**完全关闭 30 秒超时诊断**的原版期限下，`+720 s` 冷首次启动在 Run155（**2.941 秒**）与 Run156（**2.163 秒**）连续两轮均以 **0 看门狗退出严格低于 `3.000 s` 硬期限**完成，新鲜复启动最快达 **1.417 秒**。见[同步触发源报告](reports/M3-QEMU-SYNC-SOURCES.md)与[吞吐剖析报告](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.md)。

**没有成立的解释和没有奏效的改动：**

| 假设或改动 | 结果 |
|---|---|
| 内存不足（4 GiB 对 8 GiB） | 两种配置都出现过成功的 HTTP 页面（Run89、94–97）。在 30 秒诊断下计时（Run140，一轮）：8 GiB 时压缩少了 60%–71%、解压少了约 80%，第一次启动的初始化是 3.444 秒，第二次 3.061 秒，与 4 GiB 的范围没有明显差别 |
| 音频编解码器查询本身慢 | 在普通进程里单独查询很快（Opus 首次约 0.46 秒，Run104）；该对照不覆盖 Safari 沙箱内的路径 |
| 采样和观察工具拖慢了系统 | 不做堆栈采样时失败照样出现（Run104：6 次看门狗请求） |
| PAC 精确 memo（0022、0024、0029） | Run86/87 中 PAC 采样 CPU 时间下降 42.03%；命中率随容量上升（64 项约 66%，1024 项约 83–84%，16384 项约 89%），但每种容量下首次加载都失败 |
| PAC VA 投影（0025）、TB 查找指针（0026） | 0025 只在微基准上变快（每次调用 8.245 → 3.755 ns），0026 没有测到收益；首次加载仍失败（Run116、117） |
| vCPU 绑核（Run120）、内核观察器只在启动期运行（Run121）、单输出流（Run122） | 首次加载仍失败 |
| 把 PAC 算法从 QARMA5 加 memo 换成 impdef（Run132–137，每种配置 3 轮，30 秒诊断下计时） | 第一次启动的初始化时间没有变化：QARMA5 为 3.515–3.791 秒，impdef 为 3.515–3.726 秒。第二次启动同样重叠（2.768–3.419 对 2.907–3.018 秒）。impdef 让 Safari 的整条启动路径快了 11% 到 21%（事后分析），但初始化窗口不受影响 |

**当前的绕过办法。** 补丁 0030/0031 是默认关闭的诊断：QEMU 在执行 WebKit 两处加载 3.0 秒常量的指令（site0、site1）时改为给出 30.0 秒，客体代码本身不被修改，生效前校验当前地址空间里的镜像 UUID 和指令窗口。Run125 使用首版（0030），有命中计数，但目标 Safari 仍在约 3 秒时被判无响应。0031 改为按运行时 PC 推算镜像基址（同一镜像在不同进程里的滑动地址不同），Run127–128 中 site0 命中、初始化完成。

这只说明多给一点时间初始化就能完成，不是性能达标：site1 从未命中，命中计数不能归属到具体进程，Run128 仍有 2 次 WebProcess 无响应通知（Run127 为 3 次）。说明见 [E06-webkit-timeout](../experiments/E06-webkit-timeout/README.md)。

### 2.3 音频

- **设备。** Run105 起声卡绑定并成为默认设备。Run117–118 中声卡没有注册；Run118 查明设备侧与驱动侧的 used ring 物理位置相差 4096 字节。把队列大小设为 512（补丁 0028，可选，默认仍为 64）后布局一致，Run119 起注册恢复。补丁 0027 另外修复了空的 JACK_INFO/CHMAP_INFO 查询被答复为"不支持"的问题。Run109 的单流配置下声卡也没有注册，但那是在 used ring 问题查明之前，不能说明必须双流。分析见 [used-ring 布局报告](reports/M3-VIRTIO-SOUND-USED-RING-LAYOUT.md)。
- **PCM 完整性时好时坏。** 完整：Run108、114、121。缺帧（部分被零块替换）：Run110、113、116、119、120、122、123、124。从 Run113 起的边界跟踪（补丁 0023）显示，缺帧在 QEMU 从发送队列取出数据时就已经存在，之后到 WAV 文件的路径没有再改变数据；原因未定。
- **没有验证的：** 麦克风输入、物理扬声器输出、时延。

### 2.4 网络

- 多数运行自动取得 DHCP 地址；Run91 和 Run105 中 en1 没有 IPv4，原因没有查明。
- Run126 的客体没有默认路由。只读检查发现控制队列（大小 64）的 used ring 与 avail ring 在物理上重叠。补丁 0032 增加了可选的控制队列大小；设为 512 后不再重叠，Run127–128 网络正常。这不能证明重叠就是 Run126 的唯一原因。

### 2.5 运行时与宿主侧问题

- **Vulkan `DeviceLost`：** Run90（进入桌面后）和 Run93（重开 Safari 后）；事后宿主的 Vulkan 检查通过。见 [Run90](reports/M3-RUN90-4G-DEVICE-LOST.md)、[Run93](reports/M3-RUN93-4G-HTTP-DEVICE-LOST.md)。
- **宿主 GPU 挂起：** Run111 中宿主 i915 报告 GPU HANG 并重置上下文，随后 DeviceLost、QEMU 中止。见 [Run111](reports/M3-RUN111-INTEL-GPU-HANG.md)。
- **宿主崩溃：** Run110 和 Run115 期间各一次。Run115 那次的固件记录里有一个值，按机器检查状态字解读是致命的内部奇偶校验错误；Run110 那次没有这样的值，原因未知。
- **宿主温度：** Run129 十分钟的运行里，CPU 封装温度平均 78.7 °C，最高 96 °C。Run131–137 每轮约 21 到 33 分钟，平均约 80 °C，最高 92–96 °C；连续约 2.7 小时的负载期间宿主没有复位。Run140（23 分钟）平均 80.5 °C，最高 92 °C；Run142（41 分钟）平均 77.1 °C，最高 91 °C。
- **控制器被会话带走：** Run130 在登录后被中断，原因是启动它的会话结束，控制器收到 SIGTERM 并终止了 QEMU。客体没有从内部关机；下一轮用 `--allow-unclean-storage` 启动，系统正常。
- **会话结束时 QEMU 也可能直接收到信号：** Run138 在启动后 662 秒、登录之前被终止，这次 QEMU 自己收到了信号（`SHUTDOWN reason=host-signal`），控制器没来得及收尾。下一轮启动被拒绝（Run139 没有产生运行），要先执行 `recover-abandoned.py --run 标签` 清理临时链接，再用 `--allow-unclean-storage` 启动（Run140，系统正常）。Run141 在启动后 672 秒同样被中断，这次控制器自己收到 SIGTERM 并完成了收尾，Run142 加 `--allow-unclean-storage` 后正常启动。
- **客体关机被记成宿主退出：** Run133、135、137 的客体都正常关机、QEMU 以 0 退出，但控制器的一次周期请求正好落在 QEMU 关闭 QMP 之后、进程退出之前，发送失败，收尾逻辑没有读到缓冲区里的关机事件，记成了 `teardown=terminate`。原因和修正见 [PAC 算法对比](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.md)。
- **原生窗口停止更新：** Run112 后期原生窗口超过 700 秒没有更新，而 QMP 和内核仍在运行。
- **锁屏解锁失败：** Run89 出现 AppleVPKeyStore `unlock failed (-5)`；Run102 清空密码框后慢速输入解锁成功，原因没有证明。
- **采集器结果为 false：** 多数是因为客体关机后无法再做最终采集，错误列表为空，不代表运行失败。

### 2.6 运行一览

"首载失败"指全新 Safari 的 GPU 进程被看门狗终止。Run113–128 没有单独的报告，"记录"链接指向本机 `runs/` 下的应用观察记录（不入库）；对照分析见 [PAC memo 应用对照](reports/M3-PAC-MEMO-APP-COMPARISON.md)和 [used-ring 布局报告](reports/M3-VIRTIO-SOUND-USED-RING-LAYOUT.md)。

| 运行 | 做了什么 | 结果 | 详情 |
|---|---|---|---|
| 70、71 | 3600 秒空闲观察；1200 秒定向启动采集 | 没有触发 Safari，无新结论 | [70](reports/M3-SAFARI-GPU-FOLLOWUP-RUN70.md)、[71](reports/M3-SAFARI-GPU-TARGETED-RUN71.md) |
| 73–77 | native-window 接管（旧构建） | 73–75、77 以 `FramebufferFetch` 中止；76 是无 native-window 的对照 | — |
| 81、82 | 无 native-window 的登录、桌面、Safari | 界面截图成立；81 访问外网失败（原因未区分），82 的采集器因 QEMU 进程身份变化而失败 | [81](reports/M3-SAFARI-GPU-TAKEOVER-RUN81.md) |
| 83 | BGRA8 新构建，NVIDIA | 正常启动、登录，原生像素通过 | [83](reports/M3-BGRA8-NATIVE-VALIDATION-RUN83.md) |
| 85 | Intel，网络与 Safari | DHCP/DNS/HTTPS、暖启动 Safari、客体关机通过；首载失败；PAC 约占 vCPU 采样的 42.61% | [85](reports/M3-SAFARI-GPU-INPUT-RUN85.md) |
| 86、87 | PAC memo 关/开（同一二进制） | PAC 采样 CPU 时间下降 42.03%；首载失败 | [对照](reports/M3-PAC-MEMO-APP-COMPARISON.md) |
| 88 | 音频组件枚举计时 | 两次枚举耗时 24.16 / 20.25 秒，随后仍有 GPU 超时 | [85](reports/M3-SAFARI-GPU-INPUT-RUN85.md) |
| 89 | 8 GiB 内存 | 全新 Safari 的 HTTP 页面加载成功（首次有意义绘制约 19.4 秒）；之后锁屏解锁失败 | [89](reports/M3-RUN89-8G-MEMORY-CONTROL.md) |
| 90 | 4 GiB | 进入桌面后 DeviceLost | [90](reports/M3-RUN90-4G-DEVICE-LOST.md) |
| 91 | 4 GiB，本地 `file://` 页面 | 首次加载成功（首次有意义绘制约 3.9 秒），客体关机；该轮没有 IPv4 | [91](reports/M3-RUN91-4G-LOCAL-FRESH.md) |
| 92 | 网络 | 自动 DHCP、HTTP 上传、客体关机；未启动 Safari | [92](reports/M3-RUN92-NETWORK-DHCP.md) |
| 93 | 4 GiB，HTTP 页面 | 页面成功；重开 Safari 后 DeviceLost | [93](reports/M3-RUN93-4G-HTTP-DEVICE-LOST.md) |
| 94–97 | HTTP 页面对照：4 GiB（94–96）、8 GiB（97） | 页面 RGB 计数一致；94 客体关机，95–97 由宿主结束 | [94](reports/M3-RUN94-4G-HTTP-FRESH-CONTROL.md)、[95](reports/M3-RUN95-4G-HTTP-REPEAT.md)、[96](reports/M3-RUN96-4G-HTTP-REPEAT-BOUNDED.md)、[97](reports/M3-RUN97-8G-HTTP-MATCHED-CONTROL.md) |
| 98、99 | 加入看门狗归档工具 | 工具自身出问题（抢占焦点；宿主服务未监听），无结论 | [98](reports/M3-RUN98-WATCHER-HARNESS-LOSS.md)、[99](reports/M3-RUN99-WATCHER-SERVICE-MISSING.md) |
| 100 | 客体文件交换；全新 Safari | 取回 3 份客体归档；复现 8 次 GPU 看门狗终止 | [100](reports/M3-RUN100-WATCHDOG-ARCHIVE.md) |
| 101、102 | — | 运行被中断；102 解锁成功并到达桌面 | [101](reports/M3-RUN101-INTERRUPTED.md)、[102](reports/M3-RUN102-DESKTOP-INTERRUPTED.md) |
| 103 | 采样对照 | 4 次看门狗请求；记录了采样工具的局限；客体关机 | [103](reports/M3-RUN103-EARLY-SAMPLE-CONTROLS.md) |
| 104 | 独立的编解码器查询 | 查询本身很快；不采样时仍有 6 次看门狗请求 | [104](reports/M3-RUN104-ISOLATED-CODEC-QUERIES.md) |
| 105 | 启用 VirtIO 声卡 | 设备绑定并成为默认设备；该轮没有 IPv4 | [105](reports/M3-RUN105-VIRTIO-SOUND-BINDING.md) |
| 106 | 关闭音频的对照 | DHCP、HTTP、客体关机通过 | [106](reports/M3-RUN106-AUDIO-OFF-NETWORK-CONTROL.md) |
| 107 | 音频与网络共存 | 共存成立；首载失败（错误页） | [107](reports/M3-RUN107-AUDIO-NETWORK-COEXISTENCE.md) |
| 108 | `afplay` PCM | 两轮都逐字节一致；首载失败（3 次看门狗） | [108](reports/M3-RUN108-PCM-BIT-EXACT.md) |
| 109 | 单流音频配置；stackshot 工具 | 声卡未注册；stackshot 采集可用 | [109](reports/M3-RUN109-SINGLE-STREAM-STACKSHOT-CONTROLS.md) |
| 110 | 恢复双流；GPU 初始化 stackshot | PCM 缺 764 帧；GPU 主线程在等音频 SandboxHelper；之后宿主意外重启 | [110](reports/M3-RUN110-GPU-INITIALIZATION-STACKSHOT.md) |
| 111 | — | 宿主 i915 GPU 挂起，QEMU 中止 | [111](reports/M3-RUN111-INTEL-GPU-HANG.md) |
| 112 | stackshot 解码 | 看门狗触发时 SandboxHelper 仍在启动 | [112](reports/M3-RUN112-GPU-SANDBOXHELPER-STARTUP.md) |
| 113、114 | PAC memo 64 项对 1024 项（0023、0024） | 命中率约 66% → 84%；首载都失败；113 PCM 缺帧，114 完整 | 记录、记录 |
| 115 | 0025 配置 | 被第二次宿主重启打断 | 记录 |
| 116 | PAC VA 投影（0025） | 首载失败；PCM 缺帧 | 记录 |
| 117 | TB 查找指针（0026） | 声卡未注册；首载失败 | 记录 |
| 118 | 空查询修复（0027） | 声卡仍未注册；查明 used ring 错位 4096 字节 | 记录 |
| 119 | 声卡队列 512（0028） | 注册恢复；PCM 缺帧；首载失败 | 记录 |
| 120 | vCPU 绑核 | PCM 缺帧；首载失败 | 记录 |
| 121 | 内核观察器只在启动期运行 | PCM 两次都完整；首载失败 | 记录 |
| 122 | 单输出流 | 注册成功；PCM 缺帧；5 个 GPU 进程全部被看门狗终止 | 记录 |
| 123、124 | PAC memo 16384 项对 1024 项（0029） | 命中率约 89% 对 83%；首载与 PCM 都失败；124 由宿主结束 | 记录、记录 |
| 125 | 超时诊断首版（0030） | 有命中计数，但目标 Safari 仍在约 3 秒时被判无响应 | 记录 |
| 126 | 超时诊断按运行时基址（0031） | 客体没有默认路由，未能试验；发现控制队列重叠 | 记录 |
| 127 | 网络控制队列 512（0032） | 网络恢复；Safari GPU 初始化 4.081 秒完成，页面显示 | 记录 |
| 128 | 固定 WebGL2 读回 | 两份读回逐字节通过；初始化 3.781 秒；客体关机 | 记录 |
| 129 | 恢复实验后的试运行：原样启动、登录、关机（普通 3 秒期限） | 150 秒到登录窗口；客体关机，QEMU 以 0 退出；宿主温度最高 96 °C | [129](reports/M3-RUN129-SMOKE.md) |
| 130 | PAC 算法对比的演练（QARMA5，30 秒诊断） | 登录后被中断（控制器收到 SIGTERM），没有样本，客体未正常关机 | [对比](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.md) |
| 131 | 重做演练（QARMA5，允许重用未正常关机的磁盘） | 系统正常启动；与基线同条件的初始化 4.866 和 3.802 秒；客体关机 | [对比](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.md) |
| 132 | QARMA5 加 memo，第 1 轮 | 第一次启动 3.791 秒，第二次 3.348 秒 | [对比](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.md) |
| 133 | impdef，第 1 轮（已安装系统第一次在 impdef 下启动） | 第一次启动 3.726 秒，第二次 2.907 秒 | [对比](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.md) |
| 134 | QARMA5 加 memo，第 2 轮 | 第一次启动 3.515 秒，第二次 3.419 秒 | [对比](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.md) |
| 135 | impdef，第 2 轮 | 第一次启动 3.693 秒，第二次 3.018 秒 | [对比](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.md) |
| 136 | QARMA5 加 memo，第 3 轮 | 第一次启动 3.727 秒，第二次 2.768 秒 | [对比](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.md) |
| 137 | impdef，第 3 轮 | 第一次启动 3.515 秒，第二次 3.009 秒；结论：PAC 算法不是决定因素 | [对比](reports/M3-PAC-ALGORITHM-GPU-INIT-TIMING.md) |
| 138 | 宿主 CPU 采样（QARMA5 加 memo，4 GiB） | 启动后 662 秒、登录之前被会话结束打断，没有试验；11 分钟的采样验证了工具，登录窗口阶段平均 2.3 个 vCPU 在忙 | [宿主 CPU](reports/M3-GPU-INIT-HOST-CPU-USE.md) |
| 139 | 8 GiB 内存对照 | 没有产生运行：控制器因 Run138 的临时链接未清理而拒绝启动 | [内存](reports/M3-RAM-SIZE-GPU-INIT-TIMING.md) |
| 140 | 8 GiB 内存对照，带宿主采样和日志窗口导出 | 第一次启动 3.444 秒，第二次 3.061 秒，内存大小没有明显影响；初始化窗口内 8 个 vCPU 约 90% 的时间全忙；SandboxHelper 一段是进程启动的开销；客体关机被正确记录 | [内存](reports/M3-RAM-SIZE-GPU-INIT-TIMING.md)、[宿主 CPU](reports/M3-GPU-INIT-HOST-CPU-USE.md) |
| 141 | 安静客体试验（QARMA5 加 memo，4 GiB） | 启动后 672 秒被中断，没有试验；饱和阶段的两次阻塞采样显示 vCPU 线程 21%–26% 的时间在等全局锁和独占段 | [安静客体](reports/M3-QUIET-GUEST-GPU-INIT-TIMING.md) |
| 142 | 重做安静客体试验 | 后台负载在启动后约 22 分钟消退；安静后第一次启动 3.249 秒，第二次 2.588 秒；窗口内仍然饱和；客体关机 | [安静客体](reports/M3-QUIET-GUEST-GPU-INIT-TIMING.md) |
| 143 | 每核同步触发源计数（0033，`--sync-stats`）与阻塞采样 | 第一次启动 3.704 秒，第二次 2.818 秒；每秒获取 `bql` 41–46 万次、全核独占段 6516–7721 次，Safari 启动期 27.3% vCPU 时间被阻塞；定位前四大根因（`CNTVCT_EL0` 占读锁 96%、屏蔽中断与 `EXITTB` 空取锁占 29%、广播 `TLBI` 外层重取锁与独占段、PAC `HVC` 持锁堆分配 SHA-256）；客体关机 | [同步触发源](reports/M3-QEMU-SYNC-SOURCES.md) |
| 144 | 消除前四大 `bql` 争用补丁（0034–0037）实测对照 | `bql` 获取率降 77%–82%，等 `bql` 占比降至 0.4%–1.3%（-90% 至 -98%）；Safari 端到端启动快约 43%（105→60 秒、76→43 秒），GPU 初始化降至 **3.336 秒**和 **2.612 秒**（均创负载下新低，0 次 WebProcess 无响应）；剩余主阻塞为广播 `TLBI` 逐条 `start_exclusive`（11.1%）；客体关机 | [同步触发源](reports/M3-QEMU-SYNC-SOURCES.md) |
| 145 | 广播 `TLBI` 按 `DSB` 聚合与省去 EL1&0 `ERET` 锁（0038–0039） | 饱和期 vCPU 运行占比跃升至 **96.6%**（总阻塞仅 **3.4%**，`bql` 0.2%，独占段降 75% 至 2.8%，`bql` 获取率累计降 90%）；二次启动创全项目最快 **2.431 秒**（首次启动在后台峰值期为 3.798 秒，0 次无响应）；客体关机 | [同步触发源](reports/M3-QEMU-SYNC-SOURCES.md) |
| 146 | **去掉超时诊断**，安静客体实测原版期限与固定 WebGL2 读回 | 启动后 1123 秒进入安静（提前 6.5 分钟）；**无 `--webkit-timeout-diagnostic` 下首次冷启动 Safari 初始化成功（GPU 看门狗退出 0 次，3.219 秒），固定 WebGL2 双缓冲读回逐字节通过**；二次启动 **1.912 秒**（首次破 2 秒）；客体关机 | [同步触发源](reports/M3-QEMU-SYNC-SOURCES.md) |
| 147 | **无诊断独立冷启动重复性与 `0039` 符号级 `perf` 剖析** | 启动后 1335 秒安静；**H1 未通过（跨冷启动 0 看门狗仅 1/2）**：首个 GPU 在 `3.008 s` 被原版看门狗终止（无 Run146 的 0.22 秒投递裕量），经 3 次重试后第 4 个 GPU 在无诊断下 `1.661 s` 完成初始化，**WebGL2 双缓冲逐字节读回仍通过（2/2）**；`perf` 证实 **80.3%–81.1% P 核周期消耗在 QEMU C 函数**（`[JIT]` 仅 13.5%–14.5%），定位 `CF_PCREL` 跳转缓存全清空、16 KiB 页触发 `large_page_mask` 全表清空、PAuth 参数重算/标量 QARMA5 与 SME `do_fmopa_h` 重复拆包四大根因；客体关机 | [吞吐剖析](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.md) |
| 148 | **TCG 执行吞吐补丁 `0040–0043` 无诊断实测与 `perf` 复检** | 启动后 **958 秒**即安静（较 Run147 提前 6.3 分钟）；**`[JIT]` 占比跃升 71.6%（`13.52% → 23.20%`）**，SME 与 16 KiB 全表清空消除；**三轮无诊断启动全部 0 看门狗一次通过**：首次冷启动创最快 **`3.180 s`**（`constructor->proxy` 从 `23.5 s` 降至 `9.12 s`，**WebGL2 双缓冲读回 `bit_exact=true`**），二次无干扰启动 **`1.620 s`**，三次并发 `perf` 启动 `2.056 s`；定位剩余 `TTBR0_EL1` 切换清空跳转缓存与内核/物理页表 TLB 为下一轮靶点；客体关机 | [吞吐剖析](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.md) |
| 149 | **持久 `CPUJumpCache` 与选择性 `TTBR0` 刷新补丁 `0044–0045` 无诊断实测** | `tcg_flush_jmp_cache` 与 `vmsa_ttbr_write` 归零，Safari 启动期 `tb_htable_lookup` 大降 62.7%（`14.43% → 5.38%`），饱和期 `[JIT]` 升至 **`24.12%`**（累计 +78.4%）；**开机首次冷启动无诊断一次通过并首次严格击穿 `3.000 s` 硬期限达 `2.734 s`（0 看门狗退出，不靠运行循环裕量，`entry->registrar` 核秒减 37.4%，WebGL2 双缓冲读回 `bit_exact=true`）**；二次无干扰 `1.679 s`，三次并发 `perf` `1.856 s`（宿主平均温度降至 `71.2 °C`）；客体关机 | [吞吐剖析](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.md) |
| 150 | **`+720 s` 后台峰值负载窗口无诊断首次冷启动实测（`0045`）** | 不等待安静直接在 `+720 s`（`vcpu_mean_busy = 6.32`）触发：**无 `--webkit-timeout-diagnostic` 下峰值期首次冷启动一次通过（0 看门狗退出，`3.254 s`，较同窗口 Run145 的 `3.798 s` 快 14.3%，WebGL2 双缓冲读回连续 5/5 `bit_exact=true`）**；`+1020 s` 二次启动 **`1.662 s`**（0 看门狗、0 Web 无响应，较 Run145 快 31.6%）；Run148–150 连续 **8/8 次**无诊断启动 0 看门狗通过；客体关机 | [吞吐剖析](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.md) |
| 151 | **`0045` 主线完整签名原生还原复验与新系统盘基线（2 vCPU QARMA5）** | 全新空白 `128 GiB` / `32 MiB` 目标盘一次跑通 DFU → iBSS → LocalPolicy → iBEC Stage2 → 五项恢复载荷 → XNU 恢复内核（`bootx`→USB 激活 **`17.334 s`**，较 `-43` 的 `118.704 s` 提速 **6.85×**）→ `idevicerestore` **`Status: Restore Finished`**（`0x68B` 封印、`0x669` 快照、`0x642` 干净卸载全部 `result=0`）；**总耗时 `2,613.93 s`（`43.57 min`），较 `-53`（`6,706.78 s`）提速 `2.57×`（-61.0%）**；宿主温度均值 `67.6 °C`（最高 `93 °C`） | 原生还原复验 |
| 152 | **2-路内联跳转缓存、PAuth 快路径与 BTI 代码 TLB 补丁 `0046–0048` 实测** | `arm_stage1_mmu_idx`/`is_guarded_page` 归零、`probe_access_internal` 降 41.9%、`qht_lookup` 降 32.3%；但 `256 KiB` 二级 QARMA 表引入 `3.93%` L2 缺失净亏 `2.63%`；`+720 s` 首轮经 1 次看门狗自动恢复并通过 WebGL2 读回（6/6），`+1020 s` `1.795 s`，并发 `perf` `1.725 s`；客体关机 | [吞吐剖析](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.md) |
| 153 | **移除二级表、端到端 `64 KiB` PAuth 叶函数缓存 + `PAC→AUT` 逆向预填充补丁 `0049` 实测** | `[JIT]` 升至 `24.82%`，PAuth 全族自占从 `21.1%` 压至 `12.1%–13.4%`（`pauth_op_miss_aut` 仅 `0.44%`）；三轮无诊断全部 0 看门狗通过（`+720 s` `3.314 s` + WebGL2 7/7，`+1020 s` **`1.659 s`** 含 `entry->registrar = 0.760 s`，三次 `2.185 s`）；定位饱和期 `helper_gvec_fmla_idx_h -> float16_muladd_scalbn` 占 `9.18%` 及 `1.0 MiB` 跳转缓存 L1.5 缺失；客体关机 | [吞吐剖析](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.md) |
| 154 | **`0050` 主线 2.10 小时连续驻留、锁屏步进解锁与正常关机验证** | 因宿主会话暂停跨过 `+720 s` 窗口而转作长时驻留检验：唤醒锁屏后用 `guest_ui.py` 新增 `--hold 0.35 --gap 0.35` 步进输入一次解锁成功（避免 16 槽 HID 队列溢出），连续运行 **`7,550.7 s`（2.10 小时，累计 `191.38B` 次 PAuth、`89.06%` 命中率、时钟跨度 `1.5 ms`、均温 `67.3 °C`）** 并正常关机 | [吞吐剖析](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.md) |
| 155 | **硬件 `F16C+FMA` FP16 乘加与 `512 KiB` 256 页桶跳转缓存补丁 `0050` 无诊断峰值测试** | `float16_muladd` 从 `9.18%` 降至 **`< 0.05%`**，`helper_lookup_tb_ptr` 降 `-6.29%`，Safari 启动期 `[JIT]` 创 **`25.71%`** 纪录；后台风暴在 `+660 s` 提前消退，均温骤降 `-7.7 °C` 至 **`68.6 °C`**（最高 `92 °C`）；**`+720 s` 首次冷启动 `2.941 s`（`< 3.000 s`，0 看门狗）+ WebGL2 8/8 通过**，`+1020 s` `1.803 s`，**并发 `perf` 第 3 轮创全项目最快 `1.417 s`（超越 `impdef`）**；客体关机 | [吞吐剖析](reports/M3-TCG-THROUGHPUT-AND-ORIG3S.md) |
| 156 | **`0050` 主线跨子系统综合扩展验证（多通道 WebGL2/Metal、原生应用与框架、双轮音频、APFS 干净卸载）** | **`+720 s` 无诊断首次冷启动复现 `< 3.000 s` 达 `2.163 s`（0 看门狗，2/2 跨冷启动）**；**多通道 WebGL2/Metal 双缓冲 98,304 字节 0 误差通过**；**3 款内置 GUI 应用 + `jsc`/`sips`/`sqlite3`/`plutil`/`gzip` 7/7 通过**；**双轮 `afplay` PCM `full_tone_bit_exact=true` 且 `VSNDPCM1` 0 个零块欠载**；只读盘面检查点（主容器 `xid 88420 == 88420`、`ISC` `555 == 555`）与 `nx_mount` 日志双向证明主系统容器干净卸载 | [扩展验证](reports/M3-EXPANDED-VALIDATION-RUN156.md) |

## 3. ARM 平台（M1 / E06，自制固件，不含 Apple 代码）

| 契约 | 结果 | 报告 |
|---|---|---|
| VMApple 配置、UART、PSCI | 1/2/4 vCPU 通过 | [M0-RESULTS](reports/M0-RESULTS.md) |
| 定时器/GICv3 与 16 KiB MMU | 10/10（含 4 个预期负例） | M1-TIMER-MMU-BOOT-AUDIT |
| 24 MHz 精确计时 | 修正旧实现约快 1.6% 的问题；默认关闭的 `x-cntfrq-exact` | [M1-EXACT-CLOCK-RESULTS](reports/M1-EXACT-CLOCK-RESULTS.md) |
| 双 vCPU MMU/ASID/TLBI/原子 | 14/14 | [M1-SMP-MMU](reports/M1-SMP-MMU.md) |
| EL0 上下文 | 完整 Q0..Q31、FPCR/FPSR/TLS、PAC 与 IRQ 抢占，双 vCPU 迁移和按需 FP 恢复，均含负例 | [M1-EL0-SIMD32](reports/M1-EL0-SIMD32.md) |
| PAC HVC 软件模型 | 补上 Apple PAC HVC 缺口的默认关闭模型；不是 Apple 加密的等价实现 | [M1-PAC-HVC-RESTORE-TRANSPORT](reports/M1-PAC-HVC-RESTORE-TRANSPORT.md) |
| PAC 模拟开销 | QARMA5 每次 PACIBSP/RETAB 额外开销经 0018/0020/0021 从约 550 ns 降到约 214 ns，经 0042（VA 缓存 + SSSE3 `pshufb` QARMA5）进一步降至 **57.7 ns**（3.71 倍提速）；只是自制微基准，macOS 效果由实际启动运行单测 | [E06-pauth-hvc](../experiments/E06-pauth-hvc/README.md) |
| AES、AVP RTC、CTRR | 普通 supplied-key AES DMA/IRQ 通过；RTC 与 CTRR 合同经自制 guest 和原版启动局部验证，不等于硬件语义等价 | M2-NATIVE-RESTORE-CONTRACT |

## 4. 传输与存储（E05）

| 项目 | 结果 | 报告 |
|---|---|---|
| BDIF USB | 帧、队列、连接生命周期修复（0006–0009）；18/18 传输场景，1,049,600 B 背压逐字节一致 | [M1-BDIF-USB-TRANSPORT](reports/M1-BDIF-USB-TRANSPORT.md) |
| BDIF 块设备 | 读写/flush/越界等 9/9；容量改为后端实际大小（0017，修复 64 GiB 常量对 128 GiB 后端） | M2-IBSS-RECOVERY |
| 内核 VirtIO USB 与 restored | 0015 的普通传输经原版驱动和 usbmux/restored 会话完成整次还原 | M2-PAC-HVC-CTRR-LIVE-FOLLOWUP |
| VirtIO 网络 | 0019 默认关闭，显式开启网络时把 PCI cap 改为升序，原版驱动随后就绪 | [E05-installed-boot](../experiments/E05-installed-boot/README.md) |

## 5. 图形（E01–E03 / E11）

| 项目 | 结果 | 报告 |
|---|---|---|
| 宿主 Vulkan（E02） | Intel 与 NVIDIA 的 compute 和离屏渲染回读通过 | [M0-RESULTS](reports/M0-RESULTS.md) |
| 跨 GPU 共享（E03） | 一种 host-visible DMA-BUF 方案双向失败，只否定这一种方案 | 同上 |
| 跨 ISA 协议（E11） | ARM guest 16 KiB 页与宿主 4 KiB 页分离（Reims 0002–0005）；Reims Core 串行测试 ARM16K 2294/0、x86 2300/0 | [M1-ARM-REIMS](reports/M1-ARM-REIMS.md) |
| 自制 guest 上的 GPU | 两块 GPU 上的 compute/draw、完成中断、IOSurface 生命周期、原生窗口呈现、USB HID 按键到新帧，均含负例 | [E11](../experiments/E11-arm-reims/README.md) |
| 原版 macOS 渲染 | 历史设置助理、桌面、Safari、TextEdit 显示已证；Run83 新 BGRA8 构建的 NVIDIA 原生登录/桌面像素已证 | 第 1 节第 11–15 行及第 2 节 |

## 6. Apple 组件的静态与离线分析

- **签名链：** IM4M 的 RSA-4096 签名链到 Apple 根证书（43 项测试），证书约束扩展解析为 13 条规则（56 项测试）。见 M2-IM4M-OFFLINE-VERIFICATION、M2-CERTIFICATE-CONSTRAINTS。
- **SystemOS 与 Virtualization.framework：** 解析密封 APFS 并恢复 Virtualization 的原始 `__TEXT`（2,979,720 B）。见 M2-APPLE-FRAMEWORK-FILESYSTEM-AUDIT。
- **AUX 存储：** 静态确认了文件格式、UID 密钥生成/包装、配置 schema 和 XPC 消费路径（`M2-VZ-*` 系列报告）。原版 VZ 初始化从未执行，实际还原也不依赖有效 AUX；见 M2-NATIVE-AUX-READINESS。

## 7. 源码与补丁

| 工作树 | 固定基线 | 补丁 | 用途 |
|---|---|---|---|
| `sources/qemu-bdif-usb` | qemu-reims-vgpu `1a72df9` | 0003、0005、上游 USB 参考补丁、0006–0050 | 主线：原版固件、还原与桌面 |
| `sources/reims-arm-guest` | reims-vgpu `86d4921` | Reims 0001–0007 | ARM 16 KiB guest 图形库 |
| `sources/metal2vulkan-research` | `ecb80e0` | metal2vulkan 0001 | FramebufferFetch/source-over |
| `sources/qemu-reims-arm` / `qemu-clock-exact` | qemu-reims-vgpu `1a72df9` | 0003+0004 / 0003+0005 | 早期图形主线 / 精确计时实验 |
| `sources/qemu-headless`、`qemu-portable`、`reims-build` | 各自基线 | 0001 / 无 / Reims 0001 | 无 GPU 对照、诊断、x86 图形库 |

当前主线二进制：`build/qemu-bdif-usb/qemu-system-aarch64`，SHA-256 `e6f7b2cb32df3779531cf1b93612a67d8e1fa350c8b894c19d0c9e71a60f4a51`，补丁链记录到 0050；见构建记录。

**补丁 0022–0050（Run86 起新增）：**

| 补丁 | 内容 | 在实际系统中的结果 |
|---|---|---|
| 0022 | 每 vCPU 的 PAC 精确 memo（默认关闭） | PAC 采样 CPU 时间下降 42.03%（Run86/87）；没有证明应用提速 |
| 0023 | virtio-snd PCM 边界跟踪（默认关闭） | 用于定位缺帧出现的位置（Run113 起） |
| 0024 | PAC memo 容量与统计 | 64 项与 1024 项对照（Run113/114） |
| 0025 | PAC VA 投影 | 微基准每次调用 8.245 → 3.755 ns；Run116 首载仍失败 |
| 0026 | TB 查找改用状态指针 | 没有测到性能收益（Run117） |
| 0027 | virtio-snd 对空 JACK_INFO/CHMAP_INFO 查询的合法应答 | 查询成功，但声卡仍未注册（Run118） |
| 0028 | virtio-snd 队列大小可选（默认 64） | 设为 512 时布局一致、注册恢复（Run119） |
| 0029 | 允许 PAC memo 16384 项 | 命中率上升，首载仍失败（Run123/124） |
| 0030 | 两处代码点的 30 秒超时诊断（默认关闭） | 首版在 Run125 没有使目标 Safari 通过 |
| 0031 | 超时诊断按运行时 PC 推算镜像基址 | Run127–128 site0 命中，初始化完成 |
| 0032 | virtio-net 控制队列大小可选（默认 64） | 设为 512 时网络正常（Run127–128） |
| 0033 | 每核无锁 TCG 同步触发源计数（默认关闭） | Run143 分解每秒 41–46 万次 `bql` 与 6516–7721 次独占段触发源 |
| 0034–0037 | `CNTVCT_EL0` 无锁读、跳过屏蔽中断与 `EXITTB` 锁、无锁栈 HMAC PAC `HVC` 含 64 项缓存、无锁排空 `TLBI` 队列 | Run144 `bql` 阻塞降 90%–98%，Safari 启动快约 43%，GPU 初始化降至 3.336 / 2.612 秒 |
| 0038–0039 | 广播 `TLBI` 按 `DSB` 聚合及省去逐条 `hflags`/TB 截断、省去 EL1&0 `ERET` 锁 | Run145 饱和期运行占比升至 96.6%（总阻塞 3.4%）；Run146 无诊断单次通过（3.219 / 1.912 秒），Run147 证伪跨冷启动 0 看门狗稳定性（1/2）并定位剩余 80%–81% C helper 热点 |
| 0040–0043 | `CF_PCREL` 跳转缓存按页内偏移先读后写失效与 256 页桶扩容、`cputlb` 常规 16 KiB 页子页精确失效（含修正 `tlb_flush_range_locked` 单位错配）与无 EL2 内联快路径、PAuth VA 参数缓存与 SSSE3 `pshufb` QARMA5、SME `do_fmopa_h` 预拆包 | 裸机 QARMA5 `PACIBSP/RETAB` 降至 **57.7 ns**（-73.0%）；Run148 饱和期 `[JIT]` 占比跃升 71.6%（`13.5% → 23.2%`），安静提前至 958 秒，三轮无诊断全部 0 看门狗通过（`3.180 s` / `1.620 s` / `2.056 s`） |
| 0044–0045 | 宿主 RAM 地址标记的跨上下文持久 `CPUJumpCache`（TLB 失效不再清空跳转缓存，上下文切换共享缓存页绕过全局 `qht_lookup_custom`）+ `TTBR0_EL1` ASID 切换保留 `ARMMMUIdx_Phys_NS` 与 `TTBR1_EL1` 内核高半区 SoftMMU TLB | Run149 消除 `tcg_flush_jmp_cache` 与 `vmsa_ttbr_write` 热点，`tb_htable_lookup` 大降 62.7%，饱和期 `[JIT]` 达 `24.12%`；**安静客体首次冷启动无诊断首破 `3.000 s` 硬期限达 `2.734 s`**；**Run150 在 `+720 s` 后台峰值窗口无诊断首次冷启动一次通过（0 看门狗退出，`3.254 s`，二次 `1.662 s`）** |
| 0046–0050 | 64B 对齐 `512 KiB` 2-路 256 页桶内联跳转缓存（0046/0049/0050）、PAuth 近端状态与 SSSE3 常量预拆包（0047）、BTI `is_guarded_page` 代码 TLB 快路径（0048）、每核 `64 KiB` 端到端 PAuth 叶函数缓存含 `PAC→AUT` 逆向预填充（0049）、以及硬件 `F16C+AVX2+FMA` 逐位精确 `float16_muladd_scalbn` 与 `gvec_fmla_idx_h` 快路径（0050） | `float16_muladd` 从 `9.18%` 降至 **`< 0.05%`**，PAuth 总自占降 ~40%，`[JIT]` 升至 **`25.71%`**，后台风暴提前 6+ 分钟消退、均温降至 `68.6 °C`；**Run155/156 在 `+720 s` 窗口无诊断冷首次启动连续两轮击穿 `< 3.000 s`（`2.941 s`、`2.163 s`），新鲜复启动创 `1.417 s` 全项目纪录，Run154 验证 2.10 小时连续稳定运行** |

这期间主线二进制随补丁多次重建。

运行器在这期间新增了几个省略时不改变行为的开关（`--virtio-sound`、`--sound-streams`、`--vcpu-affinity`、`--kernel-observation boot-only`、`--guest-files`），用法见 [installed-boot README](../experiments/E05-installed-boot/README.md)。

### 7.1 2026-11-08 维护检查

- `make installed-offline-tests`：11 套、257 个测试方法，全部通过、无跳过；不启动 QEMU、浏览器、客体或外部网络。结果
- 5 个组件的补丁链重建通过（改动文件数：Reims 21、metal2vulkan 5、qemu-reims 6、exact-clock 9、qemu-bdif-usb 42）；43 条二进制、配置、库和补丁的身份哈希一致，主线二进制没有重新链接。重建、身份
- 清理：只删除了 4 个来源明确的历史 kernel-usb 会话目录和 8 个硬链接别名；每个镜像保留 90 个 private 链接，没有回收镜像数据块；2 个来源不明的 `kernel-usb` 目录保留。清理记录
- 2026-11-01 的 8 个工作树重建检查保留为历史基线（`runs/maintenance-20261101/`）。空白宿主的全量重建还没有测试。

这些是源码和身份层面的证据，不是新的运行结果。

### 7.2 2026-11-12 公开快照的全新克隆重建

从主仓库 7523547 导出公开快照，用它的 git bundle 在本机做了一份全新克隆：没有 `sources/`、`build/`、`runs/` 和 `private/`，Cargo 缓存也是空的。在这份克隆里依次执行的 26 个 make 目标全部通过，共用约 22 分钟：

- `fetch-sources`：三个上游按 `sources.lock.json` 的固定版本从 GitHub 拉取，外部 USB 参考补丁的 SHA-256 一致。
- 10 个构建目标（`build-reims-arm64-vulkan`、`build-reims-vulkan`、`build-qemu-bdif-usb`、`build-reims-qemu-arm16k`、`build-reims-qemu-arm`、`build-qemu-clock-exact`、`build-armvirt`、`build-vmapple-headless`、`build-reims-qemu-x86`、`build-shared-cache-identity`）和 `check-research-patches`（5 个组件的补丁链重建通过）。打完补丁的源码树与本仓库实际使用的工作树逐字节一致，只差构建产物和指向检出位置的符号链接。
- 13 个不含 Apple 代码、使用自写客体的自检目标（`smoke`、`reims-pci-realize`、`reims-arm16k-smoke`、`reims-arm-compare`、`reims-gpu-compute`、`bdif-usb-tests`、`platform-contracts`、`smp-mmu-tests`、`el0-context-tests`、`pauth-model`、`clock-functional`、`config-mailbox-tests`、`avp-rtc-tests`），以及快照自带的 14 套离线测试（276 项；缺少 `private/` 时按设计跳过 2 项；检出路径 41 字节）。

此前的尝试暴露了三个只在全新检出里出现的问题，都已修复：`check-research-patches.py` 写报告时 `runs/` 还不存在；`build-reims-qemu-arm.sh` 检查了全部 5 个工作树，其中两个要等各自的构建运行后才存在（7523547）；检出路径超过 37 字节时，存储保留测试的 UNIX 套接字路径超过 107 字节的上限（cc32608）。

限制：用的是同一台宿主、同样的系统软件包和 Rust 工具链，另一台空白机器上的重建还没有测试；二进制没有逐字节复现，因为构建会把检出路径写进二进制。原始日志在 `runs/maintenance-20261112/v1-fresh-clone/`。

### 7.3 2026-11-12 与上游的对照

- **Reims 与 metal2vulkan：** 上游的开发在 `development` 分支（2026-11-11 的 `c410a064b` 与 `43d230817`），分别比 `master` 多 2067 和 3172 个提交。我们的 8 个补丁都已不再需要：reims 0001 只锁定本地构建；0002–0005 的 16 KiB 客体页处理已被上游重写进 `crates/reims-vgpu-vulkan/src/core/memory/map.rs`；0006 的可共享临时内存，上游的 Linux `allocate_aliasable` 做的是同一件事；metal2vulkan 0001 与 reims 0007 的 source-over 混合，已被上游在着色器里按 Metal 的方式计算任意混合所覆盖。这个结论来自阅读代码和文档，没有用本项目的客体在上游新版本上实测。
- **QEMU 主线（11.1.90）：** 0013 已在上游修复。vmapple 能力链表按降序链接（0014、0019）、virtio-snd 对零数量 JACK_INFO/CHMAP_INFO 回 `NOT_SUPP`（0027）及四个队列固定为 64 项（0028）、virtio-net 控制队列固定为 64 项（0032）仍然存在。

## 8. 尚未完成

- **系统：** Run151 全新还原系统盘基线的独立冷启动复核；独立 `Apple_APFS_Recovery` 分区（`disk0s3`）在恢复流程下的重挂核验（主系统容器 `disk0s2` 与 `ISC` 容器 `disk0s1` 客体正常关机干净卸载已由盘面与内核双向证明）；历史 Run112 原生窗口停止更新与 Run90/93 `DeviceLost` 的根因；超时诊断 `site1` 路径；更大规模第三方应用、网络协议与数天级长期驻留。
- **平台：** 真实 Apple PAC/HVC 语义、SVE/SME、真实 OS 的多核调度与性能、DMA/IOMMU、avp-sealed-registers。
- **图形与交互：** 任意大型三维引擎与计算着色器 Metal 覆盖、物理输入与呈现延迟、60 FPS 持续帧率。
- **音频：** 旧构建偶发 PCM 缺帧的充分必要条件（`0050` 主线 Run156 双轮均为 0 零块欠载）、麦克风输入、物理输出、时延与数小时连续播放。
- **性能与整机（M5–M7）：** 60 FPS、输入延迟、CPU/GPU/功耗分项；睡眠、输入法、升级。
- **基线：** E04/E09/E10 性能基线，E07 x86 macOS 对照（BIOS 未开 VT-x），M0 在另一台空白宿主上的重建（同机全新克隆重建见 7.2）。

## 9. 证据存放

| 位置 | 内容 | Git |
|---|---|---|
| `docs/reports/` | 报告与脱敏 JSON（nonce 只记哈希和长度） | 跟踪 |
| `runs/` | 原始日志与逐场景结果 | 忽略 |
| `private/` | Apple 固件、密钥、票据、nonce、guest 磁盘与凭据（文件 0600、目录 0700） | 忽略，禁止提交或分发 |
| `build/`、`cache/`、`sources/` | 构建产物、下载缓存、固定第三方源码 | 忽略 |
