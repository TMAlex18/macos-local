#!/usr/bin/env python3
"""Summarise block_sampler.py output: running share, wait targets and stack hints.

    python3 block_report.py SAMPLES [--binary QEMU] [--from US] [--to US] [--json]
"""
from __future__ import annotations

import argparse
import bisect
import collections
from collections.abc import Iterable
import json
import subprocess
import sys

SYSCALLS = {
    0: 'read', 1: 'write', 3: 'close', 7: 'poll', 9: 'mmap', 10: 'mprotect',
    11: 'munmap', 14: 'rt_sigprocmask', 16: 'ioctl', 17: 'pread64',
    18: 'pwrite64', 19: 'readv', 20: 'writev', 24: 'sched_yield', 28: 'madvise',
    35: 'nanosleep', 46: 'sendmsg', 47: 'recvmsg', 74: 'fsync', 75: 'fdatasync',
    128: 'rt_sigtimedwait', 200: 'tkill', 202: 'futex', 230: 'clock_nanosleep',
    232: 'epoll_wait', 234: 'tgkill', 257: 'openat', 271: 'ppoll',
    281: 'epoll_pwait', 285: 'fallocate', 295: 'preadv', 296: 'pwritev',
    426: 'io_uring_enter',
}

LOCK_WRAPPER_SKIP = {
    'qemu_mutex_lock_impl',
    'bql_lock_impl',
    'qemu_cond_wait_impl',
    'qemu_mutex_unlock_impl',
    'qemu_cond_timedwait_impl',
}

SymbolSpan = tuple[int, int, str]


def parse_nm_output(text: str) -> tuple[list[SymbolSpan], list[SymbolSpan]]:
    data_syms: list[SymbolSpan] = []
    func_syms: list[SymbolSpan] = []
    for row in text.splitlines():
        parts = row.split()
        if len(parts) != 4:
            continue
        try:
            addr = int(parts[0], 16)
            size = int(parts[1], 16)
        except ValueError:
            continue
        kind, name = parts[2], parts[3]
        if kind in 'bBdD':
            data_syms.append((addr, size, name))
        elif kind in 'tTwW':
            func_syms.append((addr, size, name))
    data_syms.sort()
    func_syms.sort()
    return data_syms, func_syms


def load_nm_symbols(binary: str) -> tuple[list[SymbolSpan], list[SymbolSpan]]:
    proc = subprocess.run(
        ['nm', '-S', '--defined-only', binary],
        capture_output=True, text=True, check=True,
    )
    return parse_nm_output(proc.stdout)


def resolve_symbol(spans: list[SymbolSpan], offset: int, allow_entry: bool = True) -> str | None:
    if not spans or offset < 0 or offset >= spans[-1][0] + max(spans[-1][1], 1):
        return None
    idx = bisect.bisect_right(spans, (offset, float('inf'), '')) - 1
    if idx >= 0:
        start, size, name = spans[idx]
        low_ok = (start <= offset) if allow_entry else (start < offset)
        if low_ok and offset < start + max(size, 1):
            return name
    return None


def summarize_samples(
    lines: Iterable[str],
    data_symbols: list[SymbolSpan] = (),
    func_symbols: list[SymbolSpan] = (),
    start_us: int | None = None,
    end_us: int | None = None,
) -> dict[str, object]:
    data_spans = sorted(data_symbols)
    func_spans = sorted(func_symbols)
    has_data_table = bool(data_spans)
    has_func_table = bool(func_spans)

    header: dict[str, str] = {}
    threads: dict[int, str] = {}
    ticks: list[int] = []
    blocked: collections.Counter[str] = collections.Counter()
    per_thread: collections.Counter[int] = collections.Counter()
    futex_ops: collections.Counter[int] = collections.Counter()
    chains: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    running_total = 0

    for line in lines:
        f = line.split()
        if not f:
            continue
        if f[0] == 'H' and len(f) >= 3:
            header[f[1]] = f[2]
            continue
        if f[0] == 'T' and len(f) >= 3:
            threads[int(f[1])] = ' '.join(f[2:])
            continue
        if f[0] == 'R' and len(f) >= 4:
            us = int(f[1])
            if (start_us is None or us >= start_us) and (end_us is None or us <= end_us):
                ticks.append(us)
                running_total += int(f[2])
            continue

        stack_hex = ''
        if '|' in f:
            pipe = f.index('|')
            stack_hex = f[pipe + 1] if pipe + 1 < len(f) else ''
            f = f[:pipe]
        if len(f) < 3:
            continue
        us, tid, nr = int(f[0]), int(f[1]), int(f[2])
        if (start_us is not None and us < start_us) or (end_us is not None and us > end_us):
            continue

        base = int(header.get('base', '0'), 16)
        if nr == -1:
            key = 'outside a system call (page fault or similar)'
        elif nr == 202 and len(f) >= 5:
            address = int(f[3], 16)
            op = int(f[4], 16) & 0x7f
            futex_ops[op] += 1
            offset = address - base
            name = 'heap or stack'
            if has_data_table and 0 <= offset < data_spans[-1][0] + max(data_spans[-1][1], 1):
                matched = resolve_symbol(data_spans, offset)
                name = matched if matched is not None else f'binary+{offset:#x}'
            key = f'futex on {name}'
            if stack_hex and has_func_table:
                chain: list[str] = []
                for i in range(0, len(stack_hex) - 15, 16):
                    val = int.from_bytes(bytes.fromhex(stack_hex[i:i + 16]), 'little') - base
                    if val > 0:
                        # A return address points after a CALL instruction, never at offset 0
                        # of a function (which is instead a function pointer on the stack).
                        fn = resolve_symbol(func_spans, val, allow_entry=False)
                        if fn and fn not in LOCK_WRAPPER_SKIP and (not chain or chain[-1] != fn):
                            chain.append(fn)
                    if len(chain) >= 4:
                        break
                chains[name][' <- '.join(chain[:3])] += 1
        else:
            key = SYSCALLS.get(nr, f'syscall {nr}')
        blocked[key] += 1
        per_thread[tid] += 1

    n_ticks = len(ticks)
    n_threads = len(threads)
    total_samples = n_ticks * n_threads
    duration_s = (ticks[-1] - ticks[0]) / 1e6 if n_ticks >= 2 else 0.0

    return {
        'header': header,
        'tick_count': n_ticks,
        'duration_seconds': duration_s,
        'thread_count': n_threads,
        'threads': threads,
        'thread_samples': total_samples,
        'running_samples': running_total,
        'running_share': (running_total / total_samples) if total_samples else 0.0,
        'blocked_counts': dict(blocked.most_common()),
        'blocked_shares': {
            k: (v / total_samples if total_samples else 0.0)
            for k, v in blocked.most_common()
        },
        'per_thread_blocked_counts': {tid: per_thread[tid] for tid in sorted(threads)},
        'per_thread_blocked_shares': {
            threads[tid]: (per_thread[tid] / n_ticks if n_ticks else 0.0)
            for tid in sorted(threads)
        },
        'futex_ops': {op: futex_ops[op] for op in sorted(futex_ops)},
        'call_chains': {
            name: dict(counter.most_common())
            for name, counter in chains.items()
        },
        'scope': (
            'Syscall-state and futex-address attribution from /proc/PID/task/TID/syscall. '
            'Optional call chains come from raw stack-word scanning: the innermost symbol '
            'nearest the lock is reliable, while outer frames can include stale stack words.'
        ),
    }


def format_text_report(summary: dict[str, object]) -> str:
    n_ticks = int(summary['tick_count'])
    n_threads = int(summary['thread_count'])
    n = int(summary['thread_samples'])
    dur = float(summary['duration_seconds'])
    if n == 0:
        return f'{n_ticks} ticks over {dur:.1f} s, {n_threads} threads, 0 thread samples'

    out = [
        f'{n_ticks} ticks over {dur:.1f} s, {n_threads} threads, {n} thread samples',
        f"running or runnable: {float(summary['running_share']):6.1%}",
    ]
    blocked_counts: dict[str, int] = summary['blocked_counts']  # type: ignore[assignment]
    for key, count in list(blocked_counts.items())[:14]:
        out.append(f'{count / n:6.1%}  {key}')

    threads: dict[int, str] = summary['threads']  # type: ignore[assignment]
    per_tid: dict[int, int] = summary['per_thread_blocked_counts']  # type: ignore[assignment]
    parts = [
        f"{threads[t].split('/')[0].replace('CPU ', 'cpu')}={per_tid[t] / n_ticks:.0%}"
        for t in sorted(threads)
    ]
    out.append('blocked share per thread: ' + ' '.join(parts))
    out.append(f"futex ops (0 wait, 9 wait_bitset, 12 lock_pi): {summary['futex_ops']}")

    chains: dict[str, dict[str, int]] = summary['call_chains']  # type: ignore[assignment]
    for name, counter in chains.items():
        total = sum(counter.values())
        if total / n < 0.003:
            continue
        out.append(f'--- call chains of futex on {name} ({total / n:.1%} of thread samples):')
        for chain, count in list(counter.items())[:12]:
            out.append(f'   {count / n:6.2%}  {chain}')
    return '\n'.join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('samples')
    parser.add_argument('--binary')
    parser.add_argument('--from', dest='start', type=int)
    parser.add_argument('--to', dest='end', type=int)
    parser.add_argument('--json', action='store_true', help='Emit JSON summary')
    args = parser.parse_args(argv)

    data_syms: list[SymbolSpan] = []
    func_syms: list[SymbolSpan] = []
    if args.binary:
        data_syms, func_syms = load_nm_symbols(args.binary)

    with open(args.samples) as f:
        summary = summarize_samples(
            f, data_symbols=data_syms, func_symbols=func_syms,
            start_us=args.start, end_us=args.end,
        )

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(format_text_report(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
