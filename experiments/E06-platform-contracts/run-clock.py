#!/usr/bin/env python3
"""AArch64 synthetic guest CNTFRQ/CNTVCT versus host monotonic elapsed time.

This measures QEMU timer modeling, not macOS performance or product latency.
"""
from pathlib import Path
import hashlib
import json
import os
import selectors
import subprocess
import time
from statistics import median
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
OUT=ROOT/'build/e06-platform-contracts/clock'
OUT.mkdir(parents=True,exist_ok=True)
objs=[]
for f in ['start.S','runtime.c','clock.c']:
    obj=OUT/f'{f.replace(".","_")}.o'
    cmd=['clang','--target=aarch64-none-elf','-O2','-ffreestanding',
        '-fno-builtin','-fno-stack-protector','-fno-pic','-fno-pie',
        '-mgeneral-regs-only','-I',str(HERE),'-c',str(HERE/f),'-o',str(obj)]
    subprocess.run(cmd,check=True,capture_output=True,text=True)
    objs.append(str(obj))
elf=OUT/'clock.elf';image=OUT/'clock.bin'
subprocess.run(['ld.lld','--build-id=none','-T',str(HERE/'link.ld'),*objs,'-o',str(elf)],check=True)
subprocess.run(['llvm-objcopy','-O','binary',str(elf),str(image)],check=True)
M=ROOT/'build/e06-platform-contracts/headless'
Q=ROOT/'build/qemu-vmapple-headless/qemu-system-aarch64'
results=[]
for requested in [None,24000000,25000000]:
    trials=[]
    for trial in range(3):
        command=[str(Q),'-object','memory-backend-memfd,id=ram,size=512M,share=on',
            '-machine','vmapple,gfx-device=none,memory-backend=ram',
            '-cpu','max']
        if requested is not None:
            command+=['-global',f'arm-cpu.cntfrq={requested}']
        command+=['-accel','tcg','-m','512M','-smp','1',
            '-nodefaults','-nic','none','-display','none','-nographic',
            '-monitor','none','-serial','stdio',
            '-semihosting-config','enable=on,target=native',
            '-bios',str(image),
            '-drive',f'file={M/"aux.raw"},format=raw,if=pflash,index=0',
            '-drive',f'file={M/"root.raw"},format=raw,if=pflash,index=1']
        with open(OUT/f'host-{requested or "default"}-{trial}.stderr','wb') as errfile:
            proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=errfile,
                                    start_new_session=True,bufsize=0)
            sel=selectors.DefaultSelector()
            sel.register(proc.stdout,selectors.EVENT_READ)
            deadline=time.monotonic()+8
            begin=end=None
            chunk=b''
            output=[]
            try:
                while time.monotonic()<deadline:
                    events=sel.select(.25)
                    for key,event in events:
                        data=os.read(key.fileobj.fileno(),4096)
                        if not data: continue
                        chunk+=data
                        while b'\n' in chunk:
                            line,chunk=chunk.split(b'\n',1)
                            text=line.decode('utf8','replace')
                            now=time.monotonic()
                            output.append(text)
                            if text=='MEASURE_BEGIN':begin=now
                            if text=='MEASURE_END':end=now
                    if end is not None and proc.poll() is not None:break
                if proc.poll() is None:
                    proc.wait(timeout=max(.1,deadline-time.monotonic()))
            except Exception:
                proc.kill();proc.wait()
                raise
            finally:
                sel.close();proc.stdout.close()
            if begin is None or end is None or proc.returncode!=0:
                raise RuntimeError(f'Test failed: requested={requested} trial={trial} rc={proc.returncode} output={output}')
            vals={}
            for line in output:
                if '=' in line:
                    k,v=line.split('=',1)
                    if v.startswith('0x'):vals[k]=int(v,16)
            hz=vals.get('CNTFRQ');ticks=vals.get('CNTVCT_TICKS')
            assert hz and ticks,output
            wall=end-begin
            guest=ticks/hz
            trials.append({'trial':trial,'counter_reported_hz':hz,'ticks':ticks,
              'guest_seconds':guest,'host_seconds':wall,
              'guest_time_over_host_time':guest/wall})
    group={'requested_hz':requested if requested else 'default',
           'samples':trials,'median_guest_time_over_host_time':
            median(x['guest_time_over_host_time'] for x in trials)}
    results.append(group)
    print('CNTFRQ',group['requested_hz'],'observed',trials[0]['counter_reported_hz'],
          'ratios',','.join(f"{x['guest_time_over_host_time']:.4f}" for x in trials),
          'median',f"{group['median_guest_time_over_host_time']:.4f}")
record={'date_utc':datetime.now(timezone.utc).isoformat(),
    'firmware_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
    'qemu_binary':str(Q.relative_to(ROOT)), 'profile':'VMApple headless, ARM TCG, 1vCPU, 512MiB',
    'source_clock_model':'gt_cntfrq_period_ns uses integer nanosecond scale',
    'series':results,
    'scope':'Synthetic counter timed loop, no Apple system or realistic workload',
    'limitations':'User-space monotonic timestamp at UART observation; host scheduling and QEMU startup may affect some readings; not a precision metrology device'}
path=ROOT/'runs/e06-contracts-clock-rate.json'
path.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
print('RESULT',path)
