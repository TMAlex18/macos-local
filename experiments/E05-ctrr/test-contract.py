#!/usr/bin/env python3
"""Self-authored CTRR contract tests; no QEMU, Apple code, or live restore."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contract_model import CtrrError, CtrrModel, ENABLE, LOCK, READ_ONLY


RAM = ((0x70000000, 0x70400000),)


class CtrrContractTests(unittest.TestCase):
    def setUp(self):
        self.m = CtrrModel(RAM, 0x4000)

    def test_unconfigured_identity_and_bounds(self):
        self.assertEqual(self.m.read_global(0), 0x63747272)
        self.assertEqual(self.m.read_global(4), 1)
        self.assertEqual(self.m.read_global(0x0c), 16)
        self.assertEqual(self.m.read_global(0x10), 14)
        self.assertEqual(self.m.read_global(0x20), 0x70000000)
        self.assertEqual(self.m.read_global(0x28), 0x703fc000)

    def test_sixteen_ranges_start_clear(self):
        self.assertEqual(len(self.m.ranges), 16)
        self.assertTrue(all((r.lower, r.upper, r.control) == (0, 0, 0)
                            for r in self.m.ranges))

    def test_alignment_is_required(self):
        self.m.write_range(0, "lower", 0x70000001)
        self.m.write_range(0, "upper", 0x70004000)
        with self.assertRaises(CtrrError): self.m.write_range(0, "control", ENABLE)

    def test_upper_must_follow_lower(self):
        self.m.write_range(0, "lower", 0x70004000)
        self.m.write_range(0, "upper", 0x70000000)
        with self.assertRaises(CtrrError): self.m.write_range(0, "control", ENABLE)

    def test_range_must_be_inside_one_ram_interval(self):
        self.m.write_range(0, "lower", 0x6ffff000)
        self.m.write_range(0, "upper", 0x70004000)
        with self.assertRaises(CtrrError): self.m.write_range(0, "control", ENABLE)

    def test_enable_read_only_records_flags5_and_page_length(self):
        self.m.write_range(0, "lower", 0x70000000)
        self.m.write_range(0, "upper", 0x70004000)
        self.m.write_range(0, "control", ENABLE | READ_ONLY)
        self.assertEqual(self.m.calls[-1].flags, 5)
        self.assertEqual(self.m.calls[-1].length, 0x8000)

    def test_enable_rw_records_flags7(self):
        self.m.write_range(0, "lower", 0x70000000)
        self.m.write_range(0, "upper", 0x70004000)
        self.m.write_range(0, "control", ENABLE)
        self.assertEqual(self.m.calls[-1].flags, 7)

    def test_overlap_is_rejected(self):
        for i, lower in enumerate((0x70000000, 0x70004000)):
            self.m.write_range(i, "lower", lower)
            self.m.write_range(i, "upper", lower + 0x4000)
        self.m.write_range(0, "control", ENABLE)
        self.m.write_range(1, "lower", 0x70002000)
        self.m.write_range(1, "upper", 0x70006000)
        with self.assertRaises(CtrrError): self.m.write_range(1, "control", ENABLE)

    def test_enabled_bounds_are_immutable(self):
        self.m.write_range(0, "lower", 0x70000000)
        self.m.write_range(0, "upper", 0x70004000)
        self.m.write_range(0, "control", ENABLE)
        self.m.write_range(0, "lower", 0x70008000)
        self.assertEqual(self.m.read_range(0, "lower"), 0x70000000)

    def test_lock_rejects_all_later_writes(self):
        self.m.write_range(0, "lower", 0x70000000)
        self.m.write_range(0, "upper", 0x70004000)
        self.m.write_range(0, "control", ENABLE | LOCK)
        self.m.write_range(0, "lower", 0x70008000)
        self.m.write_range(0, "upper", 0x7000c000)
        self.m.write_range(0, "control", 0)
        self.assertEqual((self.m.read_range(0, "lower"), self.m.read_range(0, "upper"),
                          self.m.read_range(0, "control")), (0x70000000, 0x70004000, ENABLE | LOCK))

    def test_enabled_read_only_cannot_change_to_rw(self):
        self.m.write_range(0, "lower", 0x70000000)
        self.m.write_range(0, "upper", 0x70004000)
        self.m.write_range(0, "control", ENABLE | READ_ONLY)
        self.m.write_range(0, "control", ENABLE)
        self.assertEqual(self.m.read_range(0, "control"), ENABLE | READ_ONLY)

    def test_disable_restores_flags7(self):
        self.m.write_range(0, "lower", 0x70000000)
        self.m.write_range(0, "upper", 0x70004000)
        self.m.write_range(0, "control", ENABLE | READ_ONLY)
        self.m.write_range(0, "control", 0)
        self.assertEqual(self.m.calls[-1].flags, 7)

    def test_unsupported_control_is_ignored(self):
        self.m.write_range(0, "control", 8)
        self.assertEqual(self.m.read_range(0, "control"), 0)

    def test_teardown_restores_protection_and_clears_state(self):
        self.m.write_range(0, "lower", 0x70000000)
        self.m.write_range(0, "upper", 0x70004000)
        self.m.write_range(0, "control", ENABLE | READ_ONLY)
        self.m.teardown()
        self.assertEqual(self.m.calls[-1].reason, "teardown")
        self.assertTrue(all(r.control == 0 for r in self.m.ranges))

    def test_no_guest_or_host_side_effects_are_claimed(self):
        self.assertEqual(self.m.calls, [])
        self.assertEqual(self.m.read_global(0x100), 0)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CtrrContractTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)

