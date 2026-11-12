#!/usr/bin/env python3
"""Per-call TCG cost of PACIBSP/RETAB under each QEMU PAuth algorithm.

Self-authored guest on `-machine virt`, timed as whole QEMU processes on the
live-run binary. It measures emulation cost only: no Apple code, no network,
and no claim about Apple's PAC algorithm or a full macOS boot.
"""
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
QEMU = ROOT / 'build/qemu-bdif-usb/qemu-system-aarch64'
ITERATIONS, REPEATS = 20_000_000, 3
OUT = ROOT / 'build/e06-pauth-cost'


def build(name, defines):
    obj, elf = OUT / f'{name}.o', OUT / f'{name}.elf'
    subprocess.run(['clang', '--target=aarch64-none-elf', '-march=armv8.3-a', '-c',
                    f'-DITERATIONS={ITERATIONS}', *defines, str(HERE / 'bench-pac-cost.S'),
                    '-o', str(obj)], check=True)
    subprocess.run(['ld.lld', '--build-id=none', '-Ttext=0x40080000', '-e', '_start',
                    str(obj), '-o', str(elf)], check=True)
    return elf


def timed(elf, cpu):
    command = [str(QEMU), '-machine', 'virt', '-cpu', cpu, '-accel', 'tcg', '-smp', '1',
               '-m', '128M', '-nodefaults', '-display', 'none', '-monitor', 'none',
               '-serial', 'none', '-no-reboot', '-kernel', str(elf)]
    start = time.perf_counter()
    subprocess.run(command, check=True, timeout=600)
    return time.perf_counter() - start


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    plain, pac = build('plain', []), build('pac', ['-DWITH_PAC'])
    profiles = [('plain_ret', plain, 'max,pauth-impdef=on')]
    profiles += [(f'pacibsp_retab_{name}', pac, f'max,pauth-{name}=on')
                 for name in ('impdef', 'qarma3', 'qarma5')]
    rows, baseline = [], None
    for label, elf, cpu in profiles:
        samples = [timed(elf, cpu) for _ in range(REPEATS)]
        median = statistics.median(samples)
        baseline = median if baseline is None else baseline
        rows.append({'profile': label, 'cpu': cpu, 'seconds': [round(s, 4) for s in samples],
                     'median_seconds': round(median, 4),
                     'extra_ns_per_call_vs_plain': round((median - baseline) / ITERATIONS * 1e9, 1)})
        print(f'{label:24s} median {median:7.3f}s  extra per call {rows[-1]["extra_ns_per_call_vs_plain"]:7.1f} ns')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = ROOT / 'runs/e06-pauth-cost' / stamp / 'results.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'time': stamp, 'qemu_binary': str(QEMU.relative_to(ROOT)), 'iterations': ITERATIONS,
        'repeats': REPEATS, 'machine': 'virt', 'smp': 1, 'profiles': rows,
        'scope': 'Self-authored EL1 call loop; host wall time of whole QEMU processes. Emulation '
                 'cost of PACIBSP/RETAB only; not Apple PAC, not a macOS boot measurement.',
        'apple_firmware_or_kernel_executed': False}, indent=2) + '\n')
    print('results', path.relative_to(ROOT))
    return 0


if __name__ == '__main__':
    sys.exit(main())
