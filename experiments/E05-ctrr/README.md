> **Language:** [English](README.en.md) | **简体中文**

# CTRR 本地契约与 RAM 缓存路径

当前 opt-in QEMU 模型保留正常 RAM 读/取指缓存，并对启用 READ_ONLY 的
范围保留普通 CPU/DMA 写入拒绝。补丁
[`0016-vmapple-ctrr-shared-romd-ram.patch`](../../patches/qemu-platform/0016-vmapple-ctrr-shared-romd-ram.patch)
用一个覆盖原 RAM 的 ROMD 视图和各范围 alias 替代纯 MMIO 覆盖：

- ROMD 视图借用同一 RAMBlock、宿主字节及 TB/SMC 地址身份，持有 backing
  owner 引用；不分配第二个 RAMBlock、不复制代码、不重复登记 RAM 迁移。
- `ram=false`，普通写仍进入回调并返回 `MEMTX_ERROR`。这与 readonly RAM
  静默丢弃 CPU 写的路径不同。普通读/取指走标准直接 RAM 路径。
- enabled 但没有 READ_ONLY 的范围保留普通 RAM 路径；CPU 写及 SMC 失效
  使用原 RAM 的机制。active permission 更新、lock、范围验证保持原合同。
- reset/unrealize 移除全部范围映射；ROMD leaf/alias 保持至 owner 最终析构，
  再释放借用引用，避免旧 RCU 视图悬空。允许同 backing 的 re-realize，禁止
  将已经初始化的视图换绑到另一个 RAM。

QEMU 的管理员 debug/ROM-loading 写继续遵循标准 ROMD 特权语义，可直接
修改 backing；这不属于普通客体 CPU/DMA 的 CTRR 边界。本地 fixture 显式
使用该特权路径验证共享字节和 SMC，同时另行验证客体 CPU 写 fault 及真实
BDIF address-space DMA 写失败。没有修改 PAuth、Apple 输入或宿主保护权限，
也没有扩展迁移支持。此本地研究实现不构成 Apple 硬件语义等价证明。

## 验证入口

```sh
make build-qemu-bdif-usb
make ctrr-contract-tests
make ctrr-qemu-guest-tests
```

`contract_model.py` 的15项纯 Python 测试覆盖16个范围、对齐、RAM包含、重叠、
lock、read-only及控制状态转换。模型默认16 KiB粒度；QEMU设备的现有粒度由
寄存器 `0x10`报告，当前 Linux宿主为4 KiB，此次未改该行为。

`guest.c` 是自制 EL1 程序。`run.py` 在当前实际研究二进制上执行它，使用
独立临时 AUX/root 文件和独占 QMP/RSP socket，不使用 Apple固件/TSS或保留盘。
完整 fixture 验证：

- 保护范围内的 RAM 函数反复执行，保留 `exec,nochain,in_asm` 原始TCG日志；
  按实际JIT TB地址复用及 `CF_COUNT_MASK` 判断，不使用计时阈值。
- CPU写产生同步外部写abort，512字节内容不变；BDIF普通DMA写收到失败状态，
  同一区域的普通DMA读/磁盘读回逐字节一致。
- 重叠、未对齐、倒置和RAM外范围拒绝，保留寄存器/lock规则。
- disable→合法修改→重新保护，以及enabled RW下修改，均执行新指令；
  同值代码跨RAM/ROMD映射继续复用TB。
- 特权debug修改后protected读取可见、代码更新无陈旧TB；unrealize/re-realize
  后仍是原RAM数据；machine reset清除全部range/lock并能重新保护和写fault。
- 三个阶段的 `info ramblock` 与启动前完全相同，未增加/替换RAMBlock；同时保存
  `info jit` 和 `info mtree -f` 供复核。

默认结果在 `runs/e05-ctrr-qemu-guest/results.json`。`--output-dir` 可选择独立
结果目录；`--baseline` 只执行短代码/fault/DMA部分。`--expect-cache uncached`
用于旧MMIO二进制的基线检查；当前修复版默认要求可缓存。

## 已取得的真实结果

| 样本 | 同一RAM入口执行次数 | 不同JIT入口TB | 强制单指令条目 |
|---|---:|---:|---:|
| 旧MMIO基线 | 65 | 65 | 64 |
| 修复后同字节短fixture | 65 | 1 | 0 |
| 完整生命周期/SMC fixture | 516 | 6（六个代码版本） | 0 |

两次短fixture的输入二进制摘要相同。完整样本退出0，CPU fault、DMA错误和
共享RAM/生命周期检查全部通过；它只验证自制guest设备合同，原始正常恢复路径
仍由主线单独验证。汇总、构建元数据和原始日志位于
[`runs/m2-ctrr-ram-cache-20261030/`](../../runs/m2-ctrr-ram-cache-20261030/)。
