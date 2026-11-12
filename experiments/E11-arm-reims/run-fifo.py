#!/usr/bin/env python3
"""Differential, self-authored guest root-FIFO enable/disable experiment.

Correctness applies ONLY to this minimal synthetic FIFO handshake. It is not
a Metal/IOSurface/WindowServer protocol test.
"""
from pathlib import Path
import os
import subprocess
import sys

root=Path(__file__).resolve().parents[2]
t=root/"experiments/E11-arm-reims"
work=root/"build/e11-arm-reims"
work.mkdir(parents=True,exist_ok=True)
obj=work/"probe-fifo.o"
elf=work/"probe-fifo.elf"
image=work/"probe-fifo.bin"
commands=[
    ["clang","--target=aarch64-none-elf","-c",str(t/"probe-fifo.S"),"-o",str(obj)],
    ["ld.lld","-T",str(root/"experiments/E06-vmapple/firmware.ld"),str(obj),"-o",str(elf)],
    ["llvm-objcopy","-O","binary",str(elf),str(image)]
]
for cmd in commands:
    subprocess.run(cmd,check=True)
for name,size in [("aux-test.raw",4*1024*1024),("root-test.raw",32*1024*1024)]:
    p=work/name
    if not p.exists():
        with p.open("wb") as f:f.truncate(size)
for variant,build in [("4k","qemu-reims-arm"),("16k","qemu-reims-arm16k")]:
    qemu=root/"build"/build/"qemu-system-aarch64"
    args=[
       str(qemu),
       "-object","memory-backend-memfd,id=ram,size=512M,share=on",
       "-machine","vmapple,memory-backend=ram","-m","512M","-smp","1",
       "-cpu","max","-accel","tcg","-nodefaults","-nic","none",
       "-nographic","-display","none","-monitor","none","-serial","stdio",
       "-semihosting-config","enable=on,target=native",
       "-bios",str(image),
       "-drive",f"file={work/'aux-test.raw'},format=raw,if=pflash,index=0",
       "-drive",f"file={work/'root-test.raw'},format=raw,if=pflash,index=1"
    ]
    env=dict(os.environ, REIMS_VGPU_WINDOW="0", REIMS_VGPU_LOG="warn")
    try:
        proc=subprocess.run(args,text=True,capture_output=True,timeout=16,env=env)
    except subprocess.TimeoutExpired as error:
        print(f"{variant}: FIFO handshake timed out: {error}")
        raise SystemExit(9)
    log=root/f"runs/e11-arm-rootfifo-{variant}.log"
    log.write_text(proc.stdout+"\n=== STDERR ===\n"+proc.stderr)
    notes=[x for x in proc.stderr.splitlines()
           if "root page" in x.lower() or "root fifo" in x.lower()]
    print(f"guest ABI={variant} rc={proc.returncode} UART={proc.stdout.strip()!r}")
    for n in notes[:12]:
        print(f"  HOST_LOG {n[:260]}")
    if variant=="4k":
        ok=proc.returncode!=0 and "REIMS ARM ROOT FIFO ENABLE FAIL" in proc.stdout
        print(f"EXPECTED_REFUSAL={'PASS' if ok else 'FAIL'}")
    else:
        ok=proc.returncode==0 and "REIMS ARM ROOT FIFO ENABLE PASS" in proc.stdout
        print(f"EXPECTED_ARM64_FIFO={'PASS' if ok else 'FAIL'}")
    if not ok:
        print("stderr tail:",proc.stderr[-2000:])
        raise SystemExit(3)
print("PASS: the same ARM64 root+FIFO page numbers fail with 4K and succeed with 16K ABI")
