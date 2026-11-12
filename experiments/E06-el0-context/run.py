#!/usr/bin/env python3
"""Self-authored EL0/MMU/PAuth/timer context-switch experiment.

One virtual CPU and two EL0 tasks, no Apple firmware/TSS/USB. Outputs are
behavioral test results, not OS-compatibility or performance claims.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
BASE=ROOT/'experiments/E06-platform-contracts'
BUILD=ROOT/'build/e06-el0-context'
PROFILES={
 'headless':('build/qemu-vmapple-headless/qemu-system-aarch64',
             'vmapple,gfx-device=none,memory-backend=ram',[]),
 'reims':('build/qemu-reims-arm16k/qemu-system-aarch64',
          'vmapple,memory-backend=ram',[]),
 'reims-model':('build/qemu-reims-arm16k/qemu-system-aarch64',
                'vmapple,memory-backend=ram,x-pac-software-model=on',['-DUSE_PAC_MODEL=1'])}
CASES={
 'positive':([],0,'E06 EL0 MMU PAC IRQ CONTEXT PASS'),
 'wrong-key':(['-DWRONG_RESTORE_KEY=1'],1,'FAIL: PAC_AUTH_FAILURE_AFTER_SWITCH'),
 'data-executable':(['-DREMOVE_DATA_XN=1'],1,'FAIL: MISSING_EL0_PERMISSION_FAULT'),
 'ppi-disabled':(['-DDISABLE_PPI=1'],1,'FAIL: TIMER_PREEMPTION_MISSING')}

def build(profile,case,rounds):
    out=BUILD/f'{profile}-{case}-{rounds}'
    out.mkdir(parents=True,exist_ok=True)
    flags=['clang','--target=aarch64-none-elf','-march=armv8.3-a','-O2',
           '-ffreestanding','-fno-builtin','-fno-stack-protector','-fno-pic','-fno-pie',
           '-mgeneral-regs-only','-Wall','-Wextra','-Werror','-I',str(BASE),
           f'-DROUNDS={rounds}',*PROFILES[profile][2],*CASES[case][0]]
    objs=[]
    for source in [BASE/'start.S',BASE/'runtime.c',HERE/'user.S',HERE/'context.c']:
        obj=out/(source.name.replace('.','_')+'.o')
        subprocess.run([*flags,'-c',str(source),'-o',str(obj)],check=True)
        objs.append(str(obj))
    elf=out/'firmware.elf';image=out/'firmware.bin'
    subprocess.run(['ld.lld','--build-id=none','-T',str(HERE/'link.ld'),*objs,'-o',str(elf)],check=True)
    subprocess.run(['llvm-objcopy','-O','binary',str(elf),str(image)],check=True)
    return image

def run(profile,case,image,rounds,logs):
    binary,machine,_=PROFILES[profile]
    result={'profile':profile,'case':case,'rounds':rounds,'virtual_cpus':1,'user_tasks':2,
            'qemu_binary':binary,'expected_exit':CASES[case][1],
            'guest_firmware_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
            'qemu_source_modified':False,'apple_code_used':False,'passed':False}
    with tempfile.TemporaryDirectory(prefix='el0-state-',dir=BUILD) as d:
        tmp=Path(d)
        for name,size in [('aux.raw',4*1024*1024),('root.raw',32*1024*1024)]:
            with (tmp/name).open('wb') as f:f.truncate(size)
        args=[str(ROOT/binary),'-object','memory-backend-memfd,id=ram,size=512M,share=on',
              '-machine',machine,'-m','512M','-smp','1','-cpu','max,pauth-qarma5=on',
              '-accel','tcg','-nodefaults','-nic','none','-display','none','-nographic',
              '-monitor','none','-serial','stdio','-semihosting-config','enable=on,target=native',
              '-bios',str(image),'-drive',f'file={tmp/"aux.raw"},format=raw,if=pflash,index=0',
              '-drive',f'file={tmp/"root.raw"},format=raw,if=pflash,index=1']
        try:
            p=subprocess.run(args,capture_output=True,text=True,timeout=15,
               env=dict(os.environ,REIMS_VGPU_WINDOW='0',REIMS_VGPU_LOG='warn'))
            out,err=p.stdout,p.stderr
            result['guest_exit']=p.returncode;result['host_timeout']=False
        except subprocess.TimeoutExpired as e:
            out=(e.stdout or b'').decode(errors='replace') if isinstance(e.stdout,bytes) else e.stdout or ''
            err=(e.stderr or b'').decode(errors='replace') if isinstance(e.stderr,bytes) else e.stderr or ''
            result['guest_exit']=None;result['host_timeout']=True
    result['uart']=out
    values={k:int(v,16) for k,v in re.findall(r'^([A-Z0-9_]+)=(0x[0-9a-f]+)$',out,re.M)}
    result['counters']=values
    passed=result['guest_exit']==CASES[case][1] and CASES[case][2] in out and not result['host_timeout']
    if case=='positive':
        passed=passed and all(values.get(k)==v for k,v in [
            ('TASK0_IRQS',rounds),('TASK1_IRQS',rounds),('EL0_PERMISSION_FAULTS',6),
            ('EL0_SVCS',4),('EL0_TIMER_IRQS',2*rounds)])
        passed=passed and values.get('TASK0_SIGNED_FRAME_IRQS',0)>0 and values.get('TASK1_SIGNED_FRAME_IRQS',0)>0
    else:
        passed=passed and 'E06 EL0 MMU PAC IRQ CONTEXT PASS' not in out
    result['passed']=bool(passed)
    path=logs/f'{profile}-{case}.log'
    path.write_text(out+'\n=== STDERR ===\n'+err)
    result['log']=str(path.relative_to(ROOT))
    print(f'{profile}/{case}: exit={result["guest_exit"]} expected={CASES[case][1]} {"PASS" if passed else "FAIL"}',flush=True)
    print(out,end='',flush=True)
    if not passed:print(err[-2000:],flush=True)
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--profile',choices=[*PROFILES,'all'],default='all')
    ap.add_argument('--case',choices=[*CASES,'all'],default='all')
    ap.add_argument('--rounds',type=int,default=12)
    a=ap.parse_args()
    if not 4<=a.rounds<=128:ap.error('rounds must be 4..128')
    BUILD.mkdir(parents=True,exist_ok=True)
    label=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    logs=ROOT/'runs/e06-el0-context'/label;logs.mkdir(parents=True)
    report={'at_utc':label,'scope':'one vCPU, two self-authored EL0 tasks, MMU/ASID/PAuth/timer preemption',
            'network':False,'apple_firmware':False,'qemu_sources_modified':False,
            'cases':[],'passed':False,'limits':[
                'QARMA5 architecture and optional local HVC key-state model, not Apple crypto equivalence.',
                'Two tasks on one vCPU; not a scheduler, multi-core OS, cache/DMA or XNU test.',
                'Both machines share QEMU code; not independent hardware validation.',
                'Host deadline or unexpected guest failure never passes a negative control.']}
    path=logs/'results.json'
    for profile in PROFILES if a.profile=='all' else [a.profile]:
        for case in CASES if a.case=='all' else [a.case]:
            image=build(profile,case,a.rounds)
            report['cases'].append(run(profile,case,image,a.rounds,logs))
            report['passed']=all(x['passed'] for x in report['cases'])
            path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            if not report['passed']:print('RESULT',path);return 1
    print('RESULT',path);return 0
if __name__=='__main__':raise SystemExit(main())
