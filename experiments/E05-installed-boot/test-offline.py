#!/usr/bin/env python3
"""Cold-boot orchestration fixtures and local loopback; no QEMU or guest execution."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import threading
import urllib.request
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('installed_boot_entry', HERE / 'run.py')
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)


def profile_args(root):
    args = [str(root / 'build/qemu-system-aarch64'), '-object',
        'memory-backend-memfd,id=ram,size=4G,share=on', '-M',
        'vmapple,memory-backend=ram,uuid=1,x-ctrr-model=on,x-pac-software-model=on',
        '-m', '4G', '-smp', '2', '-cpu', boot.cpu_model('qarma5'),
        '-accel', 'tcg,thread=multi', '-display', 'none']
    for name, index in (('aux', 0), ('root', 1)):
        path = root / 'temporary-work' / (name + '.raw')
        args += ['-drive', f'file={path},format=raw,if=pflash,index={index}',
                 '-drive', f'file={path},if=none,id={name},format=raw',
                 '-device', f'vmapple-virtio-blk-pci,variant={name},drive={name},share-rw=on']
    return args + ['-device', 'vmapple-virtio-usb-pci,id=kernelusb,chardev=kernelusbr,addr=0x3']


class ColdBootTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.label = 'completed-install-fixture'
        self.logs = self.root / 'runs/e05-signed-restore' / self.label
        self.storage = self.root / 'private/signed-restore' / self.label / 'storage'
        self.logs.mkdir(parents=True)
        self.storage.mkdir(parents=True, mode=0o700)
        self.patcher = patch.multiple(boot, ROOT=self.root, QEMU=self.root / 'build/qemu-system-aarch64')
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        files = {}
        for name, size in (('aux.raw', 32 * 1024**2), ('root.raw', boot.BLANK_ROOT_BYTES)):
            path = self.storage / name
            with path.open('xb') as stream:
                stream.truncate(size)
            value = path.stat()
            files[name] = {'path': str(path), 'device': value.st_dev, 'inode': value.st_ino,
                'bytes': size, 'owner_uid': os.geteuid(), 'retained_identity_matches': True}
        self.retained = {'directory': str(self.storage), 'profile': 'retained-root-target',
            'method': 'hard_links_to_owned_active_target_inodes', 'active_target_alias_not_snapshot': True,
            'qemu_reaped': True, 'qemu_exit_code': 0, 'temporary_links_removed': True,
            'teardown': 'qmp_quit', 'files': files, 'qemu_binary': str(boot.QEMU),
            'qemu_binary_sha256': 'b' * 64, 'clean_guest_shutdown_proven': False}
        self.args = profile_args(self.root)
        (self.logs / 'result.json').write_text(json.dumps({'qemu_argv': self.args}))
        native_log = self.logs / 'native-restore-client.log'
        native_log.write_text('Status: Restore Finished\n')
        self.installed = {'state': 'complete', 'passed': True, 'client_exit_code': 0,
            'client_reaped': True, 'timed_out': False, 'errors': [],
            'restore_finished_status_observed': True, 'client_log': boot.inspect_client_log(native_log)}
        self.save_source()

    def save_source(self):
        (self.logs / 'native-install-results.json').write_text(json.dumps(self.installed))
        (self.logs / 'retained-storage.json').write_text(json.dumps(self.retained))

    def test_noncompleted_install_rejected_before_session_or_output(self):
        for change in ({'passed': False}, {'client_exit_code': 1}, {'client_exit_code': False},
                       {'restore_finished_status_observed': False}, {'timed_out': True}):
            with self.subTest(change=change), patch.object(boot, 'FirmwareSession') as session:
                original = dict(self.installed)
                self.installed.update(change)
                self.save_source()
                with self.assertRaises(ValueError):
                    boot.run(self.label, 'cold', 1)
                session.assert_not_called()
                self.assertFalse((self.root / 'runs/e05-installed-boot').exists())
                self.installed = original

    def test_completion_log_and_retained_identity_are_rechecked(self):
        (self.logs / 'native-restore-client.log').write_text('no final status\n')
        with self.assertRaises(ValueError):
            boot.validate_source(self.label)
        (self.logs / 'native-restore-client.log').write_text('Status: Restore Finished\n')
        for change in ({'qemu_reaped': False}, {'qemu_exit_code': -9}, {'teardown': 'kill'}):
            original = copy.deepcopy(self.retained)
            self.retained.update(change)
            self.save_source()
            with self.assertRaises(ValueError):
                boot.validate_source(self.label)
            self.retained = original
        self.retained['files']['root.raw']['inode'] += 1
        self.save_source()
        with self.assertRaises(ValueError):
            boot.validate_source(self.label)

    def test_source_qmp_exit_is_not_guest_shutdown_or_os_identity(self):
        source = boot.validate_source(self.label)
        self.assertTrue(source['normal_qmp_exit'])
        self.assertFalse(source['guest_clean_shutdown_proven'])
        self.assertEqual(set(source['targets']), {'root.raw', 'aux.raw'})

    def test_kernel_and_boot_argument_cues_do_not_claim_normal_or_recovery_os(self):
        for command, bsd_root in (('', None), ('-v', 'disk0s1'), ('rd=md0 -restore', 'md0')):
            result = boot.classify_boot({'runtime_evidence': {'darwin_banner_observed': True,
                'darwin_banner': 'Darwin Kernel Version fixture', 'bsd_root': bsd_root},
                'boot_args_candidates': [{'command_line': command}]})
            self.assertEqual(result['environment'], 'unknown')
            self.assertFalse(result['normal_os_boot_proven'])
            self.assertFalse(result['recovery_os_boot_proven'])
        self.assertEqual(boot.classify_boot({})['environment'], 'unknown')

    def identity_fixture(self):
        group = '11111111-2222-3333-4444-555555555555'
        snapshot = 'com.apple.os.update-' + 'A' * 64
        source_log = self.root / 'source-identity.log'
        source_log.write_text('SENSITIVE TICKET SHOULD NOT BE COPIED\ncreated volume group ' + group + '\n' +
            'libpartition: creating system volume snapshot ' + snapshot + ' succeeded.\n')
        source = {'run': self.label, 'native_installation_verified': True,
                  'installed_volume_identity': boot.source_volume_identity(source_log)}
        context = {'source_run': self.label, 'fresh_qemu_process': True,
            'ram_or_vm_snapshot_inherited': False, 'restore_upload_performed': False, 'tss_requests': 0}
        lines = ['rooting via boot-uuid from /chosen: ' + group,
            'AppleFileSystemDriver: publishing boot-uuid-media=disk9s7 (Installed)', 'BSD root: disk9s7',
            'disk9s7 mounting snapshot w/snap_xid 12 and sblock oid 0x1', 'child ' + snapshot + '(0x1) published',
            'disk9s7 successfully validated on-disk root hash',
            'disk9s7 ROSV: apfs mounted RO and is the system volume of a volume group and mounted as the root fs',
            'vfs_switch_root : shuffling mount points : /System/Volumes/BaseSystem <-> / <-> System/Volumes/macOS',
            'vfs_switch_root : done shuffling mount points with error: 0', 'system_is_base_system: 1userspace boot',
            'vfs_switch_root : shuffling mount points : /System/Volumes/macOS <-> / <-> System/Volumes/BaseSystem',
            'vfs_switch_root : done shuffling mount points with error: 0', 'system_is_base_system: 0userspace boot',
            'startingInBaseSystem=NO startingInNormalOS=YES pivotingToNormalOS=NO',
            'TRM_BaseSystem = No(0).', 'TRM_Recovery = No(0).',
            'vm: WindowServer[99] triggered unnest of range', 'disk9s8 vol-uuid: ' + group]
        path = self.root / 'kernel-identity.log'
        path.write_text('\n'.join(lines) + '\n')
        return source, context, path, lines

    def test_joint_identity_matches_dynamic_volume_device_snapshot_and_process_cue(self):
        source, context, path, _ = self.identity_fixture()
        result = boot.classify_boot({}, source=source, kernel_log=path, context=context)
        self.assertTrue(result['normal_os_boot_proven'])
        self.assertEqual(result['environment'], 'normal_os')
        self.assertTrue(all(result['joint_checks'].values()))
        self.assertTrue(result['data_volume_uuid_support'])
        self.assertEqual(result['windowserver_observation']['pids'], [99])
        self.assertEqual(result['windowserver_observation']['current_liveness'], 'unknown')
        self.assertNotIn('SENSITIVE TICKET SHOULD NOT BE COPIED', json.dumps(result))

    def test_joint_identity_requires_full_chain_and_revokes_on_later_contradiction(self):
        source, context, path, lines = self.identity_fixture()
        variants = [lines[:7], lines[7:], [row for row in lines if 'ROSV:' not in row],
            [row.replace('11111111-2222', 'AAAAAAAA-2222') if index == 0 else row for index, row in enumerate(lines)],
            [row.replace('child com.apple.os.update-A', 'child com.apple.os.update-B') for row in lines],
            [row.replace('error: 0', 'error: 5') if index == 11 else row for index, row in enumerate(lines)],
            lines + ['system_is_base_system: 1userspace boot'],
            lines + ['startingInBaseSystem=YES startingInNormalOS=NO pivotingToNormalOS=NO'],
            lines + ['TRM_Recovery = Yes(1).'],
            lines + ['vfs_switch_root : shuffling mount points : unsupported future pivot']]
        for index, rows in enumerate(variants):
            with self.subTest(index=index):
                path.write_text('\n'.join(rows) + '\n')
                result = boot.classify_boot({}, source=source, kernel_log=path, context=context)
                self.assertFalse(result['normal_os_boot_proven'])
                self.assertEqual(result['environment'], 'unknown')
                self.assertFalse(result['recovery_os_boot_proven'])
        path.write_text('\n'.join(lines) + '\n')
        for change in ({'fresh_qemu_process': False}, {'tss_requests': 1}):
            result = boot.classify_boot({}, source=source, kernel_log=path, context={**context, **change})
            self.assertFalse(result['normal_os_boot_proven'])

    def run_kernel_observation_fixture(self, sequence, mode=None):
        source=boot.validate_source(self.label)
        identity,_,_,lines=self.identity_fixture()
        source['installed_volume_identity']=identity['installed_volume_identity']
        calls={};clock=[0];captures=[]
        label='kernel-monitor-'+(mode or 'default')+'-'+sequence[-1]
        logs=self.root/'runs/e05-installed-boot'/label
        def capture():
            captures.append(clock[0])
            phase=sequence[min(len(captures)-1,len(sequence)-1)]
            if phase=='error':raise ValueError('fixture RAM capture failed')
            observed=lines[:7] if phase=='unknown' else lines+(['TRM_Recovery = Yes(1).'] if phase=='contradiction' else [])
            (logs/'kernel-boot.log').write_text('\n'.join(observed)+'\n')
            state={'capture_count':len(captures),'source':{'kind':'offline-same-VM-fixture'},
                   'runtime_evidence':{'kernel_panic_observed':phase=='panic'},'boot_args_candidates':[],
                   'retained_capture_marker':'preserve this capture'}
            (logs/'kernel-boot-state.json').write_text(json.dumps(state))
            return state
        service=SimpleNamespace(report={'ready':True},
            start=lambda:calls.update(files_started_at=clock[0]),
            close=lambda:calls.update(files_closed_at=clock[0]))
        fake_time=SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda delay:clock.__setitem__(0,clock[0]+15))
        with patch.object(boot,'validate_source',return_value=source), \
             patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=SimpleNamespace(capture=capture)), \
             patch.object(boot,'GuestFileProcess',return_value=service), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}), \
             patch.object(boot,'require_pauth_memo_capacity_build',return_value={'patch':boot.PAUTH_CAPACITY_PATCH}), \
             patch.object(boot,'time',fake_time):
            result=boot.run(self.label,label,45,user_network=True,guest_files=True,
                            pauth_memo=True,pauth_memo_entries=1024,pauth_memo_stats=True,
                            **({} if mode is None else {'kernel_observation':mode}))
        return result,calls,captures,logs

    def test_boot_only_stops_after_joint_proof_and_keeps_other_observers_and_cleanup(self):
        result,calls,captures,logs=self.run_kernel_observation_fixture(
            ['unknown','normal','contradiction','panic'],'boot-only')
        self.assertTrue(result['passed'])
        self.assertEqual(captures,[0,15])
        self.assertTrue(result['normal_os_boot_proven'])
        self.assertTrue(all(result['boot_classification']['joint_checks'].values()))
        self.assertEqual(result['boot_classification']['evidence_freshness'],'historical_frozen_after_normal_boot')
        monitor=result['kernel_observation']
        self.assertFalse(monitor['monitoring_active'])
        self.assertEqual(monitor['state'],'stopped_after_normal_boot')
        self.assertEqual(monitor['last_capture_count'],2)
        self.assertEqual(monitor['capture_attempt_count'],2)
        self.assertEqual(monitor['last_capture_elapsed_seconds'],15)
        self.assertEqual(monitor['stopped_at_utc'],monitor['last_capture_completed_at_utc'])
        self.assertIn('Historical',monitor['retained_evidence_scope'])
        self.assertIn('unobserved',monitor['after_stop_scope'])
        saved=json.loads((logs/'kernel-boot-state.json').read_text())
        self.assertEqual(saved['monitoring'],monitor)
        self.assertEqual(saved['retained_capture_marker'],'preserve this capture')
        self.assertEqual(saved['capture_count'],2)
        self.assertFalse(result['kernel_runtime_evidence']['kernel_panic_observed'])
        self.assertEqual(calls['qmp'].count('query-status'),4)
        self.assertEqual(calls['qmp'].count('query-blockstats'),4)
        self.assertEqual(result['block_io']['sample_count'],4)
        self.assertEqual(calls['memo_reads'],4)
        self.assertEqual(result['pauth_memo_statistics']['sample_count'],4)
        self.assertEqual(calls['files_closed_at'],45)
        self.assertTrue(calls['closed'])
        self.assertTrue(result['qemu_reaped'])
        self.assertTrue(result['graphics_observation']['final_capture_observed'])
        self.assertEqual(result['qmp_lifecycle']['source'],'offline fixture')
        self.assertIsNone(calls['kwargs']['vcpu_affinity'])
        self.assertNotIn('kernel_observation',calls['kwargs'])

    def test_default_continuous_keeps_capturing_and_revokes_later_contradiction(self):
        result,calls,captures,_=self.run_kernel_observation_fixture(['unknown','normal','contradiction'])
        self.assertTrue(result['passed'])
        self.assertEqual(captures,[0,15,30,45])
        self.assertFalse(result['normal_os_boot_proven'])
        self.assertEqual(result['kernel_observation']['mode'],'continuous')
        self.assertEqual(result['kernel_observation']['last_capture_count'],4)
        self.assertNotIn('evidence_freshness',result['boot_classification'])
        self.assertEqual(calls['qmp'].count('query-status'),4)

    def test_boot_only_keeps_capturing_without_joint_proof(self):
        result,_,captures,_=self.run_kernel_observation_fixture(['unknown'],'boot-only')
        self.assertTrue(result['passed'])
        self.assertEqual(captures,[0,15,30,45])
        self.assertFalse(result['normal_os_boot_proven'])
        self.assertEqual(result['kernel_observation']['state'],'collection_ended')
        self.assertEqual(result['kernel_observation']['last_capture_count'],4)

    def test_boot_only_capture_error_and_observed_panic_do_not_mark_normal_stop(self):
        result,calls,captures,_=self.run_kernel_observation_fixture(['unknown','error'],'boot-only')
        self.assertFalse(result['passed'])
        self.assertEqual(captures,[0,15])
        self.assertEqual(result['kernel_observation']['state'],'collection_ended')
        self.assertEqual(result['kernel_observation']['capture_attempt_count'],2)
        self.assertEqual(result['kernel_observation']['last_capture_count'],1)
        self.assertFalse(result['normal_os_boot_proven'])
        self.assertTrue(calls['closed'])
        self.assertTrue(any('fixture RAM capture failed' in error for error in result['errors']))
        result,_,captures,_=self.run_kernel_observation_fixture(['panic'],'boot-only')
        self.assertEqual(captures,[0])
        self.assertEqual(result['observation_end_reason'],'kernel_panic_observed')
        self.assertEqual(result['kernel_observation']['state'],'collection_ended')

    def test_kernel_observation_cli_is_optional_and_independent_of_affinity(self):
        result={'observation_completed':True,'passed':True,'normal_os_boot_proven':False,
                'boot_classification':{'environment':'unknown'}}
        with patch.object(boot,'run',return_value=result) as run:
            boot.main(['--source-run',self.label,'--kernel-observation','boot-only','--vcpu-affinity','0-7'])
            self.assertEqual(run.call_args.kwargs['kernel_observation'],'boot-only')
            self.assertEqual(run.call_args.kwargs['vcpu_affinity'],'0-7')
            boot.main(['--source-run',self.label])
            self.assertEqual(run.call_args.kwargs['kernel_observation'],'continuous')
            self.assertIsNone(run.call_args.kwargs['vcpu_affinity'])
        with patch.object(boot,'validate_source') as source,self.assertRaises(ValueError):
            boot.run(self.label,'invalid-kernel-mode',1,kernel_observation='off')
        source.assert_not_called()

    def test_prior_cold_boot_aliases_are_not_installation_snapshots(self):
        directory = self.root / 'runs/e05-installed-boot/prior-cold'
        directory.mkdir(parents=True)
        (directory / 'retained-storage.json').write_text(json.dumps(self.retained))
        source = boot.validate_source(self.label)
        aliases = source['storage_alias_provenance']
        self.assertFalse(aliases['snapshot'])
        self.assertEqual(aliases['matching_cold_boot_alias_count'], 1)
        self.assertEqual(aliases['latest_alias_records'][0]['run'], 'prior-cold')
        self.assertIn('metadata_at_validation', source['targets']['root.raw'])

    def test_source_two_current_selected_and_interactive_stop(self):
        for count in (4,8):
            bad=list(self.args);bad[bad.index('-smp')+1]=str(count)
            (self.logs/'result.json').write_text(json.dumps({'qemu_argv':bad}))
            with self.assertRaises(ValueError):boot.validate_source(self.label)
        (self.logs/'result.json').write_text(json.dumps({'qemu_argv':self.args}))
        calls={};observer=SimpleNamespace(capture=lambda:{'capture_count':1,'runtime_evidence':{}})
        with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=observer), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}):
            for count in (4,8):
                result=boot.run(self.label,f'cpu-{count}',1,vcpus=count)
                self.assertTrue(result['passed']);self.assertEqual(result['source']['qemu']['profile']['vcpus'],2)
                self.assertEqual(result['current_qemu_profile']['vcpus'],count)
        calls={};clock=[0];captures=[]
        def capture():
            captures.append(clock[0])
            if len(captures)==3:raise boot.FirmwareSessionInterrupted(15)
            return {'capture_count':len(captures),'runtime_evidence':{}}
        fake_time=SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda delay:clock.__setitem__(0,clock[0]+4000))
        with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=SimpleNamespace(capture=capture)), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}), \
             patch.object(boot,'time',fake_time):
            result=boot.run(self.label,'interactive',interactive=True)
        self.assertEqual(captures,[0,4000,8000]);self.assertIsNone(result['seconds_requested'])
        self.assertTrue(result['interrupted']);self.assertTrue(calls['closed'])
        self.assertFalse(result['observation_completed']);self.assertFalse(result['passed']);self.assertFalse(result['gui_verified'])
        with self.assertRaises(ValueError):boot.run(self.label,'bad',600,interactive=True)

    def test_pauth_memo_requires_matching_request_and_keeps_install_source_baseline(self):
        selected=list(self.args)
        selected[selected.index('-cpu')+1]=boot.cpu_model('qarma5',pauth_memo=True)
        with self.assertRaises(ValueError):boot.qemu_profile(selected)
        with self.assertRaises(ValueError):boot.qemu_profile(self.args,pauth_memo=True)
        self.assertTrue(boot.qemu_profile(selected,pauth_memo=True)['pauth_memo_requested'])
        (self.logs/'result.json').write_text(json.dumps({'qemu_argv':selected}))
        with self.assertRaises(ValueError):boot.validate_source(self.label)
        (self.logs/'result.json').write_text(json.dumps({'qemu_argv':self.args}))
        calls={};observer=SimpleNamespace(capture=lambda:{'capture_count':1,'runtime_evidence':{}})
        with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=observer), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}):
            result=boot.run(self.label,'memo-current',1,pauth_memo=True)
        self.assertTrue(result['passed'])
        self.assertTrue(calls['kwargs']['pauth_memo'])
        self.assertTrue(result['pauth_memo_requested'])
        self.assertTrue(result['current_qemu_profile']['pauth_memo_requested'])
        self.assertFalse(result['source']['qemu']['profile']['pauth_memo_requested'])
        self.assertEqual(result['current_qemu_profile']['cpu'],boot.cpu_model('qarma5',pauth_memo=True))

    def test_pauth_algorithm_is_selectable_and_impdef_excludes_memoization(self):
        selected=list(self.args)
        selected[selected.index('-cpu')+1]=boot.cpu_model('impdef')
        with self.assertRaises(ValueError):boot.qemu_profile(selected)
        with self.assertRaises(ValueError):boot.qemu_profile(self.args,pauth_algorithm='impdef')
        self.assertEqual(boot.qemu_profile(selected,pauth_algorithm='impdef')['pauth_algorithm'],'impdef')
        self.assertEqual(boot.qemu_profile(self.args)['pauth_algorithm'],'qarma5')
        for bad in ({'pauth_algorithm':'impdef','pauth_memo':True},{'pauth_algorithm':'apple'}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):boot.run(self.label,'bad-algorithm',1,**bad)
        calls={};observer=SimpleNamespace(capture=lambda:{'capture_count':1,'runtime_evidence':{}})
        with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=observer), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}):
            result=boot.run(self.label,'impdef-current',1,pauth_algorithm='impdef')
        self.assertTrue(result['passed'])
        self.assertEqual(calls['kwargs']['pauth_algorithm'],'impdef')
        self.assertEqual(result['pauth_algorithm_requested'],'impdef')
        self.assertEqual(result['current_qemu_profile']['pauth_algorithm'],'impdef')
        self.assertEqual(result['current_qemu_profile']['cpu'],boot.cpu_model('impdef'))
        self.assertEqual(result['source']['qemu']['profile']['pauth_algorithm'],'qarma5')

    def test_pauth_memo_capacity_cli_profile_and_periodic_counter_consumer(self):
        for entries in (64,1024,16384):
            calls={};observer=SimpleNamespace(capture=lambda:{'capture_count':1,'runtime_evidence':{}})
            with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
                 patch.object(boot,'KernelBootObserver',return_value=observer), \
                 patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}), \
                 patch.object(boot,'require_pauth_memo_capacity_build',return_value={'patch':boot.PAUTH_CAPACITY_PATCH}):
                result=boot.run(self.label,f'memo-{entries}',1,pauth_memo=True,
                                pauth_memo_entries=entries,pauth_memo_stats=True)
            self.assertTrue(result['passed'])
            self.assertEqual(calls['kwargs']['pauth_memo_entries'],entries)
            self.assertTrue(calls['kwargs']['pauth_memo_stats'])
            self.assertEqual(result['current_qemu_profile']['pauth_memo_entries_requested'],entries)
            self.assertEqual(result['pauth_memo_statistics']['latest']['vcpus'][0]['hits'],17)
            lines=(self.root/f'runs/e05-installed-boot/memo-{entries}/pauth-memo-statistics.jsonl').read_text().splitlines()
            self.assertEqual(len(lines),calls['memo_reads'])
            self.assertEqual(json.loads(lines[-1]),result['pauth_memo_statistics']['latest'])
            selected=list(self.args)
            selected[selected.index('-cpu')+1]=boot.cpu_model('qarma5',pauth_memo=True,
                pauth_memo_entries=entries,pauth_memo_stats=True)
            with self.assertRaises(ValueError):
                boot.qemu_profile(selected,pauth_memo=True,pauth_memo_entries=entries)
            with patch.object(boot,'run',return_value=result) as run:
                boot.main(['--source-run',self.label,'--pauth-memo','--pauth-memo-entries',str(entries),'--pauth-memo-stats'])
                self.assertEqual(run.call_args.kwargs['pauth_memo_entries'],entries)
                self.assertTrue(run.call_args.kwargs['pauth_memo_stats'])
        with patch.object(boot,'validate_source') as source:
            for options in ({'pauth_memo_entries':1024},{'pauth_memo_stats':True},
                            {'pauth_memo':True,'pauth_memo_entries':128},
                            {'pauth_memo':True,'pauth_memo_stats':1}):
                with self.subTest(options=options),self.assertRaises(ValueError):
                    boot.run(self.label,'invalid-memo-capacity',1,**options)
            source.assert_not_called()

    def test_pauth_memo_capacity_build_guard(self):
        patchpath=self.root/boot.PAUTH_CAPACITY_PATCH
        patchpath.parent.mkdir(parents=True)
        patchpath.write_bytes(b'PAC capacity implementation')
        manifest=self.root/'runs/qemu-bdif-usb-build.json'
        record={'binary_sha256':'b'*64,'patches':[],'patch_sha256':{}}
        manifest.write_text(json.dumps(record))
        with self.assertRaises(ValueError):boot.require_pauth_memo_capacity_build({'binary_sha256':'b'*64})
        record['patches']=[boot.PAUTH_CAPACITY_PATCH]
        record['patch_sha256'][boot.PAUTH_CAPACITY_PATCH]=boot.hashlib.sha256(patchpath.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(record))
        self.assertEqual(boot.require_pauth_memo_capacity_build({'binary_sha256':'b'*64})['patch'],boot.PAUTH_CAPACITY_PATCH)
        large=self.root/boot.PAUTH_LARGE_CAPACITY_PATCH
        large.write_bytes(b'16384-entry capacity implementation')
        for entries in (64,1024):
            self.assertNotIn('large_capacity',boot.require_pauth_memo_capacity_build({'binary_sha256':'b'*64},entries))
        with self.assertRaises(ValueError):boot.require_pauth_memo_capacity_build({'binary_sha256':'b'*64},16384)
        record['patches'].append(boot.PAUTH_LARGE_CAPACITY_PATCH)
        record['patch_sha256'][boot.PAUTH_LARGE_CAPACITY_PATCH]=boot.hashlib.sha256(large.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(record))
        checked=boot.require_pauth_memo_capacity_build({'binary_sha256':'b'*64},16384)
        self.assertEqual(checked['large_capacity']['entries'],16384)
        self.assertEqual(checked['large_capacity']['patch_sha256'],record['patch_sha256'][boot.PAUTH_LARGE_CAPACITY_PATCH])
        large.write_bytes(b'changed large-capacity implementation')
        with self.assertRaises(ValueError):boot.require_pauth_memo_capacity_build({'binary_sha256':'b'*64},16384)
        self.assertNotIn('large_capacity',boot.require_pauth_memo_capacity_build({'binary_sha256':'b'*64},1024))
        with self.assertRaises(ValueError):boot.require_pauth_memo_capacity_build({'binary_sha256':'c'*64})
        patchpath.write_bytes(b'changed source')
        with self.assertRaises(ValueError):boot.require_pauth_memo_capacity_build({'binary_sha256':'b'*64})

    def test_ram_profile_matches_request_and_preserves_install_source(self):
        selected=list(self.args)
        selected[selected.index('-m')+1]='8G'
        selected[selected.index('-object')+1]='memory-backend-memfd,id=ram,size=8G,share=on'
        with self.assertRaises(ValueError):boot.qemu_profile(selected)
        with self.assertRaises(ValueError):boot.qemu_profile(self.args,ram_gib=8)
        profile=boot.qemu_profile(selected,ram_gib=8)
        self.assertEqual((profile['memory'],profile['ram_bytes']),('8G',8 * 1024**3))
        for index in (selected.index('-m')+1,selected.index('-object')+1):
            mixed=list(selected);mixed[index]=self.args[index]
            with self.subTest(index=index),self.assertRaises(ValueError):boot.qemu_profile(mixed,ram_gib=8)
        duplicate=selected+['-object','memory-backend-memfd,id=extra,size=4G,share=on']
        with self.assertRaises(ValueError):boot.qemu_profile(duplicate,ram_gib=8)
        (self.logs/'result.json').write_text(json.dumps({'qemu_argv':selected}))
        with self.assertRaises(ValueError):boot.validate_source(self.label)
        (self.logs/'result.json').write_text(json.dumps({'qemu_argv':self.args}))
        calls={};observer=SimpleNamespace(capture=lambda:{'capture_count':1,'runtime_evidence':{}})
        with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=observer), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}):
            result=boot.run(self.label,'ram-current',1,ram_gib=8)
        self.assertTrue(result['passed'])
        self.assertEqual(calls['kwargs']['ram_gib'],8)
        self.assertEqual((result['ram_gib_requested'],result['ram_bytes_requested']),(8,8 * 1024**3))
        self.assertEqual(result['current_qemu_profile']['memory'],'8G')
        self.assertEqual(result['source']['qemu']['profile']['memory'],'4G')
        with patch.object(boot,'validate_source') as source:
            for gib in (True,0,2,6,16,4.0,None,'8'):
                with self.subTest(gib=gib),self.assertRaises(ValueError):
                    boot.run(self.label,'bad-ram',1,ram_gib=gib)
                with self.assertRaises(ValueError):boot.qemu_profile(self.args,ram_gib=gib)
            source.assert_not_called()
        self.assertFalse((self.root/'runs/e05-installed-boot/bad-ram').exists())

    def test_ram_cli_is_passed_to_runner(self):
        result={'observation_completed':False,'passed':False,'normal_os_boot_proven':False,
                'boot_classification':{'environment':'unknown'}}
        with patch.object(boot,'run',return_value=result) as run:
            self.assertEqual(boot.main(['--source-run',self.label,'--label','ram-cli','--ram-gib','8']),1)
            self.assertEqual(run.call_args.kwargs['ram_gib'],8)

    def fake_session_factory(self, calls):
        fixture = self
        class Session:
            def __init__(self, logs, **kwargs):
                calls['kwargs'] = kwargs
                self.logs, self.args = logs, list(fixture.args)
                self.args[self.args.index('-smp')+1]=str(kwargs.get('vcpus',2))
                memory=f"{kwargs.get('ram_gib',4)}G"
                self.args[self.args.index('-m')+1]=memory
                self.args[self.args.index('-object')+1]=f'memory-backend-memfd,id=ram,size={memory},share=on'
                self.args[self.args.index('-cpu')+1]=boot.cpu_model(kwargs.get('pauth_algorithm','qarma5'),pauth_memo=kwargs.get('pauth_memo',False),
                    pauth_memo_entries=kwargs.get('pauth_memo_entries'),pauth_memo_stats=kwargs.get('pauth_memo_stats',False),
                    webkit_timeout_diagnostic=kwargs.get('webkit_timeout_diagnostic',False))
                if kwargs.get('usb_input'):
                    self.args += ['-device', 'qemu-xhci,id=input-xhci,addr=0x4',
                        '-device', 'usb-kbd,id=input-keyboard,bus=input-xhci.0,port=1',
                        '-device', 'usb-tablet,id=input-tablet,bus=input-xhci.0,port=2']
                self.presentation = {'native_window_requested': kwargs.get('native_window', False),
                    'usb_input_requested': kwargs.get('usb_input', False),
                    'reims_vgpu_window': '1' if kwargs.get('native_window') else '0',
                    'vulkan_icd': kwargs.get('gpu_icd')}
                self.network = {'requested':kwargs.get('user_network',False),'guest_connectivity':'unknown',
                                'ascending_capabilities_requested':kwargs.get('user_network',False)}
                if kwargs.get('user_network'):
                    self.args += ['-netdev','user,id=guestnet','-device',
                        'virtio-net-pci,netdev=guestnet,id=guestnet-pci,disable-legacy=on,addr=0x5,x-ascending-capabilities=on'
                        +(f",ctrl_queue_size={kwargs['net_control_queue_size']}" if kwargs.get('net_control_queue_size') is not None else '')]
                self.network['control_queue_size']={'requested':kwargs.get('net_control_queue_size'),'default':64,
                    'configured_maximum':(kwargs.get('net_control_queue_size') or 64) if kwargs.get('user_network') else None,
                    'actual_device_arguments':[self.args[i+1] for i,arg in enumerate(self.args[:-1])
                        if arg=='-device' and self.args[i+1].split(',',1)[0]=='virtio-net-pci'],
                    'guest_selected':None,'runtime_verified':False}
                self.audio = boot.virtio_sound_configuration(kwargs.get('virtio_sound',False),
                    kwargs.get('sound_backend','none'),logs,pcm_trace=kwargs.get('sound_pcm_trace',False),
                    queue_size=kwargs.get('sound_queue_size'),streams=kwargs.get('sound_streams'))
                if kwargs.get('virtio_sound'):
                    self.args += ['-audiodev',boot.virtio_sound_backend_argument(self.audio),
                                  '-device',boot.virtio_sound_device_argument(self.audio)]
                self.proc = SimpleNamespace(pid=900001, returncode=None)
                self.proc.poll = lambda: self.proc.returncode
                self.retained_storage = None
                self.storage_info = {'profile': 'retained-root-target'}
            def __enter__(self):
                calls['entered'] = True
                return self
            def __exit__(self, *_):
                calls['closed'] = True
                self.proc.returncode = 0
                self.retained_storage.update(qemu_reaped=True, qemu_exit_code=0,
                    temporary_links_removed=True, teardown='qmp_quit')
            def retain_blank_storage(self, destination):
                destination.mkdir(mode=0o700)
                files = {}
                for name, source in fixture.retained['files'].items():
                    os.link(source['path'], destination / name)
                    files[name] = {**source, 'path': str(destination / name)}
                self.retained_storage = {'files': files, 'qemu_binary_sha256': 'b' * 64}
                return self.retained_storage
            def execution_metadata(self):
                return {'qemu_argv': self.args, 'tcg_thread_requested': 'multi',
                        'presentation': self.presentation,'network':self.network,'audio':self.audio,
                        'vcpu_affinity':{'requested':calls['kwargs'].get('vcpu_affinity'),
                                         'verified':False,'verification':'offline_fixture'}}
            def capture_pauth_memo_statistics(self):
                calls['memo_reads']=calls.get('memo_reads',0)+1
                return {'requested':True,'state':'observed','entries_per_cpu':calls['kwargs']['pauth_memo_entries'],
                        'vcpus':[{'cpu_index':0,'hits':17,'misses':4}]}
            def lifecycle_metadata(self):
                return {'guest_shutdown_observed':False,'source':'offline fixture'}
            def request(self, command, arguments=None):
                calls.setdefault('qmp', []).append(command)
                if command == 'screendump':
                    Path(arguments['filename']).write_bytes(b'P6\n1 1\n255\n\0\0\0')
                    return {}
                if command == 'query-status':
                    return {'running': True, 'status': 'running'}
                if command == 'query-blockstats':
                    return [{'device': key, 'stats': {name: 1 for name in boot.COUNTERS}}
                            for key in boot.FRONTENDS]
                raise AssertionError('Unexpected device, reset, upload or control request')
        return Session

    def test_run_reuses_only_targets_and_collects_unknown_with_normal_cleanup(self):
        calls = {}
        class Observer:
            def __init__(self, session, *, private_dir):
                calls['observer_private'] = private_dir
            def capture(self):
                return {'capture_count': 1, 'source': {'kind': 'offline-fixture'},
                        'runtime_evidence': {}, 'boot_args_candidates': []}
        with patch.object(boot, 'FirmwareSession', self.fake_session_factory(calls)), \
             patch.object(boot, 'KernelBootObserver', Observer), \
             patch.object(boot, 'require_fixed_qmp_build', return_value={'binary_sha256': 'b' * 64}), \
             patch('subprocess.Popen') as process:
            result = boot.run(self.label, 'cold', 1)
        process.assert_not_called()
        self.assertTrue(result['passed'])
        self.assertTrue(result['observation_completed'])
        self.assertFalse(result['normal_os_boot_proven'])
        self.assertEqual(result['boot_classification']['environment'], 'unknown')
        self.assertTrue(calls['closed'])
        self.assertEqual(set(calls['qmp']), {'query-blockstats', 'query-status', 'screendump'})
        self.assertTrue(result['graphics_observation']['final_capture_observed'])
        self.assertEqual(result['graphics_observation']['retained_file_count'], 1)
        self.assertFalse(result['gui_verified'])
        self.assertFalse(result['guest_shutdown_observed'])
        self.assertFalse(result['guest_clean_shutdown_proven'])
        self.assertEqual(calls['kwargs'], {'trace_block_io': True, 'aux_size_mib': 32,
            'ctrr_model': True, 'pac_model': True, 'system_disk': False, 'blank_root_disk': False,
            'resume_storage_run': self.label, 'virtio_usb': True, 'allow_unclean_storage': False,
            'pauth_algorithm': 'qarma5', 'tcg_thread': 'multi',
            'usb_input': False, 'native_window': False, 'gpu_icd': None, 'readonly_rsp': False,
            'user_network':False,'exact_half_blend':False,'display_refresh_hz':None,'vcpus':2,'pauth_memo':False,'ram_gib':4,
            'virtio_sound':False,'sound_backend':'none','sound_pcm_trace':False,'sound_queue_size':None,'sound_streams':None,
            'pauth_memo_entries':None,'pauth_memo_stats':False,'vcpu_affinity':None,
            'webkit_timeout_diagnostic':False,'net_control_queue_size':None})
        for name in self.retained['files']:
            self.assertTrue(os.path.samefile(self.storage / name,
                self.root / 'private/installed-boot/cold/storage' / name))
        self.assertFalse(result['restore_upload_performed'])
        self.assertEqual(result['tss_requests'], 0)
        self.assertFalse(result['boot_args_written'])

    def test_natural_guest_exit_does_not_relax_final_capture_or_collection(self):
        calls={}
        class NaturalExit(self.fake_session_factory(calls)):
            def __enter__(session):
                super().__enter__();session.proc.returncode=0
                return session
            def lifecycle_metadata(session):
                return {'source':'offline fixture','guest_shutdown_observed':True}
            def __exit__(session,*args):
                super().__exit__(*args)
                session.retained_storage.update(teardown='already_exited_or_not_started',guest_shutdown_observed=True)
        with patch.object(boot,'FirmwareSession',NaturalExit), \
             patch.object(boot,'KernelBootObserver'), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}):
            result=boot.run(self.label,'natural-exit',1)
        self.assertTrue(result['guest_shutdown_observed'])
        self.assertFalse(result['guest_clean_shutdown_proven'])
        self.assertFalse(result['passed'])
        self.assertFalse(result['collection_success'])
        self.assertEqual(result['graphics_observation']['final_capture_unavailable'],'QEMU process already exited')
        self.assertNotIn('screendump',calls.get('qmp',[]))

    def run_with_qmp_closed_under_a_sample(self, error, guest_shutdown):
        calls={}
        class ClosedQmp(self.fake_session_factory(calls)):
            def request(session, command, arguments=None):
                calls.setdefault('qmp', []).append(command)
                raise error
            def wait_for_guest_shutdown_exit(session):
                calls['waits']=calls.get('waits',0)+1
                if guest_shutdown:session.proc.returncode=0
                return guest_shutdown
            def lifecycle_metadata(session):
                return {'source':'offline fixture','guest_shutdown_observed':guest_shutdown}
        observer=SimpleNamespace(capture=lambda:{'capture_count':1,'source':{'kind':'offline-fixture'},
                                                 'runtime_evidence':{},'boot_args_candidates':[]})
        with patch.object(boot,'FirmwareSession',ClosedQmp), \
             patch.object(boot,'KernelBootObserver',return_value=observer), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}):
            return boot.run(self.label,f'closed-{type(error).__name__}-{guest_shutdown}'.lower(),1),calls

    def test_qmp_closing_after_guest_shutdown_ends_the_observation_normally(self):
        # Runs 133, 135 and 137: the periodic request met a closed QMP socket
        # while QEMU was exiting after the guest's own shutdown.
        for error in (BrokenPipeError(32,'Broken pipe'),ConnectionResetError(104,'reset'),EOFError('QMP closed')):
            with self.subTest(error=type(error).__name__):
                result,calls=self.run_with_qmp_closed_under_a_sample(error,True)
                self.assertEqual(result['errors'],[])
                self.assertEqual(result['observation_end_reason'],'qemu_process_exited')
                self.assertEqual(result['qmp_closed_before_process_exit'],type(error).__name__)
                self.assertTrue(result['observation_completed'])
                self.assertTrue(result['guest_shutdown_observed'])
                self.assertEqual(result['graphics_observation']['final_capture_unavailable'],'QEMU process already exited')
                self.assertEqual((calls['qmp'],calls['waits']),(['query-blockstats'],1))
                self.assertFalse(result['passed'])

    def test_qmp_closing_without_guest_shutdown_stays_a_failure(self):
        result,calls=self.run_with_qmp_closed_under_a_sample(BrokenPipeError(32,'Broken pipe'),False)
        self.assertEqual(result['errors'],['BrokenPipeError: [Errno 32] Broken pipe'])
        self.assertFalse(result['observation_completed'])
        self.assertNotIn('qmp_closed_before_process_exit',result)
        self.assertIsNone(result.get('observation_end_reason'))
        self.assertEqual(calls['waits'],1)
        self.assertTrue(calls['closed'])

    def test_observation_failure_unwinds_the_session(self):
        calls = {}
        observer = SimpleNamespace(capture=lambda: (_ for _ in ()).throw(ValueError('fixture read failed')))
        with patch.object(boot, 'FirmwareSession', self.fake_session_factory(calls)), \
             patch.object(boot, 'KernelBootObserver', return_value=observer), \
             patch.object(boot, 'require_fixed_qmp_build', return_value={'binary_sha256': 'b' * 64}):
            result = boot.run(self.label, 'cold-failure', 1)
        self.assertTrue(calls['closed'])
        self.assertFalse(result['passed'])
        self.assertFalse(result['observation_completed'])
        self.assertTrue(result['errors'])

    def test_bdif_trace_and_shared_frontends_remain_separate(self):
        path = self.root / 'trace.log'
        path.write_text('bdif_vblk_read dev=aux addr=0x70000000 off=0x0 size=0x200 r=0\n'
                        'bdif_vblk_write dev=root addr=0x70000000 off=0x1000 size=0x1000 r=-5\n')
        result = boot.summarize_trace(path)
        self.assertEqual(result['targets']['aux.raw']['read_success_bytes'], 512)
        self.assertEqual(result['targets']['root.raw']['failed_calls'], 1)
        self.assertEqual(result['targets']['root.raw']['write_success_bytes'], 0)
        rows = boot.block_counters([{'device': 'root', 'stats': {'rd_bytes': 50},
                                    'parent': {'stats': {'rd_bytes': 50}}}])
        self.assertEqual(rows['root']['rd_bytes'], 50)
        self.assertEqual(len(rows), 1)

    def test_cli_help_and_duration_validation_do_not_open_a_session(self):
        process = subprocess.run([sys.executable, str(HERE / 'run.py'), '--help'],
                                 capture_output=True, text=True, timeout=5)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('--source-run', process.stdout)
        for flag in ('--usb-input', '--native-window', '--gpu', '--user-network'):
            self.assertIn(flag, process.stdout)
        for seconds in (0, 3601):
            with patch.object(boot, 'FirmwareSession') as session, self.assertRaises(ValueError):
                boot.run(self.label, 'invalid-duration', seconds)
            session.assert_not_called()

    def test_screens_are_deduplicated_bounded_and_final_capture_is_reserved(self):
        calls = []
        color = [0]
        def request(command, arguments):
            calls.append(threading.get_ident())
            self.assertEqual(command, 'screendump')
            Path(arguments['filename']).write_bytes(b'P6\n1 1\n255\n' + bytes([color[0], 0, 0]))
            return {}
        directory = self.root / 'screens'
        directory.mkdir()
        observer = boot.ScreenObserver(SimpleNamespace(request=request), directory)
        observer.capture(0)
        observer.capture(60)
        self.assertTrue(observer.report['attempts'][-1]['deduplicated'])
        first = directory / 'screen-0001.ppm'
        original = first.read_bytes()
        for index in range(1, boot.SCREEN_MAX_FILES + 3):
            color[0] = index
            observer.capture(index * 60)
        color[0] = 100
        observer.capture(1800, final=True)
        self.assertEqual(len(list(directory.glob('*.ppm'))), boot.SCREEN_MAX_FILES)
        self.assertEqual(first.read_bytes(), original)
        self.assertTrue(observer.report['final_capture_observed'])
        self.assertTrue(observer.report['collection_success'])
        self.assertFalse(observer.report['gui_verified'])
        self.assertEqual(set(calls), {threading.get_ident()})

    def test_screen_errors_do_not_turn_nonempty_files_into_gui_or_reuse_bad_qmp(self):
        directory = self.root / 'screens'
        directory.mkdir()
        def malformed(command, arguments):
            Path(arguments['filename']).write_bytes(b'not an image')
            return {}
        observer = boot.ScreenObserver(SimpleNamespace(request=malformed), directory)
        observer.capture(1, final=True)
        self.assertFalse(observer.report['collection_success'])
        self.assertFalse(observer.report['gui_verified'])
        for error in (TimeoutError('late reply'), EOFError('closed')):
            def failed(*args): raise error
            observer = boot.ScreenObserver(SimpleNamespace(request=failed), directory)
            observer.report['attempts'] = [{}]  # Preserve the previous malformed evidence path.
            with self.assertRaises(type(error)):
                observer.capture(2)
        def qmp_error(*args): raise RuntimeError('Complete QMP error: unavailable display')
        observer = boot.ScreenObserver(SimpleNamespace(request=qmp_error), directory)
        observer.report['attempts'] = [{}]
        observer.capture(3)
        self.assertTrue(observer.report['errors'])

    def test_new_presentation_is_recorded_without_requiring_it_in_old_source(self):
        calls = {}
        observer = SimpleNamespace(capture=lambda: {'capture_count': 1, 'runtime_evidence': {}})
        with patch.object(boot, 'FirmwareSession', self.fake_session_factory(calls)), \
             patch.object(boot, 'KernelBootObserver', return_value=observer), \
             patch.object(boot, 'require_fixed_qmp_build', return_value={'binary_sha256': 'b' * 64}):
            result = boot.run(self.label, 'presentation', 1, usb_input=True, native_window=True, gpu='nvidia')
        self.assertTrue(result['passed'])
        self.assertFalse(result['source']['qemu']['profile']['presentation']['usb_input_requested'])
        selected = result['current_qemu_profile']['presentation']
        self.assertTrue(selected['usb_input_requested'])
        self.assertTrue(selected['native_window_requested'])
        self.assertEqual(selected['vulkan_icd'], boot.GPU_ICDS['nvidia'])
        self.assertFalse(result['gui_verified'])

    def test_opt_in_network_is_forwarded_and_not_claimed_connected(self):
        calls={}
        observer=SimpleNamespace(capture=lambda:{'capture_count':1,'runtime_evidence':{}})
        with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=observer), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}):
            result=boot.run(self.label,'network',1,user_network=True)
        self.assertTrue(calls['kwargs']['user_network'])
        self.assertTrue(result['user_network_requested'])
        self.assertTrue(result['current_qemu_profile']['network']['requested'])
        self.assertFalse(result['source']['qemu']['profile']['network']['requested'])
        self.assertEqual(result['current_qemu_profile']['network']['guest_connectivity'],'unknown')

    def test_net_control_queue_cli_fw_profile_and_metadata(self):
        calls={}
        observer=SimpleNamespace(capture=lambda:{'capture_count':1,'runtime_evidence':{}})
        with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=observer), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}), \
             patch.object(boot,'require_net_control_queue_size_build',return_value={'patch':boot.NET_CONTROL_QUEUE_SIZE_PATCH}) as guard:
            result=boot.run(self.label,'net-control512',1,user_network=True,net_control_queue_size=512)
        guard.assert_called_once_with({'binary_sha256':'b'*64})
        self.assertTrue(result['passed'])
        self.assertEqual(calls['kwargs']['net_control_queue_size'],512)
        self.assertEqual(result['net_control_queue_size_requested'],512)
        net=result['current_qemu_profile']['network'];meta=net['control_queue_size']
        self.assertEqual(meta['requested'],512);self.assertEqual(meta['default'],64)
        self.assertEqual(meta['configured_maximum'],512)
        self.assertFalse(meta['runtime_verified']);self.assertIsNone(meta['guest_selected'])
        self.assertEqual(net['guest_connectivity'],'unknown')
        self.assertEqual(meta['actual_device_arguments'],[boot.virtio_net_device_argument(512)])
        args=result['execution']['qemu_argv']
        self.assertEqual(boot.qemu_profile(args,network=net,net_control_queue_size=512)['network'],net)
        with self.assertRaises(ValueError):boot.qemu_profile(args,network=net)
        for changed in ([x.replace('ctrl_queue_size=512','ctrl_queue_size=64') for x in args],
                        [x.replace(',ctrl_queue_size=512','') for x in args],
                        [x+',rx_queue_size=512' if x.startswith('virtio-net-pci,') else x for x in args]):
            with self.assertRaises(ValueError):boot.qemu_profile(changed,network=net,net_control_queue_size=512)
        wrong=copy.deepcopy(net);wrong['control_queue_size']['requested']=64
        with self.assertRaises(ValueError):boot.qemu_profile(args,network=wrong,net_control_queue_size=512)
        for size in (64,512):
            with patch.object(boot,'run',return_value=result) as runner:
                boot.main(['--source-run',self.label,'--user-network','--net-control-queue-size',str(size)])
            self.assertEqual(runner.call_args.kwargs['net_control_queue_size'],size)

    def test_net_control_queue_invalid_preflight_and_default_backcompat(self):
        with patch.object(boot,'validate_source') as source:
            for enabled,size in ((False,64),(False,512),(True,0),(True,128),(True,True),(True,512.0),(True,'512')):
                with self.subTest(enabled=enabled,size=size),self.assertRaises(ValueError):
                    boot.run(self.label,'invalid-net-control',1,user_network=enabled,net_control_queue_size=size)
            source.assert_not_called()
        for enabled in (False,True):
            calls={}
            with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
                 patch.object(boot,'KernelBootObserver',return_value=SimpleNamespace(capture=lambda:{'capture_count':1,'runtime_evidence':{}})), \
                 patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}), \
                 patch.object(boot,'require_net_control_queue_size_build') as guard:
                result=boot.run(self.label,'default-net-control-'+str(enabled),1,user_network=enabled)
            guard.assert_not_called()
            self.assertIsNone(result['net_control_queue_size_requested'])
            self.assertFalse(any('ctrl_queue_size' in x for x in result['execution']['qemu_argv']))
            if enabled:self.assertIn(boot.virtio_net_device_argument(),result['execution']['qemu_argv'])

    def test_net_control_queue_requires_matching0032_before_session(self):
        patchpath=self.root/boot.NET_CONTROL_QUEUE_SIZE_PATCH
        patchpath.parent.mkdir(parents=True);patchpath.write_bytes(b'control queue fixture implementation')
        manifest=self.root/'runs/qemu-bdif-usb-build.json'
        record={'binary_sha256':'b'*64,'patches':[],'patch_sha256':{}}
        manifest.write_text(json.dumps(record))
        with self.assertRaises(ValueError):boot.require_net_control_queue_size_build({'binary_sha256':'b'*64})
        with patch.object(boot,'FirmwareSession') as session, \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}):
            with self.assertRaises(ValueError):boot.run(self.label,'net-control-missing-build',1,user_network=True,net_control_queue_size=512)
        session.assert_not_called()
        self.assertFalse((self.root/'runs/e05-installed-boot/net-control-missing-build').exists())
        record['patches']=[boot.NET_CONTROL_QUEUE_SIZE_PATCH]
        record['patch_sha256'][boot.NET_CONTROL_QUEUE_SIZE_PATCH]=boot.hashlib.sha256(patchpath.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(record))
        self.assertEqual(boot.require_net_control_queue_size_build({'binary_sha256':'b'*64})['patch'],boot.NET_CONTROL_QUEUE_SIZE_PATCH)
        with self.assertRaises(ValueError):boot.require_net_control_queue_size_build({'binary_sha256':'c'*64})
        patchpath.write_bytes(b'changed control queue implementation')
        with self.assertRaises(ValueError):boot.require_net_control_queue_size_build({'binary_sha256':'b'*64})

    def test_guest_files_requires_network_before_any_session(self):
        with patch.object(boot, 'FirmwareSession') as session, \
             patch.object(boot, 'GuestFileProcess') as service, self.assertRaises(ValueError):
            boot.run(self.label, 'files-without-network', 1, guest_files=True)
        session.assert_not_called()
        service.assert_not_called()

    def test_sound_is_explicit_and_does_not_rewrite_install_source_profile(self):
        calls={}
        observer=SimpleNamespace(capture=lambda:{'capture_count':1,'runtime_evidence':{}})
        with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=observer), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}):
            result=boot.run(self.label,'sound',1,virtio_sound=True)
        self.assertTrue(result['passed'])
        self.assertTrue(calls['kwargs']['virtio_sound'])
        self.assertTrue(result['virtio_sound_requested'])
        self.assertFalse(result['source']['qemu']['profile']['audio']['requested'])
        sound=result['current_qemu_profile']['audio']
        self.assertEqual((sound['backend'],sound['pci_address']),('none','00:06.0'))
        self.assertEqual(sound['guest_driver_binding'],'unknown')
        self.assertFalse(sound['pcm_transfer_verified'])
        self.assertFalse(sound['audible_playback_verified'])
        self.assertFalse(sound['host_audio_input'])
        self.assertEqual(boot.virtio_sound_device_argument(sound),boot.VIRTIO_SOUND_DEVICE)

    def test_sound_profile_rejects_missing_unrequested_or_changed_device_backend(self):
        selected=self.args+['-audiodev',boot.VIRTIO_SOUND_BACKEND,'-device',boot.VIRTIO_SOUND_DEVICE]
        self.assertTrue(boot.qemu_profile(selected,virtio_sound=True)['audio']['requested'])
        with self.assertRaises(ValueError):boot.qemu_profile(selected)
        with self.assertRaises(ValueError):boot.qemu_profile(self.args,virtio_sound=True)
        for old,new in ((boot.VIRTIO_SOUND_BACKEND,'wav,id=guestsound,path=unexpected.wav'),
                        (boot.VIRTIO_SOUND_DEVICE,boot.VIRTIO_SOUND_DEVICE.replace('addr=0x6','addr=0x7')),
                        (boot.VIRTIO_SOUND_DEVICE,boot.VIRTIO_SOUND_DEVICE.replace('=on','=off'))):
            changed=[new if item==old else item for item in selected]
            with self.subTest(value=new),self.assertRaises(ValueError):
                boot.qemu_profile(changed,virtio_sound=True)
        with self.assertRaises(ValueError):
            boot.qemu_profile(selected,virtio_sound=True,audio=boot.virtio_sound_configuration(False))
        with self.assertRaises(ValueError):
            boot.qemu_profile(selected+['-audiodev',boot.VIRTIO_SOUND_BACKEND],virtio_sound=True)

    def test_sound_cli_and_invalid_request_preflight(self):
        result={'observation_completed':False,'passed':False,'normal_os_boot_proven':False,
                'boot_classification':{'environment':'unknown'}}
        with patch.object(boot,'run',return_value=result) as run:
            self.assertEqual(boot.main(['--source-run',self.label,'--virtio-sound']),1)
            self.assertTrue(run.call_args.kwargs['virtio_sound'])
        with patch.object(boot,'FirmwareSession') as session:
            with self.assertRaises(ValueError):
                boot.run(self.label,'bad-sound',1,virtio_sound=1)
            session.assert_not_called()

    def test_wav_backend_is_fixed_to_current_run_and_not_a_pcm_verdict(self):
        calls={}
        observer=SimpleNamespace(capture=lambda:{'capture_count':1,'runtime_evidence':{}})
        with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=observer), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}):
            result=boot.run(self.label,'wav-current',1,virtio_sound=True,sound_backend='wav')
        expected=self.root/'runs/e05-installed-boot/wav-current/guest-audio.wav'
        self.assertTrue(result['passed'])
        self.assertEqual(calls['kwargs']['sound_backend'],'wav')
        self.assertEqual(result['sound_backend_requested'],'wav')
        audio=result['current_qemu_profile']['audio']
        self.assertEqual(audio['output_file'],str(expected))
        self.assertEqual((audio['advertised_streams'],audio['advertised_output_streams'],
                          audio['advertised_input_streams']),(2,1,1))
        self.assertFalse(audio['input_backend_available'])
        self.assertNotIn('stream_layout',audio)
        self.assertFalse(audio['host_audio_output'])
        self.assertFalse(audio['host_audio_input'])
        self.assertFalse(audio['pcm_transfer_verified'])
        self.assertEqual(result['audio_capture']['state'],'not_created')
        self.assertFalse(result['source']['qemu']['profile']['audio']['requested'])

    def test_wav_cli_preflight_and_profile_reject_arbitrary_capture_path(self):
        result={'observation_completed':False,'passed':False,'normal_os_boot_proven':False,
                'boot_classification':{'environment':'unknown'}}
        with patch.object(boot,'run',return_value=result) as run:
            boot.main(['--source-run',self.label,'--virtio-sound','--sound-backend','wav'])
            self.assertEqual(run.call_args.kwargs['sound_backend'],'wav')
        for enabled,backend in ((False,'wav'),(True,'alsa'),(False,'unexpected')):
            with self.subTest(backend=backend),patch.object(boot,'FirmwareSession') as session:
                with self.assertRaises(ValueError):
                    boot.run(self.label,'bad-wav',1,virtio_sound=enabled,sound_backend=backend)
                session.assert_not_called()
                self.assertFalse((self.root/'runs/e05-installed-boot/bad-wav').exists())
        directory=self.root/'run'
        audio=boot.virtio_sound_configuration(True,'wav',directory)
        backend=boot.virtio_sound_backend_argument(audio)
        args=self.args+['-audiodev',backend,'-device',boot.virtio_sound_device_argument(audio)]
        self.assertEqual(boot.qemu_profile(args,virtio_sound=True,sound_backend='wav',
            run_directory=directory,audio=audio)['audio']['backend'],'wav')
        changed=[value.replace(str(directory/'guest-audio.wav'),'/tmp/other.wav') for value in args]
        with self.assertRaises(ValueError):
            boot.qemu_profile(changed,virtio_sound=True,sound_backend='wav',run_directory=directory)
        rejected_single_stream=[value+',streams=1' if value==boot.VIRTIO_SOUND_DEVICE else value for value in args]
        with self.assertRaises(ValueError):
            boot.qemu_profile(rejected_single_stream,virtio_sound=True,sound_backend='wav',run_directory=directory)

    def test_wav_arguments_refuse_existing_file_and_symlink(self):
        directory=self.root/'sound-session'
        session=boot.FirmwareSession(directory,virtio_sound=True,sound_backend='wav')
        self.addCleanup(session.close)
        path=directory/'guest-audio.wav'
        self.assertIn(str(path),session.sound_arguments()[1])
        self.assertEqual(session.sound_arguments()[-1],boot.VIRTIO_SOUND_DEVICE)
        path.write_bytes(b'keep this recording')
        with self.assertRaises(ValueError):session.sound_arguments()
        self.assertEqual(path.read_bytes(),b'keep this recording')
        path.unlink()
        target=self.root/'retained-output';target.write_bytes(b'unrelated')
        path.symlink_to(target)
        with self.assertRaises(ValueError):session.sound_arguments()
        self.assertEqual(target.read_bytes(),b'unrelated')

    def test_wav_header_and_payload_presence_never_claim_pcm(self):
        directory=self.root/'wav-header';directory.mkdir()
        config=boot.virtio_sound_configuration(True,'wav',directory)
        path=directory/'guest-audio.wav'
        for data in (b'',b'\0'*8):
            path.write_bytes(struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',len(data)+36,
                b'WAVE',b'fmt ',16,1,2,48000,192000,4,16,b'data',len(data))+data)
            report=boot.inspect_wav_capture(config,qemu_reaped=True)
            self.assertTrue(report['header_valid'])
            self.assertEqual(report['data_present'],bool(data))
            self.assertFalse(report['pcm_transfer_verified'])
            self.assertFalse(report['audible_playback_verified'])
        self.assertEqual(boot.inspect_wav_capture(config,qemu_reaped=False)['state'],'unclosed')
        path.write_bytes(b'RIFF')
        self.assertFalse(boot.inspect_wav_capture(config,qemu_reaped=True)['header_valid'])

    def test_pcm_trace_cli_caller_and_fixed_profile(self):
        calls={}
        observer=SimpleNamespace(capture=lambda:{'capture_count':1,'runtime_evidence':{}})
        with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=observer), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}), \
             patch.object(boot,'require_sound_pcm_trace_build',return_value={'patch':boot.PCM_TRACE_PATCH}):
            result=boot.run(self.label,'traced',1,virtio_sound=True,sound_backend='wav',sound_pcm_trace=True)
        self.assertTrue(result['passed'])
        self.assertTrue(calls['kwargs']['sound_pcm_trace'])
        self.assertTrue(result['sound_pcm_trace_requested'])
        directory=self.root/'runs/e05-installed-boot/traced'
        audio=result['current_qemu_profile']['audio']
        trace=audio['pcm_trace']
        self.assertEqual(trace['output_file'],str(directory/'guest-audio-boundary.bin'))
        self.assertEqual(trace['max_bytes'],8*1024*1024)
        self.assertEqual((trace['completion'],trace['corruption_cause']),('unknown','unknown'))
        args=self.args+['-audiodev',boot.virtio_sound_backend_argument(audio),
                        '-device',boot.virtio_sound_device_argument(audio)]
        changed=[value.replace('guest-audio-boundary.bin','other.bin') for value in args]
        with self.assertRaises(ValueError):
            boot.qemu_profile(changed,virtio_sound=True,sound_backend='wav',
                              sound_pcm_trace=True,run_directory=directory)
        with self.assertRaises(ValueError):
            boot.qemu_profile(args,virtio_sound=True,sound_backend='wav',run_directory=directory)
        with patch.object(boot,'run',return_value=result) as run:
            boot.main(['--source-run',self.label,'--virtio-sound','--sound-pcm-trace'])
            self.assertTrue(run.call_args.kwargs['sound_pcm_trace'])

    def test_pcm_trace_invalid_selection_and_no_overwrite(self):
        for enabled,selected in ((False,True),(True,1)):
            with patch.object(boot,'FirmwareSession') as session,self.assertRaises(ValueError):
                boot.run(self.label,'invalid-trace',1,virtio_sound=enabled,sound_pcm_trace=selected)
            session.assert_not_called()
            self.assertFalse((self.root/'runs/e05-installed-boot/invalid-trace').exists())
        directory=self.root/'trace,session'
        session=boot.FirmwareSession(directory,virtio_sound=True,sound_pcm_trace=True)
        self.addCleanup(session.close)
        args=session.sound_arguments()
        self.assertIn('trace,,session/guest-audio-boundary.bin',args[-1])
        self.assertEqual(args[1],boot.VIRTIO_SOUND_BACKEND)
        path=directory/'guest-audio-boundary.bin'
        path.write_bytes(b'old trace')
        with self.assertRaises(ValueError):session.sound_arguments()
        self.assertEqual(path.read_bytes(),b'old trace')
        path.unlink()
        path.symlink_to(directory/'missing')
        with self.assertRaises(ValueError):session.sound_arguments()
        self.assertEqual(boot.virtio_sound_device_argument(
            boot.virtio_sound_configuration(True)),boot.VIRTIO_SOUND_DEVICE)
        self.assertFalse(boot.virtio_sound_configuration(False)['pcm_trace']['requested'])

    def test_vcpu_affinity_cli_and_preflight_before_target_validation(self):
        calls={}
        observer=SimpleNamespace(capture=lambda:{'capture_count':1,'runtime_evidence':{}})
        with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=observer), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}), \
             patch('firmware_session.os.sched_getaffinity',return_value=set(range(20))):
            result=boot.run(self.label,'affinity-selected',1,vcpu_affinity='0-7')
        self.assertTrue(result['passed'])
        self.assertEqual(calls['kwargs']['vcpu_affinity'],'0-7')
        self.assertEqual(result['vcpu_affinity_requested'],'0-7')
        self.assertEqual(result['execution']['vcpu_affinity']['requested'],'0-7')
        with patch.object(boot,'run',return_value=result) as run:
            boot.main(['--source-run',self.label,'--vcpu-affinity','0,2,4-7'])
            self.assertEqual(run.call_args.kwargs['vcpu_affinity'],'0,2,4-7')
        with patch.object(boot,'validate_source') as source, \
             patch('firmware_session.os.sched_getaffinity',return_value={0,1}):
            with self.assertRaises(ValueError):boot.run(self.label,'invalid-affinity',1,vcpu_affinity='0-7')
            source.assert_not_called()
            self.assertFalse((self.root/'runs/e05-installed-boot/invalid-affinity').exists())

    def test_sound_streams_cli_profile_metadata_and_existing_build_contract(self):
        calls={};observer=SimpleNamespace(capture=lambda:{'capture_count':1,'runtime_evidence':{}})
        with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=observer), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}) as fixed, \
             patch.object(boot,'require_sound_queue_size_build') as queue_build:
            result=boot.run(self.label,'one-output',1,virtio_sound=True,sound_backend='wav',sound_streams=1)
        fixed.assert_called_once_with(boot.QEMU,require_usb=True)
        queue_build.assert_not_called()
        self.assertTrue(result['passed'])
        self.assertEqual(calls['kwargs']['sound_streams'],1)
        self.assertIsNone(calls['kwargs']['sound_queue_size'])
        self.assertEqual(result['sound_streams_requested'],1)
        audio=result['current_qemu_profile']['audio']
        self.assertEqual((audio['advertised_streams'],audio['advertised_output_streams'],audio['advertised_input_streams']),(1,1,0))
        self.assertEqual(audio['streams']['requested'],1)
        self.assertFalse(audio['streams']['runtime_verified'])
        self.assertFalse(audio['input_backend_available'])
        args=self.args+['-audiodev',boot.virtio_sound_backend_argument(audio),'-device',boot.virtio_sound_device_argument(audio)]
        directory=self.root/'runs/e05-installed-boot/one-output'
        self.assertEqual(boot.qemu_profile(args,virtio_sound=True,sound_backend='wav',run_directory=directory,sound_streams=1)['audio'],audio)
        with self.assertRaises(ValueError):boot.qemu_profile(args,virtio_sound=True,sound_backend='wav',run_directory=directory)
        for count in (1,2):
            with patch.object(boot,'run',return_value=result) as run:
                boot.main(['--source-run',self.label,'--virtio-sound','--sound-streams',str(count)])
                self.assertEqual(run.call_args.kwargs['sound_streams'],count)
        for count in (None,2):
            profile=boot.virtio_sound_configuration(True,streams=count)
            self.assertEqual((profile['advertised_streams'],profile['advertised_output_streams'],profile['advertised_input_streams']),(2,1,1))
            self.assertEqual(boot.virtio_sound_device_argument(profile),boot.VIRTIO_SOUND_DEVICE+(',streams=2' if count else ''))
        with patch.object(boot,'validate_source') as source:
            for enabled,count in ((False,1),(True,0),(True,3),(True,True),(True,1.0),(True,'1')):
                with self.subTest(enabled=enabled,count=count),self.assertRaises(ValueError):
                    boot.run(self.label,'invalid-streams',1,virtio_sound=enabled,sound_streams=count)
            source.assert_not_called()

    def test_sound_queue_size_cli_caller_and_fixed_profile(self):
        calls={}
        observer=SimpleNamespace(capture=lambda:{'capture_count':1,'runtime_evidence':{}})
        with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=observer), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}), \
             patch.object(boot,'require_sound_queue_size_build',return_value={'patch':boot.SOUND_QUEUE_SIZE_PATCH}) as check:
            result=boot.run(self.label,'queue512',1,virtio_sound=True,sound_queue_size=512)
        check.assert_called_once_with({'binary_sha256':'b'*64})
        self.assertTrue(result['passed'])
        self.assertEqual(calls['kwargs']['sound_queue_size'],512)
        self.assertEqual(result['sound_queue_size_requested'],512)
        audio=result['current_qemu_profile']['audio']
        self.assertEqual(audio['queue_size']['requested'],512)
        self.assertEqual(audio['queue_size']['configured_maximum'],512)
        self.assertIsNone(audio['queue_size']['guest_selected'])
        self.assertFalse(audio['queue_size']['runtime_verified'])
        args=self.args+['-audiodev',boot.virtio_sound_backend_argument(audio),
                        '-device',boot.virtio_sound_device_argument(audio)]
        self.assertEqual(boot.qemu_profile(args,virtio_sound=True,sound_queue_size=512)['audio'],audio)
        with self.assertRaises(ValueError):boot.qemu_profile(args,virtio_sound=True)
        changed=[value.replace('queue-size=512','queue-size=64') for value in args]
        with self.assertRaises(ValueError):boot.qemu_profile(changed,virtio_sound=True,sound_queue_size=512)
        for size in (64,512):
            with patch.object(boot,'run',return_value=result) as run:
                boot.main(['--source-run',self.label,'--virtio-sound','--sound-queue-size',str(size)])
                self.assertEqual(run.call_args.kwargs['sound_queue_size'],size)

    def test_sound_queue_size_selection_and_matching_build(self):
        for enabled,size in ((False,512),(True,0),(True,128),(True,True),(True,512.0),(True,'512')):
            with self.subTest(enabled=enabled,size=size),self.assertRaises(ValueError):
                boot.run(self.label,'invalid-queue',1,virtio_sound=enabled,sound_queue_size=size)
        default=boot.virtio_sound_configuration(True)
        self.assertEqual(boot.virtio_sound_device_argument(default),boot.VIRTIO_SOUND_DEVICE)
        self.assertEqual(default['queue_size']['configured_maximum'],64)
        self.assertIsNone(default['queue_size']['requested'])
        patchpath=self.root/boot.SOUND_QUEUE_SIZE_PATCH
        patchpath.parent.mkdir(parents=True)
        patchpath.write_bytes(b'queue-size implementation')
        manifest=self.root/'runs/qemu-bdif-usb-build.json'
        record={'binary_sha256':'b'*64,'patches':[],'patch_sha256':{}}
        manifest.write_text(json.dumps(record))
        with self.assertRaises(ValueError):boot.require_sound_queue_size_build({'binary_sha256':'b'*64})
        record['patches']=[boot.SOUND_QUEUE_SIZE_PATCH]
        record['patch_sha256'][boot.SOUND_QUEUE_SIZE_PATCH]=boot.hashlib.sha256(patchpath.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(record))
        self.assertEqual(boot.require_sound_queue_size_build({'binary_sha256':'b'*64})['patch'],boot.SOUND_QUEUE_SIZE_PATCH)
        with self.assertRaises(ValueError):boot.require_sound_queue_size_build({'binary_sha256':'c'*64})
        patchpath.write_bytes(b'changed since build')
        with self.assertRaises(ValueError):boot.require_sound_queue_size_build({'binary_sha256':'b'*64})

    def test_pcm_trace_requires_matching_built_patch(self):
        patchpath=self.root/boot.PCM_TRACE_PATCH
        patchpath.parent.mkdir(parents=True)
        patchpath.write_bytes(b'PCM trace implementation')
        control_patch=self.root/boot.SOUND_EMPTY_INFO_PATCH
        control_patch.write_bytes(b'empty item-info and acceptance trace')
        manifest=self.root/'runs/qemu-bdif-usb-build.json'
        record={'binary_sha256':'b'*64,'patches':[],'patch_sha256':{}}
        manifest.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            boot.require_sound_pcm_trace_build({'binary_sha256':'b'*64})
        record['patches']=[boot.PCM_TRACE_PATCH]
        record['patch_sha256'][boot.PCM_TRACE_PATCH]=boot.hashlib.sha256(patchpath.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(record))
        # An old 0023-only build cannot supply the newly selected trace event.
        with self.assertRaises(ValueError):
            boot.require_sound_pcm_trace_build({'binary_sha256':'b'*64})
        record['patches'].append(boot.SOUND_EMPTY_INFO_PATCH)
        record['patch_sha256'][boot.SOUND_EMPTY_INFO_PATCH]=boot.hashlib.sha256(control_patch.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(record))
        verified=boot.require_sound_pcm_trace_build({'binary_sha256':'b'*64})
        self.assertEqual(verified['patch'],boot.PCM_TRACE_PATCH)
        self.assertEqual(verified['control_trace']['patch'],boot.SOUND_EMPTY_INFO_PATCH)
        self.assertEqual(verified['control_trace']['patch_sha256'],record['patch_sha256'][boot.SOUND_EMPTY_INFO_PATCH])
        self.assertEqual(verified['control_trace']['event'],'virtio_snd_empty_info')
        for path in (patchpath,control_patch):
            original=path.read_bytes()
            path.write_bytes(b'changed since build')
            with self.subTest(path=path),self.assertRaises(ValueError):
                boot.require_sound_pcm_trace_build({'binary_sha256':'b'*64})
            path.write_bytes(original)
        with self.assertRaises(ValueError):
            boot.require_sound_pcm_trace_build({'binary_sha256':'c'*64})

    def test_guest_files_failure_prevents_qemu_construction(self):
        with patch.object(boot, 'FirmwareSession') as session, \
             patch.object(boot, 'GuestFileProcess') as service, \
             patch.object(boot, 'require_fixed_qmp_build', return_value={'binary_sha256': 'b' * 64}):
            service.return_value.report = {'ready': False}
            service.return_value.start.side_effect = OSError('address in use')
            result = boot.run(self.label, 'files-busy', 1, user_network=True, guest_files=True)
        session.assert_not_called()
        service.return_value.close.assert_called_once()
        self.assertFalse(result['fresh_qemu_process'])
        self.assertFalse(result['passed'])
        self.assertIn('address in use', result['errors'][0])

    def test_webkit_timeout_default_argv_and_opt_in_cli(self):
        self.assertNotIn('webkit-timeout', boot.cpu_model())
        self.assertEqual(boot.cpu_model(), boot.cpu_model(webkit_timeout_diagnostic=False))
        selected=list(self.args)
        selected[selected.index('-cpu')+1]=boot.cpu_model(webkit_timeout_diagnostic=True)
        with self.assertRaises(ValueError):boot.qemu_profile(selected)
        profile=boot.qemu_profile(selected,webkit_timeout_diagnostic=True)
        self.assertTrue(profile['webkit_timeout_diagnostic']['requested'])
        self.assertFalse(profile['webkit_timeout_diagnostic']['original_deadline_performance_pass'])
        for value in (None,1,'on'):
            with self.assertRaises(ValueError):boot.cpu_model(webkit_timeout_diagnostic=value)
        with patch.object(boot,'run',return_value={'observation_completed':False,'passed':False,
                'normal_os_boot_proven':False,'boot_classification':{'environment':'unknown'}}) as runner:
            boot.main(['--source-run',self.label,'--webkit-timeout-diagnostic'])
        self.assertTrue(runner.call_args.kwargs['webkit_timeout_diagnostic'])

    def test_webkit_timeout_build_preflight_and_file_service_consumer(self):
        calls={}
        with patch.object(boot,'FirmwareSession',self.fake_session_factory(calls)), \
             patch.object(boot,'KernelBootObserver',return_value=SimpleNamespace(capture=lambda:{
                 'capture_count':1,'runtime_evidence':{},'boot_args_candidates':[]})), \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}), \
             patch.object(boot,'require_timeout_diagnostic_build',return_value={
                 'patch':'0030','runtime_slide':{'patch':'0031'},'requested':True}) as guard, \
             patch.object(boot,'GuestFileProcess') as files:
            files.return_value.report={'ready':True,'requested':True}
            result=boot.run(self.label,'timeout-enabled',1,user_network=True,guest_files=True,
                            webkit_timeout_diagnostic=True)
        guard.assert_called_once_with({'binary_sha256':'b'*64},self.root)
        self.assertTrue(calls['kwargs']['webkit_timeout_diagnostic'])
        self.assertTrue(files.call_args.kwargs['shared_cache_identity'])
        self.assertIn('x-webkit-timeout-diagnostic=',result['current_qemu_profile']['cpu'])
        self.assertEqual(result['webkit_timeout_diagnostic']['state'],'configured_unarmed')
        self.assertEqual(result['webkit_timeout_diagnostic']['schema'],2)
        self.assertEqual(result['webkit_timeout_diagnostic']['publication_protocol'],'runtime-pc-slide-armed-bool-v2')
        self.assertEqual(result['current_qemu_build']['webkit_timeout_diagnostic']['runtime_slide']['patch'],'0031')
        self.assertFalse(result['webkit_timeout_diagnostic']['runtime_site_hits_observed'])
        self.assertFalse(result['webkit_timeout_diagnostic']['original_deadline_performance_pass'])
        with patch.object(boot,'FirmwareSession') as session, \
             patch.object(boot,'require_fixed_qmp_build',return_value={'binary_sha256':'b'*64}), \
             patch.object(boot,'require_timeout_diagnostic_build',side_effect=boot.UIError('matching0030+0031 required')):
            with self.assertRaises(boot.UIError):
                boot.run(self.label,'timeout-bad-build',1,webkit_timeout_diagnostic=True)
        session.assert_not_called()
        self.assertFalse((self.root/'runs/e05-installed-boot/timeout-bad-build').exists())

    def test_guest_files_lifecycle_wraps_session_including_failure(self):
        calls = {}
        order = []
        class Service:
            def __init__(self, serve, uploads):
                self.report = {'ready': False, 'stopped': False}
                calls['serve'] = serve
                calls['uploads'] = uploads
            def start(self):
                order.append('files-ready')
                self.report['ready'] = True
            def close(self):
                order.append('files-closed')
                self.report['stopped'] = True
        class Session(self.fake_session_factory(calls)):
            def __enter__(self):
                order.append('qemu-start')
                return super().__enter__()
            def __exit__(self, *args):
                order.append('qemu-closed')
                return super().__exit__(*args)
        observer = SimpleNamespace(capture=lambda: (_ for _ in ()).throw(ValueError('observation failed')))
        with patch.object(boot, 'FirmwareSession', Session), \
             patch.object(boot, 'GuestFileProcess', Service), \
             patch.object(boot, 'KernelBootObserver', return_value=observer), \
             patch.object(boot, 'require_fixed_qmp_build', return_value={'binary_sha256': 'b' * 64}):
            result = boot.run(self.label, 'files-owned', 1, user_network=True, guest_files=True)
        self.assertEqual(order, ['files-ready', 'qemu-start', 'qemu-closed', 'files-closed'])
        self.assertTrue(result['guest_files_service']['ready'])
        self.assertTrue(result['guest_files_service']['stopped'])
        self.assertEqual(calls['uploads'], self.root / 'runs/e05-installed-boot/files-owned/guest-uploads')

    def test_guest_files_cli_passes_opt_in_to_runner(self):
        with patch.object(boot, 'run', return_value={'observation_completed': True, 'passed': True,
                'normal_os_boot_proven': False, 'boot_classification': {'environment': 'unknown'}}) as run:
            self.assertEqual(boot.main(['--source-run', self.label, '--user-network', '--guest-files']), 0)
        self.assertTrue(run.call_args.kwargs['guest_files'])
        self.assertTrue(run.call_args.kwargs['user_network'])

    def test_runner_real_service_readiness_before_session_and_port_cleanup(self):
        serve = self.root / 'experiments/E05-installed-boot/guest-diag'
        serve.mkdir(parents=True)
        for name in ('render.html', 'gpuwatch.sh', 'start-gpuwatch.sh'):
            (serve / name).write_bytes((HERE / 'guest-diag' / name).read_bytes())
        instances = []
        real_service = boot.GuestFileProcess
        def service_factory(*args):
            instance = real_service(*args, port=0)
            instances.append(instance)
            return instance
        calls = {}
        class Session(self.fake_session_factory(calls)):
            def __enter__(session):
                service = instances[0]
                self.assertTrue(service.report['upload_roundtrip_verified'])
                client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with client.open(service.report['host_url'] + '/get/render.html', timeout=2) as response:
                    self.assertEqual(response.read(), (serve / 'render.html').read_bytes())
                return super().__enter__()
        observer = SimpleNamespace(capture=lambda: {'capture_count': 1, 'runtime_evidence': {}})
        with patch.object(boot, 'GuestFileProcess', side_effect=service_factory), \
             patch.object(boot, 'FirmwareSession', Session), \
             patch.object(boot, 'KernelBootObserver', return_value=observer), \
             patch.object(boot, 'require_fixed_qmp_build', return_value={'binary_sha256': 'b' * 64}):
            result = boot.run(self.label, 'real-files', 1, user_network=True, guest_files=True)
        self.assertTrue(result['passed'], result['errors'])
        self.assertTrue(result['guest_files_service']['ready'])
        self.assertTrue(result['guest_files_service']['stopped'])
        self.assertEqual(instances[0].process.poll(), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
