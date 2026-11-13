> **Language:** [English](README.en.md) | **简体中文**

# E06 platform contracts: virtual timer, GICv3 and EL1 stage-1 MMU

This extends E06 rather than introducing another project milestone. The
payloads are self-authored C and AArch64 assembly, built with host Clang/LLD.
They run at guest EL1 with a real exception vector table, a guest stack and
saved general-register/ELR/SPSR frames; no Apple firmware or OS is involved.

## Reproduce

    python3 experiments/E06-platform-contracts/run.py --suite all --target all

Narrow selections: --suite timer|mmu|combined, --target headless|reims.
The runner builds each payload once and uses that same binary on both the
portable GPU-less VMApple and modern Reims/VMApple ARM16K integration.

## Tested behaviors

- CNTV virtual counter/timer, connected by the pinned VMApple board to GICv3
  PPI 27. Configure Redistributor Group1/priority/enable and CPU interface.
- Expired timer while DAIF.I is masked remains pending without executing ISR;
  unmasking enters IRQ, observes INTID 27, clears source, EOIs and ERETs.
- Rearm timer; WFI wakes on pending IRQ while DAIF.I masked, then interrupt
  is delivered after unmask; no further IRQ after disabling the source.
- Negative case disables PPI routing: timer pending exists, but ISR does not
  run. Guest exits 1; the harness requires this exact expected refusal.
- Guest stage-1 16KiB translation, TCR_EL1.TG0=16K with 32-bit input VA space,
  L2 block identity maps and L3 pages mapping a high alias to a RAM frame.
- RW alias, RO read, denied RO store (EC=0x25/DFSC=0xf/WnR=1), missing page
  (DFSC=7), missing access flag (DFSC=0xb), exact FAR and faulting PC.
- Handler skips only the known faulting LDR/STR and returns via ERET; denied
  store does not alter its physical backing. AF correction is observable.
- Break-before-make + DSB/TLBI/DSB/ISB switches a used VA from frame A to B.
- Negative RO case deliberately grants write permission: missing expected
  abort is detected, rather than silently counted as a passing fault test.
- Combined case performs the timer/GIC/exception test with MMU/caches enabled.

## Observed result, 2026-10-14

10/10 **scenario expectations** met: 6 positive guest exits (0) and 4 negative
controls (guest exit 1). 5 distinct payloads each run on both targets. This
is NOT ten independent platform implementations. The two QEMU forks share
substantial underlying code. Exact raw results and firmware digests are in
runs/e06-contracts-all-all-results.json; all individual UART logs are retained.

## Limits

Only one active vCPU per new scenario. Earlier PSCI two-vCPU test remains a
separate check. No 48-bit address-space/ASID/multicore TLB validation, no EL0
privilege transition, no nested IRQ/FIQ/LPI test, no Apple proprietary HVC or
PAuth key lifecycle, no real macOS. Guest timer frequency is not host CPU
frequency and the runner's wall times are NOT performance benchmarks.

The QEMU/Reims implementations were **not modified** in this step; this adds
missing executable contract tests for capabilities that already exist.

## Primary references (plus fixed local source)

- Arm GIC configuration: https://support.arm.com/documentation/198123/0302/Configuring-the-Arm-GIC
- Arm translation granules: https://support.arm.com/documentation/101811/0105/Translation-granule
- Arm ESR_EL1: https://support.arm.com/documentation/111107/2026-06/AArch64-Registers/ESR-EL1--Exception-Syndrome-Register--EL1-
- Fixed QEMU VMApple map/timer wiring: sources/qemu-reims-arm/hw/vmapple/vmapple.c
- Fixed GIC implementation: sources/qemu-reims-arm/hw/intc/gicv3_internal.h
  and arm_gicv3_common.c. No claim that source agreement is independent proof.

## Counter frequency differential test

run-clock.py compiles clock.c and compares three guest CNTFRQ modes
against host monotonic elapsed time. The official macOS 27 VirtualMac2,1
DeviceTree declares 24MHz on 32 CPUs, while QEMU max defaults to 1GHz.
This QEMU TCG build's integer nanosecond period leads to ~+1.61% guest
clock rate at the requested 24MHz (3 repeats). No macOS code is run.
See docs/reports/M1-RESTORE-FIRMWARE-ADT-CLOCK.md.

## 普通自给AES128 ECB密钥与DMA/IRQ专用探针

```sh
python3 experiments/E06-platform-contracts/run-aes.py
```

`aes.c`复用本目录`start.S`/`runtime.c`的EL1向量和GIC约定；原有`run.py`
入口保持不变。专用runner使用当前`runs/qemu-bdif-usb-build.json`固定的binary，
先验证其实际hash，再运行两个有界自制固件，不加载Apple代码、不改设备或库。
4GiB RAM范围为`0x70000000..0x170000000`；配置2CPU但仅CPU0执行，沿用
24MHz exact与现有研究平台配置，保护模型语义不在本测试范围内。

唯一密钥为[NIST SP800-38A原始材料](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-38a.pdf)
F.1.1 ECB-AES128.Encrypt block1（印刷第24页/PDF第31页）的公开测试值：
key=`2b7e151628aed2a6abf7158809cf4f3c`，plaintext=
`6bc1bee22e409f96e93d7e117393172a`，ciphertext=
`3ad77bb40d7a3660a89ecaf32466ef97`。guest只提交KEY selector0/context0、
AES128、encrypt、ECB，以及普通DATA/FLAG；不访问第二AES MMIO区或其他
key selector，不涉及硬件秘密、派生、storage wrapping或Apple启动保护。

正例要求：第一次DATA使用`0x72000100→0x72004100`，第二次
使用合法高RAM `0x110000100→0x110004100`；后者的FIFO address word为
`0x00010001`，保留source/destination高位。每项只加密16字节，guest独立
逐字节检查整个16KiB输入页不变、整个16KiB输出页的密文与guard。正常成功
必须实际读取IAR50、FLAG IRQ status bit5及info1→2；ISR先W1C清电平源，
再EOIR，第二项必须再次通知。屏蔽GIC SPI50的负例要求密文、FLAG及GIC
pending已就绪但ISR计数为0，guest退出1；guest counter等待上界2秒，host
15秒timeout始终失败。设备同步处理FIFO，reset STATUS/BUSY不能替代这些证据。
runner另核对实际FIFO trace与完整预期命令序列；所有测试key均由guest提供，
host不会写入预期密文。

2026-10-23首次实际执行曾停在**普通构建依赖缺口**：
唯一原始结果
与UART/stderr trace
显示KEY完整消费成功，随后合法低RAM DATA的完整请求返回`success=0`，因此
FIFO仍停在DATA，FLAG未消费，guest以`AES_FLAG_NOT_READY`退出1。

原binary `0273d0cccbb82554f6753cf54d70c5703e5627e52cc4fdbac8d64c4f49b08973`
未启用`CONFIG_GCRYPT`、`CONFIG_NETTLE`、`CONFIG_GNUTLS_CRYPTO`或
`CONFIG_AF_ALG`；cipher.c因此选择
cipher-stub.c.inc，普通
`qcrypto_cipher_ctx_new`返回无cipher library错误。这一原始失败作为缺失构建
前置条件的证据保留。

随后安装`nettle-dev 3.10.2`，两个研究构建均显式启用正常Nettle cipher backend，
不改AES设备实现或key selector。当前bdif SHA为
`b0123d45ebc127f6e1b46727c937174100ddd45b0d9adc17e2ce0e7c52ce91da`；
runs/qemu-bdif-usb-build.json记录binary及
backend/version/config摘要。

唯一规范成功结果
已实际通过上述低/高地址两项DMA：两次都得到精确NIST密文，16KiB输入/输出
整页检查通过，实际IAR50/status32/info1→2、IRQ count1→2及最终status0
确认正常ack/rearm。正例退出0；屏蔽SPI负例原始日志
显示密文/guard及FLAG/GIC pending已就绪但ISR为0，按预期退出1；两例均无
host timeout。QEMU log trace backend写入stderr，与UART保存在各case唯一
raw log中，FIFO命令及高地址word另由runner核对。

这证明当前binary的普通自给AES128 ECB数据/DMA/IRQ合同；没有测试其他key
路径、第二MMIO区、硬件秘密或Apple代码，也不能据此归因Apple
`IOConfigThread_'aes'`自旋或根启动卡点。

## VirtIO net 控制队列容量（0032）

本地研究补丁 `0032-virtio-net-control-queue-size.patch` 增加标准设备属性
`ctrl_queue_size`（uint16）。默认64不变；显式值必须为1至
`VIRTQUEUE_MAX_SIZE`（当前1024）的2次幂。初始realize和MQ队列重建均使用
同一字段，不改变RX/TX容量、feature位、控制ACK或guest地址。

```sh
python3 -B experiments/E06-platform-contracts/test-net-control.py --output runs/UNUSED-NET-CONTROL-DIRECTORY
```

该测试复用现有PCM qtest的QMP/PCI内存/descriptor传输工具，但不创建音频
设备、不执行Apple代码、不使用host TAP或物理网络。默认、显式64和512
分别核对QOM容量、PCI公告和实际selected size；RX/TX保持256。512例提交515
个控制请求，越过avail/used环尾并使用高head；直接和间接SG请求检查只读输入、
ACK前后及used-ring canary，ACK先置255再要求RX_ALLMULTI返回0，非法class
返回1，实际used.len均为1。socket对端另外逐字节验证60B Ethernet帧的收发，
三种容量的host features相同。0、3、2048、65535和uint16溢出65536均拒绝。

MQ覆盖使用遵循现有上游vhost-user-test协议的有界本地握手对端，仅公告两个
queue pairs；保持link-down，不启动offloaded数据面。实际feature协商
MQ on→off→on使队列数3→5→3→5，每一代最后的控制队列均为512并实际完成
RX_ALLMULTI/非法class ACK，数据队列仍为256。它验证QEMU队列重建和控制
消费者，不宣称vhost-user收发功能。

主binary `4a433616dc04e90ba41d720fe0dfb8925294799eff94a6eef24b52108a6b4d66`
已通过上述全部检查，所有QEMU和握手线程均退出并回收；42个受改源文件从
固定patch链重建一致。结果位于
`runs/e06-net-control-20261108/final/results.json`。
Run126确有控制ring地址重叠和guest接口inactive，但尚未翻译guest内部usedVA；
本测试不把该原因或512对macOS的修复效果视为已证明，实际OS验证另行进行。
