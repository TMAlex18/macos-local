#!/usr/bin/env python3
"""Synthetic fixtures for offline CPU occupancy; no live VM or evidence writes."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
import cpu_occupancy as c

HOST = '''=== 2026-11-01T00:00:00.000Z ===
=== 2026-11-01T00:00:00.000Z ===
 100  100   1  0 Ssl  50.0 00:00:10 qemu-system-aar
 100  101   1  1 Rsl  90.0 00:01:00 CPU 0/TCG
 100  102   1  2 Ssl  90.0 1-00:00:00 CPU 1/TCG
 100  103   1  3 Ssl   1.0 00:00:01 reims-vgpu.fifo
=== 2026-11-01T00:00:30.000Z ===
 100  100   1  0 Ssl  50.0 00:00:11 qemu-system-aar
 100  101   1  1 Rsl  90.0 00:01:15 CPU 0/TCG
 100  102   1  2 Ssl  90.0 1-00:00:30 CPU 1/TCG
 100  103   1  3 Ssl   1.0 00:00:04 reims-vgpu.fifo
'''


def cpu(name, pc='0x1', pstate='0x0', pid=1):
    return {'name': name, 'pc': pc, 'pstate': pstate, 'pid': pid, 'identity_state': 'identified'}


class CpuOccupancyTest(unittest.TestCase):
    def test_cputime_parses_days(self):
        self.assertEqual(c.cputime_seconds('1-00:00:30'), 86430)
        self.assertEqual(c.cputime_seconds('00:34:13'), 2053)

    def test_interval_uses_time_deltas_not_lifetime_percent(self):
        blocks = c.host_blocks(HOST)
        self.assertEqual(len(blocks), 2)
        [interval] = c.host_intervals(blocks)
        self.assertEqual(interval['vcpu_threads'], 2)
        self.assertEqual(interval['vcpu_cores'], 1.5)
        self.assertEqual(interval['vcpu_max_fraction'], 1.0)
        self.assertEqual(interval['graphics_cores'], 0.1)

    def test_guest_idle_pc_window_and_tracking(self):
        start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        idle = cpu('kernel_task', pc='0xidle', pstate='0x3c5', pid=0)
        rows = [{'elapsed_seconds': 10, 'state': 'sampled', 'cpus': [idle, idle]},
                {'elapsed_seconds': 40, 'state': 'sampled', 'cpus': [idle, cpu('com.apple.WebKit', pid=630)]},
                {'elapsed_seconds': 70, 'state': 'unknown', 'cpus': [cpu('ignored')]},
                {'elapsed_seconds': 100, 'state': 'sampled', 'cpus': [cpu('kernel_task', pc='0x2', pstate='0x3c5', pid=0), cpu('mds')]}]
        lo, hi = datetime(2026, 11, 1, 0, 0, 30, tzinfo=timezone.utc), datetime(2026, 11, 1, 0, 2, tzinfo=timezone.utc)
        g = c.guest_occupancy(rows, start, lo, hi)
        self.assertEqual((g['samples'], g['cpu_slots'], g['idle_slots'], g['kernel_task_slots_any_pc']), (2, 4, 1, 2))
        self.assertEqual((g['busy_slots'], g['busy_el0_slots']), (3, 2))
        self.assertEqual(g['tracked'], {'com.apple.WebKit': [[630, '00:00:40']]})

    def test_analyse_reads_run_directory_only(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / 'm3-fixture-01'
            run.mkdir()
            (run / 'results.json').write_text(json.dumps({'started_at_utc': '2026-11-01T00:00:00+00:00'}))
            (run / 'host-qemu-thread-samples.log').write_text(HOST)
            (run / 'current-process.jsonl').write_text(json.dumps({'elapsed_seconds': 5, 'state': 'sampled', 'cpus': [cpu('launchd')]}) + '\n')
            before = sorted(p.name for p in run.iterdir())
            report = c.analyse(run)
            self.assertEqual(sorted(p.name for p in run.iterdir()), before)
            self.assertEqual(report['host_window']['intervals'], 1)
            self.assertEqual(report['guest_window']['top_on_cpu'], [('launchd', 1)])


if __name__ == '__main__':
    unittest.main()
