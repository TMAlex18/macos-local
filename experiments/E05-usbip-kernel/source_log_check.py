"""Bounded regular-file and completion-log checks for installed-source records.

Contains only generic stat/digest and terminal status-line inspection helpers
shared by the installed-boot controller and restore harness, with zero archive
digests, signing fields, or external client assumptions.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import stat

FINISHED = b'Status: Restore Finished'
TAIL_BYTES = 8192
ANSI_COLOR = re.compile(rb'\x1b\[[0-9;]*m')


def file_identity(path):
    value = Path(path).lstat()
    if not stat.S_ISREG(value.st_mode):
        raise ValueError(f'Expected a regular file: {path}')
    return {key: getattr(value, key) for key in
            ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}


def read_bound_file(path, maximum):
    before = file_identity(path)
    if not 0 < before['st_size'] <= maximum:
        raise ValueError(f'File size outside bounded input contract: {path}')
    data = Path(path).read_bytes()
    if len(data) != before['st_size'] or file_identity(path) != before:
        raise ValueError(f'Input changed while reading: {path}')
    return data, before


def inspect_client_log(path):
    """Bounded-memory whole-log scan; only an 8 KiB tail enters structured output."""
    digest, pending, overlong = hashlib.sha256(), b'', False
    marker_lines, marker_count, line_number = [], 0, 0
    with Path(path).open('rb') as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
            for index, part in enumerate(chunk.split(b'\n')):
                if index:
                    line_number += 1
                    if not overlong and ANSI_COLOR.sub(b'', pending).strip() == FINISHED:
                        marker_count += 1
                        if len(marker_lines) < 8:
                            marker_lines.append(line_number)
                    pending, overlong = b'', False
                if not overlong:
                    pending += part
                    if len(pending) > 4096:
                        pending, overlong = b'', True
        if pending and not overlong and ANSI_COLOR.sub(b'', pending).strip() == FINISHED:
            marker_count += 1
            if len(marker_lines) < 8:
                marker_lines.append(line_number + 1)
        size = stream.tell()
        stream.seek(max(0, size - TAIL_BYTES))
        tail = stream.read(TAIL_BYTES).decode('utf-8', errors='replace')
    return {'path': str(path), 'bytes': size, 'sha256': digest.hexdigest(),
            'tail_max_bytes': TAIL_BYTES, 'tail': tail,
            'restore_finished_status_observed': marker_count > 0,
            'completion_marker': FINISHED.decode(), 'completion_marker_count': marker_count,
            'completion_marker_lines': marker_lines}
