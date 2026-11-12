#!/usr/bin/env python3
"""Exercise FirmwareSession's storage layout with synthetic data and paused QEMU.

No Apple firmware, AEA data, signing service or guest instructions are used.
The four -drive/-device combinations mirror firmware_session.py.  This checks
their real block layer behavior, including both simultaneous NBD connections;
it does not claim that a guest virtio driver has issued requests.
--pci-only captures the PCI capability contract without direct backend I/O.
Its bulk config read is only for this never-resumed reset fixture: an active
PCI configuration-access selector can make PCI data reads access a BAR.
The pinned build record must contain the local QMP backend-features and
VMApple capability-order fixes before this fixture is started.
--blank-root-disk instead checks a shared empty 128GiB raw target without any
source/NBD; its canonical result is separate from the existing COW result.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import socket
import struct
import subprocess
import tempfile
import time
from virtio_observation import require_fixed_qmp_build, capture_virtio_devices
from firmware_session import FirmwareSession, BLANK_ROOT_BYTES

ROOT = Path(__file__).resolve().parents[2]
QEMU = ROOT / 'build/qemu-bdif-usb/qemu-system-aarch64'
BLOCK_SIZE = 4096
WRITE_OFFSET = 1024 * 1024
BASE_SIZE = 8 * 1024 * 1024
ECAM_BASE = 0x40000000
CAP_NAMES = {1: 'common', 2: 'notify', 3: 'isr', 4: 'device', 5: 'pci_config_access'}
ORDER_FIX = 'patches/qemu-platform/0014-vmapple-virtio-capability-order.patch'
ORDERED_CAP_OFFSETS = (0x40, 0x50, 0x60, 0x70, 0x84, 0x98)
# Both devices had these identical reset bytes before 0014. Retain the captured
# normal-device oracle so only the list head and next links may change; all BARs,
# MSI-X data and the type-5 access selector must remain byte-for-byte identical.
# Source: runs/e05-storage-cow-validation/pci-0-{1,2}-0-config.bin, pre-0014.
RESET_CONFIG_BEFORE_ORDER_FIX = bytes.fromhex(
    '6b10001a00001000010000010000000000000000000000000000000000000000'
    '0c0000000000000000000000f41a001100000000980000000000000000010000'
    '0900100104000000000000000010000009401003040000000010000000100000'
    '0950100404000000002000000010000009601402040000000030000000100000'
    '0400000009701405000000000000000000000000000000001184020001000000'
    '0108000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000')


def require_fixed_storage_build():
    verified = require_fixed_qmp_build()
    record = json.loads((ROOT / 'runs/qemu-bdif-usb-build.json').read_text())
    patch_hash = hashlib.sha256((ROOT / ORDER_FIX).read_bytes()).hexdigest()
    if (ORDER_FIX not in record.get('patches', []) or
            record.get('patch_sha256', {}).get(ORDER_FIX) != patch_hash):
        raise ValueError('VMApple capability-order fix is not verified in the build record')
    return dict(verified, capability_order_patch=ORDER_FIX,
                capability_order_patch_sha256=patch_hash)


def validate_fixed_pci_layout(raw, parsed):
    expected = bytearray(RESET_CONFIG_BEFORE_ORDER_FIX)
    expected[0x34] = ORDERED_CAP_OFFSETS[0]
    for offset, next_offset in zip(ORDERED_CAP_OFFSETS, ORDERED_CAP_OFFSETS[1:] + (0,)):
        expected[offset + 1] = next_offset
    if [int(cap['offset'], 16) for cap in parsed['capabilities']] != list(ORDERED_CAP_OFFSETS):
        raise ValueError('VMApple capability links are not in increasing address order')
    if raw != expected:
        changed = [hex(index) for index, (actual, wanted) in enumerate(zip(raw, expected))
                   if actual != wanted]
        raise ValueError(f'Fixed reset PCI bytes differ beyond capability links: {changed}')
    parsed.update(capability_offsets_increasing=True,
                  capability_payloads_and_bars_preserved=True,
                  before_order_fix_config_sha256=hashlib.sha256(RESET_CONFIG_BEFORE_ORDER_FIX).hexdigest(),
                  changed_config_bytes=[hex(index) for index, (old, new) in
                                        enumerate(zip(RESET_CONFIG_BEFORE_ORDER_FIX, raw)) if old != new])


class QMPError(RuntimeError):
    def __init__(self, response):
        self.response = response
        super().__init__(str(response))


class QMP:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.settimeout(10)
        self.socket.connect(str(path))
        self.stream = self.socket.makefile('rb')
        self.greeting = json.loads(self.stream.readline())
        self.request('qmp_capabilities')

    def request(self, command, arguments=None):
        request = {'execute': command}
        if arguments is not None:
            request['arguments'] = arguments
        self.socket.sendall(json.dumps(request).encode() + b'\n')
        while True:
            line = self.stream.readline()
            if not line:
                raise EOFError('QMP closed')
            response = json.loads(line)
            if 'error' in response:
                raise QMPError(response['error'])
            if 'return' in response:
                return response['return']

    def io(self, device, command):
        return self.request('x-qemu-io', {'device': device, 'command': command})

    def close(self):
        self.stream.close()
        self.socket.close()


def wait_socket(path, process):
    deadline = time.monotonic() + 15
    while not path.exists():
        if process.poll() is not None:
            raise RuntimeError(f'Process {process.pid} exited {process.returncode} before {path.name}')
        if time.monotonic() > deadline:
            raise TimeoutError(f'{path.name} startup')
        time.sleep(.025)


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed ({result.returncode}): {result.stderr.strip()}')
    return result.stdout


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def top_level_stats(entries):
    # Recursing through parent/backing repeats the I/O seen by these backends.
    return {entry['device']: entry['stats'] for entry in entries if entry.get('device')}


def capture_virtio_status(qmp):
    """Only called after require_fixed_qmp_build() accepts this exact binary."""
    result = capture_virtio_devices(qmp)
    if len(result) != 2:
        raise ValueError('Expected two owned VMApple VirtIO block devices')
    for device in result:
        for index, queue in enumerate(device['queues']):
            if queue['queue-index'] != index or any(queue[field] for field in ('vring-desc', 'vring-avail', 'vring-used')):
                raise ValueError('Paused reset fixture has an unexpected active queue')
        if qmp.request('x-query-virtio-status', {'path': device['path']}) != device['status']:
            raise ValueError('Read-only queue queries changed VirtIO status')
        device['status_unchanged_after_queue_queries'] = True
    if qmp.request('query-status')['running']:
        raise ValueError('Synthetic guest unexpectedly resumed')
    return result


def parse_pci_capabilities(config, regions):
    """Validate read-only PCI metadata against independently queried BAR sizes.

    VirtIO 1.3 sections 4.1.4.1 through 4.1.4.6 define the structure bounds,
    common/device alignment, notification extension and ISR byte.  The Apple
    vendor/device ID is a VMApple extension, not a generic VirtIO PCI ID.
    """
    if len(config) != 256:
        raise ValueError('Expected exactly the first 256 PCI configuration bytes')
    bars = {region['bar']: region for region in regions}
    vendor, device, command_register, status = struct.unpack_from('<4H', config)
    capabilities = []
    required = set()
    msix = None
    pointer = config[0x34]
    seen = set()
    if not status & 0x10:
        raise ValueError('PCI status does not advertise a capability list')
    while pointer:
        if pointer in seen or pointer % 4 or not 0x40 <= pointer <= 0xfc or len(seen) >= 48:
            raise ValueError('Invalid, cyclic or unbounded PCI capability list')
        seen.add(pointer)
        kind, next_pointer = config[pointer:pointer + 2]
        row = {'offset': hex(pointer), 'id': hex(kind), 'next': hex(next_pointer)}
        if kind == 9:
            cap_length, cfg_type = config[pointer + 2:pointer + 4]
            if cap_length < 4 or pointer + cap_length > len(config):
                raise ValueError('Vendor capability exceeds PCI configuration space')
            row.update(cap_length=cap_length, cfg_type=cfg_type,
                       name=CAP_NAMES.get(cfg_type, 'unexamined_optional_type'))
            if cfg_type in CAP_NAMES:
                if cap_length < 16:
                    raise ValueError('Short VirtIO structure capability')
                bar = config[pointer + 4]
                offset, length = struct.unpack_from('<II', config, pointer + 8)
                row.update(bar=bar, region_offset=hex(offset), region_bytes=length)
                if cfg_type == 5:
                    # This capability is a writable access selector. Its reset
                    # BAR/offset/length need not select a configured region.
                    if cap_length < 20:
                        raise ValueError('Short PCI configuration access capability')
                    row['selector_not_accessed'] = True
                else:
                    region = bars.get(bar)
                    if region is None or region['type'] != 'memory' or not length or offset + length > region['size']:
                        raise ValueError('VirtIO structure lies outside its advertised memory BAR')
                    alignment = {1: 4, 2: 2, 3: 1, 4: 4}[cfg_type]
                    minimum = {1: 56, 2: 2, 3: 1, 4: 8}[cfg_type]
                    if offset % alignment or length < minimum:
                        raise ValueError('VirtIO structure alignment or minimum length mismatch')
                    row['bar_bounds_valid'] = True
                    required.add(cfg_type)
                    if cfg_type == 2:
                        if cap_length < 20:
                            raise ValueError('Notification capability lacks multiplier')
                        multiplier = struct.unpack_from('<I', config, pointer + 16)[0]
                        if multiplier and (multiplier < 2 or multiplier & (multiplier - 1)):
                            raise ValueError('Invalid notification offset multiplier')
                        row['notify_off_multiplier'] = multiplier
                        # This is metadata validation only: no queue selection,
                        # feature selector, ISR or PCI access-selector writes.
                        row['per_queue_notification_bounds'] = 'not_read_without_initialization'
        elif kind == 0x11:
            if pointer + 12 > len(config) or msix is not None:
                raise ValueError('Invalid MSI-X capability')
            flags = struct.unpack_from('<H', config, pointer + 2)[0]
            table, pba = struct.unpack_from('<II', config, pointer + 4)
            vectors = (flags & 0x7ff) + 1
            row.update(name='msix', vectors=vectors, enabled=bool(flags & 0x8000),
                       function_mask=bool(flags & 0x4000))
            for name, value, size in [('table', table, vectors * 16),
                                      ('pba', pba, ((vectors + 63) // 64) * 8)]:
                bar, offset = value & 7, value & ~7
                region = bars.get(bar)
                if region is None or region['type'] != 'memory' or offset + size > region['size']:
                    raise ValueError(f'MSI-X {name} lies outside its BAR')
                row[name] = {'bar': bar, 'offset': hex(offset), 'bytes': size, 'bar_bounds_valid': True}
            if row['table']['bar'] == row['pba']['bar']:
                table_start, pba_start = table & ~7, pba & ~7
                if (table_start < pba_start + row['pba']['bytes'] and
                        pba_start < table_start + row['table']['bytes']):
                    raise ValueError('MSI-X table and PBA overlap')
            msix = row
        capabilities.append(row)
        pointer = next_pointer
    if required != {1, 2, 3, 4} or msix is None:
        raise ValueError('Required modern block-device VirtIO/MSI-X capabilities missing')
    return {'vendor': hex(vendor), 'device': hex(device), 'revision': config[8],
            'subsystem_vendor': hex(struct.unpack_from('<H', config, 0x2c)[0]),
            'subsystem_device': hex(struct.unpack_from('<H', config, 0x2e)[0]),
            'command': hex(command_register), 'memory_decode_enabled': bool(command_register & 2),
            'bus_master_enabled': bool(command_register & 4), 'capabilities': capabilities,
            'required_capabilities_valid': True,
            'apple_id_extension': (vendor, device) == (0x106b, 0x1a00),
            'generic_virtio_pci_vendor_id': vendor == 0x1af4,
            'feature_negotiation_and_driver_start_verified': False}


def capture_pci(qmp, out, buses, prefix=''):
    """Capture this script's paused reset fixture, not an initialized live VM."""
    devices = [device for bus in buses for device in bus['devices']
               if device['id']['vendor'] == 0x106b and device['id']['device'] == 0x1a00]
    if {(device['bus'], device['slot'], device['function']) for device in devices} != {(0, 1, 0), (0, 2, 0)}:
        raise ValueError('Expected the two owned VMApple storage PCI devices')
    result = []
    for device in devices:
        bus, slot, function = (device[key] for key in ('bus', 'slot', 'function'))
        address = ECAM_BASE + (bus << 20) + (slot << 15) + (function << 12)
        path = out / f'{prefix}pci-{bus}-{slot}-{function}-config.bin'
        qmp.request('pmemsave', {'val': address, 'size': 256, 'filename': str(path)})
        raw = path.read_bytes()
        parsed = parse_pci_capabilities(raw, device['regions'])
        validate_fixed_pci_layout(raw, parsed)
        # An independent second ECAM read checks that metadata observation did
        # not enable decode, change BARs or write the PCI access selector.
        check = out / f'{prefix}pci-{bus}-{slot}-{function}-check.bin'
        try:
            qmp.request('pmemsave', {'val': address, 'size': 256, 'filename': str(check)})
            if check.read_bytes() != raw:
                raise ValueError('PCI configuration changed during read-only capture')
        finally:
            check.unlink(missing_ok=True)
        if not parsed['apple_id_extension'] or parsed['revision'] != 1 or parsed['command'] != '0x0':
            raise ValueError('Paused synthetic VirtIO device was not in expected modern reset state')
        parsed.update(bus=bus, slot=slot, function=function, ecam_address=hex(address),
                      raw_file=path.name, config_unchanged=True)
        result.append(parsed)
    return result


def validate(out, pci_only=False, virtio_status=False, blank_root_disk=False):
    if blank_root_disk and pci_only:
        raise ValueError('The blank root profile requires its backend read/write checks')
    virtio_status = virtio_status or blank_root_disk
    out.mkdir(parents=True, exist_ok=True)
    fixed_build = require_fixed_storage_build()
    result = {'passed': False, 'synthetic_data_only': True,
              'guest_resumed': False, 'apple_firmware_loaded': False,
              'private_material_read': False, 'steps': [],
              'mode': 'pci_read_only' if pci_only else 'storage_and_pci',
              'block_backend_io_commands': not pci_only,
              'fixed_qmp_build': fixed_build,
              'pci_contract_scope': 'PCI capability metadata only; no feature negotiation, queue initialization or Apple driver execution',
              'pci_contract_reference': 'https://docs.oasis-open.org/virtio/virtio/v1.3/virtio-v1.3.html'}
    output_name = ('blank-root-results.json' if blank_root_disk else
                   'pci-capabilities-results.json' if pci_only else 'results.json')
    nbd = qemu = qmp = None
    work = None
    with tempfile.TemporaryDirectory(prefix='storage-blank-' if blank_root_disk else 'storage-cow-',
                                     dir=ROOT / 'build') as directory:
        work = Path(directory)
        base, overlay = (work / name for name in ('base.raw', 'root-overlay.qcow2'))
        sock, qmp_sock = work / 'systemos.nbd', work / 'qmp.sock'
        layout = FirmwareSession(out, system_disk=not blank_root_disk,
                                 blank_root_disk=blank_root_disk)
        layout.prepare_local_storage(work)
        target = work / 'root.raw'
        original_digest = None
        if not blank_root_disk:
            base.write_bytes(b'\x31' * BASE_SIZE)
            base.chmod(0o400)
            original_digest = digest(base)
            layout.system_socket, layout.root_overlay = sock, overlay
        else:
            result['blank_root_allocated_bytes_before'] = target.stat().st_blocks * 512
            assert target.stat().st_size == BLANK_ROOT_BYTES
            assert result['blank_root_allocated_bytes_before'] < BLANK_ROOT_BYTES
            result['systemos_source_opened'] = False
            result['aea_reader_started'] = False
        stub = work / 'stub.bin'
        stub.write_bytes(b'\0' * BLOCK_SIZE)
        backing = f'nbd+unix:///?socket={sock}'
        prefix = 'blank-' if blank_root_disk else 'pci-' if pci_only else ''
        nbd_log = None if blank_root_disk else (out / (prefix + 'nbd.log')).open('wb')
        qemu_log = (out / (prefix + 'qemu.log')).open('wb')
        try:
            if blank_root_disk:
                for offset in (0, WRITE_OFFSET, BLANK_ROOT_BYTES - BLOCK_SIZE):
                    command(['qemu-io', '-r', '-f', 'raw', '-c',
                             f'read -P 0 {offset} {BLOCK_SIZE}', str(target)])
                result['steps'].append('128GiB sparse raw target: zero pattern at beginning, interior and end; no source/NBD')
            else:
                nbd = subprocess.Popen([
                    'qemu-nbd', '--read-only', '--persistent', '--shared=2',
                    '--socket', str(sock), '--format=raw', str(base),
                ], stdout=nbd_log, stderr=subprocess.STDOUT, start_new_session=True)
                wait_socket(sock, nbd)
                command(['qemu-img', 'create', '-q', '-f', 'qcow2', '-b', backing,
                         '-B', 'raw', str(overlay)])
            result['nbd_started'] = nbd is not None
            if not pci_only and not blank_root_disk:
                command(['qemu-io', '-r', '-f', 'qcow2', '-c',
                         f'read -P 0x31 {WRITE_OFFSET} {BLOCK_SIZE}', str(overlay)])
                command(['qemu-io', '-f', 'qcow2', '-c',
                         f'write -P 0xa5 {WRITE_OFFSET} {BLOCK_SIZE}', str(overlay)])
                # New processes independently reopen the overlay and the base.
                command(['qemu-io', '-r', '-f', 'qcow2', '-c',
                         f'read -P 0xa5 {WRITE_OFFSET} {BLOCK_SIZE}', str(overlay)])
                command(['qemu-io', '-r', '-f', 'raw', '-c',
                         f'read -P 0x31 {WRITE_OFFSET} {BLOCK_SIZE}', backing])
                refused = subprocess.run(['qemu-io', '-f', 'raw', '-c',
                                          f'write -P 0xff {WRITE_OFFSET} {BLOCK_SIZE}', backing],
                                         capture_output=True, text=True, timeout=15)
                assert refused.returncode != 0, 'Read-only NBD export opened writable'
                assert 'read-only' in refused.stderr.lower() or 'permission' in refused.stderr.lower(), refused.stderr
                result['nbd_writable_open_rejected'] = refused.stderr.strip()
                assert digest(base) == original_digest, 'Base changed after overlay write'
                result['steps'].append('qemu-img/qemu-io: COW read, write, reopen and base isolation')

            args = [str(QEMU), '-object', 'memory-backend-memfd,id=ram,size=4G,share=on',
                    '-M', 'vmapple,memory-backend=ram,uuid=1', '-m', '4G', '-smp', '2',
                    '-cpu', 'max,pauth-qarma5=on,cntfrq=24000000,x-cntfrq-exact=on',
                    '-accel', 'tcg', '-nodefaults', '-nic', 'none', '-display', 'none',
                    '-nographic', '-monitor', 'none', '-serial', 'none', '-bios', str(stub),
                    '-qmp', f'unix:{qmp_sock},server=on,wait=off', '-S']
            args += layout.storage_arguments(work)
            result['qemu_args'] = args
            qemu = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=qemu_log,
                                    env=dict(os.environ, REIMS_VGPU_WINDOW='0', REIMS_VGPU_LOG='warn'),
                                    start_new_session=True)
            wait_socket(qmp_sock, qemu)
            qmp = QMP(qmp_sock)
            status = qmp.request('query-status')
            assert status['running'] is False, status
            before_blocks = qmp.request('query-block')
            result['query_pci'] = qmp.request('query-pci')
            result['pci_devices'] = capture_pci(qmp, out, result['query_pci'], prefix='blank-' if blank_root_disk else '')
            result['steps'].append('read-only ECAM: increasing capability links; all pre-fix payload/BAR bytes preserved; reset configuration unchanged by capture')
            if virtio_status:
                result['virtio_devices'] = capture_virtio_status(qmp)
                result['qemu_alive_after_virtio_queries'] = qemu.poll() is None
                assert result['qemu_alive_after_virtio_queries']
                result['steps'].append('fixed QMP: both non-vhost device statuses and all reset queues returned; process stayed alive')
            before_stats = qmp.request('query-blockstats')
            by_name = {entry['device']: entry['inserted'] for entry in before_blocks}
            result['storage'] = layout.describe_storage(before_blocks)
            assert set(by_name) == {'pflash0', 'pflash1', 'aux', 'root'}, set(by_name)
            assert by_name['pflash1']['ro'] == (not blank_root_disk)
            assert by_name['root']['ro'] is False
            assert by_name['root']['drv'] == ('raw' if blank_root_disk else 'qcow2')
            if blank_root_disk:
                assert by_name['root']['image']['virtual-size'] == BLANK_ROOT_BYTES
                assert by_name['pflash1']['image']['virtual-size'] == BLANK_ROOT_BYTES
                assert by_name['root']['file'] == by_name['pflash1']['file'] == str(target)
                assert nbd is None and not base.exists() and not overlay.exists() and not sock.exists()
            assert by_name['aux']['ro'] is by_name['pflash0']['ro'] is False
            result['query_block'] = before_blocks
            result['query_blockstats_before'] = before_stats
            result['steps'].append('paused QEMU: four shared local raw backends opened without NBD' if blank_root_disk else
                                   'paused QEMU: four block backends and two NBD connections opened')

            if not pci_only:
                if blank_root_disk:
                    qmp.io('root', f'read -P 0 {WRITE_OFFSET} {BLOCK_SIZE}')
                    qmp.io('root', f'write -P 0xa5 {WRITE_OFFSET} {BLOCK_SIZE}')
                    qmp.io('root', 'flush')
                    qmp.io('pflash1', f'read -P 0xa5 {WRITE_OFFSET} {BLOCK_SIZE}')
                    qmp.io('pflash1', f'write -P 0x5a {WRITE_OFFSET + BLOCK_SIZE} {BLOCK_SIZE}')
                    qmp.io('pflash1', 'flush')
                    qmp.io('root', f'read -P 0x5a {WRITE_OFFSET + BLOCK_SIZE} {BLOCK_SIZE}')
                    qmp.io('root', f'write -P 0x9c {BLANK_ROOT_BYTES - BLOCK_SIZE} {BLOCK_SIZE}')
                    qmp.io('root', 'flush')
                    qmp.io('pflash1', f'read -P 0x9c {BLANK_ROOT_BYTES - BLOCK_SIZE} {BLOCK_SIZE}')
                else:
                    qmp.io('root', f'read -P 0xa5 {WRITE_OFFSET} {BLOCK_SIZE}')
                    qmp.io('root', f'read -P 0x31 {WRITE_OFFSET + BLOCK_SIZE} {BLOCK_SIZE}')
                    qmp.io('root', f'write -P 0x5a {WRITE_OFFSET + BLOCK_SIZE} {BLOCK_SIZE}')
                    qmp.io('root', f'read -P 0x5a {WRITE_OFFSET + BLOCK_SIZE} {BLOCK_SIZE}')
                    qmp.io('root', 'flush')
                    qmp.io('pflash1', f'read -P 0x31 {WRITE_OFFSET} {2 * BLOCK_SIZE}')
                    try:
                        qmp.io('pflash1', f'write -P 0xff {WRITE_OFFSET} {BLOCK_SIZE}')
                    except QMPError as exc:
                        assert 'read-only' in str(exc).lower() or 'permission' in str(exc).lower(), exc
                        result['base_write_rejected'] = exc.response
                    else:
                        raise AssertionError('Read-only pflash1 unexpectedly accepted a write')
                qmp.io('aux', f'write -P 0x73 0 {BLOCK_SIZE}')
                qmp.io('aux', 'flush')
                qmp.io('pflash0', f'read -P 0x73 0 {BLOCK_SIZE}')
                qmp.io('pflash0', f'write -P 0x27 {BLOCK_SIZE} {BLOCK_SIZE}')
                qmp.io('pflash0', 'flush')
                qmp.io('aux', f'read -P 0x27 {BLOCK_SIZE} {BLOCK_SIZE}')

            after_stats = qmp.request('query-blockstats')
            result['query_blockstats_after'] = after_stats
            before = top_level_stats(before_stats)
            after = top_level_stats(after_stats)
            result['top_level_io_delta'] = {
                device: {key: values[key] - before[device][key]
                         for key in ('rd_bytes', 'wr_bytes', 'rd_operations', 'wr_operations')}
                for device, values in after.items()}
            # x-qemu-io bypasses device accounting. Successful pattern checks
            # and persisted file reads prove data movement; rd/wr counters do
            # not. wr_highest_offset is updated by the underlying block layer.
            if not pci_only:
                assert after['root']['wr_highest_offset'] == (BLANK_ROOT_BYTES if blank_root_disk else WRITE_OFFSET + 2 * BLOCK_SIZE)
                if blank_root_disk:
                    assert after['pflash1']['wr_highest_offset'] == WRITE_OFFSET + 2 * BLOCK_SIZE
                assert after['aux']['wr_highest_offset'] == BLOCK_SIZE
                assert after['pflash0']['wr_highest_offset'] == 2 * BLOCK_SIZE
            result['stats_observation'] = (
                'Direct x-qemu-io I/O does not increment device rd/wr accounting. '
                'BDIF likewise uses blk_pread/blk_pwrite without block_acct; '
                'use BDIF trace for its activity. Guest virtio-blk explicitly '
                'accounts I/O on aux/root. Read only top-level named backends; '
                'parent/backing recursively repeat block layer observations.')
            assert qmp.request('query-status')['running'] is False
            if not pci_only:
                result['steps'].append('QMP backend I/O: bidirectional raw root sharing including final 4KiB; AUX sharing' if blank_root_disk else
                                       'QMP block I/O: root COW, base write refusal and bidirectional AUX sharing')
            qmp.request('quit')
            qemu.wait(timeout=5)
            qmp.close()
            qmp = None
            if blank_root_disk:
                for offset, pattern in [(WRITE_OFFSET, '0xa5'),
                                        (WRITE_OFFSET + BLOCK_SIZE, '0x5a'),
                                        (BLANK_ROOT_BYTES - BLOCK_SIZE, '0x9c')]:
                    command(['qemu-io', '-r', '-f', 'raw', '-c',
                             f'read -P {pattern} {offset} {BLOCK_SIZE}', str(target)])
                result['root_target_info'] = json.loads(command(['qemu-img', 'info', '--output=json', str(target)]))
                result['blank_root_allocated_bytes_after'] = target.stat().st_blocks * 512
                assert target.stat().st_size == BLANK_ROOT_BYTES
                assert result['blank_root_allocated_bytes_after'] < BLANK_ROOT_BYTES
                result['steps'].append('QEMU exit: raw root reopened with all patterns intact; 128GiB target remains sparse')
            elif not pci_only:
                command(['qemu-io', '-r', '-f', 'qcow2', '-c',
                         f'read -P 0x5a {WRITE_OFFSET + BLOCK_SIZE} {BLOCK_SIZE}', str(overlay)])
            if not blank_root_disk:
                result['qemu_img_check'] = command(['qemu-img', 'check', '-f', 'qcow2', str(overlay)]).strip()
                assert digest(base) == original_digest, 'Base changed after paused QEMU I/O'
                result['base_sha256_before'] = original_digest
                result['base_sha256_after'] = digest(base)
                result['overlay_info'] = json.loads(command(['qemu-img', 'info', '--output=json', str(overlay)]))
                result['steps'].append('QEMU exit: qcow2 check passed, base unchanged' if pci_only else
                                       'QEMU exit: persisted COW data reopened, qcow2 check passed, base unchanged')
            result['passed'] = True
        except BaseException as exc:
            result['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            if qmp is not None:
                qmp.close()
            stop(qemu)
            stop(nbd)
            if nbd_log is not None:
                nbd_log.close()
            qemu_log.close()
            result['processes_reaped'] = {
                'qemu': qemu is None or qemu.poll() is not None,
                'nbd': nbd is None or nbd.poll() is not None,
            }
            (out / output_name).write_text(json.dumps(result, indent=2) + '\n')
    result['temporary_storage_removed'] = not work.exists()
    assert result['temporary_storage_removed']
    assert all(result['processes_reaped'].values())
    result['steps'].append('cleanup: QEMU reaped; temporary 128GiB target and AUX removed; no NBD was started' if blank_root_disk else
                           'cleanup: QEMU/NBD reaped; sockets, raw files and overlay removed')
    (out / output_name).write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'runs/e05-storage-cow-validation')
    parser.add_argument('--pci-only', action='store_true',
                        help='Only inspect PCI metadata on the paused VM; skip direct backend I/O')
    parser.add_argument('--virtio-status', action='store_true',
                        help='Verify VirtIO status/queues only after checking the fixed binary build record')
    parser.add_argument('--blank-root-disk', action='store_true',
                        help='Validate the empty 128GiB raw target profile without any source or NBD')
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(ROOT / 'runs'):
        parser.error('--out must be under runs/')
    if args.blank_root_disk and args.pci_only:
        parser.error('--blank-root-disk requires backend read/write validation, not --pci-only')
    result = validate(out, pci_only=args.pci_only, virtio_status=args.virtio_status,
                      blank_root_disk=args.blank_root_disk)
    output_name = ('blank-root-results.json' if args.blank_root_disk else
                   'pci-capabilities-results.json' if args.pci_only else 'results.json')
    print(json.dumps({'passed': result['passed'], 'results': str(out / output_name),
                      'top_level_io_delta': result['top_level_io_delta']}, indent=2))


if __name__ == '__main__':
    main()
