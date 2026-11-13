#!/usr/bin/env python3
"""Loopback-only file exchange for a `--user-network` guest.

QEMU user networking maps the guest's 10.0.2.2 to host 127.0.0.1, so a guest can
fetch diagnostic scripts and return text evidence without screen OCR.
`GET /get/NAME` serves NAME from --serve; `PUT /up/NAME` stores the body under
--uploads, which is created on first upload inside an existing run directory.
Names are single path components, uploads never overwrite, bodies need a
Content-Length and are capped. Nothing else is served.
"""
import argparse
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import threading
import urllib.request
import uuid

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}')


def shared_cache_probe_resources(serve):
    """Pinned self-authored binary and generated wrapper; no private code guards served."""
    from timeout_diagnostic import ROOT, PROBE_SHA256, load_profile, read_owned, require
    build_path = ROOT / 'build/macos-shared-cache-identity/build.json'
    build = json.loads(read_owned(build_path, 65536)[0])
    binary_path = ROOT / 'build/macos-shared-cache-identity/shared-cache-identity'
    require((serve / 'shared-cache-identity').resolve() == binary_path.resolve(),
            'Shared-cache probe served link does not point to the fixed build')
    binary = read_owned(binary_path, 1024 * 1024)[0]
    checksum = (serve / 'shared-cache-identity.sha256').read_bytes()
    words = checksum.decode('ascii').split()
    require(hashlib.sha256(binary).hexdigest() == PROBE_SHA256 == build.get('binary_sha256')
            and words == [PROBE_SHA256, 'shared-cache-identity']
            and build.get('loads_target_library') is False,
            'Shared-cache probe binary/checksum/manifest differs')
    require(hashlib.sha256((ROOT / build['source']).read_bytes()).hexdigest() == build['source_sha256'],
            'Shared-cache probe source no longer matches its build')
    profile = load_profile()
    cache_uuid = profile['shared_cache_static']['main_cache_uuid']
    image_uuid = profile['image']['uuid']
    offset = profile['image']['unslid_image_base'] - profile['shared_cache_static']['shared_region_unslid_start']
    wrapper = f'''#!/bin/sh
# Fixed metadata-only probe. No dlopen, target startup, re-signing, or guest code write.
tag=$1
case "$tag" in ''|*[!A-Za-z0-9_-]*) exit 2;; esac
[ "${{#tag}}" -le 64 ] || exit 2
[ -n "$H" ] || H=http://10.0.2.2:18769
d=/tmp/$tag
mkdir "$d" || exit 2
pid=''
cleanup() {{ [ -z "$pid" ] || kill -TERM "$pid" 2>/dev/null; }}
trap cleanup 0
trap 'exit 143' TERM INT
date -u +%Y-%m-%dT%H:%M:%SZ > "$d/receipt.txt"
curl -fsS --connect-timeout 5 -m 30 "$H/get/shared-cache-identity" -o "$d/shared-cache-identity" || exit 12
curl -fsS --connect-timeout 5 -m 30 "$H/get/shared-cache-identity.sha256" -o "$d/shared-cache-identity.sha256" || exit 12
(cd "$d" && shasum -a 256 -c shared-cache-identity.sha256) >> "$d/receipt.txt" 2>&1 || exit 13
chmod 700 "$d/shared-cache-identity" || exit 13
/usr/bin/codesign --verify --strict --verbose=2 "$d/shared-cache-identity" >> "$d/receipt.txt" 2>&1 || exit 14
"$d/shared-cache-identity" {cache_uuid} {image_uuid} {offset:#x} > "$d/probe.json" 2> "$d/stderr.txt" &
pid=$!
deadline=$(( $(date +%s) + 60 ))
timed_out=false
while kill -0 "$pid" 2>/dev/null; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        timed_out=true
        kill -TERM "$pid" 2>/dev/null
        sleep 1
        kill -KILL "$pid" 2>/dev/null
        break
    fi
    sleep 1
done
wait "$pid"; rc=$?
pid=''
echo "probe_exit=$rc timeout=$timed_out" >> "$d/receipt.txt"
curl -fsS --connect-timeout 5 -m 30 -T "$d/probe.json" "$H/up/$tag.json" || exit 15
curl -fsS --connect-timeout 5 -m 30 -T "$d/receipt.txt" "$H/up/$tag-receipt.txt" || exit 15
curl -fsS --connect-timeout 5 -m 30 -T "$d/stderr.txt" "$H/up/$tag-stderr.txt" || exit 15
[ "$timed_out" = false ] || exit 124
exit "$rc"
'''.encode()
    return {'shared-cache-identity': binary, 'shared-cache-identity.sha256': checksum,
            'shared-cache-identity.sh': wrapper}


def make_handler(serve, uploads, limit, resources=None):
    class Handler(http.server.BaseHTTPRequestHandler):
        def name(self, prefix):
            rest = self.path[len(prefix):] if self.path.startswith(prefix) else ''
            return rest if NAME.fullmatch(rest) else None

        def reply(self, code, body=b''):
            self.send_response(code)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            name = self.name('/get/')
            if name in (resources or {}):
                return self.reply(200, resources[name])
            path = serve / name if name else None
            if path is None or not path.is_file():
                return self.reply(404)
            self.reply(200, path.read_bytes())

        def do_PUT(self):
            name = self.name('/up/')
            try:
                length = int(self.headers.get('Content-Length') or -1)
            except ValueError:
                return self.reply(400)
            if name is None or not 0 <= length <= limit:
                return self.reply(400)
            if not uploads.parent.is_dir() or (uploads / name).exists():
                return self.reply(409)
            uploads.mkdir(exist_ok=True, mode=0o700 if resources else 0o777)
            if resources:
                # Diagnostic uploads can contain runtime cache addresses; keep them private.
                fd=os.open(uploads/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as output:output.write(self.rfile.read(length))
            else:
                (uploads / name).write_bytes(self.rfile.read(length))
            self.log_message('stored %s (%d bytes)', name, length)
            self.reply(201)

    return Handler


class GuestFileService:
    """Own a bound loopback server and verify its actual file-exchange path."""
    def __init__(self, serve, uploads, *, port=18769, limit_bytes=32 << 20,
                 shared_cache_identity=False):
        self.serve = Path(serve).resolve()
        self.uploads = Path(uploads).resolve()
        self.port = port
        self.limit_bytes = limit_bytes
        if type(shared_cache_identity) is not bool:
            raise ValueError('Shared-cache identity resource selection must be boolean')
        self.shared_cache_identity = shared_cache_identity
        self.server = None
        self.thread = None
        self.report = {'requested': True, 'ready': False, 'stopped': False,
                       'serve': str(self.serve), 'uploads': str(self.uploads),
                       'guest_connectivity': 'unknown',
                       'scope': 'Host loopback GET/PUT readiness only; guest route and watcher remain separate.'}

    def start(self):
        if self.server is not None:
            raise RuntimeError('Guest file service already started')
        if not self.serve.is_dir() or not self.uploads.parent.is_dir():
            raise ValueError('Guest file serve and run directories must already exist')
        try:
            resources = shared_cache_probe_resources(self.serve) if self.shared_cache_identity else {}
            self.server = http.server.ThreadingHTTPServer(('127.0.0.1', self.port),
                make_handler(self.serve, self.uploads, self.limit_bytes, resources))
            self.thread = threading.Thread(target=self.server.serve_forever,
                                           kwargs={'poll_interval': 0.1}, daemon=True,
                                           name='guest-file-service')
            self.thread.start()
            base = f'http://127.0.0.1:{self.server.server_port}'
            self.report['host_url'] = base
            self.report['guest_url'] = f'http://10.0.2.2:{self.server.server_port}'
            # Ignore proxy environment settings: this is the server just bound.
            client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            fixtures = {}
            # Existing custom three-file fixture directories remain valid.
            # When present, verify the fixed authored WebGL page through GET too.
            webgl_present=(self.serve/'webgl-probe.html').is_file()
            optional=tuple(name for name in ('webgl-probe.html', 'webgl-complex-probe.html', 'app-suite-probe.sh')
                           if (self.serve/name).is_file())
            for name in ('render.html', 'gpuwatch.sh', 'start-gpuwatch.sh', *optional, *resources):
                expected = resources[name] if name in resources else (self.serve / name).read_bytes()
                with client.open(base + '/get/' + name, timeout=5) as response:
                    received = response.read()
                    if response.status != 200 or received != expected:
                        raise RuntimeError(f'Guest file GET verification failed: {name}')
                fixtures[name] = {'bytes': len(received), 'sha256': hashlib.sha256(received).hexdigest()}
            self.report['verified_get_files'] = fixtures
            self.report['webgl_probe']={'available':webgl_present,'get_verified':webgl_present,
                'scope':'Fixed page GET bytes only; rendering, readback and uploads require the actual browser trial.'}
            if self.shared_cache_identity:
                self.report['shared_cache_identity'] = {
                    'resources_verified': True, 'guest_executed': False,
                    'scope': 'Pinned binary/checksum/wrapper GET only; guest ABI and runtime site hits remain unverified.'}
            probe = 'host-preflight-' + uuid.uuid4().hex + '.txt'
            target = self.uploads / probe
            payload = ('guest-file-service ' + probe + '\n').encode()
            try:
                request = urllib.request.Request(base + '/up/' + probe, data=payload, method='PUT')
                with client.open(request, timeout=5) as response:
                    if response.status != 201 or target.read_bytes() != payload:
                        raise RuntimeError('Guest file PUT verification failed')
                self.report['upload_roundtrip_verified'] = True
            finally:
                target.unlink(missing_ok=True)
            self.report['ready'] = True
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.server is not None:
            if self.thread is not None and self.thread.is_alive():
                self.server.shutdown()
                self.thread.join(timeout=5)
            self.server.server_close()
        self.report['stopped'] = True

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()


class GuestFileProcess:
    """Keep HTTP threads outside the QEMU controller's fork/preexec process."""
    def __init__(self, serve, uploads, *, port=18769, shared_cache_identity=False):
        self.serve, self.uploads = Path(serve), Path(uploads)
        self.port = port
        if type(shared_cache_identity) is not bool:
            raise ValueError('Shared-cache identity resource selection must be boolean')
        self.shared_cache_identity = shared_cache_identity
        self.process = None
        self.log = None
        self.report = {'requested': True, 'ready': False, 'stopped': False,
                       'log': str(self.uploads.parent / 'guest-files.log')}

    def start(self):
        try:
            self.log = Path(self.report['log']).open('ab')
            self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                '--serve', str(self.serve), '--uploads', str(self.uploads),
                '--port', str(self.port), '--json-ready', '--parent-pid', str(os.getpid())]
                + (['--shared-cache-identity'] if self.shared_cache_identity else []),
                stdout=subprocess.PIPE, stderr=self.log, text=True)
            self.report['pid'] = self.process.pid
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                if not selector.select(timeout=30):
                    raise TimeoutError('Guest file service GET/PUT readiness timed out')
                line = self.process.stdout.readline()
            if not line:
                raise RuntimeError('Guest file service exited before readiness; see ' + self.report['log'])
            ready = json.loads(line)
            if ready.get('ready') is not True or ready.get('upload_roundtrip_verified') is not True:
                raise RuntimeError('Guest file service did not verify GET/PUT readiness')
            self.report.update(ready)
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            if self.process.stdout is not None:
                self.process.stdout.close()
            self.report.update(exit_code=self.process.returncode, process_reaped=True)
        if self.log is not None:
            self.log.close()
        self.report['stopped'] = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--serve', type=Path, required=True, help='directory of files the guest may fetch')
    parser.add_argument('--uploads', type=Path, required=True, help='new directory inside an existing run directory')
    parser.add_argument('--port', type=int, default=18769)
    parser.add_argument('--limit-bytes', type=int, default=32 << 20)
    parser.add_argument('--shared-cache-identity', action='store_true',
                        help='Verify and serve the fixed metadata-only shared-cache probe and wrapper')
    parser.add_argument('--json-ready', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--parent-pid', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    with GuestFileService(args.serve, args.uploads, port=args.port,
                          limit_bytes=args.limit_bytes, shared_cache_identity=args.shared_cache_identity) as service:
        print(json.dumps(service.report) if args.json_ready else
              'guest file service ready ' + service.report['host_url'], flush=True)
        while not stopped.wait(1):
            if args.parent_pid is not None and os.getppid() != args.parent_pid:
                break
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
