"""Read device state through the locally fixed QMP introspection commands."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QEMU = ROOT / 'build/qemu-bdif-usb/qemu-system-aarch64'
QEMU_PIN = '1a72df984914fb9929d35b1217619178133d65a4'
STATUS_FIX = 'patches/qemu-platform/0013-virtio-qmp-backend-features.patch'
USB_TRANSPORT = 'patches/qemu-platform/0015-vmapple-virtio-usb-transport.patch'
USB_INTERNAL_DEVICE_ID = 0x100  # QEMU vendor-private bookkeeping ID, not PCI ID.


def require_fixed_qmp_build(binary=QEMU, *, require_usb=False):
    binary = Path(binary).resolve()
    record = json.loads((ROOT / 'runs/qemu-bdif-usb-build.json').read_text())
    patch_hash = hashlib.sha256((ROOT / STATUS_FIX).read_bytes()).hexdigest()
    if (binary != QEMU.resolve() or record.get('base_commit') != QEMU_PIN or
            record.get('binary') != str(QEMU.relative_to(ROOT)) or
            STATUS_FIX not in record.get('patches', []) or
            record.get('patch_sha256', {}).get(STATUS_FIX) != patch_hash):
        raise ValueError('Pinned QMP backend-features fix is not verified in the build record')
    with binary.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != record.get('binary_sha256'):
        raise ValueError('QEMU binary differs from its fixed build record; status query refused')
    if require_usb:
        usb_hash=hashlib.sha256((ROOT/USB_TRANSPORT).read_bytes()).hexdigest()
        if (USB_TRANSPORT not in record.get('patches',[]) or
                record.get('patch_sha256',{}).get(USB_TRANSPORT)!=usb_hash):
            raise ValueError('VirtIO USB transport is not verified in the build record')
    return {'base_commit': QEMU_PIN, 'binary_sha256': actual, 'fix_patch': STATUS_FIX,
            'fix_patch_sha256': patch_hash}


def capture_virtio_devices(qmp, *, allow_usb=False):
    """Call only after require_fixed_qmp_build; reads no guest MMIO or ISR."""
    devices = qmp.request('x-query-virtio')
    if len(devices) > (3 if allow_usb else 2):
        raise ValueError('Unexpected devices in the owned VMApple session')
    result = []
    usb_count=0
    for device in devices:
        path = device['path']
        status = qmp.request('x-query-virtio-status', {'path': path})
        kind='usb' if status['device-id']==USB_INTERNAL_DEVICE_ID else 'block'
        if (status['device-id'] not in ({2,USB_INTERNAL_DEVICE_ID} if allow_usb else {2})
                or status['vhost-started']):
            raise ValueError('Unexpected device in the ordinary non-vhost VMApple path')
        usb_count+=kind=='usb'
        if usb_count>1:raise ValueError('More than one owned VirtIO USB controller')
        for field in ('guest-features', 'host-features', 'backend-features'):
            value = status.get(field)
            if not isinstance(value, dict) or not isinstance(value.get('transports'), list):
                raise ValueError(f'Missing required VirtIO QMP feature object: {field}')
        count = status['num-vqs']
        if not 1 <= count <= 16:
            raise ValueError('Unexpected queue count')
        if kind=='usb' and count!=2:raise ValueError('VirtIO USB requires its two transport queues')
        queues = [qmp.request('x-query-virtio-queue-status', {'path': path, 'queue': index})
                  for index in range(count)]
        result.append({'path': path, 'kind':kind, 'status': status, 'queues': queues})
    return result
