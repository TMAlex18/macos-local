#!/usr/bin/env python3
"""Deterministic host wire checks; no Apple VM or synthesized live response."""
import errno
import socket
import struct
import sys
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'E05-bdif-usb'))
from virtio_usb_control import (KernelVirtioUSBControl,MAX_REQUEST_BODY,
                                MAX_RESPONSE_BODY)


def frame(body):return struct.pack('<I',len(body))+body


class Wire:
    def __init__(self,*bodies,error=None):
        self.data=bytearray(b''.join(frame(body) for body in bodies))
        self.sent=[];self.error=error
    def settimeout(self,value):self.timeout=value
    def sendall(self,data):self.sent.append(data)
    def recv(self,count):
        if self.error:raise self.error
        # Exercise partial length and body reads, not just whole messages.
        count=min(count,2,len(self.data));result=bytes(self.data[:count]);del self.data[:count]
        return result


class Checks(unittest.TestCase):
    def setup_header(self,direction=1,length=18):
        return struct.pack('<BBHHH',0x80 if direction else 0x00,6,0x100,0,length)

    def test_reset_uses_the_observed_request_and_exact_value_reply(self):
        wire=Wire(b'\x04\x10');client=KernelVirtioUSBControl(wire)
        reply=client.reset_bus()
        self.assertEqual((reply.status,reply.terminal),(0,False))
        self.assertEqual(wire.sent,[frame(struct.pack('<IBB',0,0,2))])
        self.assertEqual(client.last_wire,['0410'])

    def test_reset_does_not_swallow_an_unknown_event(self):
        wire=Wire(b'\x04\x00',b'\x04\x10');client=KernelVirtioUSBControl(wire)
        reply=client.reset_bus()
        self.assertEqual((reply.status,reply.terminal),(-errno.EPROTO,True))
        self.assertEqual(client.last_wire,['0400'])
        client.reset_bus()
        self.assertEqual(len(wire.sent),1)

    def test_ep0_in_preserves_short_payload_and_endpoint_zero(self):
        wire=Wire(b'\x01\x00abc');client=KernelVirtioUSBControl(wire)
        setup=self.setup_header()
        reply=client.control(setup,1,b'',18)
        self.assertEqual((reply.status,reply.payload,reply.actual_length),(0,b'abc',3))
        self.assertEqual(wire.sent,[frame(struct.pack('<IBB',8,0,1)+setup)])

    def test_ep0_in_zero_payload_is_a_valid_short_packet_zlp(self):
        wire=Wire(b'\x01\x00');client=KernelVirtioUSBControl(wire)
        reply=client.control(self.setup_header(),1,b'',18)
        self.assertEqual((reply.status,reply.payload,reply.actual_length,reply.terminal),
                         (0,b'',0,False))
        self.assertEqual(wire.sent,[frame(struct.pack('<IBB',8,0,1)+self.setup_header())])

    def test_ep0_out_setup_and_data_are_one_request(self):
        wire=Wire(b'\x01\x00');client=KernelVirtioUSBControl(wire)
        setup=self.setup_header(0,3)
        reply=client.control(setup,0,b'xyz',3)
        self.assertEqual((reply.status,reply.actual_length),(0,3))
        self.assertEqual(wire.sent,[frame(struct.pack('<IBB',11,0,1)+setup+b'xyz')])

    def test_ep0_out_zero_length_completion_is_valid(self):
        wire=Wire(b'\x01\x00');client=KernelVirtioUSBControl(wire)
        setup=self.setup_header(0,0)
        reply=client.control(setup,0,b'',0)
        self.assertEqual((reply.status,reply.payload,reply.actual_length,reply.terminal),
                         (0,b'',0,False))
        self.assertEqual(wire.sent,[frame(struct.pack('<IBB',8,0,1)+setup)])

    def test_ep0_stall_is_explicit_and_nonterminal(self):
        wire=Wire(b'\x02\x00',b'\x01\x00x');client=KernelVirtioUSBControl(wire)
        reply=client.control(self.setup_header(),1,b'',18)
        self.assertEqual((reply.status,reply.terminal),(-errno.EPIPE,False))
        self.assertEqual(client.control(self.setup_header(),1,b'',18).payload,b'x')

    def test_unknown_or_wrong_endpoint_and_overlong_reply_are_terminal(self):
        for body in (b'\x01\x80abc',b'\x03\x00',b'\x04\x10',b'\x05\x00',
                     b'\x01\x00'+b'x'*19):
            with self.subTest(body=body):
                client=KernelVirtioUSBControl(Wire(body))
                reply=client.control(self.setup_header(),1,b'',18)
                self.assertEqual((reply.status,reply.terminal),(-errno.EPROTO,True))

    def test_request_body_bound_is_checked_before_send(self):
        client=KernelVirtioUSBControl(Wire())
        with self.assertRaises(Exception):
            client.packet(b'x'*(MAX_REQUEST_BODY+1))
        self.assertEqual(client.sock.sent,[])

    def test_local_setup_mismatch_does_not_poison_a_live_stream(self):
        wire=Wire(b'\x01\x00ok');client=KernelVirtioUSBControl(wire)
        with self.assertRaises(Exception):
            client.control(self.setup_header(),0,b'',18)
        self.assertTrue(client.usable)
        reply=client.control(self.setup_header(),1,b'',18)
        self.assertEqual((reply.status,reply.payload),(0,b'ok'))

    def test_timeout_cannot_be_reused_for_a_late_reply(self):
        wire=Wire(error=socket.timeout());client=KernelVirtioUSBControl(wire)
        reply=client.control(self.setup_header(),1,b'',18)
        self.assertEqual((reply.status,reply.terminal),(-errno.ETIMEDOUT,True))
        wire.error=None;wire.data.extend(frame(b'\x01\x00x'))
        self.assertTrue(client.control(self.setup_header(),1,b'',18).terminal)
        self.assertEqual(len(wire.sent),1)

    def test_disconnected_stream_is_terminal_but_a_new_connection_can_start(self):
        wire=Wire();client=KernelVirtioUSBControl(wire)
        reply=client.control(self.setup_header(),1,b'',18)
        self.assertEqual((reply.status,reply.terminal),(-errno.EPROTO,True))
        replacement=KernelVirtioUSBControl(Wire(b'\x01\x00ok'))
        reply=replacement.control(self.setup_header(),1,b'',18)
        self.assertEqual((reply.status,reply.payload,reply.terminal),(0,b'ok',False))

    def test_host_response_bound_is_checked_before_payload_read(self):
        wire=Wire();wire.data.extend(struct.pack('<I',MAX_RESPONSE_BODY+1))
        client=KernelVirtioUSBControl(wire)
        self.assertEqual(client.control(self.setup_header(),1,b'',18).status,-errno.EPROTO)


if __name__=='__main__':unittest.main(verbosity=2)
