#!/usr/bin/env python3
"""Real synthetic ARM guest capacity and tail-I/O checks on owned sparse files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
RT=ROOT/'experiments/E06-platform-contracts'
CASES={'root64_aux32':(64<<30,32<<20,False),'root128_aux64':(128<<30,64<<20,False),
       'independent_small':(32<<20,128<<20,False),'max_sector_count':(0xffffffff*512,1<<20,False),
       'zero_root':(0,1<<20,False),'root_too_large':(1<<41,32<<20,True),
       'aux_too_large':(32<<20,1<<41,True),'root_partial_sector':((32<<20)+1,32<<20,False),
       'aux_partial_sector':(32<<20,(32<<20)+1,False)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('before','after'),default='after')
    parser.add_argument('--cases',default='all');args=parser.parse_args()
    binary=ROOT/'build/qemu-bdif-usb/qemu-system-aarch64'
    digest=hashlib.sha256(binary.read_bytes()).hexdigest()
    build=json.loads((ROOT/'runs/qemu-bdif-usb-build.json').read_text())
    assert digest==build['binary_sha256'],'Actual consumer binary differs from build record'
    out=ROOT/'runs/e05-bdif-block-capacity'/args.phase;out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for name in (list(CASES) if args.cases=='all' else args.cases.split(',')):
        root_bytes,aux_bytes,rejected=CASES[name]
        capacity_only=args.phase=='before' or bool(root_bytes%512 or aux_bytes%512)
        assert args.phase!='before' or not rejected
        with tempfile.TemporaryDirectory(prefix='bdif-capacity-',dir=ROOT/'build') as temporary:
            work=Path(temporary);objects=[]
            for source in (RT/'start.S',RT/'runtime.c',HERE/'guest.c'):
                obj=work/(source.name+'.o')
                command=['clang','--target=aarch64-none-elf','-O2','-ffreestanding','-fno-builtin',
                         '-fno-stack-protector','-fno-pic','-fno-pie','-mgeneral-regs-only','-I',str(RT),
                         '-DTEST_CASE='+('9' if capacity_only else '10'),
                         '-DROOT_BACKEND_SECTORS='+str((root_bytes+511)//512)+'ULL',
                         '-DAUX_BACKEND_SECTORS='+str((aux_bytes+511)//512)+'ULL',
                         '-c',str(source),'-o',str(obj)]
                subprocess.run(command,check=True,capture_output=True);objects.append(str(obj))
            subprocess.run(['ld.lld','--build-id=none','-T',str(RT/'link.ld'),*objects,'-o',str(work/'guest.elf')],check=True)
            subprocess.run(['llvm-objcopy','-O','binary',str(work/'guest.elf'),str(work/'guest.bin')],check=True)
            for disk,size in [('root',root_bytes),('aux',aux_bytes)]:
                with (work/(disk+'.raw')).open('wb') as stream:
                    stream.truncate(size)
                    if size>=544:stream.seek(size-544);stream.write(b'\x5a'*544)
            cmd=[str(binary),'-M','vmapple,memory-backend=ram','-object','memory-backend-memfd,id=ram,size=512M,share=on',
                 '-cpu','max,cntfrq=24000000,x-cntfrq-exact=on','-accel','tcg','-smp','1','-m','512M',
                 '-nodefaults','-nic','none','-display','none','-nographic','-monitor','none','-serial','stdio',
                 '-semihosting-config','enable=on,target=native','-bios',str(work/'guest.bin'),
                 '-drive',f'file={work/"aux.raw"},format=raw,if=pflash,index=0',
                 '-drive',f'file={work/"root.raw"},format=raw,if=pflash,index=1']
            process=subprocess.run(cmd,capture_output=True,text=True,timeout=15,
                env=dict(os.environ,REIMS_VGPU_WINDOW='0',REIMS_VGPU_LOG='warn'))
            (out/(name+'.log')).write_text(process.stdout+'\nSTDERR\n'+process.stderr)
            observed={k:int(v,16) for k,v in re.findall(r'((?:ROOT|AUX)_CAPACITY(?:32|64))=0x([0-9a-f]+)',process.stdout)}
            host_ok=True
            for disk,size,salt in [('root',root_bytes,83),('aux',aux_bytes,57)]:
                path=work/(disk+'.raw');host_ok&=path.stat().st_size==size
                if size>=544:
                    with path.open('rb') as stream:stream.seek(size-544);actual=stream.read(544)
                    expected=b'\x5a'*544 if rejected or capacity_only else b'\x5a'*32+bytes((i*17+salt)&255 for i in range(512))
                    host_ok&=actual==expected
            passed=(process.returncode!=0 and 'vmapple-bdif:' in process.stderr and not observed and host_ok) if rejected else (
                process.returncode==0 and 'BDIF CAPACITY CONTRACT PASS' in process.stdout and host_ok)
            rows.append({'case':name,'passed':bool(passed),'root_backend_bytes':root_bytes,'aux_backend_bytes':aux_bytes,
                         'expected_rejection':rejected,'capacity_only':capacity_only,
                         'logical_root_bytes':((root_bytes+511)//512)*512,
                         'logical_aux_bytes':((aux_bytes+511)//512)*512,'guest_exit':process.returncode,'observed':observed,
                         'host_tail_sentinels_and_sizes_preserved':bool(host_ok),
                         'guest_sha256':hashlib.sha256((work/'guest.bin').read_bytes()).hexdigest(),
                         'log':str((out/(name+'.log')).relative_to(ROOT))})
            print(name,'PASS' if passed else 'BASELINE_MISMATCH' if args.phase=='before' else 'FAIL',observed,flush=True)
    report={'schema':1,'phase':args.phase,'time_utc':datetime.now(timezone.utc).isoformat(),
            'binary':str(binary.relative_to(ROOT)),'binary_sha256':digest,'cases':rows,
            'passed':all(x['passed'] for x in rows),'apple_guest_executed':False,
            'scope':'Self-authored ARM capacity register reads and sparse backend exact-tail/bounds I/O; no Apple/TSS/installation'}
    (out/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    if args.phase=='before':return 0 if any(not x['passed'] and x['observed'] for x in rows) else 1
    return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
