> **Language:** **English** | [简体中文](README.md)

# AVP RTC Self-Authored Guest Regression

```sh
python3 experiments/E05-avp-rtc/run.py
```

The runner uses `build/qemu-bdif-usb/qemu-system-aarch64`, single-core TCG,
512MiB RAM, and blank test disks. `--binary PATH` selects the binary under
test, and `--build-only` compiles without running. The build directly reuses
the E06 startup code, runtime, and linker script without loading Apple
firmware, sending TSS requests, or configuring network interfaces.

A single guest execution checks nine contracts: 32/64-bit version reads, a
consistent Unix microsecond epoch, microsecond increment rate cross-checked
against CNTPCT/CNTFRQ as an independent reference, upper/lower 32-bit counter
reads, 64-bit load and continued ticking, upper/lower 32-bit load reads and
writes, fixed control / low three-bit mask / idle status, read-only and
reserved registers, and writing Match without fabricating an alarm state. Using
a pre-/post-run `time.time_ns()` window, the host independently verifies the
64-bit epoch emitted by the guest before any load write, without hardcoding
dates.

Structured results are written to `runs/e05-avp-rtc-results.json`, and
UART/stderr output is written to `runs/e05-avp-rtc.log`. Passing requires clean
guest exit, all nine checks completed in order, and a passing host epoch window
check; a timeout always fails.

These checks do not inject host power events, verify IRQ delivery, synthesize
non-zero raw bits to verify W1C clearing, or test migration. Observing
raw/masked remain zero after writing Match proves only that no alarm state was
asserted. Nor does this run pause the VM, so its results are not generalized to
pause-timing verification.
