> **Language:** [English](README.en.md) | **简体中文**

# E06-fp16-fma — 0050 FP16 乘加快路径的精确参照校验

检查 [`0050-f16-muladd-avx2-and-jmpcache-512k.patch`](../../patches/qemu-platform/0050-f16-muladd-avx2-and-jmpcache-512k.patch) 新增的两条 x86_64 F16C+FMA 快路径：标量 `f16_muladd_x86_f16c_try()`（`fpu/softfloat.c`）和向量 `arm_gvec_fmla_idx_h_x86()`（`target/arm/tcg/vec_helper.c`，AdvSIMD/SVE 按元素的 `FMLA`/`FMLS`）。只在宿主运行，不启动 QEMU、客体或网络。

- `run.py` 每次都从补丁原文里取出这两个函数，和 [`oracle.c`](oracle.c) 一起用 gcc 加 UBSan 编译；补丁改了，测试就跟着测新代码。
- `oracle.c` 用 128 位整数精确计算 `b × c + a`，按就近舍入（偶数）得到 float16，逐个比对快路径返回的结果和 inexact 标志。快路径放弃、交回 softfloat 的调用和通道只计数，不比对。
- 输入覆盖任意位模式、指数相近（抵消和恰好落在中点）、很小的加数和零操作数；向量部分覆盖 4、8、16 个元素，`FMLS` 取反和每段的索引元素。另外检查非默认舍入模式、rebias、scale、flags 下快路径一律不接手。
- 对照：同时编译一份删掉“中点交回 softfloat”判断的副本，它必须报出不一致，以证明这项检查能抓到舍入错误。快路径自身的舍入只会把恰好落在中点的值向下舍，靠这处判断才符合就近舍入（偶数）。

运行 `make fp16-fma-tests`，或 `python3 -B experiments/E06-fp16-fma/run.py [--cases N] [--seed S]`，结果写入 `runs/e06-fp16-fma/<UTC 时间>/results.json`。默认 400 万组标量输入和 100 万次向量调用，约 5 秒。需要 gcc 和支持 F16C、FMA、AVX2 的 x86_64 CPU；其他宿主上 QEMU 不会编入这两条快路径，检查直接跳过。

不覆盖：QEMU 本身的运行、softfloat 回退路径、补丁里的启动自检，以及 `0043` 的 SME `FMOPA` 改动。
