#!/usr/bin/env python3
"""Exercise the same candidate binary with exact PAC memoization off/on.

Only self-authored guests and newly created scratch disks are used. QEMU and
Reims are never built here. Paired timing includes startup and any concurrent
host workloads; it is not a macOS or GUI performance result.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUNTIME = ROOT / 'experiments/E06-platform-contracts'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cpu', type=int, required=True)
    parser.add_argument('--iterations', type=int, default=20_000_000)
    parser.add_argument('--concurrent-pid', type=int)
    parser.add_argument('--functional-only', action='store_true',
                        help='Validate the promoted binary without repeating cost benchmarks')
    parser.add_argument('--pauth-memo-entries',type=int,choices=(64,1024,16384),
                        help='Capacity for memo-on functional/cost cases; memo-off remains unchanged')
    args = parser.parse_args()
    if args.cpu not in os.sched_getaffinity(0) or not 1 <= args.iterations <= 20_000_000:
        raise SystemExit('Invalid CPU affinity or bounded iteration count')
    qemu, out = args.qemu.resolve(), args.output.resolve()
    if qemu == ROOT / 'build/qemu-bdif-usb/qemu-system-aarch64' and not args.functional_only:
        raise SystemExit('Use the independent candidate binary for costs, or --functional-only for mainline integration')
    out.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, REIMS_VGPU_WINDOW='0', VK_DRIVER_FILES='/usr/share/vulkan/icd.d/nvidia_icd.json',
               VK_LOADER_LAYERS_DISABLE='*')
    # Use the installed driver libraries, as FirmwareSession does. An old
    # unpacked runtime can survive a host driver update and fail before the
    # self-authored guest executes, which is not a PAC test result.
    for key in ('VK_ICD_FILENAMES', 'VK_ADD_DRIVER_FILES', 'VK_INSTANCE_LAYERS', 'VK_LOADER_LAYERS_ENABLE'):
        env.pop(key, None)
    record = {'binary': str(qemu), 'binary_sha256': sha(qemu),
              'iterations': None if args.functional_only else args.iterations,
              'functional_only': args.functional_only,'pauth_memo_entries':args.pauth_memo_entries,
              'host_cpu': args.cpu, 'scaling_governor': Path(f'/sys/devices/system/cpu/cpu{args.cpu}/cpufreq/scaling_governor').read_text().strip(),
              'concurrent_pid': args.concurrent_pid, 'concurrent_pid_present_at_start': bool(args.concurrent_pid and Path(f'/proc/{args.concurrent_pid}').exists()),
              'compile_commands': [], 'source_sha256': {}, 'guest_sha256': {}, 'functional': [], 'smp': [], 'samples': [],
              'environment': {k: env[k] for k in ('REIMS_VGPU_WINDOW', 'VK_DRIVER_FILES', 'VK_LOADER_LAYERS_DISABLE', 'LD_LIBRARY_PATH') if k in env},
              'scope': 'Self-authored PAuth/HVC/EL0/PSCI consumers, then paired off/on whole-process times on one candidate binary. High-reuse leaf is a favorable cache case, changing-SP includes PAC/AUT reuse, PACGA stream is all-miss. No Apple material or OS/GUI speedup claim.'}

    def save():
        (out / 'results.json').write_text(json.dumps(record, indent=2) + '\n')

    def compile(command):
        record['compile_commands'].append(command)
        subprocess.run(command, capture_output=True, text=True, check=True)

    def firmware(name, sources):
        directory = out / name; directory.mkdir()
        flags = ['clang', '--target=aarch64-none-elf', '-march=armv8.3-a', '-O2', '-ffreestanding',
                 '-fno-builtin', '-fno-stack-protector', '-fno-pic', '-fno-pie', '-mgeneral-regs-only',
                 '-Wall', '-Wextra', '-I', str(RUNTIME)]
        objects = []
        for source in sources:
            record['source_sha256'][str(source.relative_to(ROOT))] = sha(source)
            obj = directory / (source.name.replace('.', '_') + '.o')
            compile([*flags, '-c', str(source), '-o', str(obj)]); objects.append(str(obj))
        elf, binary = directory / (name + '.elf'), directory / (name + '.bin')
        compile(['ld.lld', '--build-id=none', '-T', str(RUNTIME / 'link.ld'), *objects, '-o', str(elf)])
        compile(['llvm-objcopy', '-O', 'binary', str(elf), str(binary)])
        record['guest_sha256'][str(binary)] = sha(binary)
        return binary

    def execute(label, command, limit, affinity=False):
        if affinity:
            command = ['taskset', '-c', str(args.cpu), *command]
        start = time.perf_counter()
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            process.kill(); stdout, stderr = process.communicate(); timed_out = True
        (out / (label + '.log')).write_text(stdout + '\nSTDERR:\n' + stderr)
        row = {'command': command, 'returncode': process.returncode, 'seconds': time.perf_counter() - start,
               'pid': process.pid, 'reaped': process.returncode is not None, 'timed_out': timed_out, 'log': label + '.log'}
        return stdout, row

    try:
        common = [RUNTIME / 'start.S', RUNTIME / 'runtime.c', HERE / 'pauth.S']
        baseline = firmware('baseline', [*common, HERE / 'baseline.c'])
        model = firmware('model', [*common, HERE / 'el0.S', HERE / 'model.c'])
        for name, size in [('aux.raw', 4 << 20), ('root.raw', 32 << 20)]:
            with (out / name).open('wb') as stream:
                stream.truncate(size)
        for mode in ('off', 'on'):
            capacity = f',x-pauth-memo-entries={args.pauth_memo_entries}' if mode=='on' and args.pauth_memo_entries is not None else ''
            for case, guest, algorithm, smp, model_on, marker in [
                    *[(f'baseline-{a}', baseline, a, 1, False, 'E06 PAUTH BASELINE PASS') for a in ('qarma5', 'qarma3', 'impdef')],
                    ('hvc-qarma5', model, 'qarma5', 2, True, 'E06 SOFTWARE PAC MODEL SEMANTICS PASS')]:
                machine = 'vmapple,memory-backend=ram' + (',x-pac-software-model=on' if model_on else '')
                command = [str(qemu), '-object', 'memory-backend-memfd,id=ram,size=512M,share=on', '-M', machine,
                           '-m', '512M', '-smp', str(smp), '-cpu', f'max,pauth-{algorithm}=on,x-pauth-memo={mode}'+capacity,
                           '-accel', 'tcg', '-nodefaults', '-nic', 'none', '-display', 'none', '-nographic',
                           '-monitor', 'none', '-serial', 'stdio', '-semihosting-config', 'enable=on,target=native',
                           '-bios', str(guest), '-drive', f'file={out / "aux.raw"},format=raw,if=pflash,index=0',
                           '-drive', f'file={out / "root.raw"},format=raw,if=pflash,index=1']
                stdout, row = execute(mode + '-' + case, command, 45)
                row.update(mode=mode, case=case, passed=row['returncode'] == 0 and marker in stdout and 'FAIL:' not in stdout)
                record['functional'].append(row); save()
                print(mode, case, row['passed'], flush=True)
                if not row['passed']:
                    raise RuntimeError('Actual PAC/HVC consumer failed')
            directory = out / ('smp-' + mode)
            cmd = [sys.executable, str(HERE / 'run-smp4.py'), '--qemu', str(qemu), '--expected-sha256', record['binary_sha256'],
                   '--pauth-memo', mode, '--vcpus', '2', '4', '8', '--output', str(directory)]
            if mode=='on' and args.pauth_memo_entries is not None:
                cmd += ['--pauth-memo-entries',str(args.pauth_memo_entries)]
            checked = subprocess.run(cmd, env=env, capture_output=True, text=True)
            (out / ('smp-' + mode + '.log')).write_text(checked.stdout + '\nSTDERR:\n' + checked.stderr)
            record['smp'].append({'mode': mode, 'report': str(directory / 'results.json'), 'returncode': checked.returncode})
            save(); checked.check_returncode()
            print(mode, 'SMP 2/4/8 PASS', flush=True)

        if args.functional_only:
            record.update(passed=True,binary_unchanged=sha(qemu)==record['binary_sha256'],
                          scope='Promoted-binary PAuth/HVC/EL0/PSCI and 2/4/8-vCPU functional integration only; no new timing or OS/GUI performance claim.')
            assert record['binary_unchanged']
            return

        source = HERE / 'bench-pac-memo.S'; record['source_sha256'][str(source.relative_to(ROOT))] = sha(source)
        guests = {}
        for name, defines in [('plain', []), ('hot', ['-DWITH_PAC']),
                              ('changing_sp', ['-DWITH_PAC', '-DSTREAM_MODIFIER']), ('pacga_stream', ['-DPACGA_STREAM'])]:
            obj, elf = out / (name + '.o'), out / (name + '.elf')
            compile(['clang', '--target=aarch64-none-elf', '-march=armv8.3-a', '-c', f'-DITERATIONS={args.iterations}',
                     *defines, str(source), '-o', str(obj)])
            compile(['ld.lld', '--build-id=none', '-Ttext=0x40080000', '-e', '_start', str(obj), '-o', str(elf)])
            guests[name] = elf; record['guest_sha256'][str(elf)] = sha(elf)
        checksums = {}
        for repeat in range(3):
            for algorithm in ('qarma5', 'qarma3'):
                for profile, guest in guests.items():
                    for mode in (('off', 'on') if repeat % 2 == 0 else ('on', 'off')):
                        capacity = f',x-pauth-memo-entries={args.pauth_memo_entries}' if mode=='on' and args.pauth_memo_entries is not None else ''
                        command = [str(qemu), '-machine', 'virt', '-cpu', f'max,pauth-{algorithm}=on,x-pauth-memo={mode}'+capacity,
                                   '-accel', 'tcg', '-smp', '1', '-m', '128M', '-nodefaults', '-display', 'none',
                                   '-monitor', 'none', '-serial', 'stdio', '-no-reboot', '-kernel', str(guest)]
                        stdout, row = execute(f'cost-{repeat}-{algorithm}-{profile}-{mode}', command, 120, affinity=True)
                        match = re.fullmatch(r'([0-9a-f]{16})\s*', stdout)
                        assert row['returncode'] == 0 and match, row['log']
                        checksum = match[1]; identity = (algorithm, profile)
                        assert checksums.setdefault(identity, checksum) == checksum, 'off/on guest accumulator differs'
                        if profile != 'pacga_stream':
                            assert int(checksum, 16) == args.iterations
                        row.update(mode=mode, algorithm=algorithm, profile=profile, repeat=repeat, accumulator=checksum)
                        record['samples'].append(row); save()
                        print(repeat, algorithm, profile, mode, f'{row["seconds"]:.6f}', flush=True)
        summary = {}
        for algorithm in ('qarma5', 'qarma3'):
            summary[algorithm] = {}
            for profile in guests:
                medians = {mode: statistics.median(row['seconds'] for row in record['samples']
                           if (row['algorithm'], row['profile'], row['mode']) == (algorithm, profile, mode)) for mode in ('off', 'on')}
                summary[algorithm][profile] = {**medians, 'whole_process_speedup': medians['off'] / medians['on']}
        record.update(passed=True, timings=summary, binary_unchanged=sha(qemu) == record['binary_sha256'])
        assert record['binary_unchanged']
        print(json.dumps(summary, indent=2), flush=True)
    except Exception as error:
        record.update(passed=False, error=str(error)); raise
    finally:
        for path in out.rglob('*.o'):
            path.unlink()
        for name in ('aux.raw', 'root.raw'):
            (out / name).unlink(missing_ok=True)
        record['concurrent_pid_present_at_end'] = bool(args.concurrent_pid and Path(f'/proc/{args.concurrent_pid}').exists())
        save()


if __name__ == '__main__':
    main()
