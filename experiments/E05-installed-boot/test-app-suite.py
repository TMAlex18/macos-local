#!/usr/bin/env python3
"""Offline unit tests for tools/validate-app-suite.py."""
import gzip
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import sqlite3
import tarfile
import tempfile
import unittest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('validate_app_suite', ROOT / 'tools/validate-app-suite.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def make_png_bytes(width, height):
    im = Image.new('RGB', (width, height))
    for y in range(height):
        for x in range(width):
            im.putpixel((x, y), MOD.expected_quadrant_rgb(x, y, width, height))
    buf = io.BytesIO()
    im.save(buf, format='PNG')
    return buf.getvalue()


def make_compact_db_bytes(include_rollback_row=False):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / 'compact.db'
        conn = sqlite3.connect(str(p))
        try:
            conn.execute('CREATE TABLE ledger (id INTEGER PRIMARY KEY, bucket TEXT NOT NULL, amount INTEGER NOT NULL);')
            buckets = ('alpha', 'beta', 'gamma', 'delta')
            for n in range(1, 251):
                conn.execute('INSERT INTO ledger VALUES (?, ?, ?);', (n, buckets[n % 4], (n * 17) % 103))
            if include_rollback_row:
                conn.execute('INSERT INTO ledger VALUES (999, "rolled_back", 12345);')
            conn.commit()
        finally:
            conn.close()
        return p.read_bytes()


def build_valid_members(tag):
    exp_count, exp_sum, _ = MOD.expected_sqlite()
    exp_raw, exp_sha = MOD.expected_payload_sha256()
    exp_pl = MOD.expected_plist()
    return {
        'summary.txt': (
            f'schema=1\nfixture={MOD.FIXTURE}\ntag={tag}\ncollector_exit=0\n'
            'jsc_exit=0\nsips_encode_exit=0\nsips_resize_exit=0\n'
            'sqlite_work_exit=0\nsqlite_vacuum_exit=0\n'
            'plutil_binary_exit=0\nplutil_lint_exit=0\nplutil_json_exit=0\n'
            'gui_all_processes_live=true\n'
        ).encode('utf-8'),
        'sw_vers.txt': b'ProductName:\tmacOS\nProductVersion:\t27.0.1\nBuildVersion:\t26A434\n',
        'uname.txt': b'Darwin Virtual-Machine 26.0.0 Darwin Kernel Version 26.0.0: root:xnu/RELEASE_ARM64_VMAPPLE arm64\n',
        'jsc-out.json': json.dumps(MOD.expected_jsc()).encode('utf-8'),
        'out.png': make_png_bytes(64, 48),
        'resized.png': make_png_bytes(32, 24),
        'sqlite-out.txt': f'wal\ncount={exp_count},sum={exp_sum}\nok\n'.encode('utf-8'),
        'compact.db': make_compact_db_bytes(False),
        'data.bplist': plistlib.dumps(exp_pl, fmt=plistlib.FMT_BINARY),
        'data-plutil.json': json.dumps(exp_pl).encode('utf-8'),
        'payload.txt.gz': gzip.compress(exp_raw, compresslevel=9),
        'payload.sha256': (exp_sha + '\n').encode('ascii'),
        'payload.roundtrip.sha256': (exp_sha + '\n').encode('ascii'),
        'gui-lifecycle.txt': (
            'before:Calculator:pgrep_exit=1\nbefore:TextEdit:pgrep_exit=1\nbefore:System Information:pgrep_exit=1\n'
            'live:Calculator:pid=4101\nlive:TextEdit:pid=4102\nlive:System Information:pid=4103\n'
            'after:Calculator:pgrep_exit=1\nafter:TextEdit:pgrep_exit=1\nafter:System Information:pgrep_exit=1\n'
        ).encode('utf-8'),
        'lsappinfo-live.txt': (
            'bundleID="com.apple.calculator"\nbundleID="com.apple.TextEdit"\nbundleID="com.apple.SystemProfiler"\n'
        ).encode('utf-8'),
    }


def write_tgz(path, tag, members, *, add_symlink=False):
    with tarfile.open(path, 'w:gz') as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(f'{tag}/{name}')
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        if add_symlink:
            sym = tarfile.TarInfo(f'{tag}/bad-link')
            sym.type = tarfile.SYMTYPE
            sym.linkname = '/etc/passwd'
            tf.addfile(sym)


class AppSuiteValidatorTests(unittest.TestCase):
    def test_positive_archive_passes_all_six_checks(self):
        tag = 'unit-apps'
        with tempfile.TemporaryDirectory() as tmp:
            tgz = Path(tmp) / f'{tag}.tgz'
            write_tgz(tgz, tag, build_valid_members(tag))
            res = MOD.validate_archive(tgz, tag)
            self.assertTrue(res['passed'], res)
            self.assertEqual(set(res['checks'].keys()), {
                'summary_identity',
                'javascriptcore_exact',
                'sips_coregraphics_png',
                'sqlite3_wal_acid',
                'plutil_bplist_roundtrip',
                'gzip_and_sha256',
                'gui_apps_lifecycle',
            })

    def test_negative_controls_fail_specific_checks(self):
        tag = 'unit-apps'
        cases = (
            'bad_jsc',
            'bad_png',
            'bad_sqlite_rollback',
            'xml_not_bplist',
            'bad_gzip_sha',
            'unquit_gui',
            'missing_bundle',
            'tar_symlink',
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                members = build_valid_members(tag)
                if case == 'bad_jsc':
                    bad = MOD.expected_jsc()
                    bad['prime_sum'] += 1
                    members['jsc-out.json'] = json.dumps(bad).encode('utf-8')
                elif case == 'bad_png':
                    im = Image.open(io.BytesIO(members['out.png'])).convert('RGB')
                    im.putpixel((10, 10), (0, 0, 0))
                    b = io.BytesIO()
                    im.save(b, format='PNG')
                    members['out.png'] = b.getvalue()
                elif case == 'bad_sqlite_rollback':
                    members['compact.db'] = make_compact_db_bytes(True)
                elif case == 'xml_not_bplist':
                    members['data.bplist'] = plistlib.dumps(MOD.expected_plist(), fmt=plistlib.FMT_XML)
                elif case == 'bad_gzip_sha':
                    members['payload.roundtrip.sha256'] = b'0' * 64 + b'\n'
                elif case == 'unquit_gui':
                    members['gui-lifecycle.txt'] = members['gui-lifecycle.txt'].replace(
                        b'after:Calculator:pgrep_exit=1', b'after:Calculator:pgrep_exit=0'
                    )
                elif case == 'missing_bundle':
                    members['lsappinfo-live.txt'] = b'bundleID="com.apple.calculator"\n'
                tgz = Path(tmp) / f'{tag}.tgz'
                write_tgz(tgz, tag, members, add_symlink=(case == 'tar_symlink'))
                res = MOD.validate_archive(tgz, tag)
                self.assertFalse(res['passed'], f'Case {case} unexpectedly passed')


if __name__ == '__main__':
    unittest.main()
