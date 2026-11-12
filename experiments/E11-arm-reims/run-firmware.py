#!/usr/bin/env python3
"""Execute self-authored ARM64 E06 firmware with modern Reims/MMIO present.

Confirms coexistence of a modern vGPU host device and VMApple CPU/platform,
but does not send Apple graphic commands or use Apple guest binaries.
"""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
bin = root / ("build/qemu-reims-arm16k/qemu-system-aarch64" if os.getenv("MACOS_LOCAL_E11_VARIANT")=="16k" else "build/qemu-reims-arm/qemu-system-aarch64")
work = root / "build/e11-arm-reims"
work.mkdir(parents=True, exist_ok=True)
for name, size in [("aux-test.raw",4*1024*1024),
                   ("root-test.raw",32*1024*1024)]:
    path = work/name
    if not path.is_file():
        with path.open("wb") as f: f.truncate(size)
suffix = "-16k" if os.getenv("MACOS_LOCAL_E11_VARIANT") == "16k" else ""
tests = [
    ("config", 1, "firmware-1.bin", "VMAPPLE TCG CFG UART PASS"),
    ("psci", 2, "psci.bin", "VMAPPLE PSCI CPU_ON RAM SIGNAL PASS"),
]
for name, cores, fw, expect in tests:
    image = root / "build/e06-vmapple" / fw
    if not image.is_file():
        raise SystemExit(f"Missing E06 self-written firmware: {image}")
    args = [
        str(bin),
        "-object", "memory-backend-memfd,id=ram,size=512M,share=on",
        "-machine", "vmapple,memory-backend=ram",
        "-m", "512M", "-smp", str(cores), "-cpu", "max",
        "-accel", "tcg", "-nodefaults", "-nic", "none",
        "-display", "none", "-nographic", "-monitor", "none",
        "-serial", "stdio", "-semihosting-config", "enable=on,target=native",
        "-bios", str(image),
        "-drive", f"file={work/'aux-test.raw'},format=raw,if=pflash,index=0",
        "-drive", f"file={work/'root-test.raw'},format=raw,if=pflash,index=1",
    ]
    env = dict(os.environ, REIMS_VGPU_WINDOW="0")
    try:
        proc = subprocess.run(args, text=True, capture_output=True,
                              timeout=24, env=env)
    except subprocess.TimeoutExpired as error:
        log = root / f"runs/e11-{name}-firmware{suffix}.log"
        log.write_text(f"TIMEOUT\nstdout={error.stdout!r}\nstderr={error.stderr!r}")
        raise SystemExit(f"{name}: QEMU timed out; see {log}")
    log = root / f"runs/e11-{name}-firmware{suffix}.log"
    log.write_text(proc.stdout + "\n=== STDERR ===\n" + proc.stderr)
    print(f"{name} cores={cores} rc={proc.returncode}")
    print(f"UART_expected={expect!r} present={expect in proc.stdout}")
    if proc.returncode != 0 or expect not in proc.stdout:
        print("stderr_tail:", proc.stderr[-1800:])
        raise SystemExit(f"{name} FAIL: inspect {log}")
print("PASS: ARM firmware and PSCI CPU_ON succeeded while modern Reims/MMIO was realized")
