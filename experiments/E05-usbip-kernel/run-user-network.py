#!/usr/bin/env python3
"""Instantiate the opt-in NIC on paused generic ARM virt, without Apple/GPU code."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time

from firmware_session import FirmwareSession, ROOT


def capability_chain(config):
    if len(config)!=256:raise ValueError('Expected one conventional PCI configuration header')
    offset=config[0x34];rows=[];seen=set()
    while offset:
        if offset in seen or offset<0x40 or offset>0xfc or offset&3:
            raise ValueError('Invalid conventional capability chain')
        seen.add(offset)
        rows.append({'offset':offset,'id':config[offset],'next':config[offset+1],
                     'virtio_type':config[offset+3] if config[offset]==9 else None})
        offset=config[offset+1]
    return rows


def run_case(directory, enabled, capability_order='on'):
    # Reuse the actual production network-argument generator and QMP/cleanup
    # consumer, but intentionally do not call FirmwareSession.__enter__.
    fw = FirmwareSession(directory, user_network=enabled)
    fw.temp = tempfile.TemporaryDirectory(prefix='user-net-test-', dir=ROOT/'build')
    endpoint = Path(fw.temp.name)/'qmp.sock'
    result = {'user_network_requested':enabled, 'guest_cpu_executed':False,
              'apple_firmware_used':False, 'gpu_device_used':False, 'errors':[],
              'capability_order':capability_order if enabled else None}
    try:
        network_args=fw.network_arguments()
        if enabled and capability_order=='default':
            network_args=[arg.replace(',x-ascending-capabilities=on','') for arg in network_args]
        elif enabled and capability_order=='off':
            network_args=[arg.replace('x-ascending-capabilities=on','x-ascending-capabilities=off') for arg in network_args]
        fw.args = [str(ROOT/'build/qemu-bdif-usb/qemu-system-aarch64'),
            '-machine','virt,highmem=off','-cpu','max','-accel','tcg','-m','128M','-smp','1',
            '-nodefaults','-display','none','-monitor','none','-serial','none','-S',
            *network_args,'-qmp',f'unix:{endpoint},server=on,wait=off']
        result['command'] = fw.args
        fw.err = (directory/'qemu-stderr.log').open('wb')
        fw.proc = subprocess.Popen(fw.args, stdout=subprocess.DEVNULL, stderr=fw.err,
                                  start_new_session=True,preexec_fn=fw._child_parent_death_signal)
        result['qemu_pid'] = fw.proc.pid
        deadline = time.monotonic()+10
        while not endpoint.exists():
            if fw.proc.poll() is not None:raise RuntimeError('QEMU exited before QMP readiness')
            if time.monotonic()>=deadline:raise TimeoutError('QMP readiness')
            time.sleep(.02)
        fw.qmp = socket.socket(socket.AF_UNIX);fw.qmp.settimeout(5);fw.qmp.connect(str(endpoint))
        fw.qf = fw.qmp.makefile('rb');result['greeting']=json.loads(fw.qf.readline())
        fw.request('qmp_capabilities')
        result['status'] = fw.request('query-status')
        result['pci'] = fw.request('query-pci')
        result['network'] = fw.request('human-monitor-command', {'command-line':'info network'})
        result['usernet'] = fw.request('human-monitor-command', {'command-line':'info usernet'})
        devices = [device for bus in result['pci'] for device in bus['devices']]
        nics = [device for device in devices if device['class_info']['class']==0x0200]
        result['checks'] = {'cpus_remained_paused':result['status']['running'] is False,
            'no_host_forward_args':not any('hostfwd' in arg or 'guestfwd' in arg for arg in fw.args),
            'no_physical_bridge_args':not any(arg.startswith(('tap,','bridge,')) for arg in fw.args),
            'expected_nic_count':len(nics)==(1 if enabled else 0),
            'no_live_host_forwards':re.search(r'(?:TCP|UDP)\[HOST_FORWARD\]',result['usernet']) is None}
        if enabled:
            result['checks']['modern_virtio_net_slot5'] = (len(nics)==1 and nics[0]['slot']==5 and
                nics[0]['function']==0 and nics[0]['id']['vendor']==0x1af4 and nics[0]['id']['device']==0x1041)
            result['checks']['user_backend_present'] = 'type=user' in result['network']
            # Fixed generic virt low-ECAM map, hw/arm/virt.c VIRT_PCIE_ECAM.
            ecam=0x3f000000+(5<<15)
            raw=fw.request('human-monitor-command',{'command-line':f'xp /256bx {ecam:#x}'})
            config=bytes(int(value,16) for line in raw.splitlines() if ':' in line
                         for value in re.findall(r'\b0x([0-9a-fA-F]{2})\b',line.split(':',1)[1]))
            (directory/'pci-config.txt').write_text(raw)
            (directory/'pci-config.bin').write_bytes(config)
            result['pci_config']={'path':str(directory/'pci-config.bin'),'ecam_address':hex(ecam),
                'sha256':hashlib.sha256(config).hexdigest(),'chain':capability_chain(config)}
            result['checks']['configuration_identity']=(config[:4]==bytes.fromhex('f41a4110'))
            result['virtio_status']=fw.request('x-query-virtio-status',
                {'path':'/machine/peripheral/guestnet-pci/virtio-backend'})
        else:
            result['checks']['default_nic_none'] = fw.args[fw.args.index('-nic')+1]=='none' and not result['network'].strip()
    except Exception as error:
        result['errors'].append(repr(error))
    finally:
        fw.close()
        result['qemu_exit_code'] = fw.proc.returncode if fw.proc else None
        result['qemu_reaped'] = bool(fw.proc and fw.proc.poll() is not None)
        result['temporary_endpoint_removed'] = not endpoint.exists()
        result['qmp_shutdown_event'] = any(event.get('event')=='SHUTDOWN' and
            event.get('data',{}).get('reason')=='host-qmp-quit' for event in fw.events)
        result['passed'] = (bool(result.get('checks')) and all(result['checks'].values()) and
            not result['errors'] and result['qemu_exit_code']==0 and result['qemu_reaped'] and
            result['temporary_endpoint_removed'])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]{1,60}',args.label):parser.error('Invalid label')
    directory=ROOT/'runs/e05-user-network'/args.label
    directory.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((ROOT/'runs/qemu-bdif-usb-build.json').read_text())
    binary=ROOT/manifest['binary']
    with binary.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=manifest['binary_sha256']:raise RuntimeError('Build manifest and executable differ')
    result={'scope':'Paused generic ARM virt device instantiation only; no Apple OS, GPU, '
                    'DHCP, DNS, guest packet transfer or connectivity validation.',
            'binary':str(binary),'binary_sha256':digest,
            'device_profile':manifest['device_profile'],'reims_library':manifest['reims_library'],
            'cases':[run_case(directory/name,enabled,order) for name,enabled,order in
                     (('default',False,'default'),('nic-default',True,'default'),
                      ('nic-off',True,'off'),('nic-on',True,'on'))],
            'apple_networking_verified':False,'packet_transfer_tested':False}
    cases=result['cases']
    if all(case['passed'] for case in cases):
        old=(directory/'nic-default/pci-config.bin').read_bytes()
        off=(directory/'nic-off/pci-config.bin').read_bytes()
        new=(directory/'nic-on/pci-config.bin').read_bytes()
        old_chain=capability_chain(old);new_chain=capability_chain(new)
        allowed={0x34}|{row['offset']+1 for row in old_chain}
        changed=[i for i,(a,b) in enumerate(zip(old,new)) if a!=b]
        fields=('device-id','num-vqs','host-features','backend-features','guest-features')
        result['capability_comparison']={'changed_offsets':changed,'allowed_link_offsets':sorted(allowed),
            'checks':{'explicit_off_equals_default':off==old,
                'default_descending_retained':[row['offset'] for row in old_chain]==sorted((row['offset'] for row in old_chain),reverse=True),
                'enabled_ascending':[row['offset'] for row in new_chain]==sorted(row['offset'] for row in old_chain),
                'same_members':{row['offset'] for row in old_chain}=={row['offset'] for row in new_chain},
                'only_head_and_next_changed':bool(changed) and set(changed)<=allowed,
                'all_payload_identity_bar_msix_bytes_preserved':all(a==b for i,(a,b) in enumerate(zip(old,new)) if i not in allowed),
                'virtio_features_unchanged':all(cases[1]['virtio_status'][key]==cases[3]['virtio_status'][key] for key in fields)}}
    result['passed']=(all(case['passed'] for case in cases) and
                      all(result.get('capability_comparison',{}).get('checks',{'missing':False}).values()))
    (directory/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'report':str(directory/'results.json'),'passed':result['passed'],'binary_sha256':digest}))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
