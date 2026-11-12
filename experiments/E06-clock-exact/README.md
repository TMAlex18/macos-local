# E06: exact generic-counter and timer-deadline conversion

## Scope

Research branch: `sources/qemu-clock-exact`, pinned upstream plus cumulative
VMApple integration patch 0003 followed by the separate clock patch 0005.
It does NOT apply the optional RTC/CTRR placeholder patch 0004.
The normal mainline and no-GPU baseline binaries are not overwritten.

Binary: `build/qemu-clock-exact/qemu-system-aarch64`
Enable with CPU options: `max,cntfrq=24000000,x-cntfrq-exact=on`.
The switch is OFF by default. It is a fixed launch-time property, not a
live frequency-changing interface. Only the configured TCG system target,
frequencies up to 1 GHz and non-negative virtual time have been tested.
Migration, KVM, multiple-clock CPU combinations and Apple boot are unproven.

## Mathematics and covered paths

Counter: floor(virtual_ns * Hz / 1e9).
Deadline: ceil(counter_tick * 1e9 / Hz), saturating to signed INT64_MAX.
Integer quotient/remainder prevents overflowing a 64-bit intermediate.

Both directions are changed together. Generic timers use nanosecond scale
in exact mode; WFIT and WFET deadlines are converted to the same ns domain.
Event-stream calculation produces the next absolute physical-counter edge,
not a relative interval accidentally treated as an absolute timestamp.
The legacy/off branch retains the existing semantics.

## Tests and interpretation

- `test-arithmetic.c`: includes the same production header; independently
  checks against 128-bit math and enumerates event bit transitions, with
  host UBSan/ASan. This is not instrumentation of the whole QEMU binary.
- `run-functional.py`: 12 guest/startup scenarios: 8 positives, 3 expected
  guest failures, 1 invalid-frequency startup rejection. MMU+timer, rearm,
  WFIT/WFET, rising/falling event stream, 96 short-deadline interrupts and
  distant saturation are covered. All use self-authored firmware.
- `run-rate.py`: same QEMU off/on comparisons and frequency controls,
  six profiles, three 750-ms samples each. Wall time comes from host UART
  observation. It is sufficient to distinguish the old ~1.6% rate bias,
  not to establish ppm precision, real CPU cycle accuracy or macOS speed.

## Reproduce

    make build-qemu-clock-exact
    make clock-math
    make clock-functional
    make clock-rate-exact

Or `make clock-check` for this complete scoped suite.
Existing baseline `make clock-rate-baseline` remains separate.

Reports: `runs/e06-clock-exact-functional-all.json`,
`runs/e06-clock-exact-rate-results.json`, and
`runs/e06-clock-exact-arithmetic.log`.
Current documented summary: `docs/reports/M1-EXACT-CLOCK-RESULTS.md`.
