> **Language:** [English](README.en.md) | **简体中文**

# 配置请求 slot 的自制 guest 回归

`guest.c` 是单次执行的自制 AArch64 EL1 guest，通过真实 TCG MMIO 向
`0x4ffff8` 写入 24B 请求的物理地址。构建直接使用
`../E06-platform-contracts/` 的启动代码、runtime 与链接脚本。

```sh
python3 experiments/E05-config-mailbox/run.py
```

仅编译可使用 `--build-only`；`--binary PATH` 可选择待验证的 QEMU。
默认运行 `build/qemu-bdif-usb/qemu-system-aarch64`，使用 512MiB RAM、单核
TCG 和本目录对应的空白测试磁盘，不加载 Apple 固件或请求 TSS。

guest 在一次执行中检查 14 个场景：operation 2 的 type 1/2/3 正常
`ENOTSUP=45` 完成，其中 type 1 使用原 Stage2 请求的 5MiB capacity 并
逐字节核对整个 buffer 未变；operation 1/type 1 缺 identity 的 `ENOENT=2` 完成，operation 1/3错误type的原生 `EINVAL=22`；
错误 version、type 范围两端、不可映射参数、参数跨越 RAM 尾部、长度溢出、
不可映射 descriptor、descriptor 跨越 RAM 尾部，以及原 CFG version、CPU
数量和 RAM 大小字段。每次提交还检查 slot 读取恒为零。原参数内容和两侧
sentinel 必须不变；被拒绝的 descriptor 必须完整保持；不完整 descriptor
只检查实际存在于 guest RAM 的字节。

结果保存在 `runs/e05-config-mailbox-results.json`，完整 UART/stderr 保存在
`runs/e05-config-mailbox.log`。通过要求 guest 正常退出、全部场景按顺序完成，
且最终 PASS 标记存在；host 超时始终视为失败。这验证正常错误语义及 DMA
边界，不代表 operation 3 或任何 Apple 启动路径已实现。
