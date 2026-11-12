#!/usr/bin/env python3
"""USB/IP client -> gateway -> BDIF -> actual self-authored ARM guest.

No localhost TCP listener, vhci, root privilege or Apple code; socketpair
exercises wire protocol in user space. No restore progress is inferred.
"""
import errno,json,os,select,socket,struct,subprocess,threading,time
from pathlib import Path
from datetime import datetime,timezone
from usbip_control import AVPControl,read_exact,serve
from run import ROOT,RUNTIME
HERE=Path(__file__).resolve().parent
BUILD=ROOT/'build/e05-bdif-usb/control';BUILD.mkdir(parents=True,exist_ok=True)
objects=[]
for name in ['start.S','runtime.c','control-guest.c']:
 obj=BUILD/(name.replace('.','_')+'.o');src=(HERE if name=='control-guest.c' else RUNTIME)/name
 subprocess.run(['clang','--target=aarch64-none-elf','-O2','-ffreestanding','-fno-builtin','-fno-stack-protector',
  '-mgeneral-regs-only','-I',str(RUNTIME),'-c',str(src),'-o',str(obj)],check=True,capture_output=True)
 objects.append(str(obj))
elf=BUILD/'guest.elf';fw=BUILD/'guest.bin'
subprocess.run(['ld.lld','-T',str(RUNTIME/'link.ld'),*objects,'-o',str(elf)],check=True)
subprocess.run(['llvm-objcopy','-O','binary',str(elf),str(fw)],check=True)
for name,size in [('aux.raw',4*1024*1024),('root.raw',32*1024*1024)]:
 p=BUILD/name
 if not p.exists():
  with p.open('wb') as f:f.truncate(size)
results=[]
for scenario in ['roundtrip','malformed','timeout']:
 path=BUILD/'u.sock'
 if path.exists():path.unlink()
 binary=ROOT/'build/qemu-bdif-usb/qemu-system-aarch64'
 args=[str(binary),'-object','memory-backend-memfd,id=ram,size=512M,share=on',
  '-M','vmapple,memory-backend=ram','-cpu','max,cntfrq=24000000,x-cntfrq-exact=on','-accel','tcg','-m','512M','-smp','1',
  '-nodefaults','-nic','none','-display','none','-nographic','-monitor','none','-serial','stdio',
  '-semihosting-config','enable=on,target=native','-bios',str(fw),
  '-drive',f'file={BUILD/"aux.raw"},format=raw,if=pflash,index=0','-drive',f'file={BUILD/"root.raw"},format=raw,if=pflash,index=1',
  '-chardev',f'socket,id=usbr,path={path},server=on,wait=on','-global','vmapple-bdif.usbdev=usbr']
 events=[];errors=[];checks=[]
 stderr=(BUILD/f'{scenario}.stderr').open('wb')
 qemu=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=stderr,env=dict(os.environ,REIMS_VGPU_WINDOW='0',REIMS_VGPU_LOG='warn'))
 a=b=avp=None;thread=None;out=b'';prefix=b''
 try:
  deadline=time.monotonic()+4
  while not path.exists() and qemu.poll() is None and time.monotonic()<deadline:time.sleep(.01)
  avp=socket.socket(socket.AF_UNIX);avp.connect(str(path))
  if not select.select([qemu.stdout],[],[],4)[0]:raise TimeoutError('guest did not reach synthetic control loop')
  prefix=qemu.stdout.readline()
  if prefix!=b'CONTROL_GUEST_START\n':raise RuntimeError(f'unexpected firmware startup: {prefix!r}')
  a,b=socket.socketpair();a.settimeout(4);b.settimeout(4)
  # Initial descriptor query can include QEMU startup; avoid confusing host
  # renderer setup latency with the short subsequent control timeout.
  transport=AVPControl(avp,timeout=2)
  def bridge():
   try:serve(b,transport,events)
   except Exception as e:errors.append(repr(e))
   finally:
    b.close()
  thread=threading.Thread(target=bridge,name='synthetic-usbip',daemon=True);thread.start()
  a.sendall(struct.pack('!HHI',0x111,0x8003,0)+b'1-1'.ljust(32,b'\0'))
  imported=read_exact(a,320)
  checks.append(('identity_from_guest',struct.unpack_from('!HH',imported,8+300)==(0xf00d,0xb00b)))
  transport.sock.settimeout(.3)
  seq=0
  def control(bm,req,value=0,length=0,data=b''):
   global seq
   seq+=1;direction=1 if bm&0x80 else 0;setup=struct.pack('<BBHHH',bm,req,value,0,length)
   a.sendall(struct.pack('!IIIII',1,seq,0x10001,direction,0)+struct.pack('!Iiiii',0,length,0,0,0)+setup+data)
   header=read_exact(a,48);command,rseq=struct.unpack_from('!II',header)
   status,n=struct.unpack_from('!ii',header,20)
   assert command==3 and rseq==seq and 0<=n<=4094
   payload=read_exact(a,n) if direction==1 and n else b''
   return status,n,payload
  if scenario=='roundtrip':
   r=control(0x80,6,0x100,18);checks.append(('device_descriptor',r[0]==0 and r[1]==18 and r[2][:2]==b'\x12\x01'))
   data=bytes((i*23+19)&255 for i in range(64))
   r=control(0x40,0x5a,length=64,data=data);checks.append(('control_out',r==(0,64,b'')))
   r=control(0xc0,0x5b,length=64);checks.append(('guest_stored_out_and_returned',r==(0,64,data)))
   r=control(0xa1,3,length=6);checks.append(('guest_supplied_status',r==(0,6,b'\0\0\0\0\2\0')))
   r=control(0xa1,3,value=1,length=6);checks.append(('empty_status_not_fabricated',r==(0,0,b'')))
   r=control(0xc0,0x99,length=1);checks.append(('guest_stall_preserved',r==(-errno.EPIPE,0,b'')))
   r=control(0x40,0xfe);checks.append(('guest_shutdown',r==(0,0,b'')))
   qemu.wait(timeout=3)
  elif scenario=='malformed':
   r=control(0xc0,0x97,length=1);checks.append(('malformed_guest_reply_error',r==(-errno.EPROTO,0,b'')))
  else:
   r=control(0xa1,3,value=2,length=6);checks.append(('silent_status_is_timeout_not_success',r==(-errno.ETIMEDOUT,0,b'')))
 except Exception as e:errors.append(repr(e))
 finally:
  if a:a.close()
  if thread:thread.join(timeout=1)
  if avp:avp.close()
  if qemu.poll() is None:qemu.terminate()
  try:out,_=qemu.communicate(timeout=2)
  except subprocess.TimeoutExpired:qemu.kill();out,_=qemu.communicate()
  stderr.close()
 out=prefix+out
 ok=all(v for k,v in checks) and not errors and not(thread and thread.is_alive())
 if scenario=='roundtrip':ok=ok and qemu.returncode==0 and b'CONTROL_GUEST_PASS' in out
 report={'scenario':scenario,'passed':ok,'checks':dict(checks),'errors':errors,'events':events,
  'guest_exit':qemu.returncode,'guest_terminated_after_negative':scenario!='roundtrip','uart':out.decode(errors='replace')}
 (ROOT/f'runs/e05-bdif-usb/control-{scenario}.json').write_text(json.dumps(report,indent=2)+'\n')
 print(scenario, 'PASS' if ok else 'FAIL',dict(checks),errors,flush=True);results.append(report)
summary={'at_utc':datetime.now(timezone.utc).isoformat(),'scope':'sequential USB/IP control in user-space, synthetic guest only; no real enumeration or Apple DFU restoration',
 'passed':all(x['passed'] for x in results),'scenarios':results}
(ROOT/'runs/e05-bdif-usb/control-results.json').write_text(json.dumps(summary,indent=2)+'\n')
raise SystemExit(0 if summary['passed'] else 1)
