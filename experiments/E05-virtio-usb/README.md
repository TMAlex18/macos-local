> **Language:** [English](README.en.md) | **简体中文**

# E05：恢复内核的普通 VirtIO USB 设备合同

本目录是该普通设备的规范说明与独立自制 ARM guest 夹具。它验证透明字节、
现代 VirtIO PCI、DMA、队列完成与中断，不执行 Apple 固件、不请求 TSS，
也不把 fixture response 当作 Apple USB ACK 或 restored 服务。

恢复内核需要的设备与 booter BDIF USB 是不同通路。固定 KernelCache 的
AppleVirtualPlatform personality 以 PCI `106b:1a01` 经
`AppleVirtIOPCITransport → AppleVirtIOUSBConfiguration → AppleVirtIOUSBDeviceController`
接入。设备为显式 opt-in：

```text
-chardev socket,id=kernelusbr,path=<owned-socket>,server=on,wait=off
-device vmapple-virtio-usb-pci,chardev=kernelusbr
```

实现采用 modern-only PCI、Q0/Q1 两个 split virtqueue，默认ring大小256；
Q0承载host→guest可写请求，Q1承载guest→host只读响应。ring大小是本地实现
选择，不将原Apple guest预投的四个128KiB请求buffer误写为ring大小。
不广告USB私有feature；夹具只协商 `VIRTIO_F_VERSION_1`。

## 已核对的普通 wire 字段

host chardev外层为 `u32 LE body_bytes + body`；这个4B长度前缀不进入guest
descriptor链。request body上限`0x20000`，response body至少2B，本地实现
response容量1MiB。设备传递guest真实字节，不制造Apple语义响应。

| guest队列中的body | 布局 |
| --- | --- |
| request | `u32 LE length@0, u8 endpoint@4, u8 type@5, payload@6` |
| response | `u8 type@0, u8 endpoint_or_value@1`，之后可接独立payload descriptor |

固定来源为 `26A434/kernelcache.release.vma2`，解压后SHA-256
`ca55ea8c4c86170e870380ae5501b8b43adb2d2d3db28be4531b3040653be203`，
fileset `com.apple.driver.AppleVirtualPlatform`。下列均为未加slide的原版代码地址；
只核对具名普通方法自身指令，没有解析vtable、认证对象、密钥或未知间接目标。

| 字段/行为 | 具名方法和原版位置 |
| --- | --- |
| request至少6B，payload为buffer的`+6/-6` | `AppleVirtIOUSBRequestTransaction::getRequestHeader` `0xfffffe0008aa35f8`；`getRequest` `0xfffffe0008aa375c–379c` |
| type1 transfer、type2 reset、type3 pause | `requestQueueEventHandler` `0xfffffe0008a8f840–f890` |
| response固定2B头 | `AppleVirtIOUSBResponseTransaction::transaction` `0xfffffe0008a94970–9497c` |
| data response写`type1, endpoint` | `setDataResponse` `0xfffffe0008a94ab0–ab8` |
| control response写`type, endpoint_or_value` | `setControlResponse` `0xfffffe0008a94bf4–bf8` |
| data段从response offset2开始 | `getBufferSegment` `0xfffffe0008a94c54–cd8` |

非iso OUT将header length作为inline数据长度，检查其不超过payload和guest IO
buffer容量；IN以endpoint bit7区分，不复制inline payload，使用guest IO的
descriptor及长度生成响应。当前所核对的IN分支没有用host length截断响应，
不能把该word直接当作IN最大接收量。依据为`enqueuePendingRequestForTransaction`
`0xfffffe0008a94394–43d0`和`startNonIsocEndpoint`
`0xfffffe0008a8f350–f3cc`。

EP0有独立规则：type1/endpoint0进入控制处理，type1 / endpoint `0x80` 请求被拒；
guest的EP0 IN由`startEp0In`主动产生，响应第二字节是0，且可产生零payload
响应（`0xfffffe0008a8f190–f218`）。`handleSetupRequest`读取前8B setup，
内部状态5/6分支检查`8+wLength≤payload长度`后复制`payload+8`
（`0xfffffe0008a8ff50–ffcc`）；这里不另行推定内部状态枚举名称。

已核对response type1为data/OUT确认，type2为EP0 stall，type3为endpoint
abort，type4为reset回应（第二字节固定`0x10`，枚举含义未确认），type5为
endpoint pause回应。`handlePauseEndpointRequest`只清该endpoint的host
pending项（`0xfffffe0008a8fd94–fec8`）；`handleAbortEndpoint`还清active
guest IO并放入completion队列（`0xfffffe0008a8e93c–eaac`）。响应经host正常
used归还后，`responseQueueEventHandler`才将关联IO送入完成队列
（`0xfffffe0008a8f9dc–f9f8`），`completeBuffers`清payload引用
（`0xfffffe0008a94e90`）。不能仅取走数据而省略virtqueue完成。

这些是可核对字段，不是完整USB/IP URB ABI。仍未闭合逐URB取消关联及竞争、
timeout/断开顺序、IN chunk/short/ZLP策略和reset值`0x10`的语义。6B头没有
已观察到的transfer-ID或独立errno32字段；不得自行添加或推导未知帧。

## 原版驱动的启动与正常激活

固定内核中，`AppleVirtIOUSBConfiguration::start` 位于
`0xfffffe0008a95220`，`AppleVirtIOUSBDeviceController::start` 位于
`0xfffffe0008a8d898`；后者在 `0xfffffe0008a8da3c` 直接调用
`IOUSBDeviceController::readyForHost`。这些入口只证明匹配和启动执行，
不证明queue创建、USB描述符配置或host枚举。

实际queue创建在 `AppleVirtIOUSBDeviceController::goOnBus()`
`0xfffffe0008a8e00c–8a8e450`：Q0/Q1分别绑定request/response handler，
随后预投四个128KiB请求buffer。VERSION_1检查和feature/status协商也在这里。
因此尚未进入该方法时，只有ACKNOWLEDGE、queue地址为零，不足以归因为
feature不兼容；config_size=0、ring大小256仍是本地选择，不是原生测量值。

同一原版KernelCache的普通IOUSBDeviceFamily方法还明确区分恢复启动：

- `IOUSBDeviceConfigurator::start` (`0xfffffe00097295d8–97296b0`)
  在 `9729614–9729628` 检查 `-restore`；存在时跳到 `9729694` 返回，
  跳过DeviceDescription读取及 `9729678` 的createUSBDevice调用。
- `IOUSBDeviceController::readyForHost` (`9716b64–9716c74`) 读取并转换
  DeviceDescription，在 `9716c18` 调用createUSBDevice，之后发布；它不检查
  该返回值，也不直接调用goOnBus。createUSBDevice (`9721c28–97241fc`)
  在 `9721c68` 拒绝NULL描述。
- 正常setProperties/gated_setProperties识别USBDeviceCommand与
  USBDeviceCommandParameter；SetDeviceDescription分支在 `97245c8`
  调用createUSBDevice。该API的调用方权限检查保留，未扩展其实现。

这支持恢复模式下等待正常配置调用的解释，不证明具体用户进程已经启动、
调用或失败。没有通过host强制设置guest描述、配置状态或feature。

现有原版恢复程序的普通CreateWithService、DescriptionCreateFromControllerWithType、
SetDescription直接调用及固定来源保存在RAMDisk结果
的`restored_usb_startup_static`；该处是用户态静态证据的唯一来源。内核运行观察
使用五个独立one-shot点：readyForHost内createUSBDevice调用前/紧邻返回位置、
SetDeviceDescription直接tail-call前、startUSBStack和goOnBus(bool)入口。只记录
描述参数非NULL布尔、返回位置当前W0及on_bus布尔，不读对象，也不将未配对的
前后事件认作同次调用成功。

## 自制 guest 实际验证入口

```sh
python3 -B experiments/E05-virtio-usb/run.py --build-only
python3 -B experiments/E05-virtio-usb/run.py --expect-binary-sha256 <main-confirmed-sha256>
python3 -B experiments/E05-virtio-usb/run.py --case q0-small --expect-binary-sha256 <main-confirmed-sha256>
```

`--build-only`只编译，`--help`不构建/启动VM。实际运行先核对当前BDIF build
record的binary摘要及opt-in device存在，再创建唯一
`runs/e05-virtio-usb/<UTC>/`。原QEMU源码/库/bdif binary由主线程负责，
本夹具不修改它们。

`fixture.c`直接复用E06的自制EL1 runtime/向量/linker，动态探测PCI slot3的
BAR大小与普通现代cap链，按真实cap地址配置common/notify/ISR、feature/status
和Q0/Q1。只用正常PCI memory/bus-master使能。slot3 INTA经GPEX索引3接到
VMApple GIC输入`0x23`，所以是 **level INTID67**；ISR读清VirtIO ISR后才EOIR，
成功等待必须看到ISR记录的used index，单独DMA/used变化不能替代中断。
guest内以counter限定等待，host timeout永远失败。配置4GiB/2CPU/24MHz exact，
仅CPU0执行，16KiB对齐分配；不测试PAC/CTRR保护语义。

正例依次进行：

1. Q0预投6B header与独立大payload可写SG；host将4B外层前缀分成1/1/2B并
   分裂body。guest独立核对type1/ep4、48B fixture及整个header/payload
   allocation的canary，验证真实DMA及used length 54、IRQ67。
2. guest计算64B fixture response，用2B头及独立payload只读SG提交Q1；host
   逐字节核对原始wire，guest等待used length 0及IRQ后检查输入/guard未变。
3. 再投Q0/Q1，使用17B请求和113B响应，重复数据/used/ISR及guard检查，验证
   正常确认和再用，不假定一命令必须一中断。
4. guest提交512KiB非恒定payload响应。host在此阶段2秒不读取socket，guest
   必须至少半秒看到Q1 used与完成ISR均未提前推进；host还要求首次读取前
   没有guest大响应完成标记。之后逐字节接收全部wire，再要求used/IRQ及
   readonly payload/guard检查通过。这实际区分背压中的待完成与数据丢失，
   不把内部缓冲接收或host超时当作完成。

当前runner还使用production
`capture_virtio_devices(..., allow_usb=True)`进行实际只读QMP诊断：以`-S`
开始，先在reset/prelaunch停止状态查询；继续guest至DRIVER_OK且尚未发送
fixture请求时，再查询一次。两阶段都要求USB内部device-id `0x100`、完整的
host/guest/backend features对象、两条queue及查询后QEMU存活。reset的desc/used
应为0，DRIVER_OK应有真实desc且协商VERSION_1。这个内部ID不等于PCI device
`0x1a01`。完整原始QMP回复在每个新run的`qmp.json`，没有读取guest MMIO或ISR。

fixture数据由host正常发送请求、guest自行校验/生成响应，host从socket取得
实际字节；没有host写guest响应内存。raw wire、UART/stderr、QMP及结构化结果
保留在同一run，正常及故障路径都reap自有QEMU、关闭socket并移除临时stub盘。
不连接Apple会话、不打开窗口、不接管真实USB设备，也不宣称原Apple驱动已通过。

## 已取得的真实结果

2026-10-23在主线程构建的0015版本上，完整正例首次直接通过：
规范结果、
原始UART、
QEMU stderr。
binary SHA-256为
`df44bffecaa348d3aa5d95fc0b4d26756134897d26d1eee560a1d2f9abebd589`，
fixture SHA-256为
`2c8030c62138e6348fc6ec2941e366ecb7996fb71d58c0adf81423162a40acab`。
没有失败重跑或结果重评。

guest实际探测BAR1=`0x51000000/4KiB`、BAR4=`0x51004000/16KiB`，common=
`0x51004000`、ISR=`0x51005000`、notify=`0x51007000`；device features为
low `0x30000000` / high `1`，仅协商VERSION_1。QMP启动快照确认slot3设备ID，
其中尚未映射的BAR是guest配置前状态，不能用来否定其后UART记录的DMA执行。

Q0 used依次为`(index 1, len 54)`、`(index 2, len 23)`，Q1 used为index 1/2/3、
length均0。五次完成均由实际IAR67、ISR bitmap1确认；config IRQ计数0。
两轮请求的实际DMA/SG/guard和响应完整字节、readonly buffer检查均通过。
大响应原始wire
的body为524290B（2B头+512KiB数据），逐字节匹配，body SHA-256为
`005df533b1dd6d25dc8a1cdfef93ff2c2328fb5a76bd9c788ebd4244d5ba8968`。
host实际延迟2.0099秒未读，guest先确认used/ISR未推进，首次host读取前没有
guest完成标记；随后完整接收和真实IRQ完成，数据与guard不变。记录的socket
SO_RCVBUF为8192，但不把该选项本身当作背压证明，证明来自实际guest与wire。
guest正常退出0，owned QEMU已reap，临时socket/stub盘目录实际删除。

另一个独立Q0容量不足负例
也首次实际得到预期拒绝，正例未重跑。host发送同一合法54B body，guest仅提供
6B+8B可写SG容量14B；设备在copy前拒绝，实际stderr为
`Q0 requires a writable chain large enough for the body`。guest读取status
`0x4f`（含标准DEVICE_NEEDS_RESET bit6）、实际config INTID67/bitmap3，
Q0 used index仍0；整个header/data allocation及guard均保持`0xa5`。
即使error notification同时置ISR queue位，未变化的used index也不能冒充完成。
guest输出明确负例标志后退出1，host无timeout，没有实际response或正常完成
标志。原始UART、
设备错误
及发送wire均保留，进程/临时目录清理通过。负例固件SHA为
`5406df1c2807ed7cf7a9f09cc807cdde51633f21381b728d1298cd4e8fe274fe`。
加入条件编译后，两项固件重新编译仍与各自实际执行时摘要一致，没有重跑VM。

上述自制guest验证了本地普通透明device的协议/队列闭环；原Apple控制器的
独立集成结果见下文。自制fixture不能替代EP0描述符、bulk-IN服务或URB取消验证。

### 内部ID `0x100` 的QMP诊断回归

原版集成首次调用`x-query-virtio-status`时，QMP feature decoder未登记该内部
ID，触发宿主assert/abort；当时未到bootx，不能解释为USB数据路径失败。
原始失败日志
保留。主线程在0015内将此ID登记为仅transport features，未知private位仍保留
unknown；没有改变设备数据路径。

修正后新binary SHA为
`5ae35ab669adb8e00418d51481e88679ded777c91fe79c0c9a353f8149ece6dd`。
一次更新正例
直接通过，原df44版本正负两结果原样保留，Q0-small负例未重跑。自制guest固件
仍为相同`2c8030…acab`。实际production查询在reset/prelaunch及DRIVER_OK两阶段
均返回device-id 256、三组features对象与queue0/1，dev-features为空，host仅
INDIRECT_DESC/EVENT_IDX/VERSION_1，guest仅VERSION_1，backend为空。
reset两queue的desc/used为0；运行后两desc非零，DRIVER_OK成立；前后query-status
均响应、QEMU存活，无断言。原始回复见QMP记录。
此版本的两轮实际SG/DMA/IRQ67/guard及512KiB背压也全部通过，host延迟2.0081秒
读取，无提前完成；guest退出0，进程reap与临时目录清理成立。此项只验证该
实际失败的诊断回归及相同正向通路，不额外声称未知private feature位矩阵通过。

### 原版内核的独立集成

`m2-virtio-usb-20261023-02` 在当前5ae35a版本完成1800.335秒窗口；
原始PCI表含106b:1a01，Configuration/start/readyForHost自然断点分别在
498.819/500.072/502.998秒命中。根回调正常返回，1082.065秒全CPU暂停确认
真实EL0，具体进程未知。没有命中goOnBus()；末期USB仅ACKNOWLEDGE，两个
queue的desc/used为零，正常reset未获得wire响应，status=-110。故未发送
EP0请求，也没有USB枚举/restored服务证明。内核观察passed=true，runner因
USB探针退出5；QEMU quit 0 / reap和原AUX/root保留均成立。
实际运行与探针、
保留状态。

这已将剩余问题缩小到正常描述提交/激活及后续路径；根据上文静态启动合同，
未进goOnBus时的ACK状态不能当作feature协商失败。源01的宿主诊断异常与本轮
正常退出分别保留，不能将hard-link别名称为旧状态快照。

后续 `m2-usb-activation-20261023-01` 加入五个普通激活观察点，1800.145秒
窗口内记录createUSBDevice调用前description为NULL（490.716秒），独立返回
位置W0为0xe00002c2（490.967秒）；没有receiver/thread/SP配对。正常
SetDeviceDescription、startUSBStack和goOnBus未命中，reset仍无帧、未发EP0。
同 run 893.598 秒稳定EL0成立，QEMU quit 0 / reap及目标保留通过。该结果把下一项
定位放在普通用户态执行位置和描述提交之前，不证明服务从未执行或特性协商
失败。激活观察原始结果。
