#!/usr/bin/env python3
"""Offline check of guest-diag/log-window.sh with stand-ins for the macOS commands."""
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('guest-diag') / 'log-window.sh'
START, END = '2026-11-11 08:48:02', '2026-11-11 08:48:08'
STUBS = {
    'log': '#!/bin/sh\necho "log $*"\n',
    'memory_pressure': '#!/bin/sh\necho "System-wide memory free percentage: 50%"\n',
    # Keeps the uploaded file and the address it was sent to.
    'curl': ('#!/bin/sh\nwhile [ $# -gt 0 ]; do\n  case "$1" in\n    -T) cp "$2" "$STUB_OUT/upload.tgz"; shift;;\n'
             '    http*) echo "$1" > "$STUB_OUT/url";;\n  esac\n  shift\ndone\n'),
}


class LogWindow(unittest.TestCase):
    def run_script(self, *args, stubs=STUBS):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in ('bin', 'work', 'out'):
            (tmp / name).mkdir()
        for name, body in stubs.items():
            (tmp / 'bin' / name).write_text(body)
            (tmp / 'bin' / name).chmod(0o755)
        env = dict(os.environ, PATH=f"{tmp / 'bin'}:{os.environ['PATH']}", E05_TMP=str(tmp / 'work'),
                   STUB_OUT=str(tmp / 'out'), H='http://127.0.0.1:9')
        result = subprocess.run(['sh', str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
        return tmp, result

    def files(self, tmp):
        with tarfile.open(tmp / 'out' / 'upload.tgz') as archive:
            return {member.name: archive.extractfile(member).read().decode()
                    for member in archive.getmembers() if member.isfile()}

    def test_window_is_exported_and_uploaded_under_its_tag(self):
        tmp, result = self.run_script('t1logw', START, END)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((tmp / 'out' / 'url').read_text().strip(), 'http://127.0.0.1:9/up/t1logw.tgz')
        files = self.files(tmp)
        self.assertEqual(files['t1logw/log-show.txt'].strip(),
                         f'log show --start {START} --end {END} --style compact --info --debug')
        self.assertIn('memory free percentage', files['t1logw/memory-pressure.txt'])
        self.assertIn('PID', files['t1logw/ps-all.txt'])
        summary = files['t1logw/summary.txt'].splitlines()
        self.assertEqual([line.split()[0] for line in summary],
                         ['started_utc=' + summary[0].split('=')[1].split()[0], 'log-show', 'launchd-log', 'ps-all', 'memory-pressure'])
        self.assertIn('log-show exit=0 timeout=false', summary[1])
        # No launchd log on this host: the failure is recorded and the rest still runs.
        self.assertRegex(summary[2], r'^launchd-log exit=[1-9]\d* timeout=false')

    def test_a_slow_log_reader_is_stopped_at_its_limit(self):
        stubs = dict(STUBS, log='#!/bin/sh\nexec sleep 30\n')
        tmp, result = self.run_script('t2logw', START, END, '1', stubs=stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = self.files(tmp)['t2logw/summary.txt']
        self.assertRegex(summary, r'log-show exit=\d+ timeout=true')
        self.assertIn('memory-pressure exit=0 timeout=false', summary)

    def test_bad_arguments_are_refused_before_anything_is_written(self):
        for args in (('bad/tag', START, END), ('t3', '2026-11-11T08:48:02', END), ('t3', START, '08:48:08'),
                     ('t3', START, END, 'soon'), ('t3', START + '; id', END)):
            tmp, result = self.run_script(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertEqual(list((tmp / 'work').iterdir()), [], args)
            self.assertFalse((tmp / 'out' / 'upload.tgz').exists(), args)


if __name__ == '__main__':
    unittest.main()
