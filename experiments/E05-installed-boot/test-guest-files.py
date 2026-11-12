#!/usr/bin/env python3
"""Real loopback file exchange and lifecycle checks; no QEMU or guest needed."""
import importlib.util
import hashlib
import json
from pathlib import Path
import selectors
import socket
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('guest_files_tests', HERE / 'guest_files.py')
files = importlib.util.module_from_spec(spec)
spec.loader.exec_module(files)


class GuestFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.uploads = self.root / 'guest-uploads'
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def service(self, **kwargs):
        service = files.GuestFileService(HERE / 'guest-diag', self.uploads, port=0, **kwargs)
        self.addCleanup(service.close)
        return service

    def assert_port_closed(self, port):
        with socket.socket() as sock:
            sock.settimeout(1)
            self.assertNotEqual(sock.connect_ex(('127.0.0.1', port)), 0)

    def test_real_fixture_get_and_put_roundtrip_cleanup(self):
        service = self.service().start()
        port = service.server.server_port
        base = service.report['host_url']
        with self.client.open(base + '/get/render.html', timeout=2) as response:
            self.assertEqual(response.read(), (HERE / 'guest-diag/render.html').read_bytes())
        self.assertTrue(service.report['ready'])
        self.assertTrue(service.report['upload_roundtrip_verified'])
        self.assertEqual(service.report['verified_get_files']['render.html']['bytes'], 185)
        self.assertEqual(list(self.uploads.iterdir()), [])
        payload = b'real upload\n'
        request = urllib.request.Request(base + '/up/result.txt', data=payload, method='PUT')
        with self.client.open(request, timeout=2) as response:
            self.assertEqual(response.status, 201)
        self.assertEqual((self.uploads / 'result.txt').read_bytes(), payload)
        with self.assertRaises(urllib.error.HTTPError) as conflict:
            self.client.open(request, timeout=2)
        self.assertEqual(conflict.exception.code, 409)
        conflict.exception.close()
        service.close()
        self.assertFalse(service.thread.is_alive())
        self.assertTrue(service.report['stopped'])
        self.assert_port_closed(port)

    def test_webgl_get_exact_page_and_bounded_binary_puts(self):
        page=HERE/'guest-diag/webgl-probe.html'
        self.assertTrue(page.is_file(),'Root-owned WebGL page must exist for this integration check')
        service=self.service(limit_bytes=96*64*4).start()
        base=service.report['host_url'];tag='r128-webgl'
        with self.client.open(base+'/get/webgl-probe.html#'+tag,timeout=2) as response:
            self.assertEqual(response.read(),page.read_bytes())
        self.assertTrue(service.report['webgl_probe']['get_verified'])
        self.assertEqual(service.report['verified_get_files']['webgl-probe.html']['sha256'],
                         hashlib.sha256(page.read_bytes()).hexdigest())
        size=96*64*4
        outputs={tag+'-offscreen.bin':bytes(range(256))*(size//256),
                 tag+'-default.bin':bytes(reversed(range(256)))*(size//256),
                 tag+'.json':json.dumps({'fixture':'HTTP transport only','bytes_per_readback':size}).encode()}
        for name,payload in outputs.items():
            request=urllib.request.Request(base+'/up/'+name,data=payload,method='PUT')
            with self.client.open(request,timeout=2) as response:self.assertEqual(response.status,201)
            self.assertEqual((self.uploads/name).read_bytes(),payload)
        request=urllib.request.Request(base+'/up/too-large.bin',data=b'x'*(size+1),method='PUT')
        with self.assertRaises(urllib.error.HTTPError) as oversized:self.client.open(request,timeout=2)
        self.assertEqual(oversized.exception.code,400);oversized.exception.close()
        self.assertFalse((self.uploads/'too-large.bin').exists())
        # Neither binary uploads nor a fragment on the page GET changes render.html.
        with self.client.open(base+'/get/render.html',timeout=2) as response:
            self.assertEqual(response.read(),(HERE/'guest-diag/render.html').read_bytes())

    def test_webgl_absence_does_not_break_existing_fixture_directories(self):
        serve=self.root/'legacy-serve';serve.mkdir()
        for name in ('render.html','gpuwatch.sh','start-gpuwatch.sh'):
            (serve/name).write_bytes((HERE/'guest-diag'/name).read_bytes())
        service=files.GuestFileService(serve,self.uploads,port=0)
        self.addCleanup(service.close);service.start()
        self.assertTrue(service.report['ready'])
        self.assertEqual(service.report['webgl_probe']['available'],False)
        self.assertNotIn('webgl-probe.html',service.report['verified_get_files'])

    def test_occupied_port_rejected_without_using_other_listener(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            service = files.GuestFileService(HERE / 'guest-diag', self.uploads,
                                             port=listener.getsockname()[1])
            with self.assertRaises(OSError):
                service.start()
            self.assertFalse(service.report['ready'])
            self.assertTrue(service.report['stopped'])
            self.assertFalse(self.uploads.exists())

    def test_shared_cache_probe_get_put_and_host_metadata_consumer(self):
        sys.path.insert(0,str(HERE))
        import timeout_diagnostic as diag
        if not (diag.ROOT / diag.PROFILE_RELATIVE).exists():
            self.skipTest('private timeout-profile-guard.json absent')
        service = self.service(shared_cache_identity=True).start()
        base = service.report['host_url']
        with self.client.open(base + '/get/shared-cache-identity',timeout=2) as response:
            self.assertEqual(hashlib.sha256(response.read()).hexdigest(),diag.PROBE_SHA256)
        with self.client.open(base + '/get/shared-cache-identity.sh',timeout=2) as response:
            wrapper=response.read()
        script=self.root/'wrapper.sh';script.write_bytes(wrapper)
        subprocess.run(['sh','-n',str(script)],check=True,capture_output=True)
        self.assertIn(b'codesign --verify --strict',wrapper)
        self.assertIn(b'shasum -a 256 -c',wrapper)
        self.assertFalse(service.report['shared_cache_identity']['guest_executed'])
        profile=diag.load_profile();slide=0x400000
        probe={'schema':1,'probe_version':1,'state':'verified','pid':123,
            'cache_uuid':profile['shared_cache_static']['main_cache_uuid'],
            'image_uuid':profile['image']['uuid'],
            'cache_base':hex(profile['shared_cache_static']['shared_region_unslid_start']+slide),
            'cache_size':4*1024**3,'image_base':hex(profile['image']['unslid_image_base']+slide),
            'image_cache_offset':hex(profile['image']['unslid_image_base']-profile['shared_cache_static']['shared_region_unslid_start']),
            'image_slide':slide,'lc_uuid_payload_image_offset':profile['image']['mach_o']['lc_uuid_payload_image_offset'],
            'target_loaded_before':False,'target_loaded_after':False,'target_dlopen_performed':False}
        request=urllib.request.Request(base+'/up/identity.json',data=json.dumps(probe).encode(),method='PUT')
        with self.client.open(request,timeout=2) as response:self.assertEqual(response.status,201)
        result_path=self.root/'results.json';result_path.write_text(json.dumps({'started_at_utc':'2020-01-01T00:00:00Z'}))
        identity,receipt=diag.load_probe(SimpleNamespace(directory=self.root,result_path=result_path),'identity.json',profile)
        self.assertEqual(identity['image_base'],int(probe['image_base'],16))
        self.assertEqual(receipt['sha256'],hashlib.sha256((self.uploads/'identity.json').read_bytes()).hexdigest())
        self.assertEqual(self.uploads.stat().st_mode&0o777,0o700)
        self.assertEqual((self.uploads/'identity.json').stat().st_mode&0o777,0o600)

    def test_probe_resources_through_real_service_process_cli(self):
        sys.path.insert(0,str(HERE))
        import timeout_diagnostic as diag
        if not (diag.ROOT / diag.PROFILE_RELATIVE).exists():
            self.skipTest('private timeout-profile-guard.json absent')
        service=files.GuestFileProcess(HERE/'guest-diag',self.uploads,port=0,shared_cache_identity=True)
        self.addCleanup(service.close)
        service.start()
        self.assertTrue(service.report['shared_cache_identity']['resources_verified'])
        self.assertIn('shared-cache-identity.sh',service.report['verified_get_files'])
        with self.client.open(service.report['host_url']+'/get/shared-cache-identity.sh',timeout=2) as response:
            self.assertEqual(hashlib.sha256(response.read()).hexdigest(),
                             service.report['verified_get_files']['shared-cache-identity.sh']['sha256'])
        port=int(service.report['host_url'].rsplit(':',1)[1])
        service.close()
        self.assertTrue(service.report['process_reaped'])
        self.assertEqual(service.report['exit_code'],0)
        self.assert_port_closed(port)

    def test_probe_hash_mismatch_prevents_service_listener(self):
        sys.path.insert(0,str(HERE))
        import timeout_diagnostic as diag
        service=self.service(shared_cache_identity=True)
        with patch.object(diag,'PROBE_SHA256','0'*64),self.assertRaises(diag.UIError):service.start()
        self.assertIsNone(service.server)
        self.assertFalse(service.report['ready'])

    def test_failed_get_preflight_releases_listener(self):
        empty = self.root / 'empty'
        empty.mkdir()
        service = files.GuestFileService(empty, self.uploads, port=0)
        with self.assertRaises(FileNotFoundError):
            service.start()
        self.assertFalse(service.report['ready'])
        self.assertFalse(service.thread.is_alive())
        self.assert_port_closed(service.server.server_port)

    def test_standalone_sigterm_exits_and_releases_listener(self):
        process = subprocess.Popen([sys.executable, str(HERE / 'guest_files.py'),
            '--serve', str(HERE / 'guest-diag'), '--uploads', str(self.uploads), '--port', '0'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=5), 'service did not report readiness')
                line = process.stdout.readline().strip()
            self.assertTrue(line.startswith('guest file service ready http://127.0.0.1:'), line)
            port = int(line.rsplit(':', 1)[1])
            process.terminate()
            process.communicate(timeout=3)
            self.assertEqual(process.returncode, 0)
            self.assert_port_closed(port)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
