#!/usr/bin/env python3
"""Build two changed audio objects and link an isolated diagnostic candidate.

Reuses the original build's other objects/libraries; NOT a clean full rebuild.
Never invokes ninja builds or writes the original build's output/depfiles.
Coordinate CPU use with the owner of any live VM before invoking this script.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = ROOT / 'build/qemu-bdif-usb'
SOURCE = ROOT / 'sources/qemu-bdif-usb'
FILES = ('hw/audio/virtio-snd.c', 'hw/audio/virtio-snd-pci.c')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    if out == ORIGINAL or ORIGINAL in out.parents:
        parser.error('candidate output must be outside the original build')
    out.mkdir(parents=True, exist_ok=False)
    binary = ORIGINAL / 'qemu-system-aarch64'
    record = {'scope': __doc__, 'original_binary': str(binary),
              'original_binary_sha256': digest(binary), 'compile': [],
              'source_sha256': {p: digest(SOURCE / p) for p in
                  (*FILES, 'include/hw/audio/virtio-snd.h')}}
    commands = json.loads((ORIGINAL / 'compile_commands.json').read_text())
    replacements = {}
    log = (out / 'build.log').open('w')
    try:
        for relative in FILES:
            source = SOURCE / relative
            row, = [row for row in commands if
                    (Path(row['directory']) / row['file']).resolve() == source]
            old = shlex.split(row['command'])
            obj = out / (source.stem + '.o')
            command = ['nice', '-n', '15']
            index = 0
            while index < len(old):
                value = old[index]
                if value in ('-o', '-MF', '-MQ'):
                    suffix = '.d' if value == '-MF' else ''
                    command.extend((value, str(obj) + suffix))
                    index += 2
                    continue
                if value in ('-I', '-isystem', '-iquote', '-c'):
                    command.extend((value, str((ORIGINAL / old[index + 1]).resolve())))
                    index += 2
                    continue
                if value.startswith('-I') and len(value) > 2:
                    value = '-I' + str((ORIGINAL / value[2:]).resolve())
                command.append(value)
                index += 1
            record['compile'].append({'command': command, 'cwd': str(out),
                                      'original_object': row['output'],
                                      'original_object_sha256': digest(ORIGINAL / row['output'])})
            subprocess.run(command, cwd=out, stdout=log, stderr=log, check=True)
            record['compile'][-1]['object_sha256'] = digest(obj)
            replacements[row['output']] = str(obj)
        lines = (ORIGINAL / 'build.ninja').read_text().splitlines()
        index, line = next((i, line) for i, line in enumerate(lines)
                           if line.startswith('build qemu-system-aarch64: c_LINKER_RSP '))
        inputs = line.split(': c_LINKER_RSP ', 1)[1].split(' |', 1)[0].split()
        if not all(name in inputs for name in replacements):
            raise RuntimeError('changed objects are not explicit link inputs')
        link_args = lines[index + 1].removeprefix(' LINK_ARGS = ')
        if lines[index + 1] == link_args:
            raise RuntimeError('unexpected link argument layout')
        linked = out / 'qemu-system-aarch64'
        command = ['nice', '-n', '15', 'cc', '-m64', '-o', str(linked)]
        command += [replacements.get(name, str(ORIGINAL / name)) for name in inputs]
        for value in shlex.split(link_args):
            if value.endswith(('.a', '.o')) and not value.startswith('/'):
                value = str(ORIGINAL / value)
            command.append(value)
        record['link_command'] = command
        record['link_cwd'] = str(out)
        subprocess.run(command, cwd=out, stdout=log, stderr=log, check=True)
        record['candidate_binary'] = str(linked)
        record['candidate_binary_sha256'] = digest(linked)
        record['original_binary_sha256_after'] = digest(binary)
        record['original_unchanged'] = (
            record['original_binary_sha256_after'] == record['original_binary_sha256'] and
            all(digest(ORIGINAL / row['original_object']) == row['original_object_sha256']
                for row in record['compile']))
        if not record['original_unchanged']:
            raise RuntimeError('original binary/object changed during candidate build')
        record['passed'] = True
    except Exception as error:
        record.update(passed=False, error=repr(error))
        raise
    finally:
        log.close()
        (out / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'record': str(out / 'build.json'), 'passed': True,
                      'sha256': record['candidate_binary_sha256']}))


if __name__ == '__main__':
    main()
