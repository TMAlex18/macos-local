#!/usr/bin/env python3
"""Check the actual default-off ComputePAC memo wrapper against pinned scalar code.

No VM, Apple code or live keys are read. Tests contain synthetic inputs only;
the generated check prints aggregate counts, never input tuples or key values.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SOURCE = ROOT / 'sources/qemu-bdif-usb'
PIN = '1a72df984914fb9929d35b1217619178133d65a4'
spec = importlib.util.spec_from_file_location('pac_primitives', HERE / 'test-pac-mult.py')
primitives = importlib.util.module_from_spec(spec)
spec.loader.exec_module(primitives)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    old = primitives.original(primitives.HELPER).decode()
    new = (SOURCE / primitives.HELPER).read_text()
    cpu = (SOURCE / 'target/arm/cpu.h').read_text()
    cpu_c = (SOURCE / 'target/arm/cpu.c').read_text()
    migration = (SOURCE / 'target/arm/machine.c').read_text()
    assert old[old.index('static uint64_t pauth_addpac('):] == primitives.normalize_va_provider(
        new[new.index('static uint64_t pauth_addpac('):])
    assert 'DEFINE_PROP_BOOL("x-pauth-memo", ARMCPU, prop_pauth_memo, false)' in cpu_c
    assert cpu.index('ARMPAuthMemoEntry pauth_memo[') < cpu.index('struct {} end_reset_fields;')
    assert 'memset(env, 0, offsetof(CPUARMState, end_reset_fields));' in cpu_c
    assert 'memset(env->pauth_memo, 0, sizeof(env->pauth_memo));' in migration
    for name in ('hits','misses'):
        assert f'qatomic_set(&env->pauth_memo_{name}, 0);' in cpu_c
        assert f'qatomic_set(&env->pauth_memo_{name}, 0);' in migration
    assert 'prop_pauth_memo_stats, false)' in cpu_c
    assert 'arm_get_pauth_memo_hits, NULL, NULL, NULL)' in cpu_c
    assert 'arm_get_pauth_memo_misses, NULL, NULL, NULL)' in cpu_c
    entry = re.search(r'#define ARM_PAUTH_MEMO_DEFAULT_ENTRIES 64\n#define ARM_PAUTH_MEMO_MAX_ENTRIES 16384\n.*?\} ARMPAuthMemoEntry;', cpu, re.S)[0]
    wrapper = new[new.index('static unsigned pauth_memo_slot('):new.index('static uint64_t pauth_addpac(')]
    # Only the wrapper's slow-path calls are counted in this native harness.
    wrapper = wrapper.replace('pauth_computepac_architected(', 'counted_architected(').replace('pauth_computepac_impdef(', 'counted_impdef(')
    xxhash = primitives.original('include/qemu/xxhash.h').decode()
    xxhash = '#define QEMU_XXHASH_SEED 1\n' + xxhash[xxhash.index('#define XXH_PRIME64_1'):xxhash.index('#endif /* QEMU_XXHASH_H */')]
    support = r'''
#include <stdbool.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct { uint64_t lo, hi; } ARMPACKey;
#define MAKE_64BIT_MASK(shift, length) (((UINT64_C(1) << (length)) - 1) << (shift))
static uint64_t extract64(uint64_t x, unsigned start, unsigned length)
{ return (x >> start) & ((UINT64_C(1) << length) - 1); }
static uint32_t extract32(uint32_t x, unsigned start, unsigned length)
{ return (x >> start) & ((UINT32_C(1) << length) - 1); }
static uint64_t rol64(uint64_t x, unsigned n) { return (x << n) | (x >> (64 - n)); }
static uint64_t ror64(uint64_t x, unsigned n) { return (x >> n) | (x << (64 - n)); }
'''
    stub = r'''
typedef struct {
    ARMPAuthMemoEntry pauth_memo[ARM_PAUTH_MEMO_MAX_ENTRIES];
    uint64_t pauth_memo_hits, pauth_memo_misses;
    unsigned prop_pauth_memo_entries;
    bool prop_pauth_memo_stats;
    bool prop_pauth_memo;
    unsigned test_algorithm;
} CPUARMState;
typedef CPUARMState ARMCPU;
#define qatomic_read(ptr) (*(ptr))
#define qatomic_set(ptr, value) (*(ptr) = (value))
#define env_archcpu(env) (env)
#define cpu_isar_feature(algorithm, cpu) ((cpu)->test_algorithm == (algorithm))
enum { aa64_pauth_qarma3 = 3, aa64_pauth_qarma5 = 5 };
static uint64_t slow_calls;
static uint64_t counted_architected(uint64_t data, uint64_t modifier, ARMPACKey key, bool qarma3)
{ slow_calls++; return new_pauth_computepac_architected(data, modifier, key, qarma3); }
static uint64_t counted_impdef(uint64_t data, uint64_t modifier, ARMPACKey key)
{ slow_calls++; return qemu_xxhash64_4(data, modifier, key.lo, key.hi); }
'''
    kat = primitives.original(primitives.KAT)
    body = re.search(rb'static const uint64_t d\[5\] = \{(.*?)\};', kat, re.S)[1]
    vector = [int(x, 16) for x in re.findall(rb'0x([0-9a-fA-F]+)', body)]
    assert len(vector) == 6 and vector[-1] == 0xffffffff00000000
    known = 'static const uint64_t known[5] = {' + ', '.join(f'UINT64_C(0x{x:016x})' for x in vector[:5]) + '};\n'
    generated = support + entry + '\n' + xxhash + primitives.algorithm(old, 'old_') + primitives.algorithm(new, 'new_') + stub + wrapper + known + (HERE / 'pac-memo-oracle.c').read_text()
    path = out / 'check.c'; path.write_text(generated)
    command = [args.cc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-fsanitize=undefined', '-fno-sanitize-recover=all', str(path), '-o', str(out / 'check')]
    result = {'recorded_at_utc': datetime.now(timezone.utc).isoformat(), 'base_commit': PIN,
              'source_sha256': {name: sha((SOURCE / name).read_bytes()) for name in ['target/arm/cpu.h', 'target/arm/cpu.c', 'target/arm/machine.c', primitives.HELPER]},
              'generated_sha256': sha(generated.encode()), 'compile_command': command,
              'scope': 'Synthetic native scalar-oracle checks of full 64-bit QARMA3/5 results for 64/1024/16384-entry memo, complete-slot fill/clear, aligned and high-slot reuse/collisions/input/key/algorithm/per-CPU/reset/off/impdef and optional aggregate-counter paths. Authentication tail is byte-identical after only the three VA-provider substitutions, whose complete field and PAC-consumer equivalence is checked separately by test-pac-va.py. Reset/migration clearing and QOM read-only registration checked structurally; actual CPU/QOM tests are separate.'}
    try:
        compiled = subprocess.run(command, text=True, capture_output=True, check=True)
        checked = subprocess.run([str(out / 'check')], text=True, capture_output=True, check=True)
        result.update(passed=True, checks=json.loads(checked.stdout), stderr=checked.stderr,
                      compiler_output=compiled.stdout + compiled.stderr)
    except subprocess.CalledProcessError as error:
        result.update(passed=False, failed_command=error.cmd, stdout=error.stdout, stderr=error.stderr)
        raise
    finally:
        (out / 'check').unlink(missing_ok=True)
        (out / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['checks'], indent=2))


if __name__ == '__main__':
    main()
