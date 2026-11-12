#!/usr/bin/env python3
"""Experimental software HVC model: tests semantics, not Apple crypto equality."""
from pathlib import Path
import json,os,subprocess,sys
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent;RT=ROOT/'experiments/E06-platform-contracts'
# Optional 'impdef' reruns the same model checks with QEMU's fast impdef PAC;
# the default QARMA5 run and its result files are unchanged.
ALG=sys.argv[1] if len(sys.argv)>1 else 'qarma5'
if ALG not in ('qarma5','impdef'):raise SystemExit('usage: run-model.py [qarma5|impdef]')
SUFFIX='' if ALG=='qarma5' else '-'+ALG
OUT=ROOT/'build/e06-pauth-hvc'/('model'+SUFFIX);OUT.mkdir(parents=True,exist_ok=True)
flags=['clang','--target=aarch64-none-elf','-march=armv8.3-a','-O2','-ffreestanding','-fno-builtin',
 '-fno-stack-protector','-fno-pic','-fno-pie','-mgeneral-regs-only','-Wall','-Wextra','-I',str(RT)]
if ALG=='impdef':flags.append('-DMODEL_ACCEPT_IMPDEF')
objs=[]
for src in [RT/'start.S',RT/'runtime.c',HERE/'pauth.S',HERE/'el0.S',HERE/'model.c']:
 obj=OUT/(src.name.replace('.','_')+'.o');subprocess.run(flags+['-c',str(src),'-o',str(obj)],check=True);objs.append(str(obj))
subprocess.run(['ld.lld','--build-id=none','-T',str(RT/'link.ld'),*objs,'-o',str(OUT/'model.elf')],check=True)
subprocess.run(['llvm-objcopy','-O','binary',str(OUT/'model.elf'),str(OUT/'model.bin')],check=True)
for n,s in [('aux.raw',4*1024*1024),('root.raw',32*1024*1024)]:
 p=OUT/n
 if not p.exists():
  with p.open('wb') as f:f.truncate(s)
cmd=[str(ROOT/'build/qemu-reims-arm16k/qemu-system-aarch64'),'-object','memory-backend-memfd,id=ram,size=512M,share=on',
 '-M','vmapple,memory-backend=ram,x-pac-software-model=on','-m','512M','-smp','2','-cpu',f'max,pauth-{ALG}=on','-accel','tcg',
 '-nodefaults','-nic','none','-display','none','-nographic','-monitor','none','-serial','stdio',
 '-semihosting-config','enable=on,target=native','-bios',str(OUT/'model.bin'),
 '-drive',f'file={OUT/"aux.raw"},format=raw,if=pflash,index=0','-drive',f'file={OUT/"root.raw"},format=raw,if=pflash,index=1']
try:
 p=subprocess.run(cmd,capture_output=True,text=True,timeout=35,env=dict(os.environ,REIMS_VGPU_WINDOW='0'))
 rc,out,err=p.returncode,p.stdout,p.stderr
except subprocess.TimeoutExpired as e:
 rc=None;out=(e.stdout or b'').decode(errors='replace') if isinstance(e.stdout,bytes) else (e.stdout or '')
 err=(e.stderr or b'').decode(errors='replace') if isinstance(e.stderr,bytes) else (e.stderr or '')
log=ROOT/f'runs/e06-pauth-hvc-model{SUFFIX}.log';log.write_text(out+'\n=== STDERR ===\n'+err)
passed=rc==0 and 'E06 SOFTWARE PAC MODEL SEMANTICS PASS' in out
print('model guest_exit=',rc,'PASS' if passed else 'FAIL');print(out)
if not passed:print(err[-3500:])
report={'time':datetime.now(timezone.utc).isoformat(),'passed':passed,'guest_exit':rc,'vcpus':2,
 'model':f'opt-in HMAC-SHA256 key-state reference; CPU PAC explicitly {ALG.upper()}',
 'pac_algorithm':ALG.upper(), 'baseline_default_max_is_noncryptographic':True,
 'apple_bitwise_crypto_equivalence':False,'apple_firmware_or_kernel_executed':False,
 'checks':['HVC initial/defaults/rekey','PAC rejection after key change','EL0/EL1 key diversifier transitions','EL0 HVC denial',
 'unknown/invalid call no mutation','two CPU isolation and defaults','CPU_OFF/ON state reset'],
 'log':str(log.relative_to(ROOT)),'command':cmd}
(ROOT/f'runs/e06-pauth-hvc-model{SUFFIX}-results.json').write_text(json.dumps(report,indent=2)+'\n')
sys.exit(0 if passed else 1)
