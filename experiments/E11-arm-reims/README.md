> **Language:** [English](README.en.md) | **简体中文**

# E11 — Modern Reims, ARM VMApple and x86_64 Linux TCG

Goals: build and validate modern Reims apple-gfx-mmio/IOSurface devices in
the same QEMU process that executes our self-authored ARM64 firmware on
an x86_64 Linux host.

The standalone firmware runners in this section do not boot Apple firmware,
XNU or a macOS GUI. A self-authored ARM64
guest now submits compute and offscreen triangle workloads, including a real
IOSurface descriptor/mapping path and native-window frame updates, through Reims to
the hardware Vulkan GPU and verifies the returned data. This does not establish macOS
Metal or WindowServer compatibility.

## Source and build separation

- Modern QEMU worktree: sources/qemu-reims-arm; pinned to 1a72df9849;
  patches: patches/qemu-platform/0003-vmapple-tcg-pac-model-stack.patch and
  0004-research-avp-rtc-ctrr-mmio.patch (the build script's fixed chain).
- Modern Reims ARM-guest worktree: sources/reims-arm-guest; same upstream
  superproject pin as the original Reims worktree, plus:
    0001 Cargo dependency source pin,
    0002 ARM64 guest APV page-size feature,
    0003 IOSurface guest-page/host-page separation.
    0004 fixture host/guest boundaries, 0005 isolated-task host alignment,
    0006 shareable temporary backing for detached heaps on Linux,
    0007 pinned framebuffer-fetch compiler overlay and GPU consumer checks.
- Test binary A: build/qemu-reims-arm/qemu-system-aarch64
  (default Reims Linux x86 guest 4KiB pages, for the negative comparison).
- Test binary B: build/qemu-reims-arm16k/qemu-system-aarch64
  (Linux x86 host, ARM64 guest APV pages 16KiB).

## Build and run

From project root:

    make build-reims-arm64-vulkan
    make build-reims-qemu-arm16k
    make reims-arm16k-smoke

Test harness parts:

- run-machine.py: paused QMP, VMApple + gfx and IOSurface MMIO.
- run-firmware.py: VMApple config, UART, 2-vCPU PSCI with vGPU initialized.
- run-mmio.py: real ARM CPU reads Reims MMIO.
- run-registers.py: real ARM CPU writes/reads Reims version register.
- run-root-page.py: differential page-address decoding, 4KiB vs 16KiB.
- run-fifo.py: differential root-FIFO enable/disable, 4KiB vs 16KiB.
- run-device-info.py: Nop/GetDeviceInfo roundtrip with no-doorbell control.
- run-gpu.py: real hardware Vulkan compute/draw/present, guest-side checks,
  and explicit VM-local USB HID input/interrupt probes.
- Self-authored source: probe-mmio.S, probe-registers.S, probe-root-page.S,
  probe-fifo.S, probe-compute.c, probe-draw.c; the two GPU fixtures share
  guest-common.h, guest-start.S and guest.ld. No Apple binary artifacts included.

All logs in runs/, generated images/binaries in build/, ignored by Git.
Do not claim the 16KiB guest profile is production-valid until the complete
Apple-specific GPU protocol and IOSurface lifecycle work is checked.

## Distinct contract categories

- Guest APV/FIFO/IOSurface *page numbering* is 16KiB for an ARM64 guest.
- Host allocation/mmap granule remains the host OS page size (4KiB here).
- Platform-specific CPU register operations remain tied to host architecture.
- Reims 16KiB version is a *per-build* feature, not a runtime negotiation.

The original modern QEMU has HVF-only MAC guest hooks; these were guarded
for Linux TCG but not replaced with accurate Apple HVC/PAuth semantics.

See docs/reports/M1-ARM-REIMS.md for test outcomes and unresolved suite failures.


## v3 新增命令通道与对齐修复

- Patch 0004 persists guest/host-aware test fixtures; patch 0005 separates
  host-pointer alignment from guest range granularity for isolated tasks.
- `make reims-core-serial`: ARM16K and x86 Core regression suites.
- `make reims-fifo-roundtrip`: real guest Nop/GetDeviceInfo command and
  completion/result memory checks, plus the no-doorbell negative control.
- This is command transport and device information, not rendering.

See docs/reports/V3-ALIGNMENT-AND-PROGRESS.md and current docs/STATUS.md.

## ARM guest → 硬件 GPU → guest 计算闭环

在项目根目录运行：

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload compute --gpu nvidia
```

依赖上述固定 ARM16KiB build、`clang`/`ld.lld`/`llvm-objcopy`、`vulkaninfo`
与硬件 Vulkan ICD。runner 只允许选择 NVIDIA 或 Intel 的单一 ICD，禁用
外部 Vulkan layer，并要求同一环境下的设备清单只有一个 integrated/discrete
GPU；CPU/llvmpipe 不接受。每个 QEMU case 还必须在实际 runtime stderr 中取得
匹配的 GPU 名称或 driver ID + device ID，否则整项失败。`--gpu intel` 选择
Intel；两个硬件后端均已实测。两个 case 各有 90 秒 host 上限，guest 每条命令
有 30 秒计时器上限。

`probe-compute.c` 在裸机 guest 内构造 16KiB 页表、shared input/output buffer、
function 与 compute pipeline，使用正常 `DefineTask2 (56)`、`SetObjectList (51)`、
`ExecIndirect3 (67)`，最后 `DeleteTask (32)`。shader 直接引用 pinned Reims 的
自有 compute_add.metal
及其现成 metallib；不依赖 Apple guest shader 或新编译工具链。

guest 生成 256 个 `uint32` 输入 `((i * 1664525) ^ 1013904223)`，先将输出填为
`0xdeadbeef`，提交 `add_one` 的 256 线程、每组 64 线程。完成 stamp 到达后，
guest 独立计算每项预期值 `input + 1`（32 位回卷）并检查整个输入页未变、输出
余下 15KiB sentinel 未变、FIFO 已消费且 fault 为零。host 只装载自制 firmware
与 shader；不写答案或读取结果替 guest 判分。资源同步 flags 为
`invalidateHost | synchronize`，完成 stamp 包括结果回写。

`no-exec` 对照保持相同资源与输入，跳过 `ExecIndirect3`，改发正常 Nop 并等其
stamp；guest 必须读到原 sentinel，不能打印 compute PASS。这验证 FIFO 往返
本身不能充当 GPU 输出。

2026-10-23 两硬件后端的正例/对照均通过：RTX 5070 Ti Laptop GPU
（`10de:2f18`，discrete，driver `595.91.07`）与 Intel(R) Graphics (ARL)
（`8086:7d67`，integrated，Mesa `26.0.8-1ubuntu0.3`）。各自单 ICD 设备清单与
实际 QEMU stderr 的 driver/device ID/名称相符；正例首尾输出均为
`3c6ef360` / `252255ad`，对照均为 `deadbeef`。guest 检查全部数值，不只检查
这两个样本。guest 的 16KiB 与 host 的 4KiB 合同保持不变；没有修改 Reims/
QEMU 库实现或依赖。

可复现入口与证据：

- NVIDIA 结构化结果、
  Intel 结构化结果：
  硬件隔离、binary/shader/firmware 摘要、case 结果、实际 GPU 选择证据与 guest UART。
- 每次运行新建 `runs/e11-arm-reims-compute/<gpu>-<UTC时间>/`，保存本次
  `vulkan-device.log`、`positive.log`、`no-exec.log`、`results.json`，不覆盖原始证据。
- `build/e11-compute/` 保存自制 ELF/bin；两份临时 stub 盘在每项结束后清理。

这是有界 compute smoke，检验整数加一；独立 draw 路径如下。两者均不覆盖
IOSurface 展示、多队列负载或 macOS Metal 完整协议。Reims Vulkan 适配层及
metal2vulkan 仍有自身声明的精度/性能边界。

### 20MiB detached heap：正常构造失败与修复

2026-10-31正常OS冷启动56在`newHeapWithDescriptor:`创建20MiB heap时abort，
错误为`a remap ... 0x1400000 bytes (kern_return 1)`。Core
`resources/heap/detach.rs`先分配临时零页，再将其作为address ranges创建heap、
立即detach，第一次prepare才换成guest页。Linux的`allocate_anywhere`仅提供
匿名地址预留；该可读范围不在file backing表中，`backing::gather`因此无法用
`remap_shared_overwrite`建立共享alias。紧前的RGBA16Unorm纹理尺寸差异warning
不是这个失败的必要条件：下面不创建任何纹理的正常heap consumer精确复现同fatal。
56的apport记录`core limit 0`且非发行包被忽略，没有保留下来的core可供回溯。

修复由`patches/reims/0006-detached-heap-aliasable-backing.patch`固化：HostVm
增加有明确共享要求的`allocate_aliasable`，Mach默认仍调用原allocator；Linux
复用既有memfd allocator，Vulkan wrapper保持映射变更通知。只有detach-heap的
临时backing使用这个入口，普通预留、remap失败语义、heap detach/replace及
keep-alive生命周期不变。没有复制guest数据、忽略失败、替换纹理格式或放宽oracle。

```sh
bash tools/build-reims-arm64-vulkan.sh
bash tools/build-qemu-bdif-usb.sh
python3 experiments/E11-arm-reims/run-gpu.py --workload compute --heap --platform restore-research --gpu nvidia --irq
```

`--heap`复用同一自有`compute_add` shader和runner，建立type13的20MiB heap及
覆盖它的type14 buffer。1280个guest16KiB页的物理地址按相邻pair反序，验证
分段guest页真正聚合成GPU可用的alias；独立输入buffer为20MiB。
GPU以256线程/组计算5242880个uint32，guest按虚拟页到物理页的映射逐字检查
全部输出为`input+1`、完整输入未变及heap/input末尾两份16KiB guard不变。
实际stamp3 IRQ49、零fault和DeleteTask完成都是成功条件；guest不会只凭资源
创建成功或FIFO completion通过。

同一个guest firmware SHA
`728e3d1410c9fd376c71119e2fe3d67a0e704db30a490f798b8fe108b3ab7ef7`的前后对照：

- 修复前6731911f结果：
  初始化IRQ1/2完成，首次GPU exec创建heap时报同20MiB remap fatal，QEMU exit-6。
- 修复后f5dfbf2d结果：
  全部20MiB输出、20MiB输入、32KiB guard及真实IRQ49通过，正常exit0。
- 同一新binary的RGhA回归：
  原始RGBA16Float像素/guard、GPU/display IRQ及真实owned nativewindow红蓝图像通过。

host层验证包括Linux VM 11项、
backing 12项、
remap 7项及Core heap 33项。
新增真实memfd的20MiB测试检查双向共享写入、source及中间view释放后alias继续存活、
越界/中间洞拒绝与清理；既有第二段mmap失败测试仍验证前段恢复及backing表不变。
两个自制QEMU均已退出，临时stub盘清理。未在此扩大到全workspace/GPU矩阵。

精确patch重建与
QEMU构建记录保存来源及链接库hash。
本次QEMU SHA为`f5dfbf2d299d88b9df390c1414e573a4e5f749d55fe0bf36b760f9288b633f2d`，
静态Reims库SHA为`1f49fdb5f786d39cb96c0122606888a96fcb29be29beed47195c2b96c1a82464`。
这解决已复现的heap构造abort；56原始证据和失败结论保留。随后主线57独立取得
可读LanguageChooser及鼠标本地化反馈，
属于初始GUI验证，不等同完整桌面，也没有把未崩溃本身当作原20MiB路径命中证明。

### RGBA16Float framebuffer fetch 与 source-over 混合

正常OS初始设置58在继续语言页后，创建render pipeline时报
`FramebufferFetch / NotImplemented / Color(0)`而abort。固定依赖
`ecb80e019d54b8eb1585974fb2a6cc37bac359cc`的NVIDIA有序fetch使用pixel
interlock/storage image；原实现对普通half-float attachment只接受替换式blend，
或特定Exact策略的no-fast-math half4混合。读取后仍需premultiplied source-over
的普通路径缺失，前面的shader-side-depth-test warning不是该拒绝条件。

自有fixture的修复前拒绝在同一配置重现：
colour0 RGBA16Float115、RGB/alpha均One/OneMinusSourceAlpha/Add、mask0xf，
colour1 RG16Float65与colour2 RGBA16Float115均mask0，depth/stencil260、
无vertex descriptor。使用仓库已有`fetch_half_fragment`/`fetch_float_fragment`
metallib；最终执行fixture复用E11已有buffer驱动vertex，避免另一个测试vertex的
FunctionStorageAssumed严格拒绝。没有提取或提交Apple shader。

`patches/metal2vulkan/0001-half-float-fetch-source-over.patch`在同一个有序
interlock内读取destination、计算`source + destination * (1 - source.a)`并写回。
现有depth/stencil、coverage、write-mask guard保留；非fetch的可选half精度路径
保持原选择，未禁blend或把读取改为unordered。既有Exact half模型仍为opt-in，
默认float算术保留source/conversion precision notes；缓存语义版本1691→1692。
依赖没有换版本：`tools/prepare-metal2vulkan-research.sh`校验同一pin及patch，
Reims0007通过Cargo patch overlay使用它，不修改共享Cargo下载缓存。

真实NVIDIA release消费者经Reims的serialized descriptor、render encoder和GPU
blit readback执行。每种正例检查16×16全部像素及两份未使用attachment：
同一pass两个draw必须读取上次draw写入值；depth拒绝、第一次depth写入阻止
第二次draw、stencil拒绝、第一次stencil递增阻止第二次draw都分别有独立oracle。
例如初值`(0.25,0.5,0.75,0.5)`经两次fetch/混合成为
`(0.37109375,0.7421875,1.11328125,0.7421875)`，完整binary16字节须精确匹配。

| 自有fragment | 默认策略 | 显式`REIMS_VGPU_EXACT=half_blend` |
| --- | --- | --- |
| fast-math half4 | 5个GPU正例通过 | 5个GPU正例通过 |
| fast-math float4 | 5个GPU正例通过 | 5个GPU正例通过 |
| no-fast-math float4 | 5个GPU正例通过 | 5个GPU正例通过 |
| no-fast-math half4 | 保留原strict HalfOutputPrecision拒绝，不提交该command buffer | 5个GPU正例通过 |

默认release日志与
Exact release日志记录各row及实际policy。
两批总进程耗时4.68s/1.48s，最大RSS363856/360304KiB；最慢单pipeline创建0.666s。
这是有缓存环境的有界执行证据，不是冷启动性能比较。测试值的精确binary16结果
不证明任意输入、fast-math融合或舍入与Metal bit-exact；这里的fragment无外部
memory副作用，未增加导出depth/stencil或shader-side stand-in组合的GPU覆盖。
原版caller若为no-fast half4，必须显式选择已有half_blend Exact策略；没有修改
FW、guest或主线runtime配置来隐式启用它。

从项目根目录复现（第二条明确选择Exact）：

```sh
e11_fetch_test=fetched_half_float_source_over_keeps_order_tests_and_unused_attachments
env -u REIMS_VGPU_EXACT CARGO_TARGET_DIR="$PWD/build/reims-arm-target" VK_DRIVER_FILES=/usr/share/vulkan/icd.d/nvidia_icd.json VK_LOADER_LAYERS_DISABLE='*' REIMS_VGPU_WINDOW=0 cargo +1.98.1 test --manifest-path sources/reims-arm-guest/Cargo.toml --release --locked -p reims-vgpu-vulkan --features reims-vgpu-core/arm64-guest-pages --lib "$e11_fetch_test" -- --test-threads=1 --nocapture
env REIMS_VGPU_EXACT=half_blend CARGO_TARGET_DIR="$PWD/build/reims-arm-target" VK_DRIVER_FILES=/usr/share/vulkan/icd.d/nvidia_icd.json VK_LOADER_LAYERS_DISABLE='*' REIMS_VGPU_WINDOW=0 cargo +1.98.1 test --manifest-path sources/reims-arm-guest/Cargo.toml --release --locked -p reims-vgpu-vulkan --features reims-vgpu-core/arm64-guest-pages --lib "$e11_fetch_test" -- --test-threads=1 --nocapture
```

storage guards 10项、
新pipeline真实AIR编译与spirv-val、
原有ordered-fetch、
原有half精度/Exact回归通过；
compiler crate的`clippy --lib -- -D warnings`通过。未扩大到全workspace或GPU矩阵。

正式build已接入依赖patch重建记录
及Reims patch重建记录。
包含既有virtio-net/0018的新canonical QEMU SHA为
`0eb337028d1ef95e3de168584739d827a1abd5725bd5a0516b24fc7028fcf04e`；
静态Reims库SHA为`44d3fe9ef21ec0df8658b5488754bf3da9bc5460f41a9114bff6f396026eab53`，
完整来源保存在构建记录。
同一binary的20MiB heap回归
和RGhA原生窗口回归
均通过，显式unset Exact且结果记录其值为null。自制QEMU已退出、临时盘清理。
58原始abort证据保留；原版OS后续59及实际设置流程由主线独立验收。

## ARM guest 离屏三角形与像素回读

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload draw --gpu nvidia
python3 experiments/E11-arm-reims/run-gpu.py --workload draw --gpu intel
```

与 compute 共用固定 binary、硬件 ICD 隔离、每个 QEMU 进程的设备身份检查、
16KiB guest/4KiB host 合同、超时和临时盘清理。每次新建
`runs/e11-arm-reims-draw/<gpu>-<UTC时间>/`；旧 compute 入口统一由
`run-gpu.py --workload compute` 承担（也是默认 workload）。

`probe-draw.c` 采用 pinned suite 的
render_vbuf.metal、
render_color.metal
与现成 metallib、`render_lib.py` 的 `basic` pipeline 描述符。guest 自己构造
页表、shared buffer、RGBA8 texture view、两个 function、render pipeline、顶点
和颜色资源，经正常 `ExecIndirect3` 提交 render encoder 的
`DrawPrimitives16 (0x01)`。使用 64×64、single-sample、clear blue / store 的离屏
目标；GPU 光栅化红色三角形，结果同步到 guest buffer。

guest 顶点的 clip 坐标是 `(-.75,-.75),(.75,-.75),(0,.75)`，对应像素空间
`(8,56),(56,56),(32,8)`。guest 用像素中心的三个整数半平面条件逐项检查
4096 像素：`y<56 && 2*x+y>=71 && 2*x-y<=55` 为红，其余为蓝。所有像素中心
均不在边上，避免依赖边界归属差异；颜色通道均为精确的 0 或 1，RGBA8 检查
不需要舍入容差。还检查目标后 16KiB guard 未变、FIFO 消费和 fault 状态。

`no-draw` 只省去 draw 命令，保留同一 render pass、pipeline、clear、同步和完成
路径，guest 必须检查全帧为蓝。正例的 1152 红 / 2944 蓝与对照的 4096 蓝说明
绘制作用独立于 clear 和此前的 compute。host 不写预期图，也不替 guest 判分；
runner 仅从 guest UART 的有界 RGBA 导出保存实际 `.rgba` 与 `.png` 预览，另核对
格式及像素计数。

2026-10-23 NVIDIA 和 Intel 的正例/对照均通过，各 case 的实际 GPU 身份证据
随结果保存。生成的 PNG 已目视核对为蓝底红三角形；预览来自真实 guest 回读。

- NVIDIA 结果、
  实际图像。
- Intel 结果、
  实际图像。
- `build/e11-draw/` 保存可重建的自制 ELF/bin；每个结果目录保留完整 UART、
  hardware inventory、shader/binary/firmware 摘要与两个 case 的原始像素。

已验证的范围是正常离屏 draw、GPU 完成和 guest 像素 oracle；这不是 macOS GUI，
也没有覆盖窗口显示、复杂混合/纹理采样或完整 Metal 协议。

## GPU 完成 IRQ 模式

默认仍为 poll，保留上述原始证据。显式 `--irq` 运行一个正常 workload 正例，
由 guest 的真实中断处理程序判定完成，再执行相同的数值或像素 oracle：

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload compute --gpu nvidia --irq
python3 experiments/E11-arm-reims/run-gpu.py --workload compute --gpu nvidia --irq --disable-work-irq
python3 experiments/E11-arm-reims/run-gpu.py --workload draw --gpu nvidia --irq
python3 experiments/E11-arm-reims/run-gpu.py --workload compute --gpu intel --irq
```

`guest-irq.h` 与条件编译的 EL1 向量复用 E06 已验证的 GICv3 Group1/SRE/PMR/
EOImode0 配置。Reims 经 QEMU BH pulse 接到 SPI INTID49，因此配置为 edge。
ISR 实际读取 `ICC_IAR1_EL1=49`，读清 `0x30201018` 的 stamp 位图，捕获
`0x70004000` 内存 stamp 与零 fault 后执行 EOIR。root FIFO 恒为位图 bit0；
stamp 值 3 不对应 bit3。离屏 workload 不读取 display 的读清位图，也不把
fault 寄存器当 ACK。

每次只保留一条在途命令，stamp1/2 的初始化 IRQ 必须先实际消费；真实 GPU
workload 的 stamp3 必须由同一次 ISR 捕获 `INTID49 + bit0 + memory stamp3 +
fault0`。等待条件只读 ISR 保存的状态，不轮询 GPU stamp；用 `CNTVCT` 给循环
设置 guest 内期限，不使用 WFI。允许硬件通知合并，不要求每命令固定 IRQ 数量。
结果保存 `completion_mode`、`irq_capture`、`gpu_work_irq_verified`，而非只检查
总 IRQ 计数非零。

`--disable-work-irq` 只在初始化 IRQ 已消费后禁用 GIC 的 SPI49。GPU 照常提交和
完成；guest 两秒后检查内存 stamp3、零 fault 并继续原数值 oracle，必须因缺少
work ISR 而退出 **1**。runner 将这个明确的 guest 失败识别为负例通过；host
timeout、数据错误或未消费初始化 IRQ 都不能通过。失败后的资源删除可轮询完成，
不会恢复或替代已缺失的工作 IRQ。

2026-10-23 实测覆盖：

- NVIDIA compute IRQ：
  stamp3 ISR 与全部 256 项结果/guard 通过。
- NVIDIA 禁 SPI 对照：
  初始化 IRQ 通过、GPU 结果正确、work ISR 缺失，guest 按预期退出 1。
- NVIDIA draw IRQ：
  stamp3 ISR、4096 像素与 guard 通过，保留实际回读 PNG/RGBA。
- Intel compute IRQ：
  同一通知与数值合同通过。

新增 IRQ 代码不改 QEMU/Reims；默认 poll 的代表性 compute 正例重新编译后与原
已验证固件逐字节相同。IRQ 模式仍是单 FIFO、单个在途工作负载的有界 smoke，
没有覆盖压力下的中断合并、显示中断或多队列调度。

## 当前恢复研究平台上的图形共存

`--platform restore-research` 使用当前签名恢复工作的
`build/qemu-bdif-usb/qemu-system-aarch64`，配置4GiB RAM、两个vCPU、精确24MHz、
QARMA5及显式PAC/CTRR模型。仍然运行本目录自制EL1程序，只使用CPU0，使用
4MiB AUX/32MiB root临时stub盘；没有Apple载荷、TSS、恢复传输或系统盘流程。
模型设备已配置，不代表本图形程序执行了其保护/密钥语义。

```sh
make reims-gpu-restore-platform
```

NVIDIA的compute IRQ
和draw IRQ
分别通过同一stamp3 ISR合同及完整数值/像素检查，使用的binary摘要与当前恢复构建一致。
这证明已补齐的平台构建能与上述小型图形路径共存，未证明原Apple图形驱动或完整恢复。
默认 `--platform graphics` 保持前述512MiB/单vCPU图形基线。

正常Apple原版及保存的live DT均声明gfx的controller为GIC，interrupt cells为
`[49,0]`，IOSurface为`[48,0]`，与QEMU的路由编号吻合。IOKit公开常量中的0与
edge相容；通用DT代码只传递specifier，不能据此断言同版本AppleGIC实际写入的
ICFGR。当前没有证据证明Apple level配置与QEMU pulse冲突；本自制IRQ夹具显式使用edge。

## IOSurface descriptor → GPU → guest 与资源再用

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload iosurface-draw --platform restore-research --gpu nvidia --irq
```

此workload复用三角形与独立像素oracle，但资源入口改为真正的IOSurface
host device；原`draw`的shared-buffer texture view仍单独保留。
`guest-iosurface.h`经`0x30211000`的MMIO建立16KiB ring、descriptor table、
v2 descriptor及backing page table。每个表、ring与PFN都按 **guest 16KiB**
计算；host allocation/mmap保持 **4KiB**。寄存器使用QEMU既有low32 ABI，
guest地址均落在该范围内。

surface为non-planar单图像BGRA，64×64、每行256字节；descriptor的
`plane_count=0`表示使用base dimensions，texture使用plane0。allocation为
32KiB：前16KiB存像素、后16KiB作guard。正常Map command1后，guest必须
读到ring status0、read counter前进，以及 **host写回descriptor mapped位**。
这与像素结果一起构成descriptor被实际消费的证据。

task使用 **type11 mapper-ref texture**，通过mapping ID1查找刚建立的
IOSurface，采用正常IOSurface texture operation12与BGRA8 render pipeline，
没有创建shared-buffer texture view或使用会由CPU填测试图的`CMD_TEST`。
正常`ExecIndirect3`/`DrawPrimitives16`完成后，guest检查整个实际backing：
4096像素仍为1152红/2944蓝，guard不变，并核验GPU stamp3的真实ISR。

同一会话继续验证生命周期：

1. texture仍持有surface时，Unmap command2必须返回`Busy (e00002d5)`，mapped位保持1。
2. `DeleteResource (37)`删除mapper texture后，Unmap必须返回0并清mapped位。
3. guest给已退休backing填不同的poison sentinel；同mapping ID1改指另一块真实
   guest backing，重新Map并正常绘制。第二次4096像素/guard及GPU stamp5 ISR
   都必须通过，第一块已退休backing的32KiB必须保持poison。
4. 删除第二个texture、成功Unmap清mapped、删除task并关闭ring/table。

2026-10-23在当前restore-research binary/4GiB/2配置CPU/24MHz的NVIDIA与Intel上通过，
仍只由CPU0运行自制guest，无Apple载荷或TSS，也未修改QEMU/Reims及依赖。
IOSurface ring完成通过有界read counter/status观察；这里验证的完成IRQ是GPU
SPI49，没有新增IOSurface SPI48覆盖。

- 真实结果与五次ring命令。
- Intel同一IOSurface资源/释放再用结果。
- 第一块backing实际图像、
  释放/重映射后第二块backing实际图像。

每轮原始BGRA从guest UART保存为`.bgra`；PNG仅对实际回读作BGRA→RGBA通道
转换，不生成预期图。结构化结果保留每轮图像、mapped writeback、Busy与释放再用
证据，并明确原始/预览格式及通道转换；旧backing的poison是在第二次真正GPU完成
及像素检查之后核验。完整UART保留在同一唯一run目录，临时stub盘照常清理。原shared-buffer
draw在同平台的代表性IRQ回归也通过。此项补齐小型IOSurface资源路径，未覆盖
scanout、窗口展示、多平面、跨进程应用或完整macOS Metal/IOSurface协议。

## 自制guest的窗口present入口

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload present --platform restore-research --gpu nvidia --irq
```

本入口请求一个普通、可缩放的320×240 Reims窗口，保留约5秒后自动关闭；实际
client尺寸以capture记录为准。
QEMU自身仍使用`-display none`；仅对子进程设置`REIMS_VGPU_WINDOW=1`、
`SDL_VIDEO_DRIVER=x11`（SDL3名称）、统计与info日志，不切换桌面显示设置。

guest先完成同一IOSurface GPU绘制、真实stamp3 IRQ和全部源像素检查，随后定义
kernel task0，使用独立16KiB display shared page完成connect/expectedAck握手。
正常opcode8呈现mapping ID1，保持surface/backing存活到guest退出。成功判据要求
shared page的presentCount恰增1、presentedMappingID为1，并由ISR49从独立display
port位图和原子取清的raisedInterrupts中观察到present bit2。FIFO stamp、零fault
或单独display IRQ都不足以证明原生窗口已显示。

runner使用实际`Popen`句柄，把stdout/stderr写入临时文件避免管道堵塞，在进程
存活时调用`window_capture.py`。只有自有QEMU窗口的原生图像匹配、SDL实际x11
driver日志、window帧计数和window GPU时间统计共同成立，才接受窗口present。
guest导出的PNG仍是源像素证据，不替代原生窗口capture。helper按`_NET_WM_PID`
限制为这个子进程的client window，不截取整屏。

2026-10-23首次restore-research/NVIDIA/IRQ实际窗口验证通过：guest presentCount
0→1、presentedMappingID1、ISR49的port bit0和raised present bit2均满足。
实际自有X11 client为320×200，原生capture的三角形内外区域匹配；同一QEMU
记录window帧与window GPU时间（首个统计周期window2.0/s、GPU0.11ms）。这是
本次普通窗口帧路径的执行证据，不是吞吐或延迟基准。guest按5秒保帧窗口正常
退出，窗口已关闭。

- 完整结果、
  自有原生窗口图像。

这证明自制guest的IOSurface→present→原生窗口路径；未证明macOS GUI、物理屏幕
scanout、色彩校准、延迟或压力下的显示协议。`window_capture.py`仅负责限定自有
窗口的独立观察，guest源像素和协议ACK始终保留为不同证据。

### RGhA / RGBA16Float 单帧契约

2026-10-31针对正常macOS冷启动中实际报告的`pixel format 0x52476841`，
单帧`present`增加opt-in `--surface-format rgba16float`；默认BGRA8入口保留。
Reims的`crates/reims-vgpu-core/src/display/guest_display/present.rs`明确把
该fourcc（`RGhA`）映射为`MTLPixelFormatRGBA16Float=115`、每像素8字节，
并在mapped present时校验IOSurface的bytes-per-element。上游
`interplay_scanout_rgha_rendered`已有GPU clear/mapper/present场景，但不能替代
本实验的ARM/QEMU、完整源像素、真实ISR及原生窗口联合检查。

```sh
e11_qemu_sha=6731911f4c04d8c7c300661da334c44b2550a22c88c249bb635f610792831355
python3 experiments/E11-arm-reims/run-gpu.py --workload present --platform restore-research --gpu nvidia --irq --expect-binary-sha256 "$e11_qemu_sha"
python3 experiments/E11-arm-reims/run-gpu.py --workload present --platform restore-research --gpu nvidia --irq --surface-format rgba16float --expect-binary-sha256 "$e11_qemu_sha"
```

这两个有界case已按序在同一canonical `build/qemu-bdif-usb/qemu-system-aarch64`
上通过，binary SHA如命令所示。half-float surface为64×64、rowbytes512、
image32768字节，另有16KiB guard；v2 descriptor、16KiB guest page table、
type11/op12 mapper texture及pipeline format均实际切到RGhA/115。
CPU只初始化sentinel并提交既有shader/三角形，GPU正常绘制后，guest独立按
三角形半平面检查4096个原始像素的全部64位：1152红、2944蓝，binary16的
0/1须分别精确为`0x0000`/`0x3c00`，整个guard不变。没有CPU生成答案图。

两case都验证Map status0/mapped写回、GPU stamp3真实INTID49、display
presentCount0→1/mapping1及独立port bit0/present bit2；host日志分别报告
64×64的`0x42475241`/`0x52476841`。独立XGetImage从各自QEMU拥有的320×200
原生窗口捕获红蓝三角形，几何/区域oracle匹配，且记录到window GPU执行统计。
两个QEMU都正常退出，临时stub盘随runner清理。

- 默认BGRA控制结果。
- RGBA16Float结果、
  原始half-float回读、
  独立原生窗口capture。

`.rgba16f`保存实际little-endian RGBA binary16；PNG只将该回读转换为unorm8预览，
不作为原生窗口证据。结果记录源码侧surface metadata、host mode及原始字节hash。
这补齐小型RGhA IOSurface→GPU draw→display/nativewindow路径，不证明macOS
1920×1080多队列、WindowServer/LanguageChooser或完整GUI正常，也未覆盖HDR、
颜色管理及任意half-float值；本次没有扩大GPU或格式矩阵。

同一窗口的双帧更新入口为：

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload present-update --platform restore-research --gpu nvidia --irq
```

它使用mapping ID1/2、两份有效descriptor和独立page table/backing，先呈现红色
三角形约5秒，再实际GPU绘制绿色三角形并切换到ID2。两次完整guest像素/guard、
GPU IRQ和独立display事件都必须通过，presentCount必须0→1→2；host依次要求
同PID、同window ID的红/绿原生capture，分别保存到独立目录。绿色oracle不能
接受旧红帧。

删去旧task texture后，ID1仍由当前display持有时Unmap必须Busy。切换到ID2后，
guest允许旧窗口GPU命令暂时保留资源，只作有界Busy重试，直到正常Unmap成功并
清mapped位；随后poison旧backing，保持绿帧5秒后检查旧区没有被写入。ID2保持
存活到guest退出，不修改仍在使用的ID1映射表，不增加第三帧或额外ACK。

2026-10-23唯一一次restore-research/NVIDIA/IRQ双帧调用通过，guest正常退出后
窗口已关闭。两轮完整源像素/guard及GPU work IRQ stamp3/9通过；两次display
事件分别确认count0→1/mapping1、count1→2/mapping2。原生红/绿capture属于同一
QEMU PID和同一window ID；绿色阶段先有38次旧/未更新像素未匹配，随后实际
绿色图像匹配，不能用先前红图通过。两个呈现时段均记录到window GPU执行统计。

ID1在删task texture后仍Busy；切到ID2后首个正常Unmap即成功、mapped位清零，
本次没有命中切换后的瞬时Busy重试。guest随后poison旧backing，在绿帧保持5秒
后确认旧区未被写。独立ID2 page table与descriptor在此期间保持有效，不改旧
映射表，也没有额外ACK或第三帧。

- 双帧完整结果。
- 原生红帧、
  同窗口原生绿帧。

单帧入口保留，并完成同一源码的独立编译/链接检查；双帧实测的第一轮也执行了
共享connect/ack/present路径。此项验证有界帧更新与旧surface退役，不是持续负载、
资源回收压力测试、macOS GUI或完整WindowServer协议验证。

## 窗口输入、QEMU消费者与guest USB报告

固定源码中的正常键盘/指针路径已经接线：SDL事件经Reims窗口回调到
`apple-gfx.c`的事件队列/BH，再进入QEMU input层。键盘按USB HID usage page0x07
映射到QEMU Linux/qcode，指针按guest mode换算为绝对坐标。键盘要求窗口已有
正常focus；指针要求已有guest mode且事件落在图像区域。没有guest输入设备
handler时，QEMU input层会丢弃事件，窗口自身响应不代表guest收到。

原最小build没有xHCI/HID模型，而且`-nodefaults`禁止VMApple自动创建USB设备。
现已在`reims-arm64-tcg.mak`加入`USB_XHCI_PCI`、`USB_HID`和
`USB_TABLET_VMAPPLE`构建能力。当时仅重建graphics，摘要为
`fff712338f53852ee60b1bda5ddbaf44e53031e16d15340fb1ef5d57e15ebc2c`；更早graphics
GPU记录使用`cf6d4da1bc1d6890f94a59b59e1c53a0997ec62048f50281ed98f8e94363381c`，
当时bdif为`0273d0cccbb82554f6753cf54d70c5703e5627e52cc4fdbac8d64c4f49b08973`。
这些历史结果继续对应各自记录的binary，不追溯冒称新版验证。

随后为普通自给AES数据通路显式启用Nettle 3.10.2并重建两个研究binary；
当前graphics SHA为`d392abb3c29eae658ab256550ac81a435e2c6354d157a8a959cb3b3542010fce`，
当前bdif SHA为`b0123d45ebc127f6e1b46727c937174100ddd45b0d9adc17e2ce0e7c52ce91da`。
新版graphics的一次无窗口NVIDIA compute IRQ smoke
已通过：实际运行设备为RTX5070Ti Laptop GPU (`0x10de:0x2f18`)，256项数值与
全部guard检查、真实INTID49/stamp3工作完成均通过，guest退出0。此项只覆盖
普通graphics profile，未在新版上重跑窗口/输入矩阵。所有原有workload保持
`-nodefaults`及原设备集；只有输入workload显式添加设备。

无窗口设备级验证：

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload input --platform graphics --gpu nvidia
```

`probe-input.c`自行配置PCI slot3的虚拟qemu-xhci，建立命令/事件/EP0 transfer
ring，完成两个标准USB HID设备的枚举和配置，再用真实控制传输`GET_REPORT`
检查DMA报告。runner仅向这个自有VM发QMP合成A down/up、绝对坐标12345/23456与
左键down/up；不操作宿主键鼠。guest必须读到usage4后全零键盘报告、对应坐标和
按钮状态，不能由host填guest报告。2026-10-23实测14次成功xHCI transfer events
与全部内容通过，保留结果与QMP动作。
这验证EP0报告与event-ring轮询；不是HID interrupt-IN或控制器IRQ验证。
controller支持的4KiB DMA page与16KiB guest APV page、4KiB host mmap是不同合同。

完整原生窗口键盘闭环：

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload window-input --platform graphics --gpu nvidia --irq
```

此项只添加一个标准USB keyboard。`window_input.py`仅接受本次实际Popen及其
client XID，发送前再次核对parent/executable、`_NET_WM_PID`和该窗口已拥有的
native keyboard focus。只作两次`XSendEvent(propagate=false)`：定向A down/up；
不调用XTest、不移动鼠标、不改变全局focus，不操作其它窗口。若focus不属于
此client或SDL不接受事件，测试失败并回收，不自动重发或改成全局输入。

guest先呈现红帧；**实际USB HID读到A down后才绘制green**，并且必须真实读到
release全零报告。host分别核对同一自有窗口的红/绿原生像素，guest继续验证
GPU/display IRQ、count/mapping更新和旧surface退役。正常约10秒，guest每个键
阶段8秒内失败，host整体20秒上限；没有QMP输入代替这个native窗口动作。

2026-10-23唯一一次实际调用完成上述路径：X11 keycode38定向到同一自有窗口，
QEMU callback trace为`a down1/up0`，guest收到usage4/全零，两张原生图匹配。
初始检查器因两帧落在同一统计周期而误判失败；当前唯一结果保留
`initial_checker_passed=false`、旧条件与`reassessed_from_existing_run=true`。
修正后用原始数据执行完整case检查，保持两图/同PID-XID/HID/IRQ等判据，仅去掉
“两个统计周期”的错误前提，仍要求真实window GPU统计。没有重开窗口，原始
UART/stderr/XImage/PNG未改。

- native输入完整结果。
- 输入前原生红帧、
  guest收到输入后原生绿帧。

这证明自制guest的synthetic native-window键盘→SDL/Reims→QEMU→USB报告→绿色
GPU新帧路径。没有验证物理键盘、native鼠标/滚轮、macOS USB驱动或Apple真实系统
交互；QMP鼠标结果与native键盘结果保持独立。上述native闭环使用EP0 GET_REPORT；
标准HID interrupt-IN及控制器IRQ由下面的无窗口用例单独验证。

### HID interrupt-IN、xHCI完成中断与GIC

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload input-irq --platform graphics --gpu nvidia --irq
python3 experiments/E11-arm-reims/run-gpu.py --workload input-irq --platform graphics --gpu nvidia --irq --disable-work-irq
```

此profile在`-nodefaults`下只添加slot3的qemu-xhci及port1的usb-kbd，不开窗口，
不发送桌面事件。GPU不参与本用例；保留的Vulkan inventory仅记录相同runner的
环境，不能当作GPU执行证据。此IRQ探针不要求设备源码或额外构建变更。

固定板级路由来自VMApple create_pcie、
GPEX swizzle与
xHCI PCI中断实现：
xHCI声明INTA（PCI pin1，内部pin0），GPEX索引为`(slot3 + pin0) % 4 = 3`，
接到VMApple GIC输入`0x20+3`，因此guest IAR应为SPI **INTID67**。
`pci_set_irq(level)`使其为电平触发；不能复用GPU INTID49的edge配置。
板级另有GICv2m MSI桥，本用例不验证MSI/MSI-X；guest会检查其enable位均为0。

`probe-input.c`复用既有标准控制传输完成枚举，再读取实际USB configuration
descriptor。当前设备提供endpoint`0x81`、interrupt-IN、8字节、high-speed
`bInterval=7`。`guest-usb-irq.h`用Configure Endpoint命令建立DCI3与独立transfer
ring，提交Normal TRB，启动xHCI interrupter0/INTE和level SPI67。guest RAM分配
仍以16KiB对齐；xHCI的PAGESIZE合同是4KiB，两者不混用。

正例的成功等待只读取ISR完成状态，不轮询USB报告或event ring来代替通知。
ISR必须实际读取IAR67，看到IMAN.IP/IE及USBSTS.EINT，并校验Transfer Event的
type、slot、DCI、TRB指针、成功完成码及零剩余长度，随后捕获真实DMA报告。
正常清IP/EINT、推进ERDP/清EHB后才EOIR；收到A down后再提交下一项，A up也
必须经过相同ISR及报告检查。等待由guest counter限定5秒，不依赖host超时成功。

2026-10-23的正例
实际取得两次IAR67、IMAN=3、USBSTS=8，count1→2，8字节报告usage4→全零，
guest正常退出0。禁用SPI负例
保持控制器/端点运行，5秒后核验DMA中的usage4、成功Transfer Event、IMAN.IP及
GIC pending都已就绪，但ISR计数为0；guest按预期退出1，不能成为正常输入PASS。
每个run保留UART与QMP动作。此项验证标准HID键盘中断通知与正常ack后再接收，
该独立用例不宣称native SDL事件与interrupt-IN已在同一次调用组合，也不证明macOS驱动接入。

### 同一窗口的interrupt-IN输入与GPU/display双中断

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload window-input-irq --platform graphics --gpu nvidia --irq
```

此入口保留旧`window-input`的EP0行为，另将同一窗口的A down/up接到标准
interrupt-IN端点。guest公共ISR只读取一次IAR：INTID67交给USB模块，先清
IP/EINT/EHB电平源；INTID49继续读取GPU/display各自的read-clear事件；最后由
同一个分派器EOIR。两个GIC bank分别保留level67和edge49配置，互不替代。

guest先呈现真实GPU红帧，再提交USB IN TRB并声明ready。host在同一自有窗口
取到红图后仅定向一次A down；guest必须从ISR67取得usage4和匹配的Transfer
Event，才提交绿色GPU工作。第二次GPU/display49、全帧像素oracle、count1→2、
独立mapping2与旧surface退役继续受原判据约束；A up也必须成为ISR67的全零
报告。host最终必须从同一PID/XID取到绿色原生图，QMP不发送输入。
runner同时检查USB down ISR→green GPU→green display→USB release的UART
顺序、两次实际IAR67及GPU/display49证据，不能用独立run拼接通过。

运行仍仅操作本次QEMU自有且已有focus的窗口；不改变focus或全局输入。
默认自动关闭，正常约10秒，host上界20秒，USB每个等待阶段由guest计时5秒。

2026-10-23唯一一次NVIDIA组合调用直接通过，
guest退出0并回收窗口，无重评或重跑。实际QEMU stderr确认RTX5070Ti Laptop
GPU执行；同一次UART严格按red present→USB down ISR67→green GPU stamp9
ISR49→green present count2/mapping2→USB release ISR67完成。两次USB均
IMAN=3、USBSTS=8，报告usage4→全零；两次GPU完整帧均为1152个前景像素与
2944个蓝底像素，guard和旧ID1释放/poison检查通过。

独立原生红图
与原生绿图
来自同一PID175280/XID`0x1c0002f`的320×200 client区域；host看到green图时
red像素计数为0。输入仅为该自有且已有focus窗口的一次定向A down/up，无全局
输入/focus改变，也没有QMP输入。窗口GPU统计另提供真实提交证据。
这补上同次native输入→USB中断→guest生成新GPU帧→原生显示的组合路径；
范围仍是自制ARM guest，不能代替macOS USB/WindowServer驱动验证。

## Installed-OS WebGL2 fixed readback validation

The installed-OS path is separate from the standalone firmware runners above.
The [self-authored page](../E05-installed-boot/guest-diag/webgl-probe.html)
(`webgl2-six-tiles-v1`, source SHA256
`ac540a7ce9765c35f666d8cadfc9239a22be48c1604a7cde494beb1c041d0e4e`)
compiles a six-color WebGL2 shader and reads a 96×64 RGBA8 offscreen FBO and
default framebuffer. Each raw output is 24576 bytes, with bottom-left row
origin. The [independent host oracle](../../tools/validate-webgl-probe.py)
checks both entire outputs, fixed dimensions/format, shader/program status,
GL errors, context loss and upload metadata; it does not use a screenshot as
the pixel oracle.

The retained host control
ran actual Chrome with explicit SwiftShader and the real GuestFileService
loopback GET/PUT path. Both outputs match the independent expected SHA256
`d8da4828f987b4c3334729f48fdeb6036bd810c23d16526fcca6373c203355da`.
Eight negative controls cover byte/row changes, truncation, missing output,
lost context, incorrect dimensions/name and symlinks; all reject as expected.
Two glslang shader checks and `node --check` pass. Browser children and the
file service are stopped; a separate real-child cleanup-race regression
passes without rerunning the render control. This control uses a software
renderer, and establishes no macOS, physical GPU, Metal or performance result.
The masked Chrome string `WebKit WebGL` is not evidence of Safari execution.

Run128 completed the installed-OS consumer on the existing4a43 main binary,
network CTRL512 and the guarded finite30s diagnostic. The
actual macOS oracle
passes both entire24576B outputs with zero byte differences and the same
independent expected hash above. Actual context creation/dimensions,
shader/program status, GL error checks and PUTs pass. Native pixels show
upperCMY/lowerRGB tiles. The API reports `Apple GPU`; this does not establish
physical GPU execution, scanout, FPS, latency or general WebGL/Metal coverage.

The Safari analysis
retains one Safari-owned GPU1124: first client UIrequest→DidInitialize3.781s,
then the WebGL page connection create→DidInitialize13.716s. These gaps are
not measured blocking intervals. There are zero GPU watchdog requests but
two WebProcess unresponsive notifications. Final global site0 has one hit
onCPU2, site1 has none and rejections are zero, with unchanged per-CPU epochs.
No PID attribution or original Safari3s performance pass follows.

The two raw PUTs and JSON arrive during the ordinary watcher; its retained
timeline is133s, despite the120s request. No optional late window, reload or
authored PCM warmup was used. This new shader workload is not a matched
Run127 performance reproduction. The host transport index
records seven targeted methods (URL3/legacy directory1/GET-PUT-render2/helper1)
and separate shell syntax; synthetic transfer patterns are not rendered
outputs. Existing URLs remain unchanged; no global URL whitelist was added.

Run128 application
records guest-origin shutdown, QEMU/files exit0/reaped, no host quit and
alias cleanup. Controller1 is the unavailable post-exit final capture;
`errors=[]`, filesystem cleanliness is unproved and the5s wait was not attempted.
No VM is active. The user has requested project maintenance after this run,
with no Run129 or new feature experiments planned. Maintenance is next and
has not yet been executed; the long-term goal is not marked complete or paused.
