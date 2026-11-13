#!/usr/bin/env python3
"""Offline unit tests for sync_stats.py and cpu_model(..., sync_stats=...)."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'experiments/E05-usbip-kernel')]
from firmware_session import cpu_model
from guest_ui import UIError
import sync_stats


def make_stats(**overrides) -> dict[str, object]:
    base: dict[str, object] = {'enabled': True}
    for k in sync_stats.SCALAR_KEYS:
        base[k] = 0
    base['cpreg_io'] = {}
    base['tlbi'] = {}
    base['cpreg_non_io_helper'] = {}
    base.update(overrides)
    return base


class SyncStatsOfflineTests(unittest.TestCase):
    def test_cpu_model_omits_flag_by_default_and_appends_when_enabled(self) -> None:
        default_val = cpu_model('qarma5', pauth_memo=True, pauth_memo_entries=1024)
        self.assertNotIn('x-sync-stats', default_val)
        enabled_val = cpu_model('qarma5', pauth_memo=True, pauth_memo_entries=1024, sync_stats=True)
        self.assertIn(',x-sync-stats=on', enabled_val)
        with self.assertRaises(ValueError):
            cpu_model('qarma5', sync_stats='yes')  # type: ignore[arg-type]

    def test_validate_and_diff_computes_exact_shares_and_rejects_decreases(self) -> None:
        s0_before = make_stats(
            exit_interrupt=10, bql_cpreg_read=100, bql_do_interrupt=20,
            do_interrupt_svc=15, do_interrupt_abort=5, bql_eret=40,
            excl_tlbi_range=4, queued_work_exclusive=4,
            cpreg_io={'CNTVCT_EL0': [100, 0]},
            tlbi={'TLBI_VAE1IS': [0, 4]},
        )
        s1_before = make_stats(exit_interrupt=4, queued_work_ordinary=4)

        s0_after = make_stats(
            exit_interrupt=30, bql_cpreg_read=600, bql_cpreg_write=50,
            bql_do_interrupt=120, do_interrupt_svc=85, do_interrupt_abort=25, do_interrupt_irq=10,
            bql_eret=240, bql_mmio=10,
            excl_tlbi_range=14, excl_tlbi_all=2, queued_work_exclusive=16,
            cpreg_io={'CNTVCT_EL0': [600, 0], 'CNTP_CVAL_EL0': [0, 50]},
            tlbi={'TLBI_VAE1IS': [0, 14], 'TLBI_ASIDE1IS': [0, 2]},
        )
        s1_after = make_stats(
            exit_interrupt=16, exit_hlt=4, queued_work_ordinary=16,
            bql_cpreg_read=200,
            cpreg_io={'CNTVCT_EL0': [200, 0]},
        )

        # Validate JSON roundtrip validator
        validated = sync_stats.validate_cpu_payload(json.dumps(s0_after))
        self.assertEqual(validated['bql_cpreg_read'], 600)

        before = {
            'schema': 1, 'run': 'r1', 'qemu_pid': 1234,
            'started_realtime_ns': 1_000_000_000, 'ended_realtime_ns': 1_005_000_000,
            'vcpus': [
                {'cpu_index': 0, 'qom_path': '/machine/unattached/device[0]', 'stats': s0_before},
                {'cpu_index': 1, 'qom_path': '/machine/unattached/device[1]', 'stats': s1_before},
            ],
        }
        after = {
            'schema': 1, 'run': 'r1', 'qemu_pid': 1234,
            'started_realtime_ns': 3_000_000_000, 'ended_realtime_ns': 3_000_000_000,
            'vcpus': [
                {'cpu_index': 0, 'qom_path': '/machine/unattached/device[0]', 'stats': s0_after},
                {'cpu_index': 1, 'qom_path': '/machine/unattached/device[1]', 'stats': s1_after},
            ],
        }
        diff = sync_stats.compute_diff(before, after)
        self.assertAlmostEqual(float(diff['elapsed_seconds']), 2.0)
        # cpreg_read delta: (600-100) + (200-0) = 700
        self.assertEqual(diff['bql_by_site']['bql_cpreg_read'], 700)
        self.assertEqual(diff['bql_by_site']['bql_cpreg_write'], 50)
        # outer exits: cpu0 (30-10)=20 + cpu1 (16-4 + 4)=16 -> 36
        self.assertEqual(diff['bql_by_site']['outer_loop_exit'], 36)
        self.assertEqual(diff['exclusive_requests_total'], 12)
        self.assertEqual(diff['cpreg_io_by_register']['CNTVCT_EL0']['reads'], 700)
        self.assertEqual(diff['tlbi_by_instruction']['TLBI_VAE1IS']['writes'], 10)
        self.assertEqual(diff['tlbi_by_instruction']['TLBI_ASIDE1IS']['writes'], 2)

        txt = sync_stats.format_diff_text(diff)
        self.assertIn('CNTVCT_EL0', txt)
        self.assertIn('TLBI_VAE1IS', txt)

        # Decreasing counter must raise UIError (e.g. VM reset mid-window)
        with self.assertRaises(UIError):
            sync_stats.compute_diff(after, before)

    def test_cli_diff_writes_json_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            b_path = tdir / 'before.json'
            a_path = tdir / 'after.json'
            o_path = tdir / 'diff.json'
            b_path.write_text(json.dumps({
                'schema': 1, 'run': 'r2', 'qemu_pid': 99,
                'started_realtime_ns': 0, 'ended_realtime_ns': 0,
                'vcpus': [{'cpu_index': 0, 'qom_path': '/machine/c0', 'stats': make_stats()}],
            }))
            a_path.write_text(json.dumps({
                'schema': 1, 'run': 'r2', 'qemu_pid': 99,
                'started_realtime_ns': 1_000_000_000, 'ended_realtime_ns': 1_000_000_000,
                'vcpus': [{'cpu_index': 0, 'qom_path': '/machine/c0', 'stats': make_stats(bql_mmio=7)}],
            }))
            self.assertEqual(sync_stats.main(['diff', str(b_path), str(a_path), '--out', str(o_path)]), 0)
            saved = json.loads(o_path.read_text())
            self.assertEqual(saved['bql_acquisitions_total'], 7)
            # Overwrite refused with exit code 2
            self.assertEqual(sync_stats.main(['diff', str(b_path), str(a_path), '--out', str(o_path)]), 2)

    def test_optional_no_bql_counters_separate_exclusive_items_from_bql_reacquire(self) -> None:
        before = {
            'schema': 1, 'run': 'r3', 'qemu_pid': 77,
            'started_realtime_ns': 0, 'ended_realtime_ns': 0,
            'vcpus': [{'cpu_index': 0, 'qom_path': '/machine/c0', 'stats': make_stats(
                bql_exclusive_reacquire=0, no_bql_exit_interrupt=0,
            )}],
        }
        after = {
            'schema': 1, 'run': 'r3', 'qemu_pid': 77,
            'started_realtime_ns': 1_000_000_000, 'ended_realtime_ns': 1_000_000_000,
            'vcpus': [{'cpu_index': 0, 'qom_path': '/machine/c0', 'stats': make_stats(
                queued_work_exclusive=25, bql_exclusive_reacquire=2,
                no_bql_exit_interrupt=40, bql_mmio=3,
            )}],
        }
        diff = sync_stats.compute_diff(before, after)
        self.assertEqual(diff['bql_by_site']['queued_work_exclusive'], 2)
        self.assertEqual(diff['bql_acquisitions_total'], 5)
        self.assertEqual(diff['queued_work']['exclusive_items'], 25)
        self.assertEqual(diff['queued_work']['no_bql_exit_interrupt'], 40)


if __name__ == '__main__':
    unittest.main()
