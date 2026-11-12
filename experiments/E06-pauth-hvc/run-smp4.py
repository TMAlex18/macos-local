#!/usr/bin/env python3
"""Parameterized 2/4/8-CPU PSCI/PAC/HVC/EL0 acceptance (historical script name).

No Apple disks or canonical rebuild. VMApple necessarily initializes its
headless Reims/Vulkan device; these guests submit no graphics commands.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUNTIME = ROOT / 'experiments/E06-platform-contracts'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def cpu_records(output, count):
    records, fields = [], {}
    for line in output.splitlines():
        match = re.fullmatch(r'([A-Z0-9_]+)=0x([0-9a-f]+)', line)
        if match:
            fields[match[1]] = int(match[2], 16)
        elif line == 'CPU_GENERATION_PASS':
            records.append(fields)
            fields = {}
    expected = {(cpu, generation) for cpu in range(count) for generation in (1, 2)}
    if len(records) != count * 2 or {(r.get('CPU'), r.get('GENERATION')) for r in records} != expected:
        raise RuntimeError('Missing actual execution records for one or more CPU/generation pairs')
    for r in records:
        cpu, generation = r['CPU'], r['GENERATION']
        if r['SVC_COUNT'] != generation or r['EL0_HVC_DENIED'] != generation:
            raise RuntimeError('A CPU did not execute its own EL0/SVC and HVC rejection')
        if r['PREINIT_CHECKS'] != (generation if cpu else 1):
            raise RuntimeError('Missing per-CPU initialization lifecycle check')
        if r['CPU_ON_SUCCESSES'] != (generation if cpu else 0) or r['CPU_OFF_CONFIRMED'] != (generation if cpu else 0):
            raise RuntimeError('Missing per-CPU PSCI lifecycle observations')
        top = 0x70d00000 - cpu * 0x10000 if cpu else 0x71000000
        if not (top - 0x10000 <= r['EXCEPTION_FRAME'] < r['EL1_SP'] <= top):
            raise RuntimeError('Invalid per-CPU EL1 stack/exception record')
        if r['EL0_SP'] != 0x70e00000 - cpu * 0x10000 - 16:
            raise RuntimeError('Invalid per-CPU actual EL0 stack access')
        if r['MPIDR'] & 0xff00ffffff != cpu:
            raise RuntimeError('Guest CPU identity differs from its output slot')
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--vcpus', type=int, choices=(2, 4, 8), nargs='+', default=[2, 4])
    parser.add_argument('--qemu', type=Path, default=ROOT / 'build/qemu-bdif-usb/qemu-system-aarch64')
    parser.add_argument('--expected-sha256', help='Override the current build manifest hash for a retained comparator')
    parser.add_argument('--pauth-memo', choices=('off', 'on'), help='Explicit candidate CPU property; omitted preserves the existing CPU argument')
    parser.add_argument('--pauth-memo-entries',type=int,choices=(64,1024,16384),
                        help='Explicit capacity for the enabled memo candidate')
    args = parser.parse_args()
    if args.pauth_memo_entries is not None and args.pauth_memo != 'on':
        parser.error('--pauth-memo-entries requires --pauth-memo on')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = (args.output or ROOT / 'runs/e06-pauth-hvc-smp' / stamp).resolve()
    out.mkdir(parents=True, exist_ok=False)
    qemu = args.qemu.resolve()
    manifest = json.loads((ROOT / 'runs/qemu-bdif-usb-build.json').read_text())
    expected_sha = args.expected_sha256 or manifest['binary_sha256']
    if sha(qemu) != expected_sha:
        raise SystemExit('QEMU differs from the explicit hash or current build manifest')
    report = {
        'time_utc': stamp, 'qemu': str(qemu), 'qemu_sha256': expected_sha,
        'build_manifest': manifest if manifest['binary_sha256'] == expected_sha else None,
        'runner_sha256': sha(Path(__file__)), 'cases': [], 'compile_commands': [],
        'source_sha256': {}, 'guest_sha256': {},
        'clang_version': subprocess.check_output(['clang', '--version'], text=True),
        'scope': 'Self-authored QARMA5 guest. Every configured CPU executes EL1, actual EL0 stack '
                 'access, SVC return and EL0 HVC rejection in two generations. All secondary CPUs '
                 'are powered off and restarted; CPU0 remains the coordinator. Per-CPU key banks '
                 'are concurrently held across bounded barriers. VMApple initializes the '
                 'headless Reims/Vulkan device, but the guest submits no graphics commands. '
                 'No Apple material, installed OS throughput or GUI behavior is tested.',
        'environment': {'REIMS_VGPU_WINDOW': '0'},
        'pauth_memo': args.pauth_memo,
        'pauth_memo_entries': args.pauth_memo_entries,
    }

    def build(count):
        name = f'model-{count}cpu'
        directory = out / name
        directory.mkdir()
        flags = ['clang', '--target=aarch64-none-elf', '-march=armv8.3-a', '-O2',
                 '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-fno-pic',
                 '-fno-pie', '-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror',
                 '-I', str(RUNTIME), f'-DCPUS={count}']
        objects = []
        sources = [RUNTIME / 'start.S', RUNTIME / 'runtime.c', HERE / 'pauth.S',
                   HERE / 'el0-smp4.S', HERE / 'model-smp4.c']
        for path in [*sources, RUNTIME / 'runtime.h', RUNTIME / 'link.ld']:
            report['source_sha256'][str(path.relative_to(ROOT))] = sha(path)
        for path in sources:
            obj = directory / (path.name.replace('.', '_') + '.o')
            command = [*flags, '-c', str(path), '-o', str(obj)]
            report['compile_commands'].append(command)
            subprocess.run(command, check=True, capture_output=True, text=True)
            objects.append(str(obj))
        elf, binary = directory / (name + '.elf'), directory / (name + '.bin')
        for command in [
            ['ld.lld', '--build-id=none', '-T', str(RUNTIME / 'link.ld'), *objects, '-o', str(elf)],
            ['llvm-objcopy', '-O', 'binary', str(elf), str(binary)],
        ]:
            report['compile_commands'].append(command)
            subprocess.run(command, check=True, capture_output=True, text=True)
        report['guest_sha256'][str(binary)] = sha(binary)
        return binary

    try:
        guests = [(count, build(count)) for count in dict.fromkeys(args.vcpus)]
        for name, size in [('aux.raw', 4 * 1024 * 1024), ('root.raw', 32 * 1024 * 1024)]:
            with (out / name).open('wb') as stream:
                stream.truncate(size)
        for count, binary in guests:
            cpu = 'max,pauth-qarma5=on'
            if args.pauth_memo is not None:
                cpu += ',x-pauth-memo=' + args.pauth_memo
            if args.pauth_memo_entries is not None:
                cpu += f',x-pauth-memo-entries={args.pauth_memo_entries}'
            command = [str(qemu), '-object', 'memory-backend-memfd,id=ram,size=512M,share=on',
                       '-M', 'vmapple,memory-backend=ram,x-pac-software-model=on', '-m', '512M',
                       '-smp', str(count), '-cpu', cpu, '-accel', 'tcg,thread=multi',
                       '-nodefaults', '-nic', 'none', '-display', 'none', '-nographic', '-monitor',
                       'none', '-serial', 'stdio', '-semihosting-config', 'enable=on,target=native',
                       '-bios', str(binary),
                       '-drive', f'file={out / "aux.raw"},format=raw,if=pflash,index=0',
                       '-drive', f'file={out / "root.raw"},format=raw,if=pflash,index=1']
            row = {'vcpus': count, 'command': command}
            report['cases'].append(row)
            start = time.perf_counter()
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, env=dict(os.environ, REIMS_VGPU_WINDOW='0'))
            row['pid'] = process.pid
            try:
                stdout, stderr = process.communicate(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                row['timed_out'] = True
            row.update(returncode=process.returncode, seconds=time.perf_counter() - start,
                       reaped=process.returncode is not None,
                       proc_exists_after_wait=Path(f'/proc/{process.pid}').exists())
            log = out / f'model-{count}cpu.log'
            log.write_text(stdout + '\n=== STDERR ===\n' + stderr)
            row['log'] = str(log)
            row['passed'] = process.returncode == 0 and 'E06 SMP PSCI PAC HVC EL0 ISOLATION PASS' in stdout and 'FAIL:' not in stdout
            if row['passed']:
                row['cpu_generation_records'] = cpu_records(stdout, count)
            print(f'{count} CPU:', 'PASS' if row['passed'] else 'FAIL', f'{row["seconds"]:.3f}s', flush=True)
            if not row['passed']:
                print(stdout, flush=True)
                raise RuntimeError(f'{count}-CPU guest failed; inspect {log}')
        report['binary_unchanged'] = sha(qemu) == expected_sha
        report['passed'] = report['binary_unchanged'] and all(row['passed'] for row in report['cases'])
        if not report['passed']:
            raise RuntimeError('Canonical binary changed during the test')
    except Exception as error:
        report.update(passed=False, error=str(error))
        if isinstance(error, subprocess.CalledProcessError):
            report.update(compiler_stdout=error.stdout, compiler_stderr=error.stderr)
        raise
    finally:
        for path in out.rglob('*.o'):
            path.unlink()
        for name in ['aux.raw', 'root.raw']:
            (out / name).unlink(missing_ok=True)
        (out / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
        print('results', out / 'results.json', flush=True)


if __name__ == '__main__':
    main()
