#!/usr/bin/env python3
"""Real diskless qtest sound control/PCM boundaries; no Apple CPU or host audio.

Use a separately coordinated candidate, or explicitly validate the rebuilt
main executable with --mainline-integration after its live VMs have ended:
  python3 test-pcm-boundary.py --binary /.../candidate/qemu-system-aarch64 \
      --output /.../new-evidence-directory
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import socket
import struct
import subprocess
import tempfile
import time
import wave

from pcm_boundary_trace import LIMIT, decode, reconstruct

ROOT = Path(__file__).resolve().parents[2]


def pcm(frames=32768, seed=0):
    # A bijective 32-bit frame counter, interpreted as interleaved S16 L/R.
    # Unlike a periodic sine, missing/duplicated frames are uniquely visible.
    return b''.join(struct.pack('<I', ((i + seed) * 2654435761) & 0xffffffff)
                    for i in range(frames))


class Fixture:
    def __init__(self, binary, directory, trace=True, file_limit=None, production=False,
                 item_counts=None, control_trace=False, queue_size=None, streams=None):
        self.directory = directory
        directory.mkdir()
        self.temp = tempfile.TemporaryDirectory(prefix='pcm-qtest-')
        self.sockpath = Path(self.temp.name) / 'qtest'
        self.qmppath = Path(self.temp.name) / 'qmp'
        self.tracepath = directory / 'boundary.bin'
        self.wavpath = directory / 'capture.wav'
        self.args = [str(binary), '-machine', 'virt,highmem=off', '-cpu', 'max',
                     '-accel', 'qtest', '-m', '128M', '-nodefaults', '-nic', 'none',
                     '-display', 'none', '-serial', 'none', '-monitor', 'none',
                     '-audiodev', f'wav,id=snd,path={self.wavpath},out.frequency=48000,'
                     'out.channels=2,out.format=s16',
                     '-device', 'virtio-sound-pci,id=sound,audiodev=snd,addr=0x6,'
                     'x-ascending-capabilities=on' +
                     (f',x-pcm-trace={self.tracepath}' if trace else ''),
                     '-qtest', f'unix:{self.sockpath},server=on,wait=off',
                     '-qtest-log', '/dev/null',
                     '-qmp', f'unix:{self.qmppath},server=on,wait=off']
        if item_counts is not None:
            device = self.args.index('-device') + 1
            self.args[device] += f',jacks={item_counts[0]},chmaps={item_counts[1]}'
        if queue_size is not None:
            device = self.args.index('-device') + 1
            self.args[device] += f',queue-size={queue_size}'
        if streams is not None:
            device = self.args.index('-device') + 1
            self.args[device] += f',streams={streams}'
        self.control_trace_path = directory / 'control.log'
        if control_trace:
            from firmware_session import VIRTIO_SOUND_CONTROL_TRACE_EVENTS
            events = directory / 'control-events.txt'
            events.write_text('\n'.join(VIRTIO_SOUND_CONTROL_TRACE_EVENTS) + '\n')
            self.args += ['-trace', f'events={events},file={self.control_trace_path}']
        self.production_audio = None
        if production:
            from firmware_session import FirmwareSession
            session = FirmwareSession(directory, virtio_sound=True,
                                      sound_backend='wav', sound_pcm_trace=True,
                                      sound_queue_size=queue_size,sound_streams=streams)
            try:
                audio_args = session.sound_arguments()
                self.production_audio = session.execution_metadata()['audio']
                start = self.args.index('-audiodev')
                self.args[start:start + 4] = audio_args
                self.wavpath = Path(self.production_audio['output_file'])
                self.tracepath = Path(self.production_audio['pcm_trace']['output_file'])
            finally:
                session.close()
        self.err = (directory / 'stderr.log').open('wb')
        def child_limits():
            if file_limit is not None:
                signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
                resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
        self.proc = subprocess.Popen(self.args, stdout=subprocess.DEVNULL,
                                     stderr=self.err, preexec_fn=child_limits)
        self.qt = self.qmp = None
        self.qf = self.qmf = None
        self.queues = {}
        deadline = time.monotonic() + 8
        while not self.sockpath.exists() or not self.qmppath.exists():
            if self.proc.poll() is not None or time.monotonic() > deadline:
                self.close()
                raise RuntimeError(f'qtest startup failed; see {directory}')
            time.sleep(.01)
        self.qt = socket.socket(socket.AF_UNIX)
        self.qt.settimeout(5)
        self.qt.connect(str(self.sockpath))
        self.qf = self.qt.makefile('rb')
        self.qmp = socket.socket(socket.AF_UNIX)
        self.qmp.settimeout(5)
        self.qmp.connect(str(self.qmppath))
        self.qmf = self.qmp.makefile('rb')
        json.loads(self.qmf.readline())
        self.request('qmp_capabilities')

    def request(self, execute, arguments=None):
        message={'execute':execute}
        if arguments is not None:message['arguments']=arguments
        self.qmp.sendall((json.dumps(message) + '\n').encode())
        while True:
            row = json.loads(self.qmf.readline())
            if 'return' in row:
                return row['return']
            if 'error' in row:
                raise RuntimeError(row)

    def command(self, text):
        self.qt.sendall((text + '\n').encode())
        while True:
            reply = self.qf.readline().decode().strip().split()
            if reply and reply[0] == 'IRQ':
                continue
            if not reply or reply[0] != 'OK':
                raise RuntimeError(f'{text[:80]}: {reply}')
            return reply[1] if len(reply) > 1 else None

    def read(self, width, address):
        return int(self.command(f'read{width} {address:#x}'), 0)

    def write(self, address, data):
        for pos in range(0, len(data), 16384):
            block = data[pos:pos + 16384]
            self.command(f'write {address + pos:#x} {len(block):#x} 0x{block.hex()}')

    def setup(self, indirect=False, queue_size=8, event_idx=False):
        ecam = 0x3f000000 + (6 << 15)
        config = bytes(self.read('b', ecam + i) for i in range(256))
        assert config[:4] == bytes.fromhex('f41a5910')
        bars = {1: 0x10000000, 4: 0x10004000}
        self.command(f'writel {ecam + 0x14:#x} {bars[1]:#x}')
        self.command(f'writel {ecam + 0x20:#x} {bars[4]:#x}')
        self.command(f'writel {ecam + 0x24:#x} 0')
        self.command(f'writew {ecam + 4:#x} 6')
        caps, seen, offset = {}, set(), config[0x34]
        while offset:
            assert 0x40 <= offset <= 0xfc and offset not in seen
            seen.add(offset)
            if config[offset] == 9:
                caps[config[offset + 3]] = offset
            offset = config[offset + 1]
        def cap(kind):
            offset = caps[kind]
            return bars[config[offset + 4]] + struct.unpack_from('<I', config, offset + 8)[0]
        self.common, self.notify, self.isr, self.device_config = cap(1), cap(2), cap(3), cap(4)
        self.multiplier = struct.unpack_from('<I', config, caps[2] + 16)[0]
        self.command(f'writeb {self.common + 20:#x} 0')
        self.command(f'writeb {self.common + 20:#x} 3')
        if indirect or event_idx:
            self.command(f'writel {self.common:#x} 0')
            selected = (1 << 28 if indirect else 0) | (1 << 29 if event_idx else 0)
            assert self.read('l', self.common + 4) & selected == selected
            self.command(f'writel {self.common + 8:#x} 0')
            self.command(f'writel {self.common + 12:#x} {selected}')
        self.command(f'writel {self.common + 8:#x} 1')
        self.command(f'writel {self.common + 12:#x} 1')
        self.command(f'writeb {self.common + 20:#x} 11')
        assert self.read('b', self.common + 20) == 11
        for queue in (range(4) if indirect else (0, 2)):
            self.command(f'writew {self.common + 22:#x} {queue}')
            advertised = self.read('w', self.common + 24)
            assert advertised >= queue_size
            self.command(f'writew {self.common + 24:#x} {queue_size}')
            assert self.read('w', self.common + 24) == queue_size
            base = 0x40010000 + queue * 0x10000
            desc = base
            avail = (desc + 16 * queue_size + 4095) & -4096
            used = (avail + 6 + 2 * queue_size + 4095) & -4096
            for offset, address in ((32, desc), (40, avail), (48, used)):
                self.command(f'writel {self.common + offset:#x} {address:#x}')
                self.command(f'writel {self.common + offset + 4:#x} 0')
            self.write(avail, bytes(6 + 2 * queue_size))
            self.write(used - 16, b'\xa5' * 16 + bytes(6 + 8 * queue_size) + b'\x5a' * 16)
            self.command(f'writew {self.common + 28:#x} 1')
            notify = self.notify + self.multiplier * self.read('w', self.common + 30)
            self.queues[queue] = {'desc': desc, 'avail': avail, 'used': used,
                                  'notify': notify, 'sequence': 0, 'size': queue_size,
                                  'advertised_maximum': advertised, 'event_idx': event_idx}
        self.command(f'writeb {self.common + 20:#x} 15')

    def submit(self, queue, segments, indirect=False, head=0):
        q = self.queues[queue]
        assert 0 <= head < q['size'] and (indirect or head == 0)
        desc = b''
        for i, (address, length, writable) in enumerate(segments):
            flags = (2 if writable else 0) | (1 if i + 1 < len(segments) else 0)
            desc += struct.pack('<QIHH', address, length, flags, i + 1)
        if indirect:
            table = q['desc'] + 0x8000
            self.write(table, desc)
            desc = struct.pack('<QIHH', table, len(desc), 4, 0)
        self.write(q['desc'] + 16 * head, desc)
        self.command(f'writew {q["avail"] + 4 + 2 * (q["sequence"] % q["size"]):#x} {head}')
        q['sequence'] += 1
        self.command(f'writew {q["avail"] + 2:#x} {q["sequence"] & 0xffff}')
        self.command(f'writew {q["notify"]:#x} {queue}')

    def control(self, code, params=False):
        body = (struct.pack('<IIIII4B', code, 0, 131072, 4096, 0, 2, 5, 7, 0)
                if params else struct.pack('<II', code, 0))
        payload, response = 0x40050000, 0x40051000
        self.write(payload, body)
        self.write(response, b'\xff' * 4)
        self.submit(0, [(payload, len(body), False), (response, 4, True)])
        assert self.read('w', self.queues[0]['used'] + 2) == self.queues[0]['sequence']
        assert self.read('l', response) == 0x8000

    def enqueue(self, data):
        combined = struct.pack('<I', 0) + data
        # Split the four-byte protocol header and place SG buffers across pages.
        addresses = (0x40100fff, 0x40102001, 0x40200ffd)
        chunks = (combined[:2], combined[2:4097], combined[4097:])
        self.segments = list(zip(addresses, chunks))
        for address, chunk in self.segments:
            self.write(address, chunk)
        self.response = 0x40052000
        self.write(self.response, b'\xff' * 8)
        self.submit(2, [(address, len(chunk), False) for address, chunk in self.segments] +
                       [(self.response, 8, True)])

    def mutate(self, data):
        combined = struct.pack('<I', 0) + data
        pos = 0
        for address, old in self.segments:
            self.write(address, combined[pos:pos + len(old)])
            pos += len(old)

    def step(self, ns=10000000):
        self.command(f'clock_step {ns}')

    def drained(self):
        return self.read('w', self.queues[2]['used'] + 2) == self.queues[2]['sequence']

    def close(self):
        if self.proc.poll() is None:
            try:
                if self.qmp:
                    self.request('quit')
                else:
                    self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
                self.proc.wait(timeout=5)
        for resource_ in (self.qf, self.qt, self.qmf, self.qmp, self.err):
            if resource_:
                resource_.close()
        self.temp.cleanup()


def run_case(binary, directory, mode):
    data = pcm(12000 if mode == 'io-error' else 32768)
    changed = pcm(len(data) // 4, seed=1000000)
    fixture = Fixture(binary, directory, trace=mode != 'off', production=mode == 'production',
                      file_limit=65536 if mode == 'io-error' else None)
    result = {'mode': mode, 'command': fixture.args, 'guest_cpu_executed': False,
              'host_audio_hardware_used': False}
    if fixture.production_audio is not None:
        result['production_audio'] = fixture.production_audio
    try:
        fixture.setup()
        fixture.control(0x101, params=True)
        fixture.control(0x102)
        if mode == 'cap':
            address = 0x41000000
            fixture.command(f'memset {address:#x} {LIMIT + 4:#x} 0')
            fixture.response = 0x40052000
            fixture.write(fixture.response, b'\xff' * 8)
            fixture.submit(2, [(address, LIMIT + 4, False), (fixture.response, 8, True)])
            fixture.control(0x103)
        else:
            fixture.enqueue(data)
            assert not fixture.drained(), 'pop must not complete before START'
            if mode == 'mutation':
                fixture.mutate(changed)
            fixture.control(0x104)
            fixture.step()
            if mode == 'stop-flush':
                assert not fixture.drained(), 'packet must still be partially owned'
            else:
                for _ in range(600):
                    if fixture.drained():
                        break
                    fixture.step()
                assert fixture.drained(), 'bounded PCM completion deadline'
                # Drain the audio mixer after device acceptance before closing.
                for _ in range(20):
                    fixture.step()
            fixture.control(0x105)
            fixture.control(0x103)
        assert fixture.drained()
        assert fixture.read('l', fixture.response) == 0x8000
        used = fixture.queues[2]['used']
        assert fixture.read('l', used + 8) == 8
        result['tx_completed_ok'] = True
    finally:
        fixture.close()
        result['qemu_exit_code'] = fixture.proc.returncode
        result['qemu_reaped'] = fixture.proc.poll() is not None
    with wave.open(str(fixture.wavpath), 'rb') as stream:
        assert (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) == (2, 2, 48000)
        wav = stream.readframes(stream.getnframes())
    result['wav_bytes'] = len(wav)
    result['wav_sha256'] = hashlib.sha256(wav).hexdigest()
    if mode == 'off':
        assert not fixture.tracepath.exists()
        assert wav == data
    else:
        trace = decode(fixture.tracepath.read_bytes())
        rebuilt = reconstruct(trace)
        result.update(trace_complete=trace['complete'], terminal=trace['terminal'],
                      trace_errors=trace['errors'], reconstruction_errors=rebuilt['errors'],
                      trace_bytes=fixture.tracepath.stat().st_size,
                      counts={name: sum(row['name'] == name for row in trace['records'])
                              for name in ('pop', 'populate', 'write', 'return', 'control')})
        if mode == 'cap':
            assert trace['terminal'] == 2 and not trace['complete']
            assert fixture.tracepath.stat().st_size <= LIMIT
            assert not trace['errors'] and not wav
        elif mode == 'io-error':
            assert not trace['complete'] and trace['errors']
            assert wav == data, 'trace I/O failure must not change PCM delivery'
        else:
            assert trace['complete'] and not rebuilt['errors']
            group = rebuilt['groups'][(0, 1)]
            packet, = rebuilt['packets'].values()
            assert group['pop'] == data
            assert len(packet['pop']['sg']) >= 3
            assert (packet['pop']['channels'], packet['pop']['format'], packet['pop']['rate']) == (2, 5, 7)
            writes = [row for row in trace['records'] if row['kind'] == 3 and row['accepted']]
            assert writes and any(row['accepted'] < row['remaining'] for row in writes)
            if mode == 'stop-flush':
                assert 0 < len(group['accepted']) < len(data)
                assert group['accepted'] == data[:len(group['accepted'])]
                assert packet['return']['reason'] in (2, 3)
                assert packet['return']['remaining'] == len(data) - len(group['accepted'])
            else:
                expected = changed if mode == 'mutation' else data
                assert group['populate'] == expected
                assert group['accepted'] == expected
                assert packet['return']['reason'] == 1
                assert wav == expected
            result['accepted_bytes'] = len(group['accepted'])
            result['return_reason'] = packet['return']['reason']
            result['write_count'] = len(writes)
    assert result['qemu_exit_code'] == 0 and result['qemu_reaped']
    result['passed'] = True
    (directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def run_item_info(binary, directory, baseline=False):
    """Replay Run117's indirect query, then exercise the same control boundary."""
    directory.mkdir()
    result = {'mode': 'item-info-baseline' if baseline else 'item-info',
              'scope': 'Actual diskless indirect descriptors; no dispatch-time claim about the Run117 snapshot.',
              'devices': [], 'passed': False}
    for profile, counts in [('default', None)] + ([] if baseline else [('nonempty', (2, 2))]):
        fixture = Fixture(binary, directory / profile, trace=False, item_counts=counts,
                          control_trace=not baseline)
        row = {'profile': profile, 'command': fixture.args, 'queries': []}
        accepted_empty = []
        result['devices'].append(row)
        try:
            fixture.setup(indirect=True)
            config = [fixture.read('l', fixture.device_config + offset) for offset in (0, 4, 8)]
            assert config == ([0, 2, 0] if counts is None else [2, 2, 2])
            row['advertised_jacks_streams_chmaps'] = config

            def query(label, code, start=0, count=0, size=24, capacity=4,
                      expected=0x8000, truncate=16, split=False, payload_bytes=0):
                request = struct.pack('<IIII', code, start, count, size)[:truncate]
                payload, response = 0x40054010, 0x40055010
                prefix, suffix = b'\xa5' * 16, b'\x5a' * 16
                fixture.write(payload - 16, prefix + request + suffix)
                fixture.write(response - 16, prefix + b'\xcc' * capacity + suffix)
                segments = [(payload, len(request), False)]
                if split:
                    segments = [(payload, 6, False), (payload + 6, len(request) - 6, False)]
                if capacity:
                    segments += ([(response, 1, True), (response + 1, capacity - 1, True)]
                                 if split else [(response, capacity, True)])
                fixture.submit(0, segments, indirect=True)
                q = fixture.queues[0]
                used_index = fixture.read('w', q['used'] + 2)
                assert used_index == q['sequence'], (label, used_index, q['sequence'])
                used_entry = q['used'] + 4 + 8 * ((q['sequence'] - 1) % 8)
                used_id = fixture.read('l', used_entry)
                used_length = fixture.read('l', used_entry + 4)
                actual = bytes.fromhex(fixture.command(f'read {response - 16:#x} {capacity + 32:#x}')[2:])
                actual_request = bytes.fromhex(fixture.command(f'read {payload - 16:#x} {len(request) + 32:#x}')[2:])
                response_bytes = actual[16:16 + capacity]
                row['queries'].append({'label': label, 'request_hex': request.hex(),
                                       'response_capacity': capacity, 'response_hex': response_bytes.hex(),
                                       'expected_status': expected, 'used_index': used_index,
                                       'used_id': used_id, 'used_length': used_length,
                                       'indirect': True, 'split_buffers': split})
                assert actual[:16] == prefix and actual[16 + capacity:] == suffix, label
                assert actual_request == prefix + request + suffix, label
                assert used_id == 0
                assert used_length == min(4, capacity) + payload_bytes, label
                assert response_bytes[:4] == struct.pack('<I', expected)[:capacity], label
                assert response_bytes[4 + payload_bytes:] == b'\xcc' * max(0, capacity - 4 - payload_bytes), label
                row['queries'][-1].update(canaries_unchanged=True, request_unchanged=True, passed=True)
                if not baseline and code in (1, 0x200) and expected == 0x8000:
                    accepted_empty.append((code, start, count, size, config[0 if code == 1 else 2]))

            if baseline:
                query('run117-observed-empty-jack', 1, expected=0x8002)
                query('empty-chmap', 0x200, expected=0x8002)
            elif counts is None:
                # Exact request bytes and descriptor lengths read after Run117's
                # completion. This new execution independently proves dispatch.
                query('run117-observed-empty-jack', 1)
                query('empty-chmap', 0x200)
                for code, name in [(1, 'jack'), (0x200, 'chmap')]:
                    query(name + '-empty-size-zero', code, size=0)
                    query(name + '-empty-size-max', code, size=0xffffffff)
                    query(name + '-split-header', code, capacity=12, split=True)
                    query(name + '-start-out-of-range', code, start=1, expected=0x8001)
                    query(name + '-nonempty-out-of-range', code, count=1, capacity=28, expected=0x8001)
                    query(name + '-range-wrap', code, start=0xffffffff, count=1, expected=0x8001)
                    for length in (4, 8, 15):
                        query(name + '-short-query-' + str(length), code, truncate=length, expected=0x8001)
                    for capacity in range(4):
                        query(name + '-short-response-' + str(capacity), code, capacity=capacity, expected=0x8001)
                query('pcm-empty-at-end', 0x100, start=2, size=32)
                query('pcm-two-stream-info', 0x100, count=2, size=32, capacity=68, payload_bytes=64)
                # Consume the control sequence on the same device through PCM.
                data = pcm(1024)
                fixture.control(0x101, params=True)
                fixture.control(0x102)
                fixture.enqueue(data)
                fixture.control(0x104)
                for _ in range(100):
                    if fixture.drained():
                        break
                    fixture.step()
                assert fixture.drained()
                for _ in range(20):
                    fixture.step()
                fixture.control(0x105)
                fixture.control(0x103)
                assert fixture.read('l', fixture.response) == 0x8000
                row['pcm_after_info_expected_sha256'] = hashlib.sha256(data).hexdigest()
            else:
                for code, name in [(1, 'jack'), (0x200, 'chmap')]:
                    query(name + '-empty-at-end', code, start=2)
                    query(name + '-valid-nonempty-unsupported', code, count=1, capacity=28, expected=0x8002)
                    query(name + '-payload-product-overflow', code, count=2, size=0x80000000, expected=0x8001)
                    query(name + '-response-add-overflow', code, count=1, size=0xffffffff, expected=0x8001)
                    query(name + '-nonempty-short-buffer', code, count=1, expected=0x8001)
        finally:
            fixture.close()
            row.update(qemu_exit_code=fixture.proc.returncode, qemu_reaped=fixture.proc.poll() is not None)
            (directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        assert row['qemu_exit_code'] == 0 and row['qemu_reaped']
        if not baseline:
            observed = [tuple(map(int, values)) for values in re.findall(
                r'virtio_snd_empty_info code=(\d+) start_id=(\d+) count=(\d+) size=(\d+) total=(\d+)',
                fixture.control_trace_path.read_text())]
            assert observed == accepted_empty, (observed, accepted_empty)
            row['accepted_empty_trace'] = {'path': str(fixture.control_trace_path),
                                           'events': observed, 'matched_successful_requests_only': True}
        if not baseline and counts is None:
            with wave.open(str(fixture.wavpath), 'rb') as stream:
                assert (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) == (2, 2, 48000)
                wav = stream.readframes(stream.getnframes())
            assert wav == data
            row['pcm_after_info_bit_exact'] = True
            row['pcm_after_info_bytes'] = len(wav)
    result['passed'] = True
    (directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def run_queue_size(binary, directory, baseline=False):
    """Use full 64/512-entry split rings, including EVENT_IDX and slot wraps."""
    directory.mkdir()
    result = {'mode': 'queue-size-baseline' if baseline else 'queue-size',
              'scope': 'Actual advertised/selected ring capacity, two slot wraps, EVENT_IDX and PCM. Guest OS allocation/mapping remains unverified.',
              'devices': [], 'passed': False}
    for selected in ([None] if baseline else [None, 64, 512]):
        size = selected or 64
        fixture = Fixture(binary, directory / str(selected or 'default'), trace=False,
                          queue_size=selected, production=selected == 512)
        row = {'requested': selected, 'command': fixture.args}
        result['devices'].append(row)
        data = pcm(1024)
        try:
            fixture.setup(indirect=True, queue_size=size, event_idx=True)
            assert len(fixture.queues) == 4
            assert all(q['advertised_maximum'] == size and q['size'] == size for q in fixture.queues.values())
            row['queues'] = fixture.queues
            q = fixture.queues[0]
            payload, response = 0x40054010, 0x40055010
            request = struct.pack('<IIII', 1, 0, 0, 24)
            fixture.write(payload - 16, b'\xa5' * 16 + request + b'\x5a' * 16)
            completed = 2 * size + 3
            notified = 0
            for index in range(completed):
                fixture.read('b', fixture.isr)
                notify = index % 2 == 0
                fixture.command(f'writew {q["avail"] + 4 + 2 * size:#x} {q["sequence"] + (not notify)}')
                fixture.write(response - 16, b'\xa5' * 16 + b'\xcc' * 4 + b'\x5a' * 16)
                head = index % size
                fixture.submit(0, [(payload, 16, False), (response, 4, True)], indirect=True, head=head)
                assert fixture.read('w', q['used'] + 2) == index + 1
                used_entry = q['used'] + 4 + 8 * (index % size)
                assert fixture.read('l', used_entry) == head
                assert fixture.read('l', used_entry + 4) == 4
                actual = bytes.fromhex(fixture.command(f'read {response - 16:#x} 36')[2:])
                assert actual == b'\xa5' * 16 + struct.pack('<I', 0x8000) + b'\x5a' * 16
                assert bool(fixture.read('b', fixture.isr) & 1) == notify
                assert fixture.read('w', q['used'] + 4 + 8 * size) == index + 1
                notified += notify
            assert bytes.fromhex(fixture.command(f'read {payload - 16:#x} 48')[2:]) == b'\xa5' * 16 + request + b'\x5a' * 16
            row.update(control_completions=completed, ring_slot_wraps=2,
                       distinct_descriptor_heads=size, event_idx_interrupts=notified,
                       event_idx_suppressed=completed-notified, used_lengths_checked=True,
                       response_and_request_canaries_unchanged=True)
            fixture.control(0x101, params=True)
            fixture.control(0x102)
            fixture.enqueue(data)
            fixture.control(0x104)
            for _ in range(100):
                if fixture.drained():
                    break
                fixture.step()
            assert fixture.drained()
            for _ in range(20):
                fixture.step()
            fixture.control(0x105)
            fixture.control(0x103)
            assert fixture.read('l', fixture.response) == 0x8000
            assert fixture.read('l', fixture.queues[2]['used'] + 8) == 8
            for queue in fixture.queues.values():
                used = queue['used']
                before = bytes.fromhex(fixture.command(f'read {used - 16:#x} 16')[2:])
                after = bytes.fromhex(fixture.command(f'read {used + 6 + 8 * size:#x} 16')[2:])
                assert before == b'\xa5' * 16 and after == b'\x5a' * 16
            row['all_four_used_ring_canaries_unchanged'] = True
        finally:
            fixture.close()
            row.update(qemu_exit_code=fixture.proc.returncode, qemu_reaped=fixture.proc.poll() is not None)
            (directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        assert row['qemu_exit_code'] == 0 and row['qemu_reaped']
        with wave.open(str(fixture.wavpath), 'rb') as stream:
            assert (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) == (2, 2, 48000)
            wav = stream.readframes(stream.getnframes())
        assert wav == data
        row.update(pcm_bit_exact=True, pcm_bytes=len(wav), pcm_sha256=hashlib.sha256(wav).hexdigest())
        if selected == 512:
            trace = decode(fixture.tracepath.read_bytes())
            rebuilt = reconstruct(trace)
            assert trace['complete'] and not rebuilt['errors']
            assert rebuilt['groups'][(0, 1)]['accepted'] == data
            row.update(production_audio=fixture.production_audio, pcm_trace_complete=True)
    if not baseline:
        result['invalid_capacities'] = []
        for size in (0, 3, 2048, 65536):
            command = [str(binary), '-machine', 'virt', '-accel', 'qtest', '-nodefaults',
                       '-display', 'none', '-audiodev', 'none,id=snd',
                       '-device', f'virtio-sound-pci,audiodev=snd,queue-size={size}']
            rejected = subprocess.run(command, capture_output=True, text=True, timeout=5)
            assert rejected.returncode != 0 and 'queue-size' in rejected.stderr
            result['invalid_capacities'].append({'size': size, 'returncode': rejected.returncode,
                                                  'stderr': rejected.stderr.strip(), 'rejected': True})
    result['passed'] = True
    (directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def run_streams(binary, directory):
    """Standard streams property through production arguments and 512-entry VQs."""
    directory.mkdir()
    result={'mode':'streams','scope':'Diskless WAV enumeration and PCM; no Apple driver/application outcome.',
            'devices':[],'passed':False}
    for selected in (None,2,1):
        count=selected or 2
        fixture=Fixture(binary,directory/str(selected or 'default'),production=True,
                        queue_size=512,streams=selected)
        row={'requested_streams':selected,'command':fixture.args,'queries':[],
             'production_audio':fixture.production_audio}
        result['devices'].append(row)
        debugger=None;debug_log=None
        data=pcm(32768)
        try:
            fixture.setup(indirect=True,queue_size=512)
            sound_path='/machine/peripheral/guestsound-pci/virtio-backend'
            actual=fixture.request('qom-get',{'path':sound_path,'property':'streams'})
            config=[fixture.read('l',fixture.device_config+offset) for offset in (0,4,8)]
            assert actual==count and config==[0,count,0]
            assert all(q['advertised_maximum']==512 and q['size']==512 for q in fixture.queues.values())
            row.update(actual_qom_streams=actual,actual_config=config,actual_queue_sizes=[512]*4)

            def control(label,body,capacity,status,used_bytes):
                payload,response=0x40054010,0x40055010
                fixture.write(payload-16,b'\xa5'*16+body+b'\x5a'*16)
                fixture.write(response-16,b'\xa5'*16+b'\xcc'*capacity+b'\x5a'*16)
                fixture.submit(0,[(payload,len(body),False),(response,capacity,True)],indirect=True)
                q=fixture.queues[0];entry=q['used']+4+8*((q['sequence']-1)%q['size'])
                assert fixture.read('w',q['used']+2)==q['sequence']
                assert fixture.read('l',entry)==0 and fixture.read('l',entry+4)==used_bytes
                raw=bytes.fromhex(fixture.command(f'read {response-16:#x} {capacity+32}')[2:])
                assert raw[:16]==b'\xa5'*16 and raw[-16:]==b'\x5a'*16
                reply=raw[16:-16]
                assert struct.unpack_from('<I',reply)[0]==status
                assert reply[used_bytes:]==b'\xcc'*(capacity-used_bytes)
                assert bytes.fromhex(fixture.command(f'read {payload-16:#x} {len(body)+32}')[2:])==b'\xa5'*16+body+b'\x5a'*16
                row['queries'].append({'label':label,'request_hex':body.hex(),'response_hex':reply.hex(),
                                       'used_bytes':used_bytes,'canaries_unchanged':True,'passed':True})
                return reply

            control('empty-jack',struct.pack('<IIII',1,0,0,24),4,0x8000,4)
            reply=control('pcm-info',struct.pack('<IIII',0x100,0,count,32),4+32*count,0x8000,4+32*count)
            directions=[reply[4+32*index+24] for index in range(count)]
            assert directions==([0] if count==1 else [0,1])
            row['actual_pcm_directions']=directions
            control('invalid-stream-info',struct.pack('<IIII',0x100,count,1,32),36,0x8001,4)
            control('invalid-stream-start',struct.pack('<II',0x104,count),4,0x8001,4)
            control('empty-chmap',struct.pack('<IIII',0x200,0,0,24),4,0x8000,4)
            fixture.control(0x101,params=True)
            fixture.control(0x102)
            fixture.enqueue(data)
            if selected==1:
                # One read-only metadata observation at the verified x86-64
                # entry ABI of the output-enable function, on this owned qtest.
                ready=fixture.directory/'voice-ready'
                evidence=fixture.directory/'voice-state.json'
                script=fixture.directory/'voice-state.gdb'
                script.write_text('''set pagination off
set confirm off
set debuginfod enabled off
break *audio_be_set_active_out
python
open('''+repr(str(ready))+''', 'w').close()
end
continue
python
import gdb, json
assert gdb.newest_frame().architecture().name() == 'i386:x86-64'
record = {'function': str(gdb.parse_and_eval('audio_be_set_active_out').type),
          'architecture': gdb.newest_frame().architecture().name(),
          'backend_type': gdb.parse_and_eval('((Object *)$rdi)->class->type->name').string(),
          'input_head_is_null': int(gdb.parse_and_eval('((AudioMixengBackend *)$rdi)->hw_head_in.lh_first')) == 0,
          'output_head_present': int(gdb.parse_and_eval('((AudioMixengBackend *)$rdi)->hw_head_out.lh_first')) != 0,
          'software_output_voice_present': int(gdb.parse_and_eval('$rsi')) != 0,
          'requested_active': bool(int(gdb.parse_and_eval('$rdx')) & 255)}
with open('''+repr(str(evidence))+''', 'w') as stream:
    json.dump(record, stream, indent=2)
end
detach
''')
                debug_log=(fixture.directory/'voice-gdb.log').open('w')
                debugger=subprocess.Popen(['gdb','-q','-batch','-p',str(fixture.proc.pid),'-x',str(script)],
                                           stdout=debug_log,stderr=subprocess.STDOUT)
                deadline=time.monotonic()+5
                while not ready.exists() and debugger.poll() is None and time.monotonic()<deadline:
                    time.sleep(.01)
                if not ready.exists():
                    if debugger.poll() is None:debugger.terminate()
                    debugger.wait(timeout=5)
                    row['voice_observation']={'observed':False,'reason':'native debugger did not reach its ready point',
                                              'log':str(fixture.directory/'voice-gdb.log')}
            fixture.control(0x104)
            if debugger is not None:
                debugger.wait(timeout=5)
                if evidence.exists():
                    voice=json.loads(evidence.read_text())
                    assert voice['backend_type']=='audio-wav' and voice['input_head_is_null']
                    assert voice['output_head_present'] and voice['software_output_voice_present'] and voice['requested_active']
                    row['voice_observation']={'observed':True,'path':str(evidence),**voice}
                else:
                    row.setdefault('voice_observation',{'observed':False,'reason':'native debugger could not read metadata',
                                                       'log':str(fixture.directory/'voice-gdb.log')})
                row['debugger_reaped']=debugger.poll() is not None
            for _ in range(600):
                if fixture.drained():break
                fixture.step()
            assert fixture.drained() and fixture.read('l',fixture.response)==0x8000
            assert fixture.read('l',fixture.queues[2]['used']+8)==8
            for _ in range(20):fixture.step()
            fixture.control(0x105)
            fixture.control(0x103)
        finally:
            if debugger is not None and debugger.poll() is None:
                debugger.terminate()
                try:debugger.wait(timeout=5)
                except subprocess.TimeoutExpired:debugger.kill();debugger.wait()
            if debug_log is not None:debug_log.close()
            fixture.close()
            row.update(qemu_exit_code=fixture.proc.returncode,qemu_reaped=fixture.proc.poll() is not None)
            (directory/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        assert row['qemu_exit_code']==0 and row['qemu_reaped']
        with wave.open(str(fixture.wavpath),'rb') as wav:
            assert (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())==(2,2,48000)
            output=wav.readframes(wav.getnframes())
        trace=decode(fixture.tracepath.read_bytes());rebuilt=reconstruct(trace)
        assert trace['complete'] and not rebuilt['errors'] and rebuilt['groups'][(0,1)]['accepted']==data
        assert output==data
        row.update(pcm_bit_exact=True,pcm_bytes=len(output),pcm_sha256=hashlib.sha256(output).hexdigest(),
                   pcm_trace_complete=True)
    result['passed']=True
    (directory/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', choices=('off', 'normal', 'mutation', 'stop-flush',
                                          'cap', 'io-error', 'production',
                                          'item-info', 'item-info-baseline', 'queue-size', 'queue-size-baseline', 'streams'))
    parser.add_argument('--mainline-integration', action='store_true',
                        help='Validate the recorded main build, requiring no live owner at startup')
    args = parser.parse_args()
    binary, directory = args.binary.resolve(), args.output.resolve()
    mainline = binary == (ROOT / 'build/qemu-bdif-usb/qemu-system-aarch64').resolve()
    if mainline != args.mainline_integration:
        parser.error('main binary requires --mainline-integration; candidates omit it')
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if mainline:
        manifest = json.loads((ROOT / 'runs/qemu-bdif-usb-build.json').read_text())
        patch = 'patches/qemu-platform/0023-virtio-snd-pcm-boundary-trace.patch'
        if (manifest.get('binary_sha256') != digest or patch not in manifest.get('patches', []) or
                manifest.get('patch_sha256', {}).get(patch) != hashlib.sha256((ROOT / patch).read_bytes()).hexdigest()):
            parser.error('main binary does not match the recorded PCM trace build')
        live = []
        for executable in Path('/proc').glob('[0-9]*/exe'):
            try:
                target = os.readlink(executable)
            except OSError:
                continue
            if target in (str(binary), str(binary) + ' (deleted)'):
                live.append(int(executable.parent.name))
        if live:
            parser.error(f'main binary still has live owners: {sorted(live)}')
    directory.mkdir(parents=True, exist_ok=False)
    result = {'scope': __doc__, 'binary': str(binary),
              'binary_sha256': digest, 'mainline_integration': mainline, 'cases': []}
    try:
        for mode in ([args.case] if args.case else
                     ('off', 'normal', 'mutation', 'stop-flush', 'cap', 'io-error', 'production', 'item-info', 'queue-size', 'streams')):
            case = (run_item_info(binary, directory / mode, baseline=mode.endswith('-baseline'))
                    if mode.startswith('item-info') else
                    run_queue_size(binary, directory / mode, baseline=mode.endswith('-baseline'))
                    if mode.startswith('queue-size') else run_streams(binary,directory/mode)
                    if mode=='streams' else run_case(binary, directory / mode, mode))
            result['cases'].append(case)
        # Exclusive open rejects an existing file instead of overwriting evidence.
        path = directory / 'existing.bin'
        path.write_bytes(b'preserve this evidence')
        rejected = subprocess.run([str(binary), '-machine', 'virt', '-accel', 'qtest',
            '-display', 'none', '-nodefaults', '-audiodev', 'none,id=snd',
            '-device', f'virtio-sound-pci,audiodev=snd,x-pcm-trace={path}'],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5)
        assert rejected.returncode != 0 and path.read_bytes() == b'preserve this evidence'
        result['exclusive_open_rejected'] = True
        result['binary_unchanged'] = hashlib.sha256(binary.read_bytes()).hexdigest() == digest
        assert result['binary_unchanged']
        result['passed'] = True
    except Exception as error:
        result.update(passed=False, error=repr(error))
        raise
    finally:
        (directory / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': True, 'report': str(directory / 'results.json')}))


if __name__ == '__main__':
    main()
