#!/usr/bin/env python3
"""Run real AArch64 indirect branches and self-modification on one QEMU.

This compiles only the self-authored guest, never QEMU. It records independent
arithmetic checksums, layout and source/binary identity. No performance claim.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).with_name('tb-lookup.S')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--iterations', default=4096, type=int)
    parser.add_argument('--trace-lookups', action='store_true',
                        help='Count real lookup entries with host GDB (at most 64 iterations)')
    args = parser.parse_args()
    if not 2 <= args.iterations <= 1_000_000:
        parser.error('iterations must be 2..1000000')
    if args.trace_lookups and args.iterations > 64:
        parser.error('--trace-lookups needs at most 64 iterations')
    qemu, out = args.qemu.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    elf, obj = out / 'tb-lookup.elf', out / 'tb-lookup.o'
    commands = [
        ['clang', '--target=aarch64-none-elf', '-march=armv8-a',
         f'-DITERATIONS={args.iterations}', '-c', str(SOURCE), '-o', str(obj)],
        ['ld.lld', '--build-id=none', '-Ttext=0x40080000', '-e', '_start',
         str(obj), '-o', str(elf)],
    ]
    record = {'binary': str(qemu), 'binary_sha256': sha(qemu),
              'source_sha256': sha(SOURCE), 'iterations': args.iterations,
              'compile_commands': commands, 'passed': False,
              'scope': 'AArch64 TCG indirect lookup, jump-cache conflict, 4 KiB internal page crossing and instruction invalidation; checksum regression, not OS/GUI or timing evidence.'}
    try:
        for command in commands:
            subprocess.run(command, capture_output=True, text=True, check=True)
        symbols = subprocess.check_output(['llvm-nm', '--defined-only', str(elf)], text=True)
        addresses = {name: int(address, 16) for address, _, name in
                     (line.split() for line in symbols.splitlines())}
        # This validates the intended stress input. Runtime debugger evidence
        # separately distinguishes actual cache hits from table fallbacks.
        def jump_hash(pc):
            folded = pc ^ (pc >> 6)
            return ((folded >> 6) & 0xfc0) | (folded & 0x3f)
        names = ('hot_target', 'conflict_a', 'conflict_b', 'cross_page_target')
        record['layout'] = {name: {'pc': addresses[name], 'jump_cache_hash_4k': jump_hash(addresses[name])}
                            for name in names}
        assert addresses['conflict_b'] - addresses['conflict_a'] == 0x104
        assert jump_hash(addresses['conflict_a']) == jump_hash(addresses['conflict_b'])
        assert addresses['cross_page_target'] % 4096 == 4092
        record['guest_sha256'] = sha(elf)
        command = [str(qemu), '-machine', 'virt', '-cpu', 'max', '-accel', 'tcg',
                   '-smp', '1', '-m', '128M', '-nodefaults', '-display', 'none',
                   '-monitor', 'none', '-serial', 'stdio', '-no-reboot', '-kernel', str(elf)]
        record['command'] = command
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        (out / 'guest.log').write_text(result.stdout + '\nSTDERR:\n' + result.stderr)
        record['returncode'] = result.returncode
        expected = {'hit': args.iterations, 'conflict': args.iterations * 8,
                    'cross_page': args.iterations * 18, 'invalidate': 30 + 32 + 42}
        rows = re.findall(r'^(hit|conflict|cross_page|invalidate)=([0-9a-f]{16})$', result.stdout, re.M)
        actual = {name: int(value, 16) for name, value in rows}
        record.update(expected=expected, actual=actual, binary_unchanged=sha(qemu) == record['binary_sha256'])
        assert result.returncode == 0 and len(rows) == 4 and actual == expected
        assert record['binary_unchanged']
        if args.trace_lookups:
            points = {name: addresses[name] for name in names}
            points.update(cross_page_next=addresses['cross_page_target'] + 4,
                          modified_first=0x40200ffc, modified_second=0x40201000)
            counts_path = out / 'lookup-counts.json'
            script = out / 'lookup-counts.gdb'
            script.write_text('''set pagination off
set confirm off
set debuginfod enabled off
set max-value-size unlimited
python
import gdb, json
points = ''' + repr(points) + '''
counts = {name: {'helper': 0, 'htable': 0} for name in points}
page_bits = set()
state_is_pointer = 'const TCGTBCPUState *' in gdb.execute('whatis tb_htable_lookup', to_string=True)
class Lookup(gdb.Breakpoint):
    def __init__(self, function, kind):
        super().__init__('*' + function, internal=True)
        self.kind = kind
    def stop(self):
        # Optimized debug locations lose the by-value argument in the
        # prologue. At the exact entry the verified x86-64 ABI is unambiguous.
        assert gdb.newest_frame().architecture().name() == 'i386:x86-64'
        if self.kind == 'helper':
            pc = int(gdb.parse_and_eval('((CPUARMState *)$rdi)->pc'))
        else:
            address = '$rsi' if state_is_pointer else '$rsp + 8'
            pc = int(gdb.parse_and_eval('*(uint64_t *)(' + address + ')'))
        for name, address in points.items():
            if pc == address:
                counts[name][self.kind] += 1
                page_bits.add(int(gdb.parse_and_eval('target_page.bits')))
        return False
Lookup('helper_lookup_tb_ptr', 'helper')
Lookup('tb_htable_lookup', 'htable')
end
run
python
with open(''' + repr(str(counts_path)) + ''', 'w') as stream:
    json.dump({'counts': counts, 'target_page_bits': sorted(page_bits)}, stream, indent=2)
end
''')
            debugger = ['gdb', '-q', '-batch', '-x', str(script), '--args', *command]
            record['debugger_command'] = debugger
            child = subprocess.Popen(debugger, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, start_new_session=True)
            try:
                stdout, stderr = child.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                stdout, stderr = child.communicate()
                raise RuntimeError('Host debugger timed out')
            finally:
                (out / 'lookup-counts.log').write_text(stdout + '\nSTDERR:\n' + stderr)
            assert child.returncode == 0
            traced_rows = re.findall(r'^(hit|conflict|cross_page|invalidate)=([0-9a-f]{16})$', stdout, re.M)
            assert len(traced_rows) == 4 and {name: int(value, 16) for name, value in traced_rows} == expected
            trace = json.loads(counts_path.read_text())
            assert trace['target_page_bits'] == [12]
            counts = trace['counts']
            assert counts['hot_target']['helper'] >= args.iterations - 1
            assert 1 <= counts['hot_target']['htable'] <= 2
            for name in ('conflict_a', 'conflict_b'):
                assert counts[name]['helper'] >= args.iterations - 1
                assert counts[name]['htable'] >= args.iterations
            for name in ('cross_page_target', 'cross_page_next'):
                assert counts[name]['helper'] >= args.iterations - 1
            for name in ('modified_first', 'modified_second'):
                assert counts[name]['htable'] >= 2
            record['lookup_trace'] = trace
        record['passed'] = True
        print(json.dumps({'passed': True, 'checksums': actual, 'result': str(out / 'results.json')}))
    except Exception as error:
        record['error'] = str(error)
        raise
    finally:
        obj.unlink(missing_ok=True)
        (out / 'results.json').write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    main()
