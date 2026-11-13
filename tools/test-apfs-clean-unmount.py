#!/usr/bin/env python3
"""Offline unit tests for tools/check-apfs-clean-unmount.py."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('check_apfs_clean_unmount', HERE / 'check-apfs-clean-unmount.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

BS = 4096
APFS_GUID = bytes.fromhex('ef57347c0000aa11aa1100306543ecac')
RECOVERY_GUID = bytes.fromhex('727663520079aa11aa1100306543ecac')


def seal_block(buf: bytearray, oid: int, xid: int, o_type: int, o_subtype: int = 0) -> bytes:
    struct.pack_into('<QQQII', buf, 0, 0, oid, xid, o_type, o_subtype)
    ck = MOD.fletcher64(bytes(buf))
    struct.pack_into('<Q', buf, 0, ck)
    return bytes(buf)


def make_cpm_block(xid: int) -> bytes:
    buf = bytearray(BS)
    struct.pack_into('<II', buf, 32, 0x00000001, 0)
    return seal_block(buf, 500, xid, MOD.OBJECT_TYPE_CHECKPOINT_MAP | 0x40000000)


def make_nx_block(
    *,
    xid: int,
    block_count: int,
    xp_desc_base: int,
    xp_desc_blocks: int,
    xp_desc_index: int,
    xp_desc_len: int,
    omap_oid: int,
    fs_oids: list[int],
) -> bytes:
    buf = bytearray(BS)
    buf[32:36] = MOD.NX_MAGIC
    struct.pack_into('<IQ', buf, 36, BS, block_count)
    buf[72:88] = b'SECRET_UUID_NX00'
    struct.pack_into('<QQ', buf, 88, 1000, xid + 1)
    struct.pack_into('<IIqq', buf, 104, xp_desc_blocks, 4, xp_desc_base, xp_desc_base + xp_desc_blocks)
    struct.pack_into(
        '<IIIIII', buf, 128,
        (xp_desc_index + xp_desc_len) % xp_desc_blocks, 0, xp_desc_index, xp_desc_len, 0, 0,
    )
    struct.pack_into('<QQQ', buf, 152, 900, omap_oid, 901)
    struct.pack_into('<II', buf, 176, 0, 100)
    for idx, oid in enumerate(fs_oids):
        struct.pack_into('<Q', buf, 184 + idx * 8, oid)
    return seal_block(buf, 1, xid, MOD.OBJECT_TYPE_NX_SUPERBLOCK | 0x80000000)


def make_omap_phys(oid: int, xid: int, tree_oid: int) -> bytes:
    buf = bytearray(BS)
    struct.pack_into('<IIIIQ', buf, 32, 0, 0, MOD.OBJECT_TYPE_BTREE, 0, tree_oid)
    return seal_block(buf, oid, xid, MOD.OBJECT_TYPE_OMAP | 0x40000000)


def make_omap_leaf(oid: int, xid: int, records: list[tuple[int, int, int]], *, is_root: bool = False) -> bytes:
    buf = bytearray(BS)
    flags = MOD.BTNODE_LEAF | MOD.BTNODE_FIXED_KV_SIZE | (MOD.BTNODE_ROOT if is_root else 0)
    toc_len = 64
    struct.pack_into('<HHIHH', buf, 32, flags, 0, len(records), 0, toc_len)
    toc_start = 56
    key_area = toc_start + toc_len
    val_end = BS - (40 if is_root else 0)
    for i, (ok_oid, ok_xid, paddr) in enumerate(records):
        k_off = i * 16
        v_off = (i + 1) * 16
        struct.pack_into('<HH', buf, toc_start + i * 4, k_off, v_off)
        struct.pack_into('<QQ', buf, key_area + k_off, ok_oid, ok_xid)
        struct.pack_into('<IIq', buf, val_end - v_off, 0, BS, paddr)
    node_type = MOD.OBJECT_TYPE_BTREE if is_root else MOD.OBJECT_TYPE_BTREE_NODE
    return seal_block(buf, oid, xid, node_type | 0x40000000)


def make_omap_root_index(oid: int, xid: int, children: list[tuple[int, int, int]]) -> bytes:
    buf = bytearray(BS)
    flags = MOD.BTNODE_ROOT | MOD.BTNODE_FIXED_KV_SIZE
    toc_len = 64
    struct.pack_into('<HHIHH', buf, 32, flags, 1, len(children), 0, toc_len)
    toc_start = 56
    key_area = toc_start + toc_len
    val_end = BS - 40
    for i, (min_oid, min_xid, child_paddr) in enumerate(children):
        k_off = i * 16
        v_off = (i + 1) * 8
        struct.pack_into('<HH', buf, toc_start + i * 4, k_off, v_off)
        struct.pack_into('<QQ', buf, key_area + k_off, min_oid, min_xid)
        struct.pack_into('<Q', buf, val_end - v_off, child_paddr)
    return seal_block(buf, oid, xid, MOD.OBJECT_TYPE_BTREE | 0x40000000)


def make_apsb_block(
    *,
    oid: int,
    xid: int,
    fs_index: int,
    role_bits: int,
    unmount_ns: int = 1794550000_000_000_000,
) -> bytes:
    buf = bytearray(BS)
    buf[32:36] = MOD.APFS_MAGIC
    struct.pack_into('<I', buf, 36, fs_index)
    struct.pack_into('<QQQQ', buf, 40, 0, 0, 0, unmount_ns)
    struct.pack_into('<Q', buf, 88, 1234)
    struct.pack_into('<QQQQQ', buf, 184, 42, 7, 1, 0, 1 if role_bits == 0x0001 else 0)
    buf[240:256] = b'SECRET_UUID_VOL!'
    flags = MOD.APFS_FS_ONEKEY if role_bits == 0x0040 else MOD.APFS_FS_UNENCRYPTED
    struct.pack_into('<QQ', buf, 256, unmount_ns, flags)
    buf[272:286] = b'newfs_apfs_sim'
    buf[320:334] = b'apfs_kext_sim0'
    buf[704:716] = b'SecretVolNam'
    struct.pack_into('<H', buf, 964, role_bits)
    return seal_block(buf, oid, xid, MOD.OBJECT_TYPE_FS | 0x80000000)


def build_synthetic_container(
    *,
    flush_block0_on_unmount: bool = True,
    corrupt_latest_cpm: bool = False,
    roles: tuple[int, ...] = (0x0001, 0x0040),
) -> bytearray:
    total_blocks = 20
    img = bytearray(total_blocks * BS)
    fs_oids = [0x402 + i * 2 for i in range(len(roles))]

    nx10 = make_nx_block(
        xid=10, block_count=total_blocks, xp_desc_base=1, xp_desc_blocks=4,
        xp_desc_index=0, xp_desc_len=2, omap_oid=9, fs_oids=fs_oids,
    )
    nx20 = make_nx_block(
        xid=20, block_count=total_blocks, xp_desc_base=1, xp_desc_blocks=4,
        xp_desc_index=2, xp_desc_len=2, omap_oid=10, fs_oids=fs_oids,
    )
    img[0:BS] = nx20 if flush_block0_on_unmount else nx10

    img[1 * BS:2 * BS] = make_cpm_block(10)
    img[2 * BS:3 * BS] = nx10

    cpm20 = bytearray(make_cpm_block(20))
    if corrupt_latest_cpm:
        cpm20[40] ^= 0xFF
    img[3 * BS:4 * BS] = bytes(cpm20)
    img[4 * BS:5 * BS] = nx20

    img[9 * BS:10 * BS] = make_omap_phys(9, 10, 11)
    img[11 * BS:12 * BS] = make_omap_leaf(
        11, 10, [(oid, 10, 15 + idx) for idx, oid in enumerate(fs_oids)], is_root=True
    )

    img[10 * BS:11 * BS] = make_omap_phys(10, 20, 12)
    if len(fs_oids) == 2:
        img[12 * BS:13 * BS] = make_omap_root_index(12, 20, [(fs_oids[0], 10, 13), (fs_oids[1], 10, 14)])
        img[13 * BS:14 * BS] = make_omap_leaf(13, 20, [(fs_oids[0], 10, 15), (fs_oids[0], 20, 17)])
        img[14 * BS:15 * BS] = make_omap_leaf(14, 20, [(fs_oids[1], 10, 16), (fs_oids[1], 20, 18)])
    else:
        img[12 * BS:13 * BS] = make_omap_leaf(12, 20, [(fs_oids[0], 20, 17)], is_root=True)

    for idx, role in enumerate(roles):
        img[(15 + idx) * BS:(16 + idx) * BS] = make_apsb_block(
            oid=fs_oids[idx], xid=10, fs_index=idx, role_bits=role
        )
        img[(17 + idx) * BS:(18 + idx) * BS] = make_apsb_block(
            oid=fs_oids[idx], xid=20, fs_index=idx, role_bits=role
        )
    return img


def wrap_in_gpt_4k(partitions: list[tuple[bytes, bytes]]) -> bytes:
    lba_size = 4096
    cur_lba = 6
    entries = []
    blobs = []
    for type_guid, data in partitions:
        n_lba = len(data) // lba_size
        first_lba = cur_lba
        last_lba = first_lba + n_lba - 1
        cur_lba = last_lba + 1
        ent = bytearray(128)
        ent[:16] = type_guid
        struct.pack_into('<QQ', ent, 32, first_lba, last_lba)
        entries.append((first_lba, last_lba, bytes(ent), data))
        blobs.append(data)

    disk = bytearray((cur_lba + 6) * lba_size)
    gpt = bytearray(lba_size)
    gpt[:8] = MOD.GPT_HEADER_MAGIC
    struct.pack_into('<QII', gpt, 72, 2, len(entries), 128)
    disk[lba_size:2 * lba_size] = gpt
    for idx, (first_lba, last_lba, ent_bytes, data) in enumerate(entries):
        disk[2 * lba_size + idx * 128:2 * lba_size + (idx + 1) * 128] = ent_bytes
        disk[first_lba * lba_size:(last_lba + 1) * lba_size] = data
    return bytes(disk)


class CheckApfsCleanUnmountTests(unittest.TestCase):
    def test_clean_two_level_omap_and_uuid_omission(self):
        raw = build_synthetic_container(flush_block0_on_unmount=True)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'clean.apfs'
            p.write_bytes(raw)
            res = MOD.inspect_image(p, label='synthetic-clean')
            self.assertTrue(res['main_os_container_cleanly_unmounted'], res)
            self.assertTrue(res['all_containers_cleanly_unmounted'], res)
            cont = res['containers'][0]
            self.assertEqual(cont['block0_xid'], 20)
            self.assertEqual(cont['latest_checkpoint']['xid'], 20)
            self.assertEqual([v['roles'] for v in cont['volumes']], [['SYSTEM'], ['DATA']])
            serialized = json.dumps(res)
            self.assertNotIn('SECRET_UUID', serialized)
            self.assertNotIn('SecretVolNam', serialized)
            self.assertNotIn(str(p), serialized)

    def test_gpt_main_clean_with_untouched_recovery_partition(self):
        main_clean = bytes(build_synthetic_container(flush_block0_on_unmount=True, roles=(0x0001, 0x0040)))
        rec_unclean = bytes(build_synthetic_container(flush_block0_on_unmount=False, roles=(0x0004,)))
        disk = wrap_in_gpt_4k([(APFS_GUID, main_clean), (RECOVERY_GUID, rec_unclean)])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'disk.raw'
            p.write_bytes(disk)
            klog = Path(tmp) / 'kernel-boot.log'
            klog.write_text(
                '[5.10]: nx_mount:1482: disk0s1 checkpoint search: largest xid 20, best xid 20 @ 3\n'
                '[5.10]: nx_mount:1509: disk0s1 stable checkpoint indices: desc 2 data 0\n'
                '[5.11]: nx_mount:1482: disk0s2 checkpoint search: largest xid 20, best xid 20 @ 3\n'
                '[5.11]: nx_mount:1484: disk0s2 reloading after unclean unmount, checkpoint xid 20, superblock xid 10\n',
                encoding='utf-8',
            )
            res = MOD.inspect_image(p, label='synthetic-gpt', next_boot_kernel_log=klog)
            self.assertTrue(res['main_os_container_cleanly_unmounted'])
            self.assertFalse(res['all_containers_cleanly_unmounted'])
            self.assertFalse(res['next_boot_nx_mount']['disk0s1']['unclean_reload_logged'])
            self.assertTrue(res['next_boot_nx_mount']['disk0s2']['unclean_reload_logged'])

    def test_unclean_block0_or_corrupt_apsb_or_open_holder_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 1. Block 0 left at xid=10 while ring reached xid=20 (active/crashed mount)
            dirty = Path(tmp) / 'dirty.apfs'
            dirty.write_bytes(build_synthetic_container(flush_block0_on_unmount=False))
            r_dirty = MOD.inspect_image(dirty)
            self.assertFalse(r_dirty['main_os_container_cleanly_unmounted'])
            self.assertEqual(r_dirty['containers'][0]['block0_xid'], 10)
            self.assertEqual(r_dirty['containers'][0]['latest_checkpoint']['xid'], 20)

            # 2. Torn xid=20 checkpoint falls back to xid=10 while block0 had 20 -> mismatch
            torn = Path(tmp) / 'torn.apfs'
            torn.write_bytes(build_synthetic_container(flush_block0_on_unmount=True, corrupt_latest_cpm=True))
            r_torn = MOD.inspect_image(torn)
            self.assertFalse(r_torn['main_os_container_cleanly_unmounted'])
            self.assertEqual(r_torn['containers'][0]['latest_checkpoint']['xid'], 10)

            # 3. Corrupted APSB Fletcher-64 checksum
            bad_apsb = build_synthetic_container(flush_block0_on_unmount=True)
            bad_apsb[18 * BS + 40] ^= 1
            corrupt = Path(tmp) / 'corrupt.apfs'
            corrupt.write_bytes(bad_apsb)
            r_corrupt = MOD.inspect_image(corrupt)
            self.assertFalse(r_corrupt['main_os_container_cleanly_unmounted'])
            self.assertTrue(r_corrupt['errors'])

            # 4. Open holder PID refused when require_closed=True
            clean = Path(tmp) / 'held.apfs'
            clean.write_bytes(build_synthetic_container(flush_block0_on_unmount=True))
            with patch.object(MOD, 'owned_processes_holding_inode', return_value=[99999]):
                r_held = MOD.inspect_image(clean, require_closed=True)
                self.assertFalse(r_held['main_os_container_cleanly_unmounted'])
                self.assertIn('99999', r_held['errors'][0])


if __name__ == '__main__':
    unittest.main()
