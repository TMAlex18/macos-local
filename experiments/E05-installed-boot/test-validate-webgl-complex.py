#!/usr/bin/env python3
"""Offline unit tests for tools/validate-webgl-complex.py."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('validate_webgl_complex', ROOT / 'tools/validate-webgl-complex.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def pixel(buf, x, y):
    idx = (y * MOD.WIDTH + x) * 4
    return tuple(buf[idx:idx + 4])


def valid_report(tag):
    return {
        'schema': 1,
        'fixture': MOD.FIXTURE,
        'tag': tag,
        'width': MOD.WIDTH,
        'height': MOD.HEIGHT,
        'channels': 4,
        'format': 'RGBA8',
        'row_origin': 'bottom-left',
        'status': 'rendered',
        'stage': 'complete',
        'context_created': True,
        'context_lost': False,
        'drawing_buffer_width': MOD.WIDTH,
        'drawing_buffer_height': MOD.HEIGHT,
        'context_attributes': {
            'alpha': False,
            'antialias': False,
            'depth': True,
            'stencil': True,
            'preserveDrawingBuffer': True,
        },
        'shaders': {k: {'compiled': True, 'log': ''} for k in MOD.EXPECTED_SHADERS},
        'programs': {k: {'linked': True, 'log': ''} for k in MOD.EXPECTED_PROGRAMS},
        'passes_completed': list(MOD.EXPECTED_PASSES),
        'gl_errors': [],
        'outputs': {
            name: {
                'file': f'{tag}-{name}.bin',
                'bytes': MOD.BYTE_COUNT,
                'complete': True,
                'framebuffer_status': 0x8cd5,
            }
            for name in ('offscreen', 'default')
        },
        'uploads': {'offscreen': 201, 'default': 201},
    }


class WebGLComplexValidatorTests(unittest.TestCase):
    def test_reference_rasterizer_separates_every_pipeline_feature(self):
        off, default = MOD.expected_buffers()
        self.assertEqual(len(off), MOD.BYTE_COUNT)
        self.assertEqual(len(default), MOD.BYTE_COUNT)

        # 1. Stencil clip at x=12 (Instance 0 spans x=8..56, but stencil=1 starts at x=16)
        self.assertEqual(pixel(off, 12, 20), (20, 40, 80, 255))
        # 2. Instance 0 inside stencil (x=20, y=20) unblended
        self.assertEqual(pixel(off, 20, 20), (220, 60, 40, 255))
        # 3. Instance 1 (z_ndc=0.75 > 0.25) occluded by Pass 1a textured quad at (x=100, y=26)
        self.assertNotEqual(pixel(off, 100, 26), (250, 20, 220, 255))
        self.assertEqual(pixel(off, 100, 26), tuple(MOD.sample_pattern_ubo(100, 26)))
        # 4. Instance 2 (z_ndc=-0.5) overwrites Instance 0 (z_ndc=0.0) at (x=40, y=32)
        self.assertEqual(pixel(off, 40, 32), (40, 200, 120, 255))
        # 5. Alpha blend (alpha=51/255=1/5, RGB=(255,250,50)) over Instance 2 at (x=40, y=48):
        #    ((255 + 4*40)/5, (250 + 4*200)/5, (50 + 4*120)/5) = (83, 210, 106)
        self.assertEqual(pixel(off, 40, 48), (83, 210, 106, 255))
        # 6. Alpha blend clipped outside stencil at (x=12, y=48)
        self.assertEqual(pixel(off, 12, 48), (20, 40, 80, 255))
        # 7. Pass 2 default framebuffer border vs interior composite
        self.assertEqual(pixel(default, 4, 48), (15, 25, 35, 255))
        self.assertEqual(pixel(default, 12, 48), (20, 40, 80, 255))
        self.assertEqual(pixel(default, 40, 48), (83, 210, 106, 255))

    def test_positive_and_negative_controls(self):
        off, default = MOD.expected_buffers()
        tag = 'unit-complex'
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / f'{tag}-offscreen.bin').write_bytes(off)
            (d / f'{tag}-default.bin').write_bytes(default)
            (d / f'{tag}.json').write_text(json.dumps(valid_report(tag)))
            pos = MOD.validate(d, tag)
            self.assertTrue(pos['passed'])
            self.assertEqual(pos['validation_state'], 'complete')

        for case in ('byte_flip', 'missing_pass', 'depth_off', 'stencil_off',
                     'lost_context', 'truncated', 'missing_default', 'symlink'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                d = Path(tmp)
                off_mut = bytearray(off)
                rep = valid_report(tag)
                if case == 'byte_flip':
                    off_mut[(48 * MOD.WIDTH + 40) * 4] ^= 1
                elif case == 'missing_pass':
                    rep['passes_completed'] = MOD.EXPECTED_PASSES[:-1]
                elif case == 'depth_off':
                    rep['context_attributes']['depth'] = False
                elif case == 'stencil_off':
                    rep['context_attributes']['stencil'] = False
                elif case == 'lost_context':
                    rep['context_lost'] = True
                elif case == 'truncated':
                    off_mut = off_mut[:-4]
                (d / f'{tag}-offscreen.bin').write_bytes(off_mut)
                if case != 'missing_default':
                    (d / f'{tag}-default.bin').write_bytes(default)
                if case == 'symlink':
                    target = d / 'other.bin'
                    target.write_bytes(off)
                    (d / f'{tag}-offscreen.bin').unlink()
                    (d / f'{tag}-offscreen.bin').symlink_to(target)
                (d / f'{tag}.json').write_text(json.dumps(rep))
                res = MOD.validate(d, tag)
                self.assertFalse(res['passed'])


if __name__ == '__main__':
    unittest.main()
