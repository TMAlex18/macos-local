> **Language:** [English](M1-EXACT-CLOCK-RESULTS.en.md) | **简体中文**

# M1：24MHz精确计时修复实验

日期：2026-10-14（UTC）；总计划v3，仍未启动Apple固件或macOS。

## 1. 原问题与修复

固定QEMU版本把1e9/24e6纳秒截断为41ns，以此同时模拟counter和timer deadline。这个内部一致的旧方案会让声明24MHz的时钟实际约24.39024MHz，guest时间比host快约1.626%。前轮测量已经观察到约1.6%的偏差。

本轮在独立工作树实现默认关闭的 `x-cntfrq-exact`。计数器使用floor(ns×Hz/1e9)，截止点使用ceil(tick×1e9/Hz)，超出QEMUTimer有符号范围时饱和到INT64_MAX；整数商/余数实现，无浮点。读取和调度成对修复，避免提前callback形成past-deadline循环。

同时处理WFIT/WFET和event stream的时间域：后者在精确模式下先计算下一个绝对counter边沿，再转换为ns。旧分支保留，不把新行为强推给所有既有虚拟机。

不改Apple DeviceTree、不更改host系统时间，也不以修改guest CNTFRQ寄存器本身冒充实际时钟速率修复。仍是实验性TCG配置，非上游默认能力。

## 2. 数学与实际客体测试

相同生产header经过独立128位算术对照：387,527组换算输入、1,920组事件边沿样例及溢出边界均通过；native测试启用UBSan/ASan。这不是整套QEMU sanitizer认证。

12个功能场景均符合预期：8正向、3个预期guest退出1的负对照，以及1个>1GHz配置启动拒绝。包括：

- 24MHz计数器、GIC PPI27、重复中断和MMU同时开启；
- 禁用PPI、不设只读权限的既有负例；
- WFIT/WFET绝对截止点、过去截止点立即继续；
- event stream先于超时、WFE下降沿、禁用event stream的负对照；
- 24MHz/1GHz分别96次短deadline触发，检查没有提前交付/丢失/重复；
- UINT64_MAX远期截止点不溢出成当前中断。

全部是单vCPU自制ARM64 EL1程序，不是Apple系统。QEMU与guest测试存在共享实现基础，不等于独立真实ARM硬件认证。

## 3. 同一二进制的速率对照

每组3次，每次约750ms，记录guest counter和host读取UART marker时的monotonic时钟。所有样本保留，不作筛选。数值见独立JSON。

| 模式 | 频率 | guest时间/host时间中位数 |
|---|---:|---:|
| 旧分支 | 24MHz | 1.0161359 |
| 精确分支 | 24MHz | 0.9998432 |
| 旧分支 | 1GHz | 0.9997839 |
| 精确分支 | 1GHz | 0.9998968 |
| 精确分支 | 25MHz | 0.9997898 |
| 精确分支 | 19.2MHz | 0.9998970 |

观察支持：原先约1.6%的系统性速率偏差在本轮精确模式中不再出现，控制组也接近1。剩余小差值包括host调度和观测误差；**不能把0.9998432解释为guest恒定慢0.01568%，也不能据此承诺ppm精度。** 宽松的回归阈值用于发现百分比量级错误，不代表产品SLO。

## 4. 隔离、复现和边界

`build/qemu-clock-exact/qemu-system-aarch64` 使用upstream pin `1a72df984914fb9929d35b1217619178133d65a4` + patch0003 + patch0005；与正常主线、headless负对照分离。此branch不包含AVP RTC/CTRR占位patch0004。

`max,cntfrq=24000000,x-cntfrq-exact=on` 才开启精确模式；默认off。没有改写主线默认频率或已有guest bundle。迁移、动态改频、KVM、全EL/多核时间同步和目标Apple OS仍未验证。

运行：

    make build-qemu-clock-exact
    make clock-math
    make clock-functional
    make clock-rate-exact

补丁重建检查将新增header纳入验证，源树无须将所有生成物加入Git。`make check-research-patches` 检查修改文件可由固定来源和patch恢复，不是空白机器全依赖重建。

## 5. 文档与后续主线

维护阶段已同步五个官方小组件、设备树24MHz、缺失AVP映射及原始时钟报告，保留来源/CRC与签名未验证的区别。后续可用此24MHz精确profile研究真实启动，但不能越过AVPBooter、AUX/身份/系统盘以及实际Apple ABI缺口。

优先继续E05匹配启动材料/恢复传输；并为EL0+MMU+PAC和多核时钟建立联合测试。真实GPU任务、输入延迟、吞吐与整机功耗依旧分开评估，不以本轮时钟修复宣称native体验。

参考接口：QEMU `include/qemu/timer.h` 的ns定时API；Arm `CNTKCTL_EL1` 的event方向定义。执行依据是本机固定源码与日志，不以当前网页替代锁定版本。
