#!/usr/bin/env python3
"""Real sparse-file lifetime checks; no firmware, VM or installed-system claim."""
import json
import hashlib
import importlib.util
import fcntl
import os
from pathlib import Path
import subprocess
import signal
import socket
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from firmware_session import (FirmwareSession, FirmwareSessionInterrupted, ROOT,
                               BLANK_ROOT_BYTES,tcg_accel_argument,LIFECYCLE_SOURCE,parse_vcpu_affinity)

_recovery_spec=importlib.util.spec_from_file_location('recover_abandoned',
    ROOT/'experiments/E05-installed-boot/recover-abandoned.py')
_recovery=importlib.util.module_from_spec(_recovery_spec)
_recovery_spec.loader.exec_module(_recovery)

# Linux accepts socket paths of at most 107 bytes (sun_path is 108 with the NUL).
UNIX_SOCKET_PATH_MAX=107
# The deepest socket the reboot-recovery tests bind, relative to the parent of
# their sandbox (tempfile names are the prefix plus eight characters).
DEEPEST_RESUME_SOCKET='resume-test-xxxxxxxx/build/kernel-usb-abandoned/kernel-usb.sock'


def resume_sandbox_parent():
    """ROOT/build, unless the checkout path is too long for the sockets the
    reboot-recovery tests bind; then a short temporary directory."""
    for parent in (ROOT/'build',Path(tempfile.gettempdir()),Path('/tmp')):
        if len(os.fsencode(parent/DEEPEST_RESUME_SOCKET))<=UNIX_SOCKET_PATH_MAX:
            return parent
    raise RuntimeError('No temporary directory is short enough for AF_UNIX sockets')


class StorageRetentionTests(unittest.TestCase):
    def setUp(self):
        self.sandbox=tempfile.TemporaryDirectory(prefix='retention-test-',dir=ROOT/'build')
        self.base=Path(self.sandbox.name)
        self.fw=FirmwareSession(self.base/'logs',blank_root_disk=True)
        self.fw.temp=tempfile.TemporaryDirectory(prefix='session-',dir=self.base)
        self.work=Path(self.fw.temp.name)
        self.fw.prepare_local_storage(self.work)
        self.fw.args=[sys.executable]
        self.destination=self.base/'retained'

    def tearDown(self):
        self.fw.close()
        self.sandbox.cleanup()

    def test_signal_hook_unwinds_and_restores_controller_handlers(self):
        previous = {signum: signal.getsignal(signum)
                    for signum in (signal.SIGTERM, signal.SIGINT)}
        self.fw._install_signal_handlers()
        try:
            with self.assertRaises(FirmwareSessionInterrupted) as raised:
                self.fw._handle_signal(signal.SIGTERM, None)
            self.assertEqual(raised.exception.signum, signal.SIGTERM)
            self.assertEqual(self.fw._signal_received, signal.SIGTERM)
        finally:
            self.fw._restore_signal_handlers()
        for signum, handler in previous.items():
            self.assertEqual(signal.getsignal(signum), handler)

    def test_later_writes_survive_session_cleanup_without_copying(self):
        report=self.fw.retain_blank_storage(self.destination)
        for name in ('aux.raw','root.raw'):
            source=self.work/name
            target=self.destination/name
            self.assertTrue(os.path.samefile(source,target))
            with source.open('r+b') as stream:
                stream.seek(4096);stream.write(b'later guest-style write')
            self.assertEqual(target.stat().st_mode & 0o777,0o600)
        self.fw.close()
        self.assertFalse(self.work.exists())
        for name in ('aux.raw','root.raw'):
            with (self.destination/name).open('rb') as stream:
                stream.seek(4096);self.assertEqual(stream.read(len(b'later guest-style write')),b'later guest-style write')
            self.assertTrue(report['files'][name]['retained_identity_matches'])
        saved=json.loads((self.fw.logs/'retained-storage.json').read_text())
        self.assertTrue(saved['temporary_links_removed'])
        self.assertFalse(saved['qemu_reaped'])
        self.assertFalse(saved['clean_guest_shutdown_proven'])

    def test_existing_destination_is_not_modified(self):
        self.destination.mkdir(mode=0o700)
        marker=self.destination/'root.raw';marker.write_bytes(b'existing input')
        with self.assertRaises(FileExistsError):
            self.fw.retain_blank_storage(self.destination)
        self.assertEqual(marker.read_bytes(),b'existing input')

    def test_source_symlink_is_not_imported(self):
        original=self.work/'aux.raw';original.rename(self.base/'external.raw')
        original.symlink_to(self.base/'external.raw')
        with self.assertRaises(ValueError):
            self.fw.retain_blank_storage(self.destination)
        self.assertFalse(self.destination.exists())

    def test_partial_link_failure_removes_only_its_new_alias(self):
        real_link=os.link
        def fail_second(source,target,**kwargs):
            if Path(source).name=='root.raw':raise OSError('injected link failure')
            return real_link(source,target,**kwargs)
        with patch('firmware_session.os.link',side_effect=fail_second):
            with self.assertRaises(OSError):
                self.fw.retain_blank_storage(self.destination)
        self.assertFalse(self.destination.exists())
        self.assertTrue((self.work/'aux.raw').exists())
        self.assertTrue((self.work/'root.raw').exists())


class StorageResumeTests(unittest.TestCase):
    """Real sparse inodes/flocks; closed-source metadata is a synthetic fixture."""
    def setUp(self):
        self.sandbox=tempfile.TemporaryDirectory(prefix='resume-test-',dir=resume_sandbox_parent())
        self.base=Path(self.sandbox.name)
        self.root_patch=patch('firmware_session.ROOT',self.base)
        self.root_patch.start()
        self.sessions=[]
        self.label='closed-source'
        source=FirmwareSession(self.base/'runs/e05-signed-restore'/self.label,blank_root_disk=True)
        self.sessions.append(source)
        source.temp=tempfile.TemporaryDirectory(prefix='source-',dir=self.base)
        source.args=[sys.executable]
        source.prepare_local_storage(Path(source.temp.name))
        parent=self.base/'private/signed-restore'/self.label
        parent.mkdir(parents=True,mode=0o700)
        self.source=parent/'storage'
        source.retain_blank_storage(self.source)
        source.close()
        self.manifest=source.logs/'retained-storage.json'
        self.record=json.loads(self.manifest.read_text())
        # No QEMU is started by this suite. Model an already closed source run.
        self.record.update(qemu_reaped=True,qemu_exit_code=0,teardown='qmp_quit',temporary_links_removed=True)
        self.save_record()
        for name in ('aux.raw','root.raw'):
            with (self.source/name).open('r+b') as stream:
                stream.seek(4096);stream.write(b'original retained bytes')

    def tearDown(self):
        for fw in reversed(self.sessions):fw.close()
        self.root_patch.stop()
        self.sandbox.cleanup()

    def save_record(self):
        self.manifest.write_text(json.dumps(self.record))

    def session(self,label='next'):
        fw=FirmwareSession(self.base/'runs/e05-signed-restore'/label,resume_storage_run=self.label)
        fw.temp=tempfile.TemporaryDirectory(prefix='resume-',dir=self.base)
        fw.args=[sys.executable]
        self.sessions.append(fw)
        return fw,Path(fw.temp.name)

    def test_reuses_same_inodes_layout_and_later_writes_without_truncation(self):
        fw,work=self.session()
        fw.prepare_local_storage(work)
        parent=self.base/'private/signed-restore/next'
        parent.mkdir(parents=True,mode=0o700)
        retained=fw.retain_blank_storage(parent/'storage')
        for name,size in [('aux.raw',32*1024**2),('root.raw',BLANK_ROOT_BYTES)]:
            self.assertTrue(os.path.samefile(self.source/name,work/name))
            self.assertTrue(os.path.samefile(parent/'storage'/name,work/name))
            self.assertEqual((work/name).stat().st_size,size)
            self.assertLess((work/name).stat().st_blocks*512,1024**2)
            with (work/name).open('r+b') as stream:
                stream.seek(4096);self.assertEqual(stream.read(23),b'original retained bytes')
                stream.seek(size-512);stream.write(b'new-session guest-style write')
        args=fw.storage_arguments(work)
        self.assertEqual(len([x for x in args if x=='-drive']),4)
        self.assertEqual(sum(str(work/'root.raw') in x for x in args),2)
        self.assertEqual(sum(str(work/'aux.raw') in x for x in args),2)
        self.assertFalse(any('qcow2' in x or 'nbd' in x for x in args))
        blocks=[{'device':name,'inserted':{'image':{'virtual-size':size},'drv':'raw','ro':False}}
                for name,size in [('pflash0',32*1024**2),('pflash1',BLANK_ROOT_BYTES),
                                  ('aux',32*1024**2),('root',BLANK_ROOT_BYTES)]]
        profile=fw.describe_storage(blocks)
        self.assertEqual(profile['profile'],'retained-root-target')
        self.assertFalse(profile['blank_target_at_creation'])
        self.assertFalse(profile['installed_system_proven'])
        self.assertEqual(profile['source_run'],self.label)
        self.assertEqual(retained['source_run'],self.label)
        self.assertTrue(retained['active_target_alias_not_snapshot'])
        self.assertFalse(retained['blank_target_at_creation'])
        self.assertTrue(all(row['owner_uid']==os.geteuid() for row in retained['files'].values()))
        fw.close()
        self.assertFalse(work.exists())
        for name,size in [('aux.raw',32*1024**2),('root.raw',BLANK_ROOT_BYTES)]:
            for directory in [self.source,parent/'storage']:
                with (directory/name).open('rb') as stream:
                    stream.seek(size-512);self.assertEqual(stream.read(29),b'new-session guest-style write')
                self.assertEqual((directory/name).stat().st_size,size)

    def test_rejects_active_or_non_qmp_closed_source(self):
        for field,value in [('qemu_reaped',False),('temporary_links_removed',False),
                            ('qemu_exit_code',-9),('teardown','kill')]:
            with self.subTest(field=field):
                original=self.record[field];self.record[field]=value;self.save_record()
                fw,work=self.session()
                with self.assertRaises(ValueError):fw.prepare_local_storage(work)
                self.assertEqual(list(work.iterdir()),[])
                self.assertEqual(fw._storage_locks,[])
                self.record[field]=original
        self.save_record()

    def test_real_exclusive_locks_reject_parallel_reuse_until_close(self):
        first,work=self.session('first');first.prepare_local_storage(work)
        second,other=self.session('second')
        with self.assertRaisesRegex(ValueError,'already in use'):second.prepare_local_storage(other)
        self.assertFalse(list(other.iterdir()))
        first.close()
        second.prepare_local_storage(other)
        self.assertTrue(os.path.samefile(other/'root.raw',self.source/'root.raw'))

    def test_explicit_unclean_reuse_records_exit_and_still_rejects_active(self):
        self.record.update(qemu_exit_code=-6,teardown='terminate')
        self.save_record()
        fw,work=self.session('after-abort')
        fw.allow_unclean_storage=True
        fw.prepare_local_storage(work)
        self.assertTrue(fw.source_storage_shutdown['explicit_unclean_reuse'])
        self.assertFalse(fw.source_storage_shutdown['normal_qmp_exit'])
        self.assertEqual(fw.source_storage_shutdown['qemu_exit_code'],-6)
        self.assertTrue(os.path.samefile(work/'aux.raw',self.source/'aux.raw'))
        fw.close()
        self.record['qemu_reaped']=False;self.save_record()
        active,other=self.session('active-after-abort')
        active.allow_unclean_storage=True
        with self.assertRaises(ValueError):active.prepare_local_storage(other)
        self.assertEqual(list(other.iterdir()),[])

    def test_partial_lock_failure_releases_already_locked_aux(self):
        with (self.source/'root.raw').open('r+b') as held:
            fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            fw,work=self.session()
            with self.assertRaisesRegex(ValueError,'already in use'):fw.prepare_local_storage(work)
            self.assertEqual(fw._storage_locks,[])
            with (self.source/'aux.raw').open('r+b') as aux:
                fcntl.flock(aux,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def later_cold_boot_alias(self, *, reaped=True):
        directory=self.base/'private/installed-boot/later/storage'
        directory.mkdir(parents=True,mode=0o700)
        record=json.loads(json.dumps(self.record))
        record.update(directory=str(directory),qemu_exit_code=-6,qemu_reaped=reaped,
                      teardown='already_exited_or_not_started',temporary_links_removed=True)
        for name in ('aux.raw','root.raw'):
            os.link(self.source/name,directory/name)
            record['files'][name]['path']=str(directory/name)
        path=self.base/'runs/e05-installed-boot/later/retained-storage.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(record))
        timestamp=self.manifest.stat().st_mtime_ns+1_000_000_000
        os.utime(path,ns=(timestamp,timestamp))
        return path

    def test_later_aborted_alias_overrides_older_success_for_same_inodes(self):
        later=self.later_cold_boot_alias()
        fw,work=self.session('after-later-abort')
        with self.assertRaisesRegex(ValueError,'Latest target run'):
            fw.prepare_local_storage(work)
        self.assertEqual(fw._storage_locks,[])
        self.assertFalse(list(work.iterdir()))
        fw.allow_unclean_storage=True
        fw.prepare_local_storage(work)
        shutdown=fw.source_storage_shutdown
        self.assertEqual(shutdown['manifest'],str(later))
        self.assertEqual(shutdown['run'],'later')
        self.assertEqual(shutdown['qemu_exit_code'],-6)
        self.assertFalse(shutdown['normal_qmp_exit'])
        self.assertTrue(shutdown['explicit_unclean_reuse'])
        self.assertTrue(shutdown['selected_source_shutdown']['normal_qmp_exit'])
        self.assertTrue(os.path.samefile(work/'root.raw',self.source/'root.raw'))

    def test_latest_guest_shutdown_accepts_same_inodes_without_unclean_override(self):
        later=self.later_cold_boot_alias()
        record=json.loads(later.read_text())
        record.update(qemu_exit_code=0,qemu_pid=1234,guest_shutdown_observed=True,
            qmp_lifecycle={'source':LIFECYCLE_SOURCE,'qemu_pid':1234,'host_quit_requested':False,
                'last_shutdown_or_reset_event':{'event':'SHUTDOWN','data':{'guest':True,'reason':'guest-shutdown'}}})
        later.write_text(json.dumps(record))
        fw,work=self.session('after-guest-shutdown')
        fw.prepare_local_storage(work)
        self.assertTrue(fw.source_storage_shutdown['guest_shutdown_observed'])
        self.assertFalse(fw.source_storage_shutdown['normal_qmp_exit'])
        self.assertFalse(fw.source_storage_shutdown['explicit_unclean_reuse'])
        self.assertTrue(os.path.samefile(work/'root.raw',self.source/'root.raw'))
        fw.close()
        for key,value in [('qmp_lifecycle',None),('qemu_exit_code',-6),('qemu_pid',999),('qemu_reaped',False)]:
            later.write_text(json.dumps({**record,key:value}))
            rejected,other=self.session('unqualified-exit')
            with self.assertRaises(ValueError):rejected.prepare_local_storage(other)
            self.assertFalse(list(other.iterdir()))

    def test_unclean_option_does_not_accept_later_unreaped_alias(self):
        self.later_cold_boot_alias(reaped=False)
        fw,work=self.session('after-active-alias');fw.allow_unclean_storage=True
        with self.assertRaisesRegex(ValueError,'Latest run of these target inodes'):
            fw.prepare_local_storage(work)
        self.assertEqual(fw._storage_locks,[])
        self.assertFalse(list(work.iterdir()))

    def abandoned_fixture(self):
        path=self.later_cold_boot_alias(reaped=False)
        record=json.loads(path.read_text())
        record.update(qemu_exit_code=None,temporary_links_removed=False)
        record.pop('teardown',None)
        path.write_text(json.dumps(record))
        work=self.base/'build/kernel-usb-abandoned';work.mkdir(parents=True,mode=0o700)
        args=['qemu-system-aarch64']
        for name in ('aux.raw','root.raw'):
            os.link(self.source/name,work/name)
            args+=['-drive',f'file={work/name},format=raw']
        pids=[]
        for _ in range(2):
            proc=subprocess.Popen([sys.executable,'-c','pass'])
            pids.append(proc.pid);proc.wait(timeout=10)
        result={'label':'later','state':'observing','qemu_pid':pids[0],
                'execution':{'qemu_argv':args},'guest_files_service':{'requested':False}}
        (path.parent/'results.json').write_text(json.dumps(result))
        (path.parent/'qemu-stderr.log').write_text(
            f'qemu-system-aarch64: terminating on signal 15 from pid {pids[1]} ()\n')
        return path,work,result,pids

    def test_abandoned_recovery_preserves_unknown_exit_and_requires_explicit_reuse(self):
        path,old_work,result,_=self.abandoned_fixture()
        raw_result=(path.parent/'results.json').read_bytes()
        recovery=_recovery.recover(self.base,'later')
        self.assertTrue(recovery['recovered'])
        self.assertFalse(old_work.exists())
        saved=json.loads(path.read_text())
        self.assertIs(saved['qemu_reaped'],False)
        self.assertIsNone(saved['qemu_exit_code'])
        self.assertEqual((path.parent/'results.json').read_bytes(),raw_result)
        fw,work=self.session('after-abandoned')
        with self.assertRaisesRegex(ValueError,'Latest run of these target inodes'):
            fw.prepare_local_storage(work)
        fw.allow_unclean_storage=True;fw.prepare_local_storage(work)
        self.assertEqual(fw.source_storage_shutdown['run'],'later')
        self.assertTrue(fw.source_storage_shutdown['explicit_unclean_reuse'])
        self.assertFalse(fw.source_storage_shutdown['guest_shutdown_observed'])
        self.assertIsNone(fw.source_storage_shutdown['qemu_exit_code'])
        self.assertTrue(os.path.samefile(work/'root.raw',self.source/'root.raw'))
        with (work/'root.raw').open('rb') as stream:
            stream.seek(4096);self.assertEqual(stream.read(23),b'original retained bytes')

    def test_abandoned_recovery_refuses_live_recorded_process(self):
        path,work,result,_=self.abandoned_fixture()
        proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        try:
            result['qemu_pid']=proc.pid
            (path.parent/'results.json').write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError,'PID to be absent'):
                _recovery.recover(self.base,'later')
            self.assertTrue((work/'root.raw').exists())
        finally:proc.terminate();proc.wait(timeout=10)

    def test_abandoned_recovery_refuses_locked_or_mismatched_targets(self):
        path,work,_,_=self.abandoned_fixture()
        with (self.source/'root.raw').open('r+b') as held:
            fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError,'already in use'):
                _recovery.recover(self.base,'later')
            self.assertTrue((work/'aux.raw').exists())
        (work/'aux.raw').unlink();(work/'aux.raw').write_bytes(b'unrelated')
        with self.assertRaisesRegex(ValueError,'alias differs'):_recovery.recover(self.base,'later')
        self.assertEqual((work/'aux.raw').read_bytes(),b'unrelated')
        self.assertTrue((work/'root.raw').exists())

    def test_abandoned_recovery_rejects_modified_evidence_on_consumption(self):
        path,_,result,_=self.abandoned_fixture();_recovery.recover(self.base,'later')
        result['extra']='changed after recovery'
        (path.parent/'results.json').write_text(json.dumps(result))
        fw,work=self.session('changed-evidence');fw.allow_unclean_storage=True
        with self.assertRaisesRegex(ValueError,'no longer matches'):
            fw.prepare_local_storage(work)
        self.assertEqual(fw._storage_locks,[])
        self.assertFalse(list(work.iterdir()))

    def test_abandoned_recovery_consumption_still_requires_target_locks(self):
        self.abandoned_fixture();_recovery.recover(self.base,'later')
        with (self.source/'root.raw').open('r+b') as held:
            fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            fw,work=self.session('locked-after-recovery');fw.allow_unclean_storage=True
            with self.assertRaisesRegex(ValueError,'already in use'):
                fw.prepare_local_storage(work)
        self.assertEqual(fw._storage_locks,[])

    def test_abandoned_recovery_can_finish_after_alias_cleanup_was_interrupted(self):
        _,old_work,_,_=self.abandoned_fixture()
        (old_work/'aux.raw').unlink()
        _recovery.recover(self.base,'later')
        fw,work=self.session('after-partial-cleanup');fw.allow_unclean_storage=True
        fw.prepare_local_storage(work)
        self.assertFalse(old_work.exists())

    def test_existing_schema1_recovery_remains_consumable(self):
        path,_,_,_=self.abandoned_fixture();_recovery.recover(self.base,'later')
        record=json.loads(path.read_text())
        record['abandoned_recovery']['schema']=1
        record['abandoned_recovery'].pop('temporary_sockets')
        path.write_text(json.dumps(record))
        fw,work=self.session('after-schema1');fw.allow_unclean_storage=True
        fw.prepare_local_storage(work)
        self.assertTrue(os.path.samefile(work/'root.raw',self.source/'root.raw'))

    def reboot_fixture(self):
        path,work,result,pids=self.abandoned_fixture()
        result['started_at_utc']='1970-01-01T00:01:40+00:00'
        for name in ('q.sock','inspect.sock'):
            result['execution']['qemu_argv']+=['-qmp',f'unix:{work/name},server=on,wait=off']
        for name in ('u.sock','kernel-usb.sock'):
            result['execution']['qemu_argv']+=['-chardev',f'socket,id=fixture,path={work/name},server=on,wait=off']
        (path.parent/'results.json').write_text(json.dumps(result))
        os.utime(path.parent/'results.json',(150,150))
        (path.parent/'qemu-stderr.log').write_text('last pre-reboot output, no termination event\n')
        previous='1'*32;unit='macos-local-test.service'
        row={'_BOOT_ID':previous,'USER_UNIT':unit,'_COMM':'systemd','_UID':str(os.geteuid()),
             'MESSAGE_ID':'39f53479d3a045ac8e11786248231fbf','JOB_TYPE':'start','JOB_RESULT':'done',
             '__REALTIME_TIMESTAMP':'99500000','__CURSOR':'fixture-journal-cursor'}
        journal=subprocess.CompletedProcess([],0,json.dumps(row)+'\n','')
        current={'boot_id':'2'*32,'started_epoch_seconds':200}
        return path,work,result,pids,{'previous_boot_id':previous,'service_unit':unit},journal,current

    def test_reboot_recovery_uses_old_boot_journal_and_only_recorded_stale_sockets(self):
        path,old_work,_,_,options,journal,current=self.reboot_fixture()
        for name in ('q.sock','inspect.sock','u.sock','kernel-usb.sock'):
            with socket.socket(socket.AF_UNIX) as endpoint:endpoint.bind(str(old_work/name))
        raw_result=(path.parent/'results.json').read_bytes()
        with patch('firmware_session.recovery_host_boot',return_value=current), \
             patch('firmware_session.subprocess.run',return_value=journal):
            _recovery.recover(self.base,'later',**options)
            fw,work=self.session('after-reboot');fw.allow_unclean_storage=True
            fw.prepare_local_storage(work)
        saved=json.loads(path.read_text())
        self.assertEqual(saved['abandoned_recovery']['host_reboot']['previous_boot_id'],'1'*32)
        self.assertIsNone(saved['abandoned_recovery']['termination_log_line'])
        self.assertEqual(set(saved['abandoned_recovery']['pids']),{'qemu'})
        self.assertIs(saved['qemu_reaped'],False);self.assertIsNone(saved['qemu_exit_code'])
        self.assertFalse(old_work.exists())
        self.assertEqual((path.parent/'results.json').read_bytes(),raw_result)
        self.assertTrue(os.path.samefile(work/'root.raw',self.source/'root.raw'))

    def test_reboot_recovery_rejects_same_boot_missing_journal_or_new_result(self):
        path,work,_,_,options,journal,current=self.reboot_fixture()
        with self.assertRaisesRegex(ValueError,'termination evidence'):
            _recovery.recover(self.base,'later')
        with patch('firmware_session.recovery_host_boot',return_value={**current,'boot_id':'1'*32}), \
             self.assertRaisesRegex(ValueError,'before the current host boot'):
            _recovery.recover(self.base,'later',**options)
        with patch('firmware_session.recovery_host_boot',return_value=current), \
             patch('firmware_session.subprocess.run',return_value=subprocess.CompletedProcess([],0,'','')), \
             self.assertRaisesRegex(ValueError,'unique previous-boot'):
            _recovery.recover(self.base,'later',**options)
        os.utime(path.parent/'results.json',(201,201))
        with patch('firmware_session.recovery_host_boot',return_value=current), \
             self.assertRaisesRegex(ValueError,'before the current host boot'):
            _recovery.recover(self.base,'later',**options)
        self.assertTrue((work/'root.raw').exists())

    def test_reboot_recovery_rejects_live_pid_locked_target_and_socket_mismatch(self):
        path,work,result,_,options,journal,current=self.reboot_fixture()
        original=(path.parent/'results.json').read_bytes()
        with patch('firmware_session.recovery_host_boot',return_value=current), \
             patch('firmware_session.subprocess.run',return_value=journal):
            result['qemu_pid']=os.getpid()
            (path.parent/'results.json').write_text(json.dumps(result));os.utime(path.parent/'results.json',(150,150))
            with self.assertRaisesRegex(ValueError,'PID to be absent'):_recovery.recover(self.base,'later',**options)
            (path.parent/'results.json').write_bytes(original);os.utime(path.parent/'results.json',(150,150))
            with (self.source/'root.raw').open('r+b') as held:
                fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError,'already in use'):_recovery.recover(self.base,'later',**options)
            with socket.socket(socket.AF_UNIX) as endpoint:
                endpoint.bind(str(work/'inspect.sock'));endpoint.listen()
                with self.assertRaisesRegex(ValueError,'live kernel endpoint'):_recovery.recover(self.base,'later',**options)
            (work/'inspect.sock').unlink();(work/'inspect.sock').write_text('unrelated regular file')
            with self.assertRaisesRegex(ValueError,'not an owned socket'):_recovery.recover(self.base,'later',**options)
            (work/'inspect.sock').unlink();(work/'unexpected.sock').write_text('unrecorded')
            with self.assertRaisesRegex(ValueError,'Unexpected temporary contents'):_recovery.recover(self.base,'later',**options)
        self.assertTrue((work/'root.raw').exists())
        self.assertTrue((work/'aux.raw').exists())

    def test_existing_work_file_survives_partial_link_failure(self):
        fw,work=self.session()
        marker=work/'root.raw';marker.write_bytes(b'existing input')
        with self.assertRaises(FileExistsError):fw.prepare_local_storage(work)
        self.assertEqual(marker.read_bytes(),b'existing input')
        self.assertFalse((work/'aux.raw').exists())
        self.assertEqual(fw._storage_locks,[])
        for name in ('aux.raw','root.raw'):
            with (self.source/name).open('rb') as stream:
                stream.seek(4096);self.assertEqual(stream.read(23),b'original retained bytes')

    def test_manifest_identity_owner_and_path_are_checked(self):
        expected=dict(self.record['files']['aux.raw'])
        for key,bad in [('inode',expected['inode']+1),('device',expected['device']+1),
                        ('bytes',1),('path',str(self.base/'other.raw')),
                        ('owner_uid',os.geteuid()+1)]:
            with self.subTest(key=key):
                self.record['files']['aux.raw']=dict(expected,**{key:bad});self.save_record()
                fw,work=self.session()
                with self.assertRaises(ValueError):fw.prepare_local_storage(work)
                self.assertEqual(fw._storage_locks,[])
        self.record['files']['aux.raw']=expected;self.save_record()

    def test_legacy_ownerless_manifest_uses_real_current_owner(self):
        for row in self.record['files'].values():del row['owner_uid']
        self.save_record()
        fw,work=self.session();fw.prepare_local_storage(work)
        self.assertEqual((work/'aux.raw').stat().st_uid,os.geteuid())

    def test_symlink_source_or_directory_is_not_followed(self):
        original=self.source/'aux.raw';moved=self.source/'original.raw'
        original.rename(moved);original.symlink_to(moved)
        fw,work=self.session()
        with self.assertRaises(OSError):fw.prepare_local_storage(work)
        self.assertEqual(fw._storage_locks,[])
        original.unlink();moved.rename(original)
        directory=self.source.with_name('original-storage')
        self.source.rename(directory);self.source.symlink_to(directory,target_is_directory=True)
        with self.assertRaises(ValueError):fw.prepare_local_storage(work)

    def test_close_holds_locks_through_teardown_and_cleans_only_temporary_links(self):
        fw,work=self.session();fw.prepare_local_storage(work)
        parent=self.base/'private/signed-restore/next'
        parent.mkdir(parents=True,mode=0o700)
        fw.retain_blank_storage(parent/'storage')
        other,other_work=self.session('contender')
        # A real owned non-VM subprocess exits after the synthetic quit request.
        process=subprocess.Popen([sys.executable,'-c','import sys; sys.stdin.buffer.read(1)'],stdin=subprocess.PIPE)
        fw.proc=process
        def quit_request(command):
            self.assertEqual(command,'quit')
            self.assertIsNone(process.poll())
            self.assertTrue(work.exists())
            with self.assertRaisesRegex(ValueError,'already in use'):other.prepare_local_storage(other_work)
            process.stdin.write(b'q');process.stdin.flush()
            return {}
        fw.request=quit_request
        save=fw._save_retained_storage
        def save_after_teardown():
            self.assertIsNotNone(process.poll())
            self.assertFalse(work.exists())
            with self.assertRaisesRegex(ValueError,'already in use'):other.prepare_local_storage(other_work)
            save()
        fw._save_retained_storage=save_after_teardown
        try:
            fw.close()
            fw._save_retained_storage=save
            self.assertEqual(process.returncode,0)
            self.assertFalse(work.exists())
            self.assertTrue((self.source/'root.raw').exists())
            saved=json.loads((fw.logs/'retained-storage.json').read_text())
            self.assertTrue(saved['qemu_reaped'])
            self.assertTrue(saved['temporary_links_removed'])
            self.assertEqual(saved['teardown'],'qmp_quit')
            fw.close()
            self.assertEqual(json.loads((fw.logs/'retained-storage.json').read_text())['teardown'],'qmp_quit')
            other.prepare_local_storage(other_work)
        finally:
            fw._save_retained_storage=save
            if process.poll() is None:process.kill();process.wait()
            process.stdin.close()

    def test_resume_option_validation(self):
        for options in [{'system_disk':True},{'blank_root_disk':True},{'aux_size_mib':4}]:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):FirmwareSession(self.base/'invalid',resume_storage_run=self.label,**options)
        for label in ['', '../closed-source','a/b','a'*61,4]:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):FirmwareSession(self.base/'invalid',resume_storage_run=label)

    def test_pauth_algorithm_is_explicit_and_defaults_to_qarma5(self):
        default=FirmwareSession(self.base/'pauth-default')
        self.assertEqual(default.pauth_algorithm,'qarma5')
        self.assertEqual(default.cpu_model,'max,pauth-qarma5=on,cntfrq=24000000,x-cntfrq-exact=on')
        fast=FirmwareSession(self.base/'pauth-impdef',pauth_algorithm='impdef')
        self.assertEqual(fast.cpu_model,'max,pauth-impdef=on,cntfrq=24000000,x-cntfrq-exact=on')
        for algorithm in ['', 'QARMA5', 'off', 'impdef,pauth=off', None]:
            with self.subTest(algorithm=algorithm):
                with self.assertRaises(ValueError):
                    FirmwareSession(self.base/'pauth-invalid',pauth_algorithm=algorithm)
        self.assertFalse((self.base/'pauth-invalid').exists())

    def test_pauth_memo_is_opt_in_and_recorded_without_algorithm_change(self):
        default=FirmwareSession(self.base/'memo-default')
        self.assertFalse(default.execution_metadata()['pauth']['memo_requested'])
        selected=FirmwareSession(self.base/'memo-selected',pauth_memo=True)
        self.assertEqual(selected.cpu_model,default.cpu_model+',x-pauth-memo=on')
        self.assertEqual(selected.execution_metadata()['pauth']['algorithm'],'qarma5')
        self.assertTrue(selected.execution_metadata()['pauth']['memo_requested'])
        for options in ({'pauth_memo':'on'},{'pauth_memo':True,'pauth_algorithm':'impdef'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                FirmwareSession(self.base/'memo-invalid',**options)
        self.assertFalse((self.base/'memo-invalid').exists())


    def test_pauth_capacity_and_readonly_aggregate_qom(self):
        selected=FirmwareSession(self.base/'memo-counts',vcpus=2,pauth_memo=True,
                                 pauth_memo_entries=1024,pauth_memo_stats=True)
        self.addCleanup(selected.close)
        self.assertTrue(selected.cpu_model.endswith('x-pauth-memo=on,x-pauth-memo-entries=1024,x-pauth-memo-stats=on'))
        large=FirmwareSession(self.base/'memo-large',pauth_memo=True,pauth_memo_entries=16384)
        self.addCleanup(large.close)
        self.assertTrue(large.cpu_model.endswith('x-pauth-memo=on,x-pauth-memo-entries=16384'))
        self.assertEqual(large.execution_metadata()['pauth']['memo_effective_entries'],16384)
        self.assertEqual(selected.execution_metadata()['pauth']['memo_effective_entries'],1024)
        rows=[{'cpu-index':i,'qom-path':f'/machine/unattached/device[{i}]'} for i in range(2)]
        calls=[]
        def request(command,arguments=None):
            calls.append((command,arguments))
            if command=='query-cpus-fast':return rows
            self.assertEqual(command,'qom-get')
            self.assertIn(arguments['property'],('x-pauth-memo-hits','x-pauth-memo-misses'))
            return 19 if arguments['property'].endswith('hits') else 7
        with patch.object(selected,'request',side_effect=request):
            result=selected.capture_pauth_memo_statistics()
        self.assertEqual(result['vcpus'],[{'cpu_index':i,'hits':19,'misses':7} for i in range(2)])
        self.assertEqual(len(calls),5)
        for bad in (True,-1,1.0,2**64):
            with patch.object(selected,'request',side_effect=[rows,bad]),self.assertRaises(ValueError):
                selected.capture_pauth_memo_statistics()
        default=FirmwareSession(self.base/'memo-no-counts',pauth_memo=True)
        self.addCleanup(default.close)
        with patch.object(default,'request') as query:
            self.assertEqual(default.capture_pauth_memo_statistics()['state'],'not_requested')
            query.assert_not_called()
        for options in ({'pauth_memo_entries':64},{'pauth_memo_stats':True},
                        {'pauth_memo':True,'pauth_memo_entries':1024.0},
                        {'pauth_memo':True,'pauth_memo_entries':65}):
            with self.assertRaises(ValueError):FirmwareSession(self.base/'bad-memo-options',**options)



class VCPUAffinityTests(unittest.TestCase):
    def test_omission_has_no_affinity_syscalls(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as directory, \
             patch('firmware_session.os.sched_getaffinity') as read, \
             patch('firmware_session.os.sched_setaffinity') as write:
            self.assertIsNone(parse_vcpu_affinity(None))
            fw=FirmwareSession(Path(directory)/'default')
            self.assertEqual(fw.apply_vcpu_affinity()['state'],'not_requested')
            read.assert_not_called();write.assert_not_called()

    def test_lists_ranges_and_rejection_before_log_creation(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as directory, \
             patch('firmware_session.os.sched_getaffinity',return_value=set(range(8))):
            self.assertEqual(parse_vcpu_affinity('0-2,4,4,6-7'),[0,1,2,4,6,7])
            for value in ('','-1','2-1','0,,1','0-8','999999999','0, 1',True,[0,1]):
                with self.subTest(value=value),self.assertRaises(ValueError):
                    FirmwareSession(Path(directory)/'invalid',vcpu_affinity=value)
                self.assertFalse((Path(directory)/'invalid').exists())
        with patch('firmware_session.os.sched_getaffinity',return_value={0,2,4}),self.assertRaises(ValueError):
            parse_vcpu_affinity('0-4')

    def test_owned_threads_only_exact_readback_and_fail_closed(self):
        parent=os.getpid();pid=999991;targets=(999992,999993);other=999994
        full=list(range(8));mask=[0,1]
        before={tid:{'start_time_ticks':tid,'cpus':full} for tid in (pid,*targets,other)}
        controller={parent:{'start_time_ticks':1,'cpus':full}}
        def proc_text(path,*args,**kwargs):
            if str(path)==f'/proc/{pid}/status':return f'PPid:\t{parent}\n'
            tid=int(path.parent.name)
            return f'{tid} (CPU) '+' '.join(['S']+['0']*18+[str(tid)])
        for mode in ('ok','outside_tid','running','short_mask','other_changed'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory(dir=ROOT/'build') as directory:
                after={tid:{**row,'cpus':mask if tid in targets else full} for tid,row in before.items()}
                if mode=='other_changed':after[other]={'start_time_ticks':other,'cpus':mask}
                with patch('firmware_session.os.sched_getaffinity',side_effect=lambda tid:({0} if mode=='short_mask' else set(mask)) if tid in targets else set(full)), \
                     patch('firmware_session.os.sched_setaffinity') as write, \
                     patch('firmware_session.host_thread_affinities',side_effect=[before,controller,after,controller]), \
                     patch('firmware_session.Path.read_text',proc_text):
                    fw=FirmwareSession(Path(directory)/'logs',tcg_thread='multi',vcpu_affinity='0-1')
                    fw.proc=SimpleNamespace(pid=pid,poll=lambda:None);fw.args=['qemu','-S']
                    fw.vcpu_thread_observation={'observed':True,'configured_vcpus_match':True,
                        'vcpus':[{'cpu_index':i,'host_thread_id':tid} for i,tid in enumerate(targets)]}
                    if mode=='outside_tid':fw.vcpu_thread_observation['vcpus'][1]['host_thread_id']=999999
                    with patch.object(fw,'request',return_value={'running':mode=='running'}):
                        if mode=='ok':
                            observed=fw.apply_vcpu_affinity()
                            self.assertTrue(observed['verified'])
                            self.assertTrue(observed['non_vcpu_masks_unchanged'])
                            self.assertTrue(observed['controller_masks_unchanged'])
                            self.assertEqual([row['actual_cpu_ids'] for row in observed['vcpus']],[mask,mask])
                        else:
                            with self.assertRaises(RuntimeError):fw.apply_vcpu_affinity()
                            self.assertFalse(fw.vcpu_affinity_observation['verified'])
                    applied=[call.args[0] for call in write.call_args_list]
                    self.assertEqual(applied,[] if mode in ('outside_tid','running') else [targets[0]] if mode=='short_mask' else list(targets))
                    self.assertTrue(all(call.args[1]==set(mask) for call in write.call_args_list))


class TCGThreadingTests(unittest.TestCase):
    def test_sound_stream_count_changes_only_explicit_device_property(self):
        with tempfile.TemporaryDirectory(prefix='sound-streams-argv-',dir=ROOT/'build') as directory:
            base=Path(directory);(base/'build').mkdir();rom=base/'rom';rom.write_bytes(b'sound stream argv fixture')
            captured={}
            class LaunchIntercept(Exception):pass
            with patch('firmware_session.ROOT',base),patch('firmware_session.ROM',rom), \
                 patch('firmware_session.EXPECTED',hashlib.sha256(rom.read_bytes()).hexdigest()):
                for count in (None,1,2):
                    fw=FirmwareSession(base/'logs',virtio_sound=True,sound_queue_size=512,sound_streams=count)
                    def intercept(args,**kwargs):
                        captured[count]=[str(value).replace(fw.temp.name,'<work>') for value in args]
                        raise LaunchIntercept()
                    with patch('firmware_session.subprocess.Popen',side_effect=intercept),self.assertRaises(LaunchIntercept):fw.__enter__()
                    audio=fw.execution_metadata()['audio']
                    self.assertEqual(audio['streams']['requested'],count)
                    self.assertFalse(audio['streams']['runtime_verified'])
                    self.assertEqual((audio['advertised_output_streams'],audio['advertised_input_streams']),(1,(count or 2)-1))
                for count in (1,2):
                    expected=[value+f',streams={count}' if value.startswith('virtio-sound-pci,') else value for value in captured[None]]
                    self.assertEqual(captured[count],expected)
                with self.assertRaises(ValueError):FirmwareSession(base/'invalid',sound_streams=1)

    def test_sound_queue_size_changes_only_requested_device_property(self):
        with tempfile.TemporaryDirectory(prefix='sound-size-argv-',dir=ROOT/'build') as directory:
            base=Path(directory);(base/'build').mkdir();rom=base/'rom';rom.write_bytes(b'sound queue argv fixture')
            captured={}
            class LaunchIntercept(Exception):pass
            with patch('firmware_session.ROOT',base),patch('firmware_session.ROM',rom), \
                 patch('firmware_session.EXPECTED',hashlib.sha256(rom.read_bytes()).hexdigest()):
                for size in (None,64,512):
                    fw=FirmwareSession(base/'logs',virtio_sound=True,sound_queue_size=size)
                    def intercept(args,**kwargs):
                        captured[size]=[str(value).replace(fw.temp.name,'<work>') for value in args]
                        raise LaunchIntercept()
                    with patch('firmware_session.subprocess.Popen',side_effect=intercept),self.assertRaises(LaunchIntercept):
                        fw.__enter__()
                    metadata=fw.execution_metadata()['audio']['queue_size']
                    self.assertEqual(metadata['requested'],size)
                    self.assertEqual(metadata['configured_maximum'],size or 64)
                    self.assertFalse(metadata['runtime_verified'])
                    self.assertIsNone(metadata['guest_selected'])
                for size in (64,512):
                    expected=[value+f',queue-size={size}' if value.startswith('virtio-sound-pci,') else value
                              for value in captured[None]]
                    self.assertEqual(captured[size],expected)
                with self.assertRaises(ValueError):FirmwareSession(base/'invalid',sound_queue_size=512)

    def test_sound_control_trace_is_selected_at_launch_only_when_requested(self):
        with tempfile.TemporaryDirectory(prefix='sound-trace-argv-',dir=ROOT/'build') as directory:
            base=Path(directory);(base/'build').mkdir();rom=base/'rom';rom.write_bytes(b'sound trace argv fixture')
            class LaunchIntercept(Exception):pass
            with patch('firmware_session.ROOT',base),patch('firmware_session.ROM',rom), \
                 patch('firmware_session.EXPECTED',hashlib.sha256(rom.read_bytes()).hexdigest()):
                for block in (False,True):
                    for selected in (False,True):
                        logs=base/f'logs-{block}-{selected}'
                        fw=FirmwareSession(logs,virtio_sound=True,sound_pcm_trace=selected,
                                           trace_block_io=block)
                        seen=[]
                        def intercept(args,**kwargs):seen.extend(args);raise LaunchIntercept()
                        with patch('firmware_session.subprocess.Popen',side_effect=intercept),self.assertRaises(LaunchIntercept):
                            fw.__enter__()
                        self.assertEqual('-trace' in seen,block or selected)
                        events=(logs/'trace-events.txt').read_text().splitlines() if block or selected else []
                        for event in ('virtio_snd_get_config','virtio_snd_handle_code','virtio_snd_empty_info'):
                            self.assertEqual(event in events,selected)
                        self.assertEqual('bdif_vblk_*' in events,block)
                        if block or selected:
                            path=logs/('bdif-block-io.log' if block else 'virtio-sound-control.log')
                            self.assertEqual(seen[seen.index('-trace')+1],f'events={logs/"trace-events.txt"},file={path}')
                        self.assertEqual(bool(fw.execution_metadata()['audio']['pcm_trace']['control_events']),selected)

    def test_selected_ram_changes_only_capacity_and_records_request(self):
        with tempfile.TemporaryDirectory(prefix='ram-argv-',dir=ROOT/'build') as directory:
            base=Path(directory);(base/'build').mkdir();rom=base/'rom';rom.write_bytes(b'RAM argv fixture')
            captured={}
            class LaunchIntercept(Exception):pass
            with patch('firmware_session.ROOT',base),patch('firmware_session.ROM',rom), \
                 patch('firmware_session.EXPECTED',hashlib.sha256(rom.read_bytes()).hexdigest()):
                for gib in (4,8):
                    fw=FirmwareSession(base/'logs',ram_gib=gib)
                    self.assertEqual(fw.ram_bytes,gib * 1024**3)
                    metadata=fw.execution_metadata()
                    self.assertEqual(metadata['ram_gib_requested'],gib)
                    self.assertEqual(metadata['ram_bytes_requested'],gib * 1024**3)
                    def intercept(args,**kwargs):
                        captured[gib]=[str(value).replace(fw.temp.name,'<work>') for value in args]
                        raise LaunchIntercept()
                    with patch('firmware_session.subprocess.Popen',side_effect=intercept),self.assertRaises(LaunchIntercept):fw.__enter__()
            expected=list(captured[4]);expected[expected.index('-m')+1]='8G'
            expected[expected.index('-object')+1]='memory-backend-memfd,id=ram,size=8G,share=on'
            self.assertEqual(captured[8],expected)
            default=FirmwareSession(base/'default')
            self.assertEqual((default.ram_gib,default.ram_bytes),(4,4 * 1024**3))
            for gib in (True,0,2,6,16,4.0,None,'8'):
                with self.subTest(gib=gib),self.assertRaises(ValueError):
                    FirmwareSession(base/'invalid',ram_gib=gib)
                self.assertFalse((base/'invalid').exists())

    def test_selected_cpu_indices_and_mttcg_threads_are_verified(self):
        with tempfile.TemporaryDirectory(prefix='four-cpu-',dir=ROOT/'build') as directory:
            for count in (2,4,8):
                fw=FirmwareSession(Path(directory)/'logs',vcpus=count,tcg_thread='multi')
                rows=[{'cpu-index':i,'thread-id':101+i} for i in range(count)]
                with patch.object(fw,'request',return_value=rows):observed=fw.capture_vcpu_threads()
                self.assertTrue(observed['configured_vcpus_match']);self.assertTrue(observed['requested_mode_verified'])
                self.assertEqual(observed['distinct_host_thread_ids'],count)
                for bad in (rows[:-1],[*rows[:-1],{'cpu-index':count,'thread-id':101+count}],
                            [*rows[:-1],{'cpu-index':count-1,'thread-id':101}]):
                    with patch.object(fw,'request',return_value=bad),self.assertRaises(RuntimeError):fw.capture_vcpu_threads()
            for count in (True,1,3,16,None,'8'):
                with self.assertRaises(ValueError):FirmwareSession(Path(directory)/'invalid',vcpus=count)
            self.assertFalse((Path(directory)/'invalid').exists())

    def test_selected_cpu_launch_only_changes_smp(self):
        with tempfile.TemporaryDirectory(prefix='four-argv-',dir=ROOT/'build') as directory:
            base=Path(directory);(base/'build').mkdir();rom=base/'rom';rom.write_bytes(b'CPU argv fixture')
            captured={}
            class LaunchIntercept(Exception):pass
            with patch('firmware_session.ROOT',base),patch('firmware_session.ROM',rom), \
                 patch('firmware_session.EXPECTED',hashlib.sha256(rom.read_bytes()).hexdigest()):
                for count in (2,4,8):
                    fw=FirmwareSession(base/'logs',vcpus=count,tcg_thread='multi')
                    def intercept(args,**kwargs):
                        captured[count]=[str(value).replace(fw.temp.name,'<work>') for value in args]
                        raise LaunchIntercept()
                    with patch('firmware_session.subprocess.Popen',side_effect=intercept),self.assertRaises(LaunchIntercept):fw.__enter__()
            for count in (4,8):
                expected=list(captured[2]);expected[expected.index('-smp')+1]=str(count)
                self.assertEqual(captured[count],expected)

    def test_refresh_is_explicit_recorded_and_requires_a_window(self):
        with tempfile.TemporaryDirectory(prefix='refresh-env-',dir=ROOT/'build') as directory:
            base=Path(directory)
            with patch.dict(os.environ,{'REIMS_VGPU_WINDOW_REFRESH':'120'}):
                default=FirmwareSession(base/'default',native_window=True)
                selected=FirmwareSession(base/'selected',native_window=True,display_refresh_hz=30)
                self.assertEqual(default.qemu_environment()['REIMS_VGPU_WINDOW_REFRESH'],'120')
                self.assertEqual(selected.qemu_environment()['REIMS_VGPU_WINDOW_REFRESH'],'30')
                self.assertEqual(selected.execution_metadata()['presentation']['reims_vgpu_window_refresh'],'30')
                default.close();selected.close()
            for value,window in [(30,False),(0,True),(float('nan'),True),(1001,True),(True,True)]:
                with self.subTest(value=value,window=window),self.assertRaises(ValueError):
                    FirmwareSession(base/'invalid',native_window=window,display_refresh_hz=value)
                self.assertFalse((base/'invalid').exists())

    def test_exact_half_blend_is_explicit_and_preserves_other_policies(self):
        with tempfile.TemporaryDirectory(prefix='precision-env-',dir=ROOT/'build') as directory:
            base=Path(directory)
            with patch.dict(os.environ,{'REIMS_VGPU_EXACT':'texel_conversion'}):
                default=FirmwareSession(base/'default')
                selected=FirmwareSession(base/'selected',exact_half_blend=True)
                self.assertEqual(default.qemu_environment()['REIMS_VGPU_EXACT'],'texel_conversion')
                self.assertEqual(selected.qemu_environment()['REIMS_VGPU_EXACT'],'texel_conversion,half_blend')
                meta=selected.execution_metadata()['presentation']
                self.assertTrue(meta['exact_half_blend_requested'])
                self.assertEqual(meta['reims_vgpu_exact'],'texel_conversion,half_blend')
                default.close();selected.close()
            with self.assertRaises(ValueError):
                FirmwareSession(base/'invalid',exact_half_blend='yes')
            self.assertFalse((base/'invalid').exists())

    def test_user_network_changes_only_explicit_backend_and_slot5_nic(self):
        with tempfile.TemporaryDirectory(prefix='nat-argv-',dir=ROOT/'build') as directory:
            base=Path(directory);(base/'build').mkdir();rom=base/'rom';rom.write_bytes(b'network argv fixture')
            captured={};metadata={}
            class LaunchIntercept(Exception):pass
            with patch('firmware_session.ROOT',base),patch('firmware_session.ROM',rom), \
                 patch('firmware_session.EXPECTED',hashlib.sha256(rom.read_bytes()).hexdigest()):
                for enabled in (False,True):
                    fw=FirmwareSession(base/'logs',user_network=enabled)
                    def intercept(args,**kwargs):
                        captured[enabled]=[str(value).replace(fw.temp.name,'<work>') for value in args]
                        raise LaunchIntercept()
                    with patch('firmware_session.subprocess.Popen',side_effect=intercept),self.assertRaises(LaunchIntercept):
                        fw.__enter__()
                    metadata[enabled]=fw.execution_metadata()['network']
                with self.assertRaises(ValueError):FirmwareSession(base/'invalid',user_network='yes')
                self.assertFalse((base/'invalid').exists())
            default,enabled=captured[False],captured[True]
            self.assertEqual(default[default.index('-nic')+1],'none')
            self.assertNotIn('-netdev',default)
            self.assertNotIn('-nic',enabled)
            start=enabled.index('-netdev')
            self.assertEqual(enabled[start:start+4],['-netdev','user,id=guestnet','-device',
                'virtio-net-pci,netdev=guestnet,id=guestnet-pci,disable-legacy=on,addr=0x5,x-ascending-capabilities=on'])
            self.assertEqual(enabled[:start]+['-nic','none']+enabled[start+4:],default)
            self.assertFalse(any('hostfwd' in value or 'bridge=' in value or 'tap,' in value for value in enabled))
            self.assertFalse(metadata[False]['requested'])
            self.assertTrue(metadata[True]['requested'])
            self.assertEqual(metadata[True]['guest_connectivity'],'unknown')

    def test_net_control_queue_size_changes_only_explicit_frontend_argument(self):
        with tempfile.TemporaryDirectory(prefix='net-control-argv-',dir=ROOT/'build') as directory:
            base=Path(directory);(base/'build').mkdir();rom=base/'rom';rom.write_bytes(b'network control queue argv fixture')
            captured={};metadata={}
            class LaunchIntercept(Exception):pass
            with patch('firmware_session.ROOT',base),patch('firmware_session.ROM',rom), \
                 patch('firmware_session.EXPECTED',hashlib.sha256(rom.read_bytes()).hexdigest()):
                for size in (None,64,512):
                    fw=FirmwareSession(base/'logs',user_network=True,net_control_queue_size=size)
                    def intercept(args,**kwargs):
                        captured[size]=[str(value).replace(fw.temp.name,'<work>') for value in args]
                        raise LaunchIntercept()
                    with patch('firmware_session.subprocess.Popen',side_effect=intercept),self.assertRaises(LaunchIntercept):fw.__enter__()
                    metadata[size]=fw.execution_metadata()['network']['control_queue_size']
                    # Exercise the post-spawn metadata branch using the captured argv;
                    # this marker is a fixture, not a real process or networking proof.
                    fw.proc=SimpleNamespace(returncode=0,poll=lambda:0)
                    recorded=fw.execution_metadata()['network']['control_queue_size']['actual_device_arguments']
                    self.assertEqual(recorded,[x for x in fw.args if x.startswith('virtio-net-pci,')])
                    fw.proc=None
                for enabled,size in ((False,64),(False,512),(True,True),(True,512.0),(True,'512'),(True,128)):
                    with self.subTest(enabled=enabled,size=size),self.assertRaises(ValueError):
                        FirmwareSession(base/'invalid',user_network=enabled,net_control_queue_size=size)
                self.assertFalse((base/'invalid').exists())
            baseline=captured[None]
            device=next(x for x in baseline if x.startswith('virtio-net-pci,'))
            self.assertNotIn('ctrl_queue_size',device)
            for size in (64,512):
                changed=[x.replace(',ctrl_queue_size='+str(size),'') for x in captured[size]]
                self.assertEqual(changed,baseline)
                self.assertIn(device+',ctrl_queue_size='+str(size),captured[size])
                self.assertEqual(metadata[size]['requested'],size)
                self.assertEqual(metadata[size]['default'],64)
                self.assertEqual(metadata[size]['configured_maximum'],size)
                self.assertFalse(metadata[size]['runtime_verified'])
                self.assertIsNone(metadata[size]['guest_selected'])
                self.assertIsNone(metadata[size]['actual_device_arguments'])  # Launch was intercepted before a process existed.

    def test_readonly_rsp_is_opt_in_local_endpoint_in_actual_launch_argv(self):
        with tempfile.TemporaryDirectory(prefix='rsp-argv-',dir=ROOT/'build') as directory:
            base=Path(directory);(base/'build').mkdir();rom=base/'rom';rom.write_bytes(b'argv fixture')
            class LaunchIntercept(Exception):pass
            with patch('firmware_session.ROOT',base),patch('firmware_session.ROM',rom), \
                    patch('firmware_session.EXPECTED',hashlib.sha256(rom.read_bytes()).hexdigest()):
                for enabled in (False,True):
                    fw=FirmwareSession(base/'logs',readonly_rsp=enabled)
                    seen=[]
                    def intercept(args,**kwargs):seen.extend(args);raise LaunchIntercept()
                    with patch('firmware_session.subprocess.Popen',side_effect=intercept),self.assertRaises(LaunchIntercept):
                        fw.__enter__()
                    self.assertEqual('-gdb' in seen,enabled)
                    if enabled:
                        value=seen[seen.index('-gdb')+1]
                        self.assertTrue(value.startswith('unix:'))
                        self.assertTrue(value.endswith('/current-process.sock,server=on,wait=off'))
                    else:self.assertIsNone(fw.rsp_path)

    def test_modes_only_change_accel_in_the_actual_launch_argument_list(self):
        # Exercise the production __enter__ argv consumer but intercept Popen
        # before it can create a process, QMP socket, USB peer or guest.
        with tempfile.TemporaryDirectory(prefix='tcg-argv-',dir=ROOT/'build') as directory:
            base=Path(directory);(base/'build').mkdir();rom=base/'rom';rom.write_bytes(b'self-authored argv fixture')
            captured={}
            class LaunchIntercept(Exception):pass
            with patch('firmware_session.ROOT',base), patch('firmware_session.ROM',rom), \
                    patch('firmware_session.EXPECTED',hashlib.sha256(rom.read_bytes()).hexdigest()):
                for mode in ('auto','multi','single'):
                    fw=FirmwareSession(base/'logs',**({} if mode=='auto' else {'tcg_thread':mode}))
                    def intercept(args,**kwargs):
                        captured[mode]=[str(x).replace(fw.temp.name,'<work>') for x in args]
                        raise LaunchIntercept()
                    with patch('firmware_session.subprocess.Popen',side_effect=intercept) as launch:
                        with self.assertRaises(LaunchIntercept):fw.__enter__()
                        launch.assert_called_once()
                    self.assertTrue(fw._closed)
                    self.assertIsNone(fw.execution_metadata()['qemu_argv'])
                    self.assertEqual(fw.tcg_thread_requested,mode)
            baseline=captured['auto'];index=baseline.index('-accel')+1
            self.assertEqual(baseline[index],'tcg')
            self.assertEqual(baseline[baseline.index('-smp')+1],'2')
            self.assertEqual(baseline[baseline.index('-cpu')+1],
                             'max,pauth-qarma5=on,cntfrq=24000000,x-cntfrq-exact=on')
            self.assertNotIn('-icount',baseline)
            for mode in ('single','multi'):
                expected=list(baseline);expected[index]='tcg,thread='+mode
                self.assertEqual(captured[mode],expected)

    def test_invalid_mode_fails_before_creating_session_files(self):
        with tempfile.TemporaryDirectory(prefix='tcg-invalid-',dir=ROOT/'build') as directory:
            target=Path(directory)/'should-not-exist'
            for mode in ('','parallel','single,icount=1',None,False,[]):
                with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'Unknown TCG threading mode'):
                    FirmwareSession(target,tcg_thread=mode)
                self.assertFalse(target.exists())
            self.assertEqual(tcg_accel_argument(),'tcg')

    def test_observed_host_thread_ids_are_distinct_from_the_requested_mode(self):
        with tempfile.TemporaryDirectory(prefix='tcg-qmp-',dir=ROOT/'build') as directory:
            for mode,ids in (('auto',(101,102)),('auto',(101,101)),
                             ('single',(101,101)),('multi',(101,102))):
                with self.subTest(mode=mode,ids=ids):
                    fw=FirmwareSession(Path(directory)/'logs',tcg_thread=mode)
                    rows=[{'cpu-index':i,'thread-id':tid} for i,tid in enumerate(ids)]
                    with patch.object(fw,'request',return_value=rows) as query:
                        observed=fw.capture_vcpu_threads()
                        query.assert_called_once_with('query-cpus-fast')
                    self.assertEqual(observed['vcpu_count'],2)
                    self.assertEqual(observed['distinct_host_thread_ids'],len(set(ids)))
                    self.assertEqual(observed['requested_mode_verified'],None if mode=='auto' else True)
                    fw.args=['qemu','-accel',fw.accel_argument];fw.proc=object()
                    metadata=fw.execution_metadata()
                    self.assertEqual(metadata['tcg_thread_requested'],mode)
                    self.assertEqual(metadata['qemu_argv'],fw.args)
                    self.assertEqual(metadata['qemu_vcpu_threads'],observed)
                    metadata['qemu_argv'].append('does-not-change-launch-args')
                    self.assertEqual(len(fw.args),3)

    def test_explicit_mode_mismatches_and_invalid_qmp_rows_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='tcg-qmp-error-',dir=ROOT/'build') as directory:
            for mode,rows in (('single',[{'cpu-index':0,'thread-id':101},{'cpu-index':1,'thread-id':102}]),
                              ('multi',[{'cpu-index':0,'thread-id':101},{'cpu-index':1,'thread-id':101}]),
                              ('single',[{'cpu-index':0,'thread-id':101}])):
                fw=FirmwareSession(Path(directory)/'logs',tcg_thread=mode)
                with patch.object(fw,'request',return_value=rows),self.assertRaisesRegex(RuntimeError,'do not match'):
                    fw.capture_vcpu_threads()
                self.assertTrue(fw.vcpu_thread_observation['observed'])
                self.assertFalse(fw.vcpu_thread_observation['requested_mode_verified'])
            for rows in ([],{},[{}],[{'cpu-index':0,'thread-id':True}],
                         [{'cpu-index':0,'thread-id':101},{'cpu-index':0,'thread-id':102}]):
                fw=FirmwareSession(Path(directory)/'logs',tcg_thread='single')
                with patch.object(fw,'request',return_value=rows),self.assertRaises(ValueError):
                    fw.capture_vcpu_threads()
                self.assertFalse(fw.vcpu_thread_observation['observed'])

    def test_default_query_error_is_reported_without_claiming_a_thread_count(self):
        with tempfile.TemporaryDirectory(prefix='tcg-qmp-default-',dir=ROOT/'build') as directory:
            fw=FirmwareSession(Path(directory)/'logs')
            with patch.object(fw,'request',side_effect=RuntimeError('CommandNotFound')):
                result=fw.capture_vcpu_threads()
            self.assertFalse(result['observed']);self.assertNotIn('distinct_host_thread_ids',result)
            fw=FirmwareSession(Path(directory)/'logs',tcg_thread='single')
            with patch.object(fw,'request',side_effect=RuntimeError('CommandNotFound')),self.assertRaises(RuntimeError):
                fw.capture_vcpu_threads()


class SocketPathLimitTests(unittest.TestCase):
    def test_limit_matches_what_the_kernel_accepts(self):
        with tempfile.TemporaryDirectory(dir=resume_sandbox_parent()) as directory:
            for size,accepted in ((UNIX_SOCKET_PATH_MAX,True),(UNIX_SOCKET_PATH_MAX+1,False)):
                path=Path(directory)/('s'*(size-len(os.fsencode(directory))-1))
                self.assertEqual(len(os.fsencode(path)),size)
                with socket.socket(socket.AF_UNIX) as endpoint:
                    if accepted:endpoint.bind(str(path))
                    else:
                        with self.assertRaisesRegex(OSError,'path too long'):endpoint.bind(str(path))

    def test_resume_sandbox_leaves_the_checkout_only_when_its_path_is_too_long(self):
        with patch(f'{__name__}.ROOT',Path('/checkout')):
            self.assertEqual(resume_sandbox_parent(),Path('/checkout/build'))
        deep=Path('/'+'d'*60)
        with patch(f'{__name__}.ROOT',deep):
            parent=resume_sandbox_parent()
        self.assertNotEqual(parent,deep/'build')
        self.assertLessEqual(len(os.fsencode(parent/DEEPEST_RESUME_SOCKET)),UNIX_SOCKET_PATH_MAX)


if __name__=='__main__':unittest.main(verbosity=2)
