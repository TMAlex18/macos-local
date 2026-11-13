#!/usr/bin/env python3
"""Observe a fresh cold boot from a completed native installation's targets."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(Path(__file__).resolve().parent), str(ROOT / 'experiments/E05-usbip-kernel'), str(ROOT / 'experiments/E05-ibec')]
from guest_files import GuestFileProcess
from guest_ui import UIError
from timeout_diagnostic import require_build as require_timeout_diagnostic_build, public_configuration as timeout_configuration
from firmware_session import FirmwareSession, FirmwareSessionInterrupted, BLANK_ROOT_BYTES, cpu_model, window_refresh_value
from firmware_session import VIRTIO_SOUND_BACKEND, VIRTIO_SOUND_DEVICE, virtio_sound_configuration
from firmware_session import virtio_sound_backend_argument, virtio_sound_device_argument, inspect_wav_capture
from firmware_session import parse_vcpu_affinity, PAUTH_ALGORITHMS
from firmware_session import validate_net_control_queue_size, virtio_net_device_argument
from kernel_boot_observation import KernelBootObserver, CAPTURE_BYTES
from source_log_check import read_bound_file, inspect_client_log, file_identity
from virtio_observation import require_fixed_qmp_build

QEMU = ROOT / 'build/qemu-bdif-usb/qemu-system-aarch64'
PAUTH_CAPACITY_PATCH = 'patches/qemu-platform/0024-pauth-memo-capacity-stats.patch'
PAUTH_LARGE_CAPACITY_PATCH = 'patches/qemu-platform/0029-pauth-memo-16384.patch'
PCM_TRACE_PATCH = 'patches/qemu-platform/0023-virtio-snd-pcm-boundary-trace.patch'
SOUND_EMPTY_INFO_PATCH = 'patches/qemu-platform/0027-virtio-snd-empty-info.patch'
SOUND_QUEUE_SIZE_PATCH = 'patches/qemu-platform/0028-virtio-snd-queue-size.patch'
NET_CONTROL_QUEUE_SIZE_PATCH = 'patches/qemu-platform/0032-virtio-net-control-queue-size.patch'
SYNC_STATS_PATCH = 'patches/qemu-platform/0033-tcg-sync-source-counters.patch'
SAMPLE_SECONDS = 15
SCREEN_SECONDS = 60
SCREEN_MAX_FILES = 16
SCREEN_MAX_BYTES = 128 * 1024 * 1024
GPU_ICDS = {'nvidia': '/usr/share/vulkan/icd.d/nvidia_icd.json',
            'intel': '/usr/share/vulkan/icd.d/intel_icd.json'}
COUNTERS = ('rd_operations', 'wr_operations', 'rd_bytes', 'wr_bytes',
            'failed_rd_operations', 'failed_wr_operations')
FRONTENDS = {'pflash0': 'aux.raw', 'aux': 'aux.raw', 'pflash1': 'root.raw', 'root': 'root.raw'}
UUID_PATTERN = r'([0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12})'
SNAPSHOT_PATTERN = r'(com\.apple\.os\.update-[0-9A-Fa-f]{64})'


def read_json(path, maximum=4 * 1024 * 1024):
    raw, identity = read_bound_file(path, maximum)
    return json.loads(raw), {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'stat': identity}


def option(args, name):
    if args.count(name) != 1:
        raise ValueError(f'Expected one QEMU {name} option')
    return args[args.index(name) + 1]


def require_sound_pcm_trace_build(fixed_build):
    """PCM capture and its opt-in control events require both matching patches."""
    record=json.loads((ROOT/'runs/qemu-bdif-usb-build.json').read_text())
    expected={patch:hashlib.sha256((ROOT/patch).read_bytes()).hexdigest()
              for patch in (PCM_TRACE_PATCH,SOUND_EMPTY_INFO_PATCH)}
    if (record.get('binary_sha256')!=fixed_build.get('binary_sha256') or
            any(patch not in record.get('patches',[]) or
                record.get('patch_sha256',{}).get(patch)!=digest
                for patch,digest in expected.items())):
        raise ValueError('Requested PCM/control trace is not verified in this QEMU build; rebuild with 0023 and 0027')
    return {'patch':PCM_TRACE_PATCH,'patch_sha256':expected[PCM_TRACE_PATCH],
            'control_trace':{'patch':SOUND_EMPTY_INFO_PATCH,
                'patch_sha256':expected[SOUND_EMPTY_INFO_PATCH],
                'event':'virtio_snd_empty_info'}}


def require_sound_queue_size_build(fixed_build):
    """An explicitly requested queue-size property needs the matching 0028 build."""
    record=json.loads((ROOT/'runs/qemu-bdif-usb-build.json').read_text())
    expected=hashlib.sha256((ROOT/SOUND_QUEUE_SIZE_PATCH).read_bytes()).hexdigest()
    if (record.get('binary_sha256')!=fixed_build.get('binary_sha256') or
            SOUND_QUEUE_SIZE_PATCH not in record.get('patches',[]) or
            record.get('patch_sha256',{}).get(SOUND_QUEUE_SIZE_PATCH)!=expected):
        raise ValueError('Requested sound queue size is not verified in this QEMU build; rebuild with 0028')
    return {'patch':SOUND_QUEUE_SIZE_PATCH,'patch_sha256':expected}


def require_net_control_queue_size_build(fixed_build):
    """Explicit control-queue capacity requires the matching0032 implementation."""
    record=json.loads((ROOT/'runs/qemu-bdif-usb-build.json').read_text())
    expected=hashlib.sha256((ROOT/NET_CONTROL_QUEUE_SIZE_PATCH).read_bytes()).hexdigest()
    if (record.get('binary_sha256')!=fixed_build.get('binary_sha256') or
            NET_CONTROL_QUEUE_SIZE_PATCH not in record.get('patches',[]) or
            record.get('patch_sha256',{}).get(NET_CONTROL_QUEUE_SIZE_PATCH)!=expected):
        raise ValueError('Requested network control queue size is not verified in this QEMU build; rebuild with0032')
    return {'patch':NET_CONTROL_QUEUE_SIZE_PATCH,'patch_sha256':expected}


def require_sync_stats_build(fixed_build):
    """Explicit per-vCPU TCG sync statistics require the matching 0033 build."""
    record=json.loads((ROOT/'runs/qemu-bdif-usb-build.json').read_text())
    expected=hashlib.sha256((ROOT/SYNC_STATS_PATCH).read_bytes()).hexdigest()
    if (record.get('binary_sha256')!=fixed_build.get('binary_sha256') or
            SYNC_STATS_PATCH not in record.get('patches',[]) or
            record.get('patch_sha256',{}).get(SYNC_STATS_PATCH)!=expected):
        raise ValueError('Requested TCG sync statistics are not verified in this QEMU build; rebuild with 0033')
    return {'patch':SYNC_STATS_PATCH,'patch_sha256':expected}


def require_pauth_memo_capacity_build(fixed_build,entries=None):
    """Existing capacities/statistics need 0024; 16384 additionally needs 0029."""
    record=json.loads((ROOT/'runs/qemu-bdif-usb-build.json').read_text())
    expected=hashlib.sha256((ROOT/PAUTH_CAPACITY_PATCH).read_bytes()).hexdigest()
    if (record.get('binary_sha256')!=fixed_build.get('binary_sha256') or
            PAUTH_CAPACITY_PATCH not in record.get('patches',[]) or
            record.get('patch_sha256',{}).get(PAUTH_CAPACITY_PATCH)!=expected):
        raise ValueError('Requested PAuth memo capacity/statistics is not verified in this QEMU build; rebuild with 0024')
    result={'patch':PAUTH_CAPACITY_PATCH,'patch_sha256':expected}
    if entries==16384:
        large=hashlib.sha256((ROOT/PAUTH_LARGE_CAPACITY_PATCH).read_bytes()).hexdigest()
        if (PAUTH_LARGE_CAPACITY_PATCH not in record.get('patches',[]) or
                record.get('patch_sha256',{}).get(PAUTH_LARGE_CAPACITY_PATCH)!=large):
            raise ValueError('Requested 16384-entry PAuth memo is not verified in this QEMU build; rebuild with 0029')
        result['large_capacity']={'entries':16384,'patch':PAUTH_LARGE_CAPACITY_PATCH,'patch_sha256':large}
    return result


def qemu_profile(args, presentation=None, network=None, *, pauth_memo=False, ram_gib=4,
                 virtio_sound=False, audio=None,sound_backend='none',run_directory=None,
                 sound_pcm_trace=False,sound_queue_size=None,sound_streams=None,pauth_memo_entries=None,pauth_memo_stats=False,
                 webkit_timeout_diagnostic=False,net_control_queue_size=None,pauth_algorithm='qarma5',
                 sync_stats=False):
    if type(ram_gib) is not int or ram_gib not in (4,8):
        raise ValueError('Only explicit 4/8-GiB RAM profiles are supported')
    if not isinstance(args, list) or not args:
        raise ValueError('Source/current QEMU execution arguments are absent')
    machine = option(args, '-M')
    memory = f'{ram_gib}G'
    ram_objects = [args[index + 1] for index, value in enumerate(args[:-1])
                   if value == '-object' and args[index + 1].startswith('memory-backend-memfd,')]
    required = {'vmapple', 'memory-backend=ram', 'uuid=1', 'x-ctrr-model=on', 'x-pac-software-model=on'}
    if (not required.issubset(machine.split(',')) or option(args, '-m') != memory or
            option(args, '-smp') not in ('2','4','8') or
            option(args, '-cpu') != cpu_model(pauth_algorithm,pauth_memo=pauth_memo,
                                                pauth_memo_entries=pauth_memo_entries,pauth_memo_stats=pauth_memo_stats,
                                                webkit_timeout_diagnostic=webkit_timeout_diagnostic,
                                                sync_stats=sync_stats) or
            option(args, '-accel') != 'tcg,thread=multi' or option(args, '-display') != 'none' or
            ram_objects != [f'memory-backend-memfd,id=ram,size={memory},share=on'] or
            any(value in args for value in ('-incoming', '-loadvm', '-kernel', '-append'))):
        raise ValueError('QEMU profile differs from the fixed fresh-RAM cold-boot profile')
    drives = [args[index + 1] for index, value in enumerate(args) if value == '-drive']
    devices = [args[index + 1] for index, value in enumerate(args) if value == '-device']
    net_devices=[value for value in devices if value.split(',',1)[0]=='virtio-net-pci']
    if net_control_queue_size is not None:
        validate_net_control_queue_size(bool(network and network.get('requested') is True),net_control_queue_size)
        net_backends=[args[i+1] for i,value in enumerate(args[:-1]) if value=='-netdev']
        if (net_devices!=[virtio_net_device_argument(net_control_queue_size)] or
                net_backends!=['user,id=guestnet']):
            raise ValueError('Network control queue/backend argv differs from the explicit request')
        metadata=network.get('control_queue_size')
        if metadata is not None and (metadata.get('requested')!=net_control_queue_size or
                metadata.get('configured_maximum')!=net_control_queue_size or
                metadata.get('actual_device_arguments')!=net_devices):
            raise ValueError('Network control queue metadata differs from actual argv')
    elif any(field.split('=',1)[0]=='ctrl_queue_size' for value in net_devices for field in value.split(',')[1:]):
        raise ValueError('Network control queue property was not explicitly requested')
    sound = virtio_sound_configuration(virtio_sound,sound_backend,run_directory,
                                      pcm_trace=sound_pcm_trace,queue_size=sound_queue_size,streams=sound_streams)
    sound_devices = [value for value in devices if value.split(',',1)[0]=='virtio-sound-pci']
    sound_backends = [args[index + 1] for index, value in enumerate(args) if value == '-audiodev']
    if (sound_devices != ([virtio_sound_device_argument(sound)] if virtio_sound else []) or
            sound_backends != ([virtio_sound_backend_argument(sound)] if virtio_sound else []) or
            (audio is not None and audio != sound)):
        raise ValueError('VirtIO sound arguments or metadata differ from the explicit request')
    if (len(drives) != 4 or
            not any(value.startswith('vmapple-virtio-blk-pci,variant=aux,') for value in devices) or
            not any(value.startswith('vmapple-virtio-blk-pci,variant=root,') for value in devices) or
            not any(value.startswith('vmapple-virtio-usb-pci,') for value in devices)):
        raise ValueError('Expected ROOT/AUX dual frontends and the same kernel USB controller')
    paths = {}
    for value in drives:
        fields = dict(field.split('=', 1) for field in value.split(',') if '=' in field)
        if fields.get('format') != 'raw' or fields.get('readonly') == 'on':
            raise ValueError('Cold boot requires the owned writable raw targets')
        key = 'pflash' + fields['index'] if fields.get('if') == 'pflash' else fields.get('id')
        if key not in FRONTENDS or key in paths:
            raise ValueError('Unexpected or duplicate storage frontend')
        paths[key] = fields.get('file')
    if (set(paths) != set(FRONTENDS) or paths['pflash0'] != paths['aux'] or
            paths['pflash1'] != paths['root'] or paths['aux'] == paths['root']):
        raise ValueError('ROOT/AUX frontends do not share the expected two target paths')
    presentation = dict(presentation or {'native_window_requested': False,
        'usb_input_requested': False, 'reims_vgpu_window': '0', 'vulkan_icd': None})
    if presentation.get('usb_input_requested') and not all(item in devices for item in (
            'qemu-xhci,id=input-xhci,addr=0x4',
            'usb-kbd,id=input-keyboard,bus=input-xhci.0,port=1',
            'usb-tablet,id=input-tablet,bus=input-xhci.0,port=2')):
        raise ValueError('Requested USB input devices are missing from current QEMU arguments')
    return {'machine': machine, 'memory': memory, 'ram_bytes': ram_gib * 1024**3,
            'vcpus': int(option(args,'-smp')), 'cpu': option(args, '-cpu'),
            'pauth_algorithm':pauth_algorithm,'pauth_memo_requested':pauth_memo,
        'pauth_memo_entries_requested':pauth_memo_entries,'pauth_memo_stats_requested':pauth_memo_stats,
            'sync_stats_requested':sync_stats,
            'accelerator': 'tcg,thread=multi', 'ram_backend': 'fresh memory-backend-memfd',
            'display': 'none', 'presentation': presentation, 'storage_frontends': paths,
            'virtio_usb_present': True,
            'network':dict(network or {'requested':False,'guest_connectivity':'unknown'}),
            'audio':sound,'webkit_timeout_diagnostic':timeout_configuration(webkit_timeout_diagnostic)}


def retained_alias_provenance(targets):
    matches, unreadable = [], 0
    for path in (ROOT / 'runs/e05-installed-boot').glob('*/retained-storage.json'):
        try:
            record, _ = read_json(path, 65536)
            if all((record.get('files', {}).get(name, {}).get('device'),
                    record.get('files', {}).get(name, {}).get('inode')) ==
                   (target['device'], target['inode']) for name, target in targets.items()):
                matches.append({'run': path.parent.name, 'manifest': str(path),
                    'manifest_mtime_ns': path.stat().st_mtime_ns,
                    'recorded_qemu_reaped': record.get('qemu_reaped'),
                    'recorded_qemu_exit_code': record.get('qemu_exit_code'),
                    'recorded_teardown': record.get('teardown')})
        except (OSError, ValueError, TypeError):
            unreadable += 1
    matches.sort(key=lambda row: row['manifest_mtime_ns'])
    return {'matching_cold_boot_alias_count': len(matches), 'latest_alias_records': matches[-32:],
            'unreadable_manifest_count': unreadable, 'snapshot': False,
            'scope': 'Current inodes include any later writes through these aliases. The installation '
                     'run supplies completion provenance, not frozen disk bytes; kernel flocks remain authoritative.'}


def source_volume_identity(path):
    result = {'path': str(path), 'available': path.exists(), 'volume_groups': [], 'system_snapshots': [],
              'unambiguous': False}
    if not path.exists():
        return result
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for number, raw in enumerate(stream, 1):
            digest.update(raw)
            line = raw.decode('utf-8', 'replace')
            group = re.search(r'created volume group ' + UUID_PATTERN, line)
            snapshot = re.search(r'libpartition: creating system volume snapshot ' + SNAPSHOT_PATTERN + r' succeeded\.', line)
            if group:
                result['volume_groups'].append({'line': number, 'value': group[1].upper()})
            if snapshot:
                result['system_snapshots'].append({'line': number, 'value': snapshot[1]})
    groups = {row['value'] for row in result['volume_groups']}
    snapshots = {row['value'].casefold() for row in result['system_snapshots']}
    result.update(sha256=digest.hexdigest(), unambiguous=len(groups) == len(snapshots) == 1,
                  volume_group_uuid=next(iter(groups)) if len(groups) == 1 else None,
                  system_snapshot=next(iter(snapshots)) if len(snapshots) == 1 else None,
                  scope='Whitelist group UUID and successful system-snapshot name only; no signing payloads.')
    return result


def kernel_identity_evidence(path):
    evidence = {'path': str(path), 'available': path.exists(), 'events': {}}
    if not path.exists():
        return evidence
    events = evidence['events']
    digest, pending_snapshot, pending_pivot = hashlib.sha256(), None, None
    patterns = {
        'boot_uuid': r'rooting via boot-uuid from /chosen:\s*' + UUID_PATTERN,
        'boot_media': r'publishing boot-uuid-media=(disk[0-9]+s[0-9]+)',
        'bsd_root': r'BSD root:\s*(disk[0-9]+s[0-9]+)',
        'hash_validated': r'(disk[0-9]+s[0-9]+) successfully validated on-disk root hash',
        'system_root': r'(disk[0-9]+s[0-9]+) ROSV: apfs mounted RO and is the system volume of a volume group and mounted as the root fs',
        'base_system': r'system_is_base_system:\s*([01])(?![0-9])',
        'normal_flags': r'startingInBaseSystem=(YES|NO) startingInNormalOS=(YES|NO) pivotingToNormalOS=(YES|NO)',
        'trm_base': r'TRM_BaseSystem\s*=\s*(Yes|No)\([01]\)',
        'trm_recovery': r'TRM_Recovery\s*=\s*(Yes|No)\([01]\)',
        'volume_uuid': r'(disk[0-9]+s[0-9]+) vol-uuid:\s*' + UUID_PATTERN,
        'windowserver': r'vm: WindowServer\[(\d+)\] triggered unnest',
    }
    with path.open('rb') as stream:
        for number, raw in enumerate(stream, 1):
            digest.update(raw)
            line = raw.decode('utf-8', 'replace')
            timestamp = re.search(r'^\[\s*([0-9]+(?:\.[0-9]+)?)\]', line)
            location = {'line': number, 'boot_seconds': float(timestamp[1]) if timestamp else None}
            for name, pattern in patterns.items():
                match = re.search(pattern, line)
                if match:
                    events.setdefault(name, []).append({**location, 'values': list(match.groups())})
            mount = re.search(r'(disk[0-9]+s[0-9]+) mounting snapshot w/snap_xid (\d+)', line)
            if mount:
                pending_snapshot = {'device': mount[1], 'xid': int(mount[2]), 'mount_line': number}
            child = re.search(r'child ' + SNAPSHOT_PATTERN + r'\(', line)
            if child and pending_snapshot:
                events.setdefault('snapshot', []).append({**location, **pending_snapshot, 'name': child[1]})
            if pending_snapshot and re.search(re.escape(pending_snapshot['device']) + r' mount-complete ', line):
                pending_snapshot = None
            pivot = re.search(r'vfs_switch_root : shuffling mount points : (.+)', line)
            if pivot:
                text = pivot[1].strip()
                direction = ('normal' if re.fullmatch(r'/System/Volumes/macOS <-> / <-> /?System/Volumes/BaseSystem', text)
                             else 'base' if re.fullmatch(r'/System/Volumes/BaseSystem <-> / <-> /?System/Volumes/macOS', text)
                             else 'unknown')
                pending_pivot = {**location, 'direction': direction, 'error': None, 'end_line': None}
                events.setdefault('pivot', []).append(pending_pivot)
            completed = re.search(r'vfs_switch_root : done shuffling mount points with error:\s*(-?\d+)', line)
            if completed and pending_pivot is not None:
                pending_pivot.update(error=int(completed[1]), end_line=number)
                pending_pivot = None
    evidence['sha256'] = digest.hexdigest()
    return evidence


def validate_source(source_run):
    if not isinstance(source_run, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', source_run):
        raise ValueError('Invalid source run label')
    source_log = ROOT / 'runs/e05-signed-restore' / source_run
    source_storage = ROOT / 'private/signed-restore' / source_run / 'storage'
    if source_log.resolve(strict=True) != source_log or source_storage.resolve(strict=True) != source_storage:
        raise ValueError('Source run and storage must use their canonical nonsymlink paths')
    installed, installed_ref = read_json(source_log / 'native-install-results.json')
    if (installed.get('passed') is not True or installed.get('state') != 'complete' or
            type(installed.get('client_exit_code')) is not int or installed['client_exit_code'] != 0 or
            installed.get('client_reaped') is not True or installed.get('timed_out') is not False or
            installed.get('errors') != [] or installed.get('restore_finished_status_observed') is not True):
        raise ValueError('Source native installation has not completed successfully')
    client_log = source_log / 'native-restore-client.log'
    file_identity(client_log)
    observed = inspect_client_log(client_log)
    recorded = installed.get('client_log', {})
    if (recorded.get('path') != str(client_log) or recorded.get('sha256') != observed['sha256'] or
            recorded.get('bytes') != observed['bytes'] or not observed['restore_finished_status_observed']):
        raise ValueError('Source installation completion log is missing or changed')
    retained, retained_ref = read_json(source_log / 'retained-storage.json', 65536)
    if (retained.get('qemu_reaped') is not True or retained.get('temporary_links_removed') is not True or
            type(retained.get('qemu_exit_code')) is not int or retained['qemu_exit_code'] != 0 or
            retained.get('teardown') != 'qmp_quit' or
            retained.get('profile') not in ('blank-root-target', 'retained-root-target') or
            retained.get('directory') != str(source_storage) or
            retained.get('method') != 'hard_links_to_owned_active_target_inodes' or
            retained.get('active_target_alias_not_snapshot') is not True or
            set(retained.get('files', {})) != {'aux.raw', 'root.raw'}):
        raise ValueError('Source target lacks normal reaped QEMU teardown and retained inode evidence')
    targets = {}
    for name, size in (('aux.raw', 32 * 1024**2), ('root.raw', BLANK_ROOT_BYTES)):
        path, recorded = source_storage / name, retained['files'][name]
        current = file_identity(path)
        if (recorded.get('path') != str(path) or recorded.get('retained_identity_matches') is not True or
                (recorded.get('device'), recorded.get('inode'), recorded.get('bytes')) !=
                (current['st_dev'], current['st_ino'], size) or current['st_size'] != size or
                path.stat().st_uid != os.geteuid()):
            raise ValueError('Source retained target path, inode, size or owner changed')
        targets[name] = {'path': str(path), 'device': current['st_dev'],
                         'inode': current['st_ino'], 'bytes': size,
                         'metadata_at_validation': {'mtime_ns': current['st_mtime_ns'],
                             'ctime_ns': current['st_ctime_ns'], 'links': path.stat().st_nlink,
                             'allocated_bytes': path.stat().st_blocks * 512}}
    execution, execution_ref = read_json(source_log / 'result.json')
    profile = qemu_profile(execution.get('qemu_argv'), execution.get('presentation'), execution.get('network'))
    if profile['vcpus']!=2:
        raise ValueError('Completed native installation source must retain the verified two-vCPU profile')
    source_hash = retained.get('qemu_binary_sha256')
    if not isinstance(source_hash, str) or re.fullmatch('[0-9a-f]{64}', source_hash) is None:
        raise ValueError('Source QEMU digest missing')
    return {'run': source_run, 'native_installation': installed_ref,
            'completion_log': {key: observed[key] for key in ('path', 'bytes', 'sha256', 'completion_marker_lines')},
            'retained_storage': retained_ref, 'execution_record': execution_ref, 'targets': targets,
            'qemu': {'binary': retained.get('qemu_binary'), 'sha256': source_hash, 'profile': profile},
            'normal_qmp_exit': True, 'guest_clean_shutdown_proven': False,
            'native_installation_verified': True,
            'installed_volume_identity': source_volume_identity(source_storage.parent / 'restored-service-live.log'),
            'storage_alias_provenance': retained_alias_provenance(targets),
            'storage_validation_at_utc': datetime.now(timezone.utc).isoformat(),
            'inheritance_scope': 'The same two active target inodes, not snapshots; other aliases follow later writes.'}


def classify_boot(state, *, source=None, kernel_log=None, context=None):
    evidence = state.get('runtime_evidence', {})
    commands = list(dict.fromkeys(row.get('command_line', '') for row in state.get('boot_args_candidates', [])))
    result = {'environment': 'unknown', 'normal_os_boot_proven': False, 'recovery_os_boot_proven': False,
            'volume_source_identity_verified': False,
            'reason': 'This collector has no installed-SystemOS/RecoveryOS volume identity contract.',
            'cues': {'darwin_banner_observed': evidence.get('darwin_banner_observed', False),
                'darwin_banner': evidence.get('darwin_banner'), 'bsd_root': evidence.get('bsd_root'),
                'kernel_panic_observed': evidence.get('kernel_panic_observed', False),
                'restore_argument_in_candidate': any('-restore' in command.split() for command in commands),
                'boot_args_candidate_count': len(state.get('boot_args_candidates', [])),
                'boot_args_commands_first8': commands[:8]},
            'absence_scope': 'An empty bounded scan, a banner, absent -restore, or a non-md0 root '
                             'does not establish normal OS boot or absence of boot.'}
    if source is None or kernel_log is None or context is None:
        return result
    observed = kernel_identity_evidence(kernel_log)
    events = observed['events']
    last = lambda name: events.get(name, [{}])[-1]
    values = lambda name: last(name).get('values', [])
    identity = source.get('installed_volume_identity', {})
    pivots = events.get('pivot', [])
    normal = pivots[-1] if pivots else {}
    base = next((row for row in reversed(pivots[:-1]) if row['direction'] == 'base' and row['error'] == 0), {})
    root = values('bsd_root')[0] if values('bsd_root') else None
    root_line, pivot_line = last('bsd_root').get('line', 10**12), base.get('line', -1)
    root_fields = lambda name: any(row['values'] == [root] and root_line < row['line'] < pivot_line
                                  for row in events.get(name, []))
    matched_snapshot = any(row['device'] == root and row['name'].casefold() == identity.get('system_snapshot')
        and root_line < row['mount_line'] <= row['line'] < pivot_line for row in events.get('snapshot', []))
    base_entered = any(row['values'] == ['1'] and (base.get('end_line') or 10**12) < row['line'] < normal.get('line', -1)
                       for row in events.get('base_system', []))
    checks = {
        'completed_source_and_fresh_target_context': source.get('native_installation_verified') is True and
            context.get('source_run') == source.get('run') and context.get('fresh_qemu_process') is True and
            context.get('ram_or_vm_snapshot_inherited') is False and context.get('restore_upload_performed') is False and
            context.get('tss_requests') == 0,
        'unique_source_group_and_snapshot': identity.get('unambiguous') is True,
        'boot_group_matches_source': bool(values('boot_uuid')) and values('boot_uuid')[0].upper() == identity.get('volume_group_uuid'),
        'published_root_matches_bsd_root': bool(root) and values('boot_media') == [root] and
            last('boot_uuid').get('line', 10**12) < root_line,
        'source_snapshot_bound_to_root_mount': matched_snapshot,
        'root_hash_and_system_role_verified': root_fields('hash_validated') and root_fields('system_root'),
        'ordered_successful_base_then_normal_pivot': base_entered and normal.get('direction') == 'normal' and
            normal.get('error') == 0 and (base.get('end_line') or 10**12) < normal.get('line', -1),
        'latest_base_flag_zero_after_normal_pivot': values('base_system') == ['0'] and
            last('base_system').get('line', -1) > (normal.get('end_line') or 10**12),
        'later_explicit_normal_os_flags': values('normal_flags') == ['NO', 'YES', 'NO'] and
            last('normal_flags').get('line', -1) > last('base_system').get('line', 10**12),
        'later_base_and_recovery_false': values('trm_base') == ['No'] and values('trm_recovery') == ['No'] and
            min(last('trm_base').get('line', -1), last('trm_recovery').get('line', -1)) > last('normal_flags').get('line', 10**12),
    }
    ws = events.get('windowserver', [])
    result.update(joint_checks=checks, source_volume_identity=identity, kernel_identity_evidence=observed,
        windowserver_observation={'execution_observed': bool(ws), 'pids': sorted({int(row['values'][0]) for row in ws}),
            'evidence': ws, 'source': str(kernel_log), 'current_liveness': 'unknown',
            'scope': 'Kernel message records a named process unnest operation; separate from actual GUI review.'},
        data_volume_uuid_support=any(row['values'][1].upper() == identity.get('volume_group_uuid')
                                     for row in events.get('volume_uuid', [])))
    if all(checks.values()):
        result.update(environment='normal_os', normal_os_boot_proven=True, volume_source_identity_verified=True,
            reason='Source group/snapshot and authenticated system-root mount match, followed by a successful '
                   'BaseSystem-to-normal pivot and later explicit normal/non-Recovery state.')
    else:
        result['reason'] = 'Joint installed-root identity sequence is incomplete or contradicted; this does not prove RecoveryOS.'
    return result


def block_counters(rows):
    return {row['device']: {'target': FRONTENDS[row['device']],
                           **{key: row.get('stats', {}).get(key) for key in COUNTERS}}
            for row in rows if row.get('device') in FRONTENDS}


def ppm_metadata(path):
    before = file_identity(path)
    if not 0 < before['st_size'] <= SCREEN_MAX_BYTES:
        raise ValueError('Screendump size exceeds bounded PPM observation')
    with path.open('rb') as stream:
        header = stream.read(128)
        match = re.match(rb'P6\n([0-9]+) ([0-9]+)\n255\n', header)
        if not match:
            raise ValueError('Screendump is not the standard QEMU P6/255 image')
        width, height = int(match[1]), int(match[2])
        if not 0 < width <= 16384 or not 0 < height <= 16384 or before['st_size'] != match.end() + width * height * 3:
            raise ValueError('Screendump dimensions or pixel length are inconsistent')
        stream.seek(0)
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if file_identity(path) != before:
        raise ValueError('Screendump changed during readback')
    return {'format': 'ppm', 'width': width, 'height': height,
            'bytes': before['st_size'], 'sha256': digest}


class ScreenObserver:
    def __init__(self, session, logs):
        self.session, self.logs, self.known = session, Path(logs), {}
        self.retained_files = 0
        self.report = {'method': 'standard QMP screendump on existing owner connection',
            'interval_seconds': SCREEN_SECONDS, 'max_retained_files': SCREEN_MAX_FILES,
            'max_file_bytes': SCREEN_MAX_BYTES, 'attempts': [], 'valid_captures': 0,
            'final_capture_observed': False, 'errors': [], 'collection_success': False,
            'gui_verified': False, 'manual_review': {'state': 'not_reviewed', 'record': None},
            'scope': 'Captured pixels only. Dimensions, mode changes, nonempty files or frame '
                     'events do not establish GUI, WindowServer or normal OS identity.'}

    def capture(self, elapsed, *, final=False):
        if not final and self.retained_files >= SCREEN_MAX_FILES - 1:
            self.report['periodic_capacity_reached'] = True
            return
        index = len(self.report['attempts']) + 1
        path = self.logs / f'screen-{index:04d}.ppm'
        row = {'at_utc': datetime.now(timezone.utc).isoformat(), 'elapsed_seconds': round(elapsed, 3),
               'final': final, 'path': str(path), 'captured': False}
        self.report['attempts'].append(row)
        if path.exists():
            row['error'] = 'Existing screen sample is preserved; destination was not overwritten'
            self.report['errors'].append(row['error'])
            return
        try:
            self.session.request('screendump', {'filename': str(path)})
        except RuntimeError as error:
            if path.exists():
                self.retained_files += 1
            row['error'] = str(error)
            self.report['errors'].append(row['error'])
            return
        except Exception as error:
            row['fatal_qmp_error'] = f'{type(error).__name__}: {error}'
            raise  # An ambiguous owned QMP stream must not be reused.
        if path.exists():
            self.retained_files += 1
        try:
            row.update(ppm_metadata(path), captured=True)
        except (OSError, ValueError) as error:
            row['error'] = f'{type(error).__name__}: {error}'
            self.report['errors'].append(row['error'])
            return
        previous = self.known.get(row['sha256'])
        if previous is not None:
            path.unlink()  # Exact duplicate of an already retained sample.
            self.retained_files -= 1
            row.update(path=previous, deduplicated=True)
        else:
            self.known[row['sha256']] = str(path)
            row['deduplicated'] = False
        self.report['valid_captures'] += 1
        self.report['retained_file_count'] = self.retained_files
        if final:
            self.report['final_capture_observed'] = True
        self.report['collection_success'] = self.report['final_capture_observed']


def summarize_trace(path):
    result = {'path': str(path), 'available': path.exists(), 'targets': {},
              'scope': 'BDIF backend I/O calls, separately from QMP frontends; reads precede guest DMA completion.'}
    if not path.exists():
        return result
    pattern = re.compile(rb'bdif_vblk_(read|write) dev=(aux|root) addr=0x[0-9a-f]+ '
                         rb'off=0x[0-9a-f]+ size=0x([0-9a-f]+) r=(-?\d+)')
    with path.open('rb') as stream:
        for line in iter(lambda: stream.readline(4096), b''):
            match = pattern.search(line)
            if not match:
                continue
            operation, target, size, status = match.groups()
            key = target.decode() + '.raw'
            row = result['targets'].setdefault(key, {'read_calls': 0, 'write_calls': 0,
                'read_success_bytes': 0, 'write_success_bytes': 0, 'failed_calls': 0})
            name = operation.decode()
            row[name + '_calls'] += 1
            if int(status) == 0:
                row[name + '_success_bytes'] += int(size, 16)
            elif int(status) < 0:
                row['failed_calls'] += 1
    return result


def run(source_run, label, seconds=None, *, usb_input=False, native_window=False, gpu=None,
        sample_current_process=False, allow_unclean_storage=False, user_network=False, exact_half_blend=False,
        display_refresh_hz=None,vcpus=2,interactive=False,pauth_memo=False,ram_gib=4,guest_files=False,
        virtio_sound=False,sound_backend='none',sound_pcm_trace=False,sound_queue_size=None,sound_streams=None,
        pauth_memo_entries=None,pauth_memo_stats=False,vcpu_affinity=None,
        kernel_observation='continuous',webkit_timeout_diagnostic=False,net_control_queue_size=None,
        pauth_algorithm='qarma5',sync_stats=False):
    if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', label):
        raise ValueError('Invalid cold-boot run label')
    if type(interactive) is not bool or (interactive and seconds is not None):
        raise ValueError('Interactive mode and an observation duration are mutually exclusive')
    if kernel_observation not in ('continuous','boot-only'):
        raise ValueError('Kernel observation must be continuous or boot-only')
    seconds=None if interactive else 600 if seconds is None else seconds
    if not interactive and (type(seconds) is not int or not 1 <= seconds <= 3600):
        raise ValueError('Observation seconds must be between 1 and 3600')
    if type(vcpus) is not int or vcpus not in (2,4,8):
        raise ValueError('Only explicit 2/4/8-vCPU profiles are supported')
    parse_vcpu_affinity(vcpu_affinity)
    if type(ram_gib) is not int or ram_gib not in (4,8):
        raise ValueError('Only explicit 4/8-GiB RAM profiles are supported')
    if type(usb_input) is not bool or type(native_window) is not bool or gpu not in (None, *GPU_ICDS):
        raise ValueError('Invalid input, native-window or GPU selection')
    if type(user_network) is not bool:
        raise ValueError('User networking selection must be boolean')
    validate_net_control_queue_size(user_network,net_control_queue_size)
    if type(guest_files) is not bool or (guest_files and not user_network):
        raise ValueError('Guest file service requires --user-network')
    sound=virtio_sound_configuration(virtio_sound,sound_backend,ROOT/'runs/e05-installed-boot'/label,
                                    pcm_trace=sound_pcm_trace,queue_size=sound_queue_size,streams=sound_streams)
    if type(exact_half_blend) is not bool:
        raise ValueError('Exact half-blend selection must be boolean')
    # Rejects an unknown algorithm and impdef combined with memoization before any session exists.
    cpu_model(pauth_algorithm,pauth_memo=pauth_memo,
              pauth_memo_entries=pauth_memo_entries,pauth_memo_stats=pauth_memo_stats,
              webkit_timeout_diagnostic=webkit_timeout_diagnostic,sync_stats=sync_stats)
    window_refresh_value(display_refresh_hz,native_window)
    # Complete the eligibility checks before creating a new session or aliases.
    source = validate_source(source_run)
    current_build = require_fixed_qmp_build(QEMU, require_usb=True)
    if webkit_timeout_diagnostic:
        current_build={**current_build,'webkit_timeout_diagnostic':require_timeout_diagnostic_build(current_build,ROOT)}
    if sound_pcm_trace:
        current_build={**current_build,'pcm_trace':require_sound_pcm_trace_build(current_build)}
    if sound_queue_size is not None:
        current_build={**current_build,'sound_queue_size':require_sound_queue_size_build(current_build)}
    if net_control_queue_size is not None:
        current_build={**current_build,'net_control_queue_size':require_net_control_queue_size_build(current_build)}
    if pauth_memo_entries is not None or pauth_memo_stats:
        current_build={**current_build,'pauth_memo_capacity':require_pauth_memo_capacity_build(current_build,pauth_memo_entries)}
    if sync_stats:
        current_build={**current_build,'sync_stats':require_sync_stats_build(current_build)}
    process_contract = None
    if sample_current_process:
        try:
            from current_process import load_contract
        except ImportError as exc:
            raise ValueError('--sample-current-process requires private workspace observer modules') from exc
        process_contract = load_contract()
    logs = ROOT / 'runs/e05-installed-boot' / label
    private = ROOT / 'private/installed-boot' / label
    if logs.exists() or private.exists():
        raise FileExistsError('Cold-boot label already exists; inspect its result before another run')
    logs.mkdir(parents=True)
    private.mkdir(parents=True, mode=0o700)
    result_path = logs / 'results.json'
    report = {'schema': 1, 'label': label, 'source_run': source_run, 'source': source,
        'started_at_utc': datetime.now(timezone.utc).isoformat(), 'seconds_requested': seconds,
        'interactive_requested':interactive,'vcpus_requested':vcpus,
        'vcpu_affinity_requested':vcpu_affinity,
        'ram_gib_requested':ram_gib,'ram_bytes_requested':ram_gib * 1024**3,
        'pauth_algorithm_requested':pauth_algorithm,'pauth_memo_requested':pauth_memo,
            'pauth_memo_entries_requested':pauth_memo_entries,'pauth_memo_stats_requested':pauth_memo_stats,
        'sync_stats_requested':sync_stats,
        'pauth_memo_statistics':{'requested':pauth_memo_stats,'sample_count':0,'first':None,'latest':None},
        'state': 'starting', 'observation_completed': False, 'passed': False, 'errors': [], 'interrupted':False,
        'normal_os_boot_proven': False, 'recovery_os_boot_proven': False,
        'boot_classification': classify_boot({}), 'gui_verified': False, 'collection_success': False,
        'windowserver_observation': {'state': 'unknown', 'process_identity_verified': False},
        'presentation_requested': {'usb_input': usb_input, 'native_window': native_window,
                                   'gpu': gpu, 'gpu_icd': GPU_ICDS.get(gpu),
                                   'exact_half_blend':exact_half_blend,'display_refresh_hz':display_refresh_hz},
        'allow_unclean_storage_requested': allow_unclean_storage,
        'user_network_requested': user_network,
        'net_control_queue_size_requested':net_control_queue_size,
        'guest_files_requested': guest_files,
        'virtio_sound_requested': virtio_sound,
        'sound_backend_requested':sound_backend,
        'sound_pcm_trace_requested':sound_pcm_trace,
        'sound_queue_size_requested':sound_queue_size,
        'sound_streams_requested':sound_streams,
        'webkit_timeout_diagnostic':{**timeout_configuration(webkit_timeout_diagnostic),
            'arming_report':str(logs/'webkit-timeout-diagnostic.json') if webkit_timeout_diagnostic else None},
        'current_qemu_build': current_build, 'fresh_qemu_process': False,
        'fresh_ram_requested': True, 'ram_or_vm_snapshot_inherited': False,
        'inherited_persistent_targets': ['aux.raw', 'root.raw'],
        'restore_upload_performed': False, 'tss_requests': 0, 'boot_args_written': False,
        'observer_scope': {'ram_prefix_bytes': CAPTURE_BYTES, 'sample_interval_seconds': SAMPLE_SECONDS,
            'boot_args_abi': 'candidate Revision 3 / Version 2 only',
            'empty_scan_meaning': 'unknown, not evidence of absent boot',
            'installed_userland_image_contract_used': False},
        'kernel_observation': {'mode':kernel_observation,'monitoring_active':False,'state':'not_started',
            'capture_attempt_count':0,'last_capture_count':None,
            'last_capture_started_at_utc':None,'last_capture_completed_at_utc':None,
            'last_capture_elapsed_seconds':None,'stopped_at_utc':None,
            'retained_evidence_scope':'Most recent completed RAM capture; not a continuous guest monitor.',
            'after_stop_scope':'Later kernel panics, resets and boot reclassification are unobserved by this RAM observer. '
                               'QMP lifecycle reporting continues; RAM capture does not automatically resume.'},
        'block_io': {'sample_count': 0, 'first': None, 'latest': None,
            'scope': 'Per-frontend absolute counters and sampled deltas; shared-target frontends '
                     'are not summed. Zero counters do not exclude BDIF backend I/O.'},
        'scope': 'Fresh VM cold-boot evidence collection. Exit 0/passed describe '
                 'collection and cleanup, not normal OS boot, RecoveryOS identity or graphics.'}
    session = None
    file_service = None
    started = time.monotonic()

    def save(state=None):
        if state:
            report['state'] = state
        temporary = result_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        temporary.replace(result_path)

    save()
    try:
        if guest_files:
            file_service = GuestFileProcess(ROOT / 'experiments/E05-installed-boot/guest-diag',
                                            logs / 'guest-uploads',
                                            **({'shared_cache_identity':True} if webkit_timeout_diagnostic else {}))
            report['guest_files_service'] = file_service.report
            file_service.start()
            save()
        session = FirmwareSession(logs, trace_block_io=True, aux_size_mib=32,
            ctrr_model=True, pac_model=True, system_disk=False, blank_root_disk=False,
            resume_storage_run=source_run, virtio_usb=True, allow_unclean_storage=allow_unclean_storage,
            pauth_algorithm=pauth_algorithm, tcg_thread='multi', usb_input=usb_input,
            native_window=native_window, gpu_icd=GPU_ICDS.get(gpu), readonly_rsp=sample_current_process,
            user_network=user_network,exact_half_blend=exact_half_blend,display_refresh_hz=display_refresh_hz,
            vcpus=vcpus,pauth_memo=pauth_memo,ram_gib=ram_gib,virtio_sound=virtio_sound,sound_backend=sound_backend,
            sound_pcm_trace=sound_pcm_trace,sound_queue_size=sound_queue_size,sound_streams=sound_streams,
            pauth_memo_entries=pauth_memo_entries,pauth_memo_stats=pauth_memo_stats,
            vcpu_affinity=vcpu_affinity,webkit_timeout_diagnostic=webkit_timeout_diagnostic,
            net_control_queue_size=net_control_queue_size,sync_stats=sync_stats)
        with session as fw:
            retained = fw.retain_blank_storage(private / 'storage')
            if retained.get('qemu_binary_sha256') != current_build['binary_sha256']:
                raise ValueError('QEMU binary changed between preflight and current session')
            for name, target in source['targets'].items():
                current = retained['files'][name]
                if any(current[key] != target[key] for key in ('device', 'inode', 'bytes')):
                    raise ValueError('Cold-boot target is not the selected completed-install inode')
            report['fresh_qemu_process'] = True
            report['qemu_pid'] = fw.proc.pid
            report['execution'] = fw.execution_metadata()
            report['current_qemu_profile'] = qemu_profile(fw.args, report['execution'].get('presentation'),
                                                         report['execution'].get('network'),
                                                         pauth_memo=pauth_memo,ram_gib=ram_gib,
                                                         pauth_algorithm=pauth_algorithm,
                                                         virtio_sound=virtio_sound,audio=report['execution'].get('audio'),
                                                         sound_backend=sound_backend,run_directory=logs,
                                                         sound_pcm_trace=sound_pcm_trace,
                                                         sound_queue_size=sound_queue_size,
                                                         sound_streams=sound_streams,
                                                         pauth_memo_entries=pauth_memo_entries,pauth_memo_stats=pauth_memo_stats,
                                                         webkit_timeout_diagnostic=webkit_timeout_diagnostic,
                                                         net_control_queue_size=net_control_queue_size,
                                                         sync_stats=sync_stats)
            if report['current_qemu_profile']['vcpus']!=vcpus:
                raise ValueError('Actual QEMU CPU profile differs from the requested CPU count')
            report['storage'] = dict(fw.storage_info)
            report['retained_storage_report'] = str(logs / 'retained-storage.json')
            observer = KernelBootObserver(fw, private_dir=private)
            monitoring=report['kernel_observation']
            monitoring.update(monitoring_active=True,state='monitoring')
            screens = ScreenObserver(fw, logs)
            report['graphics_observation'] = screens.report
            process_sampler = None
            if process_contract is not None:
                from current_process import CurrentProcessSampler
                process_sampler = CurrentProcessSampler(fw, process_contract, logs / 'current-process.jsonl')
                report['current_process_observation'] = process_sampler.summary
            observing = time.monotonic()
            deadline, next_sample = None if interactive else observing + seconds, observing
            next_screen = observing
            next_process_sample = observing
            save('observing')
            try:
                while True:
                    if fw.proc.poll() is not None:
                        report['observation_end_reason'] = 'qemu_process_exited'
                        break
                    now = time.monotonic()
                    if now >= next_sample or (deadline is not None and now >= deadline):
                        state = None
                        if monitoring['monitoring_active']:
                            monitoring['capture_attempt_count']+=1
                            capture_started=datetime.now(timezone.utc).isoformat()
                            state = observer.capture()
                            captured_at=datetime.now(timezone.utc).isoformat()
                            monitoring.update(last_capture_count=state.get('capture_count'),
                                last_capture_started_at_utc=capture_started,last_capture_completed_at_utc=captured_at,
                                last_capture_elapsed_seconds=round(time.monotonic()-observing,3))
                            report['kernel_boot_state_report'] = str(logs / 'kernel-boot-state.json')
                            report['kernel_log'] = str(logs / 'kernel-boot.log')
                            report['current_ram_source'] = state.get('source')
                            report['observer_capture_count'] = state.get('capture_count')
                            report['kernel_runtime_evidence'] = state.get('runtime_evidence', {})
                            report['boot_classification'] = classify_boot(state, source=source,
                                kernel_log=logs / 'kernel-boot.log', context=report)
                            report['windowserver_observation'] = report['boot_classification'].get(
                                'windowserver_observation', {'state': 'unknown', 'process_identity_verified': False})
                            report['normal_os_boot_proven'] = report['boot_classification']['normal_os_boot_proven']
                            report['recovery_os_boot_proven'] = report['boot_classification']['recovery_os_boot_proven']
                            if (kernel_observation=='boot-only' and report['normal_os_boot_proven'] is True and
                                    not state.get('runtime_evidence',{}).get('kernel_panic_observed')):
                                monitoring.update(monitoring_active=False,state='stopped_after_normal_boot',
                                    stopped_at_utc=captured_at,
                                    retained_evidence_scope='Historical through the last successful capture only. '
                                        'Retained runtime/kernel-panic fields and boot proof do not describe later guest state.')
                                report['boot_classification'].update(evidence_freshness='historical_frozen_after_normal_boot',
                                    evidence_as_of_utc=captured_at,later_guest_state='unobserved_by_kernel_monitor')
                                # Preserve the last capture/log/proof, adding an explicit
                                # historical marker to the standalone observer state too.
                                historical={**state,'monitoring':dict(monitoring)}
                                state_path=logs/'kernel-boot-state.json'
                                temporary=state_path.with_suffix('.tmp')
                                temporary.write_text(json.dumps(historical,ensure_ascii=False,indent=2)+'\n')
                                temporary.replace(state_path)
                        sample = {'elapsed_seconds': round(now - observing, 3),
                                  'frontends': block_counters(fw.request('query-blockstats'))}
                        report['qemu_status'] = fw.request('query-status')
                        io = report['block_io']
                        if io['first'] is None:
                            io['first'] = sample
                        io['latest'] = sample
                        io['sample_count'] += 1
                        if pauth_memo_stats:
                            memo_sample=fw.capture_pauth_memo_statistics()
                            memo=report['pauth_memo_statistics']
                            if memo['first'] is None:memo['first']=memo_sample
                            memo['latest']=memo_sample
                            memo['sample_count']+=1
                            with (logs/'pauth-memo-statistics.jsonl').open('a') as stream:
                                stream.write(json.dumps(memo_sample)+'\n')
                        next_sample = now + SAMPLE_SECONDS
                        save()
                        if state is not None and state.get('runtime_evidence', {}).get('kernel_panic_observed'):
                            report['observation_end_reason'] = 'kernel_panic_observed'
                            break
                    if now >= next_screen:
                        screens.capture(time.monotonic() - observing)
                        next_screen = time.monotonic() + SCREEN_SECONDS
                        save()
                    if process_sampler is not None and now >= next_process_sample:
                        process_sampler.sample(time.monotonic() - observing)
                        next_process_sample = time.monotonic() + 30
                        save()
                    if deadline is not None and time.monotonic() >= deadline:
                        report['observation_end_reason'] = 'duration_elapsed'
                        break
                    time.sleep(.5 if deadline is None else min(.5, max(0, deadline - time.monotonic())))
            except (BrokenPipeError, ConnectionResetError, EOFError) as error:
                # QMP closed under a periodic request. After a guest-origin SHUTDOWN
                # that is the ordinary end of an interactive run, because QEMU closes
                # QMP a moment before its process exits (Runs 133, 135 and 137).
                # Without that event, or if QEMU stays alive, it is still a failure.
                if not fw.wait_for_guest_shutdown_exit():
                    raise
                report['observation_end_reason'] = 'qemu_process_exited'
                report['qmp_closed_before_process_exit'] = type(error).__name__
            report['observation_completed'] = True
            if fw.proc.poll() is None:
                screens.capture(time.monotonic() - observing, final=True)
            else:
                screens.report['final_capture_unavailable'] = 'QEMU process already exited'
            report['observation_elapsed_seconds'] = round(time.monotonic() - observing, 3)
            save('teardown')
    except Exception as error:
        report['errors'].append(f'{type(error).__name__}: {error}')
        if isinstance(error,FirmwareSessionInterrupted):
            report['interrupted']=True
            report['observation_end_reason']='controller_interrupted'
    finally:
        monitoring=report['kernel_observation']
        if monitoring['monitoring_active']:
            monitoring.update(monitoring_active=False,state='collection_ended',
                stopped_at_utc=datetime.now(timezone.utc).isoformat())
        if file_service is not None:
            file_service.close()
        if session is not None:
            # __enter__ also cleans up its own partial startup on failure.
            if vcpu_affinity is not None:
                report.setdefault('execution',{})['vcpu_affinity']=session.execution_metadata()['vcpu_affinity']
            report['qemu_exit_code'] = session.proc.returncode if session.proc is not None else None
            report['retained_storage'] = session.retained_storage
            report['qemu_reaped'] = bool(session.proc is not None and session.proc.poll() is not None)
            report['qmp_lifecycle'] = session.lifecycle_metadata()
            report['guest_shutdown_observed'] = report['qmp_lifecycle']['guest_shutdown_observed']
        else:
            report['guest_shutdown_observed'] = False
        report['guest_clean_shutdown_proven'] = False
        report['audio_capture']=inspect_wav_capture(sound,qemu_reaped=report.get('qemu_reaped') is True)
        report['bdif_backend_io'] = summarize_trace(logs / 'bdif-block-io.log')
        uart = logs / 'firmware-uart.log'
        if uart.exists():
            with uart.open('rb') as stream:
                stream.seek(max(0, uart.stat().st_size - 8192))
                report['uart_tail'] = stream.read(8192).decode('utf-8', errors='replace')
        first, latest = report['block_io']['first'], report['block_io']['latest']
        if first and latest:
            deltas = {}
            for name, current in latest['frontends'].items():
                baseline = first['frontends'].get(name, {})
                deltas[name] = {key: (current[key] - baseline[key]
                    if type(current.get(key)) is int and type(baseline.get(key)) is int
                    and current[key] >= baseline[key] else None) for key in COUNTERS}
            report['block_io']['sampled_deltas_by_frontend'] = deltas
        retained = report.get('retained_storage') or {}
        report['passed'] = (report['observation_completed'] and not report['errors'] and
            report.get('qemu_reaped') is True and report.get('qemu_exit_code') == 0 and
            retained.get('temporary_links_removed') is True and
            all(row.get('retained_identity_matches') is True for row in retained.get('files', {}).values()) and
            report.get('graphics_observation', {}).get('collection_success') is True)
        report['collection_success'] = report['passed']
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        save('complete' if report['passed'] else 'failed')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', required=True, help='Completed native installation in runs/e05-signed-restore')
    parser.add_argument('--label', default='installed-boot-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--seconds', type=int, help='Bounded observation duration, 1..3600 (default 600)')
    mode.add_argument('--interactive', action='store_true', help='No automatic deadline; existing guest/window/controller shutdown still cleans up the diagnostic session')
    parser.add_argument('--vcpus',type=int,choices=(2,4,8),default=2)
    parser.add_argument('--vcpu-affinity',help='Explicit host logical CPUs for owned vCPU threads only, e.g. 0-7 or 0,2,4-7; default unchanged')
    parser.add_argument('--ram-gib',type=int,choices=(4,8),default=4,
                        help='Explicit fresh RAM capacity in GiB; original installation source remains 4 GiB')
    parser.add_argument('--pauth-memo',action='store_true',
                        help='Opt in to per-vCPU exact PAC memoization on a compatible x-pauth-memo build')
    parser.add_argument('--pauth-memo-entries',type=int,choices=(64,1024,16384),
                        help='Explicit exact memo capacity; requires --pauth-memo and matching 0024 (also 0029 for 16384); default remains 64')
    parser.add_argument('--pauth-memo-stats',action='store_true',
                        help='Read aggregate per-vCPU memo counters; requires --pauth-memo and built 0024, default off')
    parser.add_argument('--pauth-algorithm',choices=PAUTH_ALGORITHMS,default='qarma5',
                        help='QEMU PAuth algorithm for this boot. The default qarma5 keeps the argv of every earlier run; impdef is non-cryptographic, cheaper per call and cannot be combined with --pauth-memo')
    parser.add_argument('--webkit-timeout-diagnostic',action='store_true',
                        help='Default-off fixed two-site 3s-to-30s diagnostic; requires matching0030+0031, metadata probe preflight and paused boolean arming; QEMU derives actual-PC slides, never an original-deadline performance pass')
    parser.add_argument('--sync-stats',action='store_true',
                        help='Default-off per-vCPU TCG BQL/exclusive trigger counters via x-sync-stats-json; requires built 0033')
    parser.add_argument('--usb-input', action='store_true', help='Expose the VM-local xHCI keyboard/tablet at PCI slot 4')
    parser.add_argument('--native-window', action='store_true', help='Request the Reims native presentation window')
    parser.add_argument('--gpu', choices=tuple(GPU_ICDS), help='Explicit single hardware Vulkan ICD for this session')
    parser.add_argument('--user-network', action='store_true',
                        help='Opt in to user NAT via standard virtio-net at PCI slot 5; no host forwards or physical bridge')
    parser.add_argument('--net-control-queue-size',type=int,choices=(64,512),
                        help='Explicit control-queue capacity only; requires --user-network and matching0032, omission retains the original64 default argv')
    parser.add_argument('--guest-files', action='store_true',
                        help='Own the loopback diagnostic file service; verify GET/PUT before QEMU starts (requires --user-network)')
    parser.add_argument('--virtio-sound', action='store_true',
                        help='Opt in to standard VirtIO sound at PCI slot 6; no host speaker/microphone')
    parser.add_argument('--sound-backend', choices=('none','wav'),default='none',
                        help='Sound output: none (default) or current-run guest-audio.wav; wav requires --virtio-sound')
    parser.add_argument('--sound-pcm-trace', action='store_true',
                        help='Bounded host PCM boundary evidence in this run; requires --virtio-sound, default off')
    parser.add_argument('--sound-queue-size', type=int, choices=(64,512),
                        help='Explicit VirtIO sound queue capacity; requires --virtio-sound and matching build; omission keeps 64')
    parser.add_argument('--sound-streams',type=int,choices=(1,2),
                        help='Explicit standard VirtIO stream count: 1 output-only or 2 output/input; requires --virtio-sound, default remains 2')
    parser.add_argument('--exact-half-blend', action='store_true',
                        help='Explicitly select the runtime half_blend precision model; preserves and records other inherited exact policies')
    parser.add_argument('--display-refresh-hz',type=float,
                        help='Explicit native-window offered refresh rate; a diagnostic workload setting, not an FPS claim')
    parser.add_argument('--sample-current-process', action='store_true',
                        help='Opt-in bounded owner-stop sampling of all verified configured CPUs after exact kernel source guards')
    parser.add_argument('--kernel-observation', choices=('continuous','boot-only'),default='continuous',
                        help='RAM observer policy: continuous (default), or stop only after jointly proven normal boot; other monitoring continues')
    parser.add_argument('--allow-unclean-storage', action='store_true',
                        help='Explicitly reuse the owned target after its latest observed QEMU abort; still rejects active targets')
    args = parser.parse_args(argv)
    try:
        report = run(args.source_run, args.label, args.seconds, usb_input=args.usb_input,
                     native_window=args.native_window, gpu=args.gpu,
                     sample_current_process=args.sample_current_process,
                     allow_unclean_storage=args.allow_unclean_storage,user_network=args.user_network,
                     net_control_queue_size=args.net_control_queue_size,
                     exact_half_blend=args.exact_half_blend,display_refresh_hz=args.display_refresh_hz,
                     vcpus=args.vcpus,interactive=args.interactive,pauth_memo=args.pauth_memo,ram_gib=args.ram_gib,
                     vcpu_affinity=args.vcpu_affinity,
                     kernel_observation=args.kernel_observation,
                     guest_files=args.guest_files,virtio_sound=args.virtio_sound,sound_backend=args.sound_backend,
                     sound_pcm_trace=args.sound_pcm_trace,
                     sound_queue_size=args.sound_queue_size,
                     sound_streams=args.sound_streams,
                     pauth_memo_entries=args.pauth_memo_entries,pauth_memo_stats=args.pauth_memo_stats,
                     pauth_algorithm=args.pauth_algorithm,
                     webkit_timeout_diagnostic=args.webkit_timeout_diagnostic,
                     sync_stats=args.sync_stats)
    except (ValueError, OSError, UIError) as error:
        print(f'COLD_BOOT_PREFLIGHT: {error}', file=sys.stderr)
        return 2
    print(json.dumps({'report': str(ROOT / 'runs/e05-installed-boot' / args.label / 'results.json'),
        'observation_completed': report['observation_completed'], 'passed': report['passed'],
        'normal_os_boot_proven': report['normal_os_boot_proven'],
        'boot_environment': report['boot_classification']['environment']}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
