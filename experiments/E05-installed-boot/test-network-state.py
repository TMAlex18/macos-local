#!/usr/bin/env python3
"""Bounded parser/transport fixtures; no VM or real MMIO is accessed."""
import json
import struct
import unittest
from unittest.mock import Mock

import network_state as net


class Fixture:
    def __init__(self):
        self.args=['-M','vmapple,memory-backend=ram','-m','4G']
        self.pid=123;self.start_ticks=456;self.memory={};self.reads=[];self.calls=[]
        self.ecam=net.ECAM_BASE+(5<<15)
        self.regions=[{'bar':1,'type':'memory','address':0x50001000,'size':0x1000},
                      {'bar':4,'type':'memory','address':0x50004000,'size':0x4000}]
        self.device={'bus':0,'slot':5,'function':0,'qdev_id':'guestnet-pci',
                     'class_info':{'class':0x0200,'desc':'Ethernet controller'},
                     'id':{'vendor':0x1af4,'device':0x1041,'subsystem-vendor':0x1af4,'subsystem':0x1100},
                     'regions':self.regions}
        config=bytearray(256)
        struct.pack_into('<4H',config,0,0x1af4,0x1041,6,0x10)
        struct.pack_into('<I',config,0x14,0x50001000)
        struct.pack_into('<II',config,0x20,0x5000400c,0)
        config[0x34]=0x40
        for offset,next_offset,kind,bar,region,length in (
            (0x40,0x50,1,4,0,0x1000),(0x50,0x60,3,4,0x1000,0x1000),
            (0x60,0x70,4,4,0x2000,0x1000),(0x70,0x84,2,4,0x3000,0x1000),
            (0x84,0x98,5,4,0x1000,1)):
            config[offset:offset+4]=bytes((9,next_offset,20 if kind in (2,5) else 16,kind))
            config[offset+4]=bar
            struct.pack_into('<II',config,offset+8,region,length)
            if kind==2:struct.pack_into('<I',config,offset+16,4)
        struct.pack_into('<BBHII',config,0x98,0x11,0,0x8003,1,0x801)
        self.put(self.ecam,config)
        self.forbidden=(self.ecam+0x94,4)  # active type-5 selector data
        for offset,value in ((16,0),(22,2),(24,64),(26,3),(28,1)):
            self.put(0x50004000+offset,struct.pack('<H',value))
        for i in range(4):
            self.put(0x50001000+i*16,struct.pack('<4I',0x1fff0040,0,80+i,0 if i==3 else 1))
        self.put(0x50001800,struct.pack('<Q',0))
        self.status={'device-id':1,'num-vqs':3,'vhost-started':False,
                     'guest-features':{'transports':['VIRTIO_RING_F_EVENT_IDX: enabled']}}
        self.queues=[]
        for i,n in ((0,256),(1,256),(2,64)):
            desc=net.RAM_BASE+0x10000*(i+1)
            avail=desc+16*n
            used=avail+(16 if i==2 else 0x1000)
            self.queues.append({'queue-index':i,'vring-num':n,'vring-desc':desc,
                'vring-avail':avail,'vring-used':used,'used-idx':1 if i==2 else 0})
            self.put(avail,struct.pack('<HHH',0,1 if i==2 else 0,0))
            self.put(used,struct.pack('<HHII',0,1 if i==2 else 0,0,1))
            self.put(avail+4+2*n,struct.pack('<H',0))
            self.put(used+4+8*n,struct.pack('<H',1 if i==2 else 0))
        desc=self.queues[2]['vring-desc']
        for i,(length,flags,next_index) in enumerate(((2,1,1),(1,1,2),(1,2,65535))):
            self.put(desc+16*i,struct.pack('<QIHH',net.RAM_BASE+0x40000+16*i,length,flags,next_index))
        self.put(net.RAM_BASE+0x40000,b'\0\1')
        self.put(net.RAM_BASE+0x40020,b'\0')
    def put(self,address,data):
        self.memory.update((address+i,b) for i,b in enumerate(data))
    def physical_read(self,address,count,width):
        size=count*width;self.reads.append((address,size,width))
        start,length=self.forbidden
        if address<start+length and start<address+size:
            raise AssertionError('Read would access type5 selector data')
        return bytes(self.memory[address+i] for i in range(size))
    def _request(self,command,arguments=None):
        self.calls.append((command,arguments))
        if command=='query-status':return {'status':'running','running':True}
        if command=='query-pci':return [{'bus':0,'devices':[self.device]}]
        if command=='x-query-virtio-status':return self.status
        if command=='x-query-virtio-queue-status':return self.queues[arguments['queue']]
        raise AssertionError(command)


class Tests(unittest.TestCase):
    def test_live_layout_fixture_captures_ack_and_msix_without_selector_data(self):
        fixture=Fixture();result=net.collect(fixture)
        self.assertEqual(result['state'],'observed')
        self.assertEqual(result['control']['request_name'],'RX_ALLMULTI')
        self.assertEqual(result['control']['ack_byte'],0)
        self.assertEqual(result['control']['used_length'],1)
        self.assertTrue(result['ring_headers'][2]['avail_used_regions_overlap'])
        self.assertEqual(result['ring_headers'][2]['used_event']['value'],0)
        pci=result['pci']
        self.assertTrue(pci['msix']['enabled']);self.assertFalse(pci['msix']['function_mask'])
        self.assertEqual(pci['common_config']['queue_msix_vector'],3)
        self.assertTrue(pci['common_config']['control_vector_known'])
        self.assertFalse(pci['msix']['entries'][3]['masked'])
        self.assertEqual(pci['msix']['pending_bits'],'0x0')
        self.assertLess(result['physical_bytes_read'],1024)
        self.assertTrue(all(size<=256 for _,size,_ in fixture.reads))
        self.assertFalse(any('element' in command or command in ('stop','cont') for command,_ in fixture.calls))

    def test_mismatched_pci_and_bad_capability_do_not_read_bars_or_rings(self):
        fixture=Fixture();fixture.device['id']['device']=0x1000
        self.assertEqual(net.collect(fixture)['state'],'unknown')
        self.assertEqual(fixture.reads,[])
        fixture=Fixture()
        fixture.put(fixture.ecam+0x85,b'\x94')  # next points inside type5 data
        result=net.collect(fixture)
        self.assertEqual(result['state'],'unknown')
        self.assertTrue(all(fixture.ecam<=address<fixture.ecam+256 for address,_,_ in fixture.reads))

    def test_optional_subsystem_fields_do_not_replace_exact_owned_net_identity(self):
        fixture=Fixture()
        result=net.collect(fixture)
        self.assertEqual(result['state'],'observed')
        self.assertEqual(result['pci_identity']['id']['subsystem'],0x1100)
        for key,value in (('qdev_id','other-device'),('class_info',{'class':0x0401}),
                          ('id',{'vendor':0x1af4,'device':0x1000})):
            fixture=Fixture();fixture.device[key]=value
            with self.subTest(key=key):
                self.assertEqual(net.collect(fixture)['state'],'unknown')
                self.assertEqual(fixture.reads,[])

    def test_unassigned_bar_and_large_msix_table_are_explicitly_unknown(self):
        fixture=Fixture();fixture.regions[0]['address']=0
        self.assertEqual(net.collect(fixture)['state'],'unknown')
        fixture=Fixture();fixture.put(fixture.ecam+0x9a,struct.pack('<H',0x800f))
        result=net.collect(fixture)
        self.assertEqual(result['pci']['msix']['state'],'unknown')
        self.assertFalse(any(0x50001000<=a<0x50002000 for a,_,_ in fixture.reads))

    def test_ram_range_budget_and_width_checks_precede_reads(self):
        fixture=Fixture();reads=net.MemoryReads(fixture,4*1024**3)
        for address,size,width in ((0x20000000,4,1),(net.RAM_BASE,257,1),
                                   (net.RAM_BASE+1,2,2),(net.RAM_BASE+4*1024**3,1,1)):
            with self.subTest(address=address),self.assertRaises(ValueError):
                reads.read(address,size,width=width,purpose='bad range')
        reads.total=net.MAX_READ_BYTES
        with self.assertRaises(ValueError):reads.read(net.RAM_BASE,1,purpose='budget')
        self.assertEqual(fixture.reads,[])

    def test_vhost_does_not_reach_queue_queries_or_memory(self):
        fixture=Fixture();fixture.status['vhost-started']=True
        self.assertEqual(net.collect(fixture)['state'],'unknown')
        self.assertEqual(fixture.reads,[])
        self.assertFalse(any(c=='x-query-virtio-queue-status' for c,_ in fixture.calls))

    def test_indirect_chain_and_rejections(self):
        fixture=Fixture();queue=fixture.queues[2];table=net.RAM_BASE+0x50000
        fixture.put(queue['vring-desc'],struct.pack('<QIHH',table,32,4,0))
        fixture.put(table,struct.pack('<QIHH',net.RAM_BASE+0x40000,2,1,1))
        fixture.put(table+16,struct.pack('<QIHH',net.RAM_BASE+0x40020,1,2,0))
        self.assertEqual(net.collect(fixture)['control']['ack_meaning'],'OK')
        fixture.put(table+16,struct.pack('<QIHH',table,32,4,0))
        self.assertEqual(net.collect(fixture)['control']['state'],'unknown')
        fixture.put(queue['vring-desc'],struct.pack('<QIHH',0x20000000,2,0,0))
        self.assertEqual(net.collect(fixture)['control']['state'],'unknown')

    def test_reused_completed_head_does_not_invent_an_ack(self):
        fixture=Fixture();queue=fixture.queues[2]
        fixture.put(queue['vring-used']+4,struct.pack('<II',2,1))
        fixture.put(queue['vring-desc']+32,struct.pack('<QIHH',net.RAM_BASE+0x40000,2,0,65535))
        control=net.collect(fixture)['control']
        self.assertEqual(control['used_head'],2)
        self.assertEqual(control['request_header'],[0,1])
        self.assertIsNone(control['ack_byte'])
        self.assertEqual(control['ack_meaning'],'unknown')

    def test_xp_requires_exact_addresses_count_and_width(self):
        self.assertEqual(net.parse_xp('1000: 0x1234 0xabcd\r\n',0x1000,2,2),bytes.fromhex('3412cdab'))
        for text in ('1002: 0x1234 0xabcd','1000: 0x12 0xabcd','1000: 0x1234','error'):
            with self.subTest(text=text),self.assertRaises(ValueError):net.parse_xp(text,0x1000,2,2)

    def test_read_transport_rejects_mutation_and_rechecks_binding(self):
        client=object.__new__(net.ReadOnlyNet)
        client.verify_process=Mock();client.verify_peer=Mock();client.journal=Mock();client.sock=Mock()
        for command,args in (('stop',None),('cont',None),('input-send-event',{}),
                             ('x-query-virtio-queue-element',{'path':net.NET_PATH,'queue':2}),
                             ('human-monitor-command',{'command-line':'xp /1bx 0x1000'})):
            with self.subTest(command=command),self.assertRaises(net.UIError):client._request(command,args)
        client.sock.sendall.assert_not_called()
        client.verify_process.side_effect=net.UIError('stale process')
        with self.assertRaises(net.UIError):client._request('query-pci')
        client.sock.sendall.assert_not_called()


if __name__=='__main__':unittest.main(verbosity=2)
