#!/usr/bin/env python3
"""Guest FIFO command roundtrip, plus a no-doorbell negative control.

The output check lives in self-authored ARM firmware. Host code only builds,
launches and captures. This queries device information, NOT draw/compute.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
TEST = ROOT / 'experiments/E11-arm-reims'
BUILD = ROOT / 'build/e11-device-info'
BINARY = ROOT / 'build/qemu-reims-arm16k/qemu-system-aarch64'
BUILD.mkdir(parents=True, exist_ok=True)
for name, size in [('aux.raw',4*1024*1024),('root.raw',32*1024*1024)]:
    p=BUILD/name
    if not p.exists():
        with p.open('wb') as f: f.truncate(size)
results=[]
for case, flags, expected_return, marker in [
    ('positive',[],0,'FIFO GET_DEVICE_INFO ROUNDTRIP PASS'),
    ('no-doorbell',['-DNO_DOORBELL=1'],1,'FIFO NOP STAMP TIMEOUT'),
]:
    obj=BUILD/f'{case}.o'; elf=BUILD/f'{case}.elf'; image=BUILD/f'{case}.bin'
    for command in [
        ['clang','--target=aarch64-none-elf',*flags,'-c',str(TEST/'probe-device-info.S'),'-o',str(obj)],
        ['ld.lld','-T',str(ROOT/'experiments/E06-vmapple/firmware.ld'),str(obj),'-o',str(elf)],
        ['llvm-objcopy','-O','binary',str(elf),str(image)],
    ]:
        subprocess.run(command,check=True)
    args=[str(BINARY),'-object','memory-backend-memfd,id=ram,size=512M,share=on',
          '-machine','vmapple,memory-backend=ram','-m','512M','-smp','1',
          '-cpu','max','-accel','tcg','-nodefaults','-nic','none','-display','none',
          '-nographic','-monitor','none','-serial','stdio',
          '-semihosting-config','enable=on,target=native','-bios',str(image),
          '-drive',f'file={BUILD/"aux.raw"},format=raw,if=pflash,index=0',
          '-drive',f'file={BUILD/"root.raw"},format=raw,if=pflash,index=1']
    env=dict(os.environ,REIMS_VGPU_WINDOW='0',REIMS_VGPU_LOG='warn')
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=25,env=env)
    except subprocess.TimeoutExpired as e:
        (ROOT/f'runs/v3-fifo-device-info-{case}.log').write_text(f'HOST TIMEOUT\n{e.stdout!r}\n{e.stderr!r}')
        raise SystemExit(f'{case}: host deadline exceeded; not a valid guest result')
    log=ROOT/f'runs/v3-fifo-device-info-{case}.log'
    log.write_text(p.stdout+'\n=== STDERR ===\n'+p.stderr)
    passed=p.returncode==expected_return and marker in p.stdout
    if case=='no-doorbell': passed=passed and 'ROUNDTRIP PASS' not in p.stdout
    print(f'{case}: guest_exit={p.returncode}; expected={expected_return}; observed={p.stdout.strip()!r}; verdict={"PASS" if passed else "FAIL"}')
    results.append({'case':case,'guest_exit':p.returncode,'expected_exit':expected_return,
                    'passed':passed,'uart':p.stdout,'log':str(log.relative_to(ROOT))})
    if not passed:
        print(p.stderr[-2400:],file=sys.stderr)
        break
report={'time_utc':datetime.now(timezone.utc).isoformat(),'scope':'guest FIFO consumption, shared-memory writeback and completion stamps; no draw/compute',
        'binary':str(BINARY.relative_to(ROOT)),'cases':results,'passed':len(results)==2 and all(x['passed'] for x in results)}
(ROOT/'runs/v3-fifo-device-info-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
if not report['passed']: raise SystemExit(1)
print('PASS: guest command -> Reims host FIFO -> guest RAM/stamp, negative control also behaved as expected')
