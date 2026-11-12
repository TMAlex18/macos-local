#!/usr/bin/env python3
"""Owner-QMP parsing and post-exit draining with local sockets; no VM."""
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from firmware_session import (FirmwareSession,has_guest_shutdown_evidence,LIFECYCLE_EVENT_LIMIT,
                              GUEST_SHUTDOWN_WAIT_SECONDS)


def event(name='SHUTDOWN',guest=True,reason='guest-shutdown'):
    return {'event':name,'data':{'guest':guest,'reason':reason,'ignored_payload':'not retained'},
            'timestamp':{'seconds':123,'microseconds':456}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.fw=FirmwareSession(Path(self.temp.name))
        self.peer,self.fw.qmp=socket.socketpair()
        self.fw.qmp.settimeout(2)
        self.fw.qf=self.fw.qmp.makefile('rb')
        self.proc=SimpleNamespace(pid=1234,returncode=0,poll=lambda:self.proc.returncode)
        self.fw.proc=self.proc
    def tearDown(self):
        if isinstance(self.proc,subprocess.Popen):
            if self.proc.poll() is None:self.proc.kill();self.proc.wait(timeout=2)
            if self.proc.stdin:self.proc.stdin.close()
        else:self.proc.returncode=0
        self.peer.close()
        self.fw.close()
        self.temp.cleanup()
    def send(self,*rows):
        self.peer.sendall(b''.join(json.dumps(row).encode()+b'\n' for row in rows))
    def record(self):
        return {'qemu_exit_code':self.proc.returncode,'qemu_pid':self.proc.pid,'qemu_reaped':True,
                'teardown':self.fw._teardown,'qmp_lifecycle':self.fw.lifecycle_metadata()}
    def test_buffered_tail_after_response_persists_without_sending(self):
        self.send(event('POWERDOWN',False,'host-qmp-system-powerdown'),{'return':{}},event())
        self.peer.shutdown(socket.SHUT_WR)
        self.fw.request('query-status')
        sent=self.peer.recv(4096)
        self.assertEqual(json.loads(sent)['execute'],'query-status')
        self.fw.close()
        self.assertEqual(self.peer.recv(4096),b'')
        saved=json.loads((Path(self.temp.name)/'qmp-lifecycle.json').read_text())
        self.assertTrue(saved['guest_shutdown_observed'])
        self.assertEqual(saved['tail_drain']['state'],'eof')
        self.assertEqual(saved['events'][-1]['receive_phase'],'post_exit_drain')
        self.assertNotIn('ignored_payload',saved['events'][-1]['data'])
        self.assertTrue(has_guest_shutdown_evidence(self.record()))
    def test_host_quit_or_unqualified_exits_never_mean_guest_shutdown(self):
        self.fw._teardown='already_exited_or_not_started'
        for row in (event(guest=False,reason='host-qmp-quit'),event(guest=True,reason='host-qmp-quit'),
                    event(guest=False),event('RESET')):
            self.fw._record_qmp_event(row,'request')
            self.assertFalse(has_guest_shutdown_evidence(self.record()))
        self.fw._record_qmp_event(event(),'request')
        self.assertTrue(has_guest_shutdown_evidence(self.record()))
        for change in ({'qemu_exit_code':-6},{'qemu_reaped':False},{'qemu_pid':999},
                       {'teardown':'qmp_quit'},{'qmp_lifecycle':None}):
            self.assertFalse(has_guest_shutdown_evidence({**self.record(),**change}))
        self.fw.host_quit_requested=True
        self.assertFalse(has_guest_shutdown_evidence(self.record()))
        self.fw.host_quit_requested=False
        self.fw._record_qmp_event(event('RESET'),'request')
        self.assertFalse(has_guest_shutdown_evidence(self.record()))
    def test_live_process_drain_does_no_io(self):
        self.proc.returncode=None
        with patch.object(self.fw,'request',side_effect=AssertionError('must not send')):
            self.fw._drain_qmp_tail()
        self.assertEqual(self.fw.qmp_tail_drain['state'],'skipped_process_not_reaped')
    def test_shutdown_event_then_disconnect_waits_for_owned_child(self):
        # The socket has closed but the actual owned child is still alive,
        # reproducing the ordering that sent a host quit during Run124.
        child=subprocess.Popen([sys.executable,'-c','import sys; sys.stdin.buffer.read(1)'],
                               stdin=subprocess.PIPE)
        self.proc=self.fw.proc=child
        self.send(event())
        self.peer.shutdown(socket.SHUT_WR)
        with self.assertRaises(EOFError):self.fw.request('query-status')
        self.assertIsNone(child.poll())
        def finish_child():
            try:child.stdin.write(b'x');child.stdin.flush()
            except (BrokenPipeError,OSError):pass
        release=threading.Timer(.05,finish_child)
        release.start()
        try:
            self.fw.close()
        finally:
            release.join(timeout=1)
            if child.poll() is None:child.kill();child.wait(timeout=2)
            child.stdin.close()
        self.assertEqual(child.returncode,0)
        self.assertFalse(self.fw.host_quit_requested)
        self.assertEqual(self.fw.guest_shutdown_wait['state'],'exited')
        self.assertEqual(self.fw.guest_shutdown_wait['max_seconds'],GUEST_SHUTDOWN_WAIT_SECONDS)
        self.assertTrue(has_guest_shutdown_evidence(self.record()))
        sent=self.peer.recv(4096).splitlines()
        self.assertEqual([json.loads(row)['execute'] for row in sent],['query-status'])
    def owned_child(self):
        child=subprocess.Popen([sys.executable,'-c','import sys; sys.stdin.buffer.read(1)'],
                               stdin=subprocess.PIPE)
        self.proc=self.fw.proc=child
        return child
    def test_closed_peer_send_failure_still_finds_the_guest_shutdown(self):
        # QEMU closed QMP entirely while the owned child was still exiting, so
        # the next periodic request failed on send with the SHUTDOWN event
        # unread. Runs 133, 135 and 137 were recorded as host teardowns this way.
        child=self.owned_child()
        self.send(event())
        self.peer.close()
        with self.assertRaises(BrokenPipeError):self.fw.request('query-blockstats')
        self.assertEqual(self.fw.last_shutdown_or_reset_event['receive_phase'],'send_failure_drain')
        self.assertEqual(self.fw.qmp_send_failure_drain['state'],'eof')
        self.assertEqual(self.fw.qmp_send_failure_drain['command'],'query-blockstats')
        self.assertEqual(self.fw.qmp_send_failure_drain['commands_sent'],0)
        self.assertIsNone(child.poll())
        def finish_child():
            try:child.stdin.write(b'x');child.stdin.flush()
            except (BrokenPipeError,OSError):pass
        release=threading.Timer(.05,finish_child)
        release.start()
        try:
            # The controller asks first; close() must not wait or quit again.
            self.assertTrue(self.fw.wait_for_guest_shutdown_exit())
            with self.assertRaises(BrokenPipeError):self.fw.request('query-status')
            self.fw.close()
        finally:
            release.join(timeout=1)
            if child.poll() is None:child.kill();child.wait(timeout=2)
            child.stdin.close()
        self.assertEqual(child.returncode,0)
        self.assertFalse(self.fw.host_quit_requested)
        self.assertEqual(self.fw.guest_shutdown_wait['state'],'exited')
        self.assertEqual(self.fw._teardown,'already_exited_or_not_started')
        self.assertEqual(self.fw.lifecycle_event_count,1)
        self.assertTrue(has_guest_shutdown_evidence(self.record()))
        saved=json.loads((Path(self.temp.name)/'qmp-lifecycle.json').read_text())
        self.assertTrue(saved['guest_shutdown_observed'])
        self.assertEqual(saved['send_failure_drain']['state'],'eof')
        self.assertEqual(saved['events'][-1]['receive_phase'],'send_failure_drain')
    def test_closed_peer_without_guest_shutdown_is_still_a_host_teardown(self):
        child=self.owned_child()
        self.send(event(guest=False,reason='host-signal'))
        self.peer.close()
        with self.assertRaises(BrokenPipeError):self.fw.request('query-status')
        self.assertEqual(self.fw.last_shutdown_or_reset_event['receive_phase'],'send_failure_drain')
        self.assertFalse(self.fw.wait_for_guest_shutdown_exit())
        self.assertEqual(self.fw.guest_shutdown_wait['state'],'not_attempted')
        self.assertIsNone(child.poll())
        self.fw.close()
        self.assertTrue(self.fw.host_quit_requested)
        self.assertEqual(self.fw._teardown,'terminate')
        self.assertEqual(child.returncode,-signal.SIGTERM)
        self.assertFalse(has_guest_shutdown_evidence(self.record()))
    def test_guest_shutdown_wait_happens_at_most_once(self):
        waits=[]
        def wait(timeout):
            waits.append(timeout);raise subprocess.TimeoutExpired('owned child',timeout)
        self.proc.returncode=None
        self.proc.wait=wait
        self.assertFalse(self.fw.wait_for_guest_shutdown_exit())
        self.assertEqual(waits,[])
        self.fw._record_qmp_event(event(),'request')
        self.assertFalse(self.fw.wait_for_guest_shutdown_exit())
        self.assertFalse(self.fw.wait_for_guest_shutdown_exit())
        self.assertEqual(waits,[GUEST_SHUTDOWN_WAIT_SECONDS])
        self.assertEqual(self.fw.guest_shutdown_wait['state'],'timed_out')
        self.proc.returncode=0
        self.assertTrue(self.fw.wait_for_guest_shutdown_exit())
        self.assertNotIn('send_failure_drain',self.fw.lifecycle_metadata())
    def test_guest_wait_timeout_falls_back_to_host_teardown(self):
        waits=[]
        def wait(timeout):
            waits.append(timeout)
            if len(waits)==1:raise subprocess.TimeoutExpired('owned child',timeout)
            self.proc.returncode=0
            return 0
        self.proc.returncode=None
        self.proc.wait=wait
        self.fw._record_qmp_event(event(),'request')
        self.peer.shutdown(socket.SHUT_WR)
        with patch.object(self.fw,'request',return_value={}) as request:
            self.fw.close()
        self.assertEqual(waits,[GUEST_SHUTDOWN_WAIT_SECONDS,4])
        request.assert_called_once_with('quit')
        self.assertEqual(self.fw.guest_shutdown_wait['state'],'timed_out')
        self.assertEqual(self.fw._teardown,'qmp_quit')
        self.assertTrue(self.fw.host_quit_requested)
        self.assertFalse(has_guest_shutdown_evidence(self.record()))
    def test_reset_or_host_event_does_not_grant_guest_shutdown_wait(self):
        for row in (None,event('RESET'),event(guest=False),event(reason='host-qmp-quit')):
            with self.subTest(row=row):
                self.fw._closed=False
                self.fw.host_quit_requested=False
                self.fw.last_shutdown_or_reset_event=None
                self.proc.returncode=None
                waits=[]
                def wait(timeout):
                    waits.append(timeout);self.proc.returncode=0;return 0
                self.proc.wait=wait
                self.fw._record_qmp_event(event(),'request')
                if row is None:self.fw.last_shutdown_or_reset_event=None
                else:self.fw._record_qmp_event(row,'request')
                # These controls need no transport I/O; the guest case above
                # independently exercises the real socket and real child.
                with patch.object(self.fw,'request',return_value={}), \
                     patch.object(self.fw,'_drain_qmp_tail'):
                    self.fw.close()
                self.assertEqual(waits,[4])
                self.assertEqual(self.fw.guest_shutdown_wait['state'],'not_attempted')
                self.assertFalse(has_guest_shutdown_evidence(self.record()))
    def test_idle_peer_is_bounded_and_read_error_preserves_cleanup(self):
        with patch('firmware_session.QMP_TAIL_SECONDS',.02):
            started=time.monotonic();self.fw.close()
        self.assertLess(time.monotonic()-started,.2)
        self.assertEqual(self.fw.qmp_tail_drain['state'],'read_error')
        self.assertTrue(self.fw._closed)
        self.assertFalse(self.fw.lifecycle_metadata()['guest_shutdown_observed'])
    def test_oversized_partial_frame_is_not_parsed_or_retained(self):
        self.peer.sendall(b'x'*20000)
        self.peer.shutdown(socket.SHUT_WR)
        self.fw.close()
        self.assertEqual(self.fw.qmp_tail_drain['state'],'incomplete_or_oversized_frame')
        self.assertLessEqual(self.fw.qmp_tail_drain['bytes'],16385)
        self.assertEqual(self.fw.lifecycle_event_count,0)
    def test_malformed_tail_stops_without_guess(self):
        self.peer.sendall(b'{bad json}\n'+json.dumps(event()).encode()+b'\n')
        self.peer.shutdown(socket.SHUT_WR)
        self.fw.close()
        self.assertEqual(self.fw.qmp_tail_drain['state'],'read_error')
        self.assertFalse(self.fw.lifecycle_metadata()['guest_shutdown_observed'])
    def test_lifecycle_retention_is_bounded_and_ignores_other_payloads(self):
        for _ in range(LIFECYCLE_EVENT_LIMIT+2):self.fw._record_qmp_event(event('RESET'),'request')
        self.fw._record_qmp_event(event(),'request')
        self.fw._record_qmp_event({'event':'STOP','data':{'unused':'not persisted'}},'request')
        self.assertEqual(len(self.fw.lifecycle_events),LIFECYCLE_EVENT_LIMIT)
        self.assertEqual(self.fw.lifecycle_metadata()['dropped_event_count'],3)
        self.assertEqual(self.fw.last_shutdown_or_reset_event['event'],'SHUTDOWN')


if __name__=='__main__':unittest.main(verbosity=2)
