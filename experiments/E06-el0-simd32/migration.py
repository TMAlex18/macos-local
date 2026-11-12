#!/usr/bin/env python3
"""双 vCPU 自制 EL0 迁移夹具；不执行 Apple 固件、不联系 TSS。"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
import run as fixture

ROOT=fixture.ROOT
BINARY=ROOT/'build/qemu-bdif-usb/qemu-system-aarch64'
CPU='max,pauth-qarma5=on,cntfrq=24000000,x-cntfrq-exact=on'
PASS='E06 DUAL CPU EL0 MIGRATION ALL32 TLS ASID QARMA5 PASS'
CASES={
    'positive':('positive',[],0,PASS),
    'skip-tls':('skip-tls',[],1,'FAIL: TPIDR_EL0_CONTEXT_CROSSTALK'),
    'skip-q31':('skip-q31',[],1,'FAIL: FP_SIMD_CONTEXT_CROSSTALK'),
    # Historical case name: corrupt the complete TTBR0 (table base + ASID),
    # not the ASID tag in isolation.
    'wrong-asid':('positive',['-DWRONG_RESTORE_ASID=1'],1,'FAIL: EL0_ADDRESS_SPACE_RESTORE_FAILURE'),
    'wrong-key':('wrong-key',[],1,'FAIL: PAC_AUTH_FAILURE_AFTER_SWITCH'),
}
LAZY_CASES={
    'positive':CASES['positive'],
    'skip-lazy-fp':('positive',['-DSKIP_LAZY_FP_RESTORE=1'],1,'FAIL: LAZY_FP_CONTEXT_RESTORE_MISSING'),
}
spec=importlib.util.spec_from_file_location('smp_qmp',ROOT/'experiments/E06-smp-mmu/run.py')
smp=importlib.util.module_from_spec(spec);spec.loader.exec_module(smp)


def execute(image,case,mode,rounds,folder,pac_model=False,lazy_fp=False):
    expected,marker=(LAZY_CASES if lazy_fp else CASES)[case][2:]
    row={'case':case,'mode':mode,'rounds':rounds,'virtual_cpus':2,'user_tasks':2,
         'qemu_binary':str(BINARY.relative_to(ROOT)),'cpu_requested':CPU,
         'qemu_sha256':hashlib.sha256(BINARY.read_bytes()).hexdigest(),
         'firmware_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
         'pac_model_enabled':pac_model,
         'lazy_fp_enabled':lazy_fp,
         'expected_exit':expected,'expected_marker':marker,'host_timeout':False,'errors':[]}
    with tempfile.TemporaryDirectory(prefix='migration-',dir=fixture.BUILD) as name:
        work=Path(name)
        for name,size in [('aux.raw',4*1024**2),('root.raw',32*1024**2)]:
            with (work/name).open('wb') as stream:stream.truncate(size)
        endpoint=work/'qmp.sock'
        machine='vmapple,memory-backend=ram'+(',x-pac-software-model=on' if pac_model else '')
        args=[str(BINARY),'-object','memory-backend-memfd,id=ram,size=512M,share=on',
              '-machine',machine,'-m','512M','-smp','2',
              '-cpu',CPU,'-accel',f'tcg,thread={mode}','-nodefaults','-nic','none',
              '-display','none','-nographic','-monitor','none','-serial','stdio',
              '-S','-qmp',f'unix:{endpoint},server=on,wait=off',
              '-semihosting-config','enable=on,target=native','-bios',str(image),
              '-drive',f'file={work/"aux.raw"},format=raw,if=pflash,index=0',
              '-drive',f'file={work/"root.raw"},format=raw,if=pflash,index=1']
        proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              env=dict(os.environ,REIMS_VGPU_WINDOW='0',REIMS_VGPU_LOG='warn'))
        connection=stream=None
        try:
            deadline=time.monotonic()+12
            while not endpoint.exists():
                if proc.poll() is not None:raise RuntimeError('QEMU exited before QMP')
                if time.monotonic()>deadline:raise TimeoutError('QMP startup')
                time.sleep(.02)
            connection=socket.socket(socket.AF_UNIX);connection.settimeout(8);connection.connect(str(endpoint))
            stream=connection.makefile('rwb',buffering=0)
            assert 'QMP' in json.loads(stream.readline())
            smp.qmp_request(stream,'qmp_capabilities')
            cpus=smp.qmp_request(stream,'query-cpus-fast')
            row['host_vcpu_threads']=[{'cpu':c['cpu-index'],'thread_id':c.get('thread-id')} for c in cpus]
            tids={c.get('thread-id') for c in cpus}
            if len(cpus)!=2 or None in tids or len(tids)!=(2 if mode=='multi' else 1):
                raise RuntimeError('Requested TCG scheduling mode was not observed')
            smp.qmp_request(stream,'cont')
            out,err=proc.communicate(timeout=30)
        except Exception as error:
            row['errors'].append(repr(error))
            row['host_timeout']=isinstance(error,(TimeoutError,subprocess.TimeoutExpired))
            if proc.poll() is None:proc.terminate()
            try:out,err=proc.communicate(timeout=4)
            except subprocess.TimeoutExpired:proc.kill();out,err=proc.communicate()
        finally:
            if stream:stream.close()
            if connection:connection.close()
        row['guest_exit']=proc.returncode
        row['process_reaped']=proc.poll() is not None
    text=out.decode(errors='replace')
    values={k:int(v,16) for k,v in re.findall(r'^([A-Z0-9_]+)=(0x[0-9a-f]+)$',text,re.M)}
    row['observed_counters']=values
    passed=not row['errors'] and not row['host_timeout'] and row['guest_exit']==expected and marker in text
    if case=='positive':
        for task in range(2):
            for cpu in range(2):
                passed=passed and values.get(f'TASK{task}_EL0_CPU{cpu}_CHECKS',0)>0
                passed=passed and values.get(f'TASK{task}_CPU{cpu}_IRQS',0)>0
                passed=passed and values.get(f'TASK{task}_CPU{cpu}_SIGNED_IRQS',0)>0
            passed=passed and values.get(f'TASK{task}_EL0_CPU_CHANGES',0)>0
            passed=passed and values.get(f'TASK{task}_EL0_CPU_MASK')==3
            passed=passed and values.get(f'TASK{task}_IRQS')==rounds
        passed=passed and all(values.get(k)==v for k,v in [
            ('CPU0_CNTFRQ_HZ',24000000),('CPU1_CNTFRQ_HZ',24000000),
            ('EL0_TIMER_IRQS',2*rounds),('EL0_PERMISSION_FAULTS',6),('FP_Q_REGISTERS_SAVED',64),
            ('EL0_SVCS',4),('USER_CONTEXT_SWITCHES',2*rounds)])
        if pac_model:
            passed=passed and all(values.get(k)==1 for k in (
                'PAC_MODEL_ENABLED','CPU0_MODEL_INITIALIZED','CPU1_MODEL_INITIALIZED'))
            passed=passed and all(values.get(k,0)>0 for k in ('CPU0_KERNEL_BANK_CHECKS','CPU1_KERNEL_BANK_CHECKS'))
        if lazy_fp:
            passed=passed and values.get('LAZY_FP_ENABLED')==1
            for cpu in range(2):
                traps=0
                for task in range(2):
                    count=values.get(f'TASK{task}_CPU{cpu}_FP_TRAPS',0)
                    passed=passed and count>0 and values.get(f'TASK{task}_CPU{cpu}_FP_RESTORES')==count
                    passed=passed and count<=values.get(f'TASK{task}_CPU{cpu}_FP_DEFERRED',0)
                    traps+=count
                passed=passed and values.get(f'CPU{cpu}_FP_EXACT_ELR_CHECKS')==traps
                passed=passed and values.get(f'CPU{cpu}_FP_LAST_ESR',0)>>26==7
            for task in range(2):
                passed=passed and sum(values.get(f'TASK{task}_CPU{cpu}_IRQ_BEFORE_FP',0) for cpu in range(2))>0
    else:passed=passed and PASS not in text
    row['passed']=bool(passed)
    log=folder/f'{mode}-{case}.log';log.write_text(text+'\n=== STDERR ===\n'+err.decode(errors='replace'))
    row['log']=str(log.relative_to(ROOT))
    print(f'{mode}/{case}: exit={row["guest_exit"]} expected={expected} {"PASS" if passed else "FAIL"}',flush=True)
    print(text,end='',flush=True)
    if row['errors']:print(row['errors'],flush=True)
    if not passed:print(err.decode(errors='replace')[-2000:],flush=True)
    return row


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=[*CASES,'skip-lazy-fp','negative','all'],default='positive')
    parser.add_argument('--mode',choices=['multi','single','all'],default='all')
    parser.add_argument('--rounds',type=int,default=16)
    parser.add_argument('--pac-model',action='store_true',
                        help='Opt in to the project software key-context model; only positive/wrong-key cases')
    parser.add_argument('--lazy-fp',action='store_true',
                        help='Opt in to precise EL0 FP-access traps and lazy full-state restore; architectural QARMA5 only')
    args=parser.parse_args()
    if not 8<=args.rounds<=128:parser.error('rounds must be 8..128')
    if args.pac_model and args.case not in ('positive','wrong-key','all','negative'):
        parser.error('--pac-model covers only positive and wrong-key')
    if args.lazy_fp and args.pac_model:parser.error('--lazy-fp and --pac-model are separate modes')
    if args.lazy_fp and args.case not in ('positive','skip-lazy-fp','all','negative'):
        parser.error('--lazy-fp covers only positive and skip-lazy-fp')
    if not args.lazy_fp and args.case=='skip-lazy-fp':parser.error('skip-lazy-fp requires --lazy-fp')
    fixture.BUILD.mkdir(parents=True,exist_ok=True)
    variant='model-' if args.pac_model else 'lazy-' if args.lazy_fp else ''
    label='migration-'+variant+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    folder=ROOT/'runs/e06-el0-simd32'/label;folder.mkdir(parents=True)
    report={'scope':'Two self-authored EL0 tasks migrate between two vCPUs after real PPI27 interrupts.',
            'apple_firmware':False,'tss_requests':False,'qemu_source_modified':False,
            'pac_model_enabled':args.pac_model,'lazy_fp_enabled':args.lazy_fp,'cases':[],'passed':False,'limits':[
                'QARMA5 plus the explicit project context model only when requested; no Apple crypto equivalence.',
                'MTTCG and single-thread TCG share one emulator implementation, not independent hardware.',
                'No XNU, OS scheduler, SVE/SME, nested IRQ, DMA/IOMMU or M2 root callback claim.']}
    path=folder/'results.json'
    selected=LAZY_CASES if args.lazy_fp else CASES
    if args.case=='all':cases=list(selected)
    elif args.case=='negative':cases=[c for c in selected if c!='positive']
    else:cases=[args.case]
    if args.pac_model:
        cases=[c for c in cases if c in ('positive','wrong-key')]
    for case in cases:
        buildcase,flags,_,_=selected[case]
        image=fixture.build('clock-exact',buildcase,args.rounds,migration=True,
                            extra_flags=[*flags,*(['-DUSE_PAC_MODEL=1'] if args.pac_model else []),
                                         *(['-DLAZY_FP=1'] if args.lazy_fp else [])],
                            tag=variant+case)
        for mode in ['multi','single'] if args.mode=='all' else [args.mode]:
            report['cases'].append(execute(image,case,mode,args.rounds,folder,args.pac_model,args.lazy_fp))
            report['passed']=all(x['passed'] for x in report['cases'])
            path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            if not report['passed']:print('RESULT',path);return 1
    print('RESULT',path);return 0


if __name__=='__main__':raise SystemExit(main())
