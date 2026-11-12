> **Language:** [English](M1-BDIF-USB-TRANSPORT.en.md) | **简体中文**

# M1 / E05：BDIF USB 数据通道与用户态 USB/IP 控制请求

日期：2026-10-14（UTC）；总计划v3，仍为M1。此轮没有取得AVPBooter，也没有执行Apple引导代码或恢复macOS。

## 从源码候选到可执行验证

此前现机的BDIF只处理ROOT/AUX磁盘请求，USB设备编号虽定义但没有实现。候选来源：
https://github.com/steelbrain/experiment-macOS-arm64-on-asahi-linux-arm64/tree/657bb2423ec8d572b22a97dd33563dbba1680e6a

该项目成功的图形客体来自Mac预先安装的VM；其README将Linux原生恢复另列为未完成实验。我们没有把另一个平台的成功当成这台Intel笔记本已经恢复成功。

原始 vmapple-usb-chardev.patch 保留在 patches/upstream-reference。建立独立 sources/qemu-bdif-usb，不覆盖现有主线与精确时钟分支。编译固定现代QEMU + 既有VMApple/PAC/时钟补丁 + 候选USB通道，再使用自制ARM固件与Unix socket对端测试。

## 修复前后的因果对照

原候选六个测试中仅普通和分片传输通过。其余四例的guest明确RX_TIMEOUT：

| 失败样例 | 源码机制 | 本地修正 |
|---|---|---|
| 128KiB最大包 | 接收容量不包含4字节frame header，末4字节无法进入 | 容量同时容纳完整帧头与payload |
| RAM末端16字节描述符 | 为debug读取128字节，即使描述符本身完整也越过RAM边界 | 只读真实16字节descriptor |
| 大于当前guest buffer的包后跟合法包 | 丢弃第一包后直接返回，缓存里的第二包无人继续处理 | 有界处理后续完整帧、正确恢复读就绪 |
| 半包断线重连 | 连接关闭没有清理旧缓冲，下一连接被当作旧包续传 | OPEN/CLOSED事件清理连接内状态 |

另一个运行时问题：qemu_chr_fe_write_all在peer不读取时会阻塞。使用相同256帧数据和QMP操作对照：原候选等待恢复读取前不能响应query-status；改为有界非阻塞发送后可以响应。恢复读取后两者全部字节都能传完，说明测试区分了“最终数据正确”和“控制面是否被阻塞”，不是只看进程是否退出。

## 当前结果

- 16个传输场景全部符合预期。覆盖普通、分片、粘包、最大RX、最大TX、零长度、descriptor边界、非法DMA、超长TX、未提交buffer时不得DMA、no-data负例、半包断线重连、超大帧拒绝并重连、ROOT/AUX读取回归。
- 最大RX：131072字节；TX上限：4096字节，未扩张候选协议。
- 背压：256帧、每帧4096字节payload，加4字节header总计1049600字节全部一致；固定实现的QMP在peer暂停读取时仍响应。
- 同一新QEMU binary上重跑5个timer/GIC/MMU/组合/负例场景，全部符合预期，USB backend未配置时仍可运行平台路径。
- 记录的补丁能从固定QEMU提交精确重建11个修改文件。只证明source patch持久化，未声称空白宿主全部依赖可复现。

## 更上一层：USB/IP控制请求实际穿过guest

采用Linux官方USB/IP wire format：
https://docs.kernel.org/usb/usbip_protocol.html

当前链条是：**用户态USB/IP客户端 → socketpair控制网关 → AVP长度前缀Unix通道 → QEMU BDIF → 自制ARM guest → 返回响应**。

不是只用Python mock替代guest：control OUT的64字节数据实际由ARM程序保存，再经独立IN请求取回。设备描述符由该ARM程序产生，使用明确的合成测试身份，不冒充Apple DFU。状态字节也来自guest。

三组场景、12个检查全部符合预期：descriptor、控制IN/OUT、原始数据回读、正常status、空status保持为空、guest STALL、畸形reply返回EPROTO、静默reply返回ETIMEDOUT。负例完成后主动结束测试QEMU，不将其被结束当成guest通过。

**关键区别：没有把timeout/空包自动合成为DFU下载完成或WAIT_RESET。** 上游试验桥接脚本包含这种推断，本项目没有沿用。空IN响应是实际长度0，不被扩展为6字节成功状态；真正恢复工具能否正确处理需要实测。

该网关只支持串行OP_IMPORT与EP0控制传输，不含DEVLIST、其他端点、ISO、异步多请求、UNLINK取消；没有加载vhci-hcd、没有host USB设备挂接，也没有执行idevicerestore。

## 修复/实验边界

这不是完整USB控制器或可靠restore engine。还有descriptor链、真实firmware ABI、reset期间peer帧协调、transaction IDs、超时后的恢复策略、migration和完整生命周期等未验证项。当前reset清理只保证本地software state复位，不能声明外部peer也同步复位。

底层DMA、传输完成、USB控制响应、DFU协议状态、签名验证、AUX/OS安装是不同层。此轮闭合前几层的自制实验，不将它们混称恢复macOS成功。

## 对计划的影响

E05从“候选patch只做apply-check”推进到“本机可执行的BDIF + 用户态EP0传输原型”。下一步优先取得匹配AVPBooter，开始观察真正boot firmware的USB描述符/请求，再决定何时接入Linux内核USB/IP与恢复工具。真实初始化AUX、VM身份、系统盘和签名材料仍是M2前置条件。

复现：

    make build-qemu-bdif-usb
    make bdif-usb-tests
    make bdif-usb-backpressure
    make bdif-usb-control
    make check-research-patches

代码、范围与异常行为详见 experiments/E05-bdif-usb/README.md；数据在 runs/e05-bdif-usb。原始候选失败报告保留，不覆盖为全绿结果。
