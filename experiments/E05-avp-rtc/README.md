> **Language:** [English](README.en.md) | **简体中文**

# AVP RTC 自制 guest 回归

```sh
python3 experiments/E05-avp-rtc/run.py
```

runner 使用 `build/qemu-bdif-usb/qemu-system-aarch64`、单核 TCG、512MiB
RAM 与空白测试磁盘。`--binary PATH` 可指定待验证 binary，`--build-only`
仅编译。构建直接复用 E06 的启动代码、runtime 和链接脚本，不加载 Apple
固件、发送 TSS 请求或配置网络接口。

单次 guest 检查九项合同：32/64 位 version、一致的 Unix 微秒 epoch、
以 CNTPCT/CNTFRQ 为独立参考的微秒增率、counter 高低 32 位读取、64 位
load 及持续计时、load 的高低 32 位读写、固定 control/低三位 mask/空闲
状态、只读及保留寄存器、Match 写入后不制造 alarm 状态。host 用运行前后
的 `time.time_ns()` 窗口独立核对 guest 在任何 load 写入前输出的 64 位
epoch，不硬编码日期。

结果位于 `runs/e05-avp-rtc-results.json`，UART/stderr 位于
`runs/e05-avp-rtc.log`。通过要求正常退出、九项按顺序完成、host epoch
窗口检查通过；超时始终失败。

这些检查没有注入 host power 事件、验证 IRQ 投递、制造非零 raw 位后验证
W1C，或测试 migration。Match 之后 raw/masked 保持零只证明没有产生该
alarm 状态。此次运行也没有暂停 VM，因而不把结果扩展为 pause 计时验证。
