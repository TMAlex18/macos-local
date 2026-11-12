#!/usr/bin/env python3
"""Measure how far the guest's wall clock is from the host's.

serve     An HTTP port hands the guest a short zsh script (or the same in perl,
          /clock.pl, run with `perl - TAG`). The script opens one
          TCP connection to the next port and prints its own wall-clock time a
          few times. Every line is stored with the host time of its arrival.
estimate  For every tag in such a record file: the smallest (host arrival minus
          guest stamp). That is the clock difference plus the smallest network
          delay, which is good to a few milliseconds.

Guest side, with the default port:

    curl -fsS -m 10 http://10.0.2.2:18771/clock.zsh | zsh -s TAG

Both ports listen on 127.0.0.1 only; QEMU's user network maps 10.0.2.2 to it.
"""
import argparse
import http.server
import json
import os
import re
import signal
import socketserver
import statistics
import sys
import threading
import time
from pathlib import Path

DEFAULT_PORT = 18771
SCRIPTS = {
    '/clock.zsh': '''zmodload zsh/datetime zsh/net/tcp zsh/zselect || { print "zmodload failed"; exit 1 }
tag=${1:-probe}
ztcp 10.0.2.2 @PORT@ || { print "connect failed"; exit 1 }
fd=$REPLY
for i in {1..12}; do
  print -u $fd "$tag $i $EPOCHREALTIME"
  zselect -t 25
done
ztcp -c $fd
print "clock probe $tag sent"
''',
    # The same probe for a guest whose zsh lacks one of the modules.
    '/clock.pl': '''use strict; use Time::HiRes qw(time sleep); use IO::Socket::INET;
my $tag = shift // "probe";
my $s = IO::Socket::INET->new(PeerAddr => "10.0.2.2", PeerPort => @PORT@, Proto => "tcp") or die "connect failed: $!";
$s->autoflush(1);
for my $i (1..12) { printf $s "%s %d %.6f\\n", $tag, $i, time; sleep 0.25; }
close $s;
print "clock probe $tag sent\\n";
''',
}
# zsh prints ten digits after the point; only the first nine are kept.
STAMP = re.compile(r'^([A-Za-z0-9][A-Za-z0-9_-]{0,47}) (\d{1,4}) (\d{9,11})\.(\d{1,12})$')


class Recorder:
    def __init__(self, path):
        self.stream = open(path, 'x', encoding='utf-8')
        self.lock = threading.Lock()

    def add(self, recv_ns, peer, line):
        with self.lock:
            self.stream.write(json.dumps({'recv_realtime_ns': recv_ns, 'peer': peer, 'line': line}) + '\n')
            self.stream.flush()

    def close(self):
        with self.lock:
            self.stream.close()


class LineHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(15)
        peer = '%s:%d' % self.client_address[:2]
        for _ in range(64):
            try:
                raw = self.rfile.readline(201)
            except OSError:
                return
            arrived = time.time_ns()
            if not raw or len(raw) > 200 or not raw.endswith(b'\n'):
                return
            self.server.recorder.add(arrived, peer, raw.decode('ascii', 'replace').strip())


class ScriptHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in SCRIPTS:
            self.send_error(404)
            return
        body = SCRIPTS[self.path].replace('@PORT@', str(self.server.line_port)).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class Servers:
    """The script port and the line port, serving in background threads."""

    def __init__(self, out_path, port=DEFAULT_PORT, line_port=None):
        self.recorder = Recorder(out_path)
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.lines = socketserver.ThreadingTCPServer(('127.0.0.1', port + 1 if line_port is None else line_port), LineHandler)
        self.lines.daemon_threads = True
        self.lines.recorder = self.recorder
        self.script = http.server.ThreadingHTTPServer(('127.0.0.1', port), ScriptHandler)
        self.script.daemon_threads = True
        self.script.line_port = self.lines.server_address[1]
        self.threads = [threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.2}, daemon=True)
                        for server in (self.lines, self.script)]
        for thread in self.threads:
            thread.start()

    def close(self):
        for server in (self.script, self.lines):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)
        self.recorder.close()


def estimate(rows):
    """tag -> samples, host_minus_guest_seconds (the smallest difference), median and spread."""
    by_tag = {}
    for row in rows:
        match = STAMP.match(row.get('line', ''))
        if not match:
            continue
        tag, _index, seconds, fraction = match.groups()
        guest_ns = int(seconds) * 1_000_000_000 + int(fraction[:9].ljust(9, '0'))
        by_tag.setdefault(tag, []).append((row['recv_realtime_ns'] - guest_ns) / 1e9)
    return {tag: {'samples': len(values), 'host_minus_guest_seconds': round(min(values), 6),
                  'median_seconds': round(statistics.median(values), 6),
                  'spread_seconds': round(max(values) - min(values), 6)}
            for tag, values in by_tag.items()}


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    actions = parser.add_subparsers(dest='action', required=True)
    serve = actions.add_parser('serve')
    serve.add_argument('--out', required=True, help='New record file; an existing one is refused')
    serve.add_argument('--port', type=int, default=DEFAULT_PORT, help='Script port; lines go to the next one')
    serve.add_argument('--wait-for', metavar='COMM', help='Serve only while the one process with this comm lives')
    serve.add_argument('--wait-seconds', type=float, default=120)
    actions.add_parser('estimate').add_argument('records')
    args = parser.parse_args(argv)
    if args.action == 'estimate':
        try:
            print(json.dumps(estimate(read_rows(args.records)), indent=2))
        except (OSError, ValueError) as error:
            print(f'CLOCK_OFFSET: {error}', file=sys.stderr)
            return 2
        return 0
    pid = None
    if args.wait_for:
        from host_thread_sampler import pid_of
        deadline = time.monotonic() + args.wait_seconds
        while pid is None and time.monotonic() < deadline:
            pid = pid_of(args.wait_for)
            if pid is None:
                time.sleep(0.5)
        if pid is None:
            print(f'CLOCK_OFFSET: no single process named {args.wait_for}', file=sys.stderr)
            return 2
    stopping = []
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: stopping.append(True))
    try:
        servers = Servers(args.out, args.port)
    except OSError as error:
        print(f'CLOCK_OFFSET: {error}', file=sys.stderr)
        return 2
    try:
        while not stopping and (pid is None or alive(pid)):
            time.sleep(0.5)
    finally:
        servers.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
