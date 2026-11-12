#!/usr/bin/env python3
"""Small VM-local setup and desktop input helper with immediate QMP receipts."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
PROC_ROOT = Path('/proc')
QEMU = ROOT / 'build/qemu-bdif-usb/qemu-system-aarch64'
KEYS = set('abcdefghijklmnopqrstuvwxyz0123456789') | {
    'tab','ret','esc','backspace','delete','left','right','up','down','home','end','spc',
    'shift','ctrl','alt','meta_l','minus','equal','bracket_left','bracket_right',
    'backslash','semicolon','apostrophe','grave_accent','comma','dot','slash'}
CHORDS = {('meta_l','alt','esc'), ('meta_l','a'), ('meta_l','n'), ('meta_l','spc'), ('shift','tab'),
          ('ctrl','c')}  # Interrupt a foreground diagnostic in the selected guest Terminal.
PUNCTUATION = {' ':'spc','-':'minus','=':'equal','[':'bracket_left',']':'bracket_right',
    '\\':'backslash',';':'semicolon',"'":'apostrophe','`':'grave_accent',',':'comma','.':'dot','/':'slash'}
SHIFTED = dict(zip('~!@#$%^&*()_+{}|:"<>?', "`1234567890-=[]\\;',./"))


INPUT_RETRY_SECONDS = 2.0
INPUT_POLL_SECONDS = .02
KEYBOARD_DRAIN_SECONDS = 5.0
KEYBOARD_DRAIN_READ_LIMIT = 4*1024*1024
KEY_RELEASE_INTERVAL = .4
MODIFIER_KEYS = ('shift','ctrl','alt','meta_l')
KEYBOARD_TRACE_EVENTS = ('hid_kbd_queue_full','hid_kbd_queue_empty')
SAFE_FAILURE_CLASSES = {'ui_error','qmp_rejected','vm_not_running','vm_paused_timeout','input_retry_timeout',
                        'vm_state_unavailable','transport_unknown','binding_rejected','local_error',
                        'keyboard_queue_full','keyboard_drain_timeout','keyboard_trace_unavailable'}


class UIError(RuntimeError):
    def __init__(self,message,*,failure_class='ui_error',release_confirmed=None):
        super().__init__(message)
        self.failure_class=failure_class if failure_class in SAFE_FAILURE_CLASSES else 'local_error'
        self.release_confirmed=release_confirmed


def deadline_error(failure_class):
    if failure_class=='vm_paused_timeout':
        return UIError('VM stayed paused; rejected input was not accepted',failure_class='vm_paused_timeout')
    return UIError('Input retry deadline reached before sending',failure_class='input_retry_timeout')

class InputNotRunning(UIError):
    def __init__(self):
        super().__init__('VM rejected input while not running',failure_class='vm_not_running')
        self.rejected_at=time.monotonic()


def failure_class(error):
    return error.failure_class if isinstance(error,UIError) else 'local_error'


def timestamp(): return datetime.now(timezone.utc).isoformat()


def owned_regular(path, *, private=False, maximum=8*1024*1024):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
            path.resolve(strict=True) != path.absolute() or info.st_size > maximum or
            (private and stat.S_IMODE(info.st_mode) != 0o600)):
        raise UIError('Input file ownership, mode or path is invalid')
    return info


class Journal:
    def __init__(self, directory):
        self.path = directory / 'ui-actions.jsonl'
        self.fd = os.open(self.path, os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600)
        info = os.fstat(self.fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            os.close(self.fd)
            raise UIError('UI journal must be an owned 0600 regular file')
        self.session = uuid.uuid4().hex
    def write(self, **fields):
        raw = (json.dumps({'at_utc':timestamp(), 'monotonic':time.monotonic(),
                          'session':self.session, **fields})+'\n').encode()
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        try:
            cursor = 0
            while cursor < len(raw): cursor += os.write(self.fd, raw[cursor:])
            os.fsync(self.fd)
        finally: fcntl.flock(self.fd, fcntl.LOCK_UN)
    def close(self): os.close(self.fd)


class KeyboardDrain:
    """Public-input diagnostic: bounded tail of two payload-free HID events."""
    def __init__(self,ui):
        self.ui,self.fd,self.saved,self.offset=ui,None,{},0
        self.path=ui.directory/'bdif-block-io.log'

    def start(self):
        traces=[self.ui.args[i+1] for i,arg in enumerate(self.ui.args[:-1]) if arg=='-trace']
        if not any('file='+str(self.path) in value.split(',') for value in traces):
            raise UIError('Run has no bound keyboard trace destination',failure_class='keyboard_trace_unavailable')
        info=owned_regular(self.path,maximum=(1<<63)-1)
        self.fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        current=os.fstat(self.fd);self.identity=(current.st_dev,current.st_ino)
        if self.identity!=(info.st_dev,info.st_ino):
            raise UIError('Keyboard trace identity changed',failure_class='keyboard_trace_unavailable')
        self.offset=current.st_size
        for name in KEYBOARD_TRACE_EVENTS:
            reply=self.ui._request('trace-event-get-state',{'name':name})
            if (not isinstance(reply,list) or len(reply)!=1 or reply[0].get('name')!=name or
                    reply[0].get('state') not in ('enabled','disabled')):
                raise UIError('Keyboard trace event unavailable',failure_class='keyboard_trace_unavailable')
            self.saved[name]=reply[0]['state']=='enabled'
        for name in self.saved:
            self.ui._request('trace-event-set-state',{'name':name,'enable':True})
        self.ui.journal.write(kind='keyboard_drain',phase='enabled',original_states=self.saved,
                              scope='Public keys only; queue drain is not guest application execution')

    def mark(self):
        try:info=self.path.lstat();current=os.fstat(self.fd)
        except OSError:
            raise UIError('Keyboard trace unavailable',failure_class='keyboard_trace_unavailable') from None
        if (not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino)!=self.identity or
                (current.st_dev,current.st_ino)!=self.identity or current.st_size<self.offset):
            raise UIError('Keyboard trace changed or truncated',failure_class='keyboard_trace_unavailable')
        self.offset=current.st_size
        return self.offset

    def wait(self,start):
        deadline=time.monotonic()+KEYBOARD_DRAIN_SECONDS
        cursor=start;pending=b'';empty=False;full=False
        while True:
            self.ui.verify_process()
            end=self.mark()
            if end-start>KEYBOARD_DRAIN_READ_LIMIT:
                raise UIError('Keyboard trace read budget exceeded',failure_class='keyboard_trace_unavailable')
            while cursor<end:
                try:block=os.pread(self.fd,min(65536,end-cursor),cursor)
                except OSError:
                    raise UIError('Keyboard trace read unavailable',failure_class='keyboard_trace_unavailable') from None
                if not block:raise UIError('Keyboard trace read unavailable',failure_class='keyboard_trace_unavailable')
                cursor+=len(block);pending+=block
                lines=pending.split(b'\n');pending=lines.pop()
                for line in lines:
                    full|=line.endswith(b'hid_kbd_queue_full queue full')
                    empty|=line.endswith(b'hid_kbd_queue_empty queue empty')
            # Inspect the entire appended snapshot: a later full must not be hidden by an empty.
            if full or empty:
                self.ui.journal.write(kind='keyboard_drain',phase='after',status='overflow' if full else 'drained',
                                      start_offset=start,end_offset=cursor)
                if full:raise UIError('HID keyboard queue dropped an event',failure_class='keyboard_queue_full')
                return
            if time.monotonic()>=deadline:
                self.ui.journal.write(kind='keyboard_drain',phase='after',status='timeout',
                                      start_offset=start,end_offset=cursor)
                raise UIError('HID keyboard drain unconfirmed',failure_class='keyboard_drain_timeout')
            time.sleep(min(INPUT_POLL_SECONDS,max(0,deadline-time.monotonic())))

    def close(self):
        failed=False
        try:
            for name,enabled in self.saved.items():
                try:self.ui._request('trace-event-set-state',{'name':name,'enable':enabled})
                except UIError:failed=True
            if self.saved:self.ui.journal.write(kind='keyboard_drain',phase='restore',
                status='unknown' if failed else 'restored',original_states=self.saved)
        finally:
            self.saved.clear()
            if self.fd is not None:os.close(self.fd);self.fd=None
        if failed:raise UIError('Keyboard trace restoration unconfirmed',failure_class='keyboard_trace_unavailable')


class GuestUI:
    def __init__(self, run, *, timeout=5,keyboard_drain=False):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', run): raise UIError('Invalid run label')
        self.directory = (ROOT/'runs/e05-installed-boot'/run).absolute()
        self.result_path = self.directory/'results.json'
        self.timeout, self.sock, self.stream, self.usable = timeout, None, None, False
        self.keyboard_drain,self.drain=keyboard_drain,None
        owned_regular(self.result_path)
        report = json.loads(self.result_path.read_text())
        self.pid, self.args = report.get('qemu_pid'), report.get('execution',{}).get('qemu_argv')
        if type(self.pid) is not int or self.pid <= 1 or not isinstance(self.args,list) or not self.args:
            raise UIError('Run has no recorded owned QEMU process')
        if self.args[0] != str(QEMU) or f'file:{self.directory}/firmware-uart.log' not in self.args:
            raise UIError('Run executable or serial-output binding differs')
        endpoints = [self.args[i+1].split(',',1)[0][5:] for i,value in enumerate(self.args[:-1])
                     if value=='-qmp' and self.args[i+1].startswith('unix:')
                     and self.args[i+1].split(',',1)[0].endswith('/inspect.sock')]
        if len(endpoints)!=1: raise UIError('Run has no unique local inspection endpoint')
        self.endpoint = Path(endpoints[0])
        if not self.endpoint.is_relative_to(ROOT/'build') or not self.endpoint.parent.name.startswith('kernel-usb-'):
            raise UIError('Inspection endpoint is outside the owned temporary session')
        self.start_ticks = None
        self.verify_process()
        self.journal = Journal(self.directory)

    def verify_process(self):
        try:
            report = json.loads(self.result_path.read_text())
            process = PROC_ROOT/str(self.pid)
            fields = (process/'stat').read_text().rsplit(')',1)[1].split()
            ticks = int(fields[19])
            uid = re.search(r'^Uid:\s+(\d+)\s+(\d+)',(process/'status').read_text(),re.M)
            if (report.get('state')!='observing' or report.get('qemu_pid')!=self.pid or
                    report.get('execution',{}).get('qemu_argv')!=self.args or
                    (process/'exe').resolve(strict=True)!=QEMU.resolve(strict=True) or
                    (process/'cmdline').read_bytes().rstrip(b'\0').split(b'\0') != [os.fsencode(x) for x in self.args] or
                    uid is None or int(uid[1])!=os.geteuid() or int(uid[2])!=os.geteuid() or
                    (self.start_ticks is not None and ticks!=self.start_ticks)):
                raise UIError('Selected process no longer matches this observing run')
            self.start_ticks = ticks
        except (OSError,ValueError,IndexError,TypeError):
            raise UIError('Selected QEMU process is unavailable or changed') from None

    def __enter__(self):
        self.journal.write(kind='connection',phase='before_connect',pid=self.pid,endpoint=str(self.endpoint))
        try:
            self.verify_process()
            info=self.endpoint.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.geteuid() or self.endpoint.resolve()!=self.endpoint:
                raise UIError('Inspection endpoint is not the owned local socket')
            self.sock=socket.socket(socket.AF_UNIX);self.sock.settimeout(self.timeout);self.sock.connect(str(self.endpoint))
            self.verify_peer()
            self.stream=self.sock.makefile('rb')
            if 'QMP' not in self._read():raise UIError('Invalid QMP greeting')
            self.usable=True
            self._request('qmp_capabilities')
            self.journal.write(kind='connection',phase='connected',pid=self.pid,start_ticks=self.start_ticks)
            if self.keyboard_drain:
                self.drain=KeyboardDrain(self);self.drain.start()
            return self
        except Exception as error:
            diagnostic=self.drain is not None
            self.journal.write(kind='connection',phase='failed',
                status='keyboard_drain_setup_failed' if diagnostic else 'not_connected',failure_class=failure_class(error))
            self.__exit__()
            raise UIError('Keyboard drain setup failed' if diagnostic else
                          'Could not establish the selected owned QMP connection') from None

    def verify_peer(self):
        if self.sock is None:raise UIError('Owned inspection connection is unavailable')
        peer_pid,peer_uid,_=struct.unpack('3i',self.sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        if peer_pid!=self.pid or peer_uid!=os.geteuid():raise UIError('QMP peer identity differs from selected QEMU')

    def __exit__(self,exc_type=None,*_):
        restore_error=None
        try:
            if self.drain:
                try:self.drain.close()
                except UIError as error:restore_error=error
        finally:
            self.usable=False
            if self.stream:self.stream.close()
            if self.sock:self.sock.close()
            self.journal.close()
        if restore_error is not None and exc_type is None:raise restore_error

    def _read(self):
        line=self.stream.readline(1024*1024+1)
        if not line or len(line)>1024*1024:raise UIError('QMP reply unavailable')
        return json.loads(line)

    def _request(self, command, arguments=None, *, record=True):
        if command!='input-send-event':return self._request_once(command,arguments,record=record)
        deadline=None
        while True:
            try:return self._request_once(command,arguments,record=record,deadline=deadline)
            except InputNotRunning as error:
                # Exact complete rejection precedes dispatch of the whole list.
                # Never replay an accepted event or an unknown transport result.
                if deadline is None:deadline=error.rejected_at+INPUT_RETRY_SECONDS
                self._wait_input_ready(deadline,record=record)

    def _wait_input_ready(self,deadline,*,record):
        paused=False
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:
                if paused:
                    raise UIError('VM stayed paused; rejected input was not accepted',failure_class='vm_paused_timeout')
                raise UIError('Input retry deadline reached before readiness was confirmed',failure_class='input_retry_timeout')
            # Once a status reply has shown the paused VM, a deadline that expires
            # inside the next poll is the same paused-VM timeout as one that expires here.
            status=self._request_once('query-status',record=record,deadline=deadline,
                                      deadline_class='vm_paused_timeout' if paused else 'input_retry_timeout')
            if isinstance(status,dict):
                if ((status.get('running') is True and status.get('status')=='running') or
                        status.get('status')=='suspended'):return
                if status.get('running') is False and status.get('status')=='paused':
                    paused=True
                    time.sleep(min(INPUT_POLL_SECONDS,max(0,deadline-time.monotonic())))
                    continue
            raise UIError('VM input state is unavailable',failure_class='vm_state_unavailable')

    def _request_once(self, command, arguments=None, *, record=True,deadline=None,deadline_class='input_retry_timeout'):
        if command in ('trace-event-get-state','trace-event-set-state'):
            expected={'name'} if command=='trace-event-get-state' else {'name','enable'}
            if (not self.keyboard_drain or not isinstance(arguments,dict) or set(arguments)!=expected or
                    arguments.get('name') not in KEYBOARD_TRACE_EVENTS or
                    ('enable' in arguments and type(arguments['enable']) is not bool)):
                raise UIError('Only the two keyboard queue diagnostics are supported')
        elif command not in ('qmp_capabilities','screendump','input-send-event','query-status'):
            raise UIError('Unsupported UI operation')
        if deadline is not None and time.monotonic()>=deadline:raise deadline_error(deadline_class)
        token=uuid.uuid4().hex
        if record:self.journal.write(kind='request',phase='before_send',request=token,command=command,arguments=arguments)
        try:
            self.verify_process()
        except UIError:
            if record:self.journal.write(kind='request',phase='not_sent',request=token,status='binding_rejected')
            raise
        if not self.usable:raise UIError('QMP connection unavailable; effect unknown',failure_class='transport_unknown')
        drain_start=None
        if self.drain is not None and command=='input-send-event' and any(
                event.get('type')=='key' for event in (arguments or {}).get('events',[])):
            if not record:raise UIError('Keyboard drain diagnostics do not accept secret input')
            drain_start=self.drain.mark()
        payload=(json.dumps({'execute':command,'arguments':arguments or {},'id':token})+'\n').encode()
        if deadline is not None and time.monotonic()>=deadline:
            if record:self.journal.write(kind='request',phase='not_sent',request=token,status='deadline_expired',
                                         failure_class=deadline_class)
            raise deadline_error(deadline_class)
        # Initial local preparation is not evidence of a paused VM. Keep its
        # existing wire budget, starting only once the request is ready to send.
        if deadline is None and command=='input-send-event':deadline=time.monotonic()+INPUT_RETRY_SECONDS
        try:
            if deadline is not None:self.sock.settimeout(min(self.timeout,max(.001,deadline-time.monotonic())))
            self.sock.sendall(payload)
            while True:
                if deadline is not None:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError('Bounded input reply deadline')
                    self.sock.settimeout(min(self.timeout,remaining))
                reply=self._read()
                if 'event' in reply:continue
                if reply.get('id')!=token:raise UIError('QMP reply correlation failed')
                break
        except Exception:
            self.usable=False
            if record:self.journal.write(kind='request',phase='reply_missing',request=token,status='unknown')
            raise UIError('QMP connection interrupted; request effect unknown',failure_class='transport_unknown') from None
        finally:
            if deadline is not None and self.usable:self.sock.settimeout(self.timeout)
        accepted='return' in reply and 'error' not in reply
        paused=(not accepted and 'return' not in reply and command=='input-send-event' and isinstance(reply.get('error'),dict)
                and reply['error'].get('desc')=='VM not running')
        rejection=InputNotRunning() if paused else None  # Start recovery before the durable reply receipt.
        if record:self.journal.write(kind='request',phase='reply',request=token,
            status='accepted' if accepted else 'rejected',
            **({} if accepted else {'failure_class':'vm_not_running' if paused else 'qmp_rejected'}))
        if rejection:raise rejection
        if not accepted:raise UIError('QMP rejected input; interface effect unconfirmed',failure_class='qmp_rejected')
        if drain_start is not None:self.drain.wait(drain_start)
        return reply['return']

    def screenshot(self,name):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',name):raise UIError('Invalid new screenshot name')
        ppm,png=self.directory/(name+'.ppm'),self.directory/(name+'.png')
        if ppm.exists() or png.exists():raise UIError('Screenshot evidence already exists')
        self.journal.write(kind='screenshot',phase='before',name=name)
        os.close(os.open(ppm,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o600))
        try:
            self._request('screendump',{'filename':str(ppm)})
            with Image.open(ppm) as image:
                if image.format!='PPM' or not 0<image.width<=16384 or not 0<image.height<=16384:
                    raise UIError('Unexpected screenshot image')
                width,height=image.size
                with png.open('xb') as output:image.save(output,format='PNG')
            png.chmod(0o600)
            result={'ppm':str(ppm),'png':str(png),'width':width,'height':height,
                'ppm_sha256':hashlib.sha256(ppm.read_bytes()).hexdigest(),
                'png_sha256':hashlib.sha256(png.read_bytes()).hexdigest(),'gui_effect_verified':False}
            self.journal.write(kind='screenshot',phase='after',status='captured',**result)
            return result
        except Exception:
            self.journal.write(kind='screenshot',phase='after',status='unknown',name=name)
            raise UIError('Screenshot incomplete; preserved evidence may be partial') from None

    def native_screenshot(self,name):
        """Capture the unique native client of this authenticated run."""
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',name):raise UIError('Invalid new screenshot name')
        png,metadata=self.directory/(name+'.png'),self.directory/(name+'.json')
        if any((self.directory/(name+suffix)).exists() for suffix in ('.png','.json','.ppm')):
            raise UIError('Screenshot evidence already exists')
        if not self.usable:raise UIError('Owned inspection connection is unavailable')
        self.verify_process();self.verify_peer()
        native_capture=load_native_capture()
        self.journal.write(kind='native_screenshot',phase='before',name=name,pid=self.pid,start_ticks=self.start_ticks)
        try:
            window=native_capture.unique_window(self.pid)
            child=subprocess.run([sys.executable,str(Path(__file__).with_name('native_window_capture.py')),
                '--window-id',hex(window),'--qemu-pid',str(self.pid),'--png',str(png)],
                capture_output=True,text=True,timeout=15,check=True)
            self.verify_process();self.verify_peer()
            if native_capture.unique_window(self.pid)!=window:raise UIError('Native client changed during capture')
            capture=json.loads(child.stdout)
            if (capture.get('qemu_pid')!=self.pid or capture.get('window_id')!=hex(window) or
                    capture.get('full_desktop_captured') is not False):
                raise UIError('Native screenshot binding differs from selected QEMU')
            owned_regular(png,private=True,maximum=64*1024*1024)
            with Image.open(png) as image:
                if image.format!='PNG' or image.size!=(capture['width'],capture['height']):
                    raise UIError('Native screenshot image differs from its metadata')
            capture.update(captured=True,png=str(png),metadata=str(metadata),start_ticks=self.start_ticks,
                png_sha256=hashlib.sha256(png.read_bytes()).hexdigest(),gui_effect_verified=False,
                method='XGetImage of unique PID-bound X11 client window',
                scope='Native client pixels only; not physical scanout, FPS or latency.')
            fd=os.open(metadata,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o600)
            with os.fdopen(fd,'w') as output:json.dump(capture,output,indent=2);output.write('\n')
            self.journal.write(kind='native_screenshot',phase='after',status='captured',**capture)
            return capture
        except Exception as error:
            reason='capture_failed'
            if isinstance(error,native_capture.CaptureError):reason=str(error)
            elif isinstance(error,subprocess.TimeoutExpired):reason='capture_deadline'
            elif isinstance(error,subprocess.CalledProcessError):
                try:reason=json.loads(error.stdout).get('reason',reason)
                except (ValueError,TypeError):pass
            if reason not in ('no_owned_window','multiple_owned_windows','window_identity_changed',
                              'root_window_rejected','x11_unavailable','invalid_image','capture_deadline'):
                reason='capture_failed'
            self.journal.write(kind='native_screenshot',phase='after',status='unknown',name=name,reason=reason)
            raise UIError('Native screenshot not confirmed; consult its action receipt') from None

    def _stroke(self,downs,ups,*,secret=False):
        if secret and self.keyboard_drain:raise UIError('Keyboard drain diagnostics do not accept secret input')
        failure=None;released=False
        try:
            self._request('input-send-event',{'events':downs},record=not secret)
            time.sleep(.08)
        except UIError as error:failure=error
        finally:
            if self.usable:
                try:
                    self._request('input-send-event',{'events':ups},record=not secret);released=True
                except UIError as error:failure=error
            if not released:
                if not secret:self.journal.write(kind='release',phase='after',status='unknown')
                failure=UIError('Release could not be confirmed; input state unknown',
                    failure_class=failure_class(failure) if failure else 'transport_unknown')
        if failure:
            failure.release_confirmed=released
            raise failure
        return released

    def release_known_keys(self):
        """Constant recovery template, independent of any private input/history."""
        self.journal.write(kind='release_known_keys',phase='before')
        try:
            order=list(MODIFIER_KEYS)+sorted(KEYS-set(MODIFIER_KEYS))
            for key in order:
                self._request('input-send-event',{'events':[key_event(key,False)]})
                if not self.keyboard_drain:time.sleep(KEY_RELEASE_INTERVAL)
        except UIError as error:
            self.journal.write(kind='release_known_keys',phase='after',status='unknown',
                               failure_class=failure_class(error),release_confirmed='unknown')
            raise
        result={'status':'host_accepted','release_confirmed':True,'gui_effect':'unverified',
                **self.keyboard_receipt(),
                'scope':'Constant modifier-first individual key releases; no private history inferred'}
        self.journal.write(kind='release_known_keys',phase='after',**result)
        return result

    def keyboard_receipt(self):
        return {'keyboard_queue_drained':bool(self.keyboard_drain),
                'release_confirmation':'hid_queue_drained' if self.keyboard_drain else 'qmp_request_accepted',
                'keyboard_scope':'Queue consumption does not establish guest application execution'}

    def keys(self,keys):
        if not keys or any(key not in KEYS for key in keys) or (len(keys)>1 and tuple(keys) not in CHORDS):
            raise UIError('Unsupported key or setup/desktop chord')
        self.journal.write(kind='key',phase='before',keys=keys)
        try:
            self._stroke([key_event(key,True) for key in keys],[key_event(key,False) for key in reversed(keys)])
        except UIError as error:
            self.journal.write(kind='key',phase='after',keys=keys,status='unknown',failure_class=failure_class(error))
            raise
        result={'status':'host_accepted','keys':keys,'release_confirmed':True,'gui_effect':'unverified',**self.keyboard_receipt()}
        self.journal.write(kind='key',phase='after',**result)
        return result

    def pointer(self,x,y,frame,*,click=False):
        frame=Path(frame)
        if not frame.is_absolute():frame=self.directory/frame
        if not frame.is_relative_to(self.directory):raise UIError('Pointer requires a screenshot from the selected run')
        owned_regular(frame,maximum=128*1024*1024)
        with Image.open(frame) as image:width,height=image.size;image.verify()
        if type(x) is not int or type(y) is not int or not 0<=x<width or not 0<=y<height:
            raise UIError('Pointer coordinate is outside the referenced screenshot')
        meta={'kind':'click' if click else 'pointer','x':x,'y':y,'frame':str(frame),
              'frame_sha256':hashlib.sha256(frame.read_bytes()).hexdigest(),'width':width,'height':height}
        self.journal.write(phase='before',**meta)
        try:
            self._request('input-send-event',{'events':[
                {'type':'abs','data':{'axis':'x','value':round(x*32767/max(1,width-1))}},
                {'type':'abs','data':{'axis':'y','value':round(y*32767/max(1,height-1))}}]})
            if click:self._stroke([{'type':'btn','data':{'button':'left','down':True}}],
                                  [{'type':'btn','data':{'button':'left','down':False}}])
        except UIError as error:
            self.journal.write(phase='after',status='unknown',failure_class=failure_class(error),**meta);raise
        result={'status':'host_accepted','gui_effect':'unverified','release_confirmed':True if click else None}
        self.journal.write(phase='after',**meta,**result)
        return result

    def _type_ascii(self,value,*,secret):
        validate_ascii(value)
        for char in value:
            keys=character_keys(char)
            self._stroke([key_event(key,True) for key in keys],
                         [key_event(key,False) for key in reversed(keys)],secret=secret)

    def type_ascii(self,value):
        validate_ascii(value)
        self.journal.write(kind='ascii_text',phase='before',text=value)
        try:self._type_ascii(value,secret=False)
        except UIError as error:
            self.journal.write(kind='ascii_text',phase='after',status='unknown',failure_class=failure_class(error));raise
        result={'status':'host_accepted','release_confirmed':True,'gui_effect':'unverified',
                **self.keyboard_receipt(),
                'mapping':'US ASCII physical-key mapping; guest layout/focus effect requires a screenshot'}
        self.journal.write(kind='ascii_text',phase='after',**result)
        return result

    def type_secret(self,path,key):
        if self.keyboard_drain:raise UIError('Keyboard drain diagnostics do not accept secret input')
        path=Path(path)
        if not path.is_absolute():path=ROOT/path
        source={'file':str(path.absolute()),'key':key}
        self.journal.write(kind='secret_text',phase='before',source=source)
        started=False;failed=False;value=None;failure='local_error';released=None
        try:
            value=private_value(path,key)
            started=True
            self._type_ascii(value,secret=True)
            value=None
        except BaseException as error:
            # No exception text, data, length, per-key event or request ID is exposed.
            failed=True
            failure=failure_class(error)
            released=error.release_confirmed if isinstance(error,UIError) else None
        finally:
            value=None
        if failed:
            self.journal.write(kind='secret_text',phase='after',source=source,
                               status='unknown' if started else 'not_sent',failure_class=failure,
                               release_confirmed=(True if released is True else 'unknown') if started else None)
            raise UIError('Private text input was not confirmed; consult the redacted action receipt') from None
        result={'status':'host_accepted','release_confirmed':True,'gui_effect':'unverified','source':source}
        self.journal.write(kind='secret_text',phase='after',**result)
        return result


def load_native_capture():
    spec=importlib.util.spec_from_file_location('native_window_capture',Path(__file__).with_name('native_window_capture.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def key_event(key,down):return {'type':'key','data':{'down':down,'key':{'type':'qcode','data':key}}}


def validate_ascii(value):
    if not isinstance(value,str) or not 1<=len(value)<=256 or any(not 32<=ord(char)<=126 for char in value):
        raise UIError('Text input requires 1..256 printable ASCII characters')


def character_keys(char):
    if 'A'<=char<='Z':return ('shift',char.lower())
    if char in SHIFTED:
        base=SHIFTED[char]
        return ('shift',PUNCTUATION.get(base,base))
    return (PUNCTUATION.get(char,char),)


def private_value(path,key):
    """Read only a small owned 0600 JSON inside workspace private/."""
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',key):
        raise UIError('Invalid private JSON field locator')
    path=path.absolute()
    if not path.is_relative_to(ROOT/'private'):
        raise UIError('Private JSON must be inside workspace private/')
    before=owned_regular(path,private=True,maximum=16384)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        current=os.fstat(fd)
        if (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns,current.st_ctime_ns)!=(
                before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns):
            raise UIError('Private JSON changed before reading')
        raw=os.read(fd,16385)
    finally:os.close(fd)
    if len(raw)>16384:raise UIError('Private JSON exceeds size limit')
    value=json.loads(raw)
    if not isinstance(value,dict) or key not in value:raise UIError('Private JSON field is unavailable')
    value=value[key]
    validate_ascii(value)
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True)
    parser.add_argument('--keyboard-drain',action='store_true',help='Public keyboard only: wait for HID queue drain with a bounded payload-free trace')
    actions=parser.add_subparsers(dest='action',required=True)
    screenshot=actions.add_parser('screenshot');screenshot.add_argument('--name',required=True)
    native=actions.add_parser('native-screenshot');native.add_argument('--name',required=True)
    key=actions.add_parser('key');key.add_argument('key',choices=sorted(KEYS))
    chord=actions.add_parser('chord');chord.add_argument('keys',choices=['+'.join(keys) for keys in sorted(CHORDS)])
    text=actions.add_parser('type-ascii');text.add_argument('text',help='Non-sensitive ASCII only')
    secret=actions.add_parser('type-secret');secret.add_argument('--file',type=Path,required=True);secret.add_argument('--key',required=True)
    actions.add_parser('release-known-keys',help='Release supported keys individually, modifiers first')
    for name in ('pointer','click'):
        move=actions.add_parser(name);move.add_argument('x',type=int);move.add_argument('y',type=int);move.add_argument('--frame',required=True)
    args=parser.parse_args(argv)
    if args.keyboard_drain and args.action=='type-secret':parser.error('--keyboard-drain cannot be used with type-secret')
    try:
        with GuestUI(args.run,keyboard_drain=args.keyboard_drain) as ui:
            if args.action=='screenshot':result=ui.screenshot(args.name)
            elif args.action=='native-screenshot':result=ui.native_screenshot(args.name)
            elif args.action=='key':result=ui.keys([args.key])
            elif args.action=='chord':result=ui.keys(args.keys.split('+'))
            elif args.action=='type-ascii':result=ui.type_ascii(args.text)
            elif args.action=='type-secret':result=ui.type_secret(args.file,args.key)
            elif args.action=='release-known-keys':result=ui.release_known_keys()
            else:result=ui.pointer(args.x,args.y,args.frame,click=args.action=='click')
        print(json.dumps(result));return 0
    except (UIError,OSError,ValueError):
        print(json.dumps({'status':'not_confirmed','message':'UI operation failed; consult ui-actions.jsonl for sent/accepted/unknown receipts.'}))
        return 1


if __name__=='__main__':raise SystemExit(main())
