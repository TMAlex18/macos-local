#!/usr/bin/env python3
"""Offline contract tests for gpuwatch.sh's bounded process probes."""
from pathlib import Path
import http.server
import io
import json
import signal
import subprocess
import shutil
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import os

from guest_files import make_handler


SCRIPT = Path(__file__).with_name('guest-diag') / 'gpuwatch.sh'
START_SCRIPT = Path(__file__).with_name('guest-diag') / 'start-gpuwatch.sh'
SAFARI_SCRIPT = Path(__file__).with_name('guest-diag') / 'safari-probe.sh'
CANARY_SCRIPT = Path(__file__).with_name('guest-diag') / 'log-canary.sh'
CONTROL_SCRIPT = Path(__file__).with_name('guest-diag') / 'spindump-control.sh'

# Retained compact-log lines: Run89 r89before (other client) and Run88
# a88after (Safari). Ownership must come from the emitter, never just the
# presence of a GPU PID or a GPU-looking process name.
OTHER_OWNER = "2026-11-05 02:54:10.386 Df nsattributedstringagent[1673:7ed6] [com.apple.WebKit:ProcessSuspension] 0x114090180 - ProcessAssertion::acquireSync Trying to take RBS assertion 'GPUProcess Foreground Assertion' for process with PID=1676"
OTHER_CONNECTION = '2026-11-05 02:54:22.505 Df com.apple.WebKit.GPU[1676:7eff] [com.apple.WebKit:Process] 0x10b0400c0 - GPUProcess::createGPUConnectionToWebProcess: processIdentifier=4'
SAFARI_OWNER = "2026-11-04 15:18:12.130 Df Safari[1494:63c1] [com.apple.WebKit:ProcessSuspension] 0x11b14c120 - ProcessAssertion::acquireSync Successfully took RBS assertion 'GPUProcess Foreground Assertion' for process with PID=1542"
SAFARI_CONNECTION = '2026-11-04 15:18:18.844 Df com.apple.WebKit.GPU[1542:630b] [com.apple.WebKit:Process] 0x10a0380c0 - GPUProcess::createGPUConnectionToWebProcess: processIdentifier=2'


def bounded_functions():
    lines = SCRIPT.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line == 'bounded_exec() {')
    end = next(i for i, line in enumerate(lines[start:], start) if line == 'collect_window() {')
    return '\n'.join(lines[start:end])


def run_shell(body):
    script = bounded_functions() + '\n' + body
    return subprocess.run(['sh', '-c', script], text=True, capture_output=True)


class SafariFixtureURLTests(unittest.TestCase):
    def select(self,url,host='http://10.0.2.2:18769'):
        body=SAFARI_SCRIPT.read_text().split('select_fixture_url() {',1)[1].split('\n}\nselect_fixture_url',1)[0]
        script='select_fixture_url() {'+body+'\n}\nH=$1\nselect_fixture_url "$2" || exit $?\nprintf "%s\\n%s\\n" "$fixture_name" "$webgl_probe_tag"\n'
        return subprocess.run(['sh','-c',script,'sh',host,url],capture_output=True,text=True,timeout=2)

    def test_fixed_webgl_fragment_has_strict_ascii_tag(self):
        base='http://10.0.2.2:18769/get/webgl-probe.html'
        for tag in ('a','r128-webgl','A'+('_-'*23)+'z'):
            with self.subTest(tag=tag):
                result=self.select(base+'#'+tag)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.splitlines(),['webgl-probe.html',tag])
        for tag in ('','_bad','-bad','a'*49,'bad.tag','bad/tag','a%20b','a?b','a#b','a b','a\nb','é'):
            with self.subTest(tag=tag):self.assertEqual(self.select(base+'#'+tag).returncode,2)
        for url in (base,base+'?x',base+'?x#valid'):
            with self.subTest(url=url):self.assertEqual(self.select(url).returncode,2)

    def test_other_existing_urls_keep_legacy_render_preparation(self):
        for url in ('file:///tmp/fixture.html','http://10.0.2.2:18769/get/render.html',
                    'https://example.test/fixture','-',
                    'https://example.test/get/webgl-probe.html#legacy',
                    'http://10.0.2.2:18769/get/webgl-probe.html/other#legacy'):
            with self.subTest(url=url):
                result=self.select(url)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.splitlines()[0],'render.html')

    def test_invalid_fixed_webgl_url_exits_before_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bindir=root/'bin';bindir.mkdir();calls=root/'calls'
            for name in ('date','sysctl','vm_stat','curl','shasum','pgrep','osascript','open'):
                p=bindir/name;p.write_text('#!/bin/sh\necho called >> "$CALLS"\nexit 99\n');p.chmod(0o755)
            tag=f'offline-webgl-invalid-{os.getpid()}'
            result=subprocess.run(['/bin/sh',str(SAFARI_SCRIPT),tag,
                'http://10.0.2.2:18769/get/webgl-probe.html#bad%tag'],
                env=dict(os.environ,H='http://10.0.2.2:18769',PATH=f'{bindir}:/usr/bin:/bin',CALLS=str(calls)),
                capture_output=True,text=True,timeout=3)
            self.assertEqual(result.returncode,2)
            self.assertFalse(calls.exists())

    def test_plain_helper_prefetches_fixed_page_and_preserves_fragment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);serve=root/'serve';serve.mkdir();uploads=root/'uploads'
            bindir=root/'bin';bindir.mkdir();calls=root/'watcher-argv'
            for name in ('render.html','webgl-probe.html'):
                shutil.copy(SCRIPT.parent/name,serve/name)
            (serve/'gpuwatch.sh').write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$WATCH_ARGS"\nexit 0\n')
            for name,body in {'sysctl':'echo fixture-memory','vm_stat':'echo fixture-vm-stat','pgrep':'exit 1'}.items():
                p=bindir/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o755)
            requested=[]
            Handler=make_handler(serve,uploads,32<<20)
            class RecordedHandler(Handler):
                def do_GET(self):requested.append(self.path);super().do_GET()
                def log_message(self,*_):pass
            server=http.server.ThreadingHTTPServer(('127.0.0.1',0),RecordedHandler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            host=f'http://127.0.0.1:{server.server_port}'
            env=dict(os.environ,H=host,PATH=f'{bindir}:/usr/bin:/bin',WATCH_ARGS=str(calls))
            try:
                for fixture,fragment in (('render.html',''),('webgl-probe.html','#r128-webgl')):
                    tag=f'offline-webgl-{fixture.split(".")[0]}-{os.getpid()}'
                    url=host+'/get/'+fixture+fragment
                    try:
                        result=subprocess.run(['/bin/sh',str(SAFARI_SCRIPT),tag,url],env=env,
                                              capture_output=True,text=True,timeout=10)
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                        self.assertEqual(calls.read_text().splitlines()[2],url)
                        self.assertEqual((Path('/tmp')/(tag+'.html')).read_bytes(),(serve/fixture).read_bytes())
                        prep=(uploads/(tag+'-prepare.txt')).read_text()
                        self.assertIn('prep_step=fetch-'+fixture.removesuffix('.html'),prep)
                        self.assertIn('endpoint='+host+'/get/'+fixture,prep)
                    finally:
                        for suffix in ('.html','-watch.sh','-prepare.txt','-after.txt','-prepare-current.stdout','-prepare-current.stderr'):
                            (Path('/tmp')/(tag+suffix)).unlink(missing_ok=True)
                self.assertIn('/get/webgl-probe.html',requested)
                self.assertFalse(any('#' in path for path in requested))
            finally:
                server.shutdown();thread.join(timeout=3);server.server_close()


class WatchTests(unittest.TestCase):
    def parse_connection_events(self, lines, mode='sample-connection'):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = bounded_functions() + '\nstream_connection_events "$1" "$2" "$3" "$4"'
            result = subprocess.run(
                ['sh', '-c', script, 'sh', str(root / 'stream'),
                 str(root / 'trigger'), str(root / 'raw'), mode],
                input='\n'.join(lines) + '\n', text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'stream').read_text(), '\n'.join(lines) + '\n')
            return (root / 'trigger').read_text() if (root / 'trigger').exists() else None

    def test_connection_trigger_requires_safari_owner_and_only_fires_once(self):
        trigger = self.parse_connection_events([
            OTHER_OWNER, OTHER_CONNECTION, SAFARI_OWNER, SAFARI_CONNECTION,
            SAFARI_CONNECTION.replace('processIdentifier=2', 'processIdentifier=7')])
        self.assertEqual(trigger, f'1542\n{SAFARI_OWNER}\n{SAFARI_CONNECTION}\n')

    def test_connection_trigger_does_not_sample_discovery_unknown_or_other_owner(self):
        self.assertIsNone(self.parse_connection_events([
            SAFARI_CONNECTION, OTHER_OWNER, OTHER_CONNECTION, SAFARI_OWNER]))
        reassigned = SAFARI_OWNER.replace('Safari[', 'nsattributedstringagent[')
        self.assertIsNone(self.parse_connection_events([
            SAFARI_OWNER, reassigned, SAFARI_CONNECTION]))

    def test_sample_window_triggers_on_first_successful_safari_assertion_only(self):
        trying = SAFARI_OWNER.replace('Successfully took', 'Trying to take')
        other_success = OTHER_OWNER.replace('Trying to take', 'Successfully took')
        self.assertIsNone(self.parse_connection_events(
            [other_success, OTHER_CONNECTION, trying, SAFARI_CONNECTION], 'sample-window'))
        trigger = self.parse_connection_events([
            other_success, OTHER_CONNECTION, trying, SAFARI_OWNER,
            SAFARI_CONNECTION, SAFARI_OWNER.replace('1542', '1592')], 'sample-window')
        self.assertEqual(trigger, f'1542\n{SAFARI_OWNER}\n{SAFARI_OWNER}\n')

    def test_stackshot_reuses_successful_safari_ownership_trigger(self):
        trying = SAFARI_OWNER.replace('Successfully took', 'Trying to take')
        self.assertIsNone(self.parse_connection_events(
            [OTHER_OWNER, OTHER_CONNECTION, trying, SAFARI_CONNECTION], 'stackshot'))
        trigger = self.parse_connection_events(
            [OTHER_OWNER, trying, SAFARI_OWNER, SAFARI_CONNECTION, SAFARI_OWNER], 'stackshot')
        self.assertEqual(trigger, f'1542\n{SAFARI_OWNER}\n{SAFARI_OWNER}\n')

    def test_burst_constructor_fires_once_without_claiming_safari_ownership(self):
        constructor = ('2026-11-06 09:36:36.740 Df com.apple.WebKit.GPU[886:2ae4] '
                       '(WebKit) 0x10f03c0c0 - GPUProcess::GPUProcess:')
        spoofed = constructor.replace('com.apple.WebKit.GPU[', 'Other.com.apple.WebKit.GPU[')
        owner = SAFARI_OWNER.replace('1542', '886')
        trigger = self.parse_connection_events([
            SAFARI_OWNER, SAFARI_CONNECTION, spoofed, constructor,
            constructor.replace('886:', '987:'), owner], 'sample-burst')
        self.assertEqual(trigger,
                         f'886\npending-posthoc-Safari-ownership\n{constructor}\n')
        self.assertIsNone(self.parse_connection_events(
            [SAFARI_OWNER, SAFARI_CONNECTION, spoofed], 'sample-burst'))

    def test_burst_archive_keeps_empty_and_real_stacks_and_later_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); bindir = root / 'bin'; bindir.mkdir()
            target = subprocess.Popen(['sleep', '60'])
            constructor = (f'2026-11-06 09:36:36.740 Df com.apple.WebKit.GPU[{target.pid}:2ae4] '
                           '(WebKit) 0x10f03c0c0 - GPUProcess::GPUProcess:')
            owner = SAFARI_OWNER.replace('1542', str(target.pid))
            fixture = root / 'stream.txt'; fixture.write_text(constructor + '\n' + owner + '\n')
            calls = root / 'calls'; uploads = root / 'uploads'; uploads.mkdir()
            sample_body = ('#!/bin/sh\nprintf "%s\n" "$*" >> "$SAMPLE_CALLS"\n'
                           'while [ "$#" -gt 0 ]; do\n'
                           '  if [ "$1" = -file ]; then output=$2; break; fi\n'
                           '  shift\ndone\n'
                           'printf "Date/Time: 2026-11-06 09:37:05.629 +0000\\nCall graph:\\n" > "$output"\n'
                           'case "$output" in *-1.txt) ;; *)\n'
                           '  printf "    41 Thread_7243 DispatchQueue_1: main\\n'
                           '      41 start (in dyld) + 6688 [0x19a15fe80]\\n'
                           '        41 __sigsuspend (in libsystem_kernel.dylib) [0x19a4fecc0]\\n" >> "$output";; esac\n'
                           'printf "Total number in stack (recursive counted multiple, when >=5):\\n'
                           'Binary Images:\\n0x123 - 0x456 image\\n" >> "$output"\n')
            for name, body in {
                'log': '#!/bin/sh\ncat "$LOG_FIXTURE"\nexec sleep 60\n',
                'pgrep': '#!/bin/sh\nexit 1\n',
                'ps': '#!/bin/sh\necho ps-ok\n',
                'sample': sample_body,
                'curl': '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do\n'
                        '  if [ "$1" = -T ]; then shift; source=$1; fi\n'
                        '  shift\ndone\ncp "$source" "$UPLOADS_DIR/$(basename "$source")"\n',
            }.items():
                path = bindir / name; path.write_text(body); path.chmod(0o755)
            tag = f'offline-burst-{os.getpid()}'
            env = dict(os.environ, PATH=f'{bindir}:/usr/bin:/bin',
                       SAMPLE_CALLS=str(calls), LOG_FIXTURE=str(fixture), UPLOADS_DIR=str(uploads))
            try:
                result = subprocess.run(['sh', str(SCRIPT), tag, '0', '-', 'sample-burst'],
                                        text=True, capture_output=True, env=env, timeout=25)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(calls.read_text().splitlines()), 4)
                with tarfile.open(uploads / f'{tag}.tgz') as bundle:
                    self.assertEqual(bundle.extractfile(f'{tag}/webkit-stream.log').read().decode(),
                                     fixture.read_text())
                    trigger = bundle.extractfile(f'{tag}/connection-trigger.txt').read().decode()
                    self.assertIn('owner_log=pending-posthoc-Safari-ownership', trigger)
                    for number in range(1, 5):
                        base = f'{tag}/sample-{target.pid}-{number}'
                        argv = bundle.extractfile(base + '.argv').read().decode().splitlines()
                        self.assertEqual(argv, ['sample', str(target.pid), '1', '20', '-mayDie',
                                                '-file', f'/tmp/{base}.txt'])
                        timing = bundle.extractfile(base + '.timing.txt').read().decode()
                        self.assertIn('started_utc=', timing)
                        self.assertIn('finished_utc=', timing)
                        self.assertIn('sample_exit=0', timing)
                        self.assertIn('total_budget_seconds=30', timing)
                        if number == 1:
                            self.assertIn('thread_rows=0\nstack_frame_rows=0\nstack_acquired=false', timing)
                        else:
                            self.assertIn('thread_rows=1\nstack_frame_rows=2\nstack_acquired=true', timing)
            finally:
                target.terminate(); target.wait(timeout=5)
                shutil.rmtree(Path('/tmp') / tag, ignore_errors=True)
                (Path('/tmp') / f'{tag}.tgz').unlink(missing_ok=True)

    def test_burst_stops_after_target_exits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); bindir = root / 'bin'; bindir.mkdir()
            target = subprocess.Popen(['sleep', '60'])
            reaper = threading.Thread(target=target.wait); reaper.start()
            sample = bindir / 'sample'
            sample.write_text('#!/bin/sh\nkill -TERM "$1"\nsleep 1\n')
            sample.chmod(0o755)
            env = dict(os.environ, PATH=f'{bindir}:/usr/bin:/bin')
            try:
                script = bounded_functions() + '\nd=$1; sample_burst "$2"'
                result = subprocess.run(['sh', '-c', script, 'sh', str(root), str(target.pid)],
                                        env=env, capture_output=True, text=True, timeout=8)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(list(root.glob('sample-*.argv'))), 1)
                self.assertIn('reason=target-gone', (root / 'timeline.txt').read_text())
                self.assertIn('stack_acquired=false', next(root.glob('*.timing.txt')).read_text())
            finally:
                if target.poll() is None: target.terminate()
                reaper.join(timeout=5)

    def test_burst_budget_bounds_hung_segments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); bindir = root / 'bin'; bindir.mkdir()
            target = subprocess.Popen(['sleep', '60'])
            sample = bindir / 'sample'
            sample.write_text('#!/bin/sh\nexec sleep 60\n'); sample.chmod(0o755)
            env = dict(os.environ, PATH=f'{bindir}:/usr/bin:/bin')
            try:
                script = bounded_functions() + '\nd=$1; sample_burst "$2"'
                started = time.monotonic()
                result = subprocess.run(['sh', '-c', script, 'sh', str(root), str(target.pid)],
                                        env=env, capture_output=True, text=True, timeout=33)
                elapsed = time.monotonic() - started
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertLess(elapsed, 31.5)
                reports = sorted(root.glob('*.timing.txt'))
                self.assertGreaterEqual(len(reports), 2)
                self.assertLessEqual(len(reports), 4)
                for report in reports:
                    self.assertIn('sample_exit=124', report.read_text())
                    self.assertIn('stack_acquired=false', report.read_text())
                self.assertIn('reason=budget', (root / 'timeline.txt').read_text())
            finally:
                target.terminate(); target.wait(timeout=5)

    def connection_sample_archive(self, *, timeout, mode='sample-connection'):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bindir = root / 'bin'; bindir.mkdir()
            fixture = root / 'stream.txt'
            fixture.write_text('\n'.join([OTHER_OWNER, OTHER_CONNECTION,
                                         SAFARI_OWNER, SAFARI_CONNECTION,
                                         SAFARI_CONNECTION]) + '\n')
            calls = root / 'sample-calls.txt'
            uploads = root / 'uploads'; uploads.mkdir()
            sample_body = ('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SAMPLE_CALLS"\n'
                           'while [ "$#" -gt 0 ]; do\n'
                           '  if [ "$1" = -file ]; then output=$2; break; fi\n'
                           '  shift\ndone\n'
                           'printf "Date/Time:       2026-11-06 03:00:00.123 +0000\\n" > "$output"\n')
            sample_body += 'exec sleep 60\n' if timeout else 'exit 0\n'
            for name, body in {
                'log': '#!/bin/sh\ncat "$LOG_FIXTURE"\nexec sleep 60\n',
                'pgrep': '#!/bin/sh\nexit 1\n',
                'ps': '#!/bin/sh\necho ps-ok\n',
                'sample': sample_body,
                'curl': '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do\n'
                        '  if [ "$1" = -T ]; then shift; source=$1; fi\n'
                        '  shift\ndone\ncp "$source" "$UPLOADS_DIR/$(basename "$source")"\n',
            }.items():
                path = bindir / name; path.write_text(body); path.chmod(0o755)
            tag = f'offline-{mode}-{os.getpid()}-{int(timeout)}'
            env = dict(os.environ, PATH=f'{bindir}:/usr/bin:/bin',
                       SAMPLE_CALLS=str(calls), LOG_FIXTURE=str(fixture),
                       UPLOADS_DIR=str(uploads))
            try:
                result = subprocess.run(['sh', str(SCRIPT), tag, '0', '-', mode],
                                        text=True, capture_output=True, env=env, timeout=40)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(calls.read_text().splitlines()), 1)
                expected_args = ['1542', '45', '20', '-mayDie'] if mode == 'sample-window' else ['1542', '1']
                expected_args += ['-file', f'/tmp/{tag}/sample-1542.txt']
                self.assertEqual(calls.read_text().strip().split(), expected_args)
                with tarfile.open(uploads / f'{tag}.tgz') as bundle:
                    timeline = bundle.extractfile(f'{tag}/timeline.txt').read().decode()
                    trigger = bundle.extractfile(f'{tag}/connection-trigger.txt').read().decode()
                    raw = bundle.extractfile(f'{tag}/webkit-stream.log').read().decode()
                    sample_log = bundle.extractfile(f'{tag}/sample-1542.log').read().decode()
                    argv = bundle.extractfile(f'{tag}/sample-1542.argv').read().decode().splitlines()
                self.assertEqual(argv, ['sample'] + expected_args)
                self.assertIn('guest_observed_utc=', trigger)
                self.assertIn(SAFARI_OWNER, trigger)
                self.assertIn('trigger_log=' + (SAFARI_OWNER if mode == 'sample-window' else SAFARI_CONNECTION), trigger)
                self.assertIn('sample_deadline_seconds=' + ('80' if mode == 'sample-window' else '20'), trigger)
                self.assertIn('Date/Time:       2026-11-06 03:00:00.123 +0000', trigger)
                self.assertIn('host_time_source=guest_files HTTP PUT receipt', trigger)
                self.assertEqual(raw, fixture.read_text())
                self.assertIn('end ', timeline)
                self.assertIn('exit=124' if timeout else 'exit=0', timeline)
                if timeout:
                    self.assertIn('timeout=true', sample_log)
            finally:
                shutil.rmtree(Path('/tmp') / tag, ignore_errors=True)
                (Path('/tmp') / f'{tag}.tgz').unlink(missing_ok=True)

    def test_connection_sample_uses_event_then_archives_actual_sample_date(self):
        self.connection_sample_archive(timeout=False)

    def test_connection_sample_timeout_still_uploads_archive(self):
        self.connection_sample_archive(timeout=True)

    def test_sample_window_consumes_45_seconds_20_ms_maydie_and_archives_exact_argv(self):
        self.connection_sample_archive(timeout=False, mode='sample-window')

    def test_interactive_launcher_backgrounds_watcher(self):
        text = START_SCRIPT.read_text()
        self.assertIn('nohup sh', text)
        self.assertIn('gpuwatch_started pid=', text)
        self.assertIn('guest_files_preflight=passed', text)

    def test_interactive_launcher_records_missing_guest_file_service(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            bindir = root / 'bin'
            bindir.mkdir()
            (bindir / 'curl').write_text('#!/bin/sh\necho connection-refused >&2\nexit 7\n')
            curl = bindir / 'curl'
            curl.chmod(0o755)
            tag = f'offline-preflight-{os.getpid()}'
            log = Path('/tmp') / f'{tag}-gpuwatch-launch.log'
            watch = Path('/tmp') / f'{tag}-gpuwatch.sh'
            log.unlink(missing_ok=True)
            watch.unlink(missing_ok=True)
            env = dict(os.environ, PATH=f'{bindir}:/usr/bin:/bin', H='http://missing.invalid:18769')
            result = subprocess.run(
                ['sh', str(START_SCRIPT), tag, '30', '-'],
                text=True, capture_output=True, env=env, timeout=10,
            )
            self.assertEqual(result.returncode, 12, result.stderr)
            self.assertFalse(watch.exists())
            self.assertIn('guest_files_preflight=failed', log.read_text())
            log.unlink(missing_ok=True)

    def test_outer_supervisor_and_archive_path_are_present(self):
        text = SCRIPT.read_text()
        self.assertIn('collect_window &', text)
        self.assertIn('outer_deadline=', text)
        self.assertIn('tar -C /tmp -czf', text)
        self.assertIn('curl -fsS --connect-timeout 5 -m 120 -T', text)

    def test_exec_timeout_writes_marker_and_returns_124(self):
        result = run_shell(
            "tmp=$(mktemp); bounded_exec 1 \"$tmp\" sleep 3; "
            "status=$?; grep -q 'timeout=true' \"$tmp\"; marker=$?; "
            "rm -f \"$tmp\"; test \"$status\" -eq 124 -a \"$marker\" -eq 0"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_timed_out_process_probes_are_reaped_before_archive_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bindir = root / 'bin'; bindir.mkdir()
            pids = root / 'probe-pids.jsonl'
            receipt = root / 'upload-receipt.json'
            probe = (f'#!{sys.executable}\n'
                     'import json, os, signal, sys, time\n'
                     'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                     'with open(os.environ["PROBE_PIDS"], "a") as record:\n'
                     '    record.write(json.dumps({"tool": os.path.basename(sys.argv[0]), '
                     '"pid": os.getpid(), "argv": sys.argv[1:]}) + "\\n")\n'
                     'if os.path.basename(sys.argv[0]) == "ps":\n'
                     '    print("  PID PPID STATE PRI PCPU TIME ETIME COMM", flush=True)\n'
                     '    print("  123 1 S 31 0.0 0:00 0:01 /System/Safari", flush=True)\n'
                     '    print("  456 1 S 31 0.0 0:00 0:01 /System/OtherApp", flush=True)\n'
                     '    print("ps: diagnostic warning", file=sys.stderr, flush=True)\n'
                     'time.sleep(60)\n')
            upload = (f'#!{sys.executable}\n'
                      'import json, os\n'
                      'from pathlib import Path\n'
                      'probes = [json.loads(line) for line in '
                      'Path(os.environ["PROBE_PIDS"]).read_text().splitlines()]\n'
                      'for probe in probes:\n'
                      '    try:\n'
                      '        os.kill(probe["pid"], 0)\n'
                      '    except ProcessLookupError:\n'
                      '        probe["alive_at_upload"] = False\n'
                      '    else:\n'
                      '        probe["alive_at_upload"] = True\n'
                      'Path(os.environ["UPLOAD_RECEIPT"]).write_text(json.dumps(probes))\n')
            for name, body in {
                'pgrep': probe, 'ps': probe,
                'log': '#!/bin/sh\nexec sleep 60\n',
                'curl': upload,
            }.items():
                path = bindir / name; path.write_text(body); path.chmod(0o755)
            tag = f'offline-probe-reap-{os.getpid()}'
            env = dict(os.environ, PATH=f'{bindir}:/usr/bin:/bin',
                       PROBE_PIDS=str(pids), UPLOAD_RECEIPT=str(receipt))
            try:
                result = subprocess.run(['sh', str(SCRIPT), tag, '1', '-'],
                                        text=True, capture_output=True, env=env, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                probes = json.loads(receipt.read_text())
                self.assertEqual([probe['tool'] for probe in probes], ['pgrep', 'ps'])
                self.assertEqual(probes[0]['argv'], ['-f', 'com.apple.WebKit.GPU'])
                self.assertEqual(probes[1]['argv'],
                                 ['-A', '-o', 'pid,ppid,state,pri,pcpu,time,etime,comm'])
                self.assertTrue(all(not probe['alive_at_upload'] for probe in probes), probes)
                with tarfile.open(Path('/tmp') / f'{tag}.tgz') as bundle:
                    timeline = bundle.extractfile(f'{tag}/timeline.txt').read().decode()
                    ps_output = bundle.extractfile(f'{tag}/ps.txt').read().decode()
                    gpu_pids = bundle.extractfile(f'{tag}/gpu-pids.txt').read().decode()
                    self.assertNotIn(f'{tag}/ps-current.tmp', bundle.getnames())
                self.assertIn('pgrep-gpu-timeout', timeline)
                self.assertIn('ps-all-timeout', timeline)
                self.assertIn('timeout=true command=pgrep', gpu_pids)
                self.assertIn('/System/Safari', ps_output)
                self.assertNotIn('/System/OtherApp', ps_output)
                self.assertIn('ps: diagnostic warning', ps_output)
                self.assertIn('timeout=true command=ps', ps_output)
            finally:
                # Also contain a regression that leaves a pipeline descendant.
                if pids.exists():
                    for line in pids.read_text().splitlines():
                        try:
                            os.kill(json.loads(line)['pid'], signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                shutil.rmtree(Path('/tmp') / tag, ignore_errors=True)
                (Path('/tmp') / f'{tag}.tgz').unlink(missing_ok=True)

    def test_success_preserves_output_and_status(self):
        result = run_shell(
            "tmp=$(mktemp); bounded_exec 2 \"$tmp\" sh -c 'printf ready'; "
            "status=$?; grep -q ready \"$tmp\"; marker=$?; "
            "rm -f \"$tmp\"; test \"$status\" -eq 0 -a \"$marker\" -eq 0"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_full_script_reaches_archive_upload_with_stubbed_guest_tools(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            bindir = root / 'bin'
            bindir.mkdir()
            (bindir / 'log').write_text('#!/bin/sh\nexec sleep 30\n')
            (bindir / 'pgrep').write_text('#!/bin/sh\nexit 1\n')
            (bindir / 'ps').write_text('#!/bin/sh\necho ps-ok\n')
            (bindir / 'open').write_text('#!/bin/sh\nexit 0\n')
            (bindir / 'tar').write_text(
                '#!/bin/sh\n'
                'out=\n'
                'for arg in "$@"; do\n'
                '  case "$arg" in /tmp/*.tgz) out=$arg;; esac\n'
                'done\n'
                '[ -z "$out" ] || : > "$out"\n'
            )
            (bindir / 'curl').write_text('#!/bin/sh\ntouch "$UPLOAD_MARKER"\nexit 0\n')
            for tool in bindir.iterdir():
                tool.chmod(0o755)
            marker = root / 'uploaded.marker'
            env = dict(os.environ)
            env.update(PATH=f'{bindir}:/usr/bin:/bin', UPLOAD_MARKER=str(marker),
                       H='http://stub.invalid:18769')
            tag = f'offline-full-{os.getpid()}'
            result = subprocess.run(
                ['sh', str(SCRIPT), tag, '0', '-'],
                text=True, capture_output=True, env=env, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.exists())
            archive = Path('/tmp') / f'{tag}.tgz'
            self.assertTrue(archive.exists())
            archive.unlink(missing_ok=True)
            work = Path('/tmp') / tag
            if work.exists():
                for child in work.iterdir():
                    child.unlink(missing_ok=True)
                work.rmdir()

    def test_background_launcher_downloads_and_uploads_real_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bindir = root / 'bin'
            bindir.mkdir()
            # Only replace guest-specific observers; use real HTTP, shell, tar,
            # gzip and curl, including the launch receipt and archive upload.
            for name, body in {
                'log': '#!/bin/sh\nexec sleep 30\n',
                'pgrep': '#!/bin/sh\nexit 1\n',
                'ps': '#!/bin/sh\necho ps-ok\n',
            }.items():
                path = bindir / name
                path.write_text(body)
                path.chmod(0o755)
            uploads = root / 'uploads'
            server = http.server.ThreadingHTTPServer(
                ('127.0.0.1', 0), make_handler(SCRIPT.parent, uploads, 1 << 20))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            tag = f'offline-http-{os.getpid()}'
            env = dict(os.environ, PATH=f'{bindir}:/usr/bin:/bin',
                       H=f'http://127.0.0.1:{server.server_port}')
            try:
                result = subprocess.run(['sh', str(START_SCRIPT), tag, '1', '-', 'sample-window'],
                                        text=True, capture_output=True, env=env, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('gpuwatch_started', result.stdout)
                receipt = (uploads / f'{tag}-started.txt').read_text()
                self.assertIn('guest_files_preflight=passed', receipt)
                archive = uploads / f'{tag}.tgz'
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if archive.exists():
                        try:
                            with tarfile.open(fileobj=io.BytesIO(archive.read_bytes())) as bundle:
                                timeline = bundle.extractfile(f'{tag}/timeline.txt').read().decode()
                            break
                        except (tarfile.TarError, EOFError):
                            pass
                    time.sleep(.1)
                else:
                    self.fail('Background watcher did not upload a complete archive')
                self.assertIn('start ', timeline)
                self.assertIn('end ', timeline)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                shutil.rmtree(Path('/tmp') / tag, ignore_errors=True)
                for suffix in ('.tgz', '-gpuwatch.sh', '-gpuwatch-launch.log'):
                    (Path('/tmp') / (tag + suffix)).unlink(missing_ok=True)

class StackshotTests(unittest.TestCase):
    """Linux process/HTTP checks; sudo, log and spindump are explicit fakes."""

    def test_canary_probe_requires_real_exact_nonce_event_not_filtering_or_header(self):
        source = CANARY_SCRIPT.read_text()
        start = source.index('    awk -v archive=')
        start = source.index(" '\n", start) + 2
        end = source.index("    ' < \"$d/live.fifo\"", start)
        parser = source[start:end]
        nonce = 'e05_gpuwatch_ready_offline_123_456'
        valid = ('2026-11-07 01:30:00.123 Df log[123:abc] '
                 '[local.e05.gpuwatch:readiness] ' + nonce)
        cases = [
            ('filtering', 'Filtering the log data using eventMessage == "' + nonce + '"', False),
            ('header', 'Timestamp               Ty Process[PID:TID]', False),
            ('other-process', valid.replace('log[', 'Other['), False),
            ('other-nonce', valid + '-wrong', False),
            ('exact-event', valid, True),
        ]
        for name, line, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                result = subprocess.run(['awk', '-v', f'archive={root / "stream"}',
                                         '-v', f'nonce={nonce}', '-v', f'ready={root / "ready"}', parser],
                                        input=line + '\n', capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root / 'stream').read_text(), line + '\n')
                self.assertEqual((root / 'ready').exists(), expected)
                # The same real-event requirement is integrated into the GPU
                # parser, which must continue to archive WebKit ownership too.
                result = subprocess.run([
                    'sh', '-c', bounded_functions() +
                    '\nstream_connection_events "$1" "$2" "$3" stackshot "$4" "$5"',
                    'sh', str(root / 'gpu-stream'), str(root / 'trigger'), str(root / 'raw'),
                    nonce, str(root / 'gpu-ready')], input=line + '\n',
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root / 'gpu-ready').exists(), expected)
                self.assertEqual((root / 'gpu-stream').read_text(), line + '\n')

    def test_stack_counts_require_gpu_pid_threads_and_frames_not_anchor_or_exit_zero(self):
        report = ('Date/Time: 2026-11-06 16:27:20.275 +0000\n'
                  'End time: 2026-11-06 16:27:30.276 +0000\n'
                  'Process: sleep [42]\n  Thread 0x123 83 samples (1-83)\n'
                  '  83 start (dyld + 204416) [0x183523e80] 1-83\n'
                  '  Binary Images:\n')
        for name, body, expected in [
            ('anchor-only', report, 'unknown'),
            ('empty-gpu', report + 'Process: com.apple.WebKit.GPU [1542]\n  Binary Images:\n', 'unknown'),
            ('wrong-name', report + 'Process: Other [1542]\n  Thread 0x123 1 samples\n  1 start [0x123] 1\n', 'unknown'),
            ('real-gpu', report + 'Process: com.apple.WebKit.GPU [1542]\nNum samples: 57 (1-57)\n'
             '  Thread 0x456 57 samples (1-57)\n  57 start (dyld + 2) [0x123] 1-57\n'
             '   *57 ??? (kernel + 3) [0xfff] 1-57\n  Binary Images:\n', 'true'),
        ]:
            with self.subTest(name=name), tempfile.NamedTemporaryFile(mode='w') as fixture:
                fixture.write(body); fixture.flush()
                result = subprocess.run(['sh', '-c', bounded_functions() + '\nstackshot_counts "$1" 1542',
                                         'sh', fixture.name], capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('target_stack_acquired=' + expected, result.stdout)
                self.assertIn('safari_initialization_coverage=unknown', result.stdout)
                self.assertNotIn('83 start', result.stdout)
                if expected == 'true':
                    self.assertIn('thread_rows=1\nstack_frame_rows=2', result.stdout)
                    self.assertIn('Num samples: 57 (1-57)', result.stdout)

    def run_chain(self, behavior='complete', *, sudo_fail=False, canary='delivered', mode='stackshot', query='absent'):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); bindir = root / 'bin'; bindir.mkdir()
            serve = root / 'serve'; serve.mkdir()
            script = SCRIPT.read_text()
            # Only shorten wall-clock limits in this test copy, so real ignored
            # TERM / KILL / wait paths run without a 120-second test delay.
            if behavior in ('timeout', 'term-save'):
                script = script.replace('spin_deadline=$(( now + sampler_outer_seconds ))', 'spin_deadline=$(( now + 2 ))')
            if behavior == 'timeout' and mode == 'stackshot-system-raw':
                script = script.replace('sampler_term_grace=10', 'sampler_term_grace=2')
            if canary != 'delivered':
                script = script.replace('ready_deadline=$(( $(date +%s) + 15 ))',
                                        'ready_deadline=$(( $(date +%s) + 1 ))')
            if canary == 'hang':
                script = script.replace('canary_deadline=$(( $(date +%s) + 5 ))',
                                        'canary_deadline=$(( $(date +%s) + 1 ))')
            (serve / 'gpuwatch.sh').write_text(script)
            shutil.copy(SCRIPT.parent / 'render.html', serve)
            fixture = root / 'stream.txt'
            fixture.write_text(OTHER_OWNER + '\n' + SAFARI_OWNER + '\n' + SAFARI_CONNECTION + '\n')
            marker = root / 'opened'; calls = root / 'calls.jsonl'
            prefix = (f'#!{sys.executable}\nimport os, sys, time, json, signal\nfrom pathlib import Path\n'
                      'with open(os.environ["CALLS"], "a") as f:\n'
                      ' f.write(json.dumps({"tool": Path(sys.argv[0]).name, "pid": os.getpid(), '
                      '"argv": sys.argv[1:], "root": os.environ.get("FAKE_ROOT", "0")}) + "\\n")\n')
            log = prefix + ('if sys.argv[1] == "emit":\n'
                            ' print("emit stdout", flush=True)\n'
                            ' print("emit stderr", file=sys.stderr, flush=True)\n'
                            ' if os.environ["CANARY_BEHAVIOR"] == "failure": sys.exit(79)\n'
                            ' if os.environ["CANARY_BEHAVIOR"] == "hang":\n'
                            '  signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                            '  time.sleep(60)\n'
                            ' Path(os.environ["CANARY_MESSAGE"]).write_text(sys.argv[-1])\n'
                            ' sys.exit(0)\n'
                            'print("Filtering the log data using " + sys.argv[-1], flush=True)\n'
                            'while not Path(os.environ["CANARY_MESSAGE"]).exists(): time.sleep(.01)\n'
                            'print("Timestamp               Ty Process[PID:TID]", flush=True)\n'
                            'if os.environ["CANARY_BEHAVIOR"] == "delivered":\n'
                            ' print("2026-11-06 17:31:07.632 Df log[1577:6845] "\n'
                            '       "[local.e05.gpuwatch:readiness] " +\n'
                            '       Path(os.environ["CANARY_MESSAGE"]).read_text(), flush=True)\n')
            log += ('while not Path(os.environ["OPEN_MARKER"]).exists(): time.sleep(.01)\n'
                    'time.sleep(.25)\n'
                    'print(Path(os.environ["LOG_FIXTURE"]).read_text(), end="", flush=True)\n'
                    'time.sleep(60)\n')
            spin = prefix + 'print("raw stdout", flush=True)\nprint("raw stderr", file=sys.stderr, flush=True)\n'
            probe = prefix
            if query == 'absent':
                probe += 'sys.exit(1)\n'
            elif query == 'present-then-absent':
                probe += ('marker=Path(os.environ["QUERY_MARKER"])\n'
                          'if marker.exists(): sys.exit(1)\n'
                          'marker.touch()\nprint("123", flush=True)\nsys.exit(0)\n')
            elif query in ('error', 'error-one'):
                probe += 'print("pgrep query error", file=sys.stderr, flush=True)\nsys.exit(' + ('1' if query == 'error-one' else '2') + ')\n'
            else:
                probe += ('signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                          'print("partial query stdout", flush=True)\n'
                          'print("partial query stderr", file=sys.stderr, flush=True)\n')
                if query == 'abort':
                    probe += 'time.sleep(.1)\nos.kill(os.getppid(), signal.SIGTERM)\n'
                probe += 'time.sleep(60)\n'
            if behavior == 'failure':
                spin += 'print("permission denied fixture", file=sys.stderr, flush=True)\nsys.exit(77)\n'
            elif behavior == 'abort':
                spin += 'import resource\nresource.setrlimit(resource.RLIMIT_CORE, (0, 0))\nos.kill(os.getpid(), signal.SIGABRT)\n'
            else:
                report = ('Date/Time: 2026-11-06 16:27:20.275 +0000\n'
                          'End time: 2026-11-06 16:27:30.276 +0000\n'
                          'Process: sleep [42]\n  Thread 0x123 83 samples (1-83)\n'
                          '  83 start (dyld + 2) [0x123] 1-83\n  Binary Images:\n')
                if behavior != 'empty':
                    report += ('Process: com.apple.WebKit.GPU [1542]\nNum samples: 57 (1-57)\n'
                               '  Thread 0x456 57 samples (1-57)\n'
                               '  57 start (dyld + 2) [0x123] 1-57\n'
                               '   *57 ??? (kernel + 3) [0xfff] 1-57\n  Binary Images:\n')
                if mode.startswith('stackshot-system'):
                    report += ('Process: FixtureServer [999]\nPath: /fixture/service\nUID: 55\n'
                               'Num samples: 100 (1-100)\nNum threads: 2\n'
                               '  Thread 0x7986 1 samples (87)\n  1 fixture_start [0x456] 87\n'
                               '  Thread 0x7c7e 10 samples (89-98)\n  10 fixture_wait [0x789] 89-98\n'
                               '  Binary Images:\n')
                report += 'BINARY REPRESENTATION FIXTURE RETAINED\n'
                if mode == 'stackshot-system-raw':
                    report = '' if behavior == 'raw-empty' else '\x00RAW SPINDUMP FIXTURE\x01'
                if behavior == 'term-save':
                    spin += ('def save_on_term(*_):\n'
                             + (' time.sleep(1.5)\n' if mode == 'stackshot-system-raw' else '') +
                             f' Path(sys.argv[sys.argv.index("-o") + 1]).write_text({report!r})\n'
                             ' sys.exit(0)\n'
                             'signal.signal(signal.SIGTERM, save_on_term)\ntime.sleep(60)\n')
                else:
                    spin += f'Path(sys.argv[sys.argv.index("-o") + 1]).write_text({report!r})\n'
                if behavior == 'timeout':
                    spin += 'signal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n'
                else:
                    # Deliberately finish after the 2-second observation window.
                    spin += 'time.sleep(3)\n'
            for name, body in {
                'id': '#!/bin/sh\nif [ "${FAKE_ROOT:-0}" = 1 ]; then echo 0; else echo 1000; fi\n',
                'sudo': '#!/bin/sh\n[ "${SUDO_FAIL:-0}" != 1 ] || { echo "sudo: authentication required" >&2; exit 1; }\n'
                        '[ "$1" != -n ] || shift\nexport FAKE_ROOT=1\nexec "$@"\n',
                'sysctl': '#!/bin/sh\necho "hw.memsize: 4294967296"\n',
                'vm_stat': '#!/bin/sh\necho "Pages free: 100"\n',
                'osascript': prefix + 'sys.exit(0)\n',
                'pgrep': probe,
                'open': prefix + 'assert os.environ.get("FAKE_ROOT", "0") == "0"\n'
                        'assert Path(os.environ["READY_FILE"]).is_file()\n'
                        'Path(os.environ["OPEN_MARKER"]).touch()\n',
                'log': log,
                'spindump': spin,
            }.items():
                path = bindir / name; path.write_text(body); path.chmod(0o755)
            if Path('/usr/bin/awk').resolve().name == 'mawk':
                # mawk otherwise waits for a full input block on a live FIFO;
                # use its streaming mode to exercise the installed BSD awk
                # consumer contract, without changing macOS production argv.
                wrapper = bindir / 'awk'
                wrapper.write_text('#!/bin/sh\nexec /usr/bin/awk -W interactive "$@"\n')
                wrapper.chmod(0o755)
            uploads = root / 'uploads'
            observed_alive = []
            Handler = make_handler(serve, uploads, 32 << 20)
            class CheckedHandler(Handler):
                def do_PUT(self):
                    if self.path.endswith('.tgz'):
                        summary = (Path('/tmp') / tag / 'spindump-summary.txt').read_text()
                        import re
                        pids = re.findall(r'(?:spindump_pid|anchor_pid|log_pid|parser_pid|canary_pid)=(\d+)', summary)
                        for pid in pids:
                            try: os.kill(int(pid), 0)
                            except ProcessLookupError: pass
                            else: observed_alive.append(int(pid))
                    elif self.path.endswith('-prepare.txt') and calls.exists():
                        for event in map(json.loads, calls.read_text().splitlines()):
                            if event['tool'] != 'pgrep': continue
                            try: os.kill(event['pid'], 0)
                            except ProcessLookupError: pass
                            else: observed_alive.append(event['pid'])
                    super().do_PUT()
                def log_message(self, *_): pass
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), CheckedHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            tag = f'offline-{mode}-{behavior}-{int(sudo_fail)}-{canary}-{query}-{os.getpid()}'
            env = dict(os.environ, PATH=f'{bindir}:/usr/bin:/bin',
                       H=f'http://127.0.0.1:{server.server_port}', CALLS=str(calls),
                       OPEN_MARKER=str(marker), READY_FILE=f'/tmp/{tag}/watcher-ready.txt',
                       LOG_FIXTURE=str(fixture), SUDO_FAIL=str(int(sudo_fail)),
                       CANARY_BEHAVIOR=canary, CANARY_MESSAGE=str(root / 'canary-message'))
            env['QUERY_MARKER'] = str(root / 'query-marker')
            try:
                result = subprocess.run(['sh', str(SAFARI_SCRIPT), tag,
                                         'https://example.test/fixture', mode, '2'],
                                        env=env, capture_output=True, text=True, timeout=20)
                if sudo_fail:
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(marker.exists())
                    self.assertFalse((Path('/tmp') / tag).exists())
                    self.assertIn('authentication required', (uploads / f'{tag}-sudo-preflight.txt').read_text())
                    return
                if query not in ('absent', 'present-then-absent'):
                    expected = {'hang': 124, 'abort': 143}.get(query, 13)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                    self.assertFalse(marker.exists())
                    self.assertFalse((Path('/tmp') / tag).exists())
                    self.assertFalse(observed_alive)
                    prep = (uploads / f'{tag}-prepare.txt').read_text()
                    self.assertIn('SAFARI_EXIT_UNKNOWN', prep)
                    self.assertNotIn('SAFARI_EXIT_CONFIRMED', prep)
                    self.assertIn('reaped=true', prep)
                    if query == 'hang': self.assertIn('exit=137 timeout=true', prep)
                    if query == 'abort':
                        self.assertIn('preparation_aborted=true signal=TERM stage=safari-pgrep', prep)
                        self.assertIn('partial query stderr', prep)
                    if query.startswith('error'): self.assertIn('pgrep query error', prep)
                    probes = [event for event in map(json.loads, calls.read_text().splitlines())
                              if event['tool'] == 'pgrep']
                    self.assertEqual(len(probes), 1)
                    self.assertEqual(probes[0]['argv'], ['-x', 'Safari'])
                    return
                expected_status = {'failure': 77, 'abort': 134, 'timeout': 124, 'term-save': 124, 'raw-empty': 15}.get(behavior, 0)
                if canary != 'delivered': expected_status = 12
                self.assertEqual(result.returncode, expected_status, result.stdout + result.stderr)
                self.assertFalse(observed_alive, f'Children alive when archive PUT started: {observed_alive}')
                with tarfile.open(uploads / f'{tag}.tgz') as bundle:
                    contents = {Path(m.name).name: bundle.extractfile(m).read()
                                for m in bundle.getmembers() if m.isfile()}
                summary = contents['spindump-summary.txt'].decode()
                self.assertIn('owned_children_reaped_utc=', summary)
                self.assertIn(f'watcher_exit={expected_status}', summary)
                self.assertEqual(contents['collector.sh'].decode(), script)
                self.assertEqual(contents['collector.argv'].decode().splitlines()[-1], mode)
                self.assertEqual(contents['safari-probe-source.sh'].decode(), SAFARI_SCRIPT.read_text())
                self.assertNotIn('stackshot-stream.fifo', contents)
                launch = (uploads / f'{tag}-launch.txt').read_text()
                if canary != 'delivered':
                    self.assertFalse(marker.exists())
                    self.assertIn('watcher_ready=false', summary)
                    self.assertIn('safari_open_skipped=true', launch)
                    self.assertNotIn('spindump_pid=', summary)
                    if canary == 'missing':
                        self.assertIn('canary_emit_exit=0 timeout=false', summary)
                        self.assertIn('Timestamp', contents['webkit-stream.log'].decode())
                    if canary == 'failure':
                        self.assertIn('canary_emit_exit=79 timeout=false', summary)
                    if canary == 'hang':
                        self.assertIn('canary_emit_exit=137 timeout=true', summary)
                    self.assertIn('emit stderr', contents['canary-emit.stderr.txt'].decode())
                    return
                self.assertIn('launcher_uid=1000', launch)
                self.assertIn('watcher_ready=true uid=0', launch)
                self.assertIn('safari_open_exit=0', launch)
                self.assertIn('method=unified-log-canary', launch)
                self.assertIn('canary_emit_exit=0 timeout=false', summary)
                nonce = next(line.split('=', 1)[1] for line in summary.splitlines()
                             if line.startswith('canary_nonce='))
                self.assertTrue(contents['canary-observed.txt'].decode().rstrip().endswith(nonce))
                self.assertEqual(contents['canary-emit.argv'].decode().splitlines(),
                                 [str(bindir / 'log'), 'emit', '--type', 'default', '--subsystem',
                                  'local.e05.gpuwatch', '--category', 'readiness', '--public', nonce])
                predicate = contents['log-stream.argv'].decode().splitlines()[-1]
                self.assertIn('subsystem BEGINSWITH "com.apple.WebKit"', predicate)
                self.assertIn(f'eventMessage == "{nonce}"', predicate)
                events = [json.loads(line) for line in calls.read_text().splitlines()]
                quits = [event for event in events if event['tool'] == 'osascript']
                probes = [event for event in events if event['tool'] == 'pgrep']
                self.assertEqual(len(quits), 1 if query == 'present-then-absent' else 0)
                self.assertEqual(len(probes), 2 if query == 'present-then-absent' else 1)
                spins = [event for event in events if event['tool'] == 'spindump']
                self.assertEqual(len(spins), 1, summary + launch + contents.get('webkit-stream.log', b'').decode())
                self.assertEqual(spins[0]['root'], '1')
                args = spins[0]['argv']
                selection = ['-onlyTarget', '-proc', '1542'] if mode == 'stackshot' else []
                ipc = ['-noIPC'] if mode in ('stackshot-system-noipc', 'stackshot-system-raw') else []
                raw_options = ['-noText', '-noSymbolicate'] if mode == 'stackshot-system-raw' else []
                report_name = 'spindump-raw.spindump' if mode == 'stackshot-system-raw' else 'spindump-report.txt'
                self.assertEqual(args[1:], ['10', '100'] + selection + ipc + raw_options +
                                 ['-sampleWithoutTarget', '-timelimit', '270' if mode == 'stackshot-system-raw' else '90',
                                  '-noProcessingWhileSampling', '-timeline', '-o',
                                  f'/tmp/{tag}/{report_name}'])
                self.assertIn('anchor_pid=' + args[0], summary)
                self.assertIn('collector_started_utc=', summary)
                self.assertIn('collector_finished_utc=', summary)
                self.assertEqual(contents['spindump.argv'].decode().splitlines(),
                                 [str(bindir / 'spindump')] + args)
                self.assertIn('raw stderr', contents['spindump.stderr.txt'].decode())
                self.assertIn(SAFARI_OWNER, contents['target-events.txt'].decode())
                self.assertIn(SAFARI_CONNECTION, contents['target-events.txt'].decode())
                assessment = contents['stackshot-assessment.txt'].decode()
                self.assertIn('report_completeness=unverified', assessment)
                if mode in ('stackshot-system-noipc', 'stackshot-system-raw'):
                    self.assertIn('collector_daemon_ipc=noIPC', summary)
                    self.assertIn('more expensive, but more reliable', summary)
                else:
                    self.assertIn('collector_daemon_ipc=spindump-default', summary)
                if mode == 'stackshot-system-raw':
                    self.assertIn('collection_scope=system-wide', summary)
                    self.assertIn('report_format=spindump-binary-only', summary)
                    self.assertIn('requested_duration_seconds=10 interval_ms=100 processing_timelimit_seconds=270 outer_sampler_seconds=300', summary)
                    self.assertIn('term_save_grace_seconds=' + ('2' if behavior == 'timeout' else '10'), summary)
                    self.assertIn('argv=/bin/sleep,372 ', summary)
                    self.assertIn('target_stack_acquired=unknown reason=raw-not-decoded', assessment)
                    self.assertNotIn('stack_frame_rows=', assessment)
                    self.assertNotIn('stackshot-task-index.txt', contents)
                    self.assertEqual(contents[report_name], b'' if behavior == 'raw-empty' else b'\x00RAW SPINDUMP FIXTURE\x01')
                    self.assertEqual('raw-sha256.txt' in contents, behavior != 'raw-empty')
                elif mode.startswith('stackshot-system'):
                    self.assertIn('collection_scope=system-wide process_selection=all-processes', summary)
                    index = contents['stackshot-task-index.txt'].decode()
                    self.assertIn('Process: FixtureServer [999]', index)
                    self.assertIn('Path: /fixture/service\nUID: 55', index)
                    self.assertIn('Thread 0x7986 1 samples (87)', index)
                    self.assertIn('Thread 0x7c7e 10 samples (89-98)', index)
                    self.assertIn('Process: com.apple.WebKit.GPU [1542]', index)
                    self.assertIn('stack_frame_rows=2', assessment)
                    self.assertIn('service_identity_and_wait_chain=unverified', assessment)
                else:
                    self.assertIn('collection_scope=target-and-anchor', summary)
                    self.assertNotIn('stackshot-task-index.txt', contents)
                if mode != 'stackshot-system-raw':
                    self.assertIn('processing_timelimit_seconds=90 outer_sampler_seconds=120 term_save_grace_seconds=1', summary)
                    self.assertIn('argv=/bin/sleep,162 ', summary)
                if mode == 'stackshot-system-raw':
                    self.assertIn('target_stack_acquired=unknown', assessment)
                elif behavior in ('empty', 'failure', 'abort'):
                    self.assertIn('target_stack_acquired=unknown', assessment)
                else:
                    self.assertIn('target_stack_acquired=true', assessment)
                    self.assertIn('BINARY REPRESENTATION FIXTURE RETAINED', contents['spindump-report.txt'].decode())
                if behavior == 'timeout':
                    self.assertIn('outer_timeout=true', summary)
                    self.assertIn('spindump_exit=137', summary)
                    if mode == 'stackshot-system-raw': self.assertIn('spindump_kill_sent=true', summary)
                elif behavior == 'term-save':
                    self.assertIn('spindump_exit=0 outer_timeout=true', summary)
                    if mode == 'stackshot-system-raw': self.assertIn('spindump_kill_sent=false', summary)
                elif behavior == 'raw-empty':
                    self.assertIn('spindump_exit=0', summary)
                    self.assertIn('raw_present=false', assessment)
                else:
                    self.assertIn('spindump_exit=' + str(expected_status), summary)
                # A repeated tag cannot quit or relaunch Safari or overwrite evidence.
                repeat = subprocess.run(['sh', str(SAFARI_SCRIPT), tag, '-', mode, '1'],
                                        env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(repeat.returncode, 2)
                self.assertIn('tag already used', repeat.stdout)
                root_gui = subprocess.run(['sh', str(SCRIPT), tag + '-rootgui', '1', 'https://example.test', mode],
                                          env=dict(env, FAKE_ROOT='1'), capture_output=True, text=True, timeout=5)
                self.assertEqual(root_gui.returncode, 2)
                unprivileged = subprocess.run(['sh', str(SCRIPT), tag + '-user', '1', '-', mode],
                                             env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(unprivileged.returncode, 2)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)
                if calls.exists():
                    for event in map(json.loads, calls.read_text().splitlines()):
                        try: os.kill(event['pid'], signal.SIGKILL)
                        except ProcessLookupError: pass
                for path in Path('/tmp').glob(tag + '*'):
                    if path.is_dir(): shutil.rmtree(path)
                    else: path.unlink(missing_ok=True)

    def test_user_launch_waits_for_root_readiness_and_late_sampler_reap_before_real_upload(self):
        self.run_chain()

    def test_system_scope_keeps_all_tasks_and_indexes_thread_ownership_after_collection(self):
        self.run_chain(mode='stackshot-system')

    def test_system_noipc_is_explicit_and_preserves_full_report_and_thread_index(self):
        self.run_chain(mode='stackshot-system-noipc')

    def test_raw_gpu_mode_preserves_binary_without_claiming_decoded_frames(self):
        self.run_chain(mode='stackshot-system-raw')

    def test_empty_raw_gpu_output_is_unknown_and_nonzero_even_if_sampler_exits_zero(self):
        self.run_chain('raw-empty', mode='stackshot-system-raw')

    def test_raw_gpu_timeout_reaps_after_bounded_save_grace(self):
        self.run_chain('timeout', mode='stackshot-system-raw')

    def test_raw_gpu_term_save_can_finish_without_kill_but_retains_outer_timeout(self):
        self.run_chain('term-save', mode='stackshot-system-raw')

    def test_prepare_query_errors_never_confirm_safari_absence(self):
        for query in ('error', 'error-one'):
            with self.subTest(query=query): self.run_chain(query=query)

    def test_prepare_query_timeout_reaps_and_uploads_unknown_without_launch(self):
        self.run_chain(query='hang')

    def test_prepare_abort_reaps_query_and_uploads_partial_evidence(self):
        self.run_chain(query='abort')

    def test_prepare_only_sends_quit_after_confirming_safari_present(self):
        self.run_chain(query='present-then-absent')

    def test_system_scope_term_saved_report_exit_zero_does_not_erase_outer_timeout(self):
        self.run_chain('term-save', mode='stackshot-system')

    def test_root_sampler_timeout_kills_and_reaps_before_real_upload(self):
        self.run_chain('timeout')

    def test_exit_zero_without_target_stacks_is_unknown(self):
        self.run_chain('empty')

    def test_spindump_permission_error_is_archived_with_actual_status(self):
        self.run_chain('failure')

    def test_spindump_sigabrt_is_archived_with_actual_status(self):
        self.run_chain('abort')

    def test_sudo_failure_prevents_safari_launch(self):
        self.run_chain(sudo_fail=True)

    def test_emit_zero_filtering_and_header_without_canary_event_do_not_open_safari(self):
        self.run_chain(canary='missing')

    def test_canary_emit_failure_is_archived_without_opening_safari(self):
        self.run_chain(canary='failure')

    def test_stuck_canary_emitter_is_reaped_before_archive_without_opening_safari(self):
        self.run_chain(canary='hang')


class SystemControlTests(unittest.TestCase):
    def exercise_control(self, timeout=False, mode='system-noipc', empty=False, wrong_pid=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); bindir = root / 'bin'; bindir.mkdir()
            spin = bindir / 'spindump'
            body = (f'#!{sys.executable}\nimport os,sys,time,signal\nfrom pathlib import Path\n'
                    'Path(sys.argv[sys.argv.index("-o")+1]).write_text("Process: fixture [1]\\nRAW BINARY FIXTURE\\n")\n')
            body += 'signal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n' if timeout else 'time.sleep(.2)\n'
            spin.write_text(body); spin.chmod(0o755)
            (bindir / 'id').write_text('#!/bin/sh\necho 0\n'); (bindir / 'id').chmod(0o755)
            script = CONTROL_SCRIPT.read_text().replace('/usr/sbin/spindump', str(spin))
            if timeout:
                script = script.replace('outer_seconds=120', 'outer_seconds=1').replace('outer_seconds=300', 'outer_seconds=1').replace('save_grace=10', 'save_grace=1')
            control = root / 'control.sh'; control.write_text(script)
            uploads = root / 'uploads'; alive_at_upload = []
            tag = f'offline-control-{mode}-{int(timeout)}-{int(empty)}-{os.getpid()}'
            source_tag = tag + '-source'
            source_dir = Path('/tmp') / source_tag
            payload = b'' if empty else b'RAW BINARY INPUT\x00'
            export_buffer = None
            if mode in ('reprocess', 'export-stuck'):
                source_dir.mkdir()
                (source_dir / 'collector.sh').write_text(script)
                (source_dir / 'spindump-raw.spindump').write_bytes(payload)
                (source_dir / 'spindump.argv').write_text('/usr/sbin/spindump\n-o\n' + str(source_dir / 'spindump-raw.spindump') + '\n')
            if mode == 'export-stuck':
                export_buffer = Path('/var/tmp') / f'spindump-stuck-stackshot.offline-{os.getpid()}.{os.getpid()}.buf'
                export_buffer.write_bytes(payload)
                (source_dir / 'summary.txt').write_text(f'spindump_pid={os.getpid() + int(wrong_pid)}\n')
                (source_dir / 'spindump.stderr.txt').write_text(f'spindump-stuck: Wrote stackshot to {export_buffer}\n')
            Handler = make_handler(root, uploads, 32 << 20)
            class CheckedHandler(Handler):
                def do_PUT(self):
                    import re
                    summary = (Path('/tmp') / tag / 'summary.txt').read_text()
                    for pid in re.findall(r'(?:long_pid|spindump_pid)=(\d+)', summary):
                        try: os.kill(int(pid), 0)
                        except ProcessLookupError: pass
                        else: alive_at_upload.append(int(pid))
                    super().do_PUT()
                def log_message(self, *_): pass
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), CheckedHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            env = dict(os.environ, PATH=f'{bindir}:/usr/bin:/bin', H=f'http://127.0.0.1:{server.server_port}')
            try:
                result = subprocess.run(['sh', str(control), tag, mode, source_tag], env=env,
                                        capture_output=True, text=True, timeout=12)
                self.assertEqual(result.returncode, 2 if wrong_pid else (15 if empty else (124 if timeout else 0)), result.stderr)
                if wrong_pid:
                    self.assertFalse((uploads / f'{tag}.tgz').exists())
                    self.assertEqual(export_buffer.read_bytes(), payload)
                    return
                self.assertFalse(alive_at_upload)
                with tarfile.open(uploads / f'{tag}.tgz') as bundle:
                    summary = bundle.extractfile(f'{tag}/summary.txt').read().decode()
                    if mode == 'export-stuck':
                        self.assertEqual(bundle.extractfile(f'{tag}/owned-stuck-stackshot.buf').read(), payload)
                        self.assertEqual(export_buffer.read_bytes(), payload)
                        self.assertIn('owned processor diagnostic snapshot; NOT original sampling window', summary)
                        self.assertNotIn(f'{tag}/spindump.argv', bundle.getnames())
                        return
                    if empty:
                        self.assertNotIn(f'{tag}/spindump.argv', bundle.getnames())
                        self.assertIn('input_present=false', summary)
                        return
                    argv = bundle.extractfile(f'{tag}/spindump.argv').read().decode().splitlines()
                    report_name = 'spindump-raw.spindump' if mode == 'system-raw' else 'report.txt'
                    report = bundle.extractfile(f'{tag}/{report_name}').read().decode()
                    if mode == 'reprocess':
                        self.assertEqual(bundle.extractfile(f'{tag}/input.spindump').read(), payload)
                        self.assertEqual((source_dir / 'spindump-raw.spindump').read_bytes(), payload)
                        self.assertIn(str(source_dir / 'spindump-raw.spindump'), bundle.extractfile(f'{tag}/input-reference.txt').read().decode())
                if mode == 'reprocess':
                    self.assertEqual(argv[1:], ['-i', f'/tmp/{tag}/input.spindump', '-noIPC', '-timeline', '-timelimit', '270', '-o', f'/tmp/{tag}/report.txt'])
                    self.assertIn('scope=reprocess-only existing capture input; no new live samples', summary)
                    self.assertNotIn('long_pid=', summary)
                    if not timeout:
                        self.assertIn('outer_seconds=300 term_save_grace_seconds=10', summary)
                else:
                    raw_flags = ['-noText', '-noSymbolicate'] if mode == 'system-raw' else []
                    self.assertEqual(argv[2:], ['3' if mode == 'system-raw' else '10', '100', '-sampleWithoutTarget', '-noIPC'] + raw_flags + ['-timelimit', '90', '-noProcessingWhileSampling', '-timeline', '-o', f'/tmp/{tag}/{report_name}'])
                    self.assertIn('collection_scope=system-wide diagnostic-intervention', summary)
                    self.assertIn('independent of Safari quit, pgrep and ownership events', summary)
                self.assertIn('stack_acquired=unverified report_completeness=unverified', summary)
                self.assertIn('owned_children_reaped_utc=', summary)
                self.assertIn('outer_timeout=' + str(timeout).lower(), summary)
                self.assertIn('RAW BINARY FIXTURE', report)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)
                shutil.rmtree(Path('/tmp') / tag, ignore_errors=True)
                shutil.rmtree(source_dir, ignore_errors=True)
                if export_buffer: export_buffer.unlink(missing_ok=True)
                (Path('/tmp') / f'{tag}.tgz').unlink(missing_ok=True)

    def test_three_second_raw_control_retains_binary_format_metadata(self):
        self.exercise_control(mode='system-raw')

    def test_export_stuck_only_copies_the_logged_owned_pid_path(self):
        self.exercise_control(mode='export-stuck')

    def test_export_stuck_rejects_a_mismatched_source_pid(self):
        self.exercise_control(mode='export-stuck', wrong_pid=True)

    def test_reprocess_only_reads_retained_input_and_has_independent_bounds(self):
        self.exercise_control(mode='reprocess')

    def test_reprocess_timeout_preserves_input_and_reaps_before_upload(self):
        self.exercise_control(mode='reprocess', timeout=True)

    def test_empty_raw_input_never_starts_reprocess_or_claims_success(self):
        self.exercise_control(mode='reprocess', empty=True)

    def test_system_control_uses_all_processes_without_safari_or_query_prerequisite(self):
        self.exercise_control()

    def test_system_control_timeout_reaps_before_real_archive_upload(self):
        self.exercise_control(timeout=True)


if __name__ == '__main__':
    unittest.main()
