#!/usr/bin/env python3
"""Run the authored fixture in an isolated host browser, then corrupt readbacks.

This is a fixture/transport/oracle control, never macOS or hardware-GPU proof.
"""
import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time

from guest_files import GuestFileService

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'experiments/E05-installed-boot/guest-diag/webgl-probe.html'
spec = importlib.util.spec_from_file_location('webgl_oracle', ROOT / 'tools/validate-webgl-probe.py')
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.attrs = None
        self.body = []
        self.items = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.attrs, self.body = dict(attrs), []

    def handle_data(self, data):
        if self.attrs is not None:
            self.body.append(data)

    def handle_endtag(self, tag):
        if tag == 'script':
            self.items.append((self.attrs, ''.join(self.body)))
            self.attrs = None


def syntax_checks(scratch):
    parser = Scripts()
    parser.feed(SOURCE.read_text())
    results = []
    for attrs, body in parser.items:
        name = attrs.get('id', 'script')
        suffix = {'vertex-source': '.vert', 'fragment-source': '.frag'}.get(name, '.js')
        path = scratch / (name + suffix)
        path.write_text(body)
        command = ['node', '--check', str(path)] if suffix == '.js' else [
            'glslangValidator', '-S', suffix[1:], str(path)]
        check = subprocess.run(command, capture_output=True, text=True, timeout=15)
        results.append({'name': name, 'source_sha256': hashlib.sha256(body.encode()).hexdigest(),
                        'command': command, 'exit_code': check.returncode,
                        'stdout': check.stdout, 'stderr': check.stderr})
        if check.returncode:
            raise RuntimeError('Source check failed: ' + name)
    return results


def process_table():
    rows = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            raw = path.read_text()
            fields = raw[raw.rfind(')') + 2:].split()
            rows[int(path.parent.name)] = {'parent': int(fields[1]), 'start_ticks': int(fields[19])}
        except (OSError, ValueError, IndexError):
            pass
    return rows


def descendants():
    rows = process_table()
    known = {os.getpid()}
    while True:
        added = {pid for pid, row in rows.items() if row['parent'] in known} - known
        if not added:
            break
        known.update(added)
    return {pid: rows[pid] for pid in known if pid != os.getpid()}


def stop_browser(proc):
    # This test process is a Linux subreaper and spawns only its own browser.
    # Include orphaned crash handlers as well as the normal browser tree.
    reaped = []
    signalled = []
    for signum in (signal.SIGTERM, signal.SIGKILL):
        snapshot = descendants()
        for pid, row in snapshot.items():
            current = process_table().get(pid)
            if current and current['start_ticks'] == row['start_ticks']:
                try:
                    os.kill(pid, signum)
                    signalled.append({'pid': pid, 'signal': int(signum)})
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            proc.poll()
            while True:
                try:
                    pid, status = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    break
                if not pid:
                    break
                if pid == proc.pid:
                    # Preserve the actual status if our subreaper wins the
                    # race with Popen.poll(), which otherwise sees ECHILD.
                    proc.returncode = os.waitstatus_to_exitcode(status)
                else:
                    reaped.append(pid)
            if not descendants():
                break
            time.sleep(.05)
        if not descendants():
            break
    proc.wait(timeout=1)
    remaining = list(descendants())
    if remaining:
        raise RuntimeError('Owned browser children remain: ' + str(remaining))
    return {'browser_exit_code': proc.returncode, 'reaped_children': reaped,
            'signals': signalled, 'remaining_children': remaining}


def negative_controls(uploads, tag):
    """Mutate actual browser-produced data, not an implementation-derived golden."""
    observed = {p.name: p.read_bytes() for p in uploads.glob(tag + '*') if p.is_file()}
    cases = []
    for case in ('byte_flip', 'vertical_flip', 'truncated', 'missing_default',
                 'lost_context', 'misstated_dimensions', 'wrong_output_name', 'symlink'):
        with tempfile.TemporaryDirectory(prefix='webgl-negative-') as temporary:
            directory = Path(temporary)
            for name, data in observed.items():
                (directory / name).write_bytes(data)
            image = directory / (tag + '-offscreen.bin')
            report_path = directory / (tag + '.json')
            report = json.loads(report_path.read_text())
            if case == 'byte_flip':
                data = bytearray(image.read_bytes()); data[16384] ^= 1; image.write_bytes(data)
            elif case == 'vertical_flip':
                data = image.read_bytes(); stride = oracle.WIDTH * 4
                image.write_bytes(b''.join(data[y * stride:(y + 1) * stride]
                                           for y in reversed(range(oracle.HEIGHT))))
            elif case == 'truncated':
                image.write_bytes(image.read_bytes()[:-1])
            elif case == 'missing_default':
                (directory / (tag + '-default.bin')).unlink()
            elif case == 'lost_context':
                report['context_lost'] = True
            elif case == 'misstated_dimensions':
                report['drawing_buffer_width'] = True
            elif case == 'wrong_output_name':
                report['outputs']['offscreen']['file'] = '../../unrelated.bin'
            elif case == 'symlink':
                image.unlink(); image.symlink_to(uploads / image.name)
            report_path.write_text(json.dumps(report))
            result = oracle.validate(directory, tag)
            passed = not result['passed']
            if case in ('byte_flip', 'vertical_flip', 'truncated'):
                passed = passed and result['outputs']['offscreen']['bit_exact'] is False
            if case in ('missing_default', 'symlink'):
                key = 'default' if case == 'missing_default' else 'offscreen'
                passed = passed and result['outputs'][key]['bit_exact'] is None
            cases.append({'case': case, 'passed': passed,
                          'validation_state': result['validation_state']})
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,80}', args.label):
        parser.error('Invalid output label')
    browser = shutil.which('google-chrome')
    if not browser:
        parser.error('The existing google-chrome executable is required; no installation is performed')
    out = ROOT / 'runs/e06-webgl-probe' / args.label
    out.mkdir(parents=True, exist_ok=False)
    uploads = out / 'guest-uploads'
    result = {'scope': 'Isolated host Chrome with explicit SwiftShader; not macOS, Metal, hardware GPU or performance evidence.',
              'source': str(SOURCE.relative_to(ROOT)),
              'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              'browser_version': subprocess.check_output([browser, '--version'], text=True).strip(),
              'errors': [], 'passed': False}
    # Affects only this test process, so every owned orphan can be reaped.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'Cannot become a child subreaper')
    service = GuestFileService(SOURCE.parent, uploads, port=0)
    proc = None
    try:
        with tempfile.TemporaryDirectory(prefix='webgl-host-profile-') as temporary:
            scratch = Path(temporary)
            result['syntax_checks'] = syntax_checks(scratch)
            service.start()
            tag = 'host-webgl'
            url = service.report['host_url'] + '/get/webgl-probe.html#' + tag
            command = [browser, '--headless=new', '--user-data-dir=' + str(scratch / 'profile'),
                       '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
                       '--disable-sync', '--disable-component-update', '--no-proxy-server',
                       '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
                       '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0', url]
            result['command'] = command
            with (out / 'chrome-stdout.log').open('wb') as stdout, (out / 'chrome-stderr.log').open('wb') as stderr:
                proc = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True)
                result['browser_pid'] = proc.pid
                try:
                    report = uploads / (tag + '.json')
                    deadline = time.monotonic() + 50
                    while time.monotonic() < deadline:
                        if report.exists():
                            try:
                                json.loads(report.read_text())
                                break
                            except (ValueError, OSError):
                                pass
                        if proc.poll() is not None:
                            raise RuntimeError('Host browser exited before the report upload')
                        time.sleep(.1)
                    else:
                        raise TimeoutError('Host browser report upload')
                    result['validation'] = oracle.validate(uploads, tag)
                    if result['validation']['passed']:
                        result['negative_controls'] = negative_controls(uploads, tag)
                finally:
                    result['browser_cleanup'] = stop_browser(proc)
                    proc = None
    except Exception as error:
        result['errors'].append(f'{type(error).__name__}: {error}')
    finally:
        if proc is not None:
            result['browser_cleanup'] = stop_browser(proc)
        service.close()
        result['file_service'] = service.report
    result['passed'] = (not result['errors'] and result.get('validation', {}).get('passed') is True
                        and len(result.get('negative_controls', [])) == 8
                        and all(row['passed'] for row in result['negative_controls'])
                        and result.get('browser_cleanup', {}).get('remaining_children') == [])
    (out / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'results': str(out / 'results.json'), 'passed': result['passed'],
                      'errors': result['errors'], 'scope': result['scope']}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
