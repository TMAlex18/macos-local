#!/usr/bin/env python3
"""Screen one isolated PAC inlining candidate on CPU19 using the existing oracle.

Only changes four pure helper declarations in generated source. The live
source, canonical binary, build outputs and build chain are never modified.
"""
import argparse
import ast
from datetime import datetime, timezone
import difflib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import statistics
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SOURCE = ROOT / 'sources/qemu-bdif-usb/target/arm/tcg/pauth_helper.c'
BASE_SHA = '908f118c779a0172be42d99ba46b0524408f43d390d0c953844cf87cfe25b00b'
HELPERS = ('pac_sub', 'pac_inv_sub', 'pac_cell_shuffle', 'pac_cell_inv_shuffle')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def annotate(text, prefix=''):
    for helper in HELPERS:
        before = f'static uint64_t {prefix}{helper}('
        after = f'static inline QEMU_ALWAYS_INLINE\nuint64_t {prefix}{helper}('
        if text.count(before) != 1:
            raise RuntimeError('Unexpected helper declaration: ' + helper)
        text = text.replace(before, after)
    return text


COST = r'''
#include <time.h>
typedef uint64_t (*pac_fn)(uint64_t, uint64_t, ARMPACKey, bool);
static double seconds(struct timespec a, struct timespec b)
{ return (b.tv_sec - a.tv_sec) + (b.tv_nsec - a.tv_nsec) * 1e-9; }
static void sample(unsigned round, const char *name, pac_fn fn, bool qarma3)
{
    struct timespec start, end, cpu_start, cpu_end;
    ARMPACKey key = { UINT64_C(0xec2802d4e0a488e9), UINT64_C(0x84be85ce9804e94b) };
    uint64_t value = UINT64_C(0xfb623599da6e8127);
    clock_gettime(CLOCK_MONOTONIC, &start);
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &cpu_start);
    for (unsigned i = 0; i < 20000; i++) {
        value = fn(value, UINT64_C(0x477d469dec0b8762) + i, key, qarma3);
    }
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &cpu_end);
    clock_gettime(CLOCK_MONOTONIC, &end);
    printf("{\"repeat\":%u,\"variant\":\"%s\",\"qarma\":%d,\"wall_seconds\":%.9f,"
           "\"thread_cpu_seconds\":%.9f,\"digest\":\"%016" PRIx64 "\"}\n",
           round, name, qarma3 ? 3 : 5, seconds(start, end), seconds(cpu_start, cpu_end), value);
}
int main(void)
{
    for (unsigned round = 0; round < 3; round++) {
        for (unsigned algorithm = 0; algorithm < 2; algorithm++) {
            if (round == 1) {
                sample(round, "candidate", new_pauth_computepac_architected, algorithm);
                sample(round, "current", old_pauth_computepac_architected, algorithm);
            } else {
                sample(round, "current", old_pauth_computepac_architected, algorithm);
                sample(round, "candidate", new_pauth_computepac_architected, algorithm);
            }
        }
    }
    return 0;
}
'''


def main():
    if os.sched_getaffinity(0) != {19}:
        raise SystemExit('Run with taskset -c 19')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = (args.output or ROOT / 'runs/e06-pauth-inline' / stamp).resolve()
    out.mkdir(parents=True, exist_ok=False)
    current = SOURCE.read_text()
    if sha(current.encode()) != BASE_SHA:
        raise SystemExit('Review the source baseline pin before screening a different source')
    candidate = annotate(current)
    if candidate.replace('static inline QEMU_ALWAYS_INLINE\nuint64_t ', 'static uint64_t ') != current:
        raise RuntimeError('Candidate changed function bodies')
    (out / 'candidate-pauth_helper.c').write_text(candidate)
    patch = ('From: macos-local offline PAuth screening\n'
             'Subject: [CANDIDATE, NOT IN BUILD CHAIN] Inline hot pure PAuth helpers\n'
             'Candidate-After: 0021-pauth-grouped-permutations.patch\n\n'
             'Use the existing optimization-only QEMU_ALWAYS_INLINE annotation for\n'
             'four pure helpers called repeatedly inside ComputePAC. Preserve every\n'
             'function body, algorithm choice, key operation and authentication rule.\n'
             'Native CPU19 screening only; actual QEMU consumer validation is pending.\n\n'
             'diff --git a/target/arm/tcg/pauth_helper.c b/target/arm/tcg/pauth_helper.c\n' +
             ''.join(difflib.unified_diff(current.splitlines(True), candidate.splitlines(True),
                 fromfile='a/target/arm/tcg/pauth_helper.c', tofile='b/target/arm/tcg/pauth_helper.c')))
    (out / 'candidate.patch').write_text(patch)
    oracle_path = HERE / 'test-pac-mult.py'
    spec = importlib.util.spec_from_file_location('existing_oracle', oracle_path)
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    original = oracle.original(oracle.HELPER).decode()
    support = next(node.value.value for node in ast.walk(ast.parse(oracle_path.read_text()))
                   if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                   and any(isinstance(target, ast.Name) and target.id == 'support' for target in node.targets))
    support = support.replace('static uint', 'static inline uint')
    compiler_header = ROOT / 'sources/qemu-bdif-usb/include/qemu/compiler.h'
    inline_macro = re.search(r'#if defined\(__OPTIMIZE__\)\n#define QEMU_ALWAYS_INLINE.*?#endif',
                             compiler_header.read_text(), re.S)[0]
    support += '\n' + inline_macro + '\n'
    candidate_algorithm = annotate(oracle.algorithm(current, 'new_'), 'new_')
    kat = oracle.original(oracle.KAT)
    body = re.search(rb'static const uint64_t d\[5\] = \{(.*?)\};', kat, re.S)[1]
    vector = [int(x, 16) for x in re.findall(rb'0x([0-9a-fA-F]+)', body)]
    if len(vector) != 6 or vector[-1] != 0xffffffff00000000:
        raise RuntimeError('Unexpected upstream known-answer shape')
    known = 'static const uint64_t known[5] = {' + ', '.join(
        f'UINT64_C(0x{x:016x})' for x in vector[:5]) + '};\n'
    harness = HERE / 'pac-mult-oracle.c'
    check_source = support + oracle.algorithm(original, 'old_') + candidate_algorithm + known + harness.read_text()
    cost_source = '#define _POSIX_C_SOURCE 200809L\n' + support + oracle.algorithm(current, 'old_') + candidate_algorithm + COST
    (out / 'check.c').write_text(check_source)
    (out / 'cost.c').write_text(cost_source)
    compile_entry = next(row for row in json.loads((ROOT / 'build/qemu-bdif-usb/compile_commands.json').read_text())
                         if row['file'].endswith('/target/arm/tcg/pauth_helper.c'))
    actual_flags = shlex.split(compile_entry['command'])
    flags = ['-std=gnu11', '-O2', '-m64', '-mcx16', '-msse2', '-fno-strict-aliasing',
             '-fno-common', '-fwrapv', '-ftrivial-auto-var-init=zero', '-fzero-init-padding-bits=all',
             '-fzero-call-used-regs=used-gpr', '-fstack-protector-strong', '-fPIE']
    if not all(flag in actual_flags for flag in flags):
        raise RuntimeError('Review changed production compiler flags before benchmarking')
    report = {'time_utc': stamp, 'host_affinity': sorted(os.sched_getaffinity(0)),
              'nice': os.getpriority(os.PRIO_PROCESS, 0), 'runner_sha256': sha(Path(__file__).read_bytes()),
              'current_helper_sha256': BASE_SHA, 'candidate_helper_sha256': sha(candidate.encode()),
              'original_helper_sha256': sha(original.encode()), 'original_base_commit': oracle.PIN,
              'function_bodies_unchanged': True, 'changed_declarations': list(HELPERS),
              'known_answer_source_sha256': sha(kat), 'existing_harness_sha256': sha(harness.read_bytes()),
              'existing_oracle_sha256': sha(oracle_path.read_bytes()),
              'compiler_header_sha256': sha(compiler_header.read_bytes()), 'inline_macro': inline_macro,
              'patch_sha256': sha(patch.encode()), 'check_source_sha256': sha(check_source.encode()),
              'cost_source_sha256': sha(cost_source.encode()),
              'production_compile_entry': compile_entry,
              'compiler_version': subprocess.check_output(['cc', '--version'], text=True),
              'compile_commands': [], 'scope': 'One native CPU19 candidate, unchanged function bodies. '
              'Production O2/hardening flags are identical for both variants. 20k dependent ComputePAC '
              'calls per sample, 3 AB/BA/AB pairs per algorithm under shared host load. Thread CPU and '
              'wall clocks are recorded; frequency/layout noise remains. No QEMU, GPU, live sampling, '
              'canonical rebuild or source-tree/build-chain edits. This is not P-core or OS evidence.'}
    try:
        for name, extra in [('check', ['-fsanitize=undefined', '-fno-sanitize-recover=all']), ('cost', [])]:
            executable = out / name
            command = ['cc', *flags, '-Wall', '-Wextra', '-Werror', *extra,
                       str(out / (name + '.c')), '-o', str(executable)]
            report['compile_commands'].append(command)
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True, timeout=15)
            (out / (name + '.stdout')).write_text(result.stdout)
            (out / (name + '.stderr')).write_text(result.stderr)
            report[name] = [json.loads(line) for line in result.stdout.splitlines()]
            if name == 'cost':
                symbols = subprocess.check_output(['nm', '-S', str(executable)], text=True)
                report['cost_symbols'] = [line for line in symbols.splitlines()
                                          if re.search(r'\b(?:old|new)_(?:pac_|pauth_computepac)', line)]
        report['native_cost_summary'] = {}
        for algorithm in [3, 5]:
            rows = [r for r in report['cost'] if r['qarma'] == algorithm]
            if len({r['digest'] for r in rows}) != 1:
                raise RuntimeError('Native cost workload digests differ')
            summary = {}
            for clock in ['wall_seconds', 'thread_cpu_seconds']:
                medians = {variant: statistics.median(r[clock] for r in rows if r['variant'] == variant)
                           for variant in ['current', 'candidate']}
                summary[clock] = {'median_seconds': medians,
                    'time_reduction_percent': 100 * (1 - medians['candidate'] / medians['current'])}
            report['native_cost_summary'][algorithm] = summary
        report['measured_native_seconds'] = sum(row['wall_seconds'] for row in report['cost'])
        report['source_tree_unchanged'] = sha(SOURCE.read_bytes()) == BASE_SHA
        if not report['source_tree_unchanged']:
            raise RuntimeError('Source changed during the probe')
        report['passed'] = True
        print(json.dumps(report['check'], indent=2))
        print(json.dumps(report['native_cost_summary'], indent=2))
    except Exception as error:
        report.update(passed=False, error=str(error))
        if isinstance(error, subprocess.CalledProcessError):
            report.update(stdout=error.stdout, stderr=error.stderr)
        raise
    finally:
        for name in ['check', 'cost']:
            (out / name).unlink(missing_ok=True)
        (out / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
        print('results', out / 'results.json')


if __name__ == '__main__':
    main()
