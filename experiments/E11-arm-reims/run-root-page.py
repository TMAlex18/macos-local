#!/usr/bin/env python3
"""Compare 4KiB x86-guest vs 16KiB ARM-guest page interpretation in Reims.

Look for the Reims core log's root-node mapping refusal in the 4K run.
A successful register readback alone does NOT establish mapping correctness.
"""
from pathlib import Path
import os
import subprocess

root=Path(__file__).resolve().parents[2]
t=root/"experiments/E11-arm-reims"
work=root/"build/e11-arm-reims"
work.mkdir(parents=True,exist_ok=True)
firmware=work/"probe-root-page.bin"
obj=work/"probe-root-page.o"
elf=work/"probe-root-page.elf"
for args in [
    ["clang","--target=aarch64-none-elf","-c",str(t/"probe-root-page.S"),"-o",str(obj)],
    ["ld.lld","-T",str(root/"experiments/E06-vmapple/firmware.ld"),str(obj),"-o",str(elf)],
    ["llvm-objcopy","-O","binary",str(elf),str(firmware)]]:
    subprocess.run(args,check=True)
for name,size in [("aux-test.raw",4*1024*1024),("root-test.raw",32*1024*1024)]:
    p=work/name
    if not p.exists():
        with p.open("wb") as f:f.truncate(size)
for variant,build in [("4k","qemu-reims-arm"),("16k","qemu-reims-arm16k")]:
    qemu=root/"build"/build/"qemu-system-aarch64"
    args=[
        str(qemu),"-object","memory-backend-memfd,id=ram,size=512M,share=on",
        "-machine","vmapple,memory-backend=ram","-m","512M","-smp","1",
        "-cpu","max","-accel","tcg","-nodefaults","-nic","none",
        "-nographic","-display","none","-monitor","none","-serial","stdio",
        "-semihosting-config","enable=on,target=native",
        "-bios",str(firmware),
        "-drive",f"file={work/'aux-test.raw'},format=raw,if=pflash,index=0",
        "-drive",f"file={work/'root-test.raw'},format=raw,if=pflash,index=1"
    ]
    env=dict(os.environ,REIMS_VGPU_WINDOW="0",REIMS_VGPU_LOG="warn")
    try:
        result=subprocess.run(args,text=True,capture_output=True,timeout=24,env=env)
    except subprocess.TimeoutExpired as error:
        raise SystemExit(f"{variant} timed out: {error}")
    log=root/f"runs/e11-arm-rootpage-{variant}.log"
    log.write_text(result.stdout+"\n=== STDERR ===\n"+result.stderr)
    errors=[x for x in result.stderr.splitlines()
            if "root page" in x.lower() or "mapping" in x.lower()]
    print(f"variant={variant}, qemu_exit={result.returncode}, uart={result.stdout.strip()!r}")
    for line in errors[:12]:
        print(f"  {line[:300]}")
    print(f"  mapping_log_lines={len(errors)}, log={log}")
    refused = "root page 0x1c000 not mapped" in result.stderr
    if variant == "4k" and not refused:
        raise SystemExit("4k variant unexpectedly mapped ARM64 physical page")
    if variant == "16k" and refused:
        raise SystemExit("16k variant unexpectedly refused the mapped ARM64 page")
    if result.returncode!=0 or "REIMS ARM ROOT PAGE WRITE PASS" not in result.stdout:
        print(result.stderr[-1600:])
        raise SystemExit(f"{variant} guest/MMIO transport failed")
