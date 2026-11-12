#!/usr/bin/env python3
"""Compare a finalized capture with the complete self-authored stereo fixture."""
import argparse
from array import array
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_SHA256 = '9ec59f2d67f01acfe41c24c2f658c93b4270d236ac144c7f030d609c36379d2a'


def read_pcm(path):
    raw = path.read_bytes()
    if not 44 <= len(raw) <= 64 * 1024 * 1024:
        raise ValueError('Unexpected capture size')
    with wave.open(str(path), 'rb') as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(),
                stream.getcomptype()) != (2, 2, 48000, 'NONE'):
            raise ValueError('Expected 48 kHz stereo signed16 PCM')
        count = stream.getnframes()
        pcm = stream.readframes(count)
    # The generator and QEMU WAV backend both use the fixed 44-byte header.
    if len(pcm) != count * 4 or len(raw) != 44 + len(pcm):
        raise ValueError('Declared and actual PCM lengths differ')
    return pcm, count, hashlib.sha256(raw).hexdigest()


def channel_measurements(pcm):
    samples = array('h')
    samples.frombytes(pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    result = []
    for index in range(2):
        values = samples[index::2]
        # Exclude both fades; estimate frequency from interpolated rising zeros.
        central = values[4800:-4800]
        crossings = [i - 1 - a / (b - a)
                     for i, (a, b) in enumerate(zip(central, central[1:]), 1)
                     if a <= 0 < b]
        hz = ((len(crossings) - 1) * 48000 / (crossings[-1] - crossings[0])
              if len(crossings) > 1 else None)
        result.append({'channel': 'left' if index == 0 else 'right',
                       'expected_hz': (440, 660)[index],
                       'rising_zero_crossing_hz': hz,
                       'peak_s16': max(map(abs, values), default=0),
                       'rms_s16': math.sqrt(sum(v * v for v in values) / len(values))})
    return result


def validate(reference, capture):
    expected, reference_frames, reference_hash = read_pcm(reference)
    if reference_hash != REFERENCE_SHA256 or reference_frames != 96000:
        raise ValueError('Reference is not the measured two-second fixture')
    actual, capture_frames, capture_hash = read_pcm(capture)
    offset = actual.find(expected)
    exact = offset >= 0 and offset % 4 == 0
    report = {
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'reference': str(reference.resolve()), 'reference_sha256': reference_hash,
        'capture': str(capture.resolve()), 'capture_sha256': capture_hash,
        'format': {'rate_hz': 48000, 'channels': 2, 'sample_bits': 16},
        'reference_frames': reference_frames, 'capture_frames': capture_frames,
        'capture_seconds': capture_frames / 48000,
        'full_tone_bit_exact': exact,
        'scope': 'Complete contiguous interleaved reference bytes in a finalized WAV. '
                 'No sample deletion, reordering, gain correction or header repair. '
                 'Does not establish host speaker output, physical timing or microphone capture.'}
    if exact:
        start = offset // 4
        end = start + reference_frames
        report.update(tone_start_frame=start, tone_end_frame_exclusive=end,
                      tone_seconds=reference_frames / 48000,
                      leading_frames=start,
                      trailing_frames=capture_frames - end,
                      surrounding_samples_all_zero=not any(actual[:offset] + actual[offset + len(expected):]),
                      second_complete_occurrence=actual.find(expected, offset + 4) >= 0,
                      channel_measurements=channel_measurements(actual[offset:offset + len(expected)]))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', required=True, type=Path)
    parser.add_argument('--reference', type=Path,
                        default=ROOT / 'build/audio-test-tone/audio-test-tone.wav')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = validate(args.reference, args.capture)
    with args.output.open('x') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps(report))
    return 0 if report['full_tone_bit_exact'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
