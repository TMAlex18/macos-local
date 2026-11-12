#!/usr/bin/env python3
"""Guest timer/MMU/wait regression for the isolated exact-clock QEMU build.

Expected guest failure is distinct from host timeout; every run is bounded.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
PLATFORM=ROOT/'experiments/E06-platform-contracts'
BUILD=ROOT/'build/e06-clock-exact'
BINARY=ROOT/'build/qemu-clock-exact/qemu-system-aarch64'

def compile_payload(name,sources,defines):
    out=BUILD/name;out.mkdir(parents=True,exist_ok=True)
    objs=[]
    for f in [PLATFORM/'start.S',PLATFORM/'runtime.c',*sources]:
        obj=out/(f.name.replace('.','_')+'.o')
        subprocess.run(['clang','--target=aarch64-none-elf','-march=armv8.7-a',
          '-O2','-ffreestanding','-fno-builtin','-fno-stack-protector','-fno-pie',
          '-mgeneral-regs-only','-Wall','-Wextra',*defines,'-I',str(PLATFORM),
          '-c',str(f),'-o',str(obj)],check=True,capture_output=True)
        objs.append(str(obj))
    elf=out/'guest.elf';image=out/'guest.bin'
    subprocess.run(['ld.lld','--build-id=none','-T',str(PLATFORM/'link.ld'),*objs,'-o',str(elf)],check=True)
    subprocess.run(['llvm-objcopy','-O','binary',str(elf),str(image)],check=True)
    return image

def base_args(freq,exact,image):
    for n,size in [('aux.raw',4*1024*1024),('root.raw',32*1024*1024)]:
        p=BUILD/n
        if not p.exists():
            with p.open('wb') as f:f.truncate(size)
    return [str(BINARY),'-object','memory-backend-memfd,id=ram,size=512M,share=on',
        '-M','vmapple,memory-backend=ram','-m','512M','-smp','1',
        '-cpu',f'max,cntfrq={freq},x-cntfrq-exact={"on" if exact else "off"}',
        '-accel','tcg','-nodefaults','-nic','none','-display','none',
        '-nographic','-monitor','none','-serial','stdio',
        '-semihosting-config','enable=on,target=native','-bios',str(image),
        '-drive',f'file={BUILD/"aux.raw"},format=raw,if=pflash,index=0',
        '-drive',f'file={BUILD/"root.raw"},format=raw,if=pflash,index=1']
CASES=[
 ('timer-legacy24',24000000,False,[PLATFORM/'timer.c'],[],0,'E06 TIMER GIC WFI REARM PASS'),
 ('timer-exact24',24000000,True,[PLATFORM/'timer.c'],[],0,'E06 TIMER GIC WFI REARM PASS'),
 ('combined-exact24',24000000,True,[PLATFORM/'mmu.c',PLATFORM/'timer.c'],['-DTIMER_LIBRARY=1','-DTIMER_AFTER_MMU=1'],0,'E06 MMU TIMER INTEGRATED PASS'),
 ('disabled-ppi24',24000000,True,[PLATFORM/'timer.c'],['-DBLOCK_PPI=1'],1,'PPI_DISABLED_NO_IRQ_EXPECTED'),
 ('writable-page24',24000000,True,[PLATFORM/'mmu.c'],['-DALLOW_RO_WRITES=1'],1,'MISSING_EXPECTED_PERMISSION_FAULT'),
 ('wait-exact24',24000000,True,[HERE/'waits.c'],[],0,'EXACT_CLOCK_WAIT_SUITE_PASS'),
 ('wait-exact1g',1000000000,True,[HERE/'waits.c'],[],0,'EXACT_CLOCK_WAIT_SUITE_PASS'),
 ('wait-exact192',19200000,True,[HERE/'waits.c'],[],0,'EXACT_CLOCK_WAIT_SUITE_PASS'),
 ('disabled-event24',24000000,True,[HERE/'waits.c'],['-DDISABLE_EVENT=1'],1,'EVENT_DISABLED_TIMEOUT_EXPECTED'),
 ('stress-exact24',24000000,True,[HERE/'timer-stress.c'],[],0,'EXACT_CLOCK_TIMER_STRESS_PASS'),
 ('stress-exact1g',1000000000,True,[HERE/'timer-stress.c'],[],0,'EXACT_CLOCK_TIMER_STRESS_PASS'),
 ('frequency-guard',1000000001,True,[PLATFORM/'timer.c'],[],1,'x-cntfrq-exact requires TCG'),
]
def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--case',default='all',choices=['all',*[c[0] for c in CASES]])
    opt=ap.parse_args();results=[];success=True
    for name,freq,exact,sources,defines,rc,marker in CASES:
        if opt.case!='all' and opt.case!=name:continue
        image=compile_payload(name,sources,defines)
        command=base_args(freq,exact,image)
        timeout=False
        try:
            p=subprocess.run(command,text=True,capture_output=True,timeout=15,
                env=dict(os.environ,REIMS_VGPU_WINDOW='0',REIMS_VGPU_LOG='warn'))
            got=p.returncode;out=p.stdout;err=p.stderr
        except subprocess.TimeoutExpired as e:
            timeout=True;got=None
            out=(e.stdout or b'').decode(errors='replace') if isinstance(e.stdout,bytes) else (e.stdout or '')
            err=(e.stderr or b'').decode(errors='replace') if isinstance(e.stderr,bytes) else (e.stderr or '')
        passed=not timeout and got==rc and marker in out+err
        log=ROOT/f'runs/clock-exact-{name}.log';log.write_text(out+'\n=== STDERR ===\n'+err)
        results.append({'case':name,'freq_hz':freq,'exact':exact,'guest_or_startup_exit':got,
            'expected_exit':rc,'host_timeout':timeout,'passed':passed,
            'firmware_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
            'log':str(log.relative_to(ROOT)),'uart':out})
        print(f'{name}: exit={got} expected={rc} -> {"PASS" if passed else "FAIL"}',flush=True)
        if not passed:
            print(out,err[-1800:]);success=False;break
    report={'date_utc':datetime.now(timezone.utc).isoformat(),'passed':success,
        'binary':str(BINARY.relative_to(ROOT)),'cases':results,
        'scope':'self-authored EL1 guest; generic timer/MMU/WFxT; not Apple OS or multicore timing'}
    path=ROOT/f'runs/e06-clock-exact-functional-{opt.case}.json'
    path.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print('RESULT',path)
    return 0 if success else 1
if __name__=='__main__':raise SystemExit(main())
