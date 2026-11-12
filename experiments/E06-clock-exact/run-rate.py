#!/usr/bin/env python3
"""Paired legacy/exact timer rate checks in the SAME experimental QEMU.

UART observation timestamps include host scheduling; tolerances are coarse
and this is not a native-performance benchmark or precision oscillator test.
"""
from datetime import datetime,timezone
import importlib.util
import json
import os
from pathlib import Path
import selectors
import signal
import statistics
import subprocess
import time
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('clock_functional',HERE/'run-functional.py')
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
image=f.compile_payload('rate', [f.PLATFORM/'clock.c'], [])
SERIES=[('legacy24',24000000,False),('exact24',24000000,True),
        ('legacy1g',1000000000,False),('exact1g',1000000000,True),
        ('exact25',25000000,True),('exact192',19200000,True)]
records=[]
for name,hz,exact in SERIES:
    samples=[]
    for trial in range(3):
        args=f.base_args(hz,exact,image)
        log=ROOT/f'runs/clock-rate-{name}-{trial}.log'
        err_path=ROOT/f'runs/clock-rate-{name}-{trial}.stderr'
        env=dict(os.environ,REIMS_VGPU_WINDOW='0',REIMS_VGPU_LOG='warn')
        with err_path.open('wb') as err:
            p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=err,env=env,
                               start_new_session=True,bufsize=0)
            sel=selectors.DefaultSelector();sel.register(p.stdout,selectors.EVENT_READ)
            deadline=time.monotonic()+12
            start=end=None;pending=b'';lines=[]
            try:
                eof=False
                while time.monotonic()<deadline and not eof:
                    for key,event in sel.select(.2):
                        chunk=os.read(key.fileobj.fileno(),4096)
                        if not chunk:eof=True;break
                        pending+=chunk
                        while b'\n' in pending:
                            line,pending=pending.split(b'\n',1)
                            stamp=time.monotonic_ns();text=line.decode(errors='replace');lines.append(text)
                            if text=='MEASURE_BEGIN':start=stamp
                            elif text=='MEASURE_END':end=stamp
                p.wait(timeout=max(.1,deadline-time.monotonic()))
            except Exception:
                if p.poll() is None:
                    os.killpg(p.pid,signal.SIGKILL);p.wait()
                raise
            finally:sel.close();p.stdout.close()
        log.write_text('\n'.join(lines)+'\n')
        if p.returncode!=0 or start is None or end is None or end<=start:
            raise RuntimeError(f'Invalid guest/timestamps: {name}/{trial}, exit={p.returncode}, output={lines}')
        vals={}
        for line in lines:
            if '=0x' in line:
                k,v=line.split('=',1);vals[k]=int(v,16)
        assert vals['CNTFRQ']==hz
        guest_seconds=vals['CNTVCT_TICKS']/hz;wall=(end-start)/1e9
        samples.append({'trial':trial,'ticks':vals['CNTVCT_TICKS'],'reported_hz':hz,
            'guest_seconds':guest_seconds,'host_seconds':wall,
            'ratio':guest_seconds/wall,'log':str(log.relative_to(ROOT))})
    ratios=[x['ratio'] for x in samples]
    median=statistics.median(ratios)
    record={'case':name,'freq_hz':hz,'exact':exact,'samples':samples,'median_ratio':median}
    records.append(record)
    print(f'{name}: median guest/host={median:.7f}; trials={",".join(f"{x:.7f}" for x in ratios)}',flush=True)
checks={
 'legacy_24mhz_drift_reproduced':1.01<records[0]['median_ratio']<1.03,
 'exact_24mhz_drift_below_half_percent':abs(records[1]['median_ratio']-1)<.005,
 'exact_improves_over_legacy':abs(records[1]['median_ratio']-1)<abs(records[0]['median_ratio']-1)/3,
 '1g_controls_below_half_percent':all(abs(records[i]['median_ratio']-1)<.005 for i in (2,3)),
 'other_exact_frequencies_below_half_percent':all(abs(records[i]['median_ratio']-1)<.005 for i in (4,5)),
}
report={'time_utc':datetime.now(timezone.utc).isoformat(),
 'binary':'build/qemu-clock-exact/qemu-system-aarch64','same_binary_off_on':True,
 'firmware_source':'experiments/E06-platform-contracts/clock.c','series':records,
 'checks':checks,'passed':all(checks.values()),
 'limits':'Three 750ms samples/profile; host UART observation jitter. No ppm guarantee, no macOS execution, no performance SLO.'}
path=ROOT/'runs/e06-clock-exact-rate-results.json';path.write_text(json.dumps(report,indent=2)+'\n')
print('CHECKS',checks,'RESULT',path)
if not report['passed']:raise SystemExit(1)
