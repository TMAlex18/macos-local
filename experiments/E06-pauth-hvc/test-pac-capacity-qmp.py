#!/usr/bin/env python3
"""Actual diskless TCG/QOM capacity and reset checks on one supplied binary.

Only a self-authored PACGA guest executes. No Apple firmware, retained storage,
GPU, audio device, raw PAC inputs or keys are read by the host observer.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'experiments/E05-usbip-kernel'))
from firmware_session import FirmwareSession


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_case(binary, guest, directory, entries, stats, wide=False):
    fw = FirmwareSession(directory, vcpus=2, pauth_memo=True,
                         pauth_memo_entries=entries, pauth_memo_stats=stats)
    fw.temp = tempfile.TemporaryDirectory(prefix='pac-capacity-')
    endpoint = Path(fw.temp.name) / 'qmp'
    uart = directory / 'uart.log'
    row = {'entries_requested': entries, 'effective_entries': entries or 64, 'statistics': stats,
           'pattern':'high_slot_stride' if wide else 'aligned_stride','passed': False}
    try:
        fw.args = [str(binary), '-machine', 'virt', '-accel', 'tcg,thread=multi',
                   '-cpu', fw.cpu_model, '-smp', '2', '-m', '128M', '-nodefaults',
                   '-nic', 'none', '-display', 'none', '-monitor', 'none', '-S',
                   '-serial', f'file:{uart}', '-kernel', str(guest),
                   '-qmp', f'unix:{endpoint},server=on,wait=off']
        row['command'] = fw.args
        fw.err = (directory / 'stderr.log').open('wb')
        fw.proc = subprocess.Popen(fw.args, stdout=subprocess.DEVNULL, stderr=fw.err,
                                   start_new_session=True,
                                   preexec_fn=fw._child_parent_death_signal)
        row['qemu_pid'] = fw.proc.pid
        deadline = time.monotonic() + 10
        while not endpoint.exists():
            if fw.proc.poll() is not None:
                raise RuntimeError('Candidate exited before QMP was ready')
            if time.monotonic() >= deadline:
                raise TimeoutError('Candidate QMP readiness')
            time.sleep(.02)
        fw.qmp = socket.socket(socket.AF_UNIX)
        fw.qmp.settimeout(5)
        fw.qmp.connect(str(endpoint))
        fw.qf = fw.qmp.makefile('rb')
        json.loads(fw.qf.readline())
        fw.request('qmp_capabilities')
        cpus = fw.request('query-cpus-fast')
        paths = {cpu['cpu-index']: cpu['qom-path'] for cpu in cpus}
        assert set(paths) == {0, 1}
        for path in paths.values():
            assert fw.request('qom-get', {'path': path, 'property': 'x-pauth-memo-entries'}) == (entries or 64)
            assert fw.request('qom-get', {'path': path, 'property': 'x-pauth-memo-stats'}) is stats
            for name in ('hits', 'misses'):
                prop = 'x-pauth-memo-' + name
                assert fw.request('qom-get', {'path': path, 'property': prop}) == 0
                try:
                    fw.request('qom-set', {'path': path, 'property': prop, 'value': 1})
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('Statistics property unexpectedly accepts writes')
        row['counter_properties_read_only'] = True
        row['generations'] = []
        for generation in range(2):
            fw.request('cont')
            deadline = time.monotonic() + 10
            while not uart.exists() or uart.read_text().count('MEMO CAPACITY DONE') < generation + 1:
                if fw.proc.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Self-authored PACGA guest did not finish')
                time.sleep(.01)
            fw.request('stop')
            observed = fw.capture_pauth_memo_statistics() if stats else {
                'requested': False, 'vcpus': [dict(cpu_index=i, **{
                    name: fw.request('qom-get', {'path': path, 'property': 'x-pauth-memo-' + name})
                    for name in ('hits', 'misses')}) for i, path in sorted(paths.items())]}
            reused=(entries or 64)>=(16384 if wide else 1024)
            expected = {'hits': 16 if reused else 0,
                        'misses': 16 if reused else 32} if stats else {'hits': 0, 'misses': 0}
            assert observed['vcpus'] == [{'cpu_index': 0, **expected}, {'cpu_index': 1, 'hits': 0, 'misses': 0}]
            row['generations'].append(observed)
            fw.request('system_reset')
            for path in paths.values():
                for name in ('hits', 'misses'):
                    assert fw.request('qom-get', {'path': path, 'property': 'x-pauth-memo-' + name}) == 0
        row['reset_cleared_counters_and_recomputed_misses'] = True
        row['passed'] = True
    finally:
        fw.close()
        row.update(qemu_exit_code=fw.proc.returncode if fw.proc else None,
                   qemu_reaped=bool(fw.proc and fw.proc.poll() is not None))
        (directory / 'results.json').write_text(json.dumps(row, indent=2) + '\n')
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    binary, out = args.qemu.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = HERE / 'bench-pac-capacity.S'
    obj, guest = out / 'guest.o', out / 'guest.elf'
    wide_obj,wide_guest=out/'guest-wide.o',out/'guest-wide.elf'
    commands = [['clang', '--target=aarch64-none-elf', '-march=armv8.3-a', '-c', str(source), '-o', str(obj)],
                ['ld.lld', '--build-id=none', '-Ttext=0x40080000', '-e', '_start', str(obj), '-o', str(guest)],
                ['clang','--target=aarch64-none-elf','-march=armv8.3-a','-DWIDE_REUSE','-c',str(source),'-o',str(wide_obj)],
                ['ld.lld','--build-id=none','-Ttext=0x40080000','-e','_start',str(wide_obj),'-o',str(wide_guest)]]
    report = {'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'binary': str(binary), 'binary_sha256': sha(binary),
              'source': str(source), 'source_sha256': sha(source), 'compile_commands': commands,
              'scope': __doc__, 'cases': [], 'passed': False}
    try:
        for cmd in commands:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        for entries, stats in ((None, False), (64, True), (1024, True), (16384, True)):
            report['cases'].append(run_case(binary, guest, out / f'{entries or "default"}-stats-{int(stats)}', entries, stats))
        for entries in (1024,16384):
            report['cases'].append(run_case(binary,wide_guest,out/f'{entries}-wide-stats',entries,True,wide=True))
        report['invalid_profiles'] = []
        for cpu, expected in (
                ('max,x-pauth-memo=on,x-pauth-memo-entries=128', 'x-pauth-memo-entries must be 64, 1024 or 16384'),
                ('max,x-pauth-memo=on,x-pauth-memo-entries=32768', 'x-pauth-memo-entries must be 64, 1024 or 16384'),
                ('max,x-pauth-memo-entries=1024', 'Memo capacity/statistics require x-pauth-memo=on'),
                ('max,x-pauth-memo-entries=16384', 'Memo capacity/statistics require x-pauth-memo=on'),
                ('max,x-pauth-memo-stats=on', 'Memo capacity/statistics require x-pauth-memo=on')):
            cmd = [str(binary), '-machine', 'virt', '-accel', 'tcg', '-cpu', cpu,
                   '-nodefaults', '-display', 'none', '-monitor', 'none', '-serial', 'none', '-S']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            assert result.returncode != 0 and expected in result.stderr
            report['invalid_profiles'].append({'cpu': cpu, 'returncode': result.returncode,
                                               'expected_error': expected, 'rejected': True})
        report['passed'] = all(row['passed'] and row['qemu_reaped'] and row['qemu_exit_code'] == 0 for row in report['cases'])
        assert report['passed'] and sha(binary) == report['binary_sha256']
    finally:
        obj.unlink(missing_ok=True)
        wide_obj.unlink(missing_ok=True)
        (out / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': report['passed'], 'cases': len(report['cases']), 'report': str(out / 'results.json')}))


if __name__ == '__main__':
    main()
