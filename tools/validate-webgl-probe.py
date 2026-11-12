#!/usr/bin/env python3
"""Independently validate the fixed WebGL2 six-tile raw readbacks."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 96, 64
BYTE_COUNT = WIDTH * HEIGHT * 4
FIXTURE = 'webgl2-six-tiles-v1'
TAG = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}')
SCOPE = ('Full raw RGBA8 equality for one fixed WebGL2 shader in an offscreen '
         'and default framebuffer. Does not establish physical GPU execution, '
         'scanout, FPS, latency, general WebGL/Metal coverage or original Safari deadlines.')


def expected_pixels():
    # readPixels starts at the lower-left. Each horizontal band is 32 rows.
    lower = bytes([255, 0, 0, 255]) * 32 + bytes([0, 255, 0, 255]) * 32 + bytes([0, 0, 255, 255]) * 32
    upper = bytes([0, 255, 255, 255]) * 32 + bytes([255, 0, 255, 255]) * 32 + bytes([255, 255, 0, 255]) * 32
    return lower * 32 + upper * 32


def read_regular(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_size > maximum:
            raise ValueError('Unexpected input type, ownership or size: ' + path.name)
        chunks, remaining = [], maximum + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    data = b''.join(chunks)
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or len(data) != after.st_size):
        raise ValueError('Input changed while reading: ' + path.name)
    return data


def validate(directory, tag):
    if not TAG.fullmatch(tag):
        raise ValueError('Invalid result tag')
    expected = expected_pixels()
    result = {'schema': 1, 'fixture': FIXTURE, 'tag': tag,
              'observed_at_utc': datetime.now(timezone.utc).isoformat(),
              'width': WIDTH, 'height': HEIGHT, 'format': 'RGBA8',
              'row_origin': 'bottom-left', 'expected_bytes': BYTE_COUNT,
              'expected_sha256': hashlib.sha256(expected).hexdigest(),
              'validation_state': 'unavailable', 'passed': False, 'outputs': {},
              'errors': [], 'scope': SCOPE}
    report_path = directory / (tag + '.json')
    try:
        raw = read_regular(report_path, 65536)
        report = json.loads(raw)
        result['report'] = {'path': str(report_path), 'sha256': hashlib.sha256(raw).hexdigest()}
        if not isinstance(report, dict):
            raise ValueError('Probe report is not an object')
        metadata = {'schema': 1, 'fixture': FIXTURE, 'tag': tag, 'width': WIDTH,
                    'height': HEIGHT, 'channels': 4, 'format': 'RGBA8', 'row_origin': 'bottom-left'}
        for key, value in metadata.items():
            if type(report.get(key)) is not type(value) or report[key] != value:
                raise ValueError('Probe metadata differs: ' + key)
    except (OSError, ValueError) as error:
        result['errors'].append(str(error))
        return result
    result['reported_status'] = report.get('status')
    result['reported_stage'] = report.get('stage')
    result['reported_error'] = report.get('error')
    result['context_created'] = report.get('context_created')
    result['context_lost'] = report.get('context_lost')
    result['renderer_reported'] = {key: report.get(key) for key in
                                  ('version', 'shading_language_version', 'vendor', 'renderer',
                                   'unmasked_vendor', 'unmasked_renderer', 'context_attributes')}
    complete = True
    for name in ('offscreen', 'default'):
        path = directory / (tag + '-' + name + '.bin')
        row = {'path': str(path), 'available': False, 'bit_exact': None}
        result['outputs'][name] = row
        try:
            data = read_regular(path, BYTE_COUNT + 1)
            row.update(available=True, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            if len(data) != BYTE_COUNT:
                row['bit_exact'] = False
                row['reason'] = 'Raw readback length differs'
            else:
                mismatches = [i for i, (a, b) in enumerate(zip(data, expected)) if a != b]
                row.update(bit_exact=not mismatches, mismatched_bytes=len(mismatches),
                           first_mismatches=[{'byte_offset': i, 'x': (i // 4) % WIDTH,
                                             'y_from_bottom': (i // 4) // WIDTH, 'channel': i % 4,
                                             'expected': expected[i], 'actual': data[i]}
                                            for i in mismatches[:12]])
        except (OSError, ValueError) as error:
            complete = False
            row['reason'] = str(error)
    available = sum(row['available'] for row in result['outputs'].values())
    result['validation_state'] = 'complete' if complete else 'incomplete' if available else 'unavailable'
    shaders = report.get('shaders', {})
    outputs = report.get('outputs', {})
    uploads = report.get('uploads', {})
    if not all(isinstance(x, dict) for x in (shaders, outputs, uploads)):
        result['errors'].append('Malformed probe result fields')
        return result
    result['execution_checks'] = {
        'reported_render_complete': report.get('status') == 'rendered' and report.get('stage') == 'complete',
        'context_created': report.get('context_created') is True,
        'context_not_lost': report.get('context_lost') is False,
        'actual_buffer_dimensions': type(report.get('drawing_buffer_width')) is int
            and report['drawing_buffer_width'] == WIDTH
            and type(report.get('drawing_buffer_height')) is int
            and report['drawing_buffer_height'] == HEIGHT,
        'context_attributes': isinstance(report.get('context_attributes'), dict)
            and all(report['context_attributes'].get(k) is False for k in ('alpha', 'antialias', 'depth', 'stencil'))
            and report['context_attributes'].get('preserveDrawingBuffer') is True,
        'program_linked': report.get('program_linked') is True,
        'shaders_compiled': all(isinstance(shaders.get(k), dict) and shaders[k].get('compiled') is True
                                for k in ('vertex', 'fragment')),
        'no_gl_errors': report.get('gl_errors') == [],
        'framebuffer_metadata': all(isinstance(outputs.get(k), dict)
            and outputs[k].get('file') == tag + '-' + k + '.bin'
            and type(outputs[k].get('bytes')) is int and outputs[k]['bytes'] == BYTE_COUNT
            and outputs[k].get('complete') is True
            and outputs[k].get('framebuffer_status') == 0x8cd5 for k in ('offscreen', 'default')),
        'binary_uploads_acknowledged': all(type(uploads.get(k)) is int and uploads[k] == 201
                                           for k in ('offscreen', 'default')),
    }
    result['passed'] = (complete and not result['errors'] and
                        all(row['bit_exact'] is True for row in result['outputs'].values()) and
                        all(result['execution_checks'].values()))
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
    result = validate(directory / 'guest-uploads', args.tag)
    result['run'] = args.run
    source = ROOT / 'experiments/E05-installed-boot/guest-diag/webgl-probe.html'
    result['current_fixture_source'] = {'path': str(source),
                                      'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                                      'scope': 'Current host source; compare the run file-service record for served identity.'}
    with output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'report': str(output), 'passed': result['passed'],
                      'validation_state': result['validation_state']}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
