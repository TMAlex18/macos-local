#!/usr/bin/env python3
"""Sample where a process's matching threads block via /proc/PID/task/TID/syscall.

    sudo -n python3 block_sampler.py --pid PID --comm-prefix 'CPU ' --seconds 40 --out FILE

Reading /proc/PID/task/TID/syscall requires ptrace read permission (root when
Yama or dumpable flags guard the target), and does not stop the sampled threads.
One output line is written per non-running thread sample:

    us tid nr a0 a1 a2 a3 a4 a5 sp pc [| stack_hex]   (blocked in a system call)
    us tid -1 sp pc [| stack_hex]                     (blocked outside a system call)

Running threads contribute only to the tick summary line `R us n_running n_threads`.
Header lines (`H ...`) record pid, start_realtime_ns, exe and PIE load base so
futex addresses and stack words can be mapped back to executable symbols offline.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sys
import time


def find_load_base(pid: int, exe: str, proc_root: str | Path = '/proc') -> int:
    maps_path = Path(proc_root) / str(pid) / 'maps'
    try:
        lines = maps_path.read_text().splitlines()
    except OSError:
        return 0
    for line in lines:
        fields = line.split()
        if len(fields) >= 6 and fields[5] == exe and fields[2] == '00000000':
            try:
                return int(fields[0].split('-')[0], 16)
            except ValueError:
                return 0
    return 0


class BlockSampler:
    def __init__(self, pid: int, write, comm_prefix: str = 'CPU ',
                 stack_words: int = 0, proc_root: str | Path = '/proc'):
        self.pid = pid
        self.proc_root = Path(proc_root)
        self.task_dir = self.proc_root / str(pid) / 'task'
        self.write = write
        self.comm_prefix = comm_prefix
        self.stack_words = stack_words
        self.files: dict[int, tuple[str, int]] = {}
        self.mem_fd: int | None = None
        self.exe = ''
        self.base = 0
        self.start_ns: int | None = None

    def open_target(self) -> int:
        if not self.task_dir.is_dir():
            return 0
        names = [n for n in os.listdir(self.task_dir) if n.isdigit()]
        for name in sorted(names, key=int):
            tid = int(name)
            try:
                comm = (self.task_dir / name / 'comm').read_text().strip()
            except OSError:
                continue
            if not comm.startswith(self.comm_prefix):
                continue
            try:
                fd = os.open(self.task_dir / name / 'syscall', os.O_RDONLY)
            except OSError:
                continue
            self.files[tid] = (comm, fd)
        if not self.files:
            return 0
        try:
            self.exe = os.readlink(self.proc_root / str(self.pid) / 'exe')
        except OSError:
            self.exe = ''
        self.base = find_load_base(self.pid, self.exe, self.proc_root) if self.exe else 0
        if self.stack_words > 0:
            self.mem_fd = os.open(self.proc_root / str(self.pid) / 'mem', os.O_RDONLY)
        return len(self.files)

    def write_header(self, start_ns: int) -> None:
        self.start_ns = start_ns
        self.write(
            f'H pid {self.pid}\n'
            f'H start_realtime_ns {start_ns}\n'
            f'H exe {self.exe}\n'
            f'H base {self.base:#x}\n'
        )
        for tid in sorted(self.files):
            comm, _fd = self.files[tid]
            self.write(f'T {tid} {comm}\n')

    def tick(self, now_ns: int) -> bool:
        if self.start_ns is None:
            self.start_ns = now_ns
        us = (now_ns - self.start_ns) // 1000
        running = 0
        alive = 0
        for tid, (_comm, fd) in list(self.files.items()):
            try:
                text = os.pread(fd, 256, 0).decode('ascii', errors='replace').strip()
            except OSError:
                continue
            if not text:
                continue
            alive += 1
            if text == 'running':
                running += 1
                continue
            stack = ''
            if self.mem_fd is not None and self.stack_words > 0:
                try:
                    sp = int(text.split()[-2], 16)
                    raw_stack = os.pread(self.mem_fd, 8 * self.stack_words, sp)
                    if raw_stack:
                        stack = ' | ' + raw_stack.hex()
                except (OSError, ValueError, IndexError):
                    pass
            self.write(f'{us} {tid} {text}{stack}\n')
        if alive == 0:
            return False
        self.write(f'R {us} {running} {len(self.files)}\n')
        return True

    def close(self) -> None:
        for _comm, fd in self.files.values():
            try:
                os.close(fd)
            except OSError:
                pass
        self.files.clear()
        if self.mem_fd is not None:
            try:
                os.close(self.mem_fd)
            except OSError:
                pass
            self.mem_fd = None


def restore_sudo_owner(path: str | Path) -> None:
    uid_s = os.environ.get('SUDO_UID')
    gid_s = os.environ.get('SUDO_GID')
    if uid_s is None and gid_s is None:
        return
    try:
        uid = int(uid_s) if uid_s is not None else -1
        gid = int(gid_s) if gid_s is not None else -1
        os.chown(path, uid, gid)
    except (OSError, ValueError):
        pass


def record(pid: int, out_path: str | Path, comm_prefix: str = 'CPU ',
           seconds: float = 40.0, hz: int = 100, stack_words: int = 0,
           proc_root: str | Path = '/proc', stop=lambda: False) -> int:
    period = 1.0 / hz
    sampler = BlockSampler(
        pid, lambda _s: None, comm_prefix=comm_prefix,
        stack_words=stack_words, proc_root=proc_root,
    )
    try:
        if sampler.open_target() == 0:
            raise LookupError(f'no matching threads with prefix {comm_prefix!r} in pid {pid}')
        ticks = 0
        with open(out_path, 'x', buffering=1 << 20) as out:
            sampler.write = out.write
            sampler.write_header(time.time_ns())
            started = next_tick = time.monotonic()
            while not stop() and time.monotonic() - started < seconds:
                if not sampler.tick(time.time_ns()):
                    break
                ticks += 1
                next_tick += period
                delay = next_tick - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_tick = time.monotonic()
    finally:
        sampler.close()
    restore_sudo_owner(out_path)
    return ticks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--comm-prefix', default='CPU ')
    parser.add_argument('--seconds', type=float, default=40.0)
    parser.add_argument('--hz', type=int, default=100)
    parser.add_argument('--out', required=True, help='New output file; refuses to overwrite')
    parser.add_argument(
        '--stack-words', type=int, default=0,
        help='Also copy this many 8-byte words from the blocked thread stack',
    )
    args = parser.parse_args(argv)
    if not 1 <= args.hz <= 1000:
        parser.error('--hz must be between 1 and 1000')
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    if args.stack_words < 0 or args.stack_words > 512:
        parser.error('--stack-words must be between 0 and 512')

    stopping: list[bool] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopping.append(True))

    try:
        record(
            args.pid, args.out, comm_prefix=args.comm_prefix,
            seconds=args.seconds, hz=args.hz, stack_words=args.stack_words,
            stop=lambda: bool(stopping),
        )
    except LookupError as exc:
        print(f'BLOCK_SAMPLER: {exc}', file=sys.stderr)
        return 2
    except OSError as exc:
        print(f'BLOCK_SAMPLER: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
