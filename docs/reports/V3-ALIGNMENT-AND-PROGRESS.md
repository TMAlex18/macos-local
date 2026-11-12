> **Language:** [English](V3-ALIGNMENT-AND-PROGRESS.en.md) | **简体中文**

# v3 对齐与继续执行记录

日期：2026-10-14（UTC）。总计划版本3；当前阶段M1，不是macOS启动/GUI交付。

本文件保留当时的对齐和命令通道证据。2026-10-23的guest硬件GPU计算及后续工作见[E11当前说明](../../experiments/E11-arm-reims/README.md)与[统一状态](../STATUS.md)，不将本报告的历史“未做”当作当前结论。

## 对齐了什么

完整总计划 `MASTER-PLAN.md` 覆盖原 v2 的产品目标、SLO、CPU/DBT、VMApple、图形/共享内存、显示/输入、恢复材料、存储与生命周期、测试、M0—M8、E01—E10、资源、学习、维护、授权范围；新增 E11 跨ISA图形接口实验。没有删减原生级目标，也没有把未来性能数值写成已测成绩。

主线明确为现代 Reims QEMU 的 ARM64/TCG 移植，而非继续要求老 Portable fork 接上新旧 Cargo ABI；旧 fork 和4KiB图形构建保留作对照。启动材料、timer/MMU与性能基线重新进入队列，不让 E11 挤掉其余工作。

`PLAN-STATE.json` 统一记录实验状态与带版本/线程说明的测试样本；`STATUS.md` 由脚本生成。前轮未提交的完整集成补丁/实验源码已保存到整合仓库，遗漏的 fixture修改另存补丁0004。历史文档标明快照角色。

## 后续修复：host指针对齐不由guest页大小决定

`tasks/remote.rs::whole_pages` 原先用 guest APV_PAGE_SIZE 同时检查宿主指针和范围长度。ARM guest为16KiB、Linux x86 host为4KiB时，合法host映射可能因此被拒绝，表现为 isolated task未创建。

修复把指针按 platform.vm().page_size() 检查；长度仍满足完整guest/host页条件，保留非零、空指针和非法对齐拒绝。新增host对齐但非guest对齐的成功测试，既有不合法输入测试仍保留。不是把检查删除或将所有host内存强行改成16KiB。

本机验证：FIFO模块25项通过；两个之前remote-task失败测试通过；串行完整Core：ARM16K 2294/2294、x86默认2300/2300，均0失败。两套测试人口因架构条件不同，不能把差额视为遗漏或跨ISA独立认证。

参考 Linux mmap 契约：https://man7.org/linux/man-pages/man2/mmap.2.html 。本项目的实际证据是源码、正反例和上述回归，而非仅依赖文档权威。

## 新增guest命令闭环

自制ARM64固件向同一Reims Root FIFO提交Nop(30)和GetDeviceInfo(58)：

1. guest在RAM写入12字节header和payload，发布doorbell；
2. Reims host线程消费命令，回写completion stamp；
3. GetDeviceInfo将结果写入另一个guest 16KiB页；
4. guest检查key1/sample-count、key2/bool、terminator、未触及的边界sentinel、消费字节数与fault标志；
5. 检查完成后关闭FIFO，guest退出0。

正向输出为 `FIFO NOP STAMP PASS` 和 `FIFO GET_DEVICE_INFO ROUNDTRIP PASS`。

同一固件的NO_DOORBELL变体不发布第一个doorbell，guest始终收不到completion，按有界poll失败并退出1；测试运行器将这个负对照标记为符合预期。host运行器不伪造结果，不向guest output写入。

这闭合了“guest命令→host消费→guest RAM结果/完成stamp”路径；没有draw/compute、shader、实际像素、GPU完成同步或IRQ处理验证，因此不是guest GPU渲染、WindowServer或macOS启动。

入口：`make reims-fifo-roundtrip`；源文件 `experiments/E11-arm-reims/probe-device-info.S`、`run-device-info.py`；结构化结果 `runs/v3-fifo-device-info-results.json`。

## 当前下一步

现阶段remote-task对齐和命令通道可收口；后续优先推进 E06 timer/MMU及E05材料审计，同时继续IOSurface真实descriptor操作和guest draw/compute。原生性能、系统应用、睡眠/更新仍未测试。
