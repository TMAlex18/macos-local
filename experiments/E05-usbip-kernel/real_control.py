#!/usr/bin/env python3
"""Observed AVPBooter EP0 response framing for this fixed original ROM.

For the locked original ROM, EP0 control OUT is one type-1 frame containing
8-byte setup followed by data (maximum measured data: 2048). A type-3
frame returns a type-5 acknowledgement but does not deliver control data.
Known frame 02 00 is observed on unsupported descriptor requests; map that
explicit EP0 rejection to USB STALL/EPIPE. A subsequent supported request is
part of the regression. Unknown frames and silent responses remain errors.
This is a measured compatibility mapping, not a complete Apple protocol spec.
"""
import errno,socket,struct
from usbip_control import AVPControl,ControlResult,ProtocolError,MAX_CONTROL
class RealFirmwareControl(AVPControl):
    def receive(self):
        raw=super().receive();self.last_wire.append(raw.hex());return raw
    def control(self,setup,direction,outgoing,requested):
        self.last_wire=[]
        if len(setup)!=8 or direction not in (0,1):raise ProtocolError('Invalid control header')
        if bool(setup[0]&0x80)!=bool(direction):raise ProtocolError('Direction mismatch')
        if not 0<=requested<=MAX_CONTROL or struct.unpack_from('<H',setup,6)[0]!=requested:
            raise ProtocolError('Control length mismatch')
        if len(outgoing)!=(requested if direction==0 else 0):raise ProtocolError('OUT payload mismatch')
        if direction==0 and len(outgoing)>2048:
            raise ProtocolError('Observed ROM EP0 receive limit is 8-byte setup plus 2048 data bytes')
        try:
            # Raw ROM dispatch and live DFU states confirm this is ONE
            # control request. Two-phase type3 only acknowledged/cancelled.
            body=setup+outgoing
            self.packet(struct.pack('<iBB',len(body),0,1)+body)
            expected=1
            raw=self.receive()
            if raw==b'\x02\x00':return ControlResult(-errno.EPIPE,b'',0)
            if len(raw)<2 or raw[0]!=expected or raw[1]!=0:
                raise ProtocolError('Unrecognized EP0 response frame')
            data=raw[2:]
            if direction:
                if len(data)>requested:raise ProtocolError('Overlong IN')
                return ControlResult(0,data,len(data))
            if data:raise ProtocolError('Unexpected OUT data')
            return ControlResult(0,b'',requested)
        except socket.timeout:return ControlResult(-errno.ETIMEDOUT,b'',0,True)
        except (ProtocolError,EOFError,OSError):return ControlResult(-errno.EPROTO,b'',0,True)
