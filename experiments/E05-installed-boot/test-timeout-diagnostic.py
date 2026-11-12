#!/usr/bin/env python3
"""Offline probe validation/publication state machine; no QEMU or guest execution."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import timeout_diagnostic as diag


def profile_fixture():
    return {'image': {'uuid': '70BEE322-37EE-3B22-8012-4F76BA105C29',
                     'unslid_image_base': 7210463232, 'text_segment_size': 25826444,
                     'mach_o': {'lc_uuid_payload_image_offset': 5952}},
            'shared_cache_static': {'main_cache_uuid': 'C284A291-B68B-3D0D-A1A0-3F866F87EFDC',
                                    'shared_region_unslid_start': 6442450944}}


def probe_fixture():
    profile = profile_fixture()
    slide = 0x400000
    return {'schema': 1, 'probe_version': 1, 'state': 'verified', 'pid': 100,
            'cache_uuid': profile['shared_cache_static']['main_cache_uuid'],
            'image_uuid': profile['image']['uuid'],
            'cache_base': hex(profile['shared_cache_static']['shared_region_unslid_start'] + slide),
            'cache_size': 4 * 1024**3, 'image_base': hex(profile['image']['unslid_image_base'] + slide),
            'image_cache_offset': hex(profile['image']['unslid_image_base'] - profile['shared_cache_static']['shared_region_unslid_start']),
            'image_slide': slide, 'lc_uuid_payload_image_offset': 5952,
            'target_loaded_before': False, 'target_loaded_after': False, 'target_dlopen_performed': False}


class FakeControl:
    def __init__(self, *, running=True):
        self.args = ['qemu', '-smp', '2']
        self.cpu_paths = set()
        self.transition_allowed = False
        self.allowed_armed_values = set()
        self.usable = True
        self.running = running
        self.calls = []
        self.armed = [False, False]
        self.values = [{'armed': False, 'status': 'unarmed', 'site0-hits': 0,
                        'site1-hits': 0, 'rejections': 0, 'epoch': 0} for _ in range(2)]
        self.reject_cpu = None
        self.interrupt_cpu = None
        self.rollback_reject = False

    def _request(self, command, arguments=None):
        self.calls.append((command, copy.deepcopy(arguments)))
        if command == 'query-cpus-fast':
            return [{'cpu-index': i, 'qom-path': f'/machine/unattached/device[{i}]'} for i in range(2)]
        if command == 'query-status':
            return {'running': self.running, 'status': 'running' if self.running else 'paused'}
        if command == 'stop':
            assert self.transition_allowed
            self.running = False
            return {}
        if command == 'cont':
            assert self.transition_allowed and len(set(self.armed)) == 1
            self.running = True
            return {}
        i = int(arguments['path'].split('[')[1].split(']')[0])
        name = arguments['property'].removeprefix(diag.PREFIX)
        if command == 'qom-get':
            return self.values[i][name]
        assert command == 'qom-set' and name == 'armed' and not self.running
        assert type(arguments['value']) is bool and arguments['value'] in self.allowed_armed_values
        value = arguments['value']
        if value and i == self.reject_cpu:
            raise diag.UIError('Synthetic complete rejection')
        if not value and self.rollback_reject:
            raise diag.UIError('Synthetic rollback rejection')
        self.armed[i] = value
        self.values[i].update(armed=value, status='armed-unverified' if value else 'unarmed',
                              epoch=self.values[i]['epoch'] + 1)
        if value and i == self.interrupt_cpu:
            self.usable = False
            raise diag.UIError('Synthetic transport uncertainty after setter')
        return {}


class ProbeTests(unittest.TestCase):
    def test_firmware_cpu_argument_and_startup_metadata_without_launch(self):
        sys.path.insert(0,str(diag.ROOT/'experiments/E05-usbip-kernel'))
        from firmware_session import FirmwareSession
        with tempfile.TemporaryDirectory() as directory,patch('subprocess.Popen') as popen:
            baseline=FirmwareSession(Path(directory)/'default')
            selected=FirmwareSession(Path(directory)/'diagnostic',webkit_timeout_diagnostic=True)
            self.assertEqual(selected.cpu_model,baseline.cpu_model+',x-webkit-timeout-diagnostic='+str(diag.ROOT/diag.PROFILE_RELATIVE))
            self.assertFalse(baseline.execution_metadata()['webkit_timeout_diagnostic']['requested'])
            metadata=selected.execution_metadata()['webkit_timeout_diagnostic']
            self.assertEqual(metadata['state'],'configured_unarmed')
            self.assertEqual(metadata['publication_protocol'],diag.PROTOCOL)
            self.assertEqual(metadata['schema'],2)
            self.assertFalse(metadata['original_deadline_performance_pass'])
            popen.assert_not_called()

    def test_matching_build_patch_and_profile_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patchpath = root / diag.PATCH
            patchpath.parent.mkdir(parents=True)
            patchpath.write_text('synthetic patch identity')
            runtime_patch = root / diag.RUNTIME_SLIDE_PATCH
            runtime_patch.write_text('synthetic runtime-slide patch identity')
            manifest = root / 'runs/qemu-bdif-usb-build.json'
            manifest.parent.mkdir()
            record = {'binary_sha256': 'b' * 64, 'patches': [], 'patch_sha256': {}}
            with patch.object(diag, 'load_profile', return_value=profile_fixture()):
                manifest.write_text(json.dumps(record))
                with self.assertRaises(diag.UIError):diag.require_build({'binary_sha256': 'b' * 64}, root)
                record['patches'] = [diag.PATCH]
                record['patch_sha256'][diag.PATCH] = diag.sha(patchpath.read_bytes())
                manifest.write_text(json.dumps(record))
                with self.assertRaises(diag.UIError):diag.require_build({'binary_sha256':'b'*64},root)
                record['patches'].append(diag.RUNTIME_SLIDE_PATCH)
                record['patch_sha256'][diag.RUNTIME_SLIDE_PATCH]=diag.sha(runtime_patch.read_bytes())
                manifest.write_text(json.dumps(record))
                result = diag.require_build({'binary_sha256': 'b' * 64}, root)
                self.assertTrue(result['diagnostic_only'])
                self.assertFalse(result['original_deadline_performance_pass'])
                self.assertEqual(result['publication_protocol'],diag.PROTOCOL)
                self.assertEqual(result['runtime_slide']['patch'],diag.RUNTIME_SLIDE_PATCH)
                with self.assertRaises(diag.UIError):diag.require_build({'binary_sha256': 'c' * 64}, root)
                saved=runtime_patch.read_bytes();runtime_patch.write_text('changed runtime-slide patch')
                with self.assertRaises(diag.UIError):diag.require_build({'binary_sha256':'b'*64},root)
                runtime_patch.write_bytes(saved)
                patchpath.write_text('changed after build')
                with self.assertRaises(diag.UIError):diag.require_build({'binary_sha256': 'b' * 64}, root)

    def test_exact_metadata_equations_and_scope(self):
        verified = diag.validate_probe(probe_fixture(), profile_fixture())
        self.assertEqual(verified['image_base'], int(probe_fixture()['image_base'], 16))
        self.assertIn('probe process only', verified['scope'])

    def test_every_identity_range_and_no_load_guard(self):
        for value in (None, [], 'verified'):
            with self.assertRaises(diag.UIError):diag.validate_probe(value,profile_fixture())
        mutations = {'schema': True, 'probe_version': True, 'state': 'unavailable',
                     'cache_uuid': '00000000-0000-0000-0000-000000000000',
                     'image_uuid': '00000000-0000-0000-0000-000000000000',
                     'cache_base': '0x1', 'cache_size': True, 'image_base': '0xffffffffffffffff',
                     'image_cache_offset': '0x1', 'image_slide': False,
                     'lc_uuid_payload_image_offset': 5956, 'target_loaded_before': True,
                     'target_loaded_after': None, 'target_dlopen_performed': True}
        for key, value in mutations.items():
            with self.subTest(key=key):
                probe = probe_fixture()
                probe[key] = value
                with self.assertRaises(diag.UIError):
                    diag.validate_probe(probe, profile_fixture())

    def test_current_run_owned_upload_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'guest-uploads').mkdir()
            result = root / 'results.json'
            result.write_text(json.dumps({'started_at_utc': '2020-01-01T00:00:00Z'}))
            path = root / 'guest-uploads/probe.json'
            path.write_text(json.dumps(probe_fixture()))
            client = Mock(directory=root, result_path=result)
            identity, receipt = diag.load_probe(client, 'probe.json', profile_fixture())
            self.assertEqual(receipt['sha256'], diag.sha(path.read_bytes()))
            for name in ('../probe.json', str(path), 'bad.json/next'):
                with self.assertRaises(diag.UIError):
                    diag.load_probe(client, name, profile_fixture())
            result.write_text(json.dumps({'started_at_utc': '2999-01-01T00:00:00Z'}))
            with self.assertRaises(diag.UIError):
                diag.load_probe(client, 'probe.json', profile_fixture())


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.identity = diag.validate_probe(probe_fixture(), profile_fixture())
        self.probe = {'name': 'probe.json', 'sha256': 'a' * 64, 'raw': json.dumps(probe_fixture())}
        self.saved = []

    def save(self, state):
        self.saved.append(copy.deepcopy(state))

    def test_all_cpus_verified_before_cont_and_idempotent_repeat(self):
        client = FakeControl()
        state = diag.operate(client, 'arm', self.identity, self.probe, None, self.save)
        self.assertEqual(state['stage'], 'published_unverified')
        self.assertTrue(state['resume_confirmed'])
        self.assertEqual(client.armed, [True, True])
        self.assertTrue(all(type(a['value']) is bool for command,a in client.calls if command=='qom-set'))
        self.assertEqual([x['epoch'] for x in state['published']], [1, 1])
        count = len([x for x in client.calls if x[0] == 'qom-set'])
        other_probe=probe_fixture()
        other_probe['image_slide']+=0x400000
        for key in ('image_base','cache_base'):other_probe[key]=hex(int(other_probe[key],16)+0x400000)
        other_identity=diag.validate_probe(other_probe,profile_fixture())
        repeated = diag.operate(client, 'arm', other_identity,
                                {'name':'other.json','sha256':'b'*64,'raw':json.dumps(other_probe)}, state, self.save)
        self.assertEqual(repeated['stage'], 'already_published')
        self.assertEqual(len([x for x in client.calls if x[0] == 'qom-set']), count)
        self.assertEqual(repeated['baseline'], state['baseline'])
        self.assertEqual(repeated['probe_identity'],self.identity)
        self.assertEqual(repeated['latest_probe_identity'],other_identity)
        self.assertNotEqual(other_identity['image_base'],self.identity['image_base'])
        self.assertNotIn('image_base',repeated)

    def test_initial_paused_vm_stays_paused(self):
        client = FakeControl(running=False)
        diag.operate(client, 'arm', self.identity, self.probe, None, self.save)
        self.assertFalse(client.running)
        self.assertNotIn('cont', [x[0] for x in client.calls])

    def test_partial_rejection_revokes_all_and_stays_paused(self):
        client = FakeControl()
        client.reject_cpu = 1
        with self.assertRaises(diag.UIError):
            diag.operate(client, 'arm', self.identity, self.probe, None, self.save)
        self.assertEqual(client.armed, [False, False])
        self.assertFalse(client.running)
        self.assertTrue(self.saved[-1]['rollback_verified'])
        self.assertEqual(self.saved[-1]['stage'], 'rolled_back_all_left_paused')
        self.assertNotIn('cont', [x[0] for x in client.calls])

    def test_transport_unknown_and_rollback_failure_never_resume(self):
        for transport in (True, False):
            client = FakeControl()
            if transport:
                client.interrupt_cpu = 0
            else:
                client.reject_cpu = 1
                client.rollback_reject = True
            with self.assertRaises(diag.UIError):
                diag.operate(client, 'arm', self.identity, self.probe, None, self.save)
            self.assertFalse(client.running)
            self.assertNotIn('cont', [x[0] for x in client.calls])

    def test_unreceipted_armed_state_not_republished(self):
        client = FakeControl()
        client.armed[0] = True
        client.values[0]['armed'] = True
        with self.assertRaises(diag.UIError):
            diag.operate(client, 'arm', self.identity, self.probe, None, self.save)
        self.assertFalse(any(x[0] in ('stop', 'cont', 'qom-set') for x in client.calls))

    def test_legacy_receipt_rejected_without_queries_or_writes(self):
        client=FakeControl()
        legacy={'schema':1,'image_base':self.identity['image_base'],'profile_sha256':diag.PROFILE_SHA256}
        snapshot=copy.deepcopy(legacy)
        with self.assertRaises(diag.UIError):
            diag.operate(client,'arm',self.identity,self.probe,legacy,self.save)
        self.assertEqual(legacy,snapshot)
        self.assertEqual(client.calls,[])
        self.assertEqual(self.saved,[])

    def test_final_counters_scope_epoch_reset_and_disarm(self):
        client = FakeControl()
        state = diag.operate(client, 'arm', self.identity, self.probe, None, self.save)
        client.values[0]['site0-hits'] = 3
        client.values[1]['site1-hits'] = 2
        client.values[1]['rejections'] = 4
        client.values[1]['status'] = 'candidate-identity-mismatch'
        final = diag.operate(client, 'final', None, None, state, self.save)
        public = diag.public_report(final, {}, 'final')
        self.assertEqual([r['site0-hits'] for r in public['counter_deltas']], [3, 0])
        self.assertEqual([r['site1-hits'] for r in public['counter_deltas']], [0, 2])
        self.assertEqual([r['rejections'] for r in public['counter_deltas']],[0,4])
        self.assertEqual(public['schema'],2)
        self.assertIn('not Safari failures',public['rejections_scope'])
        self.assertFalse(public['original_deadline_performance_pass'])
        serialized = json.dumps(public)
        self.assertNotIn(probe_fixture()['image_base'], serialized)
        self.assertNotIn('image_slide', serialized)
        self.assertNotIn('"failures"',serialized)
        client.values[0]['epoch'] += 1
        reset = diag.operate(client, 'status', None, None, final, self.save)
        self.assertIsNone(diag.public_report(reset, {}, 'status')['counter_deltas'][0]['site0-hits'])
        cleared = diag.operate(client, 'disarm', None, None, reset, self.save)
        self.assertEqual(client.armed, [False, False])
        self.assertTrue(client.running)
        self.assertEqual(cleared['baseline'], state['baseline'])

    def test_dedicated_capability_rejects_arbitrary_operations(self):
        client = object.__new__(diag.TimeoutControl)
        client.cpu_paths = {'/machine/unattached/device[0]'}
        client.transition_allowed = False
        client.allowed_armed_values = set()
        for command, args in [('stop', None), ('cont', None),
                              ('human-monitor-command', {'command-line': 'info registers'}),
                              ('qom-get', {'path': '/machine/unattached/device[0]', 'property': diag.PREFIX + 'base'}),
                              ('qom-set', {'path': '/machine/unattached/device[0]', 'property': diag.PREFIX + 'base', 'value': 0})]:
            with self.assertRaises(diag.UIError):
                client._request(command, args)

    def test_wire_setter_is_bound_boolean_and_never_publishes_address(self):
        client=object.__new__(diag.TimeoutControl)
        client.cpu_paths={'/machine/unattached/device[0]'}
        client.transition_allowed=True
        client.allowed_armed_values={False,True}
        client.usable=True
        client.verify_process=Mock();client.verify_peer=Mock()
        client.sock=Mock();client.journal=Mock()
        client._read=lambda:{'id':json.loads(client.sock.sendall.call_args.args[0])['id'],'return':{}}
        client._request('qom-set',{'path':'/machine/unattached/device[0]',
                                  'property':diag.PREFIX+'armed','value':True})
        client.verify_process.assert_called_once();client.verify_peer.assert_called_once()
        self.assertIs(json.loads(client.sock.sendall.call_args.args[0])['arguments']['value'],True)
        journal=json.dumps([x.kwargs for x in client.journal.write.call_args_list])
        self.assertNotIn(str(self.identity['image_base']),journal)
        self.assertIn('"value": true',journal)
        for value in (0,1,self.identity['image_base']):
            with self.assertRaises(diag.UIError):
                client._request('qom-set',{'path':'/machine/unattached/device[0]',
                                          'property':diag.PREFIX+'armed','value':value})
        for command,arguments in [
            ('qom-set',{'path':'/machine/unattached/device[0]','property':diag.PREFIX+'base','value':self.identity['image_base']}),
            ('qom-get',{'path':'/machine/unattached/device[0]','property':diag.PREFIX+'failures'})]:
            with self.assertRaises(diag.UIError):client._request(command,arguments)

    def test_cli_private_public_persistence_and_repeat_arm(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);run='offline-bound-run';public=root/'runs/e05-installed-boot'/run
            private=root/'private/installed-boot'/run
            public.mkdir(parents=True);private.mkdir(parents=True,mode=0o700)
            (public/'guest-uploads').mkdir()
            result=public/'results.json';result.write_text(json.dumps({'started_at_utc':'2020-01-01T00:00:00Z'}))
            (public/'guest-uploads/probe.json').write_text(json.dumps(probe_fixture()))
            client=FakeControl();client.directory=public;client.result_path=result
            client.pid=1000;client.start_ticks=2000
            with patch.object(diag,'ROOT',root),patch.object(diag,'load_profile',return_value=profile_fixture()), \
                 patch.object(diag,'TimeoutControl') as factory:
                factory.return_value.__enter__.return_value=client
                self.assertEqual(diag.main(['--run',run,'--action','arm','--probe','probe.json']),0)
                sets=len([x for x in client.calls if x[0]=='qom-set'])
                self.assertEqual(diag.main(['--run',run,'--action','arm','--probe','probe.json']),0)
                self.assertEqual(len([x for x in client.calls if x[0]=='qom-set']),sets)
                client.values[0]['site0-hits']=1;client.values[1]['site1-hits']=1
                self.assertEqual(diag.main(['--run',run,'--action','final']),0)
            report=json.loads((public/'webkit-timeout-diagnostic.json').read_text())
            secret=json.loads((private/'timeout-diagnostic-state.json').read_text())
            self.assertEqual(secret['probe_identity']['image_base'],self.identity['image_base'])
            self.assertNotIn('image_base',secret)
            self.assertEqual(secret['publication_protocol'],diag.PROTOCOL)
            self.assertEqual(secret['schema'],2)
            self.assertEqual(len(report['final']),2)
            self.assertFalse(report['original_deadline_performance_pass'])
            self.assertNotIn('image_base',json.dumps(report))
            self.assertEqual((private/'timeout-diagnostic-state.json').stat().st_mode&0o777,0o600)


if __name__ == '__main__':
    unittest.main()
