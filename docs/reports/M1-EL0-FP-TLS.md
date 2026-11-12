> **Language:** [English](M1-EL0-FP-TLS.en.md) | **简体中文**

# M1 — EL0 FP/SIMD、FPCR/FPSR、TLS 与 MMU/PAuth/定时器抢占联合实验

2026-10-16；本实验在 **独立目录** experiments/E06-el0-fp-tls/ 中复制既有
E06-el0-context 作为固定对照，原始历史测试保持不变。两个 EL0 任务运行在同一 ARM64 TCG vCPU。
无 Apple 固件、TSS、客体操作系统、物理USB或用户系统盘参与。

## 实际测试机制

两任务通过独立 ASID/TTBR0 和物理数据/栈页共享相同虚拟地址。实际由
GIC PPI27 定时器中断进行 EL0→EL1→EL0 轮换；期间原样保留已有 PACIA/BLRAA/PACIASP/AUTIASP、
权限 fault 和 SVC 验证。在此基础上，添加：

- 用户态 SIMD Q8/Q9，每任务各持有不同 128-bit 值；每轮 EL0 循环先
  把当前 Q 寄存器存回自己的数据页，再与各自预期字节比较。
- 用户态 TPIDR_EL0 使用不同 TLS cookie，每轮以 MRS 实际读取、比较。
- FPCR 选择不同合法舍入控制值；FPSR 置不同累计异常状态位；
  EL0 实际读取验证两者均被分别保存、恢复。
- EL1 显式状态保存/恢复函数在独立 simd_regs.S 中，以真正的 stp/ldp q8/q9、
  mrs/msr fpcr/fpsr 实现；所有 C 中断处理用 -mgeneral-regs-only 编译，
  避免编译器未经记录使用 SIMD。禁用恢复的反例不会得到全绿。
- 每轮 ASID 切换不执行全量 TLBI，避免以清空 TLB 掩盖隔离问题。

## 结果与控制

| 配置 | 12 轮基础：正常＋7个预期负例 | 64 轮延长正例 |
|---|---:|---:|
| 无GPU VMApple | 8/8 | 1/1 |
| Reims ARM VMApple | 8/8 | 1/1 |
| Reims + 实验 PAC HVC 模型 | 8/8 | 1/1 |
| 独立精确计时实验构建、24 MHz | 8/8 | 1/1 |

合计基础 **32/32**（4 正向＋28 客体预期失败），延长 **4/4**。
延长正例合计 512 次真实 EL0 定时器中断，516 次任务切换。反例包括：
故意省略 SIMD、FPCR、FPSR 或 TPIDR_EL0 恢复，必须在客体内识别
状态串扰；另外沿用错误 PAC 密钥、取消数据 UXN 与禁用 PPI27 的负例。

这是**选定寄存器**的行为验证，不代表 Q0..Q31/SVE/SME 的完整浮点上下文。
仍未覆盖多 vCPU EL0 调度、嵌套IRQ、FP lazy trapping、长期稳定性，
也不代表 Apple PAC KDF 或 XNU 的完整硬件语义。

第四种配置显式选择 `cntfrq=24000000,x-cntfrq-exact=on`；客体 MRS CNTFRQ 实际读到 24,000,000 Hz，三个其他配置读到 1 GHz；所有配置均运行相同的用户态FP/TLS抢占验证。**这不是完整多核精确时钟或GUI性能验证。**

原始逐案例 UART / exit code 见 runs/e06-el0-fp-tls/，脱敏摘要见
M1-EL0-FP-TLS-RESULTS.json。

复现：

    make el0-fp-tls-tests
    make el0-fp-tls-extended
