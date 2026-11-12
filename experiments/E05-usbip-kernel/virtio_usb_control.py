"""Sequential reset/EP0 client for the pinned kernel's ordinary VirtIO USB ABI.

Only the observed request types 1/2 and responses 1/2/4 are used. No bulk, URB
cancellation or guessed connection event is implemented. The outer LE length
is our QEMU chardev framing, not a guest VirtIO descriptor field.
"""
import errno
import socket
import struct
from usbip_control import ControlResult, ProtocolError

# The six-byte request header is part of the guest request body.  The four
# bytes before it are only the local QEMU chardev framing.
REQUEST_HEADER_SIZE=6
RESPONSE_HEADER_SIZE=2
MAX_REQUEST_BODY=0x20000
MAX_RESPONSE_BODY=1024*1024
EP0=0
REQUEST_TRANSFER=1
REQUEST_RESET=2
RESPONSE_TRANSFER=1
RESPONSE_STALL=2
RESPONSE_RESET=4
RESET_VALUE=0x10


class KernelVirtioUSBControl:
    def __init__(self,sock,timeout=6):
        self.sock=sock
        self.sock.settimeout(timeout)
        self.last_wire=[]
        self.usable=True

    def _read(self,size):
        data=bytearray()
        while len(data)<size:
            part=self.sock.recv(size-len(data))
            if not part:raise EOFError('VirtIO USB peer closed')
            data.extend(part)
        return bytes(data)

    def packet(self,body):
        if not self.usable:raise ProtocolError('Terminal VirtIO USB stream cannot be reused')
        if not REQUEST_HEADER_SIZE<=len(body)<=MAX_REQUEST_BODY:
            raise ProtocolError('Request body outside receive-buffer bound')
        self.sock.sendall(struct.pack('<I',len(body))+body)

    def receive(self):
        size=struct.unpack('<I',self._read(4))[0]
        if not RESPONSE_HEADER_SIZE<=size<=MAX_RESPONSE_BODY:
            raise ProtocolError('Response body outside host transport bound')
        body=self._read(size)
        self.last_wire.append(body.hex())
        return body

    def _terminal(self,error):
        self.usable=False
        status=-errno.ETIMEDOUT if isinstance(error,socket.timeout) else -errno.EPROTO
        return ControlResult(status,b'',0,True)

    def reset_bus(self):
        self.last_wire=[]
        try:
            self.packet(struct.pack('<IBB',0,EP0,REQUEST_RESET))
            response=self.receive()
            # The second byte is the observed reset value, not an endpoint.
            if response!=bytes((RESPONSE_RESET,RESET_VALUE)):
                raise ProtocolError('Unrecognized kernel USB reset response')
            return ControlResult(0,b'',0)
        except (socket.timeout,ProtocolError,EOFError,OSError) as error:
            return self._terminal(error)

    def control(self,setup,direction,outgoing,requested):
        self.last_wire=[]
        if (len(setup)!=8 or direction not in (0,1) or
                bool(setup[0]&0x80)!=bool(direction) or
                not 0<=requested<=0xffff or
                struct.unpack_from('<H',setup,6)[0]!=requested or
                len(outgoing)!=(requested if direction==0 else 0)):
            raise ProtocolError('Inconsistent EP0 setup/direction/data length')
        try:
            body=setup+outgoing
            self.packet(struct.pack('<IBB',len(body),EP0,REQUEST_TRANSFER)+body)
            response=self.receive()
            if response==bytes((RESPONSE_STALL,EP0)):
                return ControlResult(-errno.EPIPE,b'',0)
            if response[:RESPONSE_HEADER_SIZE]!=bytes((RESPONSE_TRANSFER,EP0)):
                raise ProtocolError('Unrecognized kernel EP0 response')
            data=response[RESPONSE_HEADER_SIZE:]
            if direction:
                if len(data)>requested:raise ProtocolError('EP0 response exceeds requested data')
                return ControlResult(0,data,len(data))
            if data:raise ProtocolError('Unexpected data on EP0 OUT completion')
            return ControlResult(0,b'',requested)
        except (socket.timeout,ProtocolError,EOFError,OSError) as error:
            return self._terminal(error)
