#!/usr/bin/env python3
"""Verify opt-in sound realization on paused, diskless generic ARM virt."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import socket
import struct
import subprocess
import tempfile
import time

from firmware_session import FirmwareSession, ROOT, inspect_wav_capture, virtio_sound_backend_argument
from virtio_observation import require_fixed_qmp_build

spec=importlib.util.spec_from_file_location('user_network_probe',Path(__file__).with_name('run-user-network.py'))
network_probe=importlib.util.module_from_spec(spec)
spec.loader.exec_module(network_probe)
capability_chain=network_probe.capability_chain


def prepare_wav_output(endpoint,config):
    """Own synthetic control requests only; no CPU execution or PCM samples."""
    connection=socket.socket(socket.AF_UNIX);connection.settimeout(5);connection.connect(str(endpoint))
    stream=connection.makefile('rb')
    def command(text):
        connection.sendall((text+'\n').encode())
        reply=stream.readline(4096).decode().strip().split()
        if not reply or reply[0]!='OK':raise RuntimeError('qtest request failed: '+str(reply))
        return int(reply[1],0) if len(reply)>1 else None
    def write_memory(address,data):
        command(f'write {address:#x} {len(data):#x} 0x{data.hex()}')
    try:
        # Generic ARM virt low PCI window and RAM, intentionally no VMApple.
        ecam=0x3f000000+(6<<15)
        bars={1:0x10000000,4:0x10004000}
        command(f'writel {ecam+0x14:#x} {bars[1]:#x}')
        command(f'writel {ecam+0x20:#x} {bars[4]:#x}')
        command(f'writel {ecam+0x24:#x} 0')
        command(f'writew {ecam+4:#x} 6')
        caps={row['virtio_type']:row['offset'] for row in capability_chain(config) if row['id']==9}
        def cap_address(kind):
            offset=caps[kind]
            return bars[config[offset+4]]+struct.unpack_from('<I',config,offset+8)[0]
        common=cap_address(1);notify=cap_address(2)
        multiplier=struct.unpack_from('<I',config,caps[2]+16)[0]
        command(f'writeb {common+20:#x} 0')
        command(f'writeb {common+20:#x} 3')
        command(f'writel {common+8:#x} 1')
        command(f'writel {common+12:#x} 1')  # VIRTIO_F_VERSION_1 only
        command(f'writeb {common+20:#x} 11')
        if command(f'readb {common+20:#x}')!=11:raise RuntimeError('Feature negotiation rejected')
        command(f'writew {common+22:#x} 0')
        command(f'writew {common+24:#x} 8')
        desc,avail,used,payload,response=(0x40010000,0x40011000,0x40012000,0x40013000,0x40014000)
        for offset,address in ((32,desc),(40,avail),(48,used)):
            command(f'writel {common+offset:#x} {address:#x}')
            command(f'writel {common+offset+4:#x} 0')
        write_memory(avail,b'\0'*22);write_memory(used,b'\0'*70)
        command(f'writew {common+28:#x} 1')
        notify+=multiplier*command(f'readw {common+30:#x}')
        command(f'writeb {common+20:#x} 15')
        configured_streams=command(f'readl {cap_address(4)+4:#x}')
        if configured_streams!=2:raise RuntimeError('Expected the restored two-stream enumeration')
        responses=[]
        requests=[
            ('PCM_INFO_OUTPUT',struct.pack('<IIII',0x100,0,1,32),36,0x8000),
            ('PCM_INFO_INPUT',struct.pack('<IIII',0x100,1,1,32),36,0x8000),
            ('PCM_SET_PARAMS',struct.pack('<IIIII4B',0x101,0,4096,1024,0,2,5,7,0),4,0x8000),
            ('PCM_PREPARE',struct.pack('<II',0x102,0),4,0x8000),
            ('PCM_RELEASE',struct.pack('<II',0x103,0),4,0x8000)]
        directions={}
        for sequence,(name,data,response_bytes,expected_status) in enumerate(requests,1):
            write_memory(payload,data);write_memory(response,b'\xff'*response_bytes)
            write_memory(desc,struct.pack('<QIHH',payload,len(data),1,1)+
                              struct.pack('<QIHH',response,response_bytes,2,0))
            command(f'writew {avail+4+2*((sequence-1)%8):#x} 0')
            command(f'writew {avail+2:#x} {sequence}')
            command(f'writew {notify:#x} 0')
            deadline=time.monotonic()+3
            while command(f'readw {used+2:#x}')!=sequence:
                if time.monotonic()>=deadline:raise TimeoutError('Synthetic audio control completion')
                time.sleep(.01)
            status=command(f'readl {response:#x}')
            responses.append({'name':name,'code':hex(struct.unpack_from('<I',data)[0]),
                              'status':hex(status),'expected_status':hex(expected_status)})
            if status!=expected_status:raise RuntimeError('Unexpected synthetic audio control response')
            if name in ('PCM_INFO_OUTPUT','PCM_INFO_INPUT'):
                # Response header(4) + PCM info hdr/features/formats/rates(24).
                direction=command(f'readb {response+28:#x}')
                stream_id=0 if name=='PCM_INFO_OUTPUT' else 1
                directions[str(stream_id)]=direction
                if direction!=stream_id:raise RuntimeError('Unexpected advertised PCM direction')
        return {'commands':[item[0] for item in requests],
                'configured_streams':configured_streams,'advertised_stream_directions':directions,
                'input_voice_or_pcm_tested':False,
                'responses':responses,'pcm_samples_sent':0,'guest_cpu_executed':False}
    finally:
        stream.close();connection.close()


def run_case(directory, enabled, backend='none'):
    fw=FirmwareSession(directory,virtio_sound=enabled,sound_backend=backend)
    fw.temp=tempfile.TemporaryDirectory(prefix='sound-test-',dir=ROOT/'build')
    endpoint=Path(fw.temp.name)/'qmp.sock'
    qtest_endpoint=Path(fw.temp.name)/'qtest.sock'
    result={'virtio_sound_requested':enabled,'guest_cpu_executed':False,
            'sound_backend':backend,'apple_firmware_used':False,'host_audio_hardware_used':False,'errors':[]}
    try:
        fw.args=[str(ROOT/'build/qemu-bdif-usb/qemu-system-aarch64'),
            '-machine','virt,highmem=off','-cpu','max','-accel','tcg','-m','128M','-smp','1',
            '-nodefaults','-nic','none','-display','none','-monitor','none','-serial','none','-S',
            *fw.sound_arguments(),'-qmp',f'unix:{endpoint},server=on,wait=off']
        if backend=='wav':
            fw.args+=['-qtest',f'unix:{qtest_endpoint},server=on,wait=off',
                      '-qtest-log',str(directory/'qtest.log')]
        result['command']=fw.args
        result['audio']=fw.execution_metadata()['audio']
        fw.err=(directory/'qemu-stderr.log').open('wb')
        fw.proc=subprocess.Popen(fw.args,stdout=subprocess.DEVNULL,stderr=fw.err,
            start_new_session=True,preexec_fn=fw._child_parent_death_signal)
        result['qemu_pid']=fw.proc.pid
        deadline=time.monotonic()+10
        while not endpoint.exists():
            if fw.proc.poll() is not None:raise RuntimeError('QEMU exited before QMP readiness')
            if time.monotonic()>=deadline:raise TimeoutError('QMP readiness')
            time.sleep(.02)
        fw.qmp=socket.socket(socket.AF_UNIX);fw.qmp.settimeout(5);fw.qmp.connect(str(endpoint))
        fw.qf=fw.qmp.makefile('rb')
        result['greeting']=json.loads(fw.qf.readline())
        fw.request('qmp_capabilities')
        result['status']=fw.request('query-status')
        result['pci']=fw.request('query-pci')
        devices=[device for bus in result['pci'] for device in bus['devices']]
        sound=[device for device in devices if device['class_info']['class']==0x0401]
        result['checks']={'cpus_remained_paused':result['status']['running'] is False,
            'expected_audio_device_count':len(sound)==(1 if enabled else 0),
            'default_has_no_audio_backend':enabled or '-audiodev' not in fw.args,
            'host_audio_hardware_not_requested':not any(
                value.startswith(('alsa,','pa,','pipewire,','oss,','coreaudio,','jack,'))
                for value in fw.args)}
        if enabled:
            device=sound[0]
            result['checks']['standard_sound_identity_slot6']=(
                device['slot']==6 and device['function']==0 and
                device['id']['vendor']==0x1af4 and device['id']['device']==0x1059)
            ecam=0x3f000000+(6<<15)
            raw=fw.request('human-monitor-command',{'command-line':f'xp /256bx {ecam:#x}'})
            config=bytes(int(value,16) for line in raw.splitlines() if ':' in line
                         for value in re.findall(r'\b0x([0-9a-fA-F]{2})\b',line.split(':',1)[1]))
            (directory/'pci-config.bin').write_bytes(config)
            chain=capability_chain(config)
            result['pci_config']={'ecam_address':hex(ecam),
                'sha256':hashlib.sha256(config).hexdigest(),'chain':chain}
            result['checks']['pci_config_identity']=config[:4]==bytes.fromhex('f41a5910')
            result['checks']['ascending_capability_chain']=(
                len(chain)>0 and [row['offset'] for row in chain]==sorted(row['offset'] for row in chain))
            status=fw.request('x-query-virtio-status',
                {'path':'/machine/peripheral/guestsound-pci/virtio-backend'})
            result['virtio_status']=status
            result['checks']['standard_id_and_four_queues']=status['device-id']==25 and status['num-vqs']==4
            result['configured_streams']=fw.request('qom-get',
                {'path':'/machine/peripheral/guestsound-pci/virtio-backend','property':'streams'})
            result['checks']['backend_stream_count']=result['configured_streams']==2
            result['queues']=[fw.request('x-query-virtio-queue-status',
                {'path':'/machine/peripheral/guestsound-pci/virtio-backend','queue':index}) for index in range(4)]
            result['checks']['selected_backend_argument']=(
                fw.args[fw.args.index('-audiodev')+1]==virtio_sound_backend_argument(fw.sound))
            if backend=='wav':
                result['synthetic_output_prepare']=prepare_wav_output(qtest_endpoint,config)
                result['checks']['cpu_still_paused_after_control']=fw.request('query-status')['running'] is False
    except Exception as error:
        result['errors'].append(repr(error))
    finally:
        fw.close()
        result.update(qemu_exit_code=fw.proc.returncode if fw.proc else None,
            qemu_reaped=bool(fw.proc and fw.proc.poll() is not None),
            temporary_endpoint_removed=not endpoint.exists())
        if backend=='wav':
            result['wav_capture']=inspect_wav_capture(fw.sound,qemu_reaped=result['qemu_reaped'])
            result.setdefault('checks',{})['empty_wav_header_after_close']=(
                result['wav_capture']['header_valid'] and result['wav_capture']['data_bytes']==0)
        result['passed']=(bool(result.get('checks')) and all(result['checks'].values()) and
            not result['errors'] and result['qemu_exit_code']==0 and result['qemu_reaped'] and
            result['temporary_endpoint_removed'])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]{1,60}',args.label):parser.error('Invalid label')
    build=require_fixed_qmp_build()
    directory=ROOT/'runs/e05-virtio-sound'/args.label
    directory.mkdir(parents=True,exist_ok=False)
    result={'scope':'Paused diskless generic ARM virt PCI/QMP realization. No Apple OS, '
                    'driver binding, CoreAudio defaults, PCM transfer or audible playback claim.',
            'build':build,'cases':[run_case(directory/name,enabled,backend)
                                  for name,enabled,backend in (('default',False,'none'),
                                                              ('sound',True,'none'),('wav',True,'wav'))]}
    result['passed']=all(case['passed'] for case in result['cases'])
    (directory/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'report':str(directory/'results.json'),'passed':result['passed']}))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
