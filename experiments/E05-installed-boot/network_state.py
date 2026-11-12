#!/usr/bin/env python3
"""Bounded live net/control-ring/MSI-X reads; never stop or modify the VM."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys
import uuid

from guest_ui import GuestUI, UIError, ROOT

sys.path.insert(0,str(ROOT/'experiments/E05-usbip-kernel'))
spec=importlib.util.spec_from_file_location('storage_pci_parser',
    ROOT/'experiments/E05-usbip-kernel/validate-storage.py')
pci_parser=importlib.util.module_from_spec(spec);spec.loader.exec_module(pci_parser)
NET_PATH='/machine/peripheral/guestnet-pci/virtio-backend'
# Pinned hw/vmapple/vmapple.c memmap, also used by validate-storage.py.
ECAM_BASE=pci_parser.ECAM_BASE
RAM_BASE=0x70000000
PCI_MMIO=(0x50000000,0x1fff0000)
MAX_READ_BYTES=4096


def inside(address,size,base,length):
    return (type(address) is int and type(size) is int and size>0
            and address>=base and address+size<=base+length)


def parse_xp(text,address,count,width):
    """Require the exact contiguous address/width output requested from HMP."""
    values=[];cursor=address
    for line in text.splitlines():
        if not line.strip():continue
        match=re.fullmatch(r'\s*(?:0x)?([0-9a-fA-F]+):\s*(.*?)\s*',line)
        if not match or int(match[1],16)!=cursor:
            raise ValueError('Unexpected physical-read address or output')
        words=match[2].split()
        if not words or any(not re.fullmatch(r'0x[0-9a-fA-F]{%d}'%(width*2),w) for w in words):
            raise ValueError('Unexpected physical-read width')
        values.extend(int(w,16) for w in words);cursor+=width*len(words)
    if len(values)!=count:raise ValueError('Incomplete physical-read output')
    return b''.join(value.to_bytes(width,'little') for value in values)


class ReadOnlyNet(GuestUI):
    """Reuse GuestUI process/argv/peer binding, with only fixed read requests."""
    def _request(self,command,arguments=None,*,record=True):
        allowed=(command in ('qmp_capabilities','query-status','query-pci') and not arguments)
        if command=='x-query-virtio-status':
            allowed=arguments=={'path':NET_PATH}
        if command=='x-query-virtio-queue-status':
            allowed=(isinstance(arguments,dict) and set(arguments)=={'path','queue'}
                     and arguments['path']==NET_PATH and type(arguments['queue']) is int
                     and 0<=arguments['queue']<3)
        if command=='human-monitor-command':
            allowed=(arguments==getattr(self,'_pending_read',None)
                     and isinstance(arguments,dict))
        if not allowed:raise UIError('Only fixed network state reads are supported')
        self.verify_process();self.verify_peer()
        token=uuid.uuid4().hex
        self.journal.write(kind='network_state_read',phase='before_send',
                           request=token,command=command,arguments=arguments)
        try:
            self.sock.sendall((json.dumps({'execute':command,'arguments':arguments or {},
                                          'id':token})+'\n').encode())
            while True:
                reply=self._read()
                if 'event' in reply:continue
                if reply.get('id')!=token:raise UIError('Read response correlation failed')
                break
        except Exception:
            self.usable=False
            self.journal.write(kind='network_state_read',phase='reply_missing',request=token)
            raise UIError('Network observation transport interrupted') from None
        accepted='return' in reply and 'error' not in reply
        self.journal.write(kind='network_state_read',phase='reply',request=token,
                           status='accepted' if accepted else 'rejected')
        if not accepted:raise UIError('Network read rejected by QMP')
        return reply['return']

    def physical_read(self,address,count,width):
        suffix={1:'b',2:'h',4:'w'}[width]
        self._pending_read={'command-line':f'xp /{count}{suffix}x {address:#x}'}
        try:
            return parse_xp(self._request('human-monitor-command',self._pending_read),
                            address,count,width)
        finally:self._pending_read=None


class MemoryReads:
    def __init__(self,client,ram_bytes):
        self.client=client;self.ram_bytes=ram_bytes;self.total=0;self.records=[]
    def read(self,address,size,*,width=1,region=None,purpose):
        base,length=region if region is not None else (RAM_BASE,self.ram_bytes)
        if (width not in (1,2,4) or address%width or size%width or size>256
                or not inside(address,size,base,length)
                or self.total+size>MAX_READ_BYTES):
            raise ValueError('Physical read exceeds the bounded selected region')
        data=self.client.physical_read(address,size//width,width)
        self.total+=size
        self.records.append({'address':hex(address),'bytes':size,'width':width,
                             'purpose':purpose,'hex':data.hex()})
        return data


def pci_config_metadata(read,ecam):
    """Skip type-5 pci_cfg_data: reading it can forward into a device ISR."""
    region=(ecam,256)
    config=bytearray(256)
    config[:64]=read(ecam,64,region=region,purpose='PCI standard header')
    if struct.unpack_from('<HH',config)!=(0x1af4,0x1041):
        raise ValueError('ECAM is not the expected modern VirtIO net device')
    pointer=config[0x34];seen=set();previous_end=0x40
    while pointer:
        if pointer in seen or pointer%4 or not previous_end<=pointer<=0xfc:
            raise ValueError('Capability chain is cyclic, overlapping or not ascending')
        seen.add(pointer)
        head=read(ecam+pointer,2,region=region,purpose='PCI capability link')
        kind,next_pointer=head
        size=12 if kind==0x11 else 2
        if kind==9:
            info=read(ecam+pointer,4,region=region,purpose='VirtIO capability header')
            cap_length,cfg_type=info[2:4]
            if not 16<=cap_length<=256-pointer:
                raise ValueError('Invalid VirtIO capability length')
            size=20 if cfg_type==2 else 16
            previous_end=pointer+cap_length
        else:previous_end=pointer+size
        config[pointer:pointer+size]=read(ecam+pointer,size,region=region,
                                          purpose='PCI capability metadata (no type5 data)')
        pointer=next_pointer
    return bytes(config)


def bar_region(config,regions,index,offset,size):
    rows=[row for row in regions if row.get('bar')==index]
    if len(rows)!=1 or rows[0].get('type')!='memory':
        raise ValueError('Missing memory BAR')
    row=rows[0];base=row.get('address');length=row.get('size')
    if type(base) is not int or type(length) is not int or not inside(base,length,*PCI_MMIO):
        raise ValueError('BAR is unassigned or outside the VMApple PCI MMIO map')
    raw=struct.unpack_from('<I',config,0x10+4*index)[0]
    actual=raw & ~15
    if raw & 6 == 4:
        if index>=5:raise ValueError('Invalid 64-bit BAR')
        actual |= struct.unpack_from('<I',config,0x14+4*index)[0]<<32
    if actual!=base or offset<0 or offset+size>length:
        raise ValueError('BAR address or range differs between PCI observations')
    return base+offset,(base,length)


def pci_details(read,device):
    ecam=ECAM_BASE+(5<<15)
    config=pci_config_metadata(read,ecam)
    parsed=pci_parser.parse_pci_capabilities(config,device['regions'])
    result={'state':'observed','ecam_address':hex(ecam),'metadata':parsed,
            'type5_data_read':False,'atomic_snapshot':False}
    common=next(c for c in parsed['capabilities'] if c.get('cfg_type')==1)
    address,region=bar_region(config,device['regions'],common['bar'],
                             int(common['region_offset'],16),common['region_bytes'])
    values={}
    for offset,name in ((16,'config_msix_vector'),(22,'queue_select_before'),
                        (24,'queue_size'),(26,'queue_msix_vector'),(28,'queue_enabled'),
                        (22,'queue_select_after')):
        values[name]=int.from_bytes(read(address+offset,2,width=2,region=region,
                                         purpose='VirtIO common '+name),'little')
    values['selector_stable']=values['queue_select_before']==values['queue_select_after']
    values['control_vector_known']=values['selector_stable'] and values['queue_select_before']==2
    result['common_config']=values
    msix=next(c for c in parsed['capabilities'] if c.get('name')=='msix')
    result['msix']={'enabled':msix['enabled'],'function_mask':msix['function_mask'],
                    'vectors':msix['vectors']}
    if msix['vectors']>8:
        result['msix'].update(state='unknown',reason='table exceeds eight-vector read bound')
        return result
    table=msix['table'];address,region=bar_region(config,device['regions'],table['bar'],
                                                int(table['offset'],16),table['bytes'])
    entries=[]
    for index in range(msix['vectors']):
        low,high,data,control=struct.unpack('<4I',read(address+16*index,16,width=4,
                            region=region,purpose=f'MSI-X table entry {index}'))
        entries.append({'index':index,'message_address':hex(low|(high<<32)),
                        'message_data':data,'vector_control':control,'masked':bool(control&1)})
    pba=msix['pba'];address,region=bar_region(config,device['regions'],pba['bar'],
                                            int(pba['offset'],16),pba['bytes'])
    pending=int.from_bytes(read(address,pba['bytes'],width=4,region=region,purpose='MSI-X PBA'),'little')
    result['msix'].update(state='observed',entries=entries,pending_bits=hex(pending))
    return result


def ring_headers(read,queue,event_idx):
    n=queue['vring-num'];avail=queue['vring-avail'];used=queue['vring-used']
    if not 1<=n<=1024 or not avail or not used:raise ValueError('Queue ring is not initialized')
    af,ai=struct.unpack('<HH',read(avail,4,purpose='net avail header'))
    uf,ui=struct.unpack('<HH',read(used,4,purpose='net used header'))
    result={'queue':queue['queue-index'],'avail_flags':af,'avail_idx':ai,'used_flags':uf,
            'used_idx':ui,'avail_used_regions_overlap':(
                avail<used+4+8*n+(2 if event_idx else 0) and
                used<avail+4+2*n+(2 if event_idx else 0))}
    if event_idx:
        for name,address in (('used_event',avail+4+2*n),('avail_event',used+4+8*n)):
            result[name]={'address':hex(address),
                          'value':int.from_bytes(read(address,2,purpose=name),'little')}
    return result


def control_descriptor(read,queue,headers):
    n=queue['vring-num'];used_idx=headers['used_idx']
    if not used_idx:
        return {'state':'unknown','reason':'no nonzero completed index in this snapshot'}
    entry=read(queue['vring-used']+4+((used_idx-1)%n)*8,8,purpose='latest control used entry')
    head,written=struct.unpack('<II',entry)
    if head>=n:raise ValueError('Control used head exceeds descriptor table')
    table=queue['vring-desc'];index=head;limit=n;seen=set();rows=[];indirect=False
    result={'state':'observed','used_head':head,'used_length':written,'descriptors':rows,
            'ack_byte':None,'ack_meaning':'unknown',
            'scope':'Current descriptor/buffer contents; buffers may have been reused after completion.'}
    for _ in range(8):
        if index>=limit or (table,index) in seen:raise ValueError('Invalid/cyclic control descriptor chain')
        seen.add((table,index))
        address,length,flags,next_index=struct.unpack('<QIHH',
            read(table+index*16,16,purpose='control descriptor'))
        row={'table':hex(table),'index':index,'address':hex(address),
             'length':length,'flags':flags,'next':next_index};rows.append(row)
        if flags & ~7:raise ValueError('Unknown split descriptor flags')
        if flags&4:
            if indirect or flags&3 or length%16 or not 16<=length<=128:
                raise ValueError('Indirect control table exceeds the one-level/eight-entry bound')
            table,index,limit,indirect=address,0,length//16,True
            continue
        if not flags&2 and 'request_header' not in result and length>=2:
            result['request_header']=list(read(address,2,purpose='control class/command'))
            if result['request_header']==[0,1]:result['request_name']='RX_ALLMULTI'
        if flags&2 and result['ack_byte'] is None and length>=1:
            result['ack_byte']=read(address,1,purpose='control ACK')[0]
            result['ack_meaning']={0:'OK',1:'ERR'}.get(result['ack_byte'],'unknown')
        if not flags&1:return result
        index=next_index
    raise ValueError('Control descriptor chain exceeds eight entries')


def collect(client):
    if '-M' not in client.args or client.args[client.args.index('-M')+1].split(',')[0]!='vmapple':
        raise ValueError('This collector only supports the pinned VMApple map')
    memory=client.args[client.args.index('-m')+1]
    if memory not in ('4G','8G'):raise ValueError('Unknown VMApple RAM extent')
    reads=MemoryReads(client,int(memory[0])*1024**3)
    result={'observed_at_utc':datetime.now(timezone.utc).isoformat(),
            'qemu_pid':client.pid,'start_ticks':client.start_ticks,
            'scope':'Bounded live reads, no stop/cont, selector writes, ISR MMIO reads or queue-element command. '
                    'Values are not atomic and do not prove past interrupt delivery.',
            'platform_map_source':'sources/qemu-bdif-usb/hw/vmapple/vmapple.c memmap',
            'reads':reads.records}
    result['vm_status']=client._request('query-status')
    status=client._request('x-query-virtio-status',{'path':NET_PATH})
    result['network_status']=status
    if status.get('device-id')!=1 or status.get('num-vqs')!=3:
        result['state']='unknown';result['reason']='Unexpected network device/queue layout';return result
    if status.get('vhost-started') is not False:
        result.update(state='unknown',reason='Vhost queue inspection is outside this read-only collector')
        return result
    queues=[client._request('x-query-virtio-queue-status',{'path':NET_PATH,'queue':i}) for i in range(3)]
    result['queues']=queues
    pci=client._request('query-pci')
    devices=[d for bus in pci for d in bus['devices']
             if (d.get('bus'),d.get('slot'),d.get('function'))==(0,5,0)]
    if (len(devices)!=1 or
            (devices[0].get('id',{}).get('vendor'),devices[0].get('id',{}).get('device'))!=(0x1af4,0x1041) or
            devices[0].get('qdev_id')!='guestnet-pci' or
            devices[0].get('class_info',{}).get('class')!=0x0200):
        result['state']='unknown';result['reason']='PCI slot5 identity mismatch';return result
    result['pci_identity']={key:devices[0][key] for key in ('bus','slot','function','qdev_id','class_info','id')}
    try:result['pci']=pci_details(reads.read,devices[0])
    except (ValueError,UIError) as error:
        result.update(state='unknown',reason=str(error));return result
    transports=status['guest-features']['transports']
    if any('VIRTIO_F_RING_PACKED:' in name for name in transports):
        result.update(state='unknown',reason='Packed ring physical inspection is unsupported');return result
    event_idx=any('VIRTIO_RING_F_EVENT_IDX:' in name for name in transports)
    result['ring_headers']=[]
    for queue in queues:
        try:header=ring_headers(reads.read,queue,event_idx);result['ring_headers'].append(header)
        except ValueError as error:
            result['ring_headers'].append({'queue':queue['queue-index'],'state':'unknown','reason':str(error)})
    control=result['ring_headers'][2]
    if control.get('state')!='unknown':
        try:result['control']=control_descriptor(reads.read,queues[2],control)
        except ValueError as error:result['control']={'state':'unknown','reason':str(error)}
    result['state']='observed';result['physical_bytes_read']=reads.total
    result['completed_at_utc']=datetime.now(timezone.utc).isoformat()
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True)
    parser.add_argument('--name',default='network-state-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',args.name):parser.error('Invalid output name')
    with ReadOnlyNet(args.run) as client:
        output=client.directory/(args.name+'.json')
        if output.exists():raise ValueError('Observation already exists')
        result=collect(client);result['run']=args.run
        with output.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps({'report':str(output),'state':result['state']}))
    return 0 if result['state']=='observed' else 1


if __name__=='__main__':raise SystemExit(main())
