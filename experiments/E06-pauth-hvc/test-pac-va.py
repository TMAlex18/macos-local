#!/usr/bin/env python3
"""Check the PAC VA projection against the pinned full helper using real headers.

The full reference and candidate are separate translation units, compiled with
the production helper flags (plus UBSan for correctness). No VM, Apple code or
live keys are read. The selected source bodies and commands stay in the result.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SOURCE = ROOT / 'sources/qemu-bdif-usb'
BUILD = ROOT / 'build/qemu-bdif-usb'
PIN = '1a72df984914fb9929d35b1217619178133d65a4'
HEADERS = '#include "qemu/osdep.h"\n#include "cpu.h"\n#include "internals.h"\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def original(path):
    return subprocess.check_output(['git', '-C', str(SOURCE), 'show', f'{PIN}:{path}']).decode()


def function(source, name):
    match = re.search(r'^.*\b' + re.escape(name) + r'\([^;]*?\)\n\{', source, re.M)
    if match is None:
        raise ValueError('Function not found: ' + name)
    start, pos = match.start(), match.end() - 1
    depth = 1
    end = pos + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


def production_flags():
    commands = json.loads((BUILD / 'compile_commands.json').read_text())
    item = next(x for x in commands if x['file'].endswith('/target/arm/helper.c'))
    words = shlex.split(item['command'])
    result = []
    skip = False
    for word in words:
        if skip:
            skip = False
            continue
        if word in ('-o', '-MF', '-MQ', '-MT'):
            skip = True
        elif word in ('-c', '-MD', '-MMD') or word == item['file']:
            continue
        else:
            result.append(word)
    return result


def generate(out):
    old = original('target/arm/helper.c')
    new = (SOURCE / 'target/arm/helper.c').read_text()
    for name in ('target/arm/cpu-features.h', 'target/arm/mmuidx-internal.h'):
        assert (SOURCE / name).read_text() == original(name)
    internals = (SOURCE / 'target/arm/internals.h').read_text()
    pinned_internals = original('target/arm/internals.h')
    for name in ('regime_tcr', 'pauth_ptr_mask'):
        assert function(internals, name) == function(pinned_internals, name)
    old_fn = function(old, 'aa64_va_parameters')
    impl = function(new, 'aa64_va_parameters_impl')
    # Only the permission/attribute metadata block may be omitted by the PAC
    # projection. Keep every original rule and the full-MMU path literal.
    guard = '''        /* PAuth does not consume permission/attribute indirection metadata. */
        if (!pauth_only
            && (!arm_feature(env, ARM_FEATURE_EL3)'''
    assert impl.count(guard) == 1
    full_body = impl.replace(guard, '        if ((!arm_feature(env, ARM_FEATURE_EL3)')
    assert old_fn[old_fn.index('{'):] == full_body[full_body.index('{'):]
    dependencies = ['aa64_va_parameter_tbi', 'aa64_va_parameter_tbid',
                    'tg0_to_gran_size', 'tg1_to_gran_size', 'have4k',
                    'have16k', 'have64k', 'sanitize_gran_size']
    for name in dependencies:
        assert function(old, name) == function(new, name)
    statics = ''.join(function(old, name) for name in dependencies[2:])
    shared = ''.join(function(old, name) for name in dependencies[:2])
    for name in ('arm_security_space_below_el3', 'arm_hcrx_el2_eff'):
        assert function(old, name) == function(new, name)
        shared += function(new, name)
    mmuidx = (SOURCE / 'target/arm/mmuidx.c').read_text()
    assert mmuidx == original('target/arm/mmuidx.c')
    reference = HEADERS + statics + old_fn.replace('aa64_va_parameters(', 'reference_va(')
    candidate = (HEADERS + statics + 'static inline QEMU_ALWAYS_INLINE\n' +
                 impl + '\n' + function(new, 'aa64_va_parameters') + '\n' +
                 function(new, 'aa64_pauth_va_parameters'))
    # The unchanged PAC consumers are compiled twice with only the parameter
    # provider different. Their exception branch is intercepted, not executed
    # on the host, so both returned bits and fault outcomes can be compared.
    pauth = (SOURCE / 'target/arm/tcg/pauth_helper.c').read_text()
    pinned_pauth = original('target/arm/tcg/pauth_helper.c')
    tail_start, tail_end = 'static uint64_t pauth_addpac(', 'static G_NORETURN\nvoid pauth_trap('
    pinned_tail = pinned_pauth[pinned_pauth.index(tail_start):pinned_pauth.index(tail_end)]
    tail = pauth[pauth.index(tail_start):pauth.index(tail_end)]
    fast = 'aa64_pauth_va_parameters(env, ptr, mmu_idx, data)'
    full = 'aa64_va_parameters(env, ptr, mmu_idx, data, false)'
    assert tail.count(fast) == 3 and tail.replace(fast, full) == pinned_tail
    algorithm = pinned_pauth[pinned_pauth.index('static uint64_t pac_cell_shuffle('):
                             pinned_pauth.index(tail_start)]
    # Keep the actual pinned scalar crypto and all feature selection. The
    # public xxhash header supplies its implementation-defined algorithm.
    consumers = HEADERS + '#include "qemu/xxhash.h"\n#include <setjmp.h>\n'
    consumers += 'extern ARMVAParameters reference_va(CPUARMState *, uint64_t, ARMMMUIdx, bool, bool);\n'
    consumers += 'static ARMMMUIdx selected_mmu;\nstatic jmp_buf trap_env;\n'
    consumers += 'static unsigned trap_value;\n'
    consumers += algorithm
    for prefix, body in [('old_', pinned_tail), ('new_', tail)]:
        body = body.replace('arm_stage1_mmu_idx(env)', 'selected_mmu')
        body = body.replace('aa64_va_parameters(env, ptr, mmu_idx, data, false)',
                            'reference_va(env, ptr, mmu_idx, data, false)')
        fail = function(body, 'pauth_fail_exception')
        body = body.replace(fail.strip(), '''void pauth_fail_exception(CPUARMState *env, bool data,
                          int keynumber, uintptr_t ra)
{
    trap_value = 1 + data + 2 * keynumber;
    longjmp(trap_env, 1);
}''')
        names = ['pauth_addpac', 'pauth_original_ptr', 'pauth_fail_exception',
                 'pauth_auth', 'pauth_strip']
        body = re.sub(r'\b(' + '|'.join(names) + r')\b', lambda m: prefix + m[0], body)
        consumers += body
    consumers += r'''
uint64_t check_pac_consumer(CPUARMState *env, uint64_t ptr, uint64_t modifier,
                            ARMPACKey *key, ARMMMUIdx idx, unsigned operation,
                            bool candidate, bool data, unsigned *fault);
uint64_t check_pac_consumer(CPUARMState *env, uint64_t ptr, uint64_t modifier,
                            ARMPACKey *key, ARMMMUIdx idx, unsigned operation,
                            bool candidate, bool data, unsigned *fault)
{
    selected_mmu = idx;
    trap_value = 0;
    uint64_t value = 0;
    if (!setjmp(trap_env)) {
        if (operation == 0) {
            value = candidate ? new_pauth_addpac(env, ptr, modifier, key, data)
                              : old_pauth_addpac(env, ptr, modifier, key, data);
        } else if (operation == 1) {
            value = candidate ? new_pauth_strip(env, ptr, data)
                              : old_pauth_strip(env, ptr, data);
        } else {
            value = candidate ? new_pauth_auth(env, ptr, modifier, key, data, 1, 0, operation == 3)
                              : old_pauth_auth(env, ptr, modifier, key, data, 1, 0, operation == 3);
        }
    }
    *fault = trap_value;
    return value;
}
'''
    files = {'reference.c': reference, 'candidate.c': candidate,
             'shared.c': HEADERS + shared, 'mmuidx.c': mmuidx,
             'consumers.c': consumers,
             'oracle.c': (HERE / 'pac-va-oracle.c').read_text()}
    for name, text in files.items():
        (out / name).write_text(text)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--benchmark', action='store_true')
    parser.add_argument('--benchmark-only', action='store_true',
                        help='Cost-only rerun after an unchanged-source equivalence result')
    parser.add_argument('--equivalence-record', type=Path,
                        help='Required successful unchanged-source oracle for --benchmark-only')
    parser.add_argument('--cpu', type=int, help='Pin only the benchmark child to an allowed CPU')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    files = generate(out)
    flags = production_flags()
    libraries = shlex.split(subprocess.check_output(['pkg-config', '--libs', 'glib-2.0'], text=True))
    record = {'started_at_utc': datetime.now(timezone.utc).isoformat(), 'base_commit': PIN,
              'source_sha256': {name: sha((SOURCE / name).read_bytes()) for name in
                  ['target/arm/helper.c', 'target/arm/internals.h', 'target/arm/tcg/pauth_helper.c',
                   'target/arm/cpu.h', 'target/arm/cpu-features.h', 'target/arm/mmuidx-internal.h']},
              'generated_sha256': {name: sha(text.encode()) for name, text in files.items()},
              'commands': [], 'passed': False,
              'scope': 'Real QEMU headers/ISA predicates/regime table; pinned full helper oracle, shared-body identity, full MMU-field and PAC projection equivalence, selected-MMU PAC consumer outputs/fault outcomes. Synthetic inputs only; actual guest instruction execution and OS behavior are separate.'}
    if args.benchmark_only:
        if args.equivalence_record is None:
            raise ValueError('--benchmark-only requires --equivalence-record')
        prior_path = args.equivalence_record.resolve()
        prior = json.loads(prior_path.read_text())
        assert prior['passed'] and prior['checks']['field_cases'] > 0
        assert prior['source_sha256'] == record['source_sha256']
        for name in files.keys() - {'oracle.c'}:
            assert prior['generated_sha256'][name] == record['generated_sha256'][name]
        split = '#else\ntypedef ARMVAParameters (*va_fn)'
        assert (prior_path.parent / 'oracle.c').read_text().split(split)[0] == files['oracle.c'].split(split)[0]
        record['equivalence_reused'] = {'path': str(prior_path), 'sha256': sha(prior_path.read_bytes()),
                                        'source_and_correctness_inputs_equal': True}
        record['scope'] = 'Cost-only run with unchanged production sources and correctness inputs matched to a completed oracle record; no new equivalence or OS result.'
    def command(cmd):
        record['commands'].append(cmd)
        return subprocess.run(cmd, cwd=BUILD, check=True, capture_output=True, text=True)
    try:
        if not args.benchmark_only:
            objects = []
            for name in files:
                obj = out / (name + '.o'); objects.append(str(obj))
                command([*flags, '-Wno-unused-parameter', '-ffunction-sections', '-fdata-sections',
                         '-fsanitize=undefined', '-fno-sanitize-recover=all', '-c', str(out / name), '-o', str(obj)])
            binary = out / 'check'
            command([flags[0], *objects, '-Wl,--gc-sections', '-fsanitize=undefined', *libraries, '-o', str(binary)])
            check = command([str(binary)])
            record['checks'] = json.loads(check.stdout)
            record['check_stderr'] = check.stderr
        if args.benchmark or args.benchmark_only:
            for name in files:
                if name == 'consumers.c':
                    continue
                command([*flags, '-DPAC_VA_BENCH', '-ffunction-sections', '-fdata-sections', '-c',
                         str(out / name), '-o', str(out / (name + '.o'))])
            bench = out / 'bench'
            command([flags[0], *[str(out / (x + '.o')) for x in files if x != 'consumers.c'],
                     '-Wl,--gc-sections', *libraries, '-o', str(bench)])
            cpu = args.cpu if args.cpu is not None else min(os.sched_getaffinity(0))
            if cpu not in os.sched_getaffinity(0):
                raise ValueError('Benchmark CPU is outside the allowed affinity')
            record['benchmark_cpu'] = cpu
            data = command(['taskset', '-c', str(cpu), str(bench)])
            record['benchmark'] = [json.loads(line) for line in data.stdout.splitlines()]
            record['benchmark_scope'] = 'Same production compiler flags, synthetic VA calls, alternating order, monotonic/thread CPU clocks; not OS throughput or watchdog evidence.'
        record['passed'] = True
    except subprocess.CalledProcessError as error:
        record.update(failed_command=error.cmd, stdout=error.stdout, stderr=error.stderr)
        raise
    finally:
        for path in out.glob('*.o'):
            path.unlink()
        for name in ('check', 'bench'):
            (out / name).unlink(missing_ok=True)
        (out / 'results.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'passed': record['passed'], 'checks': record.get('checks'),
                      'benchmark': record.get('benchmark'), 'report': str(out / 'results.json')}))


if __name__ == '__main__':
    main()
