#!/usr/bin/env python3
"""Small control-only USB/IP research gateway. No kernel attachment.

Protocol sources: Linux Documentation/usb/usbip_protocol.rst and the pinned
steelbrain AVPTransport prototype. No generated DFU progress/status responses.
Each response contains guest bytes, a genuine short response, or an error.
"""
import errno,socket,struct
from dataclasses import dataclass
MAX_CONTROL=4094  # 4096-byte guest TX limit minus two AVP response bytes.
USBIP_VERSION=0x0111

class ProtocolError(Exception):pass

def read_exact(sock:socket.socket,count:int)->bytes:
    if count<0 or count>131072:raise ProtocolError('invalid bounded read length')
    out=bytearray()
    while len(out)<count:
        part=sock.recv(count-len(out))
        if not part:raise EOFError('peer disconnected')
        out.extend(part)
    return bytes(out)

@dataclass
class ControlResult:
    status:int
    payload:bytes
    actual_length:int
    terminal:bool=False

class AVPControl:
    def __init__(self,sock:socket.socket,timeout:float=.4):
        self.sock=sock;self.sock.settimeout(timeout)
    def packet(self,data:bytes):
        if len(data)>131072:raise ProtocolError('frame exceeds transport max')
        self.sock.sendall(struct.pack('<I',len(data))+data)
    def receive(self)->bytes:
        size=struct.unpack('<I',read_exact(self.sock,4))[0]
        if size>4096:raise ProtocolError('response exceeds negotiated local TX bound')
        return read_exact(self.sock,size)
    def control(self,setup:bytes,direction:int,outgoing:bytes,requested:int)->ControlResult:
        if len(setup)!=8 or direction not in (0,1):raise ProtocolError('invalid setup')
        if bool(setup[0]&0x80)!=bool(direction):raise ProtocolError('setup direction mismatch')
        if requested<0 or requested>MAX_CONTROL or struct.unpack_from('<H',setup,6)[0]!=requested:
            raise ProtocolError('inconsistent control length')
        if len(outgoing)!=(requested if direction==0 else 0):raise ProtocolError('bad OUT payload')
        try:
            self.packet(struct.pack('<iBB',8,0,1)+setup)
            expected=1
            if direction==0 and outgoing:
                self.packet(struct.pack('<iBB',len(outgoing),0,3)+outgoing);expected=5
            response=self.receive()
            if len(response)<2 or response[0]!=expected:raise ProtocolError('malformed guest response')
            if response[1]!=0:return ControlResult(-errno.EPIPE,b'',0)
            payload=response[2:]
            if direction==1:
                if len(payload)>requested:raise ProtocolError('guest overlong response')
                return ControlResult(0,payload,len(payload))
            if payload:raise ProtocolError('unexpected OUT data')
            return ControlResult(0,b'',requested)
        except socket.timeout:
            return ControlResult(-errno.ETIMEDOUT,b'',0,True)
        except (ProtocolError,EOFError,OSError):
            # No transaction IDs: close the bridge on framing uncertainty;
            # do not reuse a late response for a subsequent control transfer.
            return ControlResult(-errno.EPROTO,b'',0,True)


def serve(client:socket.socket,avp:AVPControl,record:list)->None:
    """Run one sequential import/control stream, deriving identity from guest.

    Endpoints other than EP0 and unlink are not implemented; this deliberately
    is not a complete USB/IP controller. A caller owns and closes sockets.
    """
    result=avp.control(bytes.fromhex('8006000100001200'),1,b'',18)
    if result.status or len(result.payload)!=18 or result.payload[:2]!=b'\x12\x01':
        raise ProtocolError('guest did not supply one complete device descriptor')
    desc=result.payload
    vendor,product,revision=struct.unpack_from('<HHH',desc,8)
    header=read_exact(client,8)
    version,operation,status=struct.unpack('!HHI',header)
    if version!=USBIP_VERSION or operation!=0x8003 or status:raise ProtocolError('unsupported import')
    if read_exact(client,32).split(b'\0',1)[0]!=b'1-1':raise ProtocolError('unknown busid')
    # 312-byte device metadata, derived descriptor fields rather than an
    # Apple identity invented by the exporter.
    device=(b'/synthetic/vmapple/control'.ljust(256,b'\0')+b'1-1'.ljust(32,b'\0')+
            struct.pack('!IIIHHHBBBBBB',1,1,3,vendor,product,revision,
                        desc[4],desc[5],desc[6],0,desc[17],0))
    client.sendall(struct.pack('!HHI',version,3,0)+device)
    record.append({'event':'import','vendor':vendor,'product':product,'origin':'synthetic guest descriptor'})
    while True:
        try:h=read_exact(client,48)
        except EOFError:return
        command,seqnum,devid,direction,endpoint=struct.unpack_from('!IIIII',h)
        flags,length,start,packets,interval=struct.unpack_from('!Iiiii',h,20)
        setup=h[40:48]
        if command!=1 or endpoint!=0 or direction not in (0,1) or packets!=0:
            raise ProtocolError('only sequential EP0 non-isochronous submit supported')
        if length<0 or length>MAX_CONTROL:raise ProtocolError('control transfer length bound')
        outgoing=read_exact(client,length) if direction==0 else b''
        try:reply=avp.control(setup,direction,outgoing,length)
        except ProtocolError:reply=ControlResult(-errno.EPROTO,b'',0,True)
        client.sendall(struct.pack('!IIIIIiiiii8x',3,seqnum,devid,direction,endpoint,
                                    reply.status,reply.actual_length,0,0,0)+reply.payload)
        record.append({'event':'control','seqnum':seqnum,'setup':setup.hex(),
                       'status':reply.status,'actual_length':reply.actual_length,
                       'payload_hex':reply.payload.hex(),'synthetic_status_injected':False})
        if reply.terminal:return
