#!/usr/bin/env python3
"""Modern Reims apple-gfx-mmio + VMApple on Linux TCG: paused QMP smoke.

No guest is booted. Probes whether VMApple machine and paravirtual device can
be realized and whether MMIO memory ranges can be queried by the QEMU monitor.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
qemu = root / ("build/qemu-reims-arm16k/qemu-system-aarch64" if os.getenv("MACOS_LOCAL_E11_VARIANT")=="16k" else "build/qemu-reims-arm/qemu-system-aarch64")
work = root / "build/e11-arm-reims"
work.mkdir(parents=True, exist_ok=True)
for name, size in [("aux-test.raw", 4*1024*1024), ("root-test.raw", 32*1024*1024)]:
    p = work / name
    if not p.exists():
        with p.open("wb") as handle:
            handle.truncate(size)
bios = root / "build/e06-vmapple/firmware-1.bin"
if not bios.is_file():
    print("First run make vmapple-cfg to create the self-authored firmware", file=sys.stderr)
    sys.exit(2)
if not qemu.is_file():
    print("ARM modern Reims binary absent; build first", file=sys.stderr)
    sys.exit(2)

args = [
    str(qemu),
    "-object", "memory-backend-memfd,id=ram,size=512M,share=on",
    "-machine", "vmapple,memory-backend=ram",
    "-m", "512M", "-smp", "1", "-cpu", "max",
    "-accel", "tcg", "-nodefaults", "-nic", "none", "-display", "none",
    "-nographic", "-monitor", "none", "-serial", "none", "-S",
    "-bios", str(bios),
    "-drive", f"file={work/'aux-test.raw'},format=raw,if=pflash,index=0",
    "-drive", f"file={work/'root-test.raw'},format=raw,if=pflash,index=1",
    "-qmp", "stdio"
]
requests = [
    {"execute": "qmp_capabilities"},
    {"execute": "query-status"},
    {"execute": "human-monitor-command", "arguments": {"command-line": "xp /4wx 0x30200000"}},
    {"execute": "human-monitor-command", "arguments": {"command-line": "xp /4wx 0x30210000"}},
    {"execute": "quit"}
]
environment = dict(os.environ, REIMS_VGPU_WINDOW="0", RUST_BACKTRACE="1")
try:
    proc = subprocess.run(
        args, input="\n".join(json.dumps(x) for x in requests)+"\n",
        text=True, capture_output=True, timeout=28, env=environment)
except subprocess.TimeoutExpired as error:
    log = root / ("runs/e11-modern-arm-reims-qmp-16k.log" if os.getenv("MACOS_LOCAL_E11_VARIANT")=="16k" else "runs/e11-modern-arm-reims-qmp.log")
    log.write_text(f"TIMEOUT\nSTDOUT:\n{error.stdout!r}\nSTDERR:\n{error.stderr!r}\n")
    print("FAIL: modern ARM VMApple + graphics realization timed out; see",log)
    raise SystemExit(9)
log = root / ("runs/e11-modern-arm-reims-qmp-16k.log" if os.getenv("MACOS_LOCAL_E11_VARIANT")=="16k" else "runs/e11-modern-arm-reims-qmp.log")
log.write_text(proc.stdout + "\n=== STDERR ===\n" + proc.stderr)
print("qemu_exit=",proc.returncode)
if proc.stderr.strip():
    print("stderr_tail=",proc.stderr[-3000:])
if proc.returncode:
    print("FAIL: runtime initialization did not cleanly quit; see",log)
    raise SystemExit(3)
parsed = []
for line in proc.stdout.splitlines():
    try:
        parsed.append(json.loads(line))
    except json.JSONDecodeError:
        pass
errors = [x.get("error") for x in parsed if "error" in x]
if errors:
    print("QMP_errors=",errors)
    raise SystemExit(4)
reads = [x.get("return") for x in parsed
         if isinstance(x.get("return"),str) and ("30200000:" in x["return"] or "30210000:" in x["return"])]
print("QMP_reply_count=",len(parsed))
print("MMIO_response_count=",len(reads))
for read in reads:
    print(read[:320])
if len(reads)<2 or not any("30200000" in r for r in reads) or not any("30210000" in r for r in reads):
    print("FAIL: QMP could not obtain both gfx/IOSurface MMIO values")
    raise SystemExit(5)
print("PASS: Modern Reims VMApple instance realized under Linux TCG with gfx/IOSurface MMIO readable")
