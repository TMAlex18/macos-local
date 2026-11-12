#!/usr/bin/env python3
"""Generate a self-authored stereo PCM reference for guest-to-WAV validation."""
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'build/audio-test-tone'
SERVE = ROOT / 'experiments/E05-installed-boot/guest-diag'
RATE = 48000
FRAMES = RATE * 2
FADE = RATE // 40
FREQUENCIES = (440, 660)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    pcm = array('h')
    for index in range(FRAMES):
        envelope = min(1.0, index / FADE, (FRAMES - 1 - index) / FADE)
        for frequency in FREQUENCIES:
            pcm.append(round(0.2 * 32767 * envelope * math.sin(2 * math.pi * frequency * index / RATE)))
    if sys.byteorder != 'little':
        pcm.byteswap()
    target = OUTPUT / 'audio-test-tone.wav'
    with wave.open(str(target), 'wb') as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes(pcm.tobytes())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    checksum = OUTPUT / 'audio-test-tone.wav.sha256'
    checksum.write_text(f'{digest}  {target.name}\n')
    metadata = {'source': 'tools/build-audio-test-tone.py', 'self_authored': True,
                'format': 'PCM signed16 little-endian stereo', 'sample_rate_hz': RATE,
                'frames': FRAMES, 'duration_seconds': 2, 'left_hz': FREQUENCIES[0],
                'right_hz': FREQUENCIES[1], 'peak_amplitude_fraction': 0.2,
                'linear_fade_seconds': FADE / RATE, 'sha256': digest,
                'scope': 'Reference bytes only; no playback or guest transfer is implied.'}
    (OUTPUT / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    for artifact in (target, checksum):
        link = SERVE / artifact.name
        if os.path.lexists(link):
            if not link.is_symlink() or link.resolve() != artifact.resolve():
                raise FileExistsError(f'Refusing to replace a nonmatching served artifact: {link}')
        else:
            link.symlink_to(os.path.relpath(artifact, SERVE))
    print(json.dumps({'file': str(target), **metadata}))


if __name__ == '__main__':
    main()
