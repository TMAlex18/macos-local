#!/usr/bin/env python3
"""Independently validate guest-uploaded macOS application & framework suite archive."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import stat
import tarfile
import tempfile
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = 'macos-app-and-framework-suite-v1'
TAG = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}')
GUI_PROCS = ('Calculator', 'TextEdit', 'System Information')
GUI_BUNDLE_IDS = (
    'com.apple.calculator',
    'com.apple.TextEdit',
    'com.apple.SystemProfiler',
)
SCOPE = (
    'Host-independent verification of stock macOS arm64e userland frameworks '
    '(JavaScriptCore, CoreGraphics/ImageIO sips PNG encode & resize, SQLite3 WAL/ACID, '
    'CoreFoundation binary plist bplist00, gzip/SHA-256) and LaunchServices/process '
    'registration + clean exit of Calculator, TextEdit, and System Information. '
    'Does not by itself prove physical GPU scanout or interactive UI latency.'
)


def expected_jsc():
    n = 100000
    sieve = bytearray(n + 1)
    count = 0
    total = 0
    for p in range(2, n + 1):
        if not sieve[p]:
            count += 1
            total += p
            if p * p <= n:
                sieve[p * p:n + 1:p] = b'\x01' * (((n - p * p) // p) + 1)
    k = 16
    a = [[(i * 3 + j * 5 - 11) * 0.25 for j in range(k)] for i in range(k)]
    b = [[(i * 7 - j * 2 + 3) * 0.5 for j in range(k)] for i in range(k)]
    trace = 0.0
    frob_sq = 0.0
    for i in range(k):
        for j in range(k):
            acc = 0.0
            for t in range(k):
                acc += a[i][t] * b[t][j]
            if i == j:
                trace += acc
            frob_sq += acc * acc
    return {
        'prime_limit': n,
        'prime_count': count,
        'prime_sum': total,
        'mat_dim': k,
        'mat_trace': trace,
        'mat_frob_sq': frob_sq,
        'bigint_modexp': str(pow(3, 4096, 1000000007)),
    }


def expected_quadrant_rgb(x, y, width, height):
    half_x = width // 2
    half_y = height // 2
    # Note: in BMP y=0 is bottom; in top-down PNG (PIL), y_top=0 is top (y_bmp = height - 1 - y_top).
    y_bmp = height - 1 - y
    if y_bmp < half_y and x < half_x:
        return (220, 40, 60)
    if y_bmp < half_y and x >= half_x:
        return (40, 180, 80)
    if y_bmp >= half_y and x < half_x:
        return (50, 90, 230)
    return (240, 200, 30)


def expected_payload_sha256():
    lines = [f'line-{i:04d}-det-{(i * 2654435761) % 4294967296:08x}\n' for i in range(1024)]
    raw = ''.join(lines).encode('ascii')
    return raw, hashlib.sha256(raw).hexdigest()


def expected_sqlite():
    rows = []
    buckets = ('alpha', 'beta', 'gamma', 'delta')
    for n in range(1, 251):
        rows.append((n, buckets[n % 4], (n * 17) % 103))
    total_sum = sum(r[2] for r in rows)
    by_bucket = {}
    for _, b, amt in rows:
        by_bucket.setdefault(b, []).append(amt)
    summary = {b: (len(vals), sum(vals)) for b, vals in sorted(by_bucket.items())}
    return len(rows), total_sum, summary


def expected_plist():
    return {
        'suite': FIXTURE,
        'counts': [16384, 65536, -42],
        'exact_half': 0.625,
        'flags': {'arm64e': True, 'rosetta': False},
    }


def parse_kv(text):
    out = {}
    for line in text.splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip()
    return out


def safe_extract_members(tar_bytes, tag):
    members = {}
    prefix = tag + '/'
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode='r:gz') as tf:
        for info in tf.getmembers():
            if info.isdir():
                continue
            if not info.isfile() or info.issym() or info.islnk():
                raise ValueError('Archive contains non-regular member: ' + info.name)
            if not info.name.startswith(prefix) or '..' in Path(info.name).parts:
                raise ValueError('Unexpected archive path: ' + info.name)
            rel = info.name[len(prefix):]
            if '/' in rel or info.size > 16 * 1024 * 1024:
                raise ValueError('Invalid member path or size: ' + info.name)
            extracted = tf.extractfile(info)
            if extracted is None:
                raise ValueError('Unreadable member: ' + info.name)
            members[rel] = extracted.read()
    return members


def validate_archive(archive_path, tag):
    if not TAG.fullmatch(tag):
        raise ValueError('Invalid result tag')
    result = {
        'schema': 1,
        'fixture': FIXTURE,
        'tag': tag,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'archive_path': str(archive_path),
        'passed': False,
        'checks': {},
        'details': {},
        'errors': [],
        'scope': SCOPE,
    }
    try:
        st = archive_path.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_size > 32 * 1024 * 1024:
            raise ValueError('Invalid archive file type, owner, or size')
        raw = archive_path.read_bytes()
        result['archive_sha256'] = hashlib.sha256(raw).hexdigest()
        members = safe_extract_members(raw, tag)
    except (OSError, ValueError, tarfile.TarError) as error:
        result['errors'].append(str(error))
        return result

    summary = parse_kv(members.get('summary.txt', b'').decode('utf-8', errors='replace'))
    sw_vers = members.get('sw_vers.txt', b'').decode('utf-8', errors='replace')
    uname = members.get('uname.txt', b'').decode('utf-8', errors='replace').strip()
    result['details']['guest_os'] = {'sw_vers': sw_vers.strip(), 'uname': uname}
    result['checks']['summary_identity'] = (
        summary.get('schema') == '1'
        and summary.get('fixture') == FIXTURE
        and summary.get('tag') == tag
        and summary.get('collector_exit') == '0'
        and 'arm64' in uname
    )

    # 1. JavaScriptCore
    try:
        jsc_actual = json.loads(members['jsc-out.json'].decode('utf-8'))
        jsc_expected = expected_jsc()
        result['checks']['javascriptcore_exact'] = (
            summary.get('jsc_exit') == '0' and jsc_actual == jsc_expected
        )
        result['details']['javascriptcore'] = jsc_actual
    except (KeyError, ValueError, TypeError) as error:
        result['checks']['javascriptcore_exact'] = False
        result['errors'].append(f'jsc: {error}')

    # 2. CoreGraphics / ImageIO (sips)
    try:
        with Image.open(io.BytesIO(members['out.png'])) as im_full:
            rgb_full = im_full.convert('RGB')
            full_ok = (
                im_full.format == 'PNG'
                and im_full.size == (64, 48)
                and all(
                    rgb_full.getpixel((x, y)) == expected_quadrant_rgb(x, y, 64, 48)
                    for y in range(48) for x in range(64)
                )
            )
        with Image.open(io.BytesIO(members['resized.png'])) as im_res:
            rgb_res = im_res.convert('RGB')
            # Check quadrant centers well away from the 1px resample seam
            centers_ok = (
                im_res.format == 'PNG'
                and im_res.size == (32, 24)
                and all(
                    max(abs(a - b) for a, b in zip(
                        rgb_res.getpixel((cx, cy)),
                        expected_quadrant_rgb(cx, cy, 32, 24),
                    )) <= 2
                    for cx in (6, 25) for cy in (5, 18)
                )
            )
        result['checks']['sips_coregraphics_png'] = (
            summary.get('sips_encode_exit') == '0'
            and summary.get('sips_resize_exit') == '0'
            and full_ok and centers_ok
        )
    except (KeyError, OSError, ValueError) as error:
        result['checks']['sips_coregraphics_png'] = False
        result['errors'].append(f'sips: {error}')

    # 3. SQLite3 WAL + ACID + host sqlite3 verification on compact.db
    try:
        exp_count, exp_sum, exp_buckets = expected_sqlite()
        sql_txt = members['sqlite-out.txt'].decode('utf-8')
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / 'compact.db'
            db_path.write_bytes(members['compact.db'])
            conn = sqlite3.connect(str(db_path))
            try:
                integ = conn.execute('PRAGMA integrity_check;').fetchone()[0]
                cnt, tot = conn.execute('SELECT COUNT(*), SUM(amount) FROM ledger;').fetchone()
                rolled = conn.execute("SELECT COUNT(*) FROM ledger WHERE bucket='rolled_back';").fetchone()[0]
                b_rows = {
                    b: (c, s)
                    for b, c, s in conn.execute(
                        'SELECT bucket, COUNT(*), SUM(amount) FROM ledger GROUP BY bucket;'
                    )
                }
            finally:
                conn.close()
        result['checks']['sqlite3_wal_acid'] = (
            summary.get('sqlite_work_exit') == '0'
            and summary.get('sqlite_vacuum_exit') == '0'
            and 'wal' in sql_txt.lower()
            and f'count={exp_count},sum={exp_sum}' in sql_txt
            and integ == 'ok'
            and (cnt, tot, rolled) == (exp_count, exp_sum, 0)
            and b_rows == exp_buckets
        )
    except (KeyError, sqlite3.Error, ValueError) as error:
        result['checks']['sqlite3_wal_acid'] = False
        result['errors'].append(f'sqlite3: {error}')

    # 4. Binary plist (bplist00) + JSON round-trip
    try:
        raw_bplist = members['data.bplist']
        parsed_bplist = plistlib.loads(raw_bplist)
        parsed_json = json.loads(members['data-plutil.json'].decode('utf-8'))
        exp_pl = expected_plist()
        result['checks']['plutil_bplist_roundtrip'] = (
            raw_bplist.startswith(b'bplist00')
            and summary.get('plutil_binary_exit') == '0'
            and summary.get('plutil_lint_exit') == '0'
            and summary.get('plutil_json_exit') == '0'
            and parsed_bplist == exp_pl
            and parsed_json == exp_pl
        )
    except (KeyError, plistlib.InvalidFileException, ValueError) as error:
        result['checks']['plutil_bplist_roundtrip'] = False
        result['errors'].append(f'plutil: {error}')

    # 5. Gzip compression + SHA-256 digest
    try:
        exp_raw, exp_sha = expected_payload_sha256()
        decomp = gzip.decompress(members['payload.txt.gz'])
        sha1 = members['payload.sha256'].decode('ascii').strip()
        sha2 = members['payload.roundtrip.sha256'].decode('ascii').strip()
        result['checks']['gzip_and_sha256'] = (
            decomp == exp_raw and sha1 == exp_sha and sha2 == exp_sha
        )
    except (KeyError, OSError, ValueError) as error:
        result['checks']['gzip_and_sha256'] = False
        result['errors'].append(f'gzip: {error}')

    # 6. Stock GUI apps launched, registered in LaunchServices, and cleanly quit
    try:
        lifecycle = members['gui-lifecycle.txt'].decode('utf-8')
        lsapp = members.get('lsappinfo-live.txt', b'').decode('utf-8', errors='replace')
        live_pids_ok = all(
            re.search(rf'^live:{re.escape(p)}:pid=[0-9]+$', lifecycle, re.M) is not None
            for p in GUI_PROCS
        )
        clean_quit_ok = all(
            f'after:{p}:pgrep_exit=1' in lifecycle
            for p in GUI_PROCS
        )
        bundles_ok = all(bid.lower() in lsapp.lower() for bid in GUI_BUNDLE_IDS)
        result['checks']['gui_apps_lifecycle'] = (
            summary.get('gui_all_processes_live') == 'true'
            and live_pids_ok
            and bundles_ok
            and clean_quit_ok
        )
        result['details']['gui_apps'] = {
            'live_pids_verified': live_pids_ok,
            'launchservices_bundles_verified': bundles_ok,
            'clean_quit_verified': clean_quit_ok,
        }
    except (KeyError, ValueError) as error:
        result['checks']['gui_apps_lifecycle'] = False
        result['errors'].append(f'gui: {error}')

    result['passed'] = (
        not result['errors']
        and bool(result['checks'])
        and all(result['checks'].values())
    )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--tag', required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', args.run):
        parser.error('Invalid run label')
    if not TAG.fullmatch(args.tag):
        parser.error('Invalid result tag')
    directory = ROOT / 'runs/e05-installed-boot' / args.run
    if not directory.is_dir() or directory.resolve() != directory.absolute():
        parser.error('Expected an existing run directory')
    output = directory / (args.tag + '-validation.json')
    if output.exists():
        parser.error('Validation artifact already exists; inspect it before another operation')
    archive = directory / 'guest-uploads' / (args.tag + '.tgz')
    result = validate_archive(archive, args.tag)
    result['run'] = args.run
    with output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'report': str(output), 'passed': result['passed'], 'checks': result['checks']}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
