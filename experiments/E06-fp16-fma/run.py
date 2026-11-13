#!/usr/bin/env python3
"""Check the float16 FMA fast paths of patch 0050 against an exact reference.

Copies f16_muladd_x86_f16c_try() and arm_gvec_fmla_idx_h_x86() out of the
patch, builds oracle.c around them with UBSan and requires 0 mismatches. A
control build with the midpoint fallback removed must report mismatches, so
the check is known to catch a rounding error. Host only: no QEMU, guest or
network. Needs gcc and an x86_64 CPU with F16C, FMA and AVX2; elsewhere the
fast paths are not compiled in and the check skips.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PATCH = ROOT / 'patches/qemu-platform/0050-f16-muladd-avx2-and-jmpcache-512k.patch'
BUILD = ROOT / 'build/e06-fp16-fma'
# First line of each block in the patch, and the file whose hunk holds it.
BLOCKS = (
    ('typedef struct F16FmaFastResult {', 'fpu/softfloat.c'),
    ('static inline bool arm_f16_normal_or_zero(uint16_t h)', 'target/arm/tcg/vec_helper.c'),
)
GUARD = 'low42 == (1ULL << 41)'


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract(patch_text: str) -> str:
    """Return the two fast-path blocks of the patch without their '+' prefix."""
    lines = patch_text.splitlines()
    out = []
    for first, source in BLOCKS:
        current, start = None, None
        for i, line in enumerate(lines):
            if line.startswith('diff --git '):
                current = line.split(' b/', 1)[1]
            elif current == source and line == '+' + first:
                start = i
                break
        if start is None:
            raise SystemExit(f'{first!r} not found in the {source} part of {PATCH.name}')
        block = []
        for line in lines[start:]:
            if not line.startswith('+'):
                raise SystemExit(f'block {first!r} ends before its #endif')
            if line == '+#endif':
                break
            block.append(line[1:])
        out.append(f'/* Copied by run.py from {PATCH.name} ({source}). */\n'
                   + '\n'.join(block) + '\n')
    return '\n'.join(out)


def cpu_supported() -> bool:
    if platform.machine() not in ('x86_64', 'AMD64'):
        return False
    try:
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('flags'):
                return {'f16c', 'fma', 'avx2'} <= set(line.split(':', 1)[1].split())
    except OSError:
        pass
    return False


def build(header: str, name: str) -> Path:
    directory = BUILD / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'fastpath.h').write_text(header)
    exe = directory / 'oracle'
    subprocess.run(['gcc', '-std=gnu11', '-O2', '-g', '-Wall', '-Wno-unused-function',
                    '-fsanitize=undefined', '-fno-sanitize-recover=all',
                    '-I', str(directory), str(HERE / 'oracle.c'), '-o', str(exe)],
                   check=True)
    return exe


def run_oracle(exe: Path, cases: int, seed: int) -> tuple[dict, str]:
    done = subprocess.run([str(exe), str(cases), str(seed)], check=True,
                          capture_output=True, text=True)
    return json.loads(done.stdout), done.stderr


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--cases', type=int, default=4_000_000,
                        help='scalar operand triples; vector calls are a quarter of this')
    parser.add_argument('--seed', type=int, default=20261113)
    parser.add_argument('--output', type=Path,
                        help='results directory (default: runs/e06-fp16-fma/<UTC time>)')
    args = parser.parse_args(argv)
    if args.cases < 1000:
        parser.error('--cases must be at least 1000')
    if not cpu_supported():
        print('SKIP: needs an x86_64 CPU with F16C, FMA and AVX2')
        return 0

    patch_bytes = PATCH.read_bytes()
    header = extract(patch_bytes.decode())
    if header.count(GUARD) != 2:
        raise SystemExit(f'expected the midpoint guard twice in the copied code, '
                         f'found {header.count(GUARD)}')
    control_header = header.replace(GUARD, '0')

    real, real_log = run_oracle(build(header, 'patch'), args.cases, args.seed)
    control, _ = run_oracle(build(control_header, 'control-no-midpoint-fallback'),
                            args.cases, args.seed)
    checks = {
        'scalar_matches_reference': real['scalar']['mismatches'] == 0,
        'vector_matches_reference': (real['vector']['mismatches'] == 0
                                     and real['vector']['flag_mismatches'] == 0),
        'fast_paths_exercised': (real['scalar']['hits'] > 0
                                 and real['vector']['fast_lanes'] > 0),
        'fallbacks_exercised': (real['scalar']['fallbacks'] > 0
                                and real['vector']['fallback_lanes'] > 0),
        'other_status_never_takes_fast_path': (real['disabled']['scalar_hits'] == 0
                                               and real['disabled']['vector_fast_lanes'] == 0),
        'control_scalar_caught': control['scalar']['mismatches'] > 0,
        'control_vector_caught': control['vector']['mismatches'] > 0,
    }
    passed = all(checks.values())

    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    outdir = args.output or ROOT / 'runs/e06-fp16-fma' / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    compiler = subprocess.run(['gcc', '--version'], capture_output=True,
                              text=True).stdout.splitlines()[0]
    result = {
        'schema': 1,
        'started_utc': stamp,
        'passed': passed,
        'checks': checks,
        'patch': str(PATCH.relative_to(ROOT)),
        'patch_sha256': sha256(patch_bytes),
        'copied_code_sha256': sha256(header.encode()),
        'compiler': compiler,
        'compile_flags': '-std=gnu11 -O2 -fsanitize=undefined -fno-sanitize-recover=all',
        'cases': args.cases,
        'seed': args.seed,
        'patch_build': real,
        'control_build_without_midpoint_fallback': control,
        'scope': ('Host-only check of the two fast paths as written in the patch, with the QEMU '
                  'types they use stubbed. It does not run QEMU, the softfloat fallback or the '
                  'start-up self-test, and covers round-to-nearest-even results in the normal '
                  'float16 range, which is all the fast paths return.'),
    }
    (outdir / 'results.json').write_text(json.dumps(result, indent=2) + '\n')

    s, v = real['scalar'], real['vector']
    print(f"patch: scalar {s['hits']} fast / {s['fallbacks']} fallback, "
          f"{s['mismatches']} mismatches; vector {v['fast_lanes']} fast lanes / "
          f"{v['fallback_lanes']} fallback lanes, {v['mismatches']} mismatches, "
          f"{v['flag_mismatches']} flag mismatches")
    print(f"control without the midpoint fallback: scalar {control['scalar']['mismatches']} "
          f"and vector {control['vector']['mismatches']} mismatches (must be > 0)")
    if real_log:
        print(real_log, end='')
    for name, ok in checks.items():
        if not ok:
            print(f'FAIL: {name}')
    shown = outdir.relative_to(ROOT) if outdir.is_relative_to(ROOT) else outdir
    print(('PASS' if passed else 'FAIL') + f': {shown}/results.json')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
