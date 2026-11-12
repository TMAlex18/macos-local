#!/usr/bin/env python3
"""Offline CPU occupancy for a retained installed-boot run; reads evidence only.

Host side: per-interval deltas of the cumulative TIME column in `ps -L` blocks
(the %CPU column is a lifetime average and is not used). Guest side: tally of
the current-process samples, treating the most common kernel_task PC as the
idle loop. Both are ~30 s point samples, not traces.
"""
import argparse
import collections
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import statistics

VCPU = re.compile(r'CPU \d+/TCG')
GRAPHICS = ('reims-vgpu', 'metal2vulkan', '[vk')
HEADER = re.compile(r'=== (\S+) ===')


def utc(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def cputime_seconds(text):
    days, _, clock = text.rpartition('-')
    hours, minutes, seconds = (int(x) for x in clock.split(':'))
    return int(days or 0) * 86400 + hours * 3600 + minutes * 60 + seconds


def host_blocks(text):
    """Return [(timestamp, {(tid, comm): cpu_seconds})]; repeated headers are merged."""
    blocks = []
    for line in text.splitlines():
        header = HEADER.fullmatch(line.strip())
        if header:
            at = utc(header.group(1))
            if not blocks or blocks[-1][0] != at:
                blocks.append((at, {}))
            continue
        parts = line.split(None, 7)
        if blocks and len(parts) == 8 and parts[0].isdigit():
            blocks[-1][1][(parts[1], parts[7].strip())] = cputime_seconds(parts[6])
    return blocks


def host_intervals(blocks):
    intervals = []
    for (t0, before), (t1, after) in zip(blocks, blocks[1:]):
        wall = (t1 - t0).total_seconds()
        if wall <= 0:
            continue
        vcpu, graphics = [], 0.0
        for key, seconds in after.items():
            if key not in before:
                continue
            rate = (seconds - before[key]) / wall
            if VCPU.fullmatch(key[1]):
                vcpu.append(rate)
            elif key[1].startswith(GRAPHICS):
                graphics += rate
        if vcpu:
            intervals.append({'start': t0.isoformat(), 'end': t1.isoformat(), 'wall_seconds': wall,
                              'vcpu_threads': len(vcpu), 'vcpu_cores': round(sum(vcpu), 2),
                              'vcpu_max_fraction': round(max(vcpu), 2), 'graphics_cores': round(graphics, 2)})
    return intervals


def host_summary(intervals, lo, hi):
    inside = [i for i in intervals if lo <= utc(i['start']) and utc(i['end']) <= hi]
    if not inside:
        return {'intervals': 0}
    cores = [i['vcpu_cores'] for i in inside]
    return {'intervals': len(inside), 'vcpu_cores_min': min(cores), 'vcpu_cores_median': statistics.median(cores),
            'vcpu_cores_max': max(cores), 'vcpu_max_fraction_max': max(i['vcpu_max_fraction'] for i in inside),
            'graphics_cores_max': max(i['graphics_cores'] for i in inside)}


def guest_occupancy(rows, started, lo, hi, track=('Safari', 'WebKit'), top=14):
    pcs = collections.Counter(c.get('pc') for r in rows for c in r.get('cpus', []) if c.get('name') == 'kernel_task')
    idle_pc = pcs.most_common(1)[0][0] if pcs else None
    names, tracked = collections.Counter(), collections.defaultdict(list)
    samples = slots = idle = kernel_task = el0 = 0
    for row in rows:
        at = started + timedelta(seconds=row.get('elapsed_seconds', 0))
        if row.get('state') != 'sampled' or not lo <= at < hi:
            continue
        samples += 1
        for cpu in row.get('cpus', []):
            slots += 1
            name = cpu.get('name') or cpu.get('identity_state')
            if name == 'kernel_task':
                kernel_task += 1
                if cpu.get('pc') == idle_pc:
                    idle += 1
                    continue
            if (int(cpu.get('pstate', '0x0'), 16) >> 2) & 3 == 0:
                el0 += 1
            names[name] += 1
            if any(t in name for t in track):
                tracked[name].append([cpu.get('pid'), at.strftime('%H:%M:%S')])
    return {'samples': samples, 'cpu_slots': slots, 'idle_pc': idle_pc, 'idle_slots': idle,
            'kernel_task_slots_any_pc': kernel_task, 'busy_slots': slots - idle, 'busy_el0_slots': el0,
            'top_on_cpu': names.most_common(top), 'tracked': dict(tracked)}


def analyse(run_dir, lo=None, hi=None):
    results = json.loads((run_dir / 'results.json').read_text())
    started = utc(results['started_at_utc'])
    lo = lo or datetime.min.replace(tzinfo=timezone.utc)
    hi = hi or datetime.max.replace(tzinfo=timezone.utc)
    intervals = host_intervals(host_blocks((run_dir / 'host-qemu-thread-samples.log').read_text(errors='replace')))
    rows = [json.loads(line) for line in (run_dir / 'current-process.jsonl').read_text().splitlines() if line.strip()]
    return {'run': run_dir.name, 'window': [lo.isoformat(), hi.isoformat()],
            'host_window': host_summary(intervals, lo, hi),
            'guest_window': guest_occupancy(rows, started, lo, hi),
            'guest_before_window': guest_occupancy(rows, started, datetime.min.replace(tzinfo=timezone.utc), lo),
            'host_intervals': intervals}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--window', nargs=2, metavar=('START', 'END'), help='UTC ISO bounds, e.g. 2026-11-01T00:14:00Z')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    lo, hi = (utc(x) for x in args.window) if args.window else (None, None)
    text = json.dumps(analyse(args.run_dir, lo, hi), indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
