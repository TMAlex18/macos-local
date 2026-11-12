#!/usr/bin/env python3
"""Self-authored 2-vCPU MMU/ASID/TLBI/atomic experiments on two VMApple builds.

No Apple firmware, tickets, network service or USB kernel changes. Hardware
negative cases are explicit guest failures; host errors/timeouts always fail.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
BASE=ROOT/'experiments/E06-platform-contracts'
BUILD=ROOT/'build/e06-smp-mmu'
TARGETS={
 'headless':('build/qemu-vmapple-headless/qemu-system-aarch64','vmapple,gfx-device=none,memory-backend=ram'),
 'reims':('build/qemu-reims-arm16k/qemu-system-aarch64','vmapple,memory-backend=ram')}
CASES={
 'positive':([],0,'E06 SMP16K ASID TLBI PERMISSION ATOMIC PASS'),
 'wrong-map':(['-DOMIT_REMAP=1'],1,'FAIL: REMOTE_TRANSLATION_WRONG'),
 'missing-ro':(['-DREMOVE_RO=1'],1,'FAIL: MISSING_REMOTE_PERMISSION_FAULT')}

def build(case,rounds):
 out=BUILD/f'{case}-{rounds}';out.mkdir(parents=True,exist_ok=True)
 objects=[]
 for src in [HERE/'start.S',BASE/'runtime.c',HERE/'smp_mmu.c']:
  obj=out/(src.name.replace('.','_')+'.o')
  args=['clang','--target=aarch64-none-elf','-march=armv8-a','-O2',
        '-ffreestanding','-fno-builtin','-fno-stack-protector','-fno-pic','-fno-pie',
        '-mgeneral-regs-only','-Wall','-Wextra','-Werror','-I',str(BASE),
        f'-DROUNDS={rounds}',*CASES[case][0],'-c',str(src),'-o',str(obj)]
  subprocess.run(args,check=True);objects.append(str(obj))
 image=out/'firmware.bin';elf=out/'firmware.elf'
 subprocess.run(['ld.lld','--build-id=none','-T',str(BASE/'link.ld'),*objects,'-o',str(elf)],check=True)
 subprocess.run(['llvm-objcopy','-O','binary',str(elf),str(image)],check=True)
 return image

def qmp_request(stream,command):
 stream.write(json.dumps({'execute':command}).encode()+b'\n');stream.flush()
 while True:
  line=stream.readline()
  if not line:raise EOFError('QMP ended during control request')
  item=json.loads(line)
  if 'error' in item:raise RuntimeError(f'{command}: {item["error"]}')
  if 'return' in item:return item['return']

def run_case(image,case,target,mode,rounds,folder):
 binary,machine=TARGETS[target];wanted_exit,marker=CASES[case][1:]
 row={'target':target,'mode':mode,'case':case,'rounds':rounds,'vcpus':2,
      'binary':binary,'firmware_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
      'expected_exit':wanted_exit,'host_timeout':False,'passed':False,'errors':[]}
 with tempfile.TemporaryDirectory(prefix='smpmmu-',dir=BUILD) as tmp:
  temp=Path(tmp)
  for name,size in [('aux.raw',4*1024*1024),('root.raw',32*1024*1024)]:
   with (temp/name).open('wb') as f:f.truncate(size)
  sock=temp/'qmp.sock'
  cmd=[str(ROOT/binary),'-object','memory-backend-memfd,id=ram,size=512M,share=on',
       '-machine',machine,'-m','512M','-smp','2','-cpu','max','-accel',f'tcg,thread={mode}',
       '-nodefaults','-nic','none','-nographic','-display','none','-serial','stdio',
       '-monitor','none','-S','-qmp',f'unix:{sock},server=on,wait=off',
       '-semihosting-config','enable=on,target=native','-bios',str(image),
       '-drive',f'file={temp/"aux.raw"},format=raw,if=pflash,index=0',
       '-drive',f'file={temp/"root.raw"},format=raw,if=pflash,index=1']
  proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                        env=dict(os.environ,REIMS_VGPU_WINDOW='0',REIMS_VGPU_LOG='warn'))
  conn=None;stream=None;start=time.monotonic()
  try:
   deadline=start+12
   while not sock.exists():
    if proc.poll() is not None:raise RuntimeError(f'QEMU exited before QMP: {proc.returncode}')
    if time.monotonic()>deadline:raise TimeoutError('QMP startup deadline')
    time.sleep(.02)
   conn=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);conn.settimeout(8);conn.connect(str(sock))
   stream=conn.makefile('rwb',buffering=0)
   greeting=json.loads(stream.readline());assert 'QMP' in greeting
   qmp_request(stream,'qmp_capabilities')
   cpus=qmp_request(stream,'query-cpus-fast')
   row['host_vcpu_threads']=[{'cpu':c['cpu-index'],'thread_id':c.get('thread-id')} for c in cpus]
   threads={c.get('thread-id') for c in cpus}
   row['distinct_host_vcpu_threads']=len(threads)
   if len(cpus)!=2 or None in threads:raise RuntimeError('Expected two inspectable vCPUs')
   if mode=='multi' and len(threads)!=2:raise RuntimeError('MTTCG did not create two vCPU threads')
   if mode=='single' and len(threads)!=1:raise RuntimeError('Single-thread TCG not observed')
   qmp_request(stream,'cont')
   out,err=proc.communicate(timeout=30)
   row['guest_exit']=proc.returncode
  except Exception as exc:
   row['errors'].append(repr(exc));row['host_timeout']=isinstance(exc,(TimeoutError,subprocess.TimeoutExpired))
   if proc.poll() is None:proc.terminate()
   try:out,err=proc.communicate(timeout=4)
   except subprocess.TimeoutExpired:proc.kill();out,err=proc.communicate()
   row['guest_exit']=proc.returncode
  finally:
   if stream:stream.close()
   if conn:conn.close()
  row['uart']=out.decode(errors='replace');row['wall_seconds_not_performance']=time.monotonic()-start
  numbers={k:int(v,16) for k,v in re.findall(r'^([A-Z0-9_]+)=(0x[0-9a-f]+)$',row['uart'],re.M)}
  row['observed_counters']=numbers
  expected=(not row['errors'] and row['guest_exit']==wanted_exit and marker in row['uart'])
  if case=='positive':
   expected=expected and all(numbers.get(k)==v for k,v in [
      ('CROSS_CPU_VAE1IS_ROUNDS',rounds),('ASID_SWITCH_ROUNDS',rounds),
      ('REMOTE_PERMISSION_FAULTS',8),('ATOMIC_FINAL_COUNT',100000)])
  else:expected=expected and 'ATOMIC_FINAL_COUNT=' not in row['uart']
  row['passed']=bool(expected)
  log=folder/f'{target}-{mode}-{case}.log'
  log.write_text(row['uart']+'\n=== STDERR ===\n'+err.decode(errors='replace'))
  row['log']=str(log.relative_to(ROOT))
 print(f'{target}/{mode}/{case}: guest={row["guest_exit"]} expected={wanted_exit}; host-vCPU-threads={row.get("distinct_host_vcpu_threads")}; {"PASS" if row["passed"] else "FAIL"}',flush=True)
 print(row['uart'],end='',flush=True)
 if row['errors']:print('ERRORS',row['errors'],flush=True)
 return row

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--target',choices=[*TARGETS,'all'],default='all')
 parser.add_argument('--mode',choices=['multi','single','all'],default='all')
 parser.add_argument('--case',choices=[*CASES,'all'],default='all')
 parser.add_argument('--rounds',type=int,default=32)
 a=parser.parse_args()
 if not 1<=a.rounds<=256:parser.error('rounds must be 1..256')
 BUILD.mkdir(parents=True,exist_ok=True)
 label=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
 folder=ROOT/'runs/e06-smp-mmu'/label;folder.mkdir(parents=True)
 report={'at_utc':label,'scope':'two-vCPU self-authored EL1 MMU/ASID/TLBI/permissions/atomic test, no Apple firmware',
         'apple_firmware_executed':False,'live_tss_requested':False,'qemu_sources_modified':False,
         'cases':[],'passed':False,'limitations':[
         'QEMU may invalidate more TLB entries than architecturally required.',
         'No assumption that omitted TLBI necessarily preserves a stale entry.',
         'Single/multithread execution modes share the same emulator code, not independent hardware validation.',
         'No EL0/Apple PAC, device DMA/IOMMU or OS workload.']}
 result_path=folder/'results.json'
 for case in CASES if a.case=='all' else [a.case]:
  image=build(case,a.rounds)
  for target in TARGETS if a.target=='all' else [a.target]:
   for mode in ['multi','single'] if a.mode=='all' else [a.mode]:
    report['cases'].append(run_case(image,case,target,mode,a.rounds,folder))
    report['passed']=all(r['passed'] for r in report['cases'])
    result_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    if not report['passed']:
     print('RESULT',result_path);return 1
 print('RESULT',result_path)
 return 0
if __name__=='__main__':raise SystemExit(main())
