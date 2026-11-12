#!/usr/bin/env python3
"""Offline tests for host_thread_sampler.py, clock_offset.py and gpu_init_cpu_profile.py."""
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import clock_offset
import gpu_init_cpu_profile as profile
import host_thread_sampler as sampler

STAT_TAIL = ' '.join(['S'] + ['0'] * 35)   # fields 3..38 of /proc/PID/stat


class FakeProc:
    """A /proc tree with one process whose thread files the test rewrites."""

    def __init__(self, root, pid=4242):
        self.task = Path(root) / str(pid) / 'task'
        self.pid = pid

    def thread(self, tid, comm, run_ns, wait_ns, cpu=0):
        directory = self.task / str(tid)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'comm').write_text(comm + '\n')
        (directory / 'schedstat').write_text(f'{run_ns} {wait_ns} 7\n')
        (directory / 'stat').write_text(f'{tid} ({comm}) {STAT_TAIL} {cpu} 0 0\n')


class SamplerTests(unittest.TestCase):
    def test_only_changed_threads_are_written_and_names_with_brackets_parse(self):
        with tempfile.TemporaryDirectory() as root:
            proc, lines = FakeProc(root), []
            proc.thread(4242, 'qemu-system-aar', 1_000_000, 0, cpu=3)
            proc.thread(4300, 'CPU 0/TCG', 5_000_000, 2_000_000, cpu=11)
            proc.thread(4301, 'odd) (name', 0, 0, cpu=19)
            sample = sampler.Sampler(proc.pid, lines.append, root)
            self.assertTrue(sample.scan())
            sample.tick(1_000_000_000)
            proc.thread(4300, 'CPU 0/TCG', 14_000_000, 2_500_000, cpu=12)
            sample.tick(1_010_000_000)
            sample.tick(1_020_000_000)
            (proc.task / '4301' / 'schedstat').write_text('')
            proc.thread(4302, 'worker', 1, 1)
            self.assertTrue(sample.scan())
            sample.tick(1_030_000_000)
            sample.close()
        self.assertEqual(sorted(lines[:3]), ['T\t4242\tqemu-system-aar\n', 'T\t4300\tCPU 0/TCG\n', 'T\t4301\todd) (name\n'])
        self.assertEqual(lines[3], 'K\t0\n')
        self.assertEqual(sorted(lines[4:7]), ['S\t4242\t1000\t0\t3\n', 'S\t4300\t5000\t2000\t11\n', 'S\t4301\t0\t0\t19\n'])
        self.assertEqual(lines[7:10], ['K\t10000\n', 'S\t4300\t14000\t2500\t12\n', 'K\t20000\n'])
        self.assertEqual(lines[10:], ['T\t4302\tworker\n', 'K\t30000\n', 'X\t4301\n', 'S\t4302\t0\t0\t0\n'])

    def test_a_missing_process_ends_the_scan(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(sampler.Sampler(1, lambda _line: None, root).scan())

    def test_live_busy_and_idle_processes(self):
        busy = subprocess.Popen([sys.executable, '-c', 'while True: pass'])
        idle = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            time.sleep(0.3)   # let both finish starting
            with tempfile.TemporaryDirectory() as directory:
                reports = {}
                for name, child in (('busy', busy), ('idle', idle)):
                    out = Path(directory) / f'{name}.tsv'
                    ticks = sampler.record(child.pid, out, hz=200, max_seconds=0.5)
                    samples = profile.load_samples(out)
                    reports[name] = profile.describe(samples, samples['ticks'][1], samples['ticks'][-1], [child.pid])
                    self.assertGreater(ticks, 20)
                    with self.assertRaises(FileExistsError):
                        sampler.record(child.pid, out, max_seconds=0.01)
        finally:
            for child in (busy, idle):
                child.kill()
                child.wait(timeout=5)
        self.assertGreater(reports['busy']['vcpu_mean_busy'], 0.6)
        self.assertLess(reports['busy']['vcpu_mean_busy'], 1.2)
        self.assertLess(reports['idle']['vcpu_mean_busy'], 0.1)
        self.assertEqual(sampler.pid_of('no-such-comm-here'), None)

    def test_cli_refuses_a_process_that_never_appears(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()) as stderr:
            code = sampler.main(['--wait-for', 'no-such-comm-here', '--wait-seconds', '0.1', '--out', f'{directory}/x.tsv'])
        self.assertEqual(code, 2)
        self.assertIn('HOST_THREAD_SAMPLER', stderr.getvalue())


class ClockTests(unittest.TestCase):
    def test_script_and_lines_give_the_clock_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'clock.jsonl'
            servers = clock_offset.Servers(out, port=0, line_port=0)
            try:
                script_port, line_port = servers.script.server_address[1], servers.lines.server_address[1]
                script = urllib.request.urlopen(f'http://127.0.0.1:{script_port}/clock.zsh', timeout=5).read().decode()
                self.assertIn(f'ztcp 10.0.2.2 {line_port} ', script)
                self.assertIn('$EPOCHREALTIME', script)
                perl = urllib.request.urlopen(f'http://127.0.0.1:{script_port}/clock.pl', timeout=5).read().decode()
                self.assertIn(f'PeerPort => {line_port},', perl)
                self.assertIn('printf $s "%s %d %.6f\\n", $tag, $i, time;', perl)
                with self.assertRaises(urllib.error.HTTPError) as refused:
                    urllib.request.urlopen(f'http://127.0.0.1:{script_port}/other', timeout=5)
                self.assertEqual(refused.exception.code, 404)
                refused.exception.close()
                with socket.create_connection(('127.0.0.1', line_port), timeout=5) as guest:
                    for index in range(1, 6):
                        guest_clock = time.time() - 5.25          # a guest clock 5.25 s behind the host
                        guest.sendall(f'probe-a {index} {guest_clock:.6f}\n'.encode())
                        time.sleep(0.02)
                    guest.sendall(b'not a stamp\nprobe-b 1 1794000000.5\n')
                deadline = time.monotonic() + 5
                while len(out.read_text().splitlines()) < 7 and time.monotonic() < deadline:
                    time.sleep(0.02)
            finally:
                servers.close()
            rows = clock_offset.read_rows(out)
            result = clock_offset.estimate(rows)
            self.assertEqual(len(rows), 7)
            self.assertEqual(result['probe-a']['samples'], 5)
            self.assertAlmostEqual(result['probe-a']['host_minus_guest_seconds'], 5.25, delta=0.05)
            self.assertLess(result['probe-a']['spread_seconds'], 0.2)
            self.assertEqual(result['probe-b']['samples'], 1)
            self.assertEqual(set(result), {'probe-a', 'probe-b'})
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(clock_offset.main(['estimate', str(out)]), 0)
            self.assertEqual(json.loads(stdout.getvalue()), result)
            with self.assertRaises(FileExistsError):
                clock_offset.Recorder(out)

    def test_stamp_arithmetic_is_exact(self):
        rows = [{'recv_realtime_ns': 1_794_000_010_000_000_000, 'line': 'tag 1 1794000000.000001'},
                {'recv_realtime_ns': 1_794_000_010_500_000_000, 'line': 'tag 2 1794000000.2500000009'},
                {'recv_realtime_ns': 1, 'line': 'tag x 1794000000.25'}, {'recv_realtime_ns': 1}]
        self.assertEqual(clock_offset.estimate(rows), {'tag': {
            'samples': 2, 'host_minus_guest_seconds': 9.999999, 'median_seconds': 10.125, 'spread_seconds': 0.250001}})


def synthetic_samples(path, start_ns):
    """Two seconds at 100 Hz: vCPU 0 fully busy from 0.5 to 1.0 s on an E core, vCPU 1 half busy
    throughout on a P core, the main thread a tenth, and one worker busy in the first half second."""
    lines = [f'H\tpid\t100\nH\tstart_realtime_ns\t{start_ns}\nH\thz\t100\n',
             'T\t100\tqemu-system-aar\n', 'T\t200\tCPU 0/TCG\n', 'T\t201\tCPU 1/TCG\n', 'T\t300\tworker\n']
    totals = {100: 0, 200: 0, 201: 0, 300: 0}
    for tick in range(201):
        lines.append(f'K\t{tick * 10000}\n')
        end = tick * 10000
        gains = {100: 1000, 201: 5000, 200: 10000 if 500000 < end <= 1000000 else 0, 300: 10000 if end <= 500000 else 0}
        for tid, gain in gains.items():
            if tick == 0 or gain:
                totals[tid] += gain if tick else 0
                cpu = {100: 1, 200: 12, 201: 3, 300: 9}[tid]
                lines.append(f'S\t{tid}\t{totals[tid]}\t{totals[tid] // 10}\t{cpu}\n')
    Path(path).write_text(''.join(lines))


def synthetic_timing(clock):
    """Guest log times in the guest's default -8 h zone for a 0.5..1.0 s window."""
    names = (('constructor', 0.1), ('ui_request', 0.5), ('gpu_entry', 0.51), ('registrar_activation', 0.7),
             ('helper_create', 0.75), ('helper_destroy', 0.9), ('did_initialize', 1.0))
    return {'outcome': 'initialized', 'first_gpu': {'events': {
        name: {'time': clock(offset), 'line': 1} for name, offset in names}}}


class ProfileTests(unittest.TestCase):
    START_NS = 1_794_000_000_000_000_000
    GUEST_BEHIND = 2.5   # host minus guest

    def guest_time(self, offset):
        from datetime import datetime, timedelta, timezone
        moment = datetime.fromtimestamp(self.START_NS / 1e9 + offset - self.GUEST_BEHIND, timezone(timedelta(hours=-8)))
        return moment.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

    def test_window_and_segments_use_the_clock_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'threads.tsv'
            synthetic_samples(path, self.START_NS)
            samples = profile.load_samples(path)
        results = {'execution': {'qemu_vcpu_threads': {'vcpus': [{'cpu_index': 1, 'host_thread_id': 201},
                                                                 {'cpu_index': 0, 'host_thread_id': 200}]}}}
        report = profile.profile(synthetic_timing(self.guest_time), samples, self.GUEST_BEHIND, -8, results, {0, 1, 2, 3})
        window = report['intervals']['window']
        self.assertEqual(report['vcpu_host_thread_ids'], [200, 201])
        self.assertEqual(window['wall_seconds'], 0.5)
        self.assertAlmostEqual(window['vcpu_mean_busy'], 1.5, delta=0.02)
        self.assertAlmostEqual(window['per_vcpu_on_cpu_seconds'][0], 0.5, delta=0.01)
        self.assertAlmostEqual(window['per_vcpu_on_cpu_seconds'][1], 0.25, delta=0.01)
        self.assertAlmostEqual(window['vcpu_e_core_share'], 2 / 3, delta=0.01)
        self.assertAlmostEqual(window['vcpu_runqueue_wait_seconds'], 0.075, delta=0.005)
        self.assertAlmostEqual(window['main_thread_on_cpu_seconds'], 0.05, delta=0.005)
        self.assertEqual(window['other_threads'], [])
        # vCPU 0 fills every tick, vCPU 1 exactly half of each: two threads count as busy.
        self.assertEqual(window['vcpu_busy_count_share'], [0.0, 0.0, 1.0])
        self.assertEqual(window['longest_tick_ms'], 10.0)
        launch = report['intervals']['gpu_constructor_to_request']
        self.assertAlmostEqual(launch['vcpu_mean_busy'], 0.5, delta=0.02)
        self.assertEqual(launch['vcpu_e_core_share'], 0.0)
        self.assertEqual(launch['other_threads'][0]['comm'], 'worker')
        self.assertAlmostEqual(launch['other_threads'][0]['on_cpu_seconds'], 0.4, delta=0.01)
        self.assertAlmostEqual(report['intervals']['helper_create_to_destroy']['wall_seconds'], 0.15, delta=0.002)
        # The sampler starts 0.5 s before the request and ends 1 s after the end: neither neighbour fits.
        self.assertNotIn('10s_before_request', report['intervals'])
        self.assertNotIn('10s_after_did_initialize', report['intervals'])
        self.assertTrue(report['window_start_host_utc'].startswith('2026-11-'))
        # A wrong clock difference moves the window off vCPU 0's busy half second.
        shifted = profile.profile(synthetic_timing(self.guest_time), samples, self.GUEST_BEHIND + 0.5, -8, results, None)
        self.assertAlmostEqual(shifted['intervals']['window']['vcpu_mean_busy'], 0.5, delta=0.03)
        self.assertIsNone(shifted['intervals']['window']['vcpu_e_core_share'])

    def test_vcpus_fall_back_to_thread_names_and_cli_reports_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            synthetic_samples(directory / 'threads.tsv', self.START_NS)
            samples = profile.load_samples(directory / 'threads.tsv')
            self.assertEqual(profile.vcpu_threads(samples), [200, 201])
            self.assertEqual(profile.vcpu_threads(samples, {'execution': {'qemu_vcpu_threads': {'vcpus': [
                {'cpu_index': 0, 'host_thread_id': 999}]}}}), [200, 201])
            (directory / 'timing.json').write_text(json.dumps(synthetic_timing(self.guest_time)))
            (directory / 'clock.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in (
                {'recv_realtime_ns': self.START_NS + 100, 'line': f'a 1 {self.START_NS // 10**9 - 3}.5'},
                {'recv_realtime_ns': self.START_NS + 100, 'line': f'b 1 {self.START_NS // 10**9 - 9}.0'})))
            base = ['--timing', str(directory / 'timing.json'), '--samples', str(directory / 'threads.tsv'), '--p-cores', '0-3,7']
            with redirect_stdout(io.StringIO()) as stdout:
                code = profile.main(base + ['--clock', str(directory / 'clock.jsonl'), '--clock-tag', 'a',
                                            '--out', str(directory / 'report.json')])
            self.assertEqual(code, 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report, json.loads((directory / 'report.json').read_text()))
            self.assertEqual(report['clock']['tag'], 'a')
            self.assertAlmostEqual(report['host_minus_guest_seconds'], 2.5, places=5)
            self.assertEqual(report['p_cores'], [0, 1, 2, 3, 7])
            self.assertEqual(set(report['inputs']), {'timing', 'samples', 'clock'})
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(profile.main(base + ['--clock', str(directory / 'clock.jsonl')]), 2)
                self.assertEqual(profile.main(base + ['--host-minus-guest', '2.5', '--out', str(directory / 'report.json')]), 2)
                self.assertEqual(profile.main(base + ['--host-minus-guest', '500']), 2)
            self.assertEqual(stderr.getvalue().count('GPU_INIT_CPU_PROFILE'), 3)
            (directory / 'short.tsv').write_text('H\tpid\t1\nK\t0\n')
            with self.assertRaises(profile.ProfileError):
                profile.load_samples(directory / 'short.tsv')
        self.assertEqual(profile.parse_cpu_list('0-2,5\n'), {0, 1, 2, 5})


if __name__ == '__main__':
    unittest.main()
