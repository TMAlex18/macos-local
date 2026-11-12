> **Language:** [English](M1-PAC-HVC-RESTORE-TRANSPORT.en.md) | **简体中文**

# M1：PAuth/HVC 软件参考模型与恢复传输审计

本轮日期：2026-10-14。延续总计划 v3；仍然是 M1 研发，未启动 Apple 引导固件、XNU 或 macOS GUI。

## 1. 先把两类能力拆开实测

两条 VMApple 路径运行同一份自制 ARM64 固件，确认 IA/IB/DA/DB 的签名与认证、错误密钥和错误modifier拒绝、恢复密钥、XPACI、PACGA 密钥依赖均能执行。

原来的 Apple CPU PAC HVC 0xC1000000—0xC1000006、NOP 0xC10000F0，以及抽样的未知/OEM调用均返回 -1，而不是完成其密钥管理操作。源码路径是 HVC conduit 进入 PSCI dispatcher，其未知函数分支返回 NOT_SUPPORTED；不能只看到 `pre_hvc` 的EL2分支就误判成UNDEF。

因此，缺口不是“Intel不能执行PAC指令”，而是缺少客体请求的虚拟化服务。指令层负例触发的认证异常为实际客体异常，不是测试脚本伪造。

## 2. 新增默认关闭的软件模型

开关 `x-pac-software-model=on` 明确属于实验模型；默认保持off，原有平台/图形测试不被悄悄更改。

模型实际维护并更新密钥，而不是所有HVC返回0，也不改写Apple内核中的等待/认证代码。包含初始状态、默认输入key、A/B/G更新、EL0 diversifier、EL1临时使用EL0 key bank、NOP及失败处理。非PAuth CPU拒绝，未实现迁移序列化时明确阻止迁移。

**密码学边界尤其重要：** 本实现选择每VM随机master、按角色区分的HMAC-SHA256推导128bit key，再由QEMU的PAC指令算法执行签名/认证。它不是Apple硬件key derivation的复现，不能声明与Apple PAC结果逐位相同，更不是macOS 27 ABI认证。

QEMU的默认`max`使用非密码学impdef PAC；首轮baseline仅用于行为检查。当前模型验证显式使用 `max,pauth-qarma5=on` 并检查客体ID寄存器，避免把快速哈希混同为密码学PAC。本项目也没有宣称QARMA5就是Apple的算法。

## 3. 实际验证边界

- 初始化/defaults/A/B/G rekey确实改变对应的key bank，恢复默认输入后得到原状态。
- 旧签名在换key后不通过；正常key认证成功。
- EL1设置EL0 diversifier不会直接污染普通EL1 key；显式override才切换。
- 真实 ERET 到 EL0 签名，经 SVC 进入 EL1，结果与相同diversifier的EL1 override一致；普通EL1 bank自动恢复。
- EL0不能直接调用EL1 HVC服务，实际产生同步异常并由测试确认。
- 未知HVC不改key，非法boolean被拒绝；PSCI原有调用仍可用。
- 第二vCPU初始化取得同一VM默认输入，但其rekey不改第一个vCPU的状态。
- 第二vCPU CPU_OFF/CPU_ON两轮，每次必须重新初始化，不遗留旧managed状态。
- mode-off、无PAuth CPU、only-migratable三个负向场景分别按预期拒绝。

这是一个包含多项断言的两vCPU裸机实验，不是完整OS或硬件认证。此次EL0测试在MMU关闭下运行；不能据此声称EL0地址空间/多核TLB和PAC已经联合验证。

## 4. 生命周期与尚未实现的语义

模型支持当前测试中的per-CPU reset，但没有状态迁移、持久PAC identity，也未验证完整系统warm-reset、guest直接MSR key后的managed服务组合或所有EL/中断嵌套。OEM时钟、boot-session UUID、WFK/KICK服务仍未实现。

初始化前返回-3与非法boolean返回-2是本模型约定。历史文档没有给出这些所有错误路径，不能写成Apple既定行为。

服务编号来自Apple公开XNU头文件，实际取到的主分支提交为f6217f891ac0bb64f3d375211650a4c1ff8ca1ea；这不是27.0.1闭源kernel实测。旧版vmapple_pac.md细节来自镜像，官方检查路径404，已保留此来源差异。

## 5. E05：Linux恢复不是单纯缺少下载链接

直接审计本机固定QEMU `hw/vmapple/bdif.c`：DEVID_USB已定义，但命令分派仅处理ROOT与AUX，没有USB命令分支。这意味着当前复原路径还缺虚拟USB/DFU传输，而不只缺一个IPSW文件。

已取得第三方研究仓库的具体提交657bb2423ec8d572b22a97dd33563dbba1680e6a及其USB chardev补丁；补丁在当前源码上 `git apply --check` 通过，**没有实际应用，也没有本机运行验证**。该仓库作者报告过Asahi上的DFU传输，但journal没有证明已完成其Linux原生恢复。

下一步的三条材料路径继续分开：

1. Apple参考环境取得匹配AVPBooter与已初始化VM，仅作为一种可选来源，不假定用户有Mac。
2. 研究从官方系统制品内部读取Virtualization.framework资源；工具文档提供DMG/AEA检查方向，但本机尚未定位或提取匹配AVPBooter，不能把顶层ZIP无同名文件误解成所有内层文件系统均不存在。
3. Linux原生恢复需 AVPBooter + BDIF USB/USBIP + 正确恢复状态机 + 匹配签名材料 + 实际AUX/身份/系统盘。必须验证guest真实响应，不能把猜测的DFU状态补齐当作签名恢复已成功。

本组PAC/HVC与传输审计没有下载完整IPSW或执行Apple固件，也没有改动宿主USB模块或绕过恢复签名。收尾发现工作区另一组未提交脚本已提取五个小组件到private，并生成设备树与clock报告；已只读核对文件与既有hash，不将其覆盖、删除或收入本轮提交。仍未获得AVPBooter或安装完成的VM。

## 6. 可重复执行入口

    make pauth-baseline
    make pauth-model
    make pauth-guards
    make platform-contracts
    make reims-fifo-roundtrip
    make check-research-patches

当前QEMU构建使用累计patch 0003，包含原架构patch 0002；二者不是顺序堆叠关系。独立负对照/平台fork仍保留。源代码与测试脚本在experiments/E06-pauth-hvc；状态以PLAN-STATE.json及本轮结果JSON为准。


## 7. 收尾观察到的其他未提交工作

四个文件为E05的extract-small-boot-components.py、audit-device-tree.py，以及E06-platform-contracts的clock.c、run-clock.py。它们不是本组新增脚本；保留未提交状态。private中的DeviceTree、LLB、iBoot、iBSS、iBEC大小/hash与现有报告一致；未执行这些Apple组件，未独立验证Apple签名。设备树报告记有24MHz timebase；clock报告记有24MHz profile速率偏差，但本组未重新运行或修复该实验，不能冒充本轮时钟验证结果。
