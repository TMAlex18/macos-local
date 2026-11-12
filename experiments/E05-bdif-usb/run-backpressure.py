#!/usr/bin/env python3
"""Stop reading TX, query QMP during backpressure, then validate all frames."""
import argparse,json,os,selectors,socket,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
from run import ROOT,build_case,pattern,frame
p=argparse.ArgumentParser();p.add_argument('--candidate',action='store_true');p.add_argument('--label');args=p.parse_args()
label=args.label or ('candidate' if args.candidate else 'corrected')
if not __import__('re').fullmatch(r'[A-Za-z0-9_-]+',label):raise SystemExit('Invalid run label')
work=ROOT/'build/e05-bdif-usb/pressure';work.mkdir(parents=True,exist_ok=True)
bin=ROOT/'build/qemu-bdif-usb'/('qemu-system-aarch64.candidate' if args.candidate else 'qemu-system-aarch64')
firmware=build_case(work,11,4096)
for name,size in [('aux.raw',4*1024*1024),('root.raw',32*1024*1024)]:
 f=work/name
 if not f.exists():
  with f.open('wb') as h:h.truncate(size)
us=work/'u';qs=work/'q'
for path in (us,qs):
 if path.exists():path.unlink()
command=[str(bin),'-object','memory-backend-memfd,id=ram,size=512M,share=on',
 '-M','vmapple,memory-backend=ram','-cpu','max,cntfrq=24000000,x-cntfrq-exact=on',
 '-m','512M','-smp','1','-accel','tcg','-nodefaults','-nic','none','-display','none',
 '-S','-nographic','-monitor','none','-serial','stdio','-semihosting-config','enable=on,target=native','-bios',str(firmware),
 '-drive',f'file={work/"aux.raw"},format=raw,if=pflash,index=0',
 '-drive',f'file={work/"root.raw"},format=raw,if=pflash,index=1',
 '-qmp',f'unix:{qs},server=on,wait=off',
 '-chardev',f'socket,id=usbr,path={us},server=on,wait=on','-global','vmapple-bdif.usbdev=usbr']
errfile=(work/f'{label}.stderr').open('wb')
proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=errfile,bufsize=0,env=dict(os.environ,REIMS_VGPU_WINDOW='0',REIMS_VGPU_LOG='warn'))
u=q=None;responsive=False;wire=bytearray();uart=bytearray();error=None;phase='startup';sel=selectors.DefaultSelector()
try:
 until=time.monotonic()+4
 while not us.exists() and proc.poll() is None and time.monotonic()<until:time.sleep(.01)
 u=socket.socket(socket.AF_UNIX);u.settimeout(2);u.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,8192);u.connect(str(us))
 while not qs.exists() and proc.poll() is None:time.sleep(.01)
 q=socket.socket(socket.AF_UNIX);q.settimeout(15);q.connect(str(qs))
 phase='qmp_greeting'
 greeting=q.recv(8192)
 phase='qmp_capabilities'
 q.sendall(b'{"execute":"qmp_capabilities"}\n');reply=q.recv(8192)
 phase='guest_backpressure'
 q.sendall(b'{"execute":"cont"}\n')
 # Let the guest send > socket capacity while this process reads zero USB bytes.
 time.sleep(.18)
 q.settimeout(.25)
 q.sendall(b'{"execute":"query-status","id":"pressure"}\n')
 response=b'';limit=time.monotonic()+.25
 while time.monotonic()<limit and not responsive:
  q.settimeout(max(.001,limit-time.monotonic()))
  try:response+=q.recv(8192)
  except socket.timeout:break
  for line in response.splitlines():
   try:message=json.loads(line)
   except ValueError:continue
   if message.get('id')=='pressure' and 'return' in message:responsive=True

 phase='drain_frames'
 sel.register(u,selectors.EVENT_READ,'usb');sel.register(proc.stdout,selectors.EVENT_READ,'uart')
 until=time.monotonic()+5
 while time.monotonic()<until:
  for key,_ in sel.select(.05):
   data=os.read(key.fileobj.fileno(),262144)
   if not data:sel.unregister(key.fileobj);continue
   (wire if key.data=='usb' else uart).extend(data)
  if not sel.get_map() and proc.poll() is not None:break
 if proc.poll() is None:raise TimeoutError('guest transfer not drained')
 proc.wait(timeout=1)
except Exception as e:error=repr(e)
finally:
 if proc.poll() is None:proc.kill();proc.wait()
 for s in (u,q):
  if s:s.close()
 sel.close();proc.stdout.close();errfile.close()
expected=b''.join(frame(bytes(x^0xa5 for x in pattern(4096,79+(i%100)))) for i in range(256))
record={'time':datetime.now(timezone.utc).isoformat(),'label':label,'qmp_responsive_during_tx_backpressure':responsive,
 'guest_exit':proc.returncode,'wire_bytes':len(wire),'expected_bytes':len(expected),'payload_exact':wire==expected,
 'uart':uart.decode(errors='replace'),'host_error':error,'last_phase':phase}
record['passed']=responsive and error is None and proc.returncode==0 and wire==expected and b'USB_GUEST_PASS' in uart
out=ROOT/f'runs/e05-bdif-usb/{label}-backpressure.json';out.write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record,indent=2));raise SystemExit(0 if record['passed'] else 1)
