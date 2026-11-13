#!/usr/bin/env python3
"""Independently validate multi-pass WebGL2 offscreen and default RGBA8 readbacks."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 128, 96
BYTE_COUNT = WIDTH * HEIGHT * 4
FIXTURE = 'webgl2-multipass-pipeline-v1'
TAG = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}')
EXPECTED_SHADERS = ('scene_vertex', 'scene_fragment', 'composite_vertex', 'composite_fragment')
EXPECTED_PROGRAMS = ('scene', 'composite')
EXPECTED_PASSES = [
    'pass1a_textured_stencil',
    'pass1b_instanced_depth_stencil',
    'pass1c_alpha_blend',
    'pass2_default_composite',
]
SCOPE = (
    'Full raw RGBA8 equality for one fixed multi-pass WebGL2 pipeline '
    '(offscreen RGBA8 + DEPTH24_STENCIL8 FBO, std140 UBO, 2D texture REPEAT, '
    'stencil masking, depth occlusion, drawArraysInstanced, source-over alpha '
    'blend, and default-framebuffer texelFetch composite). Does not establish '
    'physical scanout, FPS, input latency, or unbounded arbitrary shader coverage.'
)


def sample_pattern_ubo(x, y):
    tx = ((x - 16) // 8) % 4
    ty = ((y - 12) // 6) % 4
    r = 50 + tx * 50
    g = 40 + ty * 50
    b = (200 - ((tx + ty) % 4) * 50) // 2  # std140 UBO u_scale.b = 0.5
    return [r, g, b, 255]


def expected_buffers():
    """Pure-Python reference rasterizer in bottom-left row-major RGBA8 order."""
    color = [[[20, 40, 80, 255] for _ in range(WIDTH)] for _ in range(HEIGHT)]
    depth = [[1.0 for _ in range(WIDTH)] for _ in range(HEIGHT)]
    stencil = [[0 for _ in range(WIDTH)] for _ in range(HEIGHT)]

    # Pass 1a: [16, 112) x [12, 84) at z_ndc = 0.25 -> win_z = 0.625, stencil := 1
    win_z_1a = (0.25 + 1.0) * 0.5
    for y in range(12, 84):
        for x in range(16, 112):
            if win_z_1a <= depth[y][x]:
                color[y][x] = sample_pattern_ubo(x, y)
                depth[y][x] = win_z_1a
                stencil[y][x] = 1

    # Pass 1b: stencil == 1, depth LEQUAL, 3 instances in submission order
    instances = (
        (8, 18, 56, 78, (220, 60, 40, 255), 0.0),
        (48, 24, 104, 72, (250, 20, 220, 255), 0.75),
        (32, 30, 96, 66, (40, 200, 120, 255), -0.5),
    )
    for x0, y0, x1, y1, rgba, z_ndc in instances:
        win_z = (z_ndc + 1.0) * 0.5
        for y in range(y0, y1):
            for x in range(x0, x1):
                if stencil[y][x] == 1 and win_z <= depth[y][x]:
                    color[y][x] = list(rgba)
                    depth[y][x] = win_z

    # Pass 1c: stencil == 1, depth test off, SRC_ALPHA / ONE_MINUS_SRC_ALPHA with RGBA=(255,250,50,51)
    src_rgb = (255, 250, 50)
    src_a = 51
    inv_a = 255 - src_a
    for y in range(36, 60):
        for x in range(8, 120):
            if stencil[y][x] == 1:
                dst = color[y][x]
                for c in range(3):
                    numer = src_rgb[c] * src_a + dst[c] * inv_a
                    assert numer % 255 == 0, 'Reference blend must be exact integer'
                    dst[c] = numer // 255
                alpha_num = 255 * src_a + dst[3] * inv_a
                assert alpha_num % 255 == 0
                dst[3] = alpha_num // 255

    offscreen = bytearray(BYTE_COUNT)
    default = bytearray(BYTE_COUNT)
    pos = 0
    for y in range(HEIGHT):
        for x in range(WIDTH):
            px = color[y][x]
            offscreen[pos:pos + 4] = bytes(px)
            if x < 8 or x >= 120 or y < 6 or y >= 90:
                default[pos:pos + 4] = bytes((15, 25, 35, 255))
            else:
                default[pos:pos + 4] = bytes(px)
            pos += 4
    return bytes(offscreen), bytes(default)


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
    expected_off, expected_def = expected_buffers()
    expected_map = {'offscreen': expected_off, 'default': expected_def}
    result = {
        'schema': 1, 'fixture': FIXTURE, 'tag': tag,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'width': WIDTH, 'height': HEIGHT, 'format': 'RGBA8',
        'row_origin': 'bottom-left', 'expected_bytes': BYTE_COUNT,
        'expected_sha256': {
            name: hashlib.sha256(buf).hexdigest()
            for name, buf in expected_map.items()
        },
        'validation_state': 'unavailable', 'passed': False,
        'outputs': {}, 'errors': [], 'scope': SCOPE,
    }
    report_path = directory / (tag + '.json')
    try:
        raw = read_regular(report_path, 65536)
        report = json.loads(raw)
        result['report'] = {'path': str(report_path), 'sha256': hashlib.sha256(raw).hexdigest()}
        if not isinstance(report, dict):
            raise ValueError('Probe report is not an object')
        metadata = {
            'schema': 1, 'fixture': FIXTURE, 'tag': tag,
            'width': WIDTH, 'height': HEIGHT, 'channels': 4,
            'format': 'RGBA8', 'row_origin': 'bottom-left',
        }
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
    result['renderer_reported'] = {
        key: report.get(key) for key in (
            'version', 'shading_language_version', 'vendor', 'renderer',
            'unmasked_vendor', 'unmasked_renderer', 'context_attributes',
        )
    }

    complete = True
    for name, expected in expected_map.items():
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
                max_abs_diff = max((abs(data[i] - expected[i]) for i in mismatches), default=0)
                row.update(
                    bit_exact=not mismatches,
                    mismatched_bytes=len(mismatches),
                    max_channel_abs_diff=max_abs_diff,
                    first_mismatches=[
                        {
                            'byte_offset': i,
                            'x': (i // 4) % WIDTH,
                            'y_from_bottom': (i // 4) // WIDTH,
                            'channel': i % 4,
                            'expected': expected[i],
                            'actual': data[i],
                        }
                        for i in mismatches[:12]
                    ],
                )
        except (OSError, ValueError) as error:
            complete = False
            row['reason'] = str(error)

    available = sum(row['available'] for row in result['outputs'].values())
    result['validation_state'] = 'complete' if complete else 'incomplete' if available else 'unavailable'

    shaders = report.get('shaders', {})
    programs = report.get('programs', {})
    outputs = report.get('outputs', {})
    uploads = report.get('uploads', {})
    if not all(isinstance(x, dict) for x in (shaders, programs, outputs, uploads)):
        result['errors'].append('Malformed probe result fields')
        return result

    ctx_attrs = report.get('context_attributes')
    result['execution_checks'] = {
        'reported_render_complete': report.get('status') == 'rendered' and report.get('stage') == 'complete',
        'context_created': report.get('context_created') is True,
        'context_not_lost': report.get('context_lost') is False,
        'actual_buffer_dimensions': (
            type(report.get('drawing_buffer_width')) is int and report['drawing_buffer_width'] == WIDTH
            and type(report.get('drawing_buffer_height')) is int and report['drawing_buffer_height'] == HEIGHT
        ),
        'context_attributes': (
            isinstance(ctx_attrs, dict)
            and ctx_attrs.get('alpha') is False
            and ctx_attrs.get('antialias') is False
            and ctx_attrs.get('depth') is True
            and ctx_attrs.get('stencil') is True
            and ctx_attrs.get('preserveDrawingBuffer') is True
        ),
        'shaders_compiled': all(
            isinstance(shaders.get(k), dict) and shaders[k].get('compiled') is True
            for k in EXPECTED_SHADERS
        ),
        'programs_linked': all(
            isinstance(programs.get(k), dict) and programs[k].get('linked') is True
            for k in EXPECTED_PROGRAMS
        ),
        'all_passes_completed': report.get('passes_completed') == EXPECTED_PASSES,
        'no_gl_errors': report.get('gl_errors') == [],
        'framebuffer_metadata': all(
            isinstance(outputs.get(k), dict)
            and outputs[k].get('file') == tag + '-' + k + '.bin'
            and type(outputs[k].get('bytes')) is int and outputs[k]['bytes'] == BYTE_COUNT
            and outputs[k].get('complete') is True
            and outputs[k].get('framebuffer_status') == 0x8cd5
            for k in ('offscreen', 'default')
        ),
        'binary_uploads_acknowledged': all(
            type(uploads.get(k)) is int and uploads[k] == 201
            for k in ('offscreen', 'default')
        ),
    }
    result['passed'] = (
        complete and not result['errors']
        and all(row['bit_exact'] is True for row in result['outputs'].values())
        and all(result['execution_checks'].values())
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
    result = validate(directory / 'guest-uploads', args.tag)
    result['run'] = args.run
    source = ROOT / 'experiments/E05-installed-boot/guest-diag/webgl-complex-probe.html'
    result['current_fixture_source'] = {
        'path': str(source),
        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'scope': 'Current host source; compare the run file-service record for served identity.',
    }
    with output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({
        'report': str(output),
        'passed': result['passed'],
        'validation_state': result['validation_state'],
    }))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
