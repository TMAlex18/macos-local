#!/usr/bin/env python3
"""Real synthetic EL0 two-site timeout guard checks; no Apple inputs."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
COMMON=ROOT/'experiments/E06-platform-contracts'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',nargs='+',choices=('off','matching','wrong-uuid','wrong-instruction','wrong-context'))
    args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    qemu=ROOT/'build/qemu-bdif-usb/qemu-system-aarch64'
    manifest=json.loads((ROOT/'runs/qemu-bdif-usb-build.json').read_text())
    assert sha(qemu)==manifest['binary_sha256']
    assert 'patches/qemu-platform/0031-arm-timeout-runtime-slide.patch' in manifest['patches']
    record={'translation_granule_bytes':4096,'real_macos_16k_guard_verified':False,'binary':str(qemu),'binary_sha256':sha(qemu),'scope':__doc__,'commands':[],'cases':[],'passed':False}
    (out/'start.S').write_text((COMMON/'start.S').read_text().replace('0x71000000','0x41000000'))
    (out/'runtime.c').write_text((COMMON/'runtime.c').read_text().replace('0x20010000','0x09000000').replace('0x20010018','0x09000018'))
    (out/'link.ld').write_text('ENTRY(_start)\nSECTIONS { . = 0x40080000; .text : { *(.text.boot) *(.vectors) *(.text*) } .rodata : { *(.rodata*) } .test_sites : { *(.test_sites) } .data : { *(.data*) } . = ALIGN(4096); .bss (NOLOAD) : { __bss_start = .; *(.bss*) *(COMMON) . = ALIGN(8); __bss_end = .; } /DISCARD/ : { *(.comment) *(.eh_frame*) *(.note*) } }\n')
    objects=[]
    for source in (out/'start.S',out/'runtime.c',HERE/'guest.c',HERE/'user.S'):
        obj=out/(source.name+'.o');objects.append(str(obj))
        cmd=['clang','--target=aarch64-none-elf','-march=armv8-a','-O2','-ffreestanding','-fno-builtin','-fno-stack-protector',*(['-mgeneral-regs-only'] if source.suffix=='.c' else []),'-I',str(COMMON),'-c',str(source),'-o',str(obj)]
        record['commands'].append(cmd);subprocess.run(cmd,check=True,capture_output=True,text=True)
    guest=out/'guest.elf'
    cmd=['ld.lld','--build-id=none','-T',str(out/'link.ld'),*objects,'-o',str(guest)]
    record['commands'].append(cmd);subprocess.run(cmd,check=True,capture_output=True,text=True)
    raw=out/'sites.bin'
    subprocess.run(['llvm-objcopy','--only-section=.test_sites','-O','binary',str(guest),str(raw)],check=True)
    code=raw.read_bytes();assert len(code)==128
    contract={'schema_version':1,'image':{'text_segment_size':0x400000,'uuid_bytes_hex':bytes(range(1,17)).hex(),'mach_o':{'lc_uuid_payload_image_offset':40}},'sites':[]}
    for i,offset in enumerate((0x201000,0x201100)):
        window=code[i*64:(i+1)*64]
        contract['sites'].append({'image_offset':offset,'original_instruction_u32':struct.unpack_from('<I',window)[0],
                                  'context':{'image_offset':offset,'length':64,'original_bytes_hex':window.hex()}})
    try:
        for case in (args.case or ('off','matching','wrong-uuid','wrong-instruction','wrong-context')):
            cfg=json.loads(json.dumps(contract))
            if case=='wrong-uuid':cfg['image']['uuid_bytes_hex']='00'*16
            if case=='wrong-instruction':
                for site in cfg['sites']:
                    site['original_instruction_u32']^=4
                    raw=bytearray.fromhex(site['context']['original_bytes_hex']);struct.pack_into('<I',raw,0,site['original_instruction_u32'])
                    site['context']['original_bytes_hex']=raw.hex()
            if case=='wrong-context':
                for site in cfg['sites']:
                    raw=bytearray.fromhex(site['context']['original_bytes_hex']);raw[-1]^=1
                    site['context']['original_bytes_hex']=raw.hex()
            config=out/(case+'.json');config.write_text(json.dumps(cfg))
            with tempfile.TemporaryDirectory(prefix='timeout-diag-') as temporary:
                endpoint=Path(temporary)/'qmp';qtpath=Path(temporary)/'qtest';uart=out/(case+'.uart');stderr=(out/(case+'.stderr')).open('wb');trace=out/(case+'.trace')
                cpu='max'+(f',x-webkit-timeout-diagnostic={config}' if case!='off' else '')
                cmd=[str(qemu),'-machine','virt','-cpu',cpu,'-accel','tcg,thread=multi','-smp','2','-m','128M','-nodefaults','-nic','none','-display','none','-monitor','none','-serial',f'file:{uart}','-kernel',str(guest),'-semihosting-config','enable=on,target=native','-S','-qmp',f'unix:{endpoint},server=on,wait=off','-qtest',f'unix:{qtpath},server=on,wait=off','-qtest-log','/dev/null','-trace',f'enable=pl011_read,file={trace}']
                row={'case':case,'command':cmd,'phases':[]};record['cases'].append(row)
                proc=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=stderr);sock=None;stream=None;qt=None;qf=None
                try:
                    deadline=time.monotonic()+10
                    while not endpoint.exists() or not qtpath.exists():
                        if proc.poll() is not None or time.monotonic()>deadline:raise RuntimeError('QMP startup failed')
                        time.sleep(.01)
                    sock=socket.socket(socket.AF_UNIX);sock.settimeout(10);sock.connect(str(endpoint));stream=sock.makefile('rb');json.loads(stream.readline())
                    qt=socket.socket(socket.AF_UNIX);qt.settimeout(10);qt.connect(str(qtpath));qf=qt.makefile('rb')
                    def request(command,arguments=None,error=False):
                        msg={'execute':command}
                        if arguments is not None:msg['arguments']=arguments
                        sock.sendall((json.dumps(msg)+'\n').encode())
                        while True:
                            reply=json.loads(stream.readline())
                            if 'return' in reply:
                                assert not error,command
                                return reply['return']
                            if 'error' in reply:
                                if error:return reply['error']
                                raise RuntimeError(reply['error'])
                    def phase_write(value):
                        qt.sendall(f'write 0x40300000 8 0x{struct.pack("<Q",value).hex()}\n'.encode())
                        while True:
                            line=qf.readline().decode()
                            if line.startswith('IRQ'):continue
                            assert line.startswith('OK'),line
                            break
                    def wait_text(marker,offset=0):
                        deadline=time.monotonic()+10
                        while True:
                            value=uart.read_text() if uart.exists() else ''
                            if 'FAIL:' in value[offset:]:raise RuntimeError(value[offset:])
                            if marker in value[offset:]:return value[offset:]
                            if proc.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Guest progress deadline: '+value[-3000:])
                            time.sleep(.01)
                    def counters():
                        return [{name:request('qom-get',{'path':cpu['qom-path'],'property':'x-webkit-timeout-'+name})
                                 for name in ('armed','status','site0-hits','site1-hits','rejections','epoch')} for cpu in cpus]
                    def arm(enabled):
                        assert request('query-status')['running'] is False
                        before=counters()
                        for cpu in cpus:request('qom-set',{'path':cpu['qom-path'],'property':'x-webkit-timeout-armed','value':enabled})
                        after=counters()
                        assert all(a['armed']==enabled and a['epoch']==b['epoch']+1 for a,b in zip(after,before))
                    expected_hits=[[0,0],[0,0]]
                    expected_rejections=[0,0]
                    def phase(number,active):
                        offset=len(uart.read_text());phase_write(number);request('cont')
                        text=wait_text('PHASE_DONE\n',offset);request('stop')
                        fields={};observed=[]
                        for line in text.splitlines():
                            match=re.fullmatch(r'(PHASE|CPU|SITE0|SITE1|ALIAS0|ALIAS1|REPEAT0|REPEAT1|OTHER)=0x([0-9a-f]+)',line)
                            if match:fields[match[1]]=int(match[2],16)
                            if match and match[1]=='OTHER':observed.append(fields);fields={}
                        assert len(observed)==2 and {x['CPU'] for x in observed}=={0,1},text
                        armed=counters()[0]['armed']
                        for value in observed:
                            ordinary=0x4008000000000000
                            expected=0x403e000000000000 if active else ordinary
                            if number in (3,6,7,8,13) or (number==5 and value['CPU']==1):expected=ordinary
                            if number==9:expected=0xffff
                            alias_expected=ordinary if number in (15,16) else expected
                            assert value['SITE0']==value['SITE1']==expected,text
                            assert value['ALIAS0']==value['ALIAS1']==alias_expected,text
                            assert value['REPEAT0']==value['REPEAT1']==(expected if number==14 else alias_expected),text
                            assert value['OTHER']==(0xffff if number==9 else ordinary),text
                            for key,site in (('SITE0',0),('SITE1',1),('ALIAS0',0),('ALIAS1',1),('REPEAT0',0),('REPEAT1',1)):
                                if value[key]==0x403e000000000000:
                                    expected_hits[value['CPU']][site]+=1
                                elif number!=9 and case not in ('off','wrong-instruction') and armed:
                                    expected_rejections[value['CPU']]+=1
                        actual_counts=counters()
                        for index,counts in enumerate(actual_counts):
                            assert [counts['site0-hits'],counts['site1-hits']]==expected_hits[index]
                            assert counts['rejections']==expected_rejections[index]
                        row['phases'].append({'phase':number,'observed':observed,'counters':actual_counts,'passed':True})
                    request('qmp_capabilities');cpus=request('query-cpus-fast')
                    assert len(cpus)==2 and len({x['thread-id'] for x in cpus})==2
                    request('cont');wait_text('TIMEOUT_DIAG_READY\n');request('stop')
                    for cpu in cpus:
                        error=request('qom-set',{'path':cpu['qom-path'],'property':'x-webkit-timeout-site0-hits','value':99},error=True)
                        assert 'writ' in error['desc'].lower()
                    phase(1,False)  # Hot TBs are first generated while unarmed.
                    if case=='off':
                        request('qom-set',{'path':cpus[0]['qom-path'],'property':'x-webkit-timeout-armed','value':True},error=True)
                        assert all(x['status']=='off' and not x['armed'] for x in counters())
                    else:
                        arm(True)
                        error=request('migrate',{'uri':'file:'+str(out/(case+'.migration'))},error=True)
                        assert 'timeout diagnostic profile' in error['desc'] and not (out/(case+'.migration')).exists()
                        phase(2,case=='matching')
                        request('cont');before=counters()
                        error=request('qom-set',{'path':cpus[0]['qom-path'],'property':'x-webkit-timeout-armed','value':False},error=True)
                        assert 'paused' in error['desc'];request('stop');assert counters()==before
                    if case=='matching':
                        for number in (14,15,16,17,3,4,5,6,7,8,9,12,13):phase(number,True)
                        reads=re.findall(r'pl011_read addr 0x000\b',trace.read_text())
                        assert not reads,'Diagnostic guard invoked MMIO data callback'
                        phase(10,True)
                        assert len(re.findall(r'pl011_read addr 0x000\b',trace.read_text()))==1,'MMIO observation positive control failed'
                        row['mmio_guard_and_positive_control_passed']=True
                        arm(False);phase(11,False)
                        arm(True)
                        old=len(uart.read_text());request('system_reset');request('cont');wait_text('TIMEOUT_DIAG_READY\n',old);request('stop')
                        assert all(not x['armed'] and x['site0-hits']==x['site1-hits']==0 for x in counters())
                        expected_hits[:]=[[0,0],[0,0]];expected_rejections[:]=[0,0]
                        phase(14,False);row['reset_revocation_passed']=True
                        arm(True);phase(2,True)
                        row['a_then_b_and_b_then_a_physical_tb_alias_passed']=True
                    phase_write(255);request('cont');proc.wait(timeout=10)
                    text=uart.read_text();row['uart']=text
                    assert 'TIMEOUT_DIAG_GUEST_DONE' in text and 'FAIL:' not in text,text
                    assert proc.returncode==0
                    row['passed']=True
                finally:
                    if proc.poll() is None:proc.kill();proc.wait()
                    row.update(exit_code=proc.returncode,reaped=proc.poll() is not None)
                    if stream:stream.close()
                    if sock:sock.close()
                    if qf:qf.close()
                    if qt:qt.close()
                    stderr.close()
                    (out/'results.json').write_text(json.dumps(record,indent=2)+'\n')
            print(case,row['passed'],flush=True)
        record['passed']=True
    finally:
        for path in out.glob('*.o'):path.unlink()
        (out/'results.json').write_text(json.dumps(record,indent=2)+'\n')


if __name__=='__main__':main()
