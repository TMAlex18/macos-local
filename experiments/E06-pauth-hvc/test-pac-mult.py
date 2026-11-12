#!/usr/bin/env python3
"""Check packed PAuth primitives against scalar oracles and full ComputePAC.

Only builds a single native test process. Does not build/start QEMU or execute
Apple material. Generated source and exact compiler/source provenance remain in
the result directory; the executable is removed after the check.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SOURCE = ROOT / 'sources/qemu-bdif-usb'
PIN = '1a72df984914fb9929d35b1217619178133d65a4'
HELPER = 'target/arm/tcg/pauth_helper.c'
KAT = 'tests/tcg/aarch64/system/pauth-3.c'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def original(path):
    return subprocess.check_output(['git', '-C', str(SOURCE), 'show', f'{PIN}:{path}'])


def algorithm(text, prefix):
    start = text.index('static uint64_t pac_cell_shuffle(')
    stop = text.index('static uint64_t pauth_computepac_impdef(')
    body = text[start:stop]
    names = re.findall(r'^static (?:uint64_t|int) (\w+)\(', body, re.M)
    return re.sub(r'\b(' + '|'.join(names) + r')\b',
                  lambda match: prefix + match[0], body)


def normalize_va_provider(text):
    """Allow only the three provider substitutions checked by test-pac-va."""
    fast = 'aa64_pauth_va_parameters(env, ptr, mmu_idx, data)'
    full = 'aa64_va_parameters(env, ptr, mmu_idx, data, false)'
    if fast in text:
        if text.count(fast) != 3:
            raise ValueError('Unexpected number of PAuth VA provider calls')
        return text.replace(fast, full)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cc', default='cc')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = args.output or ROOT / 'runs/e06-pauth-packed-mult' / stamp
    out.mkdir(parents=True, exist_ok=False)
    old_bytes, new_bytes = original(HELPER), (SOURCE / HELPER).read_bytes()
    old, new = old_bytes.decode(), new_bytes.decode()
    # Limit changes to independently checked primitives and their unused
    # scalar helpers. pac_sub1, rounds, keys, algorithm selection and
    # authentication must remain byte-identical to the pinned source.
    allowed = re.compile(r'^static (?:uint64_t|int) '
                         r'(?:pac_mult|rot_cell|pac_sub|pac_inv_sub|pac_cell_shuffle|'
                         r'pac_cell_inv_shuffle|tweak_shuffle|tweak_inv_shuffle|'
                         r'tweak_cell_rot|tweak_cell_inv_rot)\([^)]*\)\n'
                         r'\{.*?^\}\n\n', re.M | re.S)
    old_remainder = allowed.sub('', old)
    new_remainder = allowed.sub('', normalize_va_provider(new))
    memo = 'static unsigned pauth_memo_slot(' in new
    if memo:
        # 0024's optional counters use QEMU atomic accessors. The header is
        # not an arithmetic/authentication change; no other includes differ.
        assert new_remainder.count('#include "qemu/atomic.h"\n') == 1
        new_remainder = new_remainder.replace('#include "qemu/atomic.h"\n', '')
        # The new pure-result wrapper has its own pinned scalar oracle below.
        # Insertion/authentication stays exact except the three parameter
        # provider calls, whose field/consumer equivalence has its own oracle.
        wrappers = re.compile(r'^static (?:uint64_t|unsigned) '
                              r'(?:pauth_memo_slot|pauth_computepac)\([^)]*\)\n'
                              r'\{.*?^\}\n\n', re.M | re.S)
        old_remainder, new_remainder = wrappers.sub('', old_remainder), wrappers.sub('', new_remainder)
    if old_remainder != new_remainder:
        raise SystemExit('Source changes exceed the checked PAuth primitives and their scalar helpers')
    kat = original(KAT)
    kat_body = re.search(rb'static const uint64_t d\[5\] = \{(.*?)\};', kat, re.S)[1]
    vector = [int(value, 16) for value in re.findall(rb'0x([0-9a-fA-F]+)', kat_body)]
    if len(vector) != 6 or vector[-1] != 0xffffffff00000000:
        raise SystemExit('Unexpected upstream known-answer vector format')
    # The upstream PACGA test masks the output to 32 bits; keep the literal's
    # full 64-bit answer and test both that and the upstream masked answer.
    vector = vector[:5]
    support = r'''
#include <stdbool.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
typedef struct { uint64_t lo, hi; } ARMPACKey;
#define MAKE_64BIT_MASK(shift, length) (((UINT64_C(1) << (length)) - 1) << (shift))
static uint64_t extract64(uint64_t x, unsigned start, unsigned length)
{ return (x >> start) & ((UINT64_C(1) << length) - 1); }
static uint32_t extract32(uint32_t x, unsigned start, unsigned length)
{ return (x >> start) & ((UINT32_C(1) << length) - 1); }
static uint64_t rol64(uint64_t x, unsigned n) { return (x << n) | (x >> (64 - n)); }
static uint64_t ror64(uint64_t x, unsigned n) { return (x >> n) | (x << (64 - n)); }
'''
    known = 'static const uint64_t known[5] = {' + ', '.join(
        f'UINT64_C(0x{value:016x})' for value in vector) + '};\n'
    harness = HERE / 'pac-mult-oracle.c'
    generated = out / 'check.c'
    generated.write_text(support + algorithm(old, 'old_') + algorithm(new, 'new_') +
                         known + harness.read_text())
    flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
             '-fsanitize=undefined', '-fno-sanitize-recover=all']
    executable = out / 'check'
    command = [args.cc, *flags, str(generated), '-o', str(executable)]
    record = {
        'time_utc': stamp, 'base_commit': PIN,
        'old_source': f'{PIN}:{HELPER}', 'old_source_sha256': sha(old_bytes),
        'new_source': str((SOURCE / HELPER).relative_to(ROOT)),
        'new_source_sha256': sha(new_bytes),
        'known_answer_source': f'{PIN}:{KAT}', 'known_answer_source_sha256': sha(kat),
        'harness_sha256': sha(harness.read_bytes()),
        'runner_sha256': sha(Path(__file__).read_bytes()),
        'generated_source_sha256': sha(generated.read_bytes()),
        'compiler_version': subprocess.check_output([args.cc, '--version'], text=True),
        'compile_command': command,
        'scope': 'Native unsigned arithmetic equivalence only. No QEMU instruction/HVC, '
                 'installed OS, throughput or wall-time performance claim.',
    }
    try:
        compiled = subprocess.run(command, text=True, capture_output=True, check=True)
        record['compiler_output'] = compiled.stdout + compiled.stderr
        checked = subprocess.run([str(executable)], text=True, capture_output=True, check=True)
        record['checks'] = json.loads(checked.stdout)
        record['stderr'] = checked.stderr
        if memo:
            subprocess.run([sys.executable, str(HERE / 'test-pac-memo.py'), '--cc', args.cc,
                            '--output', str(out / 'memo')], text=True, capture_output=True, check=True)
            memo_result = json.loads((out / 'memo/results.json').read_text())
            record['memo_wrapper'] = {'passed': memo_result['passed'], 'checks': memo_result['checks'],
                                      'report': str(out / 'memo/results.json')}
            record['scope'] += ' The default-off memo wrapper is separately checked against pinned scalar ComputePAC.'
        record['passed'] = True
    except subprocess.CalledProcessError as error:
        record.update(passed=False, failed_command=error.cmd,
                      stdout=error.stdout, stderr=error.stderr)
        raise
    finally:
        executable.unlink(missing_ok=True)
        (out / 'results.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record['checks'], indent=2))
    print('results', out / 'results.json')


if __name__ == '__main__':
    main()
