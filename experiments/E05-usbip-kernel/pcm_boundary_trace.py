#!/usr/bin/env python3
"""Decode the bounded, diagnostic-only virtio-snd x-pcm-trace format v1.

File header: b'VSNDPCM1', u64 byte limit. Each record is 20 little-endian
u64 words named FIELDS below, then sg_count pairs (guest address, length),
then raw_bytes PCM bytes (protocol header excluded). POP snapshots mapped
guest SG at dequeue; POPULATE snapshots the existing lazy copy, not a new
source read. WRITE counts are bytes accepted by audio_be_write, not proof
of backend playback. RETURN reasons: 1 drained, 2 not-started, 3 flush.
CONTROL reason is the request code and accepted is its response status.
END reasons: 1 file closed, 2 cap reached, 3 capture error. Only reason 1
with intact record framing is a complete trace; it does not assert that
all queued audio was consumed. No terminal record means incomplete/unknown.

Stream generation increments on successful SET_PARAMS. Parameters are
VirtIO enum values (S16=5, 48kHz=7), not inferred from a WAV header.
This format is local research evidence, not a stable external interface.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

LIMIT = 8 * 1024 * 1024
HEADER = struct.Struct('<20Q')
FIELDS = ('kind', 'record_bytes', 'virtual_ns', 'sequence', 'stream',
          'generation', 'state', 'channels', 'format', 'rate', 'offset',
          'remaining', 'available', 'requested', 'accepted', 'reason',
          'sg_count', 'raw_bytes', 'aux', 'reserved')
KINDS = {1: 'pop', 2: 'populate', 3: 'write', 4: 'return',
         5: 'control', 6: 'end'}


def decode(data):
    """Retain valid prefix on incomplete/error files; never label it complete."""
    result = {'complete': False, 'terminal': None, 'errors': [], 'records': []}
    if len(data) < 16 or data[:8] != b'VSNDPCM1':
        result['errors'].append('missing or unsupported header')
        return result
    limit, = struct.unpack_from('<Q', data, 8)
    if limit != LIMIT or len(data) > limit:
        result['errors'].append('invalid limit or oversized file')
        return result
    pos = 16
    while pos < len(data):
        if len(data) - pos < HEADER.size:
            result['errors'].append(f'truncated record header at {pos}')
            break
        row = dict(zip(FIELDS, HEADER.unpack_from(data, pos)))
        expected = HEADER.size + row['sg_count'] * 16 + row['raw_bytes']
        if (row['kind'] not in KINDS or row['record_bytes'] != expected or
                expected > limit or row['reserved'] or
                (row['sg_count'] and row['kind'] != 1) or
                (row['raw_bytes'] and row['kind'] not in (1, 2))):
            result['errors'].append(f'invalid record at {pos}')
            break
        end = pos + expected
        if end > len(data):
            result['errors'].append(f'truncated record payload at {pos}')
            break
        payload = pos + HEADER.size
        row['sg'] = [dict(zip(('address', 'length'),
                             struct.unpack_from('<QQ', data, payload + i * 16)))
                     for i in range(row['sg_count'])]
        row['raw'] = data[payload + row['sg_count'] * 16:end]
        row['name'] = KINDS[row['kind']]
        result['records'].append(row)
        if row['kind'] == 6:
            result['terminal'] = row['reason']
            if end != len(data) or row['aux'] != pos:
                result['errors'].append('invalid terminal position/byte count')
            break
        pos = end
    if result['terminal'] is None:
        result['errors'].append('no terminal record')
    result['complete'] = result['terminal'] == 1 and not result['errors']
    return result


def reconstruct(trace):
    """Rebuild accepted bytes by packet/offset, preserving observed order.

    Reject gaps, duplicate consumption, or missing populate data rather than
    silently concatenating a misleading reconstruction. Incomplete traces
    still yield only the verifiable prefix, with explicit errors.
    """
    groups, packets, errors = {}, {}, []
    for row in trace['records']:
        key = (row['stream'], row['generation'])
        if row['kind'] in (1, 2):
            group = groups.setdefault(key, {'pop': bytearray(),
                                            'populate': bytearray(),
                                            'accepted': bytearray()})
            group[row['name']].extend(row['raw'])
        if row['kind'] == 1:
            if row['sequence'] in packets:
                errors.append('duplicate packet sequence')
            packets[row['sequence']] = {'pop': row, 'position': 0}
        elif row['kind'] == 2:
            packet = packets.get(row['sequence'])
            if packet is None or 'populate' in packet:
                errors.append('missing pop or repeated populate')
            else:
                packet['populate'] = row
                if row['accepted'] != row['raw_bytes']:
                    errors.append('short populate copy')
        elif row['kind'] == 3:
            packet = packets.get(row['sequence'], {})
            raw = packet.get('populate', {}).get('raw', b'')
            offset, accepted = row['offset'], row['accepted']
            if (offset != packet.get('position') or
                    accepted > row['requested'] or
                    row['requested'] > min(row['available'], row['remaining']) or
                    offset + row['remaining'] != len(raw)):
                errors.append(f'invalid write accounting for {row["sequence"]}')
                continue
            groups[key]['accepted'].extend(raw[offset:offset + accepted])
            packet['position'] += accepted
        elif row['kind'] == 4:
            packet = packets.get(row['sequence'], {})
            original = packet.get('pop', {}).get('raw_bytes')
            if (row['offset'] != packet.get('position') or original is None or
                    row['offset'] + row['remaining'] != original or
                    (row['reason'] == 1 and row['remaining'] != 0)):
                errors.append(f'invalid return accounting for {row["sequence"]}')
            packet['return'] = row
    return {'groups': groups, 'packets': packets, 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    data = args.trace.read_bytes()
    trace = decode(data)
    rebuilt = reconstruct(trace)
    args.output_dir.mkdir(exist_ok=False)
    summary = {**trace, 'trace_sha256': hashlib.sha256(data).hexdigest(),
               'reconstruction_errors': rebuilt['errors'], 'outputs': []}
    summary['records'] = [{**{k: v for k, v in row.items() if k != 'raw'},
                           'raw_sha256': hashlib.sha256(row['raw']).hexdigest()}
                          for row in trace['records']]
    for (stream, generation), group in rebuilt['groups'].items():
        for name, raw in group.items():
            path = args.output_dir / f'stream-{stream}-generation-{generation}-{name}.pcm'
            with path.open('xb') as output:
                output.write(raw)
            summary['outputs'].append({'path': path.name, 'bytes': len(raw),
                                       'sha256': hashlib.sha256(raw).hexdigest()})
    with (args.output_dir / 'index.json').open('x') as output:
        json.dump(summary, output, indent=2)
        output.write('\n')
    print(json.dumps({'complete': trace['complete'],
                      'errors': trace['errors'] + rebuilt['errors'],
                      'index': str(args.output_dir / 'index.json')}))
    return 0 if trace['complete'] and not rebuilt['errors'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
