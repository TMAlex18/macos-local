#!/usr/bin/env python3
"""Record the on-CPU time of every thread of one process, about 100 times a second.

The numbers come from /proc/PID/task/TID/schedstat, plus stat for the CPU a
busy thread last ran on. Reading them needs no privilege for a process of the
same user and does not touch that process. perf would give more, but this host
forbids it to ordinary users (perf_event_paranoid=4).

Output, one record per line, tab separated:

  H key value              pid, start_realtime_ns, hz
  T tid comm               a thread seen for the first time
  K us                     a sampling tick, microseconds after start_realtime_ns
  S tid run_us wait_us cpu a thread whose counters changed since its previous S
                           line: cumulative on-CPU time, cumulative time spent
                           runnable but waiting for a CPU, and the CPU it last
                           ran on. It belongs to the K line before it.
  X tid                    the thread is gone

A thread that did not run between two ticks writes nothing, so the time a
change happened is known to one tick.
"""
import argparse
import os
import signal
import sys
import time
from pathlib import Path


def pid_of(comm, proc_root='/proc'):
    """The only process of this user whose comm is exactly `comm`, or None."""
    found = []
    for entry in os.listdir(proc_root):
        if not entry.isdigit():
            continue
        try:
            path = Path(proc_root) / entry
            if path.stat().st_uid == os.geteuid() and (path / 'comm').read_text().strip() == comm:
                found.append(int(entry))
        except OSError:
            continue
    return found[0] if len(found) == 1 else None


class Sampler:
    def __init__(self, pid, write, proc_root='/proc'):
        self.task = Path(proc_root) / str(pid) / 'task'
        self.write = write
        self.files = {}   # tid -> (schedstat fd, stat fd)
        self.last = {}    # tid -> (run_ns, wait_ns)
        self.start_ns = None

    def scan(self):
        """Open the threads not seen before. False when the process is gone."""
        try:
            names = os.listdir(self.task)
        except FileNotFoundError:
            return False
        for name in names:
            tid = int(name)
            if tid in self.files:
                continue
            try:
                comm = (self.task / name / 'comm').read_text().strip()
                sched = os.open(self.task / name / 'schedstat', os.O_RDONLY)
            except OSError:
                continue
            try:
                stat = os.open(self.task / name / 'stat', os.O_RDONLY)
            except OSError:
                os.close(sched)
                continue
            self.files[tid] = (sched, stat)
            self.write(f'T\t{tid}\t{comm}\n')
        return True

    def forget(self, tid):
        for fd in self.files.pop(tid):
            os.close(fd)
        self.last.pop(tid, None)
        self.write(f'X\t{tid}\n')

    def tick(self, now_ns):
        if self.start_ns is None:
            self.start_ns = now_ns
        self.write(f'K\t{(now_ns - self.start_ns) // 1000}\n')
        for tid, (sched, stat) in list(self.files.items()):
            try:
                fields = os.pread(sched, 96, 0).split()
                counters = int(fields[0]), int(fields[1])
            except (OSError, IndexError, ValueError):
                self.forget(tid)
                continue
            if self.last.get(tid) == counters:
                continue
            self.last[tid] = counters
            cpu = -1
            try:
                # comm can hold spaces and brackets; the fields after the last ')' are fixed.
                # The first of them is field 3, so field 39 (processor) has index 36.
                cpu = int(os.pread(stat, 1024, 0).rsplit(b')', 1)[1].split()[36])
            except (OSError, IndexError, ValueError):
                pass
            self.write(f'S\t{tid}\t{counters[0] // 1000}\t{counters[1] // 1000}\t{cpu}\n')

    def close(self):
        for fds in self.files.values():
            for fd in fds:
                os.close(fd)
        self.files.clear()


def record(pid, out_path, hz=100, proc_root='/proc', stop=lambda: False, max_seconds=None):
    """Sample until the process is gone, `stop()` is true or `max_seconds` passed."""
    period = 1.0 / hz
    with open(out_path, 'x', buffering=1 << 20) as out:
        sampler = Sampler(pid, out.write, proc_root)
        sampler.start_ns = time.time_ns()
        out.write(f'H\tpid\t{pid}\nH\tstart_realtime_ns\t{sampler.start_ns}\nH\thz\t{hz}\n')
        started = next_tick = time.monotonic()
        next_scan = started
        ticks = 0
        try:
            while not stop() and (max_seconds is None or time.monotonic() - started < max_seconds):
                now = time.monotonic()
                if now >= next_scan:
                    if not sampler.scan():
                        break
                    next_scan = now + 0.5
                sampler.tick(time.time_ns())
                ticks += 1
                next_tick += period
                delay = next_tick - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_tick = time.monotonic()   # fell behind: skip ticks rather than burst
        finally:
            sampler.close()
    return ticks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--pid', type=int)
    target.add_argument('--wait-for', metavar='COMM', help='Wait up to --wait-seconds for the one process with this comm')
    parser.add_argument('--wait-seconds', type=float, default=120)
    parser.add_argument('--out', required=True, help='New file; an existing one is refused')
    parser.add_argument('--hz', type=int, default=100)
    args = parser.parse_args(argv)
    if not 1 <= args.hz <= 1000:
        parser.error('--hz must be between 1 and 1000')
    pid = args.pid
    if pid is None:
        deadline = time.monotonic() + args.wait_seconds
        while pid is None and time.monotonic() < deadline:
            pid = pid_of(args.wait_for)
            if pid is None:
                time.sleep(0.5)
        if pid is None:
            print(f'HOST_THREAD_SAMPLER: no single process named {args.wait_for}', file=sys.stderr)
            return 2
    stopping = []
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: stopping.append(True))
    try:
        ticks = record(pid, args.out, args.hz, stop=lambda: bool(stopping))
    except OSError as error:
        print(f'HOST_THREAD_SAMPLER: {error}', file=sys.stderr)
        return 2
    print(f'{ticks} ticks of pid {pid} written to {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
