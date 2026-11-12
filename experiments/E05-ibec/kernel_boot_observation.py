"""Bounded, read-only boot observation for the current FirmwareSession only.

The Revision 3/Version 2 boot_args offsets are observed ABI candidates, not a
claim that a scan identified the authoritative BootArgs pointer.  msgbuf uses
Apple's public bsd/sys/msgbuf.h layout (magic, size, write, read, buffer VA).
bsd/kern/subr_log.c updates the write cursor independently of the read cursor:
the latter belongs to a consumer and must not define retained log history.

Usage: observer = KernelBootObserver(fw, private_dir=private)
       state = observer.capture()  # observer.update() on subsequent polls

Only an owned QEMU memfd is read, never /proc/PID/mem or a guest write API.
Raw structures stay under private/; current JSON and cumulative decoded log
go to fw.logs.  No QMP commands, guest pauses or new sessions are performed.
"""
from array import array
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
QEMU = ROOT / 'build/qemu-bdif-usb/qemu-system-aarch64'
RAM_BASE = 0x70000000
RAM_SIZE = 0x100000000
CAPTURE_BYTES = 256 * 1024 * 1024
COMMAND_OFFSET = 0x6c
COMMAND_LIMIT = 1024
MSG_HEADER = struct.Struct('<4IQ')
MSG_MAGIC = 0x063061
SIGNATURE_LIMIT = 4096
CANDIDATE_LIMIT = 32
BUFFER_LIMIT = 8


def _in_ram(address, size, ram_bytes=RAM_SIZE):
    return (type(ram_bytes) is int and ram_bytes in (RAM_SIZE, 2 * RAM_SIZE) and
            size > 0 and RAM_BASE <= address and address + size <= RAM_BASE + ram_bytes)


@dataclass(frozen=True)
class BootArgsCandidate:
    physical_address: int
    virt_base: int
    phys_base: int
    mem_size: int
    top_of_kernel_data: int
    command_line: str
    device_tree_pointer: int
    device_tree_length: int
    raw: bytes = field(repr=False)
    ram_bytes: int = RAM_SIZE

    def translate(self, pointer, size):
        delta = pointer - self.virt_base
        if size <= 0 or delta < 0 or delta + size > self.mem_size:
            return None
        address = self.phys_base + delta
        return address if _in_ram(address, size, self.ram_bytes) else None

    def as_dict(self):
        dt = self.translate(self.device_tree_pointer, self.device_tree_length)
        return {'physical_address': hex(self.physical_address), 'revision': 3, 'version': 2,
                'candidate_only': True, 'virt_base': hex(self.virt_base),
                'phys_base': hex(self.phys_base), 'mem_size': self.mem_size,
                'ram_bytes': self.ram_bytes,
                'top_of_kernel_data': hex(self.top_of_kernel_data),
                'command_line': self.command_line,
                'device_tree_pointer': hex(self.device_tree_pointer),
                'device_tree_length': self.device_tree_length,
                'device_tree_physical_address': hex(dt) if dt is not None else None,
                'device_tree_range_valid': dt is not None,
                'device_tree_within_capture': dt is not None and
                    dt + self.device_tree_length <= RAM_BASE + CAPTURE_BYTES}


def parse_boot_args(raw, physical_address, ram_bytes=RAM_SIZE):
    """Return an ABI candidate, or None; never read beyond supplied bytes."""
    if (physical_address % 8 or not _in_ram(physical_address, len(raw), ram_bytes) or
            len(raw) <= COMMAND_OFFSET or raw[:4] != b'\x03\0\x02\0'):
        return None
    virt, phys, size, top = struct.unpack_from('<4Q', raw, 8)
    if (virt >> 48 != 0xffff or virt % 4096 or phys % 4096 or
            size % 4096 or not _in_ram(phys, size, ram_bytes) or
            virt + size > 1 << 64 or not phys <= top <= phys + size):
        return None
    end = raw.find(b'\0', COMMAND_OFFSET, COMMAND_OFFSET + COMMAND_LIMIT)
    if end < 0:
        return None
    command = raw[COMMAND_OFFSET:end]
    if any(byte < 32 or byte > 126 for byte in command):
        return None
    dt, dt_size = struct.unpack_from('<QI', raw, 96)
    return BootArgsCandidate(physical_address, virt, phys, size, top,
                             command.decode('ascii'), dt, dt_size, bytes(raw[:end + 1]), ram_bytes)


@dataclass(frozen=True)
class MessageBuffer:
    header_physical_address: int
    buffer_virtual_address: int
    buffer_physical_address: int
    size: int
    write_index: int
    read_index: int
    mapping: BootArgsCandidate = field(repr=False)

    def as_dict(self):
        return {'header_physical_address': hex(self.header_physical_address),
                'buffer_virtual_address': hex(self.buffer_virtual_address),
                'buffer_physical_address': hex(self.buffer_physical_address),
                'size': self.size, 'write_index': self.write_index,
                'read_index': self.read_index,
                'mapping_candidate_address': hex(self.mapping.physical_address)}


def parse_msgbuf_header(raw, mapping, physical_address, captured_bytes=CAPTURE_BYTES):
    if (not MSG_HEADER.size <= captured_bytes <= CAPTURE_BYTES or
            len(raw) != MSG_HEADER.size or physical_address % 8 or
            not RAM_BASE <= physical_address <= RAM_BASE + captured_bytes - MSG_HEADER.size):
        return None
    magic, size, write, read, pointer = MSG_HEADER.unpack(raw)
    if (magic != MSG_MAGIC or not 1024 <= size <= 1024 * 1024 or
            size & (size - 1) or write >= size or read >= size):
        return None
    address = mapping.translate(pointer, size)
    if address is None or address + size > RAM_BASE + captured_bytes:
        return None
    return MessageBuffer(physical_address, pointer, address, size, write, read, mapping)


def scan_snapshot(snapshot, ram_bytes=RAM_SIZE):
    """Find constrained structures in a supplied RAM prefix, not banner strings."""
    if not 0 < len(snapshot) <= CAPTURE_BYTES:
        raise ValueError('Snapshot must be a nonempty prefix of at most 256 MiB')
    if type(ram_bytes) is not int or ram_bytes not in (RAM_SIZE, 2 * RAM_SIZE):
        raise ValueError('Only explicit 4/8-GiB RAM bounds are supported')
    boot_args = []
    limited = False
    hits = 0
    offset = -1
    while (offset := snapshot.find(b'\x03\0\x02\0', offset + 1)) >= 0:
        hits += 1
        if hits > SIGNATURE_LIMIT or len(boot_args) >= CANDIDATE_LIMIT:
            limited = True
            break
        candidate = parse_boot_args(snapshot[offset:offset + COMMAND_OFFSET + COMMAND_LIMIT],
                                    RAM_BASE + offset, ram_bytes)
        if candidate is not None:
            boot_args.append(candidate)
    # Copies of boot_args may share a mapping; don't scan/log that mapping twice.
    mappings = {}
    for candidate in boot_args:
        mappings.setdefault((candidate.virt_base, candidate.phys_base, candidate.mem_size), candidate)
    found = {}
    conflicting = set()
    hits = 0
    offset = -1
    while (offset := snapshot.find(struct.pack('<I', MSG_MAGIC), offset + 1)) >= 0:
        hits += 1
        if hits > SIGNATURE_LIMIT:
            limited = True
            break
        matches = {}
        for mapping in mappings.values():
            header = parse_msgbuf_header(snapshot[offset:offset + MSG_HEADER.size], mapping,
                                         RAM_BASE + offset, len(snapshot))
            if header is not None:
                matches[(header.buffer_physical_address, header.size)] = header
        if len(matches) != 1:
            continue  # A header with competing valid translations is ambiguous.
        key, header = next(iter(matches.items()))
        if key in found:
            previous = found[key]
            if (header.write_index, header.read_index) != (previous.write_index, previous.read_index):
                conflicting.add(key)
            continue  # Duplicate header aliases are not independent evidence.
        if len(found) >= BUFFER_LIMIT:
            limited = True
            break
        found[key] = header
    return boot_args, [value for key, value in found.items() if key not in conflicting], limited


def ring_history(header, raw):
    if len(raw) != header.size:
        raise ValueError('Message buffer size differs from header')
    # Zero-filled unwritten space precedes the oldest character after rotation.
    return (raw[header.write_index:] + raw[:header.write_index]).lstrip(b'\0')


def _ring_slice(raw, start, count):
    return (raw[start:] + raw[:start])[:count]


def _overlap(previous, current):
    """Longest previous suffix equal to current prefix, in linear time."""
    if not current:
        return 0
    prefix = array('I', [0]) * len(current)
    match = 0
    for index in range(1, len(current)):
        while match and current[index] != current[match]:
            match = prefix[match - 1]
        if current[index] == current[match]:
            match += 1
        prefix[index] = match
    match = 0
    for byte in previous[-len(current):]:
        while match and (match == len(current) or byte != current[match]):
            match = prefix[match - 1]
        if byte == current[match]:
            match += 1
    return match


class RingAccumulator:
    """Keep only one previous ring; return new bytes for an append-only log."""
    def __init__(self):
        self.raw = None
        self.write_index = None
        self.gap_possible = False
        self.wrap_count = 0

    def update(self, header, raw):
        raw = bytes(raw)
        latest = ring_history(header, raw)
        gap = False
        if self.raw is None:
            added = latest
            gap = bool(raw[header.write_index:].strip(b'\0'))
        elif len(self.raw) != len(raw):
            raise ValueError('Use a different accumulator for a resized buffer')
        else:
            delta = (header.write_index - self.write_index) % header.size
            unchanged = header.size - delta
            outside_same = (_ring_slice(raw, header.write_index, unchanged) ==
                            _ring_slice(self.raw, header.write_index, unchanged))
            if outside_same:
                added = _ring_slice(raw, self.write_index, delta)
            else:
                gap = True  # At least a lap, buffer reset, or inconsistent read.
                previous = (self.raw[self.write_index:] + self.raw[:self.write_index]).lstrip(b'\0')
                added = latest[_overlap(previous, latest):]
            if header.write_index < self.write_index or (gap and raw != self.raw):
                self.wrap_count += 1
        self.raw, self.write_index = raw, header.write_index
        self.gap_possible |= gap
        return added, gap


def runtime_evidence(text):
    """Only call with decoded bytes obtained from a validated message buffer."""
    # A capture can end between any two characters. Do not fix a partial
    # token or banner as the first observed value before its delimiter arrives.
    banner = re.search(r'Darwin Kernel Version [^\r\n\0]+(?=[\r\n\0])', text)
    root = re.search(r'BSD root:\s*([^,\s\0]+)(?=[,\s\0])', text)
    mount = re.search(r'cannot mount root,\s*errno\s*=\s*(\d+)(?=[,\s\0])', text)
    pid1_shared_region = [float(value) for value in re.findall(
        r'^\[\s*([0-9]+(?:\.[0-9]+)?)\]: vm: shared_region:.*\[1\(launchd\)\].*check_np',
        text, re.M)]
    return {'darwin_banner_observed': bool(banner),
            'darwin_banner': banner.group(0) if banner else None,
            'cpu1_online_observed': bool(re.search(r'arm_cpu_init\(\): cpu 1 online', text)),
            'pci_enumeration_observed': 'Found type 0 device class-code' in text,
            'bsd_root': root.group(1) if root else None,
            'root_mount_error': int(mount.group(1)) if mount else None,
            'ramdisk_volume_mount_completed_observed': bool(re.search(
                r'md0s\d+ mount-complete volume ramdisk(?:,|\s)', text)),
            'launchd_load_attempted': bool(re.search(
                r'load_init_program:\s*attempting to load /sbin/launchd(?:[\r\n\0\s]|$)', text)),
            'launchd_pid1_shared_region_handler_observed': bool(pid1_shared_region),
            'launchd_pid1_shared_region_handler_timestamps': pid1_shared_region,
            'kernel_panic_observed': 'panic(cpu' in text}


def _validate_session(session):
    cls = type(session)
    module = sys.modules.get(cls.__module__)
    expected = ROOT / 'experiments/E05-usbip-kernel/firmware_session.py'
    if (cls.__name__ != 'FirmwareSession' or module is None or
            Path(getattr(module, '__file__', '')).resolve() != expected or
            getattr(module, 'FirmwareSession', None) is not cls):
        raise ValueError('Only the current FirmwareSession is accepted; no PID interface')
    proc = session.proc
    if not isinstance(proc, subprocess.Popen) or proc.poll() is not None:
        raise ValueError('FirmwareSession must own a live QEMU child process')
    args = session.args
    if not isinstance(args, list) or not args:
        raise ValueError('Owned QEMU execution arguments are absent')
    work = Path(session.temp.name).resolve()
    ram_gib = getattr(session, 'ram_gib', None)
    ram_bytes = getattr(session, 'ram_bytes', None)
    if (type(ram_gib) is not int or ram_gib not in (4, 8) or
            type(ram_bytes) is not int or ram_bytes != ram_gib * 1024**3):
        raise ValueError('Owned session has an invalid RAM capacity contract')
    memory = f'{ram_gib}G'
    ram_objects = [args[index + 1] for index, value in enumerate(args[:-1])
                   if value == '-object' and args[index + 1].startswith('memory-backend-memfd,')]
    if (work.parent != ROOT / 'build' or not work.name.startswith('kernel-usb-') or not work.is_dir() or
            not isinstance(args, list) or args != proc.args or
            Path(args[0]).resolve() != QEMU.resolve() or
            ram_objects != [f'memory-backend-memfd,id=ram,size={memory},share=on'] or
            args.count('-m') != 1 or args.index('-m') == len(args) - 1 or
            args[args.index('-m') + 1] != memory or
            f'unix:{work}/q.sock,server=on,wait=off' not in args or
            not any(arg.startswith('vmapple,memory-backend=ram,') for arg in args)):
        raise ValueError('QEMU does not match the owned temporary VMApple RAM profile')
    actual = Path(f'/proc/{proc.pid}/cmdline').read_bytes().rstrip(b'\0').split(b'\0')
    if actual != [os.fsencode(arg) for arg in args] or Path(os.readlink(f'/proc/{proc.pid}/exe')).resolve() != QEMU.resolve():
        raise ValueError('Owned QEMU process identity changed')
    return proc


def _ram_aliases(pid, ram_bytes=RAM_SIZE):
    """Inspect descriptors of the already validated owned child; never /mem."""
    if type(ram_bytes) is not int or ram_bytes not in (RAM_SIZE, 2 * RAM_SIZE):
        raise ValueError('Only explicit 4/8-GiB RAM bounds are supported')
    unique = {}
    for path in Path(f'/proc/{pid}/fd').iterdir():
        try:
            target = os.readlink(path)
            if target != '/memfd:memory-backend-memfd (deleted)':
                continue
            info = path.stat()
        except FileNotFoundError:
            continue
        if info.st_size != ram_bytes or not stat.S_ISREG(info.st_mode):
            continue
        unique.setdefault((info.st_dev, info.st_ino), []).append(path)
    if len(unique) != 1:
        raise ValueError(f'Expected one unique {ram_bytes}-byte RAM memfd, found {len(unique)}')
    identity, aliases = next(iter(unique.items()))
    return identity, sorted(aliases, key=lambda path: int(path.name))


@contextmanager
def _open_owned_ram(session):
    proc = _validate_session(session)
    identity, aliases = _ram_aliases(proc.pid, session.ram_bytes)
    fd = os.open(aliases[0], os.O_RDONLY | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if ((info.st_dev, info.st_ino) != identity or info.st_size != session.ram_bytes or
                proc.poll() is not None):
            raise ValueError('RAM descriptor changed during open')
        yield fd, {'kind': 'owned_qemu_memory_backend_memfd', 'pid': proc.pid,
                   'unique_ram_memfds': 1, 'duplicate_fd_aliases_removed': len(aliases) - 1,
                   'ram_inode': info.st_ino, 'ram_bytes': info.st_size,
                   'capture_limit_bytes': CAPTURE_BYTES,
                   'read_only': True}
    finally:
        os.close(fd)


def _pread(fd, offset, count):
    if offset < 0 or count < 0 or offset + count > CAPTURE_BYTES:
        raise ValueError('Read exceeds the first 256 MiB of VM RAM')
    data = os.pread(fd, count, offset)
    if len(data) != count:
        raise EOFError('Short owned RAM memfd read')
    return data


def _stable_buffer(fd, header):
    offset = header.header_physical_address - RAM_BASE
    for _ in range(3):
        before = _pread(fd, offset, MSG_HEADER.size)
        current = parse_msgbuf_header(before, header.mapping, header.header_physical_address)
        if current is None:
            return None
        raw = _pread(fd, current.buffer_physical_address - RAM_BASE, current.size)
        if before == _pread(fd, offset, MSG_HEADER.size):
            return current, before, raw
    return None


def _write_private(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
    finally:
        os.close(fd)


class KernelBootObserver:
    def __init__(self, session, *, private_dir):
        self.session = session
        self.logs = Path(session.logs).resolve()
        self.private = Path(private_dir).resolve()
        if not self.logs.is_relative_to(ROOT / 'runs') or not self.private.is_relative_to(ROOT / 'private'):
            raise ValueError('Structured output must stay in runs/ and raw evidence in private/')
        self.logs.mkdir(parents=True, exist_ok=True)
        self.private.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.rings = {}
        self.evidence = runtime_evidence('')
        self.text_tails = {}
        self.capture_count = 0
        self.log_bytes = 0
        self.owner_identity = None
        self.started = time.monotonic()

    def _append(self, key, chunk, gap):
        if not chunk:
            return 0
        text = chunk.decode('utf-8', 'replace')
        if sum(char.isprintable() or char in '\r\n\t' for char in text) < len(text) * .95:
            return 0
        context = ('' if gap else self.text_tails.get(key, '')) + text
        observed = runtime_evidence(context)
        for name, value in observed.items():
            if value is not None and value is not False and not self.evidence[name]:
                self.evidence[name] = value
        self.text_tails[key] = context[-4096:]
        encoded = (('\n[observer: message-buffer continuity uncertain]\n' if gap else '') + text).encode('utf-8')
        with (self.logs / 'kernel-boot.log').open('ab') as stream:
            stream.write(encoded)
        self.log_bytes += len(encoded)
        return len(encoded)

    def capture(self):
        """Return current JSON state; repeated calls append only observed changes."""
        self.capture_count += 1
        with _open_owned_ram(self.session) as (fd, source):
            identity = (source['pid'], source.get('ram_inode'))
            if self.owner_identity is not None and self.owner_identity != identity:
                raise ValueError('An observer cannot carry evidence into a different VM')
            self.owner_identity = identity
            snapshot = _pread(fd, 0, CAPTURE_BYTES)
            candidates, headers, limited = scan_snapshot(snapshot, source['ram_bytes'])
            del snapshot
            state = {'scope': 'Bounded read-only current synthetic VM RAM; candidate boot_args and validated msgbuf',
                     'source': source, 'capture_count': self.capture_count,
                     'elapsed_seconds': round(time.monotonic() - self.started, 3),
                     'boot_args_candidates': [], 'message_buffers': [],
                     'scan_limits_reached': limited, 'new_log_bytes': 0,
                     'unstable_message_buffers': 0, 'guest_writes': False,
                     'continuity_scope': 'Write indices are modulo capacity; identical full laps are not detectable. '
                         'Header is checked before/after buffer read; guest is never paused.'}
            for candidate in candidates:
                name = f'boot-args-{candidate.physical_address - RAM_BASE:x}.bin'
                _write_private(self.private / name, candidate.raw)
                state['boot_args_candidates'].append(dict(candidate.as_dict(), raw_private=name))
            for header in headers:
                stable = _stable_buffer(fd, header)
                if stable is None:
                    state['unstable_message_buffers'] += 1
                    continue
                header, header_raw, raw = stable
                suffix = f'{header.header_physical_address - RAM_BASE:x}.bin'
                name, header_name = 'msgbuf-' + suffix, 'msgbuf-header-' + suffix
                _write_private(self.private / name, raw)
                _write_private(self.private / header_name, header_raw)
                key = (header.buffer_physical_address, header.size)
                ring = self.rings.setdefault(key, RingAccumulator())
                chunk, gap = ring.update(header, raw)
                state['new_log_bytes'] += self._append(key, chunk, gap)
                state['message_buffers'].append(dict(header.as_dict(), raw_private=name,
                    header_private=header_name, consistency='header_equal_before_and_after_read',
                    gap_possible=ring.gap_possible, wrap_count=ring.wrap_count))
        state['runtime_evidence'] = dict(self.evidence)
        state['cumulative_log_bytes'] = self.log_bytes
        state['log_path'] = 'kernel-boot.log'
        state['log_gap_possible'] = any(ring.gap_possible for ring in self.rings.values())
        path = self.logs / 'kernel-boot-state.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(path)
        return state

    update = capture
