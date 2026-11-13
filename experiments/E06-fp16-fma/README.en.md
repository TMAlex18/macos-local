> **Language:** **English** | [简体中文](README.md)

# E06-fp16-fma — Exact-Reference Check of the 0050 FP16 Multiply-Add Fast Paths

Checks the two x86_64 F16C+FMA fast paths that [`0050-f16-muladd-avx2-and-jmpcache-512k.patch`](../../patches/qemu-platform/0050-f16-muladd-avx2-and-jmpcache-512k.patch) adds: the scalar `f16_muladd_x86_f16c_try()` (`fpu/softfloat.c`) and the vector `arm_gvec_fmla_idx_h_x86()` (`target/arm/tcg/vec_helper.c`, AdvSIMD/SVE by-element `FMLA`/`FMLS`). Host only: no QEMU, guest or network.

- `run.py` copies both functions out of the patch text on every run and builds them with [`oracle.c`](oracle.c) using gcc and UBSan, so a change to the patch is tested as written.
- `oracle.c` computes `b × c + a` exactly with 128-bit integers, rounds it to float16 with round-to-nearest-even, and compares every result and inexact flag that a fast path returns. Calls and lanes that the fast paths hand back to softfloat are counted, not compared.
- Inputs cover arbitrary bit patterns, close exponents (cancellation and exact midpoints), small addends and zero operands; the vector part covers 4, 8 and 16 elements, `FMLS` negation and the indexed element of each segment. It also checks that the fast paths never take over with a non-default rounding mode, rebias, scale or flags.
- Control: a copy with the midpoint hand-back removed is built as well and must report mismatches, which shows the check catches a rounding error. The fast paths' own rounding sends an exact midpoint down; that hand-back is what makes them round-to-nearest-even.

Run `make fp16-fma-tests` or `python3 -B experiments/E06-fp16-fma/run.py [--cases N] [--seed S]`; results go to `runs/e06-fp16-fma/<UTC time>/results.json`. The default is 4 million scalar triples and 1 million vector calls, about 5 seconds. It needs gcc and an x86_64 CPU with F16C, FMA and AVX2; on other hosts QEMU does not compile these fast paths in and the check skips.

Not covered: running QEMU itself, the softfloat fallback, the patch's start-up self-test, and the SME `FMOPA` change in `0043`.
