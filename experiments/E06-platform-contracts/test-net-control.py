#!/usr/bin/env python3
"""Actual diskless VirtIO net control capacity and packet checks; no Apple guest."""
import argparse
import array
import os
import threading
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments/E05-usbip-kernel'))
spec=importlib.util.spec_from_file_location('pcm_qtest',ROOT/'experiments/E05-usbip-kernel/test-pcm-boundary.py')
transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)
PATH='/machine/peripheral/net/virtio-backend'

class QueueCountPeer:
    """Bounded vhost-user handshake only, following upstream vhost-user-test.

    Advertise two backend queue pairs so real feature negotiation rebuilds
    QEMU's control queue. Link remains down: no offloaded datapath is claimed.
    """
    def __init__(self,path):
        self.listener=socket.socket(socket.AF_UNIX);self.listener.bind(str(path));self.listener.listen(1)
        self.connection=None;self.requests=[];self.error=None
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()

    def run(self):
        try:
            self.connection,_=self.listener.accept();c=self.connection
            def exact(size):
                data=b''
                while len(data)<size:
                    chunk,ancillary,_,_=c.recvmsg(size-len(data),socket.CMSG_SPACE(128))
                    for level,kind,fds in ancillary:
                        assert (level,kind)==(socket.SOL_SOCKET,socket.SCM_RIGHTS)
                        values=array.array('i');values.frombytes(fds)
                        for fd in values:os.close(fd)
                    if not chunk:raise EOFError
                    data+=chunk
                return data
            while True:
                request,flags,size=struct.unpack('<III',exact(12));assert size<=4096
                payload=exact(size);self.requests.append(request)
                response=None
                if request==1:response=struct.pack('<Q',(1<<22)|(1<<28)|(1<<29)|(1<<30)|(1<<32))
                elif request==15:response=struct.pack('<Q',1) # protocol MQ
                elif request==17:response=struct.pack('<Q',2)
                elif request==11:response=payload[:4]+bytes(4)
                elif flags&8:response=bytes(8)
                else:assert request in (2,3,4,5,8,9,10,12,13,14,16,18),request
                if response is not None:c.sendall(struct.pack('<III',request,5,len(response))+response)
        except (EOFError,ConnectionResetError):pass
        except Exception as e:self.error=repr(e)

    def close(self):
        if self.connection:self.connection.close()
        self.listener.close();self.thread.join(timeout=3)
        assert not self.thread.is_alive() and self.error is None,self.error


class NetFixture(transport.Fixture):
    """Reuse existing qtest/QMP/memory/descriptor transport, with a NIC fixture."""
    def __init__(self,binary,directory,size,mq=False):
        directory.mkdir();self.directory=directory
        self.temp=tempfile.TemporaryDirectory(prefix='net-control-qtest-')
        self.sockpath=Path(self.temp.name)/'qtest';self.qmppath=Path(self.temp.name)/'qmp'
        self.peer,child=socket.socketpair();self.peer.settimeout(3)
        self.backend=QueueCountPeer(Path(self.temp.name)/'vhost') if mq else None
        self.args=[str(binary),'-machine','virt,highmem=off','-cpu','max','-accel','qtest',
                   '-m','128M','-nodefaults','-nic','none','-display','none','-serial','none','-monitor','none',
                   '-netdev',f'socket,id=n,fd={child.fileno()}',
                   '-device','virtio-net-pci,id=net,netdev=n,addr=0x5,disable-legacy=on,x-ascending-capabilities=on,mac=52:54:00:12:34:56'+(f',ctrl_queue_size={size}' if size is not None else ''),
                   '-qtest',f'unix:{self.sockpath},server=on,wait=off','-qtest-log','/dev/null',
                   '-qmp',f'unix:{self.qmppath},server=on,wait=off']
        if mq:
            at=self.args.index('-netdev');self.args[at:at+2]=['-chardev',f'socket,id=vh,path={self.temp.name}/vhost','-netdev','vhost-user,id=n,chardev=vh,queues=2']
            at=self.args.index('-device')+1;self.args[at]+=',mq=on,vectors=6'
        self.err=(directory/'stderr.log').open('wb')
        self.qt=self.qmp=self.qf=self.qmf=None;self.queues={}
        self.proc=subprocess.Popen(self.args,stdout=subprocess.DEVNULL,stderr=self.err,pass_fds=(child.fileno(),));child.close()
        try:
            deadline=time.monotonic()+8
            while not self.sockpath.exists() or not self.qmppath.exists():
                if self.proc.poll() is not None or time.monotonic()>deadline:raise RuntimeError('qtest startup failed')
                time.sleep(.01)
            self.qt=socket.socket(socket.AF_UNIX);self.qt.settimeout(5);self.qt.connect(str(self.sockpath));self.qf=self.qt.makefile('rb')
            self.qmp=socket.socket(socket.AF_UNIX);self.qmp.settimeout(5);self.qmp.connect(str(self.qmppath));self.qmf=self.qmp.makefile('rb')
            json.loads(self.qmf.readline());self.request('qmp_capabilities')
            if mq:self.request('set_link',{'name':'net','up':False})
        except BaseException:
            self.close();raise

    def close(self):
        super().close();self.peer.close()
        if self.backend:self.backend.close()

    def mem(self,address,size):
        return bytes.fromhex(self.command(f'read {address:#x} {size:#x}')[2:])

    def queues_status(self):
        n=self.request('x-query-virtio-status',{'path':PATH})['num-vqs']
        return [self.request('x-query-virtio-queue-status',{'path':PATH,'queue':i}) for i in range(n)]

    def setup(self,size,mq=False):
        ecam=0x3f000000+(5<<15);config=self.mem(ecam,256)
        assert config[:4]==bytes.fromhex('f41a4110')
        bars={1:0x10000000,4:0x10004000}
        for offset,value in ((0x14,bars[1]),(0x20,bars[4]),(0x24,0)):
            self.command(f'writel {ecam+offset:#x} {value:#x}')
        self.command(f'writew {ecam+4:#x} 6')
        caps={};offset=config[0x34]
        while offset:
            if config[offset]==9:caps[config[offset+3]]=offset
            offset=config[offset+1]
        def cap(kind):
            at=caps[kind];return bars[config[at+4]]+struct.unpack_from('<I',config,at+8)[0]
        self.common,self.notify,self.isr=cap(1),cap(2),cap(3)
        self.multiplier=struct.unpack_from('<I',config,caps[2]+16)[0]
        self.command(f'writeb {self.common+20:#x} 0');self.command(f'writeb {self.common+20:#x} 3')
        selected=(1<<17)|(1<<18)|(1<<28)|(1<<29)|((1<<22) if mq else 0)
        self.command(f'writel {self.common:#x} 0');assert self.read('l',self.common+4)&selected==selected
        for word,features in ((0,selected),(1,1)):
            self.command(f'writel {self.common+8:#x} {word}');self.command(f'writel {self.common+12:#x} {features}')
        self.command(f'writeb {self.common+20:#x} 11');assert self.read('b',self.common+20)==11
        self.control_index=4 if mq else 2
        for index,capacity in enumerate(([256]*self.control_index)+[size]):
            self.command(f'writew {self.common+22:#x} {index}')
            assert self.read('w',self.common+24)==capacity
            base=0x40010000+index*0x10000;desc=base
            avail=(desc+16*capacity+4095)&-4096;used=(avail+6+2*capacity+4095)&-4096
            for offset,address in ((32,desc),(40,avail),(48,used)):
                self.command(f'writel {self.common+offset:#x} {address:#x}');self.command(f'writel {self.common+offset+4:#x} 0')
            self.write(avail,bytes(6+2*capacity));self.write(used-16,b'\xa5'*16+bytes(6+8*capacity)+b'\x5a'*16)
            self.command(f'writew {self.common+28:#x} 1')
            self.queues[index]={'desc':desc,'avail':avail,'used':used,'notify':self.notify+self.multiplier*self.read('w',self.common+30),'sequence':0,'size':capacity}
        self.command(f'writeb {self.common+20:#x} 15')
        self.host_features=self.request('x-query-virtio-status',{'path':PATH})['host-features']

    def complete(self,index,head,length):
        q=self.queues[index];deadline=time.monotonic()+3
        while self.read('w',q['used']+2)!=(q['sequence']&65535):
            if time.monotonic()>deadline:raise TimeoutError('used completion')
            time.sleep(.001)
        entry=self.mem(q['used']+4+8*((q['sequence']-1)%q['size']),8)
        assert struct.unpack('<II',entry)==(head,length),entry.hex()
        assert self.mem(q['used']-16,16)==b'\xa5'*16
        assert self.mem(q['used']+6+8*q['size'],16)==b'\x5a'*16

    def controls(self,count):
        index=self.control_index;q=self.queues[index];heads=[]
        for i in range(count):
            # Split the two-byte header across pages and SG entries.
            a,b,c,ack=0x40100fff,0x40102001,0x40103000,0x40104000
            invalid=i==count-1
            data=(b'\xff' if invalid else b'\x00',b'\x01',bytes([i&1]))
            expected_ack=1 if invalid else 0
            for address,body in zip((a,b,c),data):self.write(address-1,b'\xa5'+body+b'\x5a')
            self.write(ack-1,b'\xa5\xff\x5a')
            indirect=i!=0;head=(q['size']-1-i)%q['size'] if indirect else 0
            self.submit(index,[(a,1,False),(b,1,False),(c,1,False),(ack,1,True)],indirect=indirect,head=head)
            self.complete(index,head,1)
            assert self.mem(ack-1,3)==bytes([0xa5,expected_ack,0x5a])
            for address,body in zip((a,b,c),data):assert self.mem(address-1,3)==b'\xa5'+body+b'\x5a'
            self.command(f'writew {q["avail"]+4+2*q["size"]:#x} {q["sequence"]&65535}')
            if i in (0,1,q['size']-1,q['size'],count-1):heads.append({'sequence':i+1,'head':head,'ack':expected_ack,'command':'invalid-class' if invalid else 'RX_ALLMULTI','used_len':1})
        return {'requests':count,'rx_allmulti_requests':count-1,'invalid_class_requests':1,'selected_heads':heads,'ack_sentinel':255,'used_and_payload_canaries_unchanged':True,'indirect_and_direct_sg':True,'event_idx':True}

    def packets(self):
        packet=bytes.fromhex('ffffffffffff52540012345688b5')+bytes(range(46))
        tx=0x40200000;self.write(tx,bytes(12)+packet)
        self.submit(1,[(tx,12+len(packet),False)],indirect=True,head=255);self.complete(1,255,0)
        def recv_exact(n):
            out=b''
            while len(out)<n:out+=self.peer.recv(n-len(out))
            return out
        length=struct.unpack('!I',recv_exact(4))[0];assert length==len(packet);assert recv_exact(length)==packet
        rx=0x40201000;self.write(rx-1,b'\xa5'+b'\xff'*(12+len(packet))+b'\x5a')
        self.submit(0,[(rx,12+len(packet),True)],indirect=True,head=255)
        self.peer.sendall(struct.pack('!I',len(packet))+packet);self.complete(0,255,12+len(packet))
        assert self.mem(rx+12,len(packet))==packet
        assert self.mem(rx-1,1)==b'\xa5' and self.mem(rx+12+len(packet),1)==b'\x5a'
        return {'rx_and_tx_bytes':len(packet),'packet_sha256':hashlib.sha256(packet).hexdigest(),'rx_tx_queues':256,'canaries_unchanged':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    binary=ROOT/'build/qemu-bdif-usb/qemu-system-aarch64';build=json.loads((ROOT/'runs/qemu-bdif-usb-build.json').read_text())
    digest=hashlib.sha256(binary.read_bytes()).hexdigest();assert digest==build['binary_sha256']
    patch='patches/qemu-platform/0032-virtio-net-control-queue-size.patch';assert build['patch_sha256'][patch]==hashlib.sha256((ROOT/patch).read_bytes()).hexdigest()
    record={'binary_sha256':digest,'scope':__doc__,'cases':[],'invalid':[],'passed':False}
    try:
        for name,size in [('default',None),('explicit64',64),('explicit512',512)]:
            fixture=NetFixture(binary,out/name,size);row={'case':name,'command':fixture.args,'passed':False};record['cases'].append(row)
            try:
                expected=size or 64
                assert fixture.request('qom-get',{'path':PATH,'property':'ctrl_queue_size'})==expected
                row['initial_queues']=fixture.queues_status();assert [x['vring-num-default'] for x in row['initial_queues']]==[256,256,expected]
                fixture.setup(expected);row['selected_queues']=fixture.queues_status()
                assert [x['vring-num'] for x in row['selected_queues']]==[256,256,expected]
                row['controls']=fixture.controls(expected+3);row['packets']=fixture.packets();row['host_features']=fixture.host_features;row['passed']=True
            finally:
                fixture.close();row.update(exit_code=fixture.proc.returncode,reaped=fixture.proc.poll() is not None)
                (out/'results.json').write_text(json.dumps(record,indent=2)+'\n')
            print(name,'passed',flush=True)
        assert record['cases'][0]['host_features']==record['cases'][1]['host_features']==record['cases'][2]['host_features']
        fixture=NetFixture(binary,out/'mq-rebuild512',512,mq=True)
        row={'case':'mq-rebuild512','command':fixture.args,'generations':[],'passed':False};record['cases'].append(row)
        try:
            assert [x['vring-num-default'] for x in fixture.queues_status()]==[256,256,512]
            for mq in (True,False,True):
                fixture.setup(512,mq=mq)
                status=fixture.queues_status()
                assert [x['vring-num-default'] for x in status]==([256]* (4 if mq else 2))+[512]
                assert [x['vring-num'] for x in status]==([256]* (4 if mq else 2))+[512]
                row['generations'].append({'mq':mq,'queues':status,'controls':fixture.controls(3)})
                assert fixture.request('x-query-virtio-status',{'path':PATH})['vhost-started'] is False
            row['vhost_handshake_requests']=fixture.backend.requests;row['offloaded_datapath_tested']=False;row['passed']=True
        finally:
            fixture.close();row.update(exit_code=fixture.proc.returncode,reaped=fixture.proc.poll() is not None)
            (out/'results.json').write_text(json.dumps(record,indent=2)+'\n')
        print('MQ rebuild passed',flush=True)
        for value in (0,3,2048,65535,65536):
            cmd=[str(binary),'-machine','virt','-nodefaults','-display','none','-device',f'virtio-net-pci,ctrl_queue_size={value}']
            p=subprocess.run(cmd,capture_output=True,text=True,timeout=5);assert p.returncode!=0 and 'ctrl_queue_size' in p.stderr
            row={'value':value,'exit_code':p.returncode,'reaped':True,'stderr':p.stderr};record['invalid'].append(row)
        record['passed']=all(x['passed'] and x['exit_code']==0 and x['reaped'] for x in record['cases'])
    finally:(out/'results.json').write_text(json.dumps(record,indent=2)+'\n')
    print('passed',record['passed'])

if __name__=='__main__':main()
