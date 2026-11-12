> **Language:** [English](M1-SMP-MMU.en.md) | **简体中文**

# M1：双核地址空间、跨核TLB失效与原子更新实测

日期：2026-10-15（UTC）。基线提交90c01b7。现行总计划为MASTER-PLAN.md；结构化结果M1-SMP-MMU-RESULTS.json。

## 执行范围与未完成的M2

本轮先尝试现存的正常签名iBEC入口。job_start被执行层安全检查阻止，没有job id，指定日志未创建；没有切换执行器重试。本轮0个新Apple固件会话、0次TSS请求、0字节iBEC上传。

接着执行的是独立M1平台工作：**自制ARM64固件、现有QEMU二进制、空白临时AUX/root盘**。不读取Apple固件或票据，不连接USB/IP，不改宿主权限、内核模块或QEMU源码。这不能计入iBEC/Stage2启动成果。

## 为什么不只验证PSCI CPU_ON

旧实验验证了第二核能启动并通过RAM通知第一核，但不能推导出启用MMU后的地址空间切换、跨核映射失效、撤销权限与原子并发都正确。

本次两个vCPU各自进入EL1，设置独立栈和异常向量，开启16KiB粒度CPU MMU。CPU0操作页表，CPU1执行实际内存访问；用户态host脚本不写入结果或替客体制造异常。

这里是CPU翻译页表，不是Reims的APV页号。TLBI VAE1IS操作数使用VA[55:12]，16KiB粒度不将操作数改成VA>>14；该边界遵循ARM寄存器定义。

## 实测契约

### 1. 跨核break-before-make与VAE1IS

CPU1先访问VA 0xa0000000使该映射被实际使用。CPU0在对端访问暂停时将PTE置无效，执行DSB ISHST、带ASID的VAE1IS、DSB ISH/ISB，再写入新PTE和相应屏障。

CPU1在同一上下文下再次访问，无本地TLBI操作。测试在物理页0x70200000和0x70204000之间来回映射，逐轮检查读值、写回的目标页和未被触及的另一个页。基础每场景32轮，扩展每场景256轮。

### 2. ASID区分与复用

ASID 0x11/0x22两套页表将同一个VA映射到不同物理页，使用non-global叶项。CPU1切换TTBR0上下文，CPU0保持原来的ASID；每轮确认一方写入不会落到另一地址空间。ASIDE1IS参与更换映射与ASID复用，再返回原ASID确认预期数据。

QEMU可能在TTBR切换或维护操作时额外失效TLB。这里验证外部可观察的正确映射，不宣称实现了最小失效范围或真实CPU的TLB保留策略。

### 3. 跨核权限撤销、异常与恢复

CPU1先读写可访问映射，CPU0改为EL1只读并广播失效。CPU1执行已知store指令，异常处理程序检查EC=同EL Data Abort、FSC=level3 permission fault、WnR=write、FAR和准确ELR。

每个正向场景产生8次真实CPU1权限异常；对应ESR为0x9600004f、FAR为0xa0000000，目标物理页没有被拒绝的store修改。恢复写权限并失效后，后续store必须成功。

### 4. 真正的双线程与原子更新

测试分别显式选择TCG thread=single/multi，并在暂停状态由QMP query-cpus-fast核对：单线程有1个共享host线程ID，多线程有2个不同vCPU host线程ID。

两核各执行50,000次LDAXR/STLXR自增，最终必须为100,000。另记录exclusive-store重试数：最终多线程正向样本都观察到非零重试，最终计数仍精确。重试受调度和模拟实现影响，不作为固定断言或吞吐指标。

## 对照与结果

| 范围 | 场景数 | 结果 |
|---|---:|---|
| 2份QEMU × single/multi × 正向32轮 | 4 | guest退出0，全部契约满足 |
| 同一矩阵，故意不改旧物理映射 | 4 | guest退出1，准确报告REMOTE_TRANSLATION_WRONG |
| 同一矩阵，故意取消只读保护 | 4 | guest退出1，准确报告MISSING_REMOTE_PERMISSION_FAULT |
| 两份QEMU，multi模式扩展256轮 | 2 | guest退出0，全部契约满足 |

**14个最终场景符合预期：6正向＋8预期负例，0意外失败。** 开发过程中早期运行保留在runs中，不与最终样本重复累加。

6个正向场景合计：640轮跨核重映射、640轮ASID隔离、48次远端权限异常、600,000次已核对最终计数的原子增量。

负例通过故意错误PTE条件构造；不将“省略TLBI必须保留旧缓存”当成普适规则，因为架构不保证TLB缓存一定保留。host超时、崩溃、缺少阶段标记、错误计数均不能作为负对照成功。

## 验证边界

- 使用同一场景的完全相同固件比较两份QEMU和两种调度模式。
- 两份QEMU共享大量代码，不是两套独立硬件实现；仍缺实际ARM硬件对照。
- 覆盖EL1、两个vCPU、有限地址范围；不覆盖EL0/PAC/IRQ共同作用、IOMMU/DMA、超过两核或长时间真实OS负载。
- 不测延迟、帧率、CPU吞吐或能源消耗，不能由运行较快推算native性能。
- 没有发现需要修改QEMU/Reims的缺陷；这轮产出是新覆盖和可复现实验，不是虚构修复。

## 复现与位置

    cd /workspace/macos-local
    make smp-mmu-tests
    make smp-mmu-extended

源码：experiments/E06-smp-mmu；生成代码和临时存储：build/e06-smp-mmu；逐场景UART/host错误日志和完整结果：runs/e06-smp-mmu。

正常结束和host超时路径均回收测试QEMU与自己创建的临时文件；不终止其他进程。

## 参考依据

ARM VAE1IS操作数与作用范围：
https://support.arm.com/documentation/ddi0601/2023-09/AArch64-Instructions/TLBI-VAE1IS--TLBI-VAE1ISNXS--TLB-Invalidate-by-VA--EL1--Inner-Shareable

Linux ARM64实际TLBI封装（固定提交）：
https://linux.googlesource.com/linux/kernel/git/torvalds/linux/+/aaed66fadba2d2de8fe0daa0aa3eac827d2076b9/arch/arm64/include/asm/tlbflush.h

QEMU MTTCG中每vCPU线程、跨核TLB同步与原子操作：
https://www.qemu.org/docs/master/devel/multi-thread-tcg.html
