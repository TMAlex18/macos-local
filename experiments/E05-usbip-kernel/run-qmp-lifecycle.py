#!/usr/bin/env python3
"""Actual owner-QMP shutdown comparison on diskless generic virt, no Apple/GPU."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time

from firmware_session import FirmwareSession,ROOT

OFF_SOURCE=''' .text
.globl _start
_start:
    ldr x1, =2000000
1:  subs x1, x1, #1
    b.ne 1b
    ldr x0, =0x84000008
    hvc #0
2:  b 2b
'''


def run_case(binary,guest,out,natural,observe_shutdown=False):
    fw=FirmwareSession(out)
    fw.temp=tempfile.TemporaryDirectory(prefix='qmp-lifecycle-',dir=ROOT/'build')
    endpoint=Path(fw.temp.name)/'qmp.sock'
    result={'case':('guest-psci-off-query' if observe_shutdown else 'guest-psci-off')
                   if natural else 'host-qmp-quit','errors':[]}
    try:
        fw.args=[str(binary),'-machine','virt','-cpu','max','-accel','tcg','-smp','1','-m','128M',
            '-nodefaults','-nic','none','-display','none','-monitor','none','-serial','none',
            '-kernel',str(guest),'-S','-qmp',f'unix:{endpoint},server=on,wait=off']
        result['command']=fw.args
        fw.err=(out/'qemu-stderr.log').open('wb')
        fw.proc=subprocess.Popen(fw.args,stdout=subprocess.DEVNULL,stderr=fw.err,
            start_new_session=True,preexec_fn=fw._child_parent_death_signal)
        result['qemu_pid']=fw.proc.pid
        deadline=time.monotonic()+5
        while not endpoint.exists():
            if fw.proc.poll() is not None:raise RuntimeError('QEMU exited before QMP readiness')
            if time.monotonic()>deadline:raise TimeoutError('QMP readiness')
            time.sleep(.01)
        fw.qmp=socket.socket(socket.AF_UNIX);fw.qmp.settimeout(2);fw.qmp.connect(str(endpoint))
        fw.qf=fw.qmp.makefile('rb')
        json.loads(fw.qf.readline());fw.request('qmp_capabilities')
        result['initial_status']=fw.request('query-status')
        if natural:
            fw.request('cont')
            if observe_shutdown:
                # Exercise the real request/event/disconnect boundary too.
                # Whether process exit lags socket close is an observation,
                # not an assumed property of this short self-authored guest.
                result['shutdown_queries']=0
                deadline=time.monotonic()+5
                while fw.proc.poll() is None:
                    if time.monotonic()>deadline:raise TimeoutError('Guest shutdown')
                    try:
                        fw.request('query-status')
                        result['shutdown_queries']+=1
                    except (EOFError,ConnectionError) as error:
                        result['request_disconnect']={
                            'type':type(error).__name__,'detail':str(error),
                            'process_still_running':fw.proc.poll() is None}
                        break
            else:
                # No subsequent QMP request: close() must recover the tail event.
                fw.proc.wait(timeout=5)
    except Exception as error:
        result['errors'].append(f'{type(error).__name__}: {error}')
    finally:
        fw.close()
        result.update(qemu_exit_code=fw.proc.returncode if fw.proc else None,
            qemu_reaped=bool(fw.proc and fw.proc.poll() is not None),
            endpoint_removed=not endpoint.exists(),qmp_lifecycle=fw.lifecycle_metadata())
    lifecycle=result['qmp_lifecycle']
    event=lifecycle['last_shutdown_or_reset_event'] or {}
    result['checks']={'zero_reaped_exit':result['qemu_exit_code']==0 and result['qemu_reaped'],
        'endpoint_removed':result['endpoint_removed'],
        'expected_origin':event.get('data',{})=={'guest':natural,
            'reason':'guest-shutdown' if natural else 'host-qmp-quit'},
        'expected_guest_shutdown_classification':lifecycle['guest_shutdown_observed'] is natural,
        'expected_teardown':lifecycle['teardown']==('already_exited_or_not_started' if natural else 'qmp_quit'),
        # QEMU may reset the host-quit socket after its event was consumed;
        # preserve that read_error, without claiming the tail was complete.
        'natural_tail_reached_eof':not natural or lifecycle['tail_drain']['state']=='eof',
        'tail_bounded':lifecycle['tail_drain']['elapsed_seconds']<.35,
        'tail_sent_no_commands':lifecycle['tail_drain']['commands_sent']==0,
        'guest_event_recovered_at_expected_boundary':not natural or
            event.get('receive_phase')==('request' if observe_shutdown else 'post_exit_drain'),
        'natural_exit_sent_no_host_quit':not natural or lifecycle['host_quit_requested'] is False}
    result['passed']=not result['errors'] and all(result['checks'].values())
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]{1,60}',args.label):parser.error('Invalid label')
    out=ROOT/'runs/e05-qmp-lifecycle'/args.label
    out.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((ROOT/'runs/qemu-bdif-usb-build.json').read_text())
    binary=ROOT/manifest['binary']
    with binary.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=manifest['binary_sha256']:raise RuntimeError('Canonical executable differs from manifest')
    result={'binary':str(binary),'binary_sha256':digest,
        'session_source':'experiments/E05-usbip-kernel/firmware_session.py',
        'session_source_sha256':hashlib.sha256(Path(__file__).with_name('firmware_session.py').read_bytes()).hexdigest(),
        'guest_source':OFF_SOURCE,'guest_source_sha256':hashlib.sha256(OFF_SOURCE.encode()).hexdigest(),
        'scope':'Diskless generic ARM virt: self-authored PSCI shutdown with post-exit tail or concurrent QMP queries, versus paused host QMP quit. No Apple code, target disk, GPU or network.',
        'filesystem_cleanliness_tested':False,'cases':[]}
    with tempfile.TemporaryDirectory(prefix='lifecycle-guest-',dir=ROOT/'build') as build:
        build=Path(build);source=build/'off.S';obj=build/'off.o';guest=build/'off.elf'
        source.write_text(OFF_SOURCE)
        for command in (['clang','--target=aarch64-none-elf','-c',str(source),'-o',str(obj)],
                        ['ld.lld','--build-id=none','-Ttext=0x40080000','-e','_start',str(obj),'-o',str(guest)]):
            subprocess.run(command,check=True,capture_output=True,timeout=15)
        result['guest_elf_sha256']=hashlib.sha256(guest.read_bytes()).hexdigest()
        for natural in (True,False):
            result['cases'].append(run_case(binary,guest,out/('guest' if natural else 'host'),natural))
        result['cases'].append(run_case(binary,guest,out/'guest-query',True,observe_shutdown=True))
    result['passed']=all(case['passed'] for case in result['cases'])
    (out/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'results':str(out/'results.json'),'passed':result['passed'],
        'cases':[{key:case[key] for key in ('case','passed','errors','checks')} for case in result['cases']]},indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
