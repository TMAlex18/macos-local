#!/usr/bin/env python3
"""Fixed, default-off WebKit 3s -> 30s diagnostic publication and counters.

The uploaded probe validates guest material/ABI in its own process only. Host
publication is boolean; QEMU derives each candidate base from the actual PC and
checks the executing address space at each site. Arming is not a hit.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import uuid

from guest_ui import GuestUI, UIError, ROOT, owned_regular

PROFILE_RELATIVE = 'private/webkit-watchdog-source/actual-26A434/timeout-profile-guard.json'
PROFILE_SHA256 = '7c1347bb831793ad20eb2be756a5e9976e2e5d047f3dcc405a25642efb3dc2ab'
PATCH = 'patches/qemu-platform/0030-arm-timeout-diagnostic.patch'
RUNTIME_SLIDE_PATCH = 'patches/qemu-platform/0031-arm-timeout-runtime-slide.patch'
PROTOCOL = 'runtime-pc-slide-armed-bool-v2'
REPORT_SCHEMA = 2
PROBE_SHA256 = '8db34eefeafe51c5ee847b0149f2a15da17bbff4cc92adfd65fbacbc73d4179b'
PREFIX = 'x-webkit-timeout-'
READ_PROPERTIES = ('armed', 'status', 'site0-hits', 'site1-hits', 'rejections', 'epoch')
STATUSES = {'off', 'unarmed', 'armed-unverified', 'site0-verified', 'site1-verified',
            'candidate-unreadable-or-unsupported', 'candidate-identity-mismatch',
            'candidate-context-changed', 'candidate-invalid-range'}
SCOPE = ('Fixed two-site 30-second diagnostic only. Host publishes an armed boolean, '
         'never the probe image base. QEMU derives candidate bases from actual PC minus '
         'site offset and checks current-space identity, original instruction and '
         '64-byte context. Site counters span processes and do not attribute a Safari outcome; '
         'original 3-second deadline performance pass remains false.')


def require(condition, reason):
    if not condition:
        raise UIError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_owned(path, maximum):
    selected = owned_regular(path, maximum=maximum)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        raw = os.read(fd, maximum + 1)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    require((before.st_dev, before.st_ino) == (selected.st_dev, selected.st_ino)
            and before.st_uid == os.geteuid()
            and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            and len(raw) == after.st_size <= maximum, 'Input changed while reading')
    return raw, after


def load_profile(root=ROOT):
    path = root / PROFILE_RELATIVE
    raw, _ = read_owned(path, 65536)
    require(sha(raw) == PROFILE_SHA256, 'Fixed private timeout profile hash differs')
    profile = json.loads(raw)
    require(profile['schema_version'] == 1 and profile['default_enabled'] is False
            and profile['diagnostic_only'] is True
            and profile['original_deadline_performance_pass'] is False
            and len(profile['sites']) == 2, 'Timeout profile scope differs')
    for site in profile['sites']:
        context = site['context']
        code = bytes.fromhex(context['original_bytes_hex'])
        require(len(code) == 64 and sha(code) == context['sha256']
                and site['semantic']['original_seconds'] == 3
                and site['semantic']['diagnostic_seconds'] == 30,
                'Fixed timeout site guard differs')
    return profile


def public_configuration(enabled, profile=None):
    require(type(enabled) is bool, 'Timeout diagnostic selection must be boolean')
    return {'schema': REPORT_SCHEMA, 'publication_protocol': PROTOCOL if enabled else None,
            'requested': enabled, 'diagnostic_only': enabled,
            'profile_sha256': PROFILE_SHA256 if enabled else None,
            'state': 'configured_unarmed' if enabled else 'off',
            'runtime_site_hits_observed': False,
            'original_deadline_performance_pass': False,
            'scope': SCOPE if enabled else 'Default off; original timeout behavior unchanged.'}


def require_build(fixed_build, root=ROOT):
    profile = load_profile(root)
    record = json.loads((root / 'runs/qemu-bdif-usb-build.json').read_text())
    digests = {patch: sha((root / patch).read_bytes()) for patch in (PATCH, RUNTIME_SLIDE_PATCH)}
    require(record.get('binary_sha256') == fixed_build.get('binary_sha256')
            and all(patch in record.get('patches', [])
                    and record.get('patch_sha256', {}).get(patch) == digest
                    for patch, digest in digests.items()),
            'Timeout diagnostic needs matching 0030 and 0031 QEMU patches')
    return {'patch': PATCH, 'patch_sha256': digests[PATCH],
            'runtime_slide': {'patch': RUNTIME_SLIDE_PATCH, 'patch_sha256': digests[RUNTIME_SLIDE_PATCH]},
            **public_configuration(True, profile)}


def validate_probe(probe, profile):
    """Metadata identity in the probe only; never a cross-process activation claim."""
    require(isinstance(probe, dict), 'Probe JSON must be an object')
    def integer(key, minimum, maximum):
        value = probe.get(key)
        require(type(value) is int and minimum <= value <= maximum, 'Invalid probe integer: ' + key)
        return value
    def address(key):
        value = probe.get(key)
        require(isinstance(value, str) and re.fullmatch(r'0x[0-9a-fA-F]{1,16}', value),
                'Invalid probe address encoding: ' + key)
        return int(value, 16)
    require(probe.get('schema') == 1 and type(probe.get('schema')) is int
            and probe.get('probe_version') == 1 and type(probe.get('probe_version')) is int
            and probe.get('state') == 'verified', 'Probe did not verify current metadata')
    require(all(probe.get(key) is False for key in
                ('target_loaded_before', 'target_loaded_after', 'target_dlopen_performed')),
            'Probe loaded the target or lacks unloaded-target evidence')
    for key, expected in [('cache_uuid', profile['shared_cache_static']['main_cache_uuid']),
                          ('image_uuid', profile['image']['uuid'])]:
        value = probe.get(key)
        require(isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value)
                and value.lower() == expected.lower(), 'Probe UUID differs: ' + key)
    integer('pid', 1, (1 << 31) - 1)
    cache, image, offset = (address(key) for key in ('cache_base', 'image_base', 'image_cache_offset'))
    size = integer('cache_size', 4096, 1 << 36)
    slide = integer('image_slide', -(1 << 63), (1 << 63) - 1)
    uuid_offset = integer('lc_uuid_payload_image_offset', 0, 65536)
    static_image = profile['image']['unslid_image_base']
    static_cache = profile['shared_cache_static']['shared_region_unslid_start']
    require(offset == static_image - static_cache
            and image == cache + offset and image - slide == static_image
            and cache - slide == static_cache, 'Probe base/slide/offset equations disagree')
    require(0 < cache < image < (1 << 47) and cache + size <= (1 << 47)
            and cache % 4096 == image % 4096 == slide % 4096 == 0
            and offset + profile['image']['text_segment_size'] <= size
            and uuid_offset == profile['image']['mach_o']['lc_uuid_payload_image_offset'],
            'Probe address range/alignment/UUID offset differs')
    return {'image_base': image, 'cache_base': cache, 'cache_size': size,
            'image_slide': slide, 'image_cache_offset': offset,
            'scope': 'Verified metadata equations for this probe process only.'}


def load_probe(client, name, profile):
    require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.json', name),
            'Probe must be one JSON basename in this run guest-uploads')
    path = client.directory / 'guest-uploads' / name
    raw, info = read_owned(path, 16384)
    run = json.loads(client.result_path.read_text())
    started = datetime.fromisoformat(run['started_at_utc'].replace('Z', '+00:00')).timestamp()
    require(info.st_mtime >= started, 'Probe upload predates the selected run')
    identity = validate_probe(json.loads(raw), profile)
    return identity, {'name': name, 'sha256': sha(raw), 'raw': raw.decode(),
                      'scope': 'Current-run uploaded probe; QEMU must still guard each executing mapping.'}


class TimeoutControl(GuestUI):
    """Dedicated capability, separate from the ordinary UI/read-only allowlists."""
    def __init__(self, run):
        super().__init__(run)
        self.cpu_paths = set()
        self.transition_allowed = False
        self.allowed_armed_values = set()
        try:
            require(self.args.count('-cpu') == 1, 'Missing fixed CPU configuration')
            cpu = self.args[self.args.index('-cpu') + 1].split(',')
            require(cpu.count('x-webkit-timeout-diagnostic=' + str(ROOT / PROFILE_RELATIVE)) == 1,
                    'Selected run did not explicitly enable the fixed timeout profile')
            run = json.loads(self.result_path.read_text())
            configuration = run.get('webkit_timeout_diagnostic', {})
            require(configuration.get('requested') is True
                    and configuration.get('schema') == REPORT_SCHEMA
                    and configuration.get('publication_protocol') == PROTOCOL,
                    'Run does not request the current boolean/runtime-PC diagnostic protocol')
        except Exception:
            self.journal.close()
            raise

    def _request(self, command, arguments=None, *, record=True):
        allowed = command in ('qmp_capabilities', 'query-status', 'query-cpus-fast') and not arguments
        if command in ('stop', 'cont'):
            allowed = self.transition_allowed and not arguments
        if command == 'qom-get':
            allowed = (isinstance(arguments, dict) and set(arguments) == {'path', 'property'}
                       and arguments['path'] in self.cpu_paths
                       and arguments['property'] in {PREFIX + x for x in READ_PROPERTIES})
        if command == 'qom-set':
            allowed = (self.transition_allowed and isinstance(arguments, dict)
                       and set(arguments) == {'path', 'property', 'value'}
                       and arguments['path'] in self.cpu_paths
                       and arguments['property'] == PREFIX + 'armed'
                       and type(arguments['value']) is bool
                       and arguments['value'] in self.allowed_armed_values)
        require(allowed, 'Operation is outside the fixed timeout publication capability')
        self.verify_process()
        self.verify_peer()
        require(self.usable, 'Diagnostic transport is unavailable')
        token = uuid.uuid4().hex
        # The wire/journal contains a boolean only, never a process-specific base.
        logged = dict(arguments or {})
        self.journal.write(kind='timeout_diagnostic', phase='before_send', request=token,
                           command=command, arguments=logged)
        try:
            self.sock.sendall((json.dumps({'execute': command, 'arguments': arguments or {},
                                          'id': token}) + '\n').encode())
            while True:
                reply = self._read()
                if 'event' in reply:
                    continue
                require(reply.get('id') == token, 'Diagnostic response correlation failed')
                break
        except Exception:
            self.usable = False
            raise UIError('Diagnostic transport interrupted; no resume is authorized') from None
        accepted = 'return' in reply and 'error' not in reply
        self.journal.write(kind='timeout_diagnostic', phase='reply', request=token,
                           status='accepted' if accepted else 'rejected')
        require(accepted, 'QMP rejected the fixed diagnostic operation')
        return reply['return']


def cpu_snapshot(client):
    rows = client._request('query-cpus-fast')
    count = int(client.args[client.args.index('-smp') + 1])
    require(count in (2, 4, 8) and isinstance(rows, list) and len(rows) == count
            and all(isinstance(x, dict) and type(x.get('cpu-index')) is int for x in rows)
            and {x['cpu-index'] for x in rows} == set(range(count)), 'Unexpected vCPU set')
    paths = [x.get('qom-path') for x in rows]
    require(all(isinstance(p, str) and re.fullmatch(r'/machine/[A-Za-z0-9_./\[\]-]{1,240}', p)
                and '..' not in p.split('/') for p in paths) and len(set(paths)) == count,
            'Missing/ambiguous owned CPU paths')
    if client.cpu_paths:
        require(client.cpu_paths == set(paths), 'CPU topology changed')
    client.cpu_paths = set(paths)
    result = []
    for row in sorted(rows, key=lambda x: x['cpu-index']):
        values = {name: client._request('qom-get', {'path': row['qom-path'], 'property': PREFIX + name})
                  for name in READ_PROPERTIES}
        require(type(values['armed']) is bool and isinstance(values['status'], str)
                and values['status'] in STATUSES
                and all(type(values[k]) is int and 0 <= values[k] < 2**64
                        for k in ('site0-hits', 'site1-hits', 'rejections', 'epoch')),
                'Invalid diagnostic counter/status types')
        result.append({'cpu_index': row['cpu-index'], 'path': row['qom-path'], **values})
    return result


def public_cpus(rows):
    return [{k: v for k, v in row.items() if k != 'path'} for row in rows]


def publish_verified(before, after, armed):
    return (len(before) == len(after) and all(
        a['cpu_index'] == b['cpu_index'] and a['path'] == b['path']
        and b['armed'] is armed and b['status'] == ('armed-unverified' if armed else 'unarmed')
        and b['epoch'] == (a['epoch'] + 1) % (1 << 64)
        and all(b[k] == a[k] for k in ('site0-hits', 'site1-hits', 'rejections'))
        for a, b in zip(before, after)))


def paused(client):
    status = client._request('query-status')
    return status.get('running') is False and status.get('status') == 'paused'


def ensure_paused(client):
    status = client._request('query-status')
    if status.get('running') is True and status.get('status') == 'running':
        client._request('stop')
    deadline = time.monotonic() + 5
    while not paused(client):
        require(time.monotonic() < deadline, 'VM did not enter a paused state')
        time.sleep(.02)


def atomic_json(path, value):
    if path.exists():
        owned_regular(path, private=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def operate(client, action, identity, probe, previous, save):
    """Inspect first; publish all while paused; rollback all or never resume."""
    if previous is not None:
        require(previous.get('schema') == REPORT_SCHEMA and previous.get('publication_protocol') == PROTOCOL,
                'Legacy diagnostic receipt is not a boolean/runtime-PC receipt; left unchanged')
    current = cpu_snapshot(client)
    require(all(x['status'] != 'off' for x in current), 'Profile is off on a vCPU')
    state = dict(previous or {})
    state.update(schema=REPORT_SCHEMA, publication_protocol=PROTOCOL)
    state['latest'] = current
    if action in ('status', 'final'):
        if action == 'final':
            state['final'] = current
        state['stage'] = 'observed'
        save(state)
        return state
    require(action in ('arm', 'disarm'), 'Unknown diagnostic action')
    if action == 'arm':
        require(identity is not None and probe is not None, 'Arming requires a verified current-run probe')
        if any(x['armed'] for x in current):
            prior = state.get('published', [])
            require(len(prior) == len(current)
                    and all(a['armed'] and a['cpu_index'] == b['cpu_index']
                            and a['path'] == b['path'] and a['epoch'] == b['epoch']
                            for a, b in zip(current, prior)),
                    'Existing armed state has no matching publication receipt; no rearming performed')
            state.update(latest_probe_identity=identity, latest_probe=probe)
            state['stage'] = 'already_published'
            save(state)
            return state
    elif not any(x['armed'] for x in current):
        state['stage'] = 'already_unarmed'
        save(state)
        return state
    initial = client._request('query-status')
    require(initial.get('status') in ('running', 'paused'), 'VM state is not publishable')
    resume = initial.get('running') is True
    client.transition_allowed = True
    client.allowed_armed_values = {False, True} if action == 'arm' else {False}
    state.update(stage='pausing', resume_requested=resume, resume_confirmed=False)
    save(state)
    try:
        ensure_paused(client)
        before = cpu_snapshot(client)
        state['stage'] = 'publishing_paused'
        if action == 'arm':
            state['baseline'] = before
        else:
            state['final'] = before
        if identity:
            state.update(probe=probe, probe_identity=identity)
        save(state)  # Recovery receipt precedes any potentially effective setter.
        value = action == 'arm'
        for row in before:
            require(paused(client), 'VM left paused state during publication')
            client._request('qom-set', {'path': row['path'], 'property': PREFIX + 'armed', 'value': value})
        after = cpu_snapshot(client)
        require(publish_verified(before, after, action == 'arm') and paused(client),
                'Not all vCPUs confirmed the same complete publication')
        state.update(stage='published_unverified' if action == 'arm' else 'disarmed',
                     published=after, latest=after)
        if action == 'arm':
            state['armed_publication'] = after
        save(state)
        if resume:
            client._request('cont')
            status = client._request('query-status')
            require(status.get('running') is True and status.get('status') == 'running',
                    'Resume was not confirmed')
            state['resume_confirmed'] = True
            save(state)
        return state
    except Exception as error:
        state.update(stage='failure_left_paused_or_transport_unknown', resume_confirmed=False,
                     failure_class=type(error).__name__)
        if client.usable:
            try:
                ensure_paused(client)
                rollback_before = cpu_snapshot(client)
                for row in rollback_before:
                    client._request('qom-set', {'path': row['path'], 'property': PREFIX + 'armed', 'value': False})
                rollback_after = cpu_snapshot(client)
                state['latest'] = rollback_after
                state['rollback_verified'] = publish_verified(rollback_before, rollback_after, False)
                require(state['rollback_verified'] and paused(client), 'Rollback readback failed')
                state['stage'] = 'rolled_back_all_left_paused'
            except Exception:
                state['rollback_verified'] = False
        save(state)
        raise
    finally:
        client.transition_allowed = False
        client.allowed_armed_values = set()


def public_report(state, binding, action):
    latest = state.get('latest', [])
    baseline = state.get('baseline', [])
    deltas = []
    measured = state.get('final', latest) if action == 'disarm' else latest
    publication = state.get('armed_publication', [])
    if len(measured) == len(baseline) == len(publication):
        for a, b, p in zip(baseline, measured, publication):
            deltas.append({'cpu_index': b['cpu_index'], 'publication_epoch_unchanged':b['epoch']==p['epoch'], **{
                k: b[k] - a[k] if b['epoch'] == p['epoch'] and b[k] >= a[k] else None
                for k in ('site0-hits', 'site1-hits', 'rejections')}})
    return {'schema': REPORT_SCHEMA, 'publication_protocol': PROTOCOL,
            'observed_at_utc': datetime.now(timezone.utc).isoformat(),
            'action': action, 'binding': binding, 'state': state.get('stage', 'unknown'),
            'diagnostic_only': True, 'original_deadline_performance_pass': False,
            'profile_sha256': PROFILE_SHA256, 'probe_sha256': state.get('probe', {}).get('sha256'),
            'latest_probe_sha256': state.get('latest_probe', {}).get('sha256'),
            'probe_scope': 'Material/ABI preflight and the probe process mapping only; no base is published to QEMU.',
            'baseline': public_cpus(baseline), 'latest': public_cpus(latest),
            'publication': public_cpus(publication),
            'final': public_cpus(state.get('final', [])), 'counter_deltas': deltas,
            'runtime_site0_hits_observed': any(x['site0-hits'] > 0 for x in latest),
            'runtime_site1_hits_observed': any(x['site1-hits'] > 0 for x in latest),
            'runtime_hits_scope': 'Current counter-epoch cumulative observations across eligible mappings/processes. '
                                  'Equal cache/image UUIDs do not imply equal slides. Baseline deltas with unchanged epochs '
                                  'bound an interval, not a Safari PID or first-GPU outcome. End older Safari processes or '
                                  'record contamination; independent process evidence is still required. QOM reads are not atomic.',
            'rejections_scope': 'Eligible actual-PC-derived candidate guard rejections; not Safari failures or watchdog counts.',
            'rollback_verified': state.get('rollback_verified'),
            'failure_class': state.get('failure_class'),
            'resume_confirmed': state.get('resume_confirmed', False), 'scope': SCOPE}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--action', choices=('arm', 'status', 'final', 'disarm'), default='status')
    parser.add_argument('--probe', help='Current-run guest-uploads JSON basename; required for arm')
    args = parser.parse_args(argv)
    profile = load_profile()
    require((args.action == 'arm') == (args.probe is not None), 'Only arm requires --probe')
    with TimeoutControl(args.run) as client:
        private = ROOT / 'private/installed-boot' / args.run
        require(private.is_dir() and private.resolve() == private.absolute(), 'Owned run private directory unavailable')
        info=private.stat()
        require(info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700,
                'Run private directory ownership/mode differs')
        lock = os.open(private / 'timeout-diagnostic.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info=os.fstat(lock)
            require(stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid()
                    and stat.S_IMODE(info.st_mode)==0o600,'Diagnostic lock ownership/mode differs')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = private / 'timeout-diagnostic-state.json'
            output = client.directory / 'webkit-timeout-diagnostic.json'
            binding = {'qemu_pid': client.pid, 'start_ticks': client.start_ticks,
                       'argv_sha256': sha(b'\0'.join(os.fsencode(x) for x in client.args))}
            previous = None
            if path.exists():
                raw, _ = read_owned(path, 65536)
                previous = json.loads(raw)
                require(previous.get('binding') == binding and previous.get('profile_sha256') == PROFILE_SHA256
                        and previous.get('schema') == REPORT_SCHEMA
                        and previous.get('publication_protocol') == PROTOCOL,
                        'Stored diagnostic state belongs to another process/profile')
            if output.exists():
                existing = json.loads(read_owned(output, 65536)[0])
                require(existing.get('binding') == binding and existing.get('profile_sha256') == PROFILE_SHA256
                        and existing.get('schema') == REPORT_SCHEMA
                        and existing.get('publication_protocol') == PROTOCOL,
                        'Existing public diagnostic output belongs to another process/profile')
            identity, probe = load_probe(client, args.probe, profile) if args.probe else (None, None)
            def save(state):
                state.update(binding=binding, profile_sha256=PROFILE_SHA256,
                             schema=REPORT_SCHEMA, publication_protocol=PROTOCOL)
                atomic_json(path, state)
                atomic_json(output, public_report(state, binding, args.action))
            try:
                state = operate(client, args.action, identity, probe, previous, save)
            except (UIError, OSError, ValueError):
                print(json.dumps({'state': 'failed', 'report': str(output),
                                  'scope': 'Inspect report and VM state; automatic resume is not retried.'}))
                return 1
        finally:
            os.close(lock)
    print(json.dumps({'state': state['stage'], 'report': str(output)}))
    return 0


if __name__ == '__main__':
    try:
        code = main()
    except (UIError, ValueError, OSError) as error:
        print(json.dumps({'state':'failed', 'reason_class':type(error).__name__, 'reason':str(error),
                          'scope':'Inspect existing private/public state and live properties before retry; no automatic resume.'}))
        code = 2
    raise SystemExit(code)
