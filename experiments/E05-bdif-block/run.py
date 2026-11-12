#!/usr/bin/env python3
"""BDIF read/write/flush correctness with synthetic guest-owned raw disks.

Post-exit host read checks backing bytes, bounds and original length.
No real user disk, Apple firmware, nonce or TSS is used.
"""
import argparse,json,os,subprocess,tempfile
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
RT=ROOT/'experiments/E06-platform-contracts'
CASES={'aux-write':0,'root-write':1,'readonly':2,'end-exact':3,'cross-end':4,
       'invalid-data':5,'invalid-flags':6,'invalid-return':7,'invalid-descriptor':8}
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--baseline',action='store_true');parser.add_argument('--cases',default='all');a=parser.parse_args()
    q=ROOT/'build/qemu-bdif-usb'/('qemu-system-aarch64.before-block-write' if a.baseline else 'qemu-system-aarch64')
    results=[]
    for name in (list(CASES) if a.cases=='all' else a.cases.split(',')):
      case=CASES[name];size=32*1024*1024
      with tempfile.TemporaryDirectory(prefix='blk-',dir=ROOT/'build') as temp:
        w=Path(temp);objs=[]
        for src in ('start.S','runtime.c','guest.c'):
          source=(HERE if src=='guest.c' else RT)/src;obj=w/(src+'.o')
          subprocess.run(['clang','--target=aarch64-none-elf','-O2','-ffreestanding',
            '-fno-builtin','-fno-stack-protector','-fno-pic','-fno-pie','-mgeneral-regs-only',
            '-I',str(RT),f'-DTEST_CASE={case}','-c',str(source),'-o',str(obj)],check=True,capture_output=True)
          objs.append(str(obj))
        subprocess.run(['ld.lld','-T',str(RT/'link.ld'),*objs,'-o',str(w/'guest.elf')],check=True)
        subprocess.run(['llvm-objcopy','-O','binary',str(w/'guest.elf'),str(w/'guest.bin')],check=True)
        for filename in ('aux.raw','root.raw'):
          with (w/filename).open('wb') as f:f.truncate(size)
        target=w/('root.raw' if case==1 else 'aux.raw')
        start=512 if case==1 else size-512 if case in (3,4) else 16*1024*1024
        length=1024 if case in (1,4) else 512
        lo=start-32;hi=min(size,start+length+32)
        with target.open('r+b') as f:f.seek(lo);f.write(b'\x5a'*(hi-lo))
        aux=f'file={w/"aux.raw"},format=raw,if=pflash,index=0'+(',readonly=on' if case==2 else '')
        args=[str(q),'-M','vmapple,memory-backend=ram','-object','memory-backend-memfd,id=ram,size=512M,share=on',
          '-cpu','max,cntfrq=24000000,x-cntfrq-exact=on','-accel','tcg','-smp','1','-m','512M',
          '-nodefaults','-nic','none','-display','none','-nographic','-monitor','none','-serial','stdio',
          '-semihosting-config','enable=on,target=native','-bios',str(w/'guest.bin'),'-drive',aux,
          '-drive',f'file={w/"root.raw"},format=raw,if=pflash,index=1']
        try:p=subprocess.run(args,text=True,capture_output=True,timeout=10,env=dict(os.environ,REIMS_VGPU_WINDOW='0',REIMS_VGPU_LOG='warn'))
        except subprocess.TimeoutExpired:raise SystemExit(name+': host timeout')
        with target.open('rb') as f:f.seek(lo);actual=f.read(hi-lo)
        expected=bytearray(b'\x5a'*(hi-lo))
        if case in (0,1,3):expected[32:32+length]=bytes((i*17+83)&255 for i in range(length))
        data_ok=actual==expected;size_ok=target.stat().st_size==size
        ok=p.returncode==0 and 'BDIF BLOCK CONTRACT PASS' in p.stdout and data_ok and size_ok
        item={'case':name,'passed':ok,'guest_exit':p.returncode,'host_data_correct':data_ok,'host_file_length_preserved':size_ok,'uart':p.stdout}
        results.append(item)
        out=ROOT/f'runs/bdif-block-{name}{"-baseline" if a.baseline else ""}.log';out.write_text(p.stdout+'\n=== STDERR ===\n'+p.stderr)
        print(name,'PASS' if ok else 'FAIL','rc',p.returncode,'host bytes',data_ok,'size',size_ok,flush=True)
    report={'time':datetime.now(timezone.utc).isoformat(),'binary':str(q.relative_to(ROOT)),'baseline':a.baseline,'cases':results,'passed':all(x['passed'] for x in results)}
    (ROOT/f'runs/bdif-block-{ "baseline" if a.baseline else "final" }-results.json').write_text(json.dumps(report,indent=2)+'\n')
    raise SystemExit(0 if report['passed'] else 1)
if __name__=='__main__':main()
