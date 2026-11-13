#!/usr/bin/env python3
"""Read and diff per-vCPU TCG BQL/exclusive trigger counters via inspect.sock.

    python3 sync_stats.py snapshot --run LABEL --out SNAPSHOT.json
    python3 sync_stats.py diff BEFORE.json AFTER.json [--out DIFF.json] [--json]
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from guest_ui import GuestUI, UIError

SCALAR_KEYS = (
    'exit_interrupt',
    'exit_hlt',
    'exit_halted',
    'exit_yield',
    'exit_atomic',
    'exit_debug',
    'exit_other',
    'bql_atomic_reacquire',
    'halt_cond_waits',
    'queued_work_batches',
    'queued_work_ordinary',
    'queued_work_exclusive',
    'bql_do_interrupt',
    'do_interrupt_svc',
    'do_interrupt_abort',
    'do_interrupt_irq',
    'do_interrupt_other',
    'bql_handle_interrupt',
    'bql_eret',
    'bql_cpreg_read',
    'bql_cpreg_write',
    'bql_mmio',
    'bql_ptw',
    'excl_tlbi_all',
    'excl_tlbi_page',
    'excl_tlbi_range',
    'excl_step_atomic',
    'excl_tb_flush',
)

OPTIONAL_SCALAR_KEYS = (
    'bql_exclusive_reacquire',
    'no_bql_exit_interrupt',
)

OUTER_EXIT_KEYS = (
    'exit_interrupt',
    'exit_hlt',
    'exit_halted',
    'exit_yield',
    'exit_atomic',
    'exit_debug',
    'exit_other',
)

BQL_SITE_KEYS = (
    'outer_loop_exit',
    'bql_atomic_reacquire',
    'queued_work_exclusive',
    'bql_do_interrupt',
    'bql_handle_interrupt',
    'bql_eret',
    'bql_cpreg_read',
    'bql_cpreg_write',
    'bql_mmio',
    'bql_ptw',
)

EXCLUSIVE_KEYS = (
    'excl_tlbi_all',
    'excl_tlbi_page',
    'excl_tlbi_range',
    'excl_step_atomic',
    'excl_tb_flush',
)


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise UIError(msg)


class SyncStatsClient(GuestUI):
    """Read-only QMP client restricted to query-cpus-fast and x-sync-stats-json."""

    def __init__(self, run: str):
        super().__init__(run)
        self.cpu_paths: set[str] = set()
        try:
            _require(self.args.count('-cpu') == 1, 'Missing fixed CPU configuration')
            cpu_tokens = self.args[self.args.index('-cpu') + 1].split(',')
            _require('x-sync-stats=on' in cpu_tokens,
                     'Selected run did not enable --sync-stats (x-sync-stats=on)')
        except Exception:
            self.journal.close()
            raise

    def _request(self, command: str, arguments=None, *, record: bool = True):
        allowed = command in ('qmp_capabilities', 'query-cpus-fast') and not arguments
        if command == 'qom-get':
            allowed = (
                isinstance(arguments, dict)
                and set(arguments) == {'path', 'property'}
                and arguments['path'] in self.cpu_paths
                and arguments['property'] == 'x-sync-stats-json'
            )
        _require(allowed, 'Operation is outside the read-only sync-stats allowlist')
        self.verify_process()
        self.verify_peer()
        _require(self.usable, 'Inspection QMP connection is unavailable')
        token = uuid.uuid4().hex
        if record:
            self.journal.write(
                kind='sync_stats', phase='before_send', request=token,
                command=command, arguments=dict(arguments or {}),
            )
        payload = {'execute': command, **({'arguments': arguments} if arguments else {})}
        self.sock.sendall((json.dumps(payload, separators=(',', ':')) + '\n').encode())
        while True:
            reply = self._read()
            if 'event' in reply:
                continue
            break
        if 'error' in reply:
            if record:
                self.journal.write(
                    kind='sync_stats', phase='after_reply', request=token,
                    command=command, status='qmp_rejected',
                )
            raise UIError('QMP rejected sync-stats read', failure_class='qmp_rejected')
        _require('return' in reply, 'Malformed QMP reply')
        if record:
            self.journal.write(
                kind='sync_stats', phase='after_reply', request=token,
                command=command, status='accepted',
            )
        return reply['return']


def validate_cpu_payload(raw_json: str) -> dict[str, object]:
    _require(isinstance(raw_json, str) and len(raw_json) <= 1024 * 1024,
             'Invalid x-sync-stats-json string')
    data = json.loads(raw_json)
    _require(isinstance(data, dict) and data.get('enabled') is True,
             'CPU sync-stats are not enabled')
    for key in SCALAR_KEYS:
        val = data.get(key)
        _require(type(val) is int and 0 <= val < 2**64, f'Invalid counter {key}')
    for key in OPTIONAL_SCALAR_KEYS:
        if key in data:
            val = data.get(key)
            _require(type(val) is int and 0 <= val < 2**64, f'Invalid counter {key}')
    for map_key in ('cpreg_io', 'tlbi', 'cpreg_non_io_helper'):
        mp = data.get(map_key)
        _require(isinstance(mp, dict), f'Invalid map {map_key}')
        for name, pair in mp.items():
            _require(
                isinstance(name, str)
                and isinstance(pair, list)
                and len(pair) == 2
                and all(type(x) is int and 0 <= x < 2**64 for x in pair),
                f'Invalid entry {name} in {map_key}',
            )
    return data


def take_snapshot(run: str) -> dict[str, object]:
    started_mono = time.monotonic()
    started_ns = time.time_ns()
    with SyncStatsClient(run) as client:
        rows = client._request('query-cpus-fast')
        count = int(client.args[client.args.index('-smp') + 1])
        _require(
            count in (2, 4, 8)
            and isinstance(rows, list)
            and len(rows) == count
            and {r.get('cpu-index') for r in rows} == set(range(count)),
            'Unexpected vCPU set',
        )
        paths = [r.get('qom-path') for r in rows]
        _require(
            all(isinstance(p, str) and re.fullmatch(r'/machine/[A-Za-z0-9_./\[\]-]{1,240}', p)
                and '..' not in p.split('/') for p in paths)
            and len(set(paths)) == count,
            'Invalid QOM CPU paths',
        )
        client.cpu_paths = set(paths)
        vcpus = []
        for row in sorted(rows, key=lambda r: r['cpu-index']):
            raw = client._request('qom-get', {
                'path': row['qom-path'],
                'property': 'x-sync-stats-json',
            })
            parsed = validate_cpu_payload(raw)
            vcpus.append({
                'cpu_index': row['cpu-index'],
                'qom_path': row['qom-path'],
                'stats': parsed,
            })
        ended_ns = time.time_ns()
        return {
            'schema': 1,
            'run': run,
            'qemu_pid': client.pid,
            'started_realtime_ns': started_ns,
            'ended_realtime_ns': ended_ns,
            'observed_at_utc': datetime.now(timezone.utc).isoformat(),
            'read_seconds': time.monotonic() - started_mono,
            'vcpus': vcpus,
            'scope': (
                'Read-only non-atomic per-vCPU QOM counter snapshot without pausing QEMU. '
                'Counts are event frequencies, not lock hold/wait durations.'
            ),
        }


def compute_diff(before: dict[str, object], after: dict[str, object]) -> dict[str, object]:
    _require(before.get('run') == after.get('run') and before.get('qemu_pid') == after.get('qemu_pid'),
             'Snapshots do not belong to the same QEMU session')
    b_cpus = {c['cpu_index']: c['stats'] for c in before['vcpus']}  # type: ignore[index]
    a_cpus = {c['cpu_index']: c['stats'] for c in after['vcpus']}  # type: ignore[index]
    _require(set(b_cpus) == set(a_cpus) and len(b_cpus) > 0, 'Mismatched vCPU index sets')

    totals = {k: 0 for k in SCALAR_KEYS + OPTIONAL_SCALAR_KEYS}
    has_optional = {
        k: all(k in b_cpus[idx] and k in a_cpus[idx] for idx in b_cpus)
        for k in OPTIONAL_SCALAR_KEYS
    }
    per_cpu_deltas = {}
    cpreg_io_totals: dict[str, list[int]] = {}
    tlbi_totals: dict[str, list[int]] = {}
    non_io_totals: dict[str, list[int]] = {}

    for idx in sorted(b_cpus):
        bs, as_ = b_cpus[idx], a_cpus[idx]
        cdelta = {}
        for k in SCALAR_KEYS:
            d = int(as_[k]) - int(bs[k])
            _require(d >= 0, f'Counter {k} decreased on CPU {idx} (reset occurred between snapshots)')
            cdelta[k] = d
            totals[k] += d
        for k in OPTIONAL_SCALAR_KEYS:
            if has_optional[k]:
                d = int(as_[k]) - int(bs[k])
                _require(d >= 0, f'Counter {k} decreased on CPU {idx} (reset occurred between snapshots)')
                cdelta[k] = d
                totals[k] += d
        per_cpu_deltas[idx] = cdelta

        for map_key, target_totals in (
            ('cpreg_io', cpreg_io_totals),
            ('tlbi', tlbi_totals),
            ('cpreg_non_io_helper', non_io_totals),
        ):
            bm = bs.get(map_key, {})
            am = as_.get(map_key, {})
            for name in set(bm) | set(am):
                br, bw = bm.get(name, [0, 0])
                ar, aw = am.get(name, [0, 0])
                dr, dw = int(ar) - int(br), int(aw) - int(bw)
                _require(dr >= 0 and dw >= 0, f'Map counter {name} decreased on CPU {idx}')
                if dr or dw:
                    acc = target_totals.setdefault(name, [0, 0])
                    acc[0] += dr
                    acc[1] += dw

    elapsed_s = (int(after['ended_realtime_ns']) - int(before['started_realtime_ns'])) / 1e9
    outer_exit_total = sum(totals[k] for k in OUTER_EXIT_KEYS)
    excl_bql_reacquire = (
        totals['bql_exclusive_reacquire']
        if has_optional['bql_exclusive_reacquire']
        else totals['queued_work_exclusive']
    )
    bql_sites = {
        'outer_loop_exit': outer_exit_total,
        **{
            k: (excl_bql_reacquire if k == 'queued_work_exclusive' else totals[k])
            for k in BQL_SITE_KEYS
            if k != 'outer_loop_exit'
        },
    }
    bql_total = sum(bql_sites.values())
    excl_sites = {k: totals[k] for k in EXCLUSIVE_KEYS}
    excl_total = sum(excl_sites.values())

    def sort_rw(mp: dict[str, list[int]]) -> dict[str, dict[str, int]]:
        items = sorted(mp.items(), key=lambda kv: (kv[1][0] + kv[1][1], kv[0]), reverse=True)
        return {
            k: {'reads': v[0], 'writes': v[1], 'total': v[0] + v[1]}
            for k, v in items
        }

    return {
        'run': before['run'],
        'qemu_pid': before['qemu_pid'],
        'elapsed_seconds': elapsed_s,
        'vcpu_count': len(b_cpus),
        'bql_acquisitions_total': bql_total,
        'bql_acquisitions_per_second': (bql_total / elapsed_s) if elapsed_s > 0 else 0.0,
        'bql_by_site': bql_sites,
        'bql_share_by_site': {
            k: (v / bql_total if bql_total else 0.0)
            for k, v in sorted(bql_sites.items(), key=lambda kv: kv[1], reverse=True)
        },
        'outer_exit_by_reason': {k: totals[k] for k in OUTER_EXIT_KEYS},
        'do_interrupt_by_kind': {
            'svc': totals['do_interrupt_svc'],
            'abort': totals['do_interrupt_abort'],
            'irq': totals['do_interrupt_irq'],
            'other': totals['do_interrupt_other'],
        },
        'queued_work': {
            'batches': totals['queued_work_batches'],
            'ordinary_items': totals['queued_work_ordinary'],
            'exclusive_items': totals['queued_work_exclusive'],
            'halt_cond_waits': totals['halt_cond_waits'],
            **(
                {'no_bql_exit_interrupt': totals['no_bql_exit_interrupt']}
                if has_optional['no_bql_exit_interrupt']
                else {}
            ),
        },
        'exclusive_requests_total': excl_total,
        'exclusive_requests_per_second': (excl_total / elapsed_s) if elapsed_s > 0 else 0.0,
        'exclusive_by_source': excl_sites,
        'exclusive_share_by_source': {
            k: (v / excl_total if excl_total else 0.0)
            for k, v in sorted(excl_sites.items(), key=lambda kv: kv[1], reverse=True)
        },
        'cpreg_io_by_register': sort_rw(cpreg_io_totals),
        'tlbi_by_instruction': sort_rw(tlbi_totals),
        'cpreg_non_io_helper_top': dict(list(sort_rw(non_io_totals).items())[:20]),
        'per_cpu_totals': per_cpu_deltas,
        'scope': (
            'Exact delta between two non-atomic QOM snapshots on the same running VM. '
            'Measures acquisition/request counts per trigger site, not lock hold/wait time.'
        ),
    }


def format_diff_text(diff: dict[str, object]) -> str:
    dur = float(diff['elapsed_seconds'])
    bql_tot = int(diff['bql_acquisitions_total'])
    excl_tot = int(diff['exclusive_requests_total'])
    out = [
        f"Window: {dur:.2f} s across {diff['vcpu_count']} vCPUs",
        f"Total vCPU BQL acquisitions: {bql_tot:,} ({float(diff['bql_acquisitions_per_second']):,.0f}/s)",
    ]
    bql_sites: dict[str, int] = diff['bql_by_site']  # type: ignore[assignment]
    for k, v in sorted(bql_sites.items(), key=lambda kv: kv[1], reverse=True):
        if v:
            pct = (100.0 * v / bql_tot) if bql_tot else 0.0
            out.append(f'  {pct:6.2f}%  {v:12,d}  {k}')
    out.append(f"  outer exits: {diff['outer_exit_by_reason']}")
    out.append(f"  do_interrupt kinds: {diff['do_interrupt_by_kind']}")
    out.append(f"  queued work: {diff['queued_work']}")

    out.append(f"Total exclusive requests: {excl_tot:,} ({float(diff['exclusive_requests_per_second']):,.1f}/s)")
    excl_sites: dict[str, int] = diff['exclusive_by_source']  # type: ignore[assignment]
    for k, v in sorted(excl_sites.items(), key=lambda kv: kv[1], reverse=True):
        if v:
            pct = (100.0 * v / excl_tot) if excl_tot else 0.0
            out.append(f'  {pct:6.2f}%  {v:12,d}  {k}')

    cpreg_io: dict[str, dict[str, int]] = diff['cpreg_io_by_register']  # type: ignore[assignment]
    if cpreg_io:
        out.append('Top ARM_CP_IO registers (BQL-locked):')
        cpreg_tot = sum(v['total'] for v in cpreg_io.values())
        for name, row in list(cpreg_io.items())[:15]:
            pct = (100.0 * row['total'] / cpreg_tot) if cpreg_tot else 0.0
            out.append(f"  {pct:6.2f}%  total={row['total']:11,d} (r={row['reads']:,}, w={row['writes']:,})  {name}")

    tlbi: dict[str, dict[str, int]] = diff['tlbi_by_instruction']  # type: ignore[assignment]
    if tlbi:
        out.append('TLBI helper instructions:')
        tlbi_tot = sum(v['total'] for v in tlbi.values())
        for name, row in list(tlbi.items())[:15]:
            pct = (100.0 * row['total'] / tlbi_tot) if tlbi_tot else 0.0
            out.append(f"  {pct:6.2f}%  writes={row['writes']:10,d}  {name}")

    return '\n'.join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_snap = sub.add_parser('snapshot')
    p_snap.add_argument('--run', required=True)
    p_snap.add_argument('--out', type=Path, required=True)

    p_diff = sub.add_parser('diff')
    p_diff.add_argument('before', type=Path)
    p_diff.add_argument('after', type=Path)
    p_diff.add_argument('--out', type=Path)
    p_diff.add_argument('--json', action='store_true')

    args = parser.parse_args(argv)
    try:
        if args.cmd == 'snapshot':
            if args.out.exists():
                raise FileExistsError(f'Refusing to overwrite {args.out}')
            snap = take_snapshot(args.run)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(snap, indent=2) + '\n')
            print(f"Saved {len(snap['vcpus'])}-vCPU sync snapshot to {args.out}")  # type: ignore[arg-type]
        elif args.cmd == 'diff':
            before = json.loads(args.before.read_text())
            after = json.loads(args.after.read_text())
            diff = compute_diff(before, after)
            if args.out:
                if args.out.exists():
                    raise FileExistsError(f'Refusing to overwrite {args.out}')
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(diff, indent=2) + '\n')
            if args.json:
                print(json.dumps(diff, indent=2))
            else:
                print(format_diff_text(diff))
    except (UIError, OSError, ValueError) as exc:
        print(f'SYNC_STATS: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
