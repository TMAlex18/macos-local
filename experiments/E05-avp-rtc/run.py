#!/usr/bin/env python3
"""Self-authored RTC guest MMIO checks plus an independent host Unix-time oracle."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUNTIME = ROOT / 'experiments/E06-platform-contracts'
BUILD = ROOT / 'build/e05-avp-rtc'
CASES = [
    'version-widths', 'unix-epoch-snapshot', 'running-microsecond-rate',
    'counter-32bit-lanes', 'load64-and-counter', 'load-32bit-lanes',
    'control-mask-idle-state', 'readonly-and-reserved',
    'match-does-not-create-alarm-state',
]


def build_guest():
    BUILD.mkdir(parents=True, exist_ok=True)
    common = ['clang', '--target=aarch64-none-elf', '-O2', '-ffreestanding',
              '-fno-builtin', '-fno-stack-protector', '-fno-pic', '-fno-pie',
              '-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror', '-I', str(RUNTIME)]
    objects = []
    for directory, name in ((RUNTIME, 'start.S'), (RUNTIME, 'runtime.c'), (HERE, 'guest.c')):
        obj = BUILD / (name.replace('.', '_') + '.o')
        result = subprocess.run([*common, '-c', str(directory / name), '-o', str(obj)],
                                capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f'compile {name}:\n{result.stdout}\n{result.stderr}')
        objects.append(str(obj))
    elf = BUILD / 'guest.elf'
    image = BUILD / 'guest.bin'
    subprocess.run(['ld.lld', '--build-id=none', '-T', str(RUNTIME / 'link.ld'),
                    *objects, '-o', str(elf)], check=True)
    subprocess.run(['llvm-objcopy', '-O', 'binary', str(elf), str(image)], check=True)
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=Path('build/qemu-bdif-usb/qemu-system-aarch64'))
    parser.add_argument('--build-only', action='store_true')
    args = parser.parse_args()
    image = build_guest()
    if args.build_only:
        print('BUILT', image.relative_to(ROOT))
        return 0
    binary = args.binary if args.binary.is_absolute() else ROOT / args.binary
    for name, size in (('aux.raw', 4 * 1024 * 1024), ('root.raw', 32 * 1024 * 1024)):
        path = BUILD / name
        if not path.exists():
            with path.open('wb') as handle:
                handle.truncate(size)
    command = [str(binary), '-object', 'memory-backend-memfd,id=ram,size=512M,share=on',
               '-M', 'vmapple,memory-backend=ram', '-cpu', 'max,cntfrq=24000000,x-cntfrq-exact=on',
               '-accel', 'tcg', '-m', '512M', '-smp', '1', '-nodefaults', '-nic', 'none',
               '-display', 'none', '-nographic', '-monitor', 'none', '-serial', 'stdio',
               '-semihosting-config', 'enable=on,target=native', '-bios', str(image),
               '-drive', f'file={BUILD / "aux.raw"},format=raw,if=pflash,index=0',
               '-drive', f'file={BUILD / "root.raw"},format=raw,if=pflash,index=1']
    timeout = False
    host_before_us = time.time_ns() // 1000
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15,
                                env=dict(os.environ, REIMS_VGPU_WINDOW='0', REIMS_VGPU_LOG='warn'))
        uart, stderr, rc = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as error:
        timeout = True
        rc = None
        uart = error.stdout.decode(errors='replace') if isinstance(error.stdout, bytes) else error.stdout or ''
        stderr = error.stderr.decode(errors='replace') if isinstance(error.stderr, bytes) else error.stderr or ''
    host_after_us = time.time_ns() // 1000
    epoch_fields = re.findall(r'^RTC_EPOCH_US=(0x[0-9a-f]+)\r?$', uart, flags=re.MULTILINE)
    epoch_us = int(epoch_fields[0], 16) if len(epoch_fields) == 1 else None
    epoch_passed = epoch_us is not None and host_before_us <= epoch_us <= host_after_us
    observed = re.findall(r'^RTC_CASE_PASS ([a-z0-9-]+)\r?$', uart, flags=re.MULTILINE)
    passed = (not timeout and rc == 0 and observed == CASES and epoch_passed
              and 'AVP_RTC_GUEST_PASS' in uart and 'FAIL:' not in uart)
    log = ROOT / 'runs/e05-avp-rtc.log'
    report_path = ROOT / 'runs/e05-avp-rtc-results.json'
    log.write_text(uart + '\n=== STDERR ===\n' + stderr)
    report = {
        'time_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'Self-authored EL1 guest, real TCG RTC MMIO, independent host Unix-time window and CNTPCT/CNTFRQ rate checks.',
        'passed': passed, 'guest_exit': rc, 'host_timeout': timeout,
        'binary': str(binary.relative_to(ROOT)) if binary.is_relative_to(ROOT) else str(binary),
        'firmware_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
        'runtime': 'experiments/E06-platform-contracts/{start.S,runtime.c,runtime.h,link.ld}',
        'cases': [{'case': name, 'passed': name in observed and (name != 'unix-epoch-snapshot' or epoch_passed)}
                  for name in CASES],
        'host_epoch_oracle': {'before_us': host_before_us, 'after_us': host_after_us,
                              'guest_counter_us': epoch_us, 'passed': epoch_passed},
        'case_order_matches': observed == CASES, 'uart': uart,
        'limitations': ['No host-power event injection or IRQ delivery validation.',
                        'Idle raw/masked/W1C checks do not validate clearing an asserted event.',
                        'VM-pause progression and migration are not exercised.'],
        'log': str(log.relative_to(ROOT)), 'command': command,
        'apple_firmware_executed': False, 'new_tss_request': False,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f'AVP_RTC: {"PASS" if passed else "FAIL"} {len(observed)}/{len(CASES)} cases guest_exit={rc} epoch_window={epoch_passed} timeout={timeout}')
    print('REPORT', report_path.relative_to(ROOT))
    if not passed:
        print(uart, end='')
        print(stderr[-3500:], file=sys.stderr)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
