#!/usr/bin/env python3
"""Retain bounded, finalized WAV voices before QEMU reopens its output with wb."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
MAX_BYTES=64*1024*1024
MAX_VOICES=4


def evidence_directory(directory,name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',name):
        raise ValueError('Evidence name must be one restricted directory basename')
    return directory/name


def utc():
    return datetime.now(timezone.utc).isoformat()


def metadata(info):
    return {name:getattr(info,'st_'+name) for name in
            ('dev','ino','size','mtime_ns','ctime_ns')}


def signature(info):
    return tuple(metadata(info).values())


def source_stat(source):
    """A not-yet-opened WAV is normal; an unexpected existing object is not."""
    try:info=source.lstat()
    except FileNotFoundError:return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid():
        raise ValueError('WAV source must be an owned regular file')
    return info


def wav_header(raw,size):
    result={'valid':False}
    if len(raw)!=44:return result
    riff,rlen,wave,fmt,flen,codec,channels,rate,byte_rate,align,bits,data,dlen=struct.unpack(
        '<4sI4s4sIHHIIHH4sI',raw)
    result.update(riff_bytes=rlen,data_bytes=dlen,channels=channels,
                  sample_rate_hz=rate,sample_bits=bits,block_align=align)
    result['valid']=(riff==b'RIFF' and wave==b'WAVE' and fmt==b'fmt ' and data==b'data'
        and flen==16 and codec==1 and channels==2 and rate==48000 and bits==16
        and align==4 and byte_rate==192000 and rlen==size-8 and dlen==size-44
        and dlen%4==0)
    return result


class ProcessBinding:
    def __init__(self,pid,argv,executable):
        self.pid=pid;self.argv=list(argv);self.executable=Path(executable).resolve()
        self.start_ticks=None
        self.verify()
    def verify(self):
        proc=Path('/proc')/str(self.pid)
        ticks=int((proc/'stat').read_text().rsplit(')',1)[1].split()[19])
        ids=re.search(r'^Uid:\s+(\d+)\s+(\d+)',(proc/'status').read_text(),re.M)
        if ((self.start_ticks is not None and ticks!=self.start_ticks)
                or (proc/'exe').resolve(strict=True)!=self.executable
                or (proc/'cmdline').read_bytes().rstrip(b'\0').split(b'\0')!=
                    [os.fsencode(arg) for arg in self.argv]
                or ids is None or any(int(value)!=os.geteuid() for value in ids.groups())):
            raise ValueError('Owned process identity changed')
        self.start_ticks=ticks
    def has_inode_open(self,info):
        self.verify()
        found=False
        # Read only this already-bound process, never the host process inventory.
        for entry in (Path('/proc')/str(self.pid)/'fd').iterdir():
            try:opened=entry.stat()
            except FileNotFoundError:continue
            if (opened.st_dev,opened.st_ino)==(info.st_dev,info.st_ino):
                found=True;break
        self.verify()
        return found


def freeze_closed(binding,source,destination):
    """Create one immutable evidence copy only if both closure/stat checks agree."""
    try:before=source.lstat()
    except FileNotFoundError:return None,'pending_not_created'
    if not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid():
        return None,'unknown_source_type'
    if not 44<=before.st_size<=MAX_BYTES:return None,'unknown_size'
    if binding.has_inode_open(before):return None,'pending_open_voice'
    temporary=destination.with_suffix('.pending')
    source_fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        with os.fdopen(source_fd,'rb') as stream:
            opened=os.fstat(stream.fileno())
            if signature(opened)!=signature(before):return None,'pending_source_changed'
            header=wav_header(stream.read(44),opened.st_size)
            if not header['valid']:return None,'pending_unfinalized_header'
            stream.seek(0)
            digest=hashlib.sha256()
            output_fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o600)
            with os.fdopen(output_fd,'wb') as output:
                remaining=opened.st_size
                while remaining:
                    chunk=stream.read(min(65536,remaining))
                    if not chunk:return None,'pending_source_changed'
                    output.write(chunk);digest.update(chunk);remaining-=len(chunk)
                output.flush();os.fsync(output.fileno())
            after=os.fstat(stream.fileno());path_after=source.lstat()
            if (signature(opened)!=signature(after) or signature(opened)!=signature(path_after)):
                return None,'pending_source_changed'
            if binding.has_inode_open(after):return None,'pending_reopened_voice'
            # Hard-link the completed temporary copy so an existing final name
            # is never overwritten, even if another actor created one.
            os.link(temporary,destination,follow_symlinks=False)
            return {'file':destination.name,'captured_at_utc':utc(),
                    'sha256':digest.hexdigest(),'header':header,
                    'source_before':metadata(before),'source_after':metadata(path_after),
                    'qemu_inode_fd_closed_at_both_checks':True,
                    'pcm_waveform_verified':False},'captured'
    finally:
        # This is only this attempt's unaccepted scratch copy, never the source.
        temporary.unlink(missing_ok=True)


def monitor(binding,source,evidence,receipt,seconds,*,poll=.1,quiet=2):
    evidence.mkdir(mode=0o700,exist_ok=False)
    baseline=source_stat(source)
    initial=signature(baseline) if baseline is not None else None
    initial_receipt=signature(receipt.stat()) if receipt.exists() else None
    manifest={'schema':1,'started_at_utc':utc(),'state':'ready','ready':True,
        'binding':{'pid':binding.pid,'start_ticks':binding.start_ticks,
                   'executable':str(binding.executable),
                   'argv_sha256':hashlib.sha256(b'\0'.join(os.fsencode(x) for x in binding.argv)).hexdigest()},
        'source':str(source),'baseline':metadata(baseline) if baseline is not None else None,
        'baseline_exists':baseline is not None,'baseline_is_tone':False,
        'source_exists':baseline is not None,
        'receipt':str(receipt),'receipt_preexisting':initial_receipt is not None,
        'seconds_limit':seconds,'max_voices':MAX_VOICES,'max_voice_bytes':MAX_BYTES,
        'voices':[],'pcm_waveform_verified':False,
        'scope':'New finalized, closed-voice copies only. Stable before/after checks are not a writer lock; '
                'no VM or original WAV changes. A receipt/valid header does not prove the target tone.'}
    manifest_path=evidence/'manifest.json'
    def save():
        manifest['updated_at_utc']=utc()
        temporary=evidence/'.manifest.pending'
        temporary.write_text(json.dumps(manifest,indent=2)+'\n')
        temporary.replace(manifest_path)
    save()
    deadline=time.monotonic()+seconds
    last_signature=initial;last_change=time.monotonic();saved_signatures=set()
    receipt_signature=initial_receipt
    interrupted=[False]
    previous={}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT,signal.SIGTERM):
            previous[signum]=signal.signal(signum,lambda *_:interrupted.__setitem__(0,True))
    last_save=0
    try:
        while time.monotonic()<deadline and not interrupted[0]:
            binding.verify()
            info=source_stat(source)
            current=signature(info) if info is not None else None
            if current!=last_signature:
                last_signature=current;last_change=time.monotonic()
            open_voice=binding.has_inode_open(info) if info is not None else None
            manifest['source_latest']=metadata(info) if info is not None else None
            manifest['source_exists']=info is not None
            manifest['qemu_has_source_inode_open']=open_voice
            manifest['state']=('pending_not_created' if info is None else
                               'pending_open_voice' if open_voice else 'pending_new_voice')
            if info is not None and current!=initial and current not in saved_signatures and not open_voice:
                destination=evidence/f'voice-{len(manifest["voices"])+1}.wav'
                row,state=freeze_closed(binding,source,destination)
                manifest['state']=state
                if row is not None:
                    manifest['voices'].append(row);saved_signatures.add(current)
                    last_change=time.monotonic()
                    if len(manifest['voices'])>=MAX_VOICES:
                        manifest['state']='capture_limit_reached';break
            observed_receipt=signature(receipt.stat()) if receipt.exists() else None
            if observed_receipt!=receipt_signature:
                receipt_signature=observed_receipt;last_change=time.monotonic()
            receipt_new=observed_receipt is not None and observed_receipt!=initial_receipt
            manifest['new_receipt_observed']=receipt_new
            if (receipt_new and manifest['voices'] and not open_voice
                    and current in saved_signatures and time.monotonic()-last_change>=quiet):
                manifest['state']='complete_receipt_and_closed_voice';break
            if time.monotonic()-last_save>=1:
                save();last_save=time.monotonic()
            time.sleep(poll)
        else:
            manifest['state']='interrupted' if interrupted[0] else 'deadline'
    except (OSError,ValueError) as error:
        manifest.update(state='unknown',error=type(error).__name__+': '+str(error))
    finally:
        for signum,handler in previous.items():signal.signal(signum,handler)
        manifest['finished_at_utc']=utc();save()
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True)
    parser.add_argument('--expected-pid',type=int,required=True)
    parser.add_argument('--receipt',default='r108pcm.tgz')
    parser.add_argument('--evidence-name',default='pcm-evidence')
    parser.add_argument('--seconds',type=int,default=240)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',args.run):parser.error('Invalid run label')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}',args.receipt):parser.error('Invalid receipt name')
    if not 1<=args.seconds<=240:parser.error('Observation limit must be 1..240 seconds')
    directory=ROOT/'runs/e05-installed-boot'/args.run
    evidence=evidence_directory(directory,args.evidence_name)
    if directory.resolve()!=directory:raise ValueError('Run directory must not contain symlinks')
    record=json.loads((directory/'results.json').read_text())
    source=directory/'guest-audio.wav'
    audio=record.get('execution',{}).get('audio',{})
    argv=record.get('execution',{}).get('qemu_argv')
    if (record.get('state')!='observing' or record.get('qemu_pid')!=args.expected_pid
            or not isinstance(argv,list) or not argv
            or argv[0]!=str(ROOT/'build/qemu-bdif-usb/qemu-system-aarch64')
            or audio.get('backend')!='wav' or audio.get('output_file')!=str(source)):
        raise ValueError('Run is not the expected owned WAV session')
    binding=ProcessBinding(args.expected_pid,argv,argv[0])
    result=monitor(binding,source,evidence,
                   directory/'guest-uploads'/args.receipt,args.seconds)
    print(json.dumps({'manifest':str(evidence/'manifest.json'),
                      'state':result['state'],'voices':len(result['voices'])}))
    return 0 if result['voices'] else 1


if __name__=='__main__':raise SystemExit(main())
