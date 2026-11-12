#!/usr/bin/env python3
"""One diskless eight-vCPU guest using FirmwareSession's actual affinity hook.

No QEMU build, Apple code, disks, GPU, host policy changes, or timing claim.
The independently known guest results are 4096 * (CPU index + 1).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'experiments/E05-usbip-kernel'))
from firmware_session import FirmwareSession, parse_vcpu_affinity


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True, type=Path)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--vcpu-affinity', required=True)
    args = parser.parse_args()
    selected = parse_vcpu_affinity(args.vcpu_affinity)
    binary, out = args.qemu.resolve(), args.output.resolve()
    assert sha(binary) == args.expected_sha256
    for path in Path('/proc').glob('[0-9]*/exe'):
        try:
            target = os.readlink(path)
        except OSError:
            continue
        if target in (str(binary), str(binary) + ' (deleted)'):
            raise RuntimeError('The supplied QEMU binary already has a live owner')
    out.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).with_name('vcpu-affinity.S')
    obj, guest = out / 'guest.o', out / 'guest.elf'
    commands = [['clang', '--target=aarch64-none-elf', '-march=armv8-a', '-c', str(source), '-o', str(obj)],
                ['ld.lld', '--build-id=none', '-Ttext=0x40080000', '-e', '_start', str(obj), '-o', str(guest)]]
    record = {'scope': __doc__, 'binary': str(binary), 'binary_sha256': args.expected_sha256,
              'source_sha256': sha(source), 'compile_commands': commands, 'passed': False}
    fw = None
    parent_before = os.sched_getaffinity(0)
    try:
        for command in commands:
            subprocess.run(command, capture_output=True, text=True, check=True)
        record['guest_sha256'] = sha(guest)
        fw = FirmwareSession(out, vcpus=8, tcg_thread='multi', vcpu_affinity=args.vcpu_affinity)
        fw.temp = tempfile.TemporaryDirectory(prefix='vcpu-affinity-')
        endpoint = Path(fw.temp.name) / 'qmp'
        uart = out / 'guest-uart.log'
        fw.args = [str(binary), '-machine', 'virt', '-cpu', 'max', '-accel', 'tcg,thread=multi',
                   '-smp', '8', '-m', '128M', '-nodefaults', '-nic', 'none', '-display', 'none',
                   '-monitor', 'none', '-serial', f'file:{uart}', '-kernel', str(guest), '-S',
                   '-qmp', f'unix:{endpoint},server=on,wait=off']
        record['command'] = fw.args
        fw.err = (out / 'qemu-stderr.log').open('wb')
        fw.proc = subprocess.Popen(fw.args, stdout=subprocess.DEVNULL, stderr=fw.err,
                                   start_new_session=True, preexec_fn=fw._child_parent_death_signal)
        record['pid'] = fw.proc.pid
        deadline = time.monotonic() + 10
        while not endpoint.exists():
            if fw.proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Diskless QEMU did not reach startup QMP')
            time.sleep(.01)
        fw.qmp = socket.socket(socket.AF_UNIX)
        fw.qmp.settimeout(5)
        fw.qmp.connect(str(endpoint))
        fw.qf = fw.qmp.makefile('rb')
        json.loads(fw.qf.readline())
        fw.request('qmp_capabilities')
        fw.capture_vcpu_threads()
        observation = fw.apply_vcpu_affinity()
        record['execution'] = fw.execution_metadata()
        assert observation['verified'] and len(observation['vcpus']) == 8
        for cpu in observation['vcpus']:
            assert sorted(os.sched_getaffinity(cpu['host_thread_id'])) == selected
        assert observation['non_vcpu_masks_unchanged'] and observation['controller_masks_unchanged']
        fw.request('cont')
        fw.proc.wait(timeout=10)
        output = uart.read_text()
        assert re.fullmatch(r'(?:[0-9a-f]{16}\n){8}', output), output
        actual = [int(line, 16) for line in output.splitlines()]
        expected = [4096 * (cpu + 1) for cpu in range(8)]
        assert actual == expected and fw.proc.returncode == 0
        record.update(actual_guest_results=actual, expected_guest_results=expected,
                      binary_unchanged=sha(binary) == args.expected_sha256,
                      controller_mask_unchanged_after_guest=os.sched_getaffinity(0) == parent_before)
        assert record['binary_unchanged'] and record['controller_mask_unchanged_after_guest']
        record['passed'] = True
    except Exception as error:
        record['error'] = repr(error)
        raise
    finally:
        if fw is not None:
            fw.close()
            record.update(qemu_exit_code=fw.proc.returncode if fw.proc else None,
                          qemu_reaped=bool(fw.proc and fw.proc.poll() is not None))
        obj.unlink(missing_ok=True)
        (out / 'results.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'passed': record['passed'], 'result': str(out / 'results.json')}))


if __name__ == '__main__':
    main()
