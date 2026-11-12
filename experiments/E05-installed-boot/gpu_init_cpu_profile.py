#!/usr/bin/env python3
"""Host CPU use of QEMU while Safari's first GPU connection initialised.

Joins three records of one trial:

  gpu-init-timing-TAG.json  from gpu_init_timing.py: guest log times of the events
  host-threads.tsv          from host_thread_sampler.py: on-CPU time of every QEMU thread
  clock-offset.jsonl        from clock_offset.py: the guest clock against the host clock

For the initialisation window, each of its segments and a few intervals around
it, the report says how many vCPU threads were on a host CPU on average, how
that splits over vCPUs and over host core types, how long vCPU threads waited
for a host CPU, and what the other QEMU threads did.

The sampler sees host threads, not guest processes: a busy vCPU may be running
anything in the guest. An idle guest vCPU sleeps in the host, so low numbers
mean the guest was waiting, not computing.
"""
import argparse
import bisect
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import clock_offset

VCPU = re.compile(r'^CPU (\d+)/TCG$')
SEGMENTS = (('window', 'ui_request', 'did_initialize'),
            ('request_to_gpu_entry', 'ui_request', 'gpu_entry'),
            ('gpu_entry_to_registrar', 'gpu_entry', 'registrar_activation'),
            ('registrar_to_helper_create', 'registrar_activation', 'helper_create'),
            ('helper_create_to_destroy', 'helper_create', 'helper_destroy'),
            ('helper_destroy_to_did_initialize', 'helper_destroy', 'did_initialize'),
            ('gpu_constructor_to_request', 'constructor', 'ui_request'))
AROUND_SECONDS = 10


class ProfileError(ValueError):
    pass


def load_samples(path):
    """Parse a sampler file into ticks and, per thread, the change seen at each tick."""
    header, threads, ticks, changes, last = {}, {}, [], {}, {}
    with open(path, encoding='utf-8') as stream:
        for line in stream:
            kind, _, rest = line.rstrip('\n').partition('\t')
            if kind == 'S':
                tid, run, wait, cpu = (int(value) for value in rest.split('\t'))
                if tid in last:
                    changes.setdefault(tid, []).append((len(ticks) - 1, run - last[tid][0], wait - last[tid][1], cpu))
                last[tid] = (run, wait)
            elif kind == 'K':
                ticks.append(int(rest))
            elif kind == 'T':
                tid, comm = rest.split('\t', 1)
                threads[int(tid)] = comm
            elif kind == 'X':
                last.pop(int(rest), None)
            elif kind == 'H':
                key, value = rest.split('\t', 1)
                header[key] = int(value)
    if 'start_realtime_ns' not in header or len(ticks) < 2:
        raise ProfileError('Sampler file has no start time or fewer than two ticks')
    return {'header': header, 'threads': threads, 'ticks': ticks, 'changes': changes}


def parse_cpu_list(text):
    cpus = set()
    for part in text.strip().split(','):
        if part:
            low, _, high = part.partition('-')
            cpus.update(range(int(low), int(high or low) + 1))
    return cpus


def overlaps(samples, start_us, end_us):
    """Yield (tick index, share of that tick interval inside [start, end], interval length)."""
    ticks = samples['ticks']
    first = max(1, bisect.bisect_right(ticks, start_us))
    for index in range(first, len(ticks)):
        low, high = ticks[index - 1], ticks[index]
        if low >= end_us:
            break
        inside = min(high, end_us) - max(low, start_us)
        if inside > 0 and high > low:
            yield index, inside / (high - low), high - low


def describe(samples, start_us, end_us, vcpu_tids, p_cores=None):
    """CPU use of every thread class between two times, in microseconds after the sampler start."""
    if end_us <= start_us:
        raise ProfileError('Interval ends before it starts')
    ticks, threads, pid = samples['ticks'], samples['threads'], samples['header'].get('pid')
    if start_us < ticks[0] or end_us > ticks[-1]:
        raise ProfileError('Interval is outside the sampled time')
    share = {index: (part, length) for index, part, length in overlaps(samples, start_us, end_us)}
    wall = (end_us - start_us) / 1e6
    run, wait, on_e, busy_ticks = {}, {}, 0.0, {}
    first, last = min(share), max(share)
    for tid, rows in samples['changes'].items():
        for index, run_us, wait_us, cpu in rows[bisect.bisect_left(rows, (first,)):]:
            if index > last:
                break
            part, length = share[index]
            run[tid] = run.get(tid, 0.0) + run_us * part
            wait[tid] = wait.get(tid, 0.0) + wait_us * part
            if tid in vcpu_tids:
                if p_cores is not None and cpu not in p_cores:
                    on_e += run_us * part
                if run_us >= 0.5 * length:
                    busy_ticks[index] = busy_ticks.get(index, 0) + 1
    vcpu_run = sum(run.get(tid, 0.0) for tid in vcpu_tids)
    histogram = [0.0] * (len(vcpu_tids) + 1)
    for index, (part, length) in share.items():
        histogram[min(busy_ticks.get(index, 0), len(vcpu_tids))] += part * length
    others = {}
    for tid, value in run.items():
        if tid in vcpu_tids or tid == pid:
            continue
        row = others.setdefault(threads.get(tid, '?'), {'threads': 0, 'on_cpu_seconds': 0.0})
        row['threads'] += 1
        row['on_cpu_seconds'] += value / 1e6
    gaps = [length for _part, length in share.values()]
    return {
        'wall_seconds': round(wall, 3),
        'vcpu_on_cpu_seconds': round(vcpu_run / 1e6, 3),
        'vcpu_mean_busy': round(vcpu_run / 1e6 / wall, 3),
        'vcpu_runqueue_wait_seconds': round(sum(wait.get(tid, 0.0) for tid in vcpu_tids) / 1e6, 3),
        'vcpu_e_core_share': None if p_cores is None or not vcpu_run else round(on_e / vcpu_run, 3),
        'per_vcpu_on_cpu_seconds': [round(run.get(tid, 0.0) / 1e6, 3) for tid in vcpu_tids],
        'vcpu_busy_count_share': [round(value / (end_us - start_us), 3) for value in histogram],
        'main_thread_on_cpu_seconds': round(run.get(pid, 0.0) / 1e6, 3),
        'other_threads': [{'comm': comm, **{k: round(v, 3) if k != 'threads' else v for k, v in row.items()}}
                          for comm, row in sorted(others.items(), key=lambda item: -item[1]['on_cpu_seconds'])[:6]],
        'all_threads_on_cpu_seconds': round(sum(run.values()) / 1e6, 3),
        'longest_tick_ms': round(max(gaps) / 1e3, 1) if gaps else None,
    }


def vcpu_threads(samples, results=None):
    """Host thread ids of the vCPUs in index order, from the controller's record or the thread names."""
    if results:
        rows = results.get('execution', {}).get('qemu_vcpu_threads', {}).get('vcpus') or []
        tids = [row['host_thread_id'] for row in sorted(rows, key=lambda row: row['cpu_index'])]
        if tids and all(tid in samples['threads'] for tid in tids):
            return tids
    named = sorted((int(match.group(1)), tid) for tid, comm in samples['threads'].items()
                   if (match := VCPU.match(comm)))
    if not named:
        raise ProfileError('No vCPU threads found')
    return [tid for _index, tid in named]


def profile(timing, samples, host_minus_guest, guest_utc_offset_hours, results=None, p_cores=None):
    events = (timing.get('first_gpu') or {}).get('events') or {}
    zone = timezone(timedelta(hours=guest_utc_offset_hours))
    start_ns = samples['header']['start_realtime_ns']

    def host_us(name):
        event = events.get(name)
        if not event:
            return None
        guest = datetime.fromisoformat(event['time']).replace(tzinfo=zone).timestamp()
        return ((guest + host_minus_guest) * 1e9 - start_ns) / 1e3

    tids = vcpu_threads(samples, results)
    intervals = {}
    for name, first, last in SEGMENTS:
        start, end = host_us(first), host_us(last)
        if start is not None and end is not None and end > start:
            intervals[name] = describe(samples, start, end, tids, p_cores)
    request, done = host_us('ui_request'), host_us('did_initialize')
    around = AROUND_SECONDS * 1e6
    for name, start, end in ((f'{AROUND_SECONDS}s_before_request', request and request - around, request),
                             (f'{AROUND_SECONDS}s_after_did_initialize', done, done and done + around)):
        try:
            if start is not None and end is not None:
                intervals[name] = describe(samples, start, end, tids, p_cores)
        except ProfileError:
            pass   # the sampler did not cover this neighbouring interval
    ticks = samples['ticks']
    return {
        'schema': 1, 'outcome': timing.get('outcome'),
        'host_minus_guest_seconds': host_minus_guest, 'guest_utc_offset_hours': guest_utc_offset_hours,
        'vcpu_host_thread_ids': tids, 'p_cores': sorted(p_cores) if p_cores is not None else None,
        'sampler': {'hz': samples['header'].get('hz'), 'ticks': len(ticks),
                    'seconds': round((ticks[-1] - ticks[0]) / 1e6, 1), 'threads_seen': len(samples['threads'])},
        'window_start_host_utc': None if request is None else datetime.fromtimestamp(
            (start_ns + request * 1e3) / 1e9, timezone.utc).isoformat(timespec='milliseconds'),
        'intervals': intervals,
    }


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--timing', required=True, help='gpu-init-timing-TAG.json of the trial')
    parser.add_argument('--samples', required=True, help='host_thread_sampler.py output of the run')
    clock = parser.add_mutually_exclusive_group(required=True)
    clock.add_argument('--clock', help='clock_offset.py record file')
    clock.add_argument('--host-minus-guest', type=float, help='Clock difference in seconds, if measured elsewhere')
    parser.add_argument('--clock-tag', help='Which probe of the record file to use; needed when it holds several')
    parser.add_argument('--results', help="The run's results.json, for the vCPU thread ids")
    parser.add_argument('--guest-utc-offset-hours', type=float, default=-8, help='Zone of the guest log times (default -8)')
    parser.add_argument('--p-cores', help='Host CPUs counted as performance cores, like 0-7; default from sysfs')
    parser.add_argument('--out', help='Also write the report to this new file')
    args = parser.parse_args(argv)
    try:
        inputs = {'timing': args.timing, 'samples': args.samples}
        if args.clock:
            estimates = clock_offset.estimate(clock_offset.read_rows(args.clock))
            if args.clock_tag is None and len(estimates) != 1:
                raise ProfileError(f'Clock record holds {len(estimates)} probes; choose one with --clock-tag')
            tag = args.clock_tag or next(iter(estimates))
            if tag not in estimates:
                raise ProfileError(f'No clock probe tagged {tag}')
            difference, inputs['clock'] = estimates[tag]['host_minus_guest_seconds'], args.clock
            clock_report = {'tag': tag, **estimates[tag]}
        else:
            difference, clock_report = args.host_minus_guest, {'source': 'command line'}
        cores = args.p_cores
        if cores is None and Path('/sys/devices/cpu_core/cpus').exists():
            cores = Path('/sys/devices/cpu_core/cpus').read_text()
        results = json.loads(Path(args.results).read_text()) if args.results else None
        report = profile(json.loads(Path(args.timing).read_text()), load_samples(args.samples), difference,
                         args.guest_utc_offset_hours, results, parse_cpu_list(cores) if cores else None)
        report = {'inputs': {name: {'path': str(path), 'sha256': sha256(path)} for name, path in inputs.items()},
                  'clock': clock_report, **report}
        rendered = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
        if args.out:
            with open(args.out, 'x', encoding='utf-8') as output:
                output.write(rendered)
    except (OSError, ValueError, KeyError) as error:
        print(f'GPU_INIT_CPU_PROFILE: {error}', file=sys.stderr)
        return 2
    sys.stdout.write(rendered)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
