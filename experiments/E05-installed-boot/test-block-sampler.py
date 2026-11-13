#!/usr/bin/env python3
"""Offline unit tests for block_sampler.py and block_report.py."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import block_report
import block_sampler


class FakeBlockedProc:
    """Synthetic /proc/PID layout for testing BlockSampler without root or QEMU."""

    def __init__(self, root: str | Path, pid: int = 5150, base: int = 0x555555554000):
        self.root = Path(root)
        self.pid = pid
        self.base = base
        self.proc_dir = self.root / str(pid)
        self.task_dir = self.proc_dir / 'task'
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self.exe_target = self.root / 'fake-qemu-system-aarch64'
        self.exe_target.write_bytes(b'\x7fELF')
        os.symlink(self.exe_target, self.proc_dir / 'exe')
        (self.proc_dir / 'maps').write_text(
            f'{base:x}-{base + 0x200000:x} r--p 00000000 08:01 12345 {self.exe_target}\n'
            f'{base + 0x200000:x}-{base + 0x900000:x} r-xp 00200000 08:01 12345 {self.exe_target}\n'
        )
        # Small sparse-like file for stack reads at low synthetic offsets
        self.mem_path = self.proc_dir / 'mem'
        self.mem_path.write_bytes(b'\x00' * 4096)

    def set_thread(self, tid: int, comm: str, syscall_line: str) -> None:
        tdir = self.task_dir / str(tid)
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / 'comm').write_text(comm + '\n')
        (tdir / 'syscall').write_text(syscall_line + '\n')

    def write_stack_words(self, sp_offset: int, words: list[int]) -> None:
        buf = bytearray(self.mem_path.read_bytes())
        packed = struct.pack(f'<{len(words)}Q', *words)
        buf[sp_offset:sp_offset + len(packed)] = packed
        self.mem_path.write_bytes(bytes(buf))


class BlockSamplerAndReportTests(unittest.TestCase):
    def test_sampler_filters_prefix_reads_stacks_and_handles_missing_maps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fp = FakeBlockedProc(tmp, pid=777, base=0x560000000000)
            bql_addr = fp.base + 0x1200000
            # Stack at offset 0x200: wrapper (skipped) -> fn-pointer at entry+0 (skipped) -> helper_get_cp_reg64 -> cpu_exec_loop
            fp.write_stack_words(
                0x200,
                [
                    fp.base + 0x300010,  # bql_lock_impl (skipped)
                    fp.base + 0x380000,  # exact entry address = function pointer, not return address
                    fp.base + 0x400020,  # helper_get_cp_reg64+0x20 (valid return address)
                    fp.base + 0x500030,  # cpu_exec_loop+0x30
                ],
            )
            fp.set_thread(777, 'qemu-system-aar', '271 0x1 0x2 0x3 0x0 0x0 0x0 0x100 0x7f0001')
            fp.set_thread(778, 'CPU 0/TCG', 'running')
            fp.set_thread(
                779, 'CPU 1/TCG',
                f'202 {bql_addr:#x} 0x80 0x2 0x0 0x0 0x0 0x200 0x7f0010',
            )

            buf = io.StringIO()
            sampler = block_sampler.BlockSampler(
                777, buf.write, comm_prefix='CPU ', stack_words=4, proc_root=tmp,
            )
            try:
                self.assertEqual(sampler.open_target(), 2)
                sampler.write_header(1_000_000_000)
                self.assertTrue(sampler.tick(1_010_000_000))
                # Second tick: both CPUs running
                fp.set_thread(779, 'CPU 1/TCG', 'running')
                self.assertTrue(sampler.tick(1_020_000_000))
            finally:
                sampler.close()

            nm_text = '\n'.join([
                '0000000001200000 0000000000000030 B bql',
                '0000000001200100 0000000000000030 B exclusive_resume',
                '0000000000300000 0000000000000080 T bql_lock_impl',
                '0000000000380000 0000000000000040 T mttcg_force_rcu',
                '0000000000400000 0000000000000080 T helper_get_cp_reg64',
                '0000000000500000 0000000000000100 T cpu_exec_loop',
            ])
            data_syms, func_syms = block_report.parse_nm_output(nm_text)
            summary = block_report.summarize_samples(
                buf.getvalue().splitlines(),
                data_symbols=data_syms,
                func_symbols=func_syms,
            )
            self.assertEqual(summary['tick_count'], 2)
            self.assertEqual(summary['thread_count'], 2)
            self.assertEqual(summary['thread_samples'], 4)
            self.assertEqual(summary['running_samples'], 3)
            self.assertAlmostEqual(float(summary['running_share']), 0.75)
            self.assertEqual(summary['blocked_counts'], {'futex on bql': 1})
            self.assertEqual(summary['futex_ops'], {0: 1})
            self.assertEqual(
                summary['call_chains']['bql'],
                {'helper_get_cp_reg64 <- cpu_exec_loop': 1},
            )

            txt = block_report.format_text_report(summary)
            self.assertIn('running or runnable:  75.0%', txt)
            self.assertIn('25.0%  futex on bql', txt)
            self.assertIn('helper_get_cp_reg64 <- cpu_exec_loop', txt)

    def test_window_filtering_non_syscall_and_heap_futex_attribution(self) -> None:
        raw_lines = [
            'H pid 900',
            'H base 0x550000000000',
            'T 901 CPU 0/TCG',
            'T 902 CPU 1/TCG',
            # Tick 1 at 1000 us (excluded by start_us=2000)
            '1000 901 202 0x550000100000 0x89 0x0 0x0 0x0 0x0 0x7ffd00 0x7f0001',
            'R 1000 1 2',
            # Tick 2 at 2000 us: tid 901 outside syscall (-1), tid 902 on heap futex
            '2000 901 -1 0x7ffd00 0x550000040000',
            '2000 902 202 0x7f9900001000 0x89 0x0 0x0 0x0 0x0 0x7ffe00 0x7f0001',
            'R 2000 0 2',
            # Tick 3 at 12000 us: tid 901 in binary+offset gap, tid 902 running
            '12000 901 202 0x550000100050 0x80 0x0 0x0 0x0 0x0 0x7ffd00 0x7f0001',
            'R 12000 1 2',
            # Tick 4 at 30000 us (excluded by end_us=20000)
            'R 30000 2 2',
        ]
        data_syms, _ = block_report.parse_nm_output(
            '0000000000100000 0000000000000020 B bql\n'
            '0000000000100100 0000000000000020 B exclusive_resume\n'
        )
        summary = block_report.summarize_samples(
            raw_lines, data_symbols=data_syms, start_us=2000, end_us=20000,
        )
        self.assertEqual(summary['tick_count'], 2)
        self.assertAlmostEqual(float(summary['duration_seconds']), 0.01)
        self.assertEqual(summary['thread_samples'], 4)
        self.assertEqual(summary['running_samples'], 1)
        self.assertEqual(
            summary['blocked_counts'],
            {
                'outside a system call (page fault or similar)': 1,
                'futex on heap or stack': 1,
                'futex on binary+0x100050': 1,
            },
        )
        self.assertEqual(summary['futex_ops'], {0: 1, 9: 1})

    def test_empty_window_does_not_divide_by_zero_and_cli_emits_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sample_file = Path(tmp) / 'sample.txt'
            sample_file.write_text('H pid 1\nH base 0x0\nT 2 CPU 0/TCG\nR 500 1 1\n')
            summary = block_report.summarize_samples(
                sample_file.read_text().splitlines(), start_us=5000, end_us=6000,
            )
            self.assertEqual(summary['tick_count'], 0)
            self.assertEqual(summary['thread_samples'], 0)
            self.assertEqual(summary['running_share'], 0.0)
            self.assertIn('0 thread samples', block_report.format_text_report(summary))

            buf = io.StringIO()
            orig_stdout = sys.stdout
            try:
                sys.stdout = buf
                rc = block_report.main([str(sample_file), '--json'])
            finally:
                sys.stdout = orig_stdout
            self.assertEqual(rc, 0)
            parsed = json.loads(buf.getvalue())
            self.assertEqual(parsed['running_share'], 1.0)

    def test_record_refuses_existing_output_and_handles_missing_Maps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fp = FakeBlockedProc(tmp, pid=888)
            (fp.proc_dir / 'maps').unlink()
            fp.set_thread(889, 'CPU 0/TCG', 'running')
            out = Path(tmp) / 'out.txt'
            ticks = block_sampler.record(
                888, out, comm_prefix='CPU ', seconds=0.03, hz=200, proc_root=tmp,
            )
            self.assertGreaterEqual(ticks, 1)
            text = out.read_text()
            self.assertIn('H base 0x0\n', text)
            with self.assertRaises(FileExistsError):
                block_sampler.record(888, out, proc_root=tmp, seconds=0.01)


if __name__ == '__main__':
    unittest.main()
