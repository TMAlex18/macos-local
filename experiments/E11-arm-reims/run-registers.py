#!/usr/bin/env python3
"""Real guest CPU reads apple-gfx-mmio and IOSurface MMIO without Apple code."""
from pathlib import Path
import os
import subprocess
import sys

root=Path(__file__).resolve().parents[2]
t=root/"experiments/E11-arm-reims"
build=root/"build/e11-arm-reims"
build.mkdir(parents=True,exist_ok=True)
obj=build/"probe-registers.o"
elf=build/"probe-registers.elf"
binfile=build/"probe-registers.bin"
for args in [
    ["clang","--target=aarch64-none-elf","-c",str(t/"probe-registers.S"),"-o",str(obj)],
    ["ld.lld","-T",str(root/"experiments/E06-vmapple/firmware.ld"),str(obj),"-o",str(elf)],
    ["llvm-objcopy","-O","binary",str(elf),str(binfile)]
]:
    subprocess.run(args,check=True)
for name,size in [("aux-test.raw",4*1024*1024),("root-test.raw",32*1024*1024)]:
    p=build/name
    if not p.exists():
        with p.open("wb") as f:f.truncate(size)
args=[
    str(root/("build/qemu-reims-arm16k/qemu-system-aarch64" if os.getenv("MACOS_LOCAL_E11_VARIANT")=="16k" else "build/qemu-reims-arm/qemu-system-aarch64")),
    "-object","memory-backend-memfd,id=ram,size=512M,share=on",
    "-machine","vmapple,memory-backend=ram","-m","512M","-smp","1","-cpu","max",
    "-accel","tcg","-nodefaults","-nic","none","-display","none",
    "-nographic","-monitor","none","-serial","stdio",
    "-semihosting-config","enable=on,target=native",
    "-bios",str(binfile),
    "-drive",f"file={build/'aux-test.raw'},format=raw,if=pflash,index=0",
    "-drive",f"file={build/'root-test.raw'},format=raw,if=pflash,index=1"
]
env=dict(os.environ,REIMS_VGPU_WINDOW="0")
try:
    r=subprocess.run(args,text=True,capture_output=True,timeout=24,env=env)
except subprocess.TimeoutExpired as ex:
    print("TIMEOUT:",ex)
    raise SystemExit(9)
log=root/("runs/e11-arm-gfx-mmio-rw-guest-16k.log" if os.getenv("MACOS_LOCAL_E11_VARIANT")=="16k" else "runs/e11-arm-gfx-mmio-rw-guest.log")
log.write_text(r.stdout+"\n=== STDERR ===\n"+r.stderr)
print(f"VMApple+Reims direct guest MMIO read exit={r.returncode}; UART={r.stdout.strip()!r}")
if r.returncode!=0 or "REIMS ARM TCG MMIO RW PASS" not in r.stdout:
    print("stderr tail:",r.stderr[-1000:])
    raise SystemExit(2)
print("PASS: AArch64 CPU/MMIO path safely writes and reads the modern Reims GPU version registers")
