#!/usr/bin/env python3
"""Time Safari's first GPU-process connection from a gpuwatch archive.

Input is the archive that guest-diag/safari-probe.sh uploads (TAG.tgz with
TAG/webkit-stream.log), or that log as a plain file. For the first WebKit GPU
process owned by the UI process (Safari by default) the report gives:

  ui_request_to_did_initialize   the UI process logs that a web process is
                                 requesting a GPU connection, until it logs
                                 GPUProcessConnection::didInitialize
  gpu_entry_to_did_initialize    the GPU process logs
                                 createGPUConnectionToWebProcess, until the
                                 same end

and, when the connection never initialises, the time until the UI process
reports the GPU process as unresponsive. WebKit's own limit for this step is
three seconds, so these are the numbers that decide a first-load trial.

Timestamps are guest-local log times and only differences are used. The log is
not strictly in time order, so events are matched by time, never by line order.
"""
import argparse
import hashlib
import json
import re
import sys
import tarfile
from datetime import datetime
from pathlib import Path

MAX_LOG_BYTES = 64 * 1024 * 1024
LINE = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3,6}) +(\S+) +(.+?)\[(\d+):[0-9a-fA-F]+\] (.*)$')
ASSERTION = re.compile(r"Successfully took RBS assertion 'GPUProcess \w+ Assertion' for process with PID=(\d+)")
DID_INITIALIZE = re.compile(r'WebContent\[(\d+)\]: GPUProcessConnection::didInitialize\((\d+)\)')
GPU_EXIT = re.compile(r'GPUProcessProxy::gpuProcessExited: reason=(\w+)')
# kind -> (substring that identifies the line, True when the GPU process itself logs it)
MARKS = {
    'gpu_constructor': ('GPUProcess::GPUProcess:', True),
    'gpu_initialize': ('GPUProcess::initializeGPUProcess:', True),
    'gpu_connection': ('GPUProcess::createGPUConnectionToWebProcess:', True),
    'registrar': ('name=com.apple.audio.AudioComponentRegistrar', True),
    'helper_create': ('Creating SandboxHelper connection', True),
    'helper_destroy': ('Destroying SandboxHelper connection', True),
    'proxy_launched': ('GPUProcessProxy::didFinishLaunching:', False),
    'ui_request': ('GPUProcessProxy is taking a background assertion because a web process is requesting a connection', False),
    'web_unresponsive': ('WebProcessProxy::didBecomeUnresponsive:', False),
}


class TimingError(ValueError):
    pass


def read_log(path):
    """Return (text, sha256 of the input file, member name or None)."""
    path = Path(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if path.suffix != '.tgz':
        if len(data) > MAX_LOG_BYTES:
            raise TimingError('Log is larger than the 64 MiB bound')
        return data.decode('utf-8', 'replace'), digest, None
    with tarfile.open(path, 'r:gz') as archive:
        members = [m for m in archive.getmembers() if m.isfile() and m.name.endswith('/webkit-stream.log')]
        if len(members) != 1:
            raise TimingError(f'Expected one webkit-stream.log in the archive, found {len(members)}')
        if members[0].size > MAX_LOG_BYTES:
            raise TimingError('webkit-stream.log is larger than the 64 MiB bound')
        return archive.extractfile(members[0]).read().decode('utf-8', 'replace'), digest, members[0].name


def parse_events(text):
    """Classify the log lines this analysis uses; return (events, line statistics)."""
    events, parsed = [], 0
    lines = text.split('\n')
    for number, line in enumerate(lines, 1):
        match = LINE.match(line)
        if not match:
            continue
        parsed += 1
        stamp, _level, process, pid, message = match.groups()
        base = {'time': datetime.fromisoformat(stamp), 'line': number, 'process': process.strip(), 'pid': int(pid)}
        is_gpu = base['process'].endswith('WebKit.GPU')
        for kind, (needle, from_gpu) in MARKS.items():
            if needle in message and from_gpu == is_gpu:
                events.append({**base, 'kind': kind})
        found = ASSERTION.search(message)
        if found:
            events.append({**base, 'kind': 'gpu_assertion', 'gpu_pid': int(found.group(1))})
        found = DID_INITIALIZE.search(message)
        if found:
            events.append({**base, 'kind': 'did_initialize', 'web_pid': int(found.group(1)),
                           'connection': int(found.group(2))})
        found = GPU_EXIT.search(message)
        if found:
            events.append({**base, 'kind': 'gpu_exit', 'reason': found.group(1)})
    events.sort(key=lambda event: (event['time'], event['line']))
    return events, {'lines': len(lines), 'timestamped_lines': parsed}


def seconds(start, end):
    return None if start is None or end is None else round((end['time'] - start['time']).total_seconds(), 3)


def public(event):
    if event is None:
        return None
    return {key: (value.isoformat(sep=' ', timespec='milliseconds') if key == 'time' else value)
            for key, value in event.items() if key != 'kind'}


def analyse(text, ui_process='Safari'):
    events, statistics = parse_events(text)

    def pick(kind, **conditions):
        return [e for e in events if e['kind'] == kind and all(e.get(k) == v for k, v in conditions.items())]

    constructors = pick('gpu_constructor')
    owners = {}
    for event in pick('gpu_assertion'):
        owners.setdefault(event['gpu_pid'], {'process': event['process'], 'pid': event['pid']})
    report = {
        'schema': 1, 'ui_process': ui_process, **statistics,
        'gpu_processes_constructed': len(constructors),
        'gpu_unresponsive_exits_reported_by_ui_process': len(
            [e for e in pick('gpu_exit', process=ui_process) if e['reason'] == 'Unresponsive']),
        'gpu_unresponsive_exits_all_clients': len([e for e in pick('gpu_exit') if e['reason'] == 'Unresponsive']),
        'web_process_unresponsive_notifications': len(pick('web_unresponsive', process=ui_process)),
        'first_gpu': None,
    }
    owned = [c for c in constructors if owners.get(c['pid'], {}).get('process') == ui_process]
    if not owned:
        report['outcome'] = 'no_gpu_process_owned_by_ui_process'
        return report
    gpu = owned[0]
    # Only a later GPU process of the same owner ends this one's events. Another
    # client may start its own GPU process in the middle of them (Run142).
    later = [c for c in owned if c['time'] > gpu['time']]
    end = later[0]['time'] if later else None

    def within(candidates, after=None):
        start = (after or gpu)['time']
        return [e for e in candidates if e['time'] >= start and (end is None or e['time'] < end)]

    def first(candidates, after=None):
        found = within(candidates, after)
        return found[0] if found else None

    request = first(pick('ui_request', process=ui_process))
    entry = first(pick('gpu_connection', pid=gpu['pid']))
    start = min((e for e in (request, entry) if e), key=lambda e: e['time'], default=None)
    initialized = first(pick('did_initialize', process=ui_process), start) if start else None
    exits = [e for e in pick('gpu_exit', process=ui_process) if e['reason'] == 'Unresponsive']
    # The exit report is logged after the next GPU process may already exist; do not bound it by `end`.
    unresponsive = next((e for e in exits if e['time'] >= gpu['time']), None)
    if initialized and unresponsive and unresponsive['time'] < initialized['time']:
        initialized = None
    registrar = first(pick('registrar', pid=gpu['pid']), entry) if entry else None
    helper_create = first(pick('helper_create', pid=gpu['pid']), entry) if entry else None
    helper_destroy = first(pick('helper_destroy', pid=gpu['pid']), helper_create) if helper_create else None
    launched = first(pick('proxy_launched', process=ui_process))
    report['first_gpu'] = {
        'gpu_pid': gpu['pid'], 'owner': owners[gpu['pid']],
        'events': {name: public(event) for name, event in (
            ('constructor', gpu), ('proxy_launched', launched), ('ui_request', request), ('gpu_entry', entry),
            ('registrar_activation', registrar), ('helper_create', helper_create),
            ('helper_destroy', helper_destroy), ('did_initialize', initialized),
            ('unresponsive_exit', unresponsive if not initialized else None))},
        'seconds': {
            'constructor_to_proxy_launched': seconds(gpu, launched),
            'ui_request_to_did_initialize': seconds(request, initialized),
            'gpu_entry_to_did_initialize': seconds(entry, initialized),
            'gpu_entry_to_registrar_activation': seconds(entry, registrar) if initialized else None,
            'registrar_activation_to_helper_create': seconds(registrar, helper_create) if initialized else None,
            'helper_create_to_helper_destroy': seconds(helper_create, helper_destroy) if initialized else None,
            'helper_destroy_to_did_initialize': seconds(helper_destroy, initialized),
            'ui_request_to_unresponsive_exit': None if initialized else seconds(request, unresponsive),
            'gpu_entry_to_unresponsive_exit': None if initialized else seconds(entry, unresponsive),
        },
    }
    if initialized:
        report['outcome'] = 'initialized'
    elif unresponsive:
        report['outcome'] = 'terminated_unresponsive'
    elif start:
        report['outcome'] = 'connection_requested_no_result_in_log'
    else:
        report['outcome'] = 'no_connection_requested'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('archive', help='TAG.tgz from safari-probe.sh, or a webkit-stream.log file')
    parser.add_argument('--ui-process', default='Safari', help='Process name of the WebKit UI process (default Safari)')
    parser.add_argument('--out', help='Also write the JSON report to this new file; an existing file is refused')
    args = parser.parse_args(argv)
    try:
        text, digest, member = read_log(args.archive)
        report = {'input': str(args.archive), 'input_sha256': digest, 'archive_member': member,
                  **analyse(text, args.ui_process)}
        rendered = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
        if args.out:
            with open(args.out, 'x', encoding='utf-8') as output:
                output.write(rendered)
    except (OSError, TimingError, tarfile.TarError) as error:
        print(f'GPU_INIT_TIMING: {error}', file=sys.stderr)
        return 2
    sys.stdout.write(rendered)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
