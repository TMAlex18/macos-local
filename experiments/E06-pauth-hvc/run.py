#!/usr/bin/env python3
"""Self-authored PAC instruction and Apple-HVC baseline; no Apple guest code."""
import json,os,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
RT=ROOT/'experiments/E06-platform-contracts';OUT=ROOT/'build/e06-pauth-hvc'
OUT.mkdir(parents=True,exist_ok=True)
flags=['clang','--target=aarch64-none-elf','-march=armv8.3-a','-O2','-ffreestanding','-fno-builtin',
 '-fno-stack-protector','-fno-pic','-fno-pie','-mgeneral-regs-only','-Wall','-Wextra','-I',str(RT)]
objs=[]
for src in [RT/'start.S',RT/'runtime.c',HERE/'pauth.S',HERE/'baseline.c']:
 obj=OUT/(src.name.replace('.','_')+'.o');subprocess.run(flags+['-c',str(src),'-o',str(obj)],check=True);objs.append(str(obj))
subprocess.run(['ld.lld','--build-id=none','-T',str(RT/'link.ld'),*objs,'-o',str(OUT/'baseline.elf')],check=True)
subprocess.run(['llvm-objcopy','-O','binary',str(OUT/'baseline.elf'),str(OUT/'baseline.bin')],check=True)
results=[]
for target,binary,machine in [
 ('headless','build/qemu-vmapple-headless/qemu-system-aarch64','vmapple,gfx-device=none,memory-backend=ram'),
 ('reims','build/qemu-reims-arm16k/qemu-system-aarch64','vmapple,memory-backend=ram')]:
 d=OUT/target;d.mkdir(exist_ok=True)
 for n,s in [('aux.raw',4*1024*1024),('root.raw',32*1024*1024)]:
  f=d/n
  if not f.exists():
   with f.open('wb') as h:h.truncate(s)
 cmd=[str(ROOT/binary),'-object','memory-backend-memfd,id=ram,size=512M,share=on','-M',machine,
 '-m','512M','-smp','1','-cpu','max','-accel','tcg','-nodefaults','-nic','none','-display','none',
 '-nographic','-monitor','none','-serial','stdio','-semihosting-config','enable=on,target=native',
 '-bios',str(OUT/'baseline.bin'),'-drive',f'file={d/"aux.raw"},format=raw,if=pflash,index=0',
 '-drive',f'file={d/"root.raw"},format=raw,if=pflash,index=1']
 p=subprocess.run(cmd,capture_output=True,text=True,timeout=20,env=dict(os.environ,REIMS_VGPU_WINDOW='0'))
 log=ROOT/f'runs/e06-pauth-hvc-{target}-baseline.log';log.write_text(p.stdout+'\n=== STDERR ===\n'+p.stderr)
 passed=p.returncode==0 and 'E06 PAUTH BASELINE PASS' in p.stdout
 print(target,'exit',p.returncode,'PASS' if passed else 'FAIL');print(p.stdout)
 results.append({'target':target,'guest_exit':p.returncode,'passed':passed,'log':str(log.relative_to(ROOT))})
 if not passed:print(p.stderr[-2000:]);break
report={'time':datetime.now(timezone.utc).isoformat(),'scope':'architectural PAC and observed unsupported Apple HVC; not Apple cipher equivalence','cases':results,'passed':len(results)==2 and all(r['passed'] for r in results)}
(ROOT/'runs/e06-pauth-hvc-baseline-results.json').write_text(json.dumps(report,indent=2)+'\n')
sys.exit(0 if report['passed'] else 1)
