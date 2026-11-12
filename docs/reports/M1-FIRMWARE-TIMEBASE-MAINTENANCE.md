> **Language:** [English](M1-FIRMWARE-TIMEBASE-MAINTENANCE.en.md) | **简体中文**

# M1 固件样本、设备树与24MHz时间契约：维护记录

记录日期：2026-10-14（UTC）。本文件先固定已经观察到的证据；精确计时修复结果另行追加，不能提前宣称完成。总计划仍为v3、阶段仍为M1。

## 已取得但没有执行的Apple组件

从锁定候选27.0.1/26A434的Apple CDN IPSW通过HTTP Range取得DeviceTree、LLB、iBoot、iBSS、iBEC五个小型IM4P文件，单次有效传输1,760,833字节；未下载整个26,637,307,067字节IPSW。原始文件只在private/official-restore-research/27.0.1-26A434，未入Git。

ZIP CRC、长度和本地SHA256已核验。它们不是Apple签名验证、完整归档摘要校验或成功启动证明。静态IM4P/LZFSE解析识别mBoot-20457.1.29，不能以iBoot或LLB替代尚未取得的AVPBooter。AUX、机器身份、已安装系统盘和Linux恢复链仍未闭合。

资料：runs/e05-source-audit/im4p-small-firmware-audit.json。提取脚本属于显式联网操作；设备树与MMIO比较在材料存在后可离线进行。

## 设备树直接观察

同一候选的DeviceTree解压61,208字节，递归解析139节点、1,272属性，恰好耗尽数据；32个CPU模板节点均声明timebase-frequency=24,000,000Hz。模板有32个CPU不等于系统实际运行32vCPU。

arm-io的child地址要经过ranges的0x10000000基址转换。与默认Reims QEMU实例的有效MMIO范围比较，avp-ctrr、avp-sealed-registers、avp-rtc三个节点没有对应设备映射。声明地址并不证明驱动必然访问；较大的DT预留窗口与较小QEMU寄存器范围也不能自动当成bug。

资料：runs/e05-source-audit/27-vmapple-dtree-contracts.json及27-adt-vs-qemu-mtree.json。已有RTC/CTRR默认关闭的占位实验另列；返回零/忽略写入不等于实现这两类设备，sealed-registers仍无实现。

## 24MHz误差的证据与机制

当前固定QEMU源码的gt_cntfrq_period_ns将每tick周期截断为整数纳秒。24MHz的理想周期是125/3纳秒；旧实现使用41ns，因此实际频率约24.3902439MHz，而CNTFRQ仍报告24MHz。预测guest时间/host时间=125/123≈1.01626016。

前轮三组各3次guest计数器等待样本，中位数：默认1GHz约0.9999、24MHz约1.0161、25MHz约0.9998。原始UART观察使用宿主monotonic时钟，包含观测和调度误差，不是硬件精密计时或应用性能测量。

资料：runs/e06-contracts-clock-rate.json；clock.c、run-clock.py。保持该原始报告，不以新实验覆盖基线。

不能只改计数器读取：定时器截止点必须使用同一数学时间域，按tick截止点向上取整到纳秒，避免提前callback重新排回过去而形成循环。WFIT/WFET和事件流相关路径也必须审计，长时间/超大截止点需有明确饱和边界。

## 继续执行的范围

先做默认关闭的精确计时实验，并保留原24MHz旧算法负对照。目标是保持24MHz声明与真实速率一致，不改Apple DeviceTree，也不改变宿主时钟。验证包括整数数学边界、guest计数器速率、timer/GIC重新触发、MMU+timer组合，以及等待指令相关行为。

这个修复即使通过，也不是macOS时间系统、OEM HVC或全套ARM架构认证；真实Apple启动材料与平台契约仍需独立推进。
