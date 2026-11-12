> **Language:** [English](M1-EL0-SIMD32.en.md) | **简体中文**

# M1：完整 Q0–Q31 SIMD/FPCR/FPSR/TLS 与 MMU/ASID/PAC/IRQ 联合验证

单核样本日期：2026-10-16；双核迁移扩展：2026-10-23。单核起点 bd06949；实验位于 experiments/E06-el0-simd32，
**没有覆盖**已通过的 E06-el0-fp-tls（仅Q8/Q9）或整数上下文实验。无 Apple
固件、TSS、虚拟系统安装、物理USB和图形工作负载。

## 不只是复制原来的小测试

一个ARM64 TCG vCPU上的两个EL0任务在相同的用户虚拟地址运行，
通过不同ASID/TTBR0映射各自的真实物理数据页和用户栈；不同的PAC
指针认证密钥保护函数指针/栈返回地址；真实GICv3虚拟定时器PPI27
中断进入EL1，切换任务后用ERET恢复。每次切换不全量TLBI。

新增加：所有32个AArch64/AdvSIMD Q0–Q31寄存器、16字节×32=512字节
保存与恢复。专用AArch64 asm对所有向量寄存器执行16组STP和16组LDP；
FPCR/FPSR也由真实MRS/MSR操作保存/恢复，TPIDR_EL0代表真实线程
本地存储上下文。C中断代码坚持使用 -mgeneral-regs-only 编译以避免
编译器偷偷使用SIMD寄存器。

两个EL0任务为全部Q0–Q31的64个uint64独立选择不同数据模式；
每次EL0工作循环**实际把所有Q寄存器写入客体RAM，再逐字节等价比较
自身预期内容**。结束阶段还读取EL1保存的完整向量并比较。FPCR/FPSR/TLS
在用户循环内分别读取、验证。程序同时继续执行原PAC/ASID/IRQ/SVC/
权限故障路径。负对照故意不恢复Q0/Q1、Q16/Q17、Q30/Q31任一组，
均必须在客体内产生实际数据串扰报告，不能靠“测试退出1”掩盖超时。

## 新一轮结果

| QEMU配置 | 12轮基础（正常+10预期负例） | 64轮延长 |
|---|---:|---:|
| Portable VMApple无GPU | 11/11 | 1/1 |
| Reims VMApple | 11/11 | 1/1 |
| Reims＋默认关闭的实验PAC HVC软件模型 | 11/11 | 1/1 |
| 独立24MHz精确计时分支 | 11/11 | 1/1 |

**44/44 基础场景符合预期（4正向＋40预期负例），4/4扩展正例通过。**
四个扩展样本每个任务64次真实EL0 IRQ，合计512次IRQ与516次上下文切换。
24MHz分支由客体实际读取CNTFRQ=24000000，另外三配置读到1GHz。
既有错误PAC key、数据UXN解除、PPI禁用和FPCR/FPSR/TLS恢复失败
等负例继续有效；新增Q0、Q16、Q31这三组漏恢复负例也稳定触发。

这是**Q0–Q31完整AdvSIMD寄存器集合在上述有限上下文切换中的行为验证**，
不是整个ARM处理器的完整上下文：SVE/SME/FPMR、FP异常注入与lazy
trap、嵌套IRQ、长期稳定性及真实XNU尚未验证；这些单核样本也不覆盖下述双核迁移。
三个QEMU构建有较多共享代码，也不是三套独立硬件认证；统计的IRQ
与上下文切换不是系统性能基准。

复现：

    make el0-simd32-tests
    make el0-simd32-extended

结果 docs/reports/M1-EL0-SIMD32-RESULTS.json；
原始UART和逐场景结果保留在runs/e06-el0-simd32/。

## 双 vCPU 用户任务迁移

同一夹具新增显式 `migration.py` 入口，复用原EL0代码、SIMD保存汇编和双核启动向量。两个CPU分别执行EL0任务，实际PPI27中断后保存整数帧、SP、TTBR0、Q0..Q31、FPCR/FPSR、TLS及架构PAuth状态；双方通过release/acquire发布完成状态并原子认领对方任务。同一任务不会同时在两CPU运行，每次迁移也不全量清空TLB。

CPU身份在每颗CPU初始化到只读的TPIDRRO_EL0，异常入口用MPIDR核对；EL0在数据检查和实际签名返回之后记录所在CPU。每个任务都有两CPU的非零检查、mask=3及两CPU上签名栈帧内被中断的证据。QMP同时核对MTTCG使用两个宿主vCPU线程、single模式共用一个线程。两种模式使用同一研究QEMU和精确24MHz配置，不能当成独立硬件验证。

| 独立样本 | 结果与范围 |
|---|---|
| 16轮架构QARMA5，multi与single各一窗口 | 两任务均在两CPU运行，每窗口32次EL0 IRQ、32次迁移 |
| TLS、Q30/Q31、key恢复负例 | 两模式下均为明确guest错误和exit1，未以宿主超时判通过 |
| 错误TTBR0恢复的收紧复验 | 两模式均识别；同时改变页表base和ASID，不是独立ASID-tag负例 |
| 64轮架构QARMA5，两个模式 | 每任务每CPU32次IRQ，其中30—32次落在签名栈帧内；每窗口128次迁移 |
| 原单核clock-exact正向回归 | 12轮通过；没有把历史44+4样本宣称为本次重跑 |
| 显式软件key-context模型迁移 | 16轮multi/single正向及两模式wrong-key负例分别通过；每CPU独立初始化，正向各完成21次EL1 APIA bank检查 |
| 原单核reims-model正向回归 | 模型初始化重构后12轮通过，保持该原配置的1GHz时钟 |

原始结果的逐项路径及各自范围保存在[结构化报告](M1-EL0-SIMD32-RESULTS.json)的 `cross_vcpu_migration`。负例最初出现双CPU串口字符交错，夹具现用原子认领的单一失败报告者输出错误；原始运行记录保留。上述结果来自不同调用，不能拼成一次完整矩阵。

显式 `--pac-model` 使用项目自有软件上下文模型。各CPU独立初始化并核对EL1 kernel bank；任务恢复使用相同diversifier，没有把EL1 key读数当成用户态密钥保存，也不输出随机key值。架构与model构建目录相互独立。这是本项目模型的功能验证，不证明Apple硬件或加密语义等价。

复现架构迁移：`make el0-migration-tests`；延长：`make el0-migration-extended`；显式模型：`make el0-migration-model-tests`。这验证了两个人工任务在两个vCPU上的有限上下文行为，未建立真实OS多核调度、SVE/SME/FPMR、完整浮点算术异常、嵌套IRQ、DMA/IOMMU、长期稳定性或性能结论，也未解决M2平台根回调。

## 首次FP访问时恢复状态

`--lazy-fp` 是独立的架构QARMA5模式，与 `--pac-model` 互斥。每次任务迁移后设置CPACR_EL1.FPEN=01，允许EL1操作FP、让EL0首次FP/SIMD访问陷入。35条可陷入指令均在执行前用独立ADR把自身地址记录到x24；异常检查EL0来源、EC=7、syndrome及ELR与x24精确相等，恢复完整Q/FP状态后保持ELR重试同一条指令。只判断“ELR落在某条FP指令上”不足以通过。

每CPU还跟踪FP状态是否属于当前任务。尚未使用FP就再次收到IRQ时，保留任务已保存的状态，避免把CPU里旧任务的寄存器写回。lazy专用的整数前导实际等待第一次PPI27，因此这个分支在两CPU都被执行，并非仅靠检查代码推断。

multi/single各一个16轮正向窗口通过：每CPU15次精确陷入和恢复，每个任务在两CPU均有7/8次FP trap，最终EL0 mask=3。遗漏恢复的两个负例由EL0完整数据比较报错，guest exit1，无host timeout；只有故障注入分支会停用本CPU定时器，让数据错误先于下一次EL1 owner检查报告。初版负例被owner检查提前拦截的原始记录保留，未作为最终数据比较证据。

入口：`make el0-migration-lazy-fp-tests`。逐项原始路径见[结构化报告](M1-EL0-SIMD32-RESULTS.json)的 `cross_vcpu_migration.lazy_fp`。默认eager及model重编译的固件与先前输入逐字节一致；此次没有重复它们的运行矩阵。此结果不覆盖浮点算术异常、SVE/SME、真实OS或lazy模式与软件key模型的组合。
