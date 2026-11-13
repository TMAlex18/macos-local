#!/usr/bin/env python3
"""Read-only APFS container checkpoint and clean-unmount inspector.

Specification basis: Apple File System Reference ("Mounting an Apple File System
Container", NX superblock, checkpoint descriptor ring, OMAP, and APSB).
An APFS container writes checkpoints only into its nx_xp_desc ring while
mounted, leaving container block 0 at an older XID; only a clean container
unmount copies the final committed nx_superblock_t to container block 0 so that
block 0 passes Fletcher-64 and block0.o_xid == latest_checkpoint.o_xid (when
they differ, XNU apfs.kext logs "nx_mount: reloading after unclean unmount,
checkpoint xid C, superblock xid S" on the next mount).

Never opens an image for writing and never emits container/volume UUIDs or
volume names.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import struct

GPT_HEADER_MAGIC = b'EFI PART'
# Mixed-endian GPT partition type GUIDs defined by Apple APFS partitioning:
GPT_APFS_TYPES = {
    bytes.fromhex('ef57347c0000aa11aa1100306543ecac'): 'Apple_APFS',
    bytes.fromhex('616964690067aa11aa1100306543ecac'): 'Apple_APFS_ISC',
    bytes.fromhex('727663520079aa11aa1100306543ecac'): 'Apple_APFS_Recovery',
}

NX_MAGIC = b'NXSB'
APFS_MAGIC = b'APSB'

OBJECT_TYPE_MASK = 0x0000FFFF
OBJECT_TYPE_NX_SUPERBLOCK = 0x0001
OBJECT_TYPE_BTREE = 0x0002
OBJECT_TYPE_BTREE_NODE = 0x0003
OBJECT_TYPE_OMAP = 0x000B
OBJECT_TYPE_CHECKPOINT_MAP = 0x000C
OBJECT_TYPE_FS = 0x000D

BTNODE_ROOT = 0x0001
BTNODE_LEAF = 0x0002
BTNODE_FIXED_KV_SIZE = 0x0004
BTNODE_NOHEADER = 0x0010

OV_INVALID = 0x00000001

APFS_FS_UNENCRYPTED = 0x00000001
APFS_FS_ONEKEY = 0x00000100

ROLE_NAMES = (
    (0x0001, 'SYSTEM'),
    (0x0002, 'USER'),
    (0x0004, 'RECOVERY'),
    (0x0008, 'VM'),
    (0x0010, 'PREBOOT'),
    (0x0020, 'INSTALLER'),
    (0x0040, 'DATA'),
    (0x0080, 'BASEBAND'),
    (0x0100, 'UPDATE'),
    (0x0200, 'XART'),
    (0x0400, 'HARDWARE'),
    (0x0800, 'BACKUP'),
)

SCOPE = (
    'Read-only verification of APFS container block-0 vs checkpoint-ring '
    'nx_superblock_t XID/OMAP identity, Fletcher-64 block checksums, container '
    'OMAP B-tree resolution, and per-volume apfs_superblock_t integrity on a '
    'closed disk image. Proves whether the latest committed checkpoint was '
    'cleanly flushed to container block 0 on unmount; not a full fs-tree walk.'
)


def fletcher64(block: bytes) -> int:
    if len(block) < 32 or len(block) % 4 != 0:
        raise ValueError('Invalid APFS metadata block size')
    mod = 0xFFFFFFFF
    words = struct.unpack_from(f'<{len(block) // 4}I', block, 0)
    sum1 = 0
    sum2 = 0
    for w in words[2:]:
        sum1 += w
        sum2 += sum1
    sum1 %= mod
    sum2 %= mod
    ck1 = mod - ((sum1 + sum2) % mod)
    ck2 = mod - ((sum1 + ck1) % mod)
    return (ck2 << 32) | ck1


def verify_fletcher64(block: bytes) -> bool:
    stored, = struct.unpack_from('<Q', block, 0)
    return stored == fletcher64(block)


def parse_obj_header(block: bytes) -> dict:
    cksum, oid, xid, o_type, o_subtype = struct.unpack_from('<QQQII', block, 0)
    return {
        'cksum': cksum,
        'oid': oid,
        'xid': xid,
        'type': o_type & OBJECT_TYPE_MASK,
        'flags': o_type & ~OBJECT_TYPE_MASK,
        'subtype': o_subtype,
    }


def decode_roles(role_bits: int) -> list[str]:
    if role_bits == 0:
        return ['NONE']
    names = [name for mask, name in ROLE_NAMES if role_bits & mask]
    known = sum(mask for mask, _ in ROLE_NAMES)
    if role_bits & ~known:
        names.append(f'OTHER_0x{role_bits & ~known:x}')
    return names


def owned_processes_holding_inode(dev: int, ino: int) -> list[int]:
    uid = os.geteuid()
    holders = []
    for proc_stat in Path('/proc').glob('[0-9]*/status'):
        try:
            pid = int(proc_stat.parent.name)
            if pid == os.getpid():
                continue
            status_txt = proc_stat.read_text()
            if f'\nUid:\t{uid}\t' not in '\n' + status_txt:
                continue
            for fd_entry in (proc_stat.parent / 'fd').iterdir():
                try:
                    st = fd_entry.stat()
                except OSError:
                    continue
                if (st.st_dev, st.st_ino) == (dev, ino):
                    holders.append(pid)
                    break
        except (OSError, ValueError):
            continue
    return sorted(holders)


def read_exact(fd: int, offset: int, size: int) -> bytes:
    out = bytearray()
    while len(out) < size:
        chunk = os.pread(fd, size - len(out), offset + len(out))
        if not chunk:
            raise ValueError(f'Short read at byte offset {offset + len(out)}')
        out.extend(chunk)
    return bytes(out)


def find_apfs_containers(fd: int, image_size: int) -> list[dict]:
    if image_size >= 4096:
        head = read_exact(fd, 0, 4096)
        if head[32:36] == NX_MAGIC:
            return [{
                'source': 'direct_container',
                'gpt_type_name': 'Apple_APFS',
                'partition_index': None,
                'byte_offset': 0,
                'byte_length': image_size,
            }]
    for sector_size in (4096, 512):
        if image_size < sector_size * 3:
            continue
        gpt = read_exact(fd, sector_size, sector_size)
        if gpt[:8] != GPT_HEADER_MAGIC:
            continue
        part_lba, num_parts, entry_size = struct.unpack_from('<QII', gpt, 72)
        if entry_size < 128 or entry_size > 4096 or num_parts > 512:
            continue
        table_bytes = num_parts * entry_size
        table_off = part_lba * sector_size
        if table_off + table_bytes > image_size:
            continue
        table = read_exact(fd, table_off, table_bytes)
        containers = []
        for idx in range(num_parts):
            ent = table[idx * entry_size:(idx + 1) * entry_size]
            type_guid = ent[:16]
            type_name = GPT_APFS_TYPES.get(type_guid)
            if type_name is not None:
                first_lba, last_lba = struct.unpack_from('<QQ', ent, 32)
                if last_lba >= first_lba > 0:
                    containers.append({
                        'source': f'gpt_lba_{sector_size}',
                        'gpt_type_name': type_name,
                        'partition_index': idx,
                        'byte_offset': first_lba * sector_size,
                        'byte_length': (last_lba - first_lba + 1) * sector_size,
                    })
        if containers:
            return containers
    raise ValueError('No bare APFS container or GPT APFS partition found')


def parse_nx_superblock(block: bytes) -> dict:
    if block[32:36] != NX_MAGIC:
        raise ValueError('Missing NXSB magic')
    hdr = parse_obj_header(block)
    if hdr['type'] != OBJECT_TYPE_NX_SUPERBLOCK:
        raise ValueError(f'Unexpected NX object type {hdr["type"]:#x}')
    block_size, block_count = struct.unpack_from('<IQ', block, 36)
    next_oid, next_xid = struct.unpack_from('<QQ', block, 88)
    xp_desc_blocks, xp_data_blocks, xp_desc_base, xp_data_base = struct.unpack_from('<IIqq', block, 104)
    xp_desc_next, xp_data_next, xp_desc_index, xp_desc_len, xp_data_index, xp_data_len = struct.unpack_from(
        '<IIIIII', block, 128
    )
    spaceman_oid, omap_oid, reaper_oid = struct.unpack_from('<QQQ', block, 152)
    max_fs, = struct.unpack_from('<I', block, 180)
    count = min(max_fs, 100)
    fs_oids = [oid for oid in struct.unpack_from(f'<{count}Q', block, 184) if oid != 0]
    return {
        'header': hdr,
        'checksum_valid': verify_fletcher64(block),
        'block_size': block_size,
        'block_count': block_count,
        'next_oid': next_oid,
        'next_xid': next_xid,
        'xp_desc_is_btree': bool(xp_desc_blocks & 0x80000000),
        'xp_desc_blocks': xp_desc_blocks & 0x7FFFFFFF,
        'xp_data_blocks': xp_data_blocks & 0x7FFFFFFF,
        'xp_desc_base': xp_desc_base,
        'xp_data_base': xp_data_base,
        'xp_desc_next': xp_desc_next,
        'xp_desc_index': xp_desc_index,
        'xp_desc_len': xp_desc_len,
        'spaceman_oid': spaceman_oid,
        'omap_oid': omap_oid,
        'reaper_oid': reaper_oid,
        'max_file_systems': max_fs,
        'fs_oids': fs_oids,
    }


def lookup_omap(fd: int, base_off: int, block_size: int, tree_paddr: int, target_oid: int, max_xid: int, depth: int = 0):
    if depth > 16:
        raise ValueError('OMAP B-tree depth exceeded safety bound')
    block = read_exact(fd, base_off + tree_paddr * block_size, block_size)
    hdr = parse_obj_header(block)
    btn_flags, btn_level, btn_nkeys, toc_off, toc_len = struct.unpack_from('<HHIHH', block, 32)
    if not (btn_flags & BTNODE_NOHEADER):
        if not verify_fletcher64(block):
            raise ValueError(f'OMAP B-tree node at block {tree_paddr} failed Fletcher-64')
        if hdr['type'] not in (OBJECT_TYPE_BTREE, OBJECT_TYPE_BTREE_NODE):
            raise ValueError(f'Unexpected OMAP tree node type {hdr["type"]:#x}')
    toc_start = 56 + toc_off
    key_area = toc_start + toc_len
    val_end = block_size - (40 if (btn_flags & BTNODE_ROOT) else 0)
    fixed = bool(btn_flags & BTNODE_FIXED_KV_SIZE)
    entries = []
    for i in range(btn_nkeys):
        if fixed:
            k_off, v_off = struct.unpack_from('<HH', block, toc_start + i * 4)
            k_len = 16
            v_len = 16 if (btn_flags & BTNODE_LEAF) else 8
        else:
            k_off, k_len, v_off, v_len = struct.unpack_from('<HHHH', block, toc_start + i * 8)
        if k_len < 16:
            raise ValueError('OMAP key shorter than 16 bytes')
        ok_oid, ok_xid = struct.unpack_from('<QQ', block, key_area + k_off)
        v_bytes = block[val_end - v_off:val_end - v_off + v_len]
        entries.append((ok_oid, ok_xid, v_bytes))

    if btn_flags & BTNODE_LEAF:
        if btn_level != 0:
            raise ValueError('Leaf flag set with non-zero B-tree level')
        best = None
        for ok_oid, ok_xid, v_bytes in entries:
            if ok_oid == target_oid and ok_xid <= max_xid:
                if len(v_bytes) < 16:
                    raise ValueError('OMAP leaf value shorter than 16 bytes')
                ov_flags, ov_size, ov_paddr = struct.unpack_from('<IIq', v_bytes, 0)
                if not (ov_flags & OV_INVALID):
                    if best is None or ok_xid > best['xid']:
                        best = {'oid': ok_oid, 'xid': ok_xid, 'flags': ov_flags, 'size': ov_size, 'paddr': ov_paddr}
        return best

    best = None
    for idx, (ok_oid, ok_xid, v_bytes) in enumerate(entries):
        if (ok_oid, ok_xid) > (target_oid, max_xid) and idx > 0:
            break
        next_min_oid = entries[idx + 1][0] if idx + 1 < len(entries) else None
        if next_min_oid is not None and next_min_oid < target_oid:
            continue
        if len(v_bytes) < 8:
            raise ValueError('OMAP index value shorter than 8 bytes')
        child_paddr, = struct.unpack_from('<Q', v_bytes, 0)
        cand = lookup_omap(fd, base_off, block_size, child_paddr, target_oid, max_xid, depth + 1)
        if cand is not None and (best is None or cand['xid'] > best['xid']):
            best = cand
    return best


def parse_apfs_superblock(block: bytes) -> dict:
    if block[32:36] != APFS_MAGIC:
        raise ValueError('Missing APSB magic')
    hdr = parse_obj_header(block)
    if hdr['type'] != OBJECT_TYPE_FS:
        raise ValueError(f'Unexpected volume superblock type {hdr["type"]:#x}')
    if not verify_fletcher64(block):
        raise ValueError('Volume superblock failed Fletcher-64')
    fs_index, = struct.unpack_from('<I', block, 36)
    features, ro_compat, incompat, unmount_time = struct.unpack_from('<QQQQ', block, 40)
    alloc_count, = struct.unpack_from('<Q', block, 88)
    num_files, num_dirs, num_symlinks, num_other, num_snaps = struct.unpack_from('<QQQQQ', block, 184)
    last_mod_time, fs_flags = struct.unpack_from('<QQ', block, 256)
    formatted_id = block[272:304].split(b'\x00', 1)[0].decode('ascii', errors='replace')
    last_mod_id = block[320:352].split(b'\x00', 1)[0].decode('ascii', errors='replace')
    role_bits, = struct.unpack_from('<H', block, 964)
    unmount_iso = (
        datetime.fromtimestamp(unmount_time / 1e9, tz=timezone.utc).isoformat()
        if unmount_time > 0 else None
    )
    last_mod_iso = (
        datetime.fromtimestamp(last_mod_time / 1e9, tz=timezone.utc).isoformat()
        if last_mod_time > 0 else None
    )
    return {
        'fs_index': fs_index,
        'xid': hdr['xid'],
        'roles': decode_roles(role_bits),
        'role_bits': role_bits,
        'fs_flags': fs_flags,
        'unencrypted': bool(fs_flags & APFS_FS_UNENCRYPTED),
        'onekey_encrypted': bool(fs_flags & APFS_FS_ONEKEY),
        'unmount_time_ns': unmount_time,
        'unmount_time_utc': unmount_iso,
        'last_mod_time_ns': last_mod_time,
        'last_mod_time_utc': last_mod_iso,
        'alloc_blocks': alloc_count,
        'num_files': num_files,
        'num_directories': num_dirs,
        'num_snapshots': num_snaps,
        'formatted_by': formatted_id,
        'last_modified_by': last_mod_id,
        'fletcher64_valid': True,
    }


def inspect_container(fd: int, container: dict) -> dict:
    base = container['byte_offset']
    length = container['byte_length']
    first4k = read_exact(fd, base, 4096)
    boot_nx = parse_nx_superblock(first4k)
    bs = boot_nx['block_size']
    if bs < 4096 or bs > 65536 or (bs & (bs - 1)) != 0:
        raise ValueError(f'Unsupported APFS block size {bs}')
    if bs != 4096:
        boot_block = read_exact(fd, base, bs)
        boot_nx = parse_nx_superblock(boot_block)
    if boot_nx['block_count'] * bs > length:
        raise ValueError('Container block count exceeds partition length')
    if boot_nx['xp_desc_is_btree'] or boot_nx['xp_desc_blocks'] == 0:
        raise ValueError('Unsupported or empty checkpoint descriptor ring')

    desc_base = boot_nx['xp_desc_base']
    desc_count = boot_nx['xp_desc_blocks']
    candidates = []
    ring_blocks = []
    for idx in range(desc_count):
        blk = read_exact(fd, base + (desc_base + idx) * bs, bs)
        ring_blocks.append(blk)
        if blk[32:36] == NX_MAGIC and verify_fletcher64(blk):
            parsed = parse_nx_superblock(blk)
            parsed['ring_index'] = idx
            candidates.append(parsed)

    if not candidates:
        raise ValueError('No valid NXSB checkpoint superblocks found in descriptor ring')

    candidates.sort(key=lambda c: c['header']['xid'], reverse=True)
    chosen = None
    for cand in candidates:
        xp_idx = cand['xp_desc_index']
        xp_len = cand['xp_desc_len']
        if xp_len == 0 or xp_len > desc_count:
            continue
        slice_ok = True
        for off in range(xp_len):
            r_idx = (xp_idx + off) % desc_count
            r_blk = ring_blocks[r_idx]
            r_hdr = parse_obj_header(r_blk)
            if not verify_fletcher64(r_blk):
                slice_ok = False
                break
            if r_hdr['xid'] != cand['header']['xid']:
                slice_ok = False
                break
            if off < xp_len - 1 and r_hdr['type'] != OBJECT_TYPE_CHECKPOINT_MAP:
                slice_ok = False
                break
        if slice_ok and ((xp_idx + xp_len - 1) % desc_count) == cand['ring_index']:
            chosen = cand
            break

    if chosen is None:
        raise ValueError('No checkpoint descriptor slice had intact Fletcher-64 & XID framing')

    omap_blk = read_exact(fd, base + chosen['omap_oid'] * bs, bs)
    if not verify_fletcher64(omap_blk):
        raise ValueError('Container omap_phys_t failed Fletcher-64')
    omap_hdr = parse_obj_header(omap_blk)
    if omap_hdr['type'] != OBJECT_TYPE_OMAP:
        raise ValueError(f'Unexpected container OMAP object type {omap_hdr["type"]:#x}')
    om_flags, om_snap_count, om_tree_type, om_snap_tree_type, om_tree_oid = struct.unpack_from(
        '<IIIIQ', omap_blk, 32
    )

    volumes = []
    max_xid = chosen['header']['xid']
    for fs_oid in chosen['fs_oids']:
        resolved = lookup_omap(fd, base, bs, om_tree_oid, fs_oid, max_xid)
        if resolved is None:
            raise ValueError(f'Volume virtual OID {fs_oid:#x} missing from container OMAP')
        vol_blk = read_exact(fd, base + resolved['paddr'] * bs, bs)
        vol = parse_apfs_superblock(vol_blk)
        vol['virtual_oid'] = fs_oid
        vol['omap_xid'] = resolved['xid']
        vol['physical_block'] = resolved['paddr']
        volumes.append(vol)

    volumes.sort(key=lambda v: v['fs_index'])
    all_roles = {r for v in volumes for r in v['roles']}
    is_main_os_container = (
        container['gpt_type_name'] == 'Apple_APFS'
        and 'SYSTEM' in all_roles
        and 'DATA' in all_roles
    )
    container_cleanly_unmounted = (
        boot_nx['checksum_valid']
        and boot_nx['header']['xid'] == max_xid
        and boot_nx['omap_oid'] == chosen['omap_oid']
        and boot_nx['fs_oids'] == chosen['fs_oids']
        and bool(volumes)
        and all(v['fletcher64_valid'] for v in volumes)
    )
    return {
        'source': container['source'],
        'gpt_type_name': container['gpt_type_name'],
        'partition_index': container['partition_index'],
        'is_main_os_container': is_main_os_container,
        'byte_offset': base,
        'byte_length': length,
        'block_size': bs,
        'block_count': chosen['block_count'],
        'block0_nxsb_fletcher64_valid': boot_nx['checksum_valid'],
        'block0_xid': boot_nx['header']['xid'],
        'block0_matches_latest_checkpoint': (
            boot_nx['checksum_valid']
            and boot_nx['header']['xid'] == max_xid
            and boot_nx['omap_oid'] == chosen['omap_oid']
        ),
        'container_cleanly_unmounted': container_cleanly_unmounted,
        'valid_checkpoint_superblocks_in_ring': len(candidates),
        'latest_checkpoint': {
            'xid': max_xid,
            'ring_index': chosen['ring_index'],
            'xp_desc_index': chosen['xp_desc_index'],
            'xp_desc_len': chosen['xp_desc_len'],
            'slice_fletcher64_verified': True,
            'omap_oid': chosen['omap_oid'],
            'omap_tree_oid': om_tree_oid,
            'omap_flags': om_flags,
            'omap_snap_count': om_snap_count,
        },
        'volume_count': len(volumes),
        'volumes': volumes,
    }


def parse_kernel_boot_apfs_mount(log_text: str) -> dict:
    """Cross-check XNU apfs.kext nx_mount lines from a subsequent boot log."""
    search_re = re.compile(
        r'nx_mount:\d+:\s+(disk\d+(?:s\d+)?)\s+checkpoint search: largest xid (\d+), best xid (\d+)'
    )
    unclean_re = re.compile(
        r'nx_mount:\d+:\s+(disk\d+(?:s\d+)?)\s+reloading after unclean unmount, checkpoint xid (\d+), superblock xid (\d+)'
    )
    stable_re = re.compile(
        r'nx_mount:\d+:\s+(disk\d+(?:s\d+)?)\s+stable checkpoint indices: desc (\d+) data (\d+)'
    )
    mounts: dict[str, dict] = {}
    for dev, largest, best in search_re.findall(log_text):
        mounts.setdefault(dev, {'unclean_reload_logged': False})
        mounts[dev].update(largest_xid=int(largest), best_xid=int(best))
    for dev, cp_xid, sb_xid in unclean_re.findall(log_text):
        mounts.setdefault(dev, {})
        mounts[dev].update(
            unclean_reload_logged=True,
            checkpoint_xid=int(cp_xid),
            superblock_xid=int(sb_xid),
        )
    for dev, desc_idx, data_idx in stable_re.findall(log_text):
        mounts.setdefault(dev, {'unclean_reload_logged': False})
        mounts[dev].update(stable_desc_index=int(desc_idx), stable_data_index=int(data_idx))
    return mounts


def inspect_image(
    path: Path,
    *,
    label: str | None = None,
    require_closed: bool = True,
    next_boot_kernel_log: Path | None = None,
) -> dict:
    result = {
        'schema': 1,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'target_label': label or path.name,
        'opened_readonly': True,
        'require_closed_by_owned_processes': require_closed,
        'main_os_container_cleanly_unmounted': False,
        'all_containers_cleanly_unmounted': False,
        'containers': [],
        'errors': [],
        'scope': SCOPE,
    }
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
            raise ValueError('Image must be an owned regular file')
        result['image_bytes'] = st.st_size
        holders = owned_processes_holding_inode(st.st_dev, st.st_ino)
        result['open_holder_pids'] = holders
        if require_closed and holders:
            raise ValueError(f'Image inode is currently held open by owned PIDs: {holders}')
        containers = find_apfs_containers(fd, st.st_size)
        for cont in containers:
            result['containers'].append(inspect_container(fd, cont))
        after = os.fstat(fd)
        if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise ValueError('Image changed during read-only inspection')
    except (OSError, ValueError, struct.error) as error:
        result['errors'].append(str(error))
    finally:
        os.close(fd)

    if next_boot_kernel_log is not None:
        try:
            result['next_boot_nx_mount'] = parse_kernel_boot_apfs_mount(
                next_boot_kernel_log.read_text(encoding='utf-8', errors='replace')
            )
        except OSError as error:
            result['errors'].append(f'kernel_log: {error}')

    if not result['errors'] and result['containers']:
        main_containers = [c for c in result['containers'] if c['is_main_os_container']]
        target_main = main_containers if main_containers else result['containers']
        result['main_os_container_cleanly_unmounted'] = all(
            c['container_cleanly_unmounted'] for c in target_main
        )
        result['all_containers_cleanly_unmounted'] = all(
            c['container_cleanly_unmounted'] for c in result['containers']
        )
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, type=Path)
    parser.add_argument('--label', help='Redacted logical label to emit instead of filesystem path')
    parser.add_argument('--next-boot-kernel-log', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--allow-open', action='store_true')
    args = parser.parse_args(argv)
    report = inspect_image(
        args.image,
        label=args.label,
        require_closed=not args.allow_open,
        next_boot_kernel_log=args.next_boot_kernel_log,
    )
    if args.output is not None:
        with args.output.open('x') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
    print(json.dumps({
        'target_label': report['target_label'],
        'main_os_container_cleanly_unmounted': report['main_os_container_cleanly_unmounted'],
        'all_containers_cleanly_unmounted': report['all_containers_cleanly_unmounted'],
        'containers': [
            {
                'partition_index': c['partition_index'],
                'gpt_type_name': c['gpt_type_name'],
                'is_main_os_container': c['is_main_os_container'],
                'block0_xid': c['block0_xid'],
                'latest_checkpoint_xid': c['latest_checkpoint']['xid'],
                'container_cleanly_unmounted': c['container_cleanly_unmounted'],
                'volume_count': c['volume_count'],
            }
            for c in report['containers']
        ],
        'errors': report['errors'],
    }))
    return 0 if report['main_os_container_cleanly_unmounted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
