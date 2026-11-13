> **Language:** [English](README.en.md) | **简体中文**

# E06-sync-counters — TCG 全局锁与独占段触发源计数器验证

使用自写双核 AArch64 裸机固件（不含任何 Apple 代码或外部网络）验证补丁 `0033-tcg-sync-source-counters.patch` 的默认关闭行为、精确逐项计数与 `system_reset` 清零语义：

- 默认关闭（省略 `x-sync-stats=on`）时，`x-sync-stats-json` 返回 `{"enabled":false}`；
- 开启 `-cpu max,x-sync-stats=on` 时，双 vCPU 下固定次数的 `CNTVCT_EL0` 读取（37 次）、`CNTP_CVAL_EL0` 写入（13 次）、`SVC`+`ERET`（19 次陷入 / 38 次 `ERET` BQL 钩子）、`TLBI VAE1IS`（11 次 `excl_tlbi_page`）、`TLBI RVAE1IS`（3 次 `excl_tlbi_range`）、`TLBI VMALLE1IS`（5 次 `excl_tlbi_all`）、UART MMIO 写入（5 次 `bql_mmio`），以及对端 vCPU 收到的异步队列刷新均与理论计数逐项吻合；
- `system_reset` 后全部标量计数与系统寄存器/TLBI 计数清零。
