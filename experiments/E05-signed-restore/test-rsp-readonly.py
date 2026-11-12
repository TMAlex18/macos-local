#!/usr/bin/env python3
"""Narrow RSP command/stop contracts; no guest or network is required."""
import socket
import unittest
from rsp_readonly import (
    RSP, select_system_cpu, stop_thread, system_cpu_thread,
    system_thread_cpu_index,
)


class AckSocket:
    def __init__(self): self.sent = []
    def sendall(self, data): self.sent.append(data)
    def recv(self, size): return b'+'


class AsyncStopAckSocket(AckSocket):
    def __init__(self):
        super().__init__()
        payload = b'T02thread:01;'
        checksum = f'{sum(payload) & 255:02x}'.encode()
        self.wire = bytearray(b'$' + payload + b'#' + checksum + b'+')
    def recv(self, size):
        if self.wire:
            return bytes([self.wire.pop(0)])
        return b'+'


class FailedStepSocket(AckSocket):
    def __init__(self, wire):
        super().__init__(); self.wire = bytearray(wire); self.timeout = .25; self.closed = False
    def gettimeout(self): return self.timeout
    def settimeout(self, value): self.timeout = value
    def close(self): self.closed = True
    def recv(self, size):
        if not self.wire: raise socket.timeout('injected partial step response')
        return bytes([self.wire.pop(0)])


class RspReadonlyTests(unittest.TestCase):
    def setUp(self):
        self.rsp = RSP.__new__(RSP)
        self.rsp.sock = AckSocket()

    def test_only_positive_exact_thread_selection_and_step(self):
        for command in ('Hg01', 'Hg02', 'Hgp01.02', 'vCont?', 'vCont;s:02', 'vCont;s:p01.02'):
            self.rsp.send(command)
        self.assertEqual(len(self.rsp.sock.sent), 6)

    def test_target_description_and_register_reads_are_read_only(self):
        for command in ('p78', 'p0', 'qXfer:features:read:target.xml:0,ffb',
                        'qXfer:features:read:system-registers.xml:1f40,ffb'):
            self.rsp.send(command)
        self.assertEqual(len(self.rsp.sock.sent), 4)
        for command in ('P78=0000000000000000', 'p', 'p10000', 'qXfer:features:read:../x.xml:0,ffb',
                        'qXfer:features:write:target.xml:0:00', 'qXfer:memory-map:read::0,ffb'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.rsp.send(command)

    def test_system_register_reads_resolve_regnums_from_target_description(self):
        target = ('<target><xi:include href="aarch64-core.xml"/>'
                  '<xi:include href="system-registers.xml"/></target>')
        core = '<feature><reg name="x0" bitsize="64" regnum="0" type="int"/></feature>'
        first = '<feature><reg name="TPIDR_EL1" bitsize="64" regnum="120" type="int" group="system"/>'
        rest = '<reg name="CNTKCTL_EL1" bitsize="32" regnum="121" type="int"/></feature>'
        replies = {
            'qXfer:features:read:target.xml:0,ffb': 'l' + target,
            'qXfer:features:read:aarch64-core.xml:0,ffb': 'l' + core,
            'qXfer:features:read:system-registers.xml:0,ffb': 'm' + first,
            f'qXfer:features:read:system-registers.xml:{len(first):x},ffb': 'l' + rest,
            'p78': '8877665544332211', 'p79': '03000000',
        }

        class Scripted(RSP):
            def __init__(self): self.commands = []
            def command(self, c): self.commands.append(c); return replies[c]

        rsp = Scripted()
        self.assertEqual(rsp.read_system_register('TPIDR_EL1'), 0x1122334455667788)
        self.assertEqual(rsp.read_system_register('CNTKCTL_EL1'), 3)
        self.assertEqual(rsp.register_numbers()['TPIDR_EL1'], (120, 64))
        self.assertEqual(sum(c.startswith('qXfer') for c in rsp.commands), 4)
        with self.assertRaisesRegex(RuntimeError, 'not in the target description'):
            rsp.read_system_register('TTBR0_EL1')
        replies['p7a'] = 'E01'
        with self.assertRaises(RuntimeError):
            rsp.read_register(0x7a)
        for invalid in (-1, True, 0x10000, '1'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                rsp.read_register(invalid)

    def test_system_cpu_virtual_address_selection_uses_cpu_index_not_pause_thread(self):
        self.assertEqual(system_cpu_thread(0), '01')
        self.assertEqual(system_cpu_thread(1), '02')
        self.assertEqual(system_cpu_thread(15), '10')
        self.assertEqual(system_thread_cpu_index('01'), 0)
        self.assertEqual(system_thread_cpu_index('02'), 1)
        self.assertEqual(system_thread_cpu_index('10'), 15)

        class Selector:
            def __init__(self): self.selected = []
            def select_thread(self, thread): self.selected.append(thread)

        rsp = Selector()
        # A generic pause reports thread 01 and is not a trap stop (run -17
        # failed by parsing it as one); a mapping captured through CPU1 must
        # still select thread 02 before virtual memory reads.
        with self.assertRaises(ValueError):
            stop_thread('T02thread:01;')
        self.assertEqual(select_system_cpu(rsp, 1), '02')
        self.assertEqual(rsp.selected, ['02'])
        for invalid in (-1, True, 1 << 32, '1'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                system_cpu_thread(invalid)
        for invalid in ('00', 'p01.02', '', None, 2):
            with self.subTest(thread=invalid), self.assertRaises(ValueError):
                system_thread_cpu_index(invalid)

    def test_async_stop_packet_before_command_ack_is_drained(self):
        self.rsp.sock = AsyncStopAckSocket()
        self.rsp.send('Z1,10405885c,4')
        self.assertEqual(self.rsp.sock.sent[-1], b'+')

    def test_writes_address_resume_bare_step_and_wildcards_stay_forbidden(self):
        for command in ('Hg0', 'Hg00', 'Hg-1', 'Hg000000001', 'Hgp0.2', 'vCont;s:0', 'vCont;s:-1',
                        'vCont;s:2;c', 'vCont;s:2;C05:1', 'vCont;c', 's', 's100',
                        'c100', 'P0=01', 'G00', 'M100,1:00', 'X100,1:0', 'Hg1\ng'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.rsp.send(command)
        self.assertEqual(self.rsp.sock.sent, [])

    def test_stop_thread_is_explicit_not_guessed(self):
        self.assertEqual(stop_thread('T05thread:2;'), '2')
        self.assertEqual(stop_thread('T05thread:02;'), '02')
        self.assertEqual(stop_thread('T05hwbreak:;thread:p1.2;'), 'p1.2')
        for reply in ('S05', 'OK', 'T02thread:2;', 'T05thread:0;', 'T05thread:-1;',
                      'T05thread:1;thread:2;', 'T05thread:p0.2;', 'T05thread:2'):
            with self.subTest(reply=reply), self.assertRaises(ValueError): stop_thread(reply)

    def test_timeout_or_bad_packet_closes_connection_and_cannot_send_continue(self):
        for wire in (b'+$T05thread:', b'+$T05thread:02;#00'):
            with self.subTest(wire=wire):
                rsp = RSP.__new__(RSP); rsp.sock = FailedStepSocket(wire)
                with self.assertRaises((socket.timeout, ValueError)): rsp.step_thread('02')
                self.assertTrue(rsp.sock.closed)
                with self.assertRaises(RuntimeError): rsp.send('c')
                self.assertEqual(len(rsp.sock.sent), 1)


if __name__ == '__main__': unittest.main(verbosity=2)
