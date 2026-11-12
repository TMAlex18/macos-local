#!/usr/bin/env python3
"""Real guest BDIF MMIO + local Unix peer; no USB/IP or Apple firmware.

The peer injects specified frames; only guest code validates RAM, sentinels,
completion and TX response bytes. Each case uses a fresh QEMU process.
"""
import argparse
from datetime import datetime,timezone
import json,os,selectors,socket,struct,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
RUNTIME=ROOT/'experiments/E06-platform-contracts'
CASES={
 'small':(0,33), 'fragmented':(0,65), 'coalesced':(1,127),
 'large-rx-capacity':(14,33),
 'queue-control':(13,64),
 'maximum':(0,131072), 'descriptor-boundary':(2,64),
 'oversize-for-buffer':(3,32),'preposted-data':(4,80),
 'no-data':(5,16),'invalid-data-address':(6,32),
 'oversize-tx':(7,32),'maximum-tx':(8,4096),
 'disconnect-partial':(9,33),'invalid-descriptor':(10,32),
 'zero-length':(0,0), 'oversize-frame-reconnect':(9,33), 'root-aux-regression':(12,32),
}
def build_case(work,number,length):
 objects=[]
 for name in ['start.S','runtime.c','guest.c']:
  src=(HERE if name=='guest.c' else RUNTIME)/name
  obj=work/(name.replace('.','_')+'.o')
  subprocess.run(['clang','--target=aarch64-none-elf','-O2','-ffreestanding',
      '-fno-builtin','-fno-stack-protector','-fno-pic','-fno-pie',
      '-mgeneral-regs-only','-Wall','-Wextra','-I',str(RUNTIME),
      f'-DTEST_CASE={number}',f'-DTEST_LENGTH={length}','-c',str(src),'-o',str(obj)],check=True,capture_output=True)
  objects.append(str(obj))
 elf=work/'guest.elf';bin=work/'guest.bin'
 subprocess.run(['ld.lld','-T',str(RUNTIME/'link.ld'),*objects,'-o',str(elf)],check=True)
 subprocess.run(['llvm-objcopy','-O','binary',str(elf),str(bin)],check=True)
 return bin

def pattern(n,salt=79):return bytes((i*17+salt)&255 for i in range(n))
def frame(b):return struct.pack('<I',len(b))+b

def run_case(binary,name,number,length,out,phase):
 work=ROOT/'build/e05-bdif-usb'/name;work.mkdir(parents=True,exist_ok=True)
 firmware=build_case(work,number,length)
 for file,size in [('aux.raw',4*1024*1024),('root.raw',32*1024*1024)]:
  p=work/file
  if not p.exists():
   with p.open('wb') as f:f.truncate(size)
 if number==12:
  for fname,offset,salt in [('root.raw',512,61),('aux.raw',1024,99)]:
   with (work/fname).open('r+b') as f:f.seek(offset);f.write(pattern(64,salt))
 # Stay below Linux Unix-socket path length limit.
 sock_path=work/'u.sock'
 if sock_path.exists():sock_path.unlink()
 args=[str(binary),'-object','memory-backend-memfd,id=ram,size=512M,share=on',
  '-M','vmapple,memory-backend=ram','-cpu','max,cntfrq=24000000,x-cntfrq-exact=on',
  '-accel','tcg','-m','512M','-smp','1','-nodefaults','-nic','none','-display','none',
  '-nographic','-monitor','none','-serial','stdio',
  '-semihosting-config','enable=on,target=native','-bios',str(firmware),
  '-drive',f'file={work/"aux.raw"},format=raw,if=pflash,index=0',
  '-drive',f'file={work/"root.raw"},format=raw,if=pflash,index=1',
  '-chardev',f'socket,id=usbr,path={sock_path},server=on,wait=on',
  '-global','vmapple-bdif.usbdev=usbr']
 stderr=work/f'{phase}.stderr'
 peer=None;sel=selectors.DefaultSelector();serial=b'';wire=b'';done=False;timeout=False;host_error=None
 with stderr.open('wb') as err:
  proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=err,bufsize=0,env=dict(os.environ,REIMS_VGPU_WINDOW='0',REIMS_VGPU_LOG='warn'),start_new_session=True)
  try:
   deadline=time.monotonic()+8
   while not sock_path.exists() and proc.poll() is None and time.monotonic()<deadline:time.sleep(.01)
   peer=socket.socket(socket.AF_UNIX);peer.settimeout(2);peer.connect(str(sock_path))
   sel.register(proc.stdout,selectors.EVENT_READ,'serial');sel.register(peer,selectors.EVENT_READ,'usb')
   sent=False
   while time.monotonic()<deadline:
    for key,_ in sel.select(.05):
     b=os.read(key.fileobj.fileno(),262144)
     if not b:
      try:sel.unregister(key.fileobj)
      except KeyError:pass
      continue
     if key.data=='usb':wire+=b
     else:
      serial+=b
      trigger=(b'USB_PREPOST_WINDOW' if number==4 else b'USB_RECONNECT_WINDOW' if number==9 else b'USB_RX_READY')
      if not sent and trigger in serial:
       sent=True
       payload=pattern(length)
       if number==5:pass
       elif number==1:peer.sendall(frame(payload)+frame(pattern(length,113)))
       elif number==3:peer.sendall(frame(pattern(length+9))+frame(payload))
       elif number==9:
        peer.sendall(struct.pack('<I',131073) if name=='oversize-frame-reconnect' else struct.pack('<I',48)+b'PART');sel.unregister(peer);peer.close()
        time.sleep(.06);peer=socket.socket(socket.AF_UNIX);peer.settimeout(2);peer.connect(str(sock_path));sel.register(peer,selectors.EVENT_READ,'usb');peer.sendall(frame(payload))
       elif name=='fragmented':
        blob=frame(payload)
        for start in range(0,len(blob),3):peer.sendall(blob[start:start+3]);time.sleep(.001)
       else:peer.sendall(frame(payload))
    if proc.poll() is not None:
     # drain any final buffers instead of racing UART vs socket delivery.
     for _ in range(3):
      events=sel.select(.02)
      for key,_ in events:
       b=os.read(key.fileobj.fileno(),262144)
       if key.data=='usb':wire+=b
       else:serial+=b
     break
   if proc.poll() is None:timeout=True;proc.kill()
   proc.wait(timeout=3)
  except Exception as ex:
   host_error=repr(ex)
   if proc.poll() is None:proc.kill()
   proc.wait(timeout=3)
  finally:
   sel.close()
   if peer:peer.close()
   proc.stdout.close()
   if sock_path.exists():sock_path.unlink()
 uart=serial.decode(errors='replace')
 expect_rc=1 if number==5 else 0
 if number==5:expected_wire=b'';mark='USB_NO_DATA_EXPECTED_TIMEOUT'
 else:
  n=4096 if number==8 else min(length,4096)
  salt=113 if number==1 else 79
  expected_wire=frame(bytes(x^0xa5 for x in pattern(n,salt)));mark='USB_GUEST_PASS'
 ok=not timeout and host_error is None and proc.returncode==expect_rc and mark in uart and wire==expected_wire
 result={'case':name,'guest_exit':proc.returncode,'expected_exit':expect_rc,'host_timeout':timeout,
  'host_error':host_error,'passed':ok,'wire_bytes':len(wire),'expected_wire_bytes':len(expected_wire),
  'wire_exact_match':wire==expected_wire,'uart':uart,'phase':phase,'binary':str(binary.relative_to(ROOT))}
 (out/f'{phase}-{name}.json').write_text(json.dumps(result,indent=2)+'\n')
 (out/f'{phase}-{name}.log').write_text(uart+'\n=== STDERR ===\n'+stderr.read_text(errors='replace'))
 print(f'{phase}/{name}: {"PASS" if ok else "FAIL"} rc={proc.returncode} wire={len(wire)}/{len(expected_wire)} host_error={host_error}',flush=True)
 return result

def main():
 p=argparse.ArgumentParser();p.add_argument('--phase',default='candidate');p.add_argument('--cases',default='all');p.add_argument('--binary',default='build/qemu-bdif-usb/qemu-system-aarch64');args=p.parse_args()
 out=ROOT/'runs/e05-bdif-usb';out.mkdir(parents=True,exist_ok=True)
 binary=ROOT/args.binary
 names=list(CASES) if args.cases=='all' else args.cases.split(',')
 res=[run_case(binary,name,*CASES[name],out,args.phase) for name in names]
 report={'at_utc':datetime.now(timezone.utc).isoformat(),'scope':'guest BDIF descriptor + Unix framing; no Apple boot, USB/IP enumeration or DFU restore','passed':all(x['passed'] for x in res),'cases':res}
 (out/f'{args.phase}-results.json').write_text(json.dumps(report,indent=2)+'\n')
 print(f'{sum(x["passed"] for x in res)}/{len(res)} expectations met')
 return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
