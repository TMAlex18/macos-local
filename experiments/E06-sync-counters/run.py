#!/usr/bin/env python3
"""Validate 0033 per-vCPU TCG BQL and exclusive-section source counters."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_guest(build_dir: Path) -> Path:
    build_dir.mkdir(parents=True, exist_ok=True)
    ld_path = build_dir / 'link.ld'
    ld_path.write_text(
        'ENTRY(_start)\n'
        'SECTIONS {\n'
        '  . = 0x40080000;\n'
        '  .text : { *(.text.boot) *(.text*) }\n'
        '  .data : { *(.data*) }\n'
        '  /DISCARD/ : { *(.comment) *(.eh_frame*) *(.note*) }\n'
        '}\n'
    )
    obj = build_dir / 'guest.o'
    elf = build_dir / 'guest.elf'
    subprocess.run(
        [
            'clang', '--target=aarch64-none-elf', '-march=armv8.4-a',
            '-c', str(HERE / 'guest.S'), '-o', str(obj),
        ],
        check=True,
    )
    subprocess.run(
        ['ld.lld', '--build-id=none', '-T', str(ld_path), str(obj), '-o', str(elf)],
        check=True,
    )
    return elf


def run_case(qemu: Path, guest: Path, enabled: bool) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix='e06-sync-cnt-') as tmp:
        tmp_dir = Path(tmp)
        qmp_path = tmp_dir / 'qmp.sock'
        uart_path = tmp_dir / 'uart.log'
        cpu_arg = 'max,x-sync-stats=on' if enabled else 'max'
        cmd = [
            str(qemu), '-machine', 'virt', '-cpu', cpu_arg,
            '-accel', 'tcg,thread=multi', '-smp', '2', '-m', '128M',
            '-nodefaults', '-nic', 'none', '-display', 'none', '-monitor', 'none',
            '-serial', f'file:{uart_path}', '-kernel', str(guest),
            '-qmp', f'unix:{qmp_path},server=on,wait=off',
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        sock = None
        stream = None
        try:
            deadline = time.monotonic() + 10.0
            while not qmp_path.exists():
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('QMP socket did not appear')
                time.sleep(0.01)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(10.0)
            sock.connect(str(qmp_path))
            stream = sock.makefile('rb')
            json.loads(stream.readline())

            def qmp(execute: str, arguments: dict[str, object] | None = None):
                msg: dict[str, object] = {'execute': execute}
                if arguments is not None:
                    msg['arguments'] = arguments
                sock.sendall((json.dumps(msg) + '\n').encode())
                while True:
                    reply = json.loads(stream.readline())
                    if 'return' in reply:
                        return reply['return']
                    if 'error' in reply:
                        raise RuntimeError(f'QMP error on {execute}: {reply["error"]}')

            qmp('qmp_capabilities')
            while True:
                if uart_path.exists() and 'DONE\n' in uart_path.read_text():
                    break
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Guest did not reach DONE marker')
                time.sleep(0.01)

            # Give CPU 1 a brief moment to enter WFI before stopping
            time.sleep(0.05)
            qmp('stop')
            cpus = sorted(qmp('query-cpus-fast'), key=lambda r: r['cpu-index'])
            before_reset = [
                json.loads(qmp('qom-get', {'path': c['qom-path'], 'property': 'x-sync-stats-json'}))
                for c in cpus
            ]
            qmp('system_reset')
            after_reset = [
                json.loads(qmp('qom-get', {'path': c['qom-path'], 'property': 'x-sync-stats-json'}))
                for c in cpus
            ]
            qmp('quit')
            proc.wait(timeout=5.0)
            return {'before_reset': before_reset, 'after_reset': after_reset, 'exit_code': proc.returncode}
        finally:
            if stream is not None:
                stream.close()
            if sock is not None:
                sock.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--output', type=Path,
        default=ROOT / 'runs/e06-sync-counters/results.json',
    )
    args = parser.parse_args()

    qemu = ROOT / 'build/qemu-bdif-usb/qemu-system-aarch64'
    manifest = json.loads((ROOT / 'runs/qemu-bdif-usb-build.json').read_text())
    assert sha256(qemu) == manifest['binary_sha256'], 'QEMU binary SHA mismatch'
    assert 'patches/qemu-platform/0033-tcg-sync-source-counters.patch' in manifest['patches']

    guest = build_guest(ROOT / 'build/e06-sync-counters')

    off_res = run_case(qemu, guest, enabled=False)
    assert off_res['exit_code'] == 0
    assert off_res['before_reset'] == [{'enabled': False}, {'enabled': False}], off_res

    on_res = run_case(qemu, guest, enabled=True)
    assert on_res['exit_code'] == 0
    c0, c1 = on_res['before_reset']  # type: ignore[misc]
    assert c0['enabled'] is True and c1['enabled'] is True

    # Exact assertions on CPU 0 workload (including 0034/0035/0037/0038/0039 elisions)
    assert c0['cpreg_non_io_helper'].get('CNTVCT_EL0') == [37, 0], c0
    assert c0['cpreg_io'].get('CNTP_CVAL_EL0') == [0, 13], c0['cpreg_io']
    assert c0['bql_cpreg_read'] == 0, c0
    assert c0['bql_cpreg_write'] >= 13, c0
    assert c0['do_interrupt_svc'] == 19, c0
    assert c0['bql_do_interrupt'] >= 19, c0
    assert c0['bql_handle_interrupt'] == 0, c0
    assert c0['bql_eret'] == 0, c0
    assert c0['tlbi'].get('TLBI_VAE1IS') == [0, 11], c0['tlbi']
    assert c0['tlbi'].get('TLBI_RVAE1IS') == [0, 3], c0['tlbi']
    assert c0['tlbi'].get('TLBI_VMALLE1IS') == [0, 5], c0['tlbi']
    assert c0['tlbi'].get('TLBI_VALE1IS') == [0, 8], c0['tlbi']
    assert c0['excl_tlbi_page'] == 11, c0
    assert c0['excl_tlbi_range'] == 4, c0  # 3 RVAE1IS + 1 coalesced 8-page VALE1IS DSB batch
    assert c0['excl_tlbi_all'] == 5, c0
    assert c0['queued_work_exclusive'] == 20, c0
    assert c0['bql_exclusive_reacquire'] == 0, c0
    assert c0['bql_mmio'] == 5, c0  # 5 UART writes ('D','O','N','E','\n')

    # Peer CPU 1 received at least the 20 DSB-batched broadcast TLBI ordinary work items without BQL
    assert c1['queued_work_ordinary'] >= 20, c1
    assert c1['exit_interrupt'] == 0, c1
    assert c1['no_bql_exit_interrupt'] >= 1, c1

    # Verify reset cleared every guest-execution, BQL, exclusive, cpreg and TLBI counter
    # (a paused/halted vCPU thread can immediately re-park on halt_cond once after reset).
    for cr in on_res['after_reset']:  # type: ignore[union-attr]
        assert cr['enabled'] is True
        assert cr['cpreg_io'] == {} and cr['tlbi'] == {} and cr['cpreg_non_io_helper'] == {}, cr
        for k, v in cr.items():
            if k in ('enabled', 'cpreg_io', 'tlbi', 'cpreg_non_io_helper'):
                continue
            if k == 'halt_cond_waits':
                assert 0 <= v <= 2, f'Unexpected post-reset halt_cond_waits: {v}'
            else:
                assert v == 0, f'Counter {k} not cleared on reset: {v}'

    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        'passed': True,
        'binary': str(qemu.relative_to(ROOT)),
        'binary_sha256': sha256(qemu),
        'off_case': off_res,
        'enabled_case': on_res,
        'scope': 'Synthetic dual-vCPU bare-metal exact counter and reset checks; not macOS workload proportions.',
    }
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(f'PASS: E06 sync-source counters verified -> {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
