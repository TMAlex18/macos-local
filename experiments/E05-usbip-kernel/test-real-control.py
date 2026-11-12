#!/usr/bin/env python3
"""Protocol-unit negative cases, independent of guest boot/USB hardware.

These model wire inputs to the adapter; they do not attest Apple semantics.
"""
import errno,json,socket,struct,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments/E05-bdif-usb'))
from real_control import RealFirmwareControl
from usbip_control import ProtocolError
class FakeWire(RealFirmwareControl):
    def __init__(self,response):self.response=response;self.sent=[]
    def packet(self,data):self.sent.append(data)
    def receive(self):
        if isinstance(self.response,Exception):raise self.response
        self.last_wire.append(self.response.hex());return self.response
class Checks(unittest.TestCase):
    def send(self,reply,requested=6,direction=1,payload=b''):
        x=FakeWire(reply);setup=struct.pack('<BBHHH',0xa1 if direction else 0x21,3,0,0,requested)
        return x,x.control(setup,direction,payload,requested)
    def test_supported_data_preserved(self):
        _,r=self.send(b'\x01\0\0\x32\0\0\x02\0');self.assertEqual((r.status,r.payload,r.actual_length,r.terminal),(0,b'\0\x32\0\0\x02\0',6,False))
    def test_empty_reply_not_fabricated(self):
        _,r=self.send(b'\x01\0');self.assertEqual((r.status,r.actual_length,r.payload),(0,0,b''))
    def test_short_reply_not_padded(self):
        _,r=self.send(b'\x01\0\x02');self.assertEqual((r.actual_length,r.payload),(1,b'\x02'))
    def test_explicit_stall_nonterminal(self):
        _,r=self.send(b'\x02\0');self.assertEqual((r.status,r.terminal),(-errno.EPIPE,False))
    def test_unknown_frame_is_terminal(self):
        _,r=self.send(b'\x99\0');self.assertEqual((r.status,r.terminal),(-errno.EPROTO,True))
    def test_wrong_endpoint_is_not_stall(self):
        _,r=self.send(b'\x01\x01');self.assertEqual((r.status,r.terminal),(-errno.EPROTO,True))
    def test_overlong_reply_is_terminal(self):
        _,r=self.send(b'\x01\0'+b'x'*7);self.assertEqual((r.status,r.terminal),(-errno.EPROTO,True))
    def test_empty_frame_is_terminal(self):
        _,r=self.send(b'');self.assertEqual((r.status,r.terminal),(-errno.EPROTO,True))
    def test_timeout_is_not_success(self):
        _,r=self.send(socket.timeout());self.assertEqual((r.status,r.terminal),(-errno.ETIMEDOUT,True))
    def test_out_ack_actual_length(self):
        x,r=self.send(b'\x01\0',requested=16,direction=0,payload=b'x'*16)
        self.assertEqual((r.status,r.actual_length,r.payload),(0,16,b''));self.assertEqual(len(x.sent),1)
        self.assertEqual(x.sent[0][:6],struct.pack('<iBB',24,0,1));self.assertEqual(x.sent[0][14:],b'x'*16)
    def test_control_out_type5_is_not_success(self):
        _,r=self.send(b'\x05\0',requested=16,direction=0,payload=b'x'*16)
        self.assertEqual((r.status,r.terminal),(-errno.EPROTO,True))
    def test_real_rom_out_limit_2048(self):
        x,r=self.send(b'\x01\0',requested=2048,direction=0,payload=b'z'*2048)
        self.assertEqual((r.status,r.actual_length),(0,2048))
        self.assertEqual(len(x.sent),1);self.assertEqual(len(x.sent[0]),2062)
    def test_oversized_control_out_not_sent(self):
        x=FakeWire(b'\x01\0')
        setup=struct.pack('<BBHHH',0x21,1,0,0,2049)
        with self.assertRaises(ProtocolError):x.control(setup,0,b'z'*2049,2049)
        self.assertEqual(x.sent,[])
    def test_out_zero_length(self):
        x,r=self.send(b'\x01\0',requested=0,direction=0);self.assertEqual((r.status,r.actual_length),(0,0));self.assertEqual(len(x.sent),1)
    def test_invalid_input_never_sent(self):
        x=FakeWire(b'\x01\0')
        with self.assertRaises(ProtocolError):x.control(b'\x80'+b'\0'*7,0,b'',0)
        self.assertEqual(x.sent,[])
if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Checks)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    p=ROOT/'runs/e05-usbip-kernel/protocol-tests.json';p.parent.mkdir(exist_ok=True,parents=True)
    p.write_text(json.dumps({'scope':'host adapter synthetic wire tests, not guest','tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful()},indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
