#!/usr/bin/env python3
"""Build self-authored AArch64 payloads and test both VMApple branches.

No Apple code or guest operating system is loaded. Expected negative guest
exits are explicit; host timeouts are always failures, never successes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
BUILD=ROOT/'build/e06-platform-contracts'
TARGETS={
 'headless':('build/qemu-vmapple-headless/qemu-system-aarch64','vmapple,gfx-device=none,memory-backend=ram'),
 'reims':('build/qemu-reims-arm16k/qemu-system-aarch64','vmapple,memory-backend=ram'),
}
CASES={
 'timer':[
  ('timer-positive',[],0,'E06 TIMER GIC WFI REARM PASS'),
  ('timer-no-ppi',['-DBLOCK_PPI=1'],1,'PPI_DISABLED_NO_IRQ_EXPECTED'),
 ],
 'combined':[
  ('mmu-plus-timer',['-DTIMER_LIBRARY=1','-DTIMER_AFTER_MMU=1'],0,'E06 MMU TIMER INTEGRATED PASS'),
 ],
 'mmu':[
  ('mmu-positive',[],0,'E06 MMU16K PERMISSION TRANSLATION TLBI PASS'),
  ('mmu-writable-negative',['-DALLOW_RO_WRITES=1'],1,'MISSING_EXPECTED_PERMISSION_FAULT'),
 ]
}

def compile_case(suite,name,defines):
 d=BUILD/name; d.mkdir(parents=True,exist_ok=True)
 common=['clang','--target=aarch64-none-elf','-O2','-ffreestanding','-fno-builtin',
         '-fno-stack-protector','-fno-pic','-fno-pie','-mgeneral-regs-only',
         '-Wall','-Wextra','-I',str(HERE)]
 objs=[]
 files=['start.S','runtime.c']+(['mmu.c','timer.c'] if suite=='combined' else [f'{suite}.c'])
 for filename in files:
  obj=d/(filename.replace('.','_')+'.o')
  p=subprocess.run([*common,*defines,'-c',str(HERE/filename),'-o',str(obj)],capture_output=True,text=True)
  if p.returncode: raise RuntimeError(f'compile {filename}:\n{p.stdout}\n{p.stderr}')
  if p.stderr: print(p.stderr,file=sys.stderr,end='')
  objs.append(str(obj))
 image=d/'firmware.bin';elf=d/'firmware.elf'
 subprocess.run(['ld.lld','--build-id=none','-T',str(HERE/'link.ld'),*objs,'-o',str(elf)],check=True)
 subprocess.run(['llvm-objcopy','-O','binary',str(elf),str(image)],check=True)
 return image

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--suite',choices=['timer','mmu','combined','all'],default='all')
 parser.add_argument('--target',choices=[*TARGETS,'all'],default='all')
 args=parser.parse_args()
 BUILD.mkdir(parents=True,exist_ok=True)
 results=[]
 for suite in CASES if args.suite=='all' else [args.suite]:
  for name,defines,expected_exit,marker in CASES[suite]:
   image=compile_case(suite,name,defines)
   for target in TARGETS if args.target=='all' else [args.target]:
    binary,machine=TARGETS[target]
    d=BUILD/target;d.mkdir(exist_ok=True)
    for filename,size in [('aux.raw',4*1024*1024),('root.raw',32*1024*1024)]:
     p=d/filename
     if not p.exists():
      with p.open('wb') as f: f.truncate(size)
    command=[str(ROOT/binary),'-object','memory-backend-memfd,id=ram,size=512M,share=on',
             '-machine',machine,'-cpu','max','-accel','tcg','-m','512M','-smp','1',
             '-nodefaults','-nic','none','-display','none','-nographic','-monitor','none',
             '-serial','stdio','-semihosting-config','enable=on,target=native',
             '-bios',str(image),
             '-drive',f'file={d/"aux.raw"},format=raw,if=pflash,index=0',
             '-drive',f'file={d/"root.raw"},format=raw,if=pflash,index=1']
    started=time.monotonic();timeout=False
    try:
     p=subprocess.run(command,capture_output=True,text=True,timeout=15,
                      env=dict(os.environ,REIMS_VGPU_WINDOW='0',REIMS_VGPU_LOG='warn'))
     out,err,rc=p.stdout,p.stderr,p.returncode
    except subprocess.TimeoutExpired as e:
     timeout=True;rc=None
     out=e.stdout.decode(errors='replace') if isinstance(e.stdout,bytes) else (e.stdout or '')
     err=e.stderr.decode(errors='replace') if isinstance(e.stderr,bytes) else (e.stderr or '')
    log=ROOT/f'runs/e06-contracts-{target}-{name}.log'
    log.write_text(out+'\n=== STDERR ===\n'+err)
    passed=not timeout and rc==expected_exit and marker in out
    row={'target':target,'suite':suite,'case':name,'binary':binary,
         'firmware_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
         'guest_exit':rc,'expected_exit':expected_exit,'host_timeout':timeout,
         'passed':passed,'uart':out,'log':str(log.relative_to(ROOT)),
         'wall_seconds_not_benchmark':round(time.monotonic()-started,4),'command':command}
    results.append(row)
    print(f'{target}/{name}: rc={rc} expected={expected_exit} verdict={"PASS" if passed else "FAIL"}')
    print(out,end='')
    if not passed:
     print(err[-3500:],file=sys.stderr)
     return finish(args,results,False)
 return finish(args,results,True)

def finish(args,results,passed):
 report={'time_utc':datetime.now(timezone.utc).isoformat(),'suite':args.suite,'target':args.target,
         'scope':'synthetic EL1 ARM64 timer/GIC and MMU contracts; not Apple firmware/macOS',
         'passed':passed,'cases':results}
 path=ROOT/f'runs/e06-contracts-{args.suite}-{args.target}-results.json'
 path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print(f'Result: {path.relative_to(ROOT)}')
 return 0 if passed else 1
if __name__=='__main__':sys.exit(main())
