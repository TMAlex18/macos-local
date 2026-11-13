> **Language:** [English](M3-EXPANDED-VALIDATION-RUN156.en.md) | **简体中文**

# M3 扩展已安装系统验证（Run156：多通道 WebGL2/Metal 管线、原生应用与框架套件、VirtIO 音频边界追踪与 APFS 干净卸载核验）

更新时间：`2026-11-13 UTC`

## 范围与动机

在 `0033–0050` 补丁链完成 QEMU TCG 多核同步争用消除与纯执行吞吐深挖后，需要验证主线快速二进制（`build/qemu-bdif-usb/qemu-system-aarch64`，SHA-256 `e6f7b2cb32df3779531cf1b93612a67d8e1fa350c8b894c19d0c9e71a60f4a51`，**关闭 `--webkit-timeout-diagnostic` 与 `--sync-stats`**）不仅加速单一定点测试页的 Safari 冷启动，而且在更广泛的已安装 macOS 27.0.1（`26A434`、`arm64e`）跨层负载上保持逐位或代数确定性正确，并闭合 `docs/PLAN-STATE.json` 中悬置的 `filesystem_clean_unmount_proven` 边界。

本报告覆盖四个正交维度的端到端验证及对应的宿主独立裁判工具：

1. **多通道 WebGL2 → Metal → Reims → `metal2vulkan` → 宿主 Vulkan 渲染管线**：
   - 客体夹具：[`webgl-complex-probe.html`](../../experiments/E05-installed-boot/guest-diag/webgl-complex-probe.html)
   - 宿主像素裁判：[`validate-webgl-complex.py`](../../tools/validate-webgl-complex.py)（离线单测：[`test-validate-webgl-complex.py`](../../experiments/E05-installed-boot/test-validate-webgl-complex.py)；宿主隔离 SwiftShader 预检：[`test-webgl-complex.py`](../../experiments/E05-installed-boot/test-webgl-complex.py)）
   - 覆盖能力：`128 × 96` 离屏 `RGBA8` 颜色纹理 + `DEPTH24_STENCIL8` 深度模板渲染缓冲 FBO、跨双着色器程序绑定的 `std140` 统一缓冲对象（`UBO`）、`4 × 4` 程序化二维纹理（`REPEAT` + `NEAREST`）、模板缓冲写入与裁剪（`STENCIL_TEST` `REPLACE` / `EQUAL`）、深度遮挡剔除（`DEPTH_TEST` `LEQUAL`）、3 实例 `drawArraysInstanced` 逐实例属性流、有理数精确 `SRC_ALPHA / ONE_MINUS_SRC_ALPHA` 混合（$\alpha = 51/255 = 1/5$，直接检验 Reims `0007` 与 `metal2vulkan 0001` 混合状态序列化/反序列化），以及默认帧缓冲 `texelFetch` 合成通道。
2. **原生 macOS `arm64e` 图形应用与系统框架确定性套件**：
   - 客体探针：[`app-suite-probe.sh`](../../experiments/E05-installed-boot/guest-diag/app-suite-probe.sh)
   - 宿主独立验证器：[`validate-app-suite.py`](../../tools/validate-app-suite.py)（离线单测：[`test-app-suite.py`](../../experiments/E05-installed-boot/test-app-suite.py)）
   - 覆盖能力：
     - `JavaScriptCore`（`jsc`）：100,000 以内埃氏筛（素数计数与求和）、`16 × 16` `Float64Array` 矩阵乘法（迹与 Frobenius 范数平方）与 4096 次幂 `BigInt` 模幂；
     - `CoreGraphics` / `ImageIO`（`sips`）：`64 × 48` 四象限无损 BMP → PNG 编码（宿主 `Pillow` 逐像素核对全部 3,072 像素 0 误差）与 `32 × 24` 下采样；
     - `SQLite3`：`WAL` 日志模式、250 行递归 CTE 事务提交、显式 `ROLLBACK` 隔离性、窗口函数 `SUM(...) OVER (...)`、`VACUUM INTO` 导出数据库并在宿主用 Python `sqlite3` 独立执行 `PRAGMA integrity_check` 与聚合核对；
     - `CoreFoundation` 属性列表（`plutil`）：XML → `bplist00` 二进制格式 → JSON 往返转换，宿主用 `plistlib` 独立解析 `bplist00`；
     - 归档压缩与摘要（`gzip -9` + `shasum -a 256`）及 `sw_vers` / `uname` 架构身份核对；
     - 三款系统内置 Cocoa/SwiftUI GUI 应用（`Calculator.app`、`TextEdit.app`、`System Information.app`）并发启动、`lsappinfo` 前台应用注册验证、宿主原生窗口截图留证，以及关机前 `osascript` 干净退出与 `pgrep_exit=1` 确认。
3. **VirtIO 音频（`afplay`）双轮全波形逐位比对与 `pcm_boundary_trace` 因果复核**：
   - 结合 [`freeze-audio-voices.py`](../../tools/freeze-audio-voices.py)、[`validate-audio-test-tone.py`](../../tools/validate-audio-test-tone.py) 与 [`pcm_boundary_trace.py`](../../experiments/E05-usbip-kernel/pcm_boundary_trace.py)，在 `0050` 快速二进制上复核两轮独立 `afplay` 48 kHz 立体声 96,000 帧 PCM 的逐位完整性，并对比 `0033–0050` 优化前后 `VSNDPCM1` 边界追踪与最终 WAV 中的零值欠载块数量。
4. **APFS 容器检查点环与干净卸载只读核验**：
   - 宿主只读检查器：[`check-apfs-clean-unmount.py`](../../tools/check-apfs-clean-unmount.py)（离线单测：[`test-apfs-clean-unmount.py`](../../tools/test-apfs-clean-unmount.py)）
   - 依据 Apple 公开的 *Apple File System Reference*（"Mounting an Apple File System Container"）与 XNU `apfs.kext`（`nx_mount:1482–1509`）规范：容器挂载运行期间仅向检查点描述符环（`nx_xp_desc`）追加新事务，容器第 0 块超级块保持旧 `o_xid`；唯有容器干净卸载时才将最终提交的 `nx_superblock_t` 刷回容器第 0 块，使得 **`block0.o_xid == latest_checkpoint.o_xid`**（若二者不等，下次启动时 `apfs.kext` 输出 `nx_mount: reloading after unclean unmount, checkpoint xid C, superblock xid S`）。检查器以只读无跟随模式打开镜像、确认无宿主进程持有 inode，自动识别全部三类 Apple GPT APFS 分区（`Apple_APFS`、`Apple_APFS_ISC`、`Apple_APFS_Recovery`），逐一校验第 0 块与最高 `o_xid` 完整检查点切片、容器 `OMAP` B-tree 及全部 `apfs_superblock_t`（`APSB`）的 Fletcher-64 校验和与卸载时间戳（全程不输出容器/卷 UUID 或卷名）。

---

## 运行前假设与判定准则（Run156）

在 `0050` 二进制（8 vCPU、4 GiB RAM、`qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`，NVIDIA 原生窗口，VirtIO 网络与 VirtIO 音频开启，**关闭 `--webkit-timeout-diagnostic` 与 `--sync-stats`**）上执行 `m3-expanded-system-validation-20261113-156`：

1. **H14（多通道 WebGL2/Metal 像素逐位恒等假设）**：冷启动 Safari 直接加载 `webgl-complex-probe.html`，首个 GPU 进程在原版 3.0 s 硬期限下 `0` 看门狗退出一次完成初始化，离屏与默认帧缓冲各 `49,152` 字节读回经 `validate-webgl-complex.py` 校验均满足 **`bit_exact = true`（共 98,304 字节 0 误差）**。
2. **H15（原生应用与框架套件全项通过假设）**：`app-suite-probe.sh` 以退出码 `0` 完成，且 `validate-app-suite.py` 的全部 **7 项独立检查**（`summary_identity`、`javascriptcore_exact`、`sips_coregraphics_png`、`sqlite3_wal_acid`、`plutil_bplist_roundtrip`、`gzip_and_sha256`、`gui_apps_lifecycle`）全部通过，三款 GUI 应用在关机前全部确认退出（`pgrep_exit = 1`）。
3. **H16（VirtIO 音频逐位精确与欠载改善假设）**：`freeze-audio-voices.py` 冻结的两轮独立 `afplay` 完整声部均通过 `validate-audio-test-tone.py`（`full_tone_bit_exact = true`，`96,000` 帧完整连续、前后静音全零）；关机后 `pcm_boundary_trace.py` 完整解析 `guest-audio-boundary.bin`（`complete = true`，`errors = []`），且首轮播放的 WAV 内部零块数较 Run108 基线（`18` 块）显著下降。
4. **H17（APFS 容器干净卸载可证假设）**：在客体写入持久化校验文件并执行 `sudo /sbin/shutdown -h now` 正常退出（`qemu_exit_code = 0`，`guest_shutdown_observed = true`）后，只读运行 `check-apfs-clean-unmount.py` 验证已安装主系统容器（`Apple_APFS` `disk0s2`，含 `SYSTEM`、`DATA`、`PREBOOT`、`RECOVERY`、`VM`、`UPDATE` 全部 6 卷）与 `Apple_APFS_ISC`（`disk0s1`）均满足 Fletcher-64 完整且 **`block0_xid == latest_checkpoint.xid`**（`main_os_container_cleanly_unmounted = true`），并与开机 `kernel-boot.log` 中 `nx_mount` 零 `reloading after unclean unmount` 记录交叉吻合（同时如实记录自初始 `qmp_quit` 恢复以来常规启动从未读写挂载的 `Apple_APFS_Recovery` 分区 `disk0s3` 状态）。

---

## 实测结果（Run156：四项假设 H14–H17 全部通过）

### 运行身份与宿主/客体健康度

- 运行目录：`runs/e05-installed-boot/m3-expanded-system-validation-20261113-156`
- 二进制：`build/qemu-bdif-usb/qemu-system-aarch64`（SHA-256 `e6f7b2cb32df3779531cf1b93612a67d8e1fa350c8b894c19d0c9e71a60f4a51`，补丁链至 `0050`）
- 配置：8 vCPU、4 GiB RAM、`qarma5` + `--pauth-memo --pauth-memo-entries 1024 --pauth-memo-stats`，NVIDIA 原生窗口，VirtIO 网络（`--net-control-queue-size 512`）与 VirtIO 音频（`--sound-backend wav --sound-queue-size 512 --sound-streams 1 --sound-pcm-trace`），**未传 `--webkit-timeout-diagnostic` 与 `--sync-stats`**。
- 生命周期与热学指标：起始封装温度 `45 °C`；`+151 s` 到达登录窗口、`+193 s` 登录、`+362 s` 进入桌面；在完成全部四组验证并确认 `Safari` 与三款测试 GUI 应用均已退出（`pgrep_exit = 1`）后，从客体内部执行 `sudo /sbin/shutdown -h now` 正常关机（`qemu_exit_code = 0`、`qemu_reaped = true`、`host_quit_requested = false`、`guest_shutdown_observed = true`、QMP 事件 `SHUTDOWN {'guest': True, 'reason': 'guest-shutdown'}`、`errors = []`，总时长 `1,429.6 s`）；284 个宿主封装温度样本 `min = 50 °C`、**`mean = 68.6 °C`**、`max = 95 °C`；时钟探针 `r156a = -3.1615 s`（跨度 `2.5 ms`）、`r156b = -3.1665 s`（跨度 **`0.69 ms`**）；全程累计执行 **`42.02B` 次架构级 QARMA5 PAuth 操作**，综合命中率 **`87.53%`**。

### H14 检验结果（通过）：`+720 s` 冷首次启动 `2.163 s` 复现 `< 3.000 s`，多通道 WebGL2/Metal 双缓冲 `98,304` 字节 `0` 误差

1. **`+720 s` 无诊断冷首次启动跨冷启动重复性（2/2）**：
   - 在启动后 `+720 s`（无任何 `perf` 干扰）直接冷启动 Safari 打开 `webgl-complex-probe.html#r156-webgl-complex`：
     - `outcome = initialized`、`gpu_processes_constructed = 1`、**`gpu_unresponsive_exits = 0`**；
     - **`ui_request_to_did_initialize = 2.163 s`**（`gpu_entry_to_did_initialize = 2.160 s`，`gpu_entry -> registrar_activation = 1.085 s`，`registrar -> helper_create = 0.089 s`，`helper_create -> destroy = 0.664 s`，`helper_destroy -> did_initialize = 0.322 s`；窗口内 `vcpu_mean_busy = 5.544`）；
     - 结合 Run155 同窗口的 `2.941 s`，`0050` 主线已连续 **2/2 轮独立冷启动**在 `+720 s` 窗口无需任何超时诊断即大幅低于原版 WebKit `3.000 s` 硬期限。
2. **多通道 WebGL2 → Metal → Reims → `metal2vulkan` → NVIDIA Vulkan 像素逐位验证**：
   - 客体 WebKit 报告渲染器为 `Apple Inc. / Apple GPU`（`WebGL 2.0`、`WebGL GLSL ES 3.00`、`depth = true`、`stencil = true`），全部 4 个着色器编译、2 个程序链接、4 个渲染通道（`pass1a_textured_stencil`、`pass1b_instanced_depth_stencil`、`pass1c_alpha_blend`、`pass2_default_composite`）无任何 GL 错误完成；
   - 宿主运行 `validate-webgl-complex.py` 对两份原始 `128 × 96` `RGBA8` 读回与纯软件光栅化参考逐字节比对：
     - 离屏缓冲（`r156-webgl-complex-offscreen.bin`，`49,152` 字节，SHA-256 `9603e435…`）：**`bit_exact = true`，`mismatched_bytes = 0`，`max_channel_abs_diff = 0`**；
     - 默认帧缓冲（`r156-webgl-complex-default.bin`，`49,152` 字节，SHA-256 `98a456fd…`）：**`bit_exact = true`，`mismatched_bytes = 0`，`max_channel_abs_diff = 0`**；
   - 宿主原生窗口截图 `r156-t1.png` 清晰呈现 `UBO` 深蓝外框、模板裁剪边界、重复二维纹理、实例深度前后遮挡关系以及半透明黄白混合条带。**H14 成立**。

### H15 检验结果（通过）：6 类原生 `arm64e` 框架负载与 3 款系统内置 GUI 应用全部通过宿主独立校验（7/7）

`app-suite-probe.sh r156apps 18` 在 17 秒内跑完全部 5 组命令行框架测试并拉起 3 款 GUI 应用，保持窗口呈现 18 秒供宿主抓取 `r156-gui-apps.png` 后自动干净退出并上传归档（SHA-256 `cf6b9e04…`）。宿主运行 `validate-app-suite.py` 的 **7/7 项检查全部 `true`**：

| 检查项 | 客体执行组件 | 宿主独立裁判结果 |
|---|---|---|
| `summary_identity` | `sw_vers`、`uname` | 确认 `macOS 27.0.1`（`26A434`）、`RELEASE_ARM64_VMAPPLE arm64`、`collector_exit = 0` |
| `javascriptcore_exact` | `JavaScriptCore.framework/.../jsc` | `100,000` 埃氏筛（`9,592` 个素数，和 `454,396,537`）、`16 × 16` `Float64` 矩阵积（`trace = 83,224`，`frob_sq = 8,168,844,416`）、`3^4096 mod 1000000007 = 451694074` 与宿主计算完全一致 |
| `sips_coregraphics_png` | `/usr/bin/sips`（CoreGraphics / ImageIO） | `64 × 48` 无损 PNG 全部 `3,072` 像素与数学定义 `0` 误差一致；`32 × 24` 下采样四象限中心色彩准确 |
| `sqlite3_wal_acid` | `/usr/bin/sqlite3` | `WAL` 模式、250 行递归 CTE 提交、`ROLLBACK` 行完全不可见、窗口聚合一致，导出 `compact.db` 在宿主通过 `PRAGMA integrity_check = ok` |
| `plutil_bplist_roundtrip` | `/usr/bin/plutil`（CoreFoundation） | 产出合法 `bplist00` 二进制属性列表与 JSON，宿主 `plistlib` 独立解析完全一致 |
| `gzip_and_sha256` | `/usr/bin/gzip`、`shasum -a 256` | 1,024 行确定性文本压缩流经宿主解压与期望字节及两份 SHA-256 完全一致 |
| `gui_apps_lifecycle` | `Calculator.app`、`TextEdit.app`、`System Information.app`、`lsappinfo` | 三款应用并发存活 PID 与 `com.apple.calculator` / `com.apple.TextEdit` / `com.apple.SystemProfiler` 前台 Bundle ID 注册均确认（原生截图见桌面 `TextEdit` 文档窗口、菜单栏 `System Information` 与 Dock 指示点），随后全部干净退出（`pgrep_exit = 1`） |

### H16 检验结果（通过）：双轮 `afplay` 全部逐位精确，且 `VSNDPCM1` 边界追踪确认 `0` 零块欠载

1. **双声部冻结 WAV 逐位验证**：
   - 第一轮（`pcm-voice1/voice-1.wav`，SHA-256 `42fe0ea1…`，共 `126,464` 帧）：`full_tone_bit_exact = true`，`96,000` 帧（`2.000 s`）测试音频位于帧 `[14848, 110848)` 完整连续，前后静音样本全零（`surrounding_samples_all_zero = true`），过零频率测量左声道 `439.999996 Hz`、右声道 `660.000001 Hz`；
   - 第二轮（`pcm-voice2/voice-1.wav`，SHA-256 `900af42e…`，共 `137,728` 帧）：`full_tone_bit_exact = true`，`96,000` 帧位于 `[2560, 98560)` 完整连续，`surrounding_samples_all_zero = true`。
2. **`pcm_boundary_trace.py` 因果复核**：
   - 关机后解码 `guest-audio-boundary.bin`：`complete = true`、`terminal = 1`（正常关闭落盘）、`errors = []`、`reconstruction_errors = []`，共 `465` 条记录；
   - 客体 `coreaudiod` 协商参数为双声道、`48 kHz`、`VIRTIO_SND_PCM_FMT_FLOAT`（枚举值 `19`，每帧 8 字节）：`generation 1`（`1,011,712 B = 126,464 × 8 B`，SHA-256 `6646d44a…`）与 `generation 2`（`1,101,824 B = 137,728 × 8 B`，SHA-256 `a7fc7308…`）各自的 `pop`、`populate`、`accepted` 三阶段字节流 **100% 逐字节恒等**，且两轮 `768,000` 字节（`96,000` 帧）有效音频区间内 **`4 KiB` 全零欠载块数均为 `0`**（Run108 基线首轮曾出现 `18` 个零块）。**H16 成立**。

### H17 检验结果（通过）：主系统容器与 ISC 容器干净卸载经盘面检查点与内核 `nx_mount` 双重证实

1. **跨运行持久化校验**：Run63 建立的 `Desktop/macos-local-persistence.txt` 保持原始 SHA-256 `bc1d2eab496a732ba15ffcc27e5bbeeee788cb12dfd900f0a1891dddf0d2a98a` 不变；本轮新增写入并 `sync` 的 `Desktop/r156-persist.txt` 校验和为 `dc9f70f39b61577510f9322fdebf589fb4268ae7fbad9faf25ca8c73cea52f82`。
2. **退出后只读盘面检查点核验（`check-apfs-clean-unmount.py`）**：在确认无任何宿主进程持有镜像后，对关闭的系统镜像做只读扫描（`errors = []`）：
   - 分区 0（`Apple_APFS_ISC`，`disk0s1`）：`block0` 与检查点环最高事务号均为 **`xid = 555`**（`block0_xid == latest_checkpoint.xid`），全部 4 个卷 `APSB` Fletcher-64 有效，可写卷卸载时间戳为 `2026-11-13T08:07:45Z`（即 QEMU 退出时刻）——**`container_cleanly_unmounted = true`**；
   - 分区 1（`Apple_APFS` 主安装系统容器，`disk0s2`）：`block0` 与检查点环（`140` 个有效历史检查点）最高事务号均为 **`xid = 88420`**（`block0_xid == latest_checkpoint.xid`），容器 `OMAP` 与全部 **6 个卷**（`SYSTEM`、`PREBOOT`、`RECOVERY`、`UPDATE`、`DATA`、`VM`）的 `APSB` 超级块全部通过 Fletcher-64 校验，其中 `DATA`、`PREBOOT`、`UPDATE`、`VM` 均记录本次关机时刻 `2026-11-13T08:07:45.033Z–08:07:45.514Z` 卸载时间戳——**`main_os_container_cleanly_unmounted = true`**；
   - 分区 2（`Apple_APFS_Recovery`，`disk0s3`）：保留首次原生还原以宿主 `qmp_quit` 结束时的 `block0_xid = 52`、`latest_checkpoint.xid = 55` 状态（常规 macOS 启动从不以读写方式挂载该独立恢复分区，因此不会改写其第 0 块）；
   - **独立内核日志交叉印证**：在紧随 Run154 与 Run155 正常关机启动的 Run155、Run156 `kernel-boot.log` 中，XNU `apfs.kext` 的 `nx_mount` 对 `disk0s1`/`disk1` 与主容器 `disk0s2`/`disk3`（例如 Run156 启动时 `largest xid 87646, best xid 87646`）均直接打印 `stable checkpoint indices`，**零次出现 `reloading after unclean unmount`**（仅对从未读写挂载的 `disk0s3` 打印 `checkpoint xid 55, superblock xid 52`；而在首次 `qmp_quit` 还原后的冷启动 Run54 中，`disk0s2` 曾明确打印 `reloading after unclean unmount, checkpoint xid 720, superblock xid 718`）。因此，**已安装 macOS 主系统容器在客体 `shutdown -h now` 下的文件系统干净卸载已被盘面结构与 XNU 内核挂载器双向证明**。

### 保留的边界与限制

- 多通道 WebGL2 验证覆盖离屏 FBO、深度/模板、UBO、二维纹理、实例化绘制与有理数精确 alpha 混合的逐字节正确性，不代表任意第三方大型三维引擎、计算着色器或物理屏幕扫描输出帧率/输入延迟保证。
- VirtIO 音频双轮 `afplay` 在 `0050` 上均实现 `0` 零块欠载与逐位精确，但尚未覆盖数小时连续音频流、麦克风输入或宿主物理扬声器声学延迟。
- 独立 `Apple_APFS_Recovery` 分区（`disk0s3`）因常规系统启动不挂载写入而保持初始还原时的 `xid 52 / 55` 差值；主系统容器（`disk0s2`）与 `ISC` 容器（`disk0s1`）每次客体正常关机均完整刷回第 0 块超级块。

