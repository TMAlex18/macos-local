> **Language:** [English](M1-EL0-CONTEXT.en.md) | **简体中文**

# M1：EL0 / MMU / PAuth / IRQ 上下文联合验证

**日期：2026-10-15（UTC）｜起点：d5f037d。** 保留前序整理的 docs/RESULTS 汇总、reports 目录，以及按 Variant 选择恢复身份、显式前置校验、复用 iBEC 参考制品的修改。

## 执行边界

受影响的恢复前置检查在本机重跑：iBEC 33、签名7、EP0适配15，合计55项通过。随后通过已有run-ibec入口尝试正常签名恢复，调用被工具安全检查拦截，没有进程/job或新会话日志；只读检查也未发现新QEMU。没有换执行器、改写被拒绝操作或请求用户手动绕过。

实际继续执行的是独立M1自制载荷，没有Apple固件、TSS、USB挂接、系统磁盘修改或新增依赖。新代码完全位于 `experiments/E06-el0-context/`，复用已有E06启动向量/runtime，未修改QEMU或Reims。

## 不是把几个单项测试拼在一起

一个vCPU实际轮流运行两个EL0任务。两任务使用相同虚拟地址，但通过ASID 0x31/0x32和不同页表映射到各自的物理数据页/栈，指针认证密钥也不同。定时器IRQ从EL0进入EL1；处理程序保存通用寄存器、ELR/SPSR和SP_EL0，切换TTBR0/ASID及密钥状态，再ERET恢复另一个任务。任务切换时不靠清空全部TLB掩盖ASID隔离问题。

用户代码先用PACIA签名一个实际函数指针，然后反复通过BLRAA进入函数；函数用PACIASP/AUTIASP保护真实栈上的返回地址。实验要求中断真实落在已保存签名返回地址、尚未恢复的函数体内，再验证恢复后的函数调用和返回继续正常，私有页中的owner与工作计数不串扰。

同时，每个EL0任务依次尝试：读取EL1-only映射、写只读代码页、跳转到UXN数据页。数据页中确实放有RET指令，避免把“数据无法解码”误当作执行权限保护。异常处理检查来源EL、预期指令/FAR及权限fault状态，只跳过自己事先设计的探针。

## 三种配置

| 配置 | 内存/调度 | PAuth密钥来源 |
|---|---|---|
| headless | 既有无GPU VMApple、1vCPU、2任务 | 自制EL1程序写架构key寄存器 |
| reims | 既有Reims VMApple、1vCPU、2任务 | 同上，设备初始化与上下文测试共同存在 |
| reims-model | 同上并显式开启local PAC model | 既有HVC参考模型的EL0 diversifier；在IRQ/SVC/权限异常检查EL1银行已恢复 |

全部显式选择 `max,pauth-qarma5=on`，不用QEMU默认非密码学impdef算法。**QARMA5是架构算法；第三种模式的HMAC密钥派生仍是本项目的参考模型，不是Apple专有cipher/KDF的等价实现。**

## 结果与反例

三配置各运行正常、错误密钥恢复、取消UXN、禁用PPI四种场景，共12项；另对三配置各运行64次IRQ/任务的延长正例，总计15项：**6个正向＋9个预期负例，全部符合预期。**

| 条件 | 预期行为 | 实际 |
|---|---|---|
| 正常切换 | 两任务各12或64次IRQ，恢复后继续签名调用/返回 | 6个正向退出0；计数、私有内存和不同签名检查通过 |
| 恢复错误key/diversifier | 不能接受旧签名 | 三配置均发生实际认证异常，ESR_EL1=0x72000000，退出1 |
| 把数据页变为可执行 | 原先UXN探针不再fault，测试必须发现 | 三配置均报缺少预期权限异常，退出1 |
| 不启用GIC PPI27 | 无EL0定时器抢占，不能悄悄算成功 | 三配置均由guest有界等待报错、退出1，不是host超时 |

只统计6个最终正向运行：456次EL0定时器中断、462次任务上下文切换、36次预期权限异常、24次SVC；其中449次IRQ观测PC位于带签名栈返回地址的函数体内。上下文切换数包含任务结束SVC导致的调度，不把462次都说成timer抢占。

总量对应本报告两次实际运行，不能与前期开发/旧SMP结果相加。每个profile都共享相当部分QEMU代码，并非三个独立硬件验证。

### 开发中的两个测试代码问题

第一次构建的结构体赋值被编译为缺失libc的memcpy；改为与既有向量布局一致的显式字段复制。model-only构建随后因未使用直接key helper触发-Werror；改为仅在对应架构配置编译。它们是本次新测试代码的问题，不是QEMU平台缺陷，均未计入最终15项通过结果。

## 当前结论与局限

此前分别通过的EL0/PAC与EL1/MMU/IRQ，在这里共同经历了有真实栈与地址空间的用户态抢占/恢复。没有修改既有模拟器实现。验证的是单vCPU上的两个整数任务，而非完整内核或多核EL0调度。

FP/SIMD/SVE、TLS、嵌套IRQ、更多vCPU、DMA/cache一致性、24MHz与这些契约的联合、真实Apple KDF/HVC及macOS都仍未验证。循环次数和启动墙钟不作为性能成绩。M1继续进行，M2没有新阶段成果。

## 复现与记录

    cd /workspace/macos-local
    make el0-context-tests
    make el0-context-extended

结果：`M1-EL0-CONTEXT-RESULTS.json`，其中列出两次最终运行日志、源文件SHA-256、每个场景的预期/实际退出码与计数。原始日志留在runs，构建输出在build，各临时磁盘使用TemporaryDirectory并已清理。

继续沿用既定的文档分工：当前状态更新PLAN-STATE并生成STATUS，成果在RESULTS加一行，报告在reports；不再向MASTER-PLAN追加实验流水账。

参考：QEMU CPU features文档及固定QEMU提交内docs/system/arm/cpu-features.rst；架构PAuth背景为Linux官方arm64/pointer-authentication文档。具体运行证据仍是本机自制载荷输出。
