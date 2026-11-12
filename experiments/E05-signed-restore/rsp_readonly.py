"""Narrow QEMU GDB RSP observer: registers and execution breakpoints.

No memory/register write command exists in this helper. Hardware breakpoint
requests are handled by QEMU; no guest ROM instruction bytes are overwritten.
"""
import socket,re
# QEMU prints zero-padded uint32 CPU/process IDs; keep their wire spelling.
POSITIVE_HEX = r'(?=[0-9a-f]{1,8}(?![0-9a-f]))0*[1-9a-f][0-9a-f]*'
THREAD_ID = r'(?:p' + POSITIVE_HEX + r'\.)?' + POSITIVE_HEX
# Target-description documents QEMU serves through read-only qXfer.
FEATURE_ANNEX = r'[A-Za-z0-9_.-]{1,64}\.xml'
REGISTER_ELEMENT = re.compile(r'<reg name="([A-Za-z0-9_]+)" bitsize="(\d+)" regnum="(\d+)"')


def system_cpu_thread(cpu_index):
    """Return QEMU system-mode's positive GDB thread ID for one CPU index.

    QEMU numbers system-emulation CPUs from one because thread zero means
    "any CPU". Keep the exact lower-case, at-least-two-digit wire spelling
    used by ``gdb_append_thread_id`` instead of deriving address-space
    selection from an unrelated asynchronous stop packet.
    """
    if (isinstance(cpu_index, bool) or not isinstance(cpu_index, int) or
            not 0 <= cpu_index < (1 << 32) - 1):
        raise ValueError('Expected one bounded non-negative QEMU CPU index')
    return f'{cpu_index + 1:02x}'


def select_system_cpu(rsp, cpu_index):
    """Select the QEMU system CPU whose virtual address space will be read."""
    thread = system_cpu_thread(cpu_index)
    rsp.select_thread(thread)
    return thread


def system_thread_cpu_index(thread):
    """Invert one plain QEMU system-mode CPU thread ID to its zero-based index."""
    if not isinstance(thread, str) or not re.fullmatch(POSITIVE_HEX, thread):
        raise ValueError('Expected one plain positive QEMU system CPU thread ID')
    value = int(thread, 16)
    if value <= 0:
        raise ValueError('QEMU system CPU thread zero is not a concrete CPU')
    return value - 1


def stop_thread(reply):
    """A positive QEMU CPU thread from a trap stop; never normalize pointers/IDs."""
    threads = [field[7:] for field in reply[3:].split(';') if field.startswith('thread:')]
    if (not re.fullmatch(r'T05(?:[^;]+;)+', reply) or len(threads) != 1
            or not re.fullmatch(THREAD_ID, threads[0])):
        raise ValueError('Expected a QEMU trap stop with one positive thread ID')
    return threads[0]


class RSP:
    def __init__(self,path,timeout=6):
        self.sock=socket.socket(socket.AF_UNIX);self.sock.settimeout(timeout);self.sock.connect(str(path))
    def byte(self):
        b=self.sock.recv(1)
        if not b:raise EOFError('RSP closed')
        return b[0]
    def _discard_packet_after_start(self):
        """Consume an asynchronous packet seen while waiting for command ACK."""
        raw=bytearray()
        while True:
            b=self.byte()
            if b==ord('#'):break
            raw.append(b)
            if len(raw)>1048576:raise ValueError('RSP packet too large')
        check=bytes([self.byte(),self.byte()])
        if int(check,16)!=(sum(raw)&255):raise ValueError('RSP checksum mismatch')
        self.sock.sendall(b'+')
    def send(self,command):
        if getattr(self,'step_failed',False):raise RuntimeError('Uncertain step connection is closed; reconnect after stopping the owned VM')
        if not re.fullmatch(r'(?:qSupported|\?|Hg' + THREAD_ID + r'|Hc-1|vCont\?|vCont;s:' + THREAD_ID + r'|g|p[0-9a-f]{1,4}|qXfer:features:read:' + FEATURE_ANNEX + r':[0-9a-f]{1,8},[0-9a-f]{1,8}|m[0-9a-f]+,[0-9a-f]+|[Zz]1,[0-9a-f]+,4|c|D)',command):
            raise ValueError('Only read/continue/execution-breakpoint RSP commands allowed')
        raw=command.encode('ascii');self.sock.sendall(b'$'+raw+b'#'+f'{sum(raw)&255:02x}'.encode())
        b=self.byte()
        while b==ord('$'):
            self._discard_packet_after_start()
            b=self.byte()
        if b!=ord('+'):raise RuntimeError(f'RSP missing ACK {b}')
    def receive(self):
        b=self.byte()
        while b!=ord('$'):
            b=self.byte()
        raw=bytearray()
        while True:
            b=self.byte()
            if b==ord('#'):break
            raw.append(b)
            if len(raw)>1048576:raise ValueError('RSP packet too large')
        check=bytes([self.byte(),self.byte()])
        if int(check,16)!=(sum(raw)&255):raise ValueError('RSP checksum mismatch')
        self.sock.sendall(b'+')
        out=bytearray();i=0
        while i<len(raw):
            b=raw[i];i+=1
            if b==ord('}'):
                b=raw[i]^32;i+=1;out.append(b)
            elif b==ord('*'):
                if not out or i>=len(raw):raise ValueError('invalid RLE')
                repeat=raw[i]-29;i+=1
                if repeat<0:raise ValueError('invalid repeat')
                out.extend(out[-1:]*repeat)
            else:out.append(b)
        return out.decode('ascii')
    def command(self,c):self.send(c);return self.receive()
    def select_thread(self,thread):
        if not re.fullmatch(THREAD_ID,thread):raise ValueError('Expected one positive thread ID')
        answer=self.command('Hg'+thread)
        if answer!='OK':raise RuntimeError(f'Cannot select stopped thread: {answer}')
    def step_thread(self,thread):
        if not re.fullmatch(THREAD_ID,thread):raise ValueError('Expected one positive thread ID')
        previous=self.sock.gettimeout();self.sock.settimeout(3)
        failed=False
        try:return self.command('vCont;s:'+thread)
        except BaseException:
            failed=True;self.step_failed=True;self.close();raise
        finally:
            if not failed:self.sock.settimeout(previous)
    def regs(self):
        raw=self.command('g')
        if raw.startswith('E'):raise RuntimeError(raw)
        b=bytes.fromhex(raw)
        if len(b)<268:raise ValueError(f'Not AArch64 core registers: {len(b)}')
        d={f'x{i}':int.from_bytes(b[8*i:8*i+8],'little') for i in range(31)}
        d.update(sp=int.from_bytes(b[248:256],'little'),pc=int.from_bytes(b[256:264],'little'),pstate=int.from_bytes(b[264:268],'little'))
        return d
    def read_feature(self,annex):
        """Read one target-description document through read-only qXfer."""
        if not re.fullmatch(FEATURE_ANNEX,annex):raise ValueError('Invalid target-description annex')
        parts=[];offset=0
        while True:
            reply=self.command(f'qXfer:features:read:{annex}:{offset:x},ffb')
            if not reply or reply[0] not in 'ml':raise RuntimeError(f'Target description read failed: {reply[:8]!r}')
            parts.append(reply[1:]);offset+=len(reply)-1
            if offset>1<<20:raise ValueError('Target description too large')
            if reply[0]=='l':return ''.join(parts)
            if len(reply)==1:raise RuntimeError('Target description read made no progress')
    def register_numbers(self):
        """Map register names to (regnum, bitsize) from QEMU's included features."""
        if getattr(self,'_register_numbers',None) is None:
            numbers={}
            for annex in re.findall(r'<xi:include href="([^"]+)"/>',self.read_feature('target.xml')):
                for name,bits,regnum in REGISTER_ELEMENT.findall(self.read_feature(annex)):
                    numbers.setdefault(name,(int(regnum),int(bits)))
            self._register_numbers=numbers
        return self._register_numbers
    def read_register(self,regnum,size=8):
        """Read one register of the selected thread; never a 'P' write."""
        if isinstance(regnum,bool) or not isinstance(regnum,int) or not 0<=regnum<0x10000 or size not in (4,8):
            raise ValueError('register number or size outside bound')
        raw=self.command(f'p{regnum:x}')
        if raw.startswith('E') or len(raw)!=2*size or not re.fullmatch('[0-9a-f]+',raw):
            raise RuntimeError(f'RSP register read failed: {raw[:8]!r}')
        return int.from_bytes(bytes.fromhex(raw),'little')
    def read_system_register(self,name):
        """Read a named system register (for example TPIDR_EL1) of the selected thread."""
        numbers=self.register_numbers()
        if name not in numbers:raise RuntimeError(f'{name} is not in the target description')
        regnum,bits=numbers[name]
        return self.read_register(regnum,bits//8)
    def read_memory(self,address,length):
        if not (0 <= address < 1 << 64 and 0 < length <= 1 << 20):
            raise ValueError('memory read outside bounded u64 range')
        raw=self.command(f'm{address:x},{length:x}')
        if raw.startswith('E'):
            raise RuntimeError(f'RSP memory read failed: {raw}')
        data=bytes.fromhex(raw)
        if len(data)!=length:
            raise ValueError(f'RSP memory read length {len(data)} != {length}')
        return data
    def add_break(self,pc):
        ans=self.command(f'Z1,{pc:x},4')
        if ans!='OK':raise RuntimeError(f'Cannot set execution breakpoint {pc:x}: {ans}')
    def remove_break(self,pc):return self.command(f'z1,{pc:x},4')
    def close(self):self.sock.close()
