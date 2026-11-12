#!/usr/bin/env python3
"""Negative configuration controls for the opt-in HVC model; no OS images."""
import json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
report=json.loads((ROOT/'runs/e06-pauth-hvc-model-results.json').read_text())
results=[]
for name,kind,value,marker in [
 ('no-pauth','cpu','cortex-a72','requires architectural PAuth'),
 ('migration-refused','arg','-only-migratable','no migration state format'),
 ('model-off','machine','off','PREINIT_MUST_FAIL'),
]:
 cmd=report['command'].copy()
 if kind=='cpu':cmd[cmd.index('-cpu')+1]=value
 elif kind=='arg':cmd.append(value)
 else:cmd[cmd.index('-M')+1]=cmd[cmd.index('-M')+1].replace('x-pac-software-model=on','x-pac-software-model=off')
 p=subprocess.run(cmd,capture_output=True,text=True,timeout=15,env=dict(os.environ,REIMS_VGPU_WINDOW='0'))
 combined=p.stdout+'\n=== STDERR ===\n'+p.stderr
 log=ROOT/f'runs/e06-pauth-model-{name}.log';log.write_text(combined)
 passed=p.returncode==1 and marker in combined
 results.append({'case':name,'passed':passed,'process_exit':p.returncode,'expected_exit':1,'log':str(log.relative_to(ROOT))})
 print(name,'expected refusal',passed,'exit',p.returncode)
 if not passed:print(combined[-2500:]);break
(ROOT/'runs/e06-pauth-model-guard-results.json').write_text(json.dumps(results,indent=2)+'\n')
sys.exit(0 if len(results)==3 and all(r['passed'] for r in results) else 1)
