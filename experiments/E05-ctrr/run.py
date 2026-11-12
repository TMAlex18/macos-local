#!/usr/bin/env python3
"""Run a self-authored EL1 guest against the opt-in QEMU CTRR model.

No Apple firmware, TSS request, signing material or network connection is
used. This validates actual MMIO, CPU/DMA write errors, protected-code TB reuse,
shared RAM, self-modifying code and reset/unrealize behavior.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import re
import socket
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUNTIME = ROOT / "experiments/E06-platform-contracts"
BUILD = ROOT / "build/e05-ctrr"
sys.path.insert(0,str(ROOT/"experiments/E05-signed-restore"))
from rsp_readonly import RSP


def build_guest(baseline=False):
    BUILD.mkdir(parents=True, exist_ok=True)
    objects = []
    extra = ["-DCTRR_BASELINE=1"] if baseline else []
    common = ["clang", "--target=aarch64-none-elf", "-O2", "-ffreestanding",
              "-fno-builtin", "-fno-stack-protector", "-fno-pic", "-fno-pie",
              "-mgeneral-regs-only", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
    for directory, name in ((RUNTIME, "start.S"), (RUNTIME, "runtime.c"), (HERE, "guest.c")):
        obj = BUILD / (name.replace('.', '_') + ('-baseline' if baseline else '') + '.o')
        subprocess.run([*common, *extra, '-c', str(directory / name), '-o', str(obj)], check=True,
                       capture_output=True, text=True)
        objects.append(str(obj))
    elf = BUILD / ('guest-baseline.elf' if baseline else 'guest.elf')
    image = BUILD / ('guest-baseline.bin' if baseline else 'guest.bin')
    subprocess.run(['ld.lld', '--build-id=none', '-T', str(RUNTIME / 'link.ld'),
                    *objects, '-o', str(elf)], check=True)
    subprocess.run(['llvm-objcopy', '-O', 'binary', str(elf), str(image)], check=True)
    return image


class QMP:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX); self.sock.settimeout(5); self.sock.connect(str(path))
        self.stream = self.sock.makefile('rb'); json.loads(self.stream.readline())
        self.request('qmp_capabilities')
    def request(self, command, arguments=None):
        self.sock.sendall((json.dumps({'execute':command, **({'arguments':arguments} if arguments is not None else {})})+'\n').encode())
        while True:
            row = json.loads(self.stream.readline())
            if 'return' in row: return row['return']
            if 'error' in row: raise RuntimeError(row['error'])
    def hmp(self, command):
        return self.request('human-monitor-command', {'command-line':command})
    def close(self):
        self.stream.close(); self.sock.close()


class FixtureRSP(RSP):
    """Writes only three fixed addresses in this owned self-authored fixture."""
    def write_fixture(self, address, data):
        if (address, len(data)) not in ((0x72000000,4),(0x72000100,8),(0x72830008,8)):
            raise ValueError('Not a fixture control/data address')
        command=f'M{address:x},{len(data):x}:{data.hex()}'.encode()
        self.sock.sendall(b'$'+command+b'#'+f'{sum(command)&255:02x}'.encode())
        ack=self.byte()
        while ack==ord('$'):
            self._discard_packet_after_start();ack=self.byte()
        if ack!=ord('+'):raise RuntimeError('Fixture write command ACK missing')
        return self.receive()


def cache_evidence(path):
    text=path.read_text()
    rows=re.findall(r'Trace (\d+): (0x[0-9a-f]+) \[[^/]+/([0-9a-f]+)/[0-9a-f]+/([0-9a-f]+)\]',text)
    entries=[{'cpu':int(cpu),'tb_host_address':tb,'cflags':int(flags,16)}
             for cpu,tb,pc,flags in rows if int(pc,16)==0x72000000]
    hosts=Counter(row['tb_host_address'] for row in entries)
    return {'code_guest_address':'0x72000000','entry_executions':len(entries),
            'unique_entry_tb_host_addresses':len(hosts),
            'entry_executions_per_tb':dict(hosts),
            'forced_single_instruction_entries':sum((row['cflags']&0x1ff)==1 for row in entries),
            'trace':str(path.relative_to(ROOT)),
            'scope':'Actual QEMU exec/nochain/in_asm trace; host JIT TB reuse and CF_COUNT_MASK=0x1ff. Not a timing threshold.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',action='store_true',help='Short code/fault/DMA fixture, no lifecycle controls')
    parser.add_argument('--expect-cache',choices=('cached','uncached'),default='cached')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'runs/e05-ctrr-qemu-guest')
    args=parser.parse_args()
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    image=build_guest(args.baseline)
    binary=ROOT/'build/qemu-bdif-usb/qemu-system-aarch64'
    report={'passed':False,'baseline':args.baseline,'expect_cache':args.expect_cache,
            'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'guest_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
            'ctrr_model_opt_in':True,'apple_firmware_executed':False,'new_tss_request':False,
            'scope':'Self-authored EL1 guest on the actual research binary. Protected-code JIT reuse, CPU fault, normal DMA, shared RAM, SMC and reset/unrealize contracts only; no Apple execution or hardware-equivalence claim.',
            'privileged_debug_scope':'Fixture debug writes use standard administrator ROMD semantics and are outside the normal guest CPU/DMA protection boundary.'}
    qmp=rsp=None
    with tempfile.TemporaryDirectory(prefix='ctrr-cache-',dir=ROOT/'build') as name:
        work=Path(name)
        for filename,size in [('aux.raw',4<<20),('root.raw',32<<20)]:
            with (work/filename).open('wb') as stream:stream.truncate(size)
        command=[str(binary),'-object','memory-backend-memfd,id=ram,size=512M,share=on',
                 '-M','vmapple,memory-backend=ram,x-ctrr-model=on','-cpu','max,cntfrq=24000000,x-cntfrq-exact=on',
                 '-accel','tcg','-m','512M','-smp','1','-nodefaults','-nic','none','-display','none','-nographic',
                 '-monitor','none','-serial',f'file:{out/"uart.log"}','-S',
                 '-semihosting-config','enable=on,target=native','-bios',str(image),
                 '-drive',f'file={work/"aux.raw"},format=raw,if=pflash,index=0',
                 '-drive',f'file={work/"root.raw"},format=raw,if=pflash,index=1',
                 '-qmp',f'unix:{work/"qmp.sock"},server=on,wait=off',
                 '-gdb',f'unix:{work/"gdb.sock"},server=on,wait=off',
                 '-d','exec,nochain,in_asm','-dfilter','0x72000000+0x10000','-D',str(out/'tcg.log')]
        report['command']=command
        with (out/'stderr.log').open('w') as error:
            proc=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=error,
                env=dict(os.environ,REIMS_VGPU_WINDOW='0',REIMS_VGPU_LOG='warn'))
            report['qemu_pid']=proc.pid
            try:
                start=time.monotonic()
                while not (work/'qmp.sock').exists():
                    if proc.poll() is not None or time.monotonic()-start>15:raise RuntimeError('QEMU endpoint startup failed')
                    time.sleep(.01)
                qmp=QMP(work/'qmp.sock');rsp=FixtureRSP(work/'gdb.sock',timeout=5)
                rsp.command('qSupported');rsp.command('?')
                initial_blocks=qmp.hmp('info ramblock');(out/'ramblock-before.txt').write_text(initial_blocks)
                children=qmp.request('qom-list',{'path':'/machine/unattached'})
                ctrr=[x['name'] for x in children if x.get('type')=='child<vmapple-ctrr>']
                if len(ctrr)!=1:raise RuntimeError('Unique CTRR device not found in owned VM')
                path='/machine/unattached/'+ctrr[0];report['ctrr_qom_path']=path
                rsp.send('c')
                if not args.baseline:
                    for phase in (1,2,3):
                        marker=f'CTRR_HOST_CHECKPOINT=0x{phase:016x}'
                        deadline=time.monotonic()+30
                        while marker not in (out/'uart.log').read_text():
                            if proc.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Guest checkpoint missing: '+marker)
                            time.sleep(.01)
                        qmp.request('stop');stop=rsp.receive()
                        if not stop.startswith('T02'):raise RuntimeError('Owned pause packet missing')
                        rsp.select_thread('01')
                        (out/f'jit-phase{phase}.txt').write_text(qmp.hmp('info jit'))
                        (out/f'mtree-phase{phase}.txt').write_text(qmp.hmp('info mtree -f'))
                        blocks=qmp.hmp('info ramblock');(out/f'ramblock-phase{phase}.txt').write_text(blocks)
                        if blocks!=initial_blocks:raise RuntimeError('Range enable allocated/replaced a RAMBlock')
                        if phase==1:
                            if rsp.write_fixture(0x72000100,(0xb7b7b7b7b7b7b7b7).to_bytes(8,'little'))!='OK':raise RuntimeError('Privileged ROMD data write failed')
                            if rsp.write_fixture(0x72000000,(0xd2800000|(44<<5)).to_bytes(4,'little'))!='OK':raise RuntimeError('Privileged ROMD code write failed')
                            report['privileged_debug_write_return']='OK'
                        elif phase==2:
                            qmp.request('qom-set',{'path':path,'property':'realized','value':False})
                            if rsp.read_memory(0x72000100,8)!=(0xb7b7b7b7b7b7b7b7).to_bytes(8,'little'):raise RuntimeError('ROMD/backing RAM identity diverged')
                            if rsp.write_fixture(0x72000000,(0xd2800000|(55<<5)).to_bytes(4,'little'))!='OK':raise RuntimeError('Unrealized backing RAM write failed')
                            qmp.request('qom-set',{'path':path,'property':'realized','value':True})
                            report['unrealize_and_rerealize_completed']=True
                        else:
                            qmp.request('system_reset');report['system_reset_requested']=True
                        if phase!=3:
                            if rsp.write_fixture(0x72830008,phase.to_bytes(8,'little'))!='OK':raise RuntimeError('Fixture mailbox write failed')
                        rsp.send('c')
                proc.wait(timeout=20)
                report['guest_exit']=proc.returncode
                uart=(out/'uart.log').read_text();report['uart']=uart
                expected='CTRR_BASELINE_PASS' if args.baseline else 'CTRR_GUEST_PASS'
                if proc.returncode!=0 or expected not in uart or 'FAIL:' in uart:raise RuntimeError('Guest contract failed')
                evidence=cache_evidence(out/'tcg.log');report['jit_cache']=evidence
                expected_calls=sum(int(value,16) for value in re.findall(r'CTRR_(?:BASELINE|PHASE[12])_CALLS=0x([0-9a-f]+)',uart))
                if evidence['entry_executions']!=expected_calls:raise RuntimeError('JIT execution log does not cover guest call count')
                if args.expect_cache=='cached':
                    if evidence['forced_single_instruction_entries']!=0:raise RuntimeError('Protected code still forced into one-shot TBs')
                    if max(evidence['entry_executions_per_tb'].values(),default=0)<64:raise RuntimeError('No demonstrated cached TB reuse across the loop')
                elif evidence['forced_single_instruction_entries']<64 or evidence['unique_entry_tb_host_addresses']<64:
                    raise RuntimeError('Expected uncached baseline was not reproduced')
                report.update(passed=True,normal_cpu_write_abort_verified=True,normal_dma_error_and_readback_verified=True,
                              same_ramblock_list_across_range_enable=(True if not args.baseline else None))
            except BaseException as exc:
                report['error']=repr(exc)
            finally:
                if rsp is not None:rsp.close()
                if qmp is not None:qmp.close()
                if proc.poll() is None:
                    proc.terminate()
                    try:proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
                report['qemu_reaped']=proc.poll() is not None
                report.setdefault('guest_exit',proc.returncode)
                if (out/'uart.log').exists():report['uart']=(out/'uart.log').read_text()
    (out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('CTRR_QEMU_GUEST', 'PASS' if report['passed'] else 'FAIL', 'exit',report['guest_exit'])
    print('REPORT',out/'results.json')
    if 'error' in report:print(report['error']);print(report.get('uart',''))
    elif 'jit_cache' in report:print(json.dumps(report['jit_cache'],indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
