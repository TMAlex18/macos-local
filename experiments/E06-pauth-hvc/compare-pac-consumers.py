#!/usr/bin/env python3
"""Run actual BDIF QEMU PAC/HVC consumers and paired 20M-call cost samples.

Requires an explicitly retained before-patch binary; never builds QEMU or
executes Apple material. Both binaries consume the same compiled guest files.
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
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUNTIME = ROOT / 'experiments/E06-platform-contracts'
ITERATIONS = 20_000_000


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dependencies(binary):
    output = subprocess.check_output(['ldd', str(binary)], text=True)
    paths = sorted(set(re.findall(r'(?:=>\s+|^\s*)(/\S+)\s+\(', output, re.M)))
    return {path: sha(Path(path)) for path in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, default=ROOT / 'build/qemu-bdif-usb/qemu-system-aarch64')
    parser.add_argument('--cpu', type=int, required=True, help='One allowed host CPU for all QEMU threads')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.cpu not in os.sched_getaffinity(0):
        raise SystemExit('Requested host CPU is outside current affinity')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = args.output or ROOT / 'runs/e06-pauth-packed-mult' / (stamp + '-consumers')
    out.mkdir(parents=True, exist_ok=False)
    binaries = {'before': args.before.resolve(), 'after': args.after.resolve()}
    hashes = {label: sha(binary) for label, binary in binaries.items()}
    deps = {label: dependencies(binary) for label, binary in binaries.items()}
    if deps['before'] != deps['after']:
        raise SystemExit('Before/after dynamically linked dependencies differ')
    env = dict(os.environ, REIMS_VGPU_WINDOW='0')
    record = {
        'time_utc': stamp, 'binaries': {label: str(path) for label, path in binaries.items()},
        'binary_sha256': hashes, 'dynamic_dependencies': deps['after'],
        'dependency_scope': 'ldd-resolved shared objects and ELF interpreter; not kernel vdso '
                            'or runtime-loaded drivers. Display disabled for these guests.',
        'host_cpu': args.cpu, 'host_cpu_model': re.search(r'model name\s*:\s*(.+)',
                                                        Path('/proc/cpuinfo').read_text())[1],
        'scaling_governor': Path(f'/sys/devices/system/cpu/cpu{args.cpu}/cpufreq/scaling_governor').read_text().strip(),
        'iterations': ITERATIONS, 'repeats': 3, 'build_commands': [], 'functional': [],
        'samples': [], 'guest_source_sha256': {}, 'guest_sha256': {},
        'runner_sha256': sha(Path(__file__)),
        'scope': 'Self-authored guests only. Functional PAC/HVC on vmapple, then whole-process '
                 'wall-time cost on virt with fixed host CPU affinity. Includes process startup; '
                 'not isolated ComputePAC cycles, Apple algorithm equality or macOS/GUI progress.',
    }

    def save():
        (out / 'results.json').write_text(json.dumps(record, indent=2) + '\n')

    def compile_command(command):
        record['build_commands'].append(command)
        subprocess.run(command, check=True, capture_output=True, text=True)

    def firmware(name, sources):
        directory = out / name
        directory.mkdir()
        flags = ['clang', '--target=aarch64-none-elf', '-march=armv8.3-a', '-O2',
                 '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-fno-pic',
                 '-fno-pie', '-mgeneral-regs-only', '-Wall', '-Wextra', '-I', str(RUNTIME)]
        objects = []
        for source in sources:
            record['guest_source_sha256'][str(source.relative_to(ROOT))] = sha(source)
            obj = directory / (source.name.replace('.', '_') + '.o')
            compile_command([*flags, '-c', str(source), '-o', str(obj)])
            objects.append(str(obj))
        elf, binary = directory / (name + '.elf'), directory / (name + '.bin')
        compile_command(['ld.lld', '--build-id=none', '-T', str(RUNTIME / 'link.ld'),
                         *objects, '-o', str(elf)])
        compile_command(['llvm-objcopy', '-O', 'binary', str(elf), str(binary)])
        record['guest_sha256'][str(binary)] = sha(binary)
        return binary

    def benchmark_guest(name, defines):
        source = HERE / 'bench-pac-cost.S'
        record['guest_source_sha256'][str(source.relative_to(ROOT))] = sha(source)
        obj, elf = out / (name + '.o'), out / (name + '.elf')
        compile_command(['clang', '--target=aarch64-none-elf', '-march=armv8.3-a', '-c',
                         f'-DITERATIONS={ITERATIONS}', *defines, str(source), '-o', str(obj)])
        compile_command(['ld.lld', '--build-id=none', '-Ttext=0x40080000', '-e', '_start',
                         str(obj), '-o', str(elf)])
        record['guest_sha256'][str(elf)] = sha(elf)
        return elf

    def execute(label, command, timeout):
        command = ['taskset', '-c', str(args.cpu), *command]
        started = datetime.now(timezone.utc).isoformat()
        start = time.perf_counter()
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout, env=env)
        elapsed = time.perf_counter() - start
        (out / (label + '.log')).write_text(result.stdout + '\n=== STDERR ===\n' + result.stderr)
        return result, {'command': command, 'start_utc': started, 'seconds': elapsed,
                        'returncode': result.returncode, 'log': label + '.log'}

    try:
        common = [RUNTIME / 'start.S', RUNTIME / 'runtime.c', HERE / 'pauth.S']
        baseline = firmware('baseline', [*common, HERE / 'baseline.c'])
        model = firmware('model', [*common, HERE / 'el0.S', HERE / 'model.c'])
        record['guest_source_sha256'][str((RUNTIME / 'link.ld').relative_to(ROOT))] = sha(RUNTIME / 'link.ld')
        for name, size in [('aux.raw', 4 * 1024 * 1024), ('root.raw', 32 * 1024 * 1024)]:
            with (out / name).open('wb') as stream:
                stream.truncate(size)
        for label, binary in binaries.items():
            for test, guest, algorithm, smp, enabled, marker in [
                ('baseline-qarma5', baseline, 'qarma5', 1, False, 'E06 PAUTH BASELINE PASS'),
                ('baseline-qarma3', baseline, 'qarma3', 1, False, 'E06 PAUTH BASELINE PASS'),
                ('model-qarma5', model, 'qarma5', 2, True, 'E06 SOFTWARE PAC MODEL SEMANTICS PASS'),
            ]:
                machine = 'vmapple,memory-backend=ram' + (',x-pac-software-model=on' if enabled else '')
                command = [str(binary), '-object', 'memory-backend-memfd,id=ram,size=512M,share=on',
                           '-M', machine, '-m', '512M', '-smp', str(smp), '-cpu',
                           f'max,pauth-{algorithm}=on', '-accel', 'tcg', '-nodefaults', '-nic', 'none',
                           '-display', 'none', '-nographic', '-monitor', 'none', '-serial', 'stdio',
                           '-semihosting-config', 'enable=on,target=native', '-bios', str(guest),
                           '-drive', f'file={out / "aux.raw"},format=raw,if=pflash,index=0',
                           '-drive', f'file={out / "root.raw"},format=raw,if=pflash,index=1']
                result, row = execute(label + '-' + test, command, 35)
                row.update(binary=label, case=test, passed=result.returncode == 0 and marker in result.stdout)
                record['functional'].append(row)
                save()
                print(label, test, 'PASS' if row['passed'] else 'FAIL', flush=True)
                if not row['passed']:
                    raise RuntimeError('Functional consumer failed; inspect ' + row['log'])
        plain, pac = benchmark_guest('plain', []), benchmark_guest('pac', ['-DWITH_PAC'])
        profiles = [('plain_ret', plain, 'max,pauth-impdef=on'),
                    ('qarma5', pac, 'max,pauth-qarma5=on'),
                    ('qarma3', pac, 'max,pauth-qarma3=on')]
        for repeat in range(3):
            order = ['before', 'after'] if repeat % 2 == 0 else ['after', 'before']
            for profile, guest, cpu in profiles:
                for label in order:
                    command = [str(binaries[label]), '-machine', 'virt', '-cpu', cpu, '-accel', 'tcg',
                               '-smp', '1', '-m', '128M', '-nodefaults', '-display', 'none',
                               '-monitor', 'none', '-serial', 'none', '-no-reboot', '-kernel', str(guest)]
                    result, row = execute(f'cost-{repeat}-{profile}-{label}', command, 120)
                    row.update(binary=label, profile=profile, repeat=repeat)
                    record['samples'].append(row)
                    save()
                    print(repeat, profile, label, f'{row["seconds"]:.6f}s', flush=True)
                    result.check_returncode()
        medians = {label: {profile: statistics.median(
            row['seconds'] for row in record['samples'] if row['binary'] == label and row['profile'] == profile)
            for profile, _, _ in profiles} for label in binaries}
        record['median_seconds'] = medians
        record['cost_comparison'] = {}
        for profile in ['qarma5', 'qarma3']:
            old = medians['before'][profile] - medians['before']['plain_ret']
            new = medians['after'][profile] - medians['after']['plain_ret']
            record['cost_comparison'][profile] = {
                'before_extra_ns_per_call': old / ITERATIONS * 1e9,
                'after_extra_ns_per_call': new / ITERATIONS * 1e9,
                'extra_cost_reduction_percent': (1 - new / old) * 100,
                'whole_process_speedup': medians['before'][profile] / medians['after'][profile],
            }
        if {label: sha(binary) for label, binary in binaries.items()} != hashes:
            raise RuntimeError('A binary changed during the comparison')
        if any(dependencies(binary) != deps[label] for label, binary in binaries.items()):
            raise RuntimeError('Dynamic dependencies changed during the comparison')
        record['passed'] = True
        print(json.dumps(record['cost_comparison'], indent=2), flush=True)
    except Exception as error:
        record.update(passed=False, error=str(error))
        raise
    finally:
        save()
        print('results', out / 'results.json', flush=True)


if __name__ == '__main__':
    main()
