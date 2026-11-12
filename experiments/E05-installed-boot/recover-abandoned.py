#!/usr/bin/env python3
"""Recover retained aliases after a recorded installed-run controller vanished."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments/E05-usbip-kernel'))
from firmware_session import abandoned_run_context, latest_retained_shutdown


def recover(project, label, *, previous_boot_id=None,service_unit=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',label):
        raise ValueError('Invalid installed-run label')
    manifest=project/'runs/e05-installed-boot'/label/'retained-storage.json'
    if manifest.resolve(strict=True)!=manifest:
        raise ValueError('Retained manifest must not contain symlinks')
    info=manifest.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_size>65536:
        raise ValueError('Retained manifest must be an owned bounded regular file')
    original=manifest.read_bytes();record=json.loads(original)
    if record.get('abandoned_recovery') is not None:
        raise ValueError('This run already has recovery evidence; inspect it before any new action')
    if record.get('qemu_reaped') is not False or record.get('qemu_exit_code') is not None:
        raise ValueError('Run already has an observed exit; abandoned recovery is not applicable')
    if (previous_boot_id is None)!=(service_unit is None):
        raise ValueError('Host reboot recovery requires both prior boot ID and service unit')
    reboot={'previous_boot_id':previous_boot_id,'service_unit':service_unit} if previous_boot_id else None
    context=abandoned_run_context(project,manifest,record,host_reboot=reboot)
    directory=Path(record['directory']);work=Path(context['temporary_directory'])
    if (directory.resolve(strict=True)!=directory or directory.stat().st_uid!=os.geteuid() or
            directory.stat().st_mode & 0o077):
        raise ValueError('Retained directory must be owned and private')
    locks=[]
    try:
        for name,size in [('aux.raw',32*1024**2),('root.raw',128*1024**3)]:
            source=directory/name
            fd=os.open(source,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
            locks.append(fd);info=os.fstat(fd);expected=record['files'][name]
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or
                    expected.get('path')!=str(source) or
                    (info.st_dev,info.st_ino,info.st_size)!=(expected.get('device'),expected.get('inode'),size) or
                    expected.get('bytes')!=size):
                raise ValueError('Retained target inode, size or owner mismatch')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as error:raise ValueError('Retained target is already in use') from error
        latest=latest_retained_shutdown(project,manifest,record)
        if latest['manifest']!=str(manifest):
            raise ValueError('A later retention record already owns these target inodes')
        if work.resolve()!=work:
            raise ValueError('Temporary directory must not contain symlinks')
        remaining=[]
        if work.exists():
            info=work.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o077:
                raise ValueError('Temporary directory must be owned and private')
            sockets=context['temporary_sockets']
            if {p.name for p in work.iterdir()}-({'aux.raw','root.raw'}|set(sockets)):
                raise ValueError('Unexpected temporary contents; only recorded aliases and sockets may be removed')
            for name,fd in zip(('aux.raw','root.raw'),locks):
                target=work/name
                if not os.path.lexists(target):continue
                info=target.lstat();source=os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or
                        (info.st_dev,info.st_ino)!=(source.st_dev,source.st_ino)):
                    raise ValueError('Temporary alias differs from the locked retained inode')
                remaining.append(target)
            active_sockets={line.split(maxsplit=7)[7] for line in Path('/proc/net/unix').read_text().splitlines()[1:]
                            if len(line.split(maxsplit=7))==8}
            for name,path in sockets.items():
                target=work/name
                if not os.path.lexists(target):continue
                info=target.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.geteuid():
                    raise ValueError('Recorded socket path is not an owned socket')
                if path in active_sockets:
                    raise ValueError('Recorded socket still has a live kernel endpoint')
                remaining.append(target)
        # Revalidate immediately before the only destructive operation: removing
        # these verified aliases, while each retained inode is still linked/locked.
        if abandoned_run_context(project,manifest,record,host_reboot=reboot)!=context or manifest.read_bytes()!=original:
            raise ValueError('Abandoned evidence changed during recovery')
        for target in remaining:target.unlink()
        if work.exists():work.rmdir()
        record.update(qemu_pid=context['pids']['qemu'],qemu_exit_code=None,qemu_reaped=False,
                      teardown='abandoned_owner_exit_unobserved',temporary_links_removed=True,
                      clean_guest_shutdown_proven=False,
                      abandoned_recovery={'schema':2,'verified_at_utc':datetime.now(timezone.utc).isoformat(),
                          **context,'both_target_flocks_acquired':True,
                          'scope':'Recorded PIDs absent and temporary aliases removed; exit code, reaping and guest shutdown are unobserved. Explicit unclean reuse remains required.'})
        fd,tmp=tempfile.mkstemp(prefix='.retained-recovery-',dir=manifest.parent)
        try:
            with os.fdopen(fd,'w') as stream:
                stream.write(json.dumps(record,indent=2)+'\n');stream.flush();os.fsync(stream.fileno())
            os.replace(tmp,manifest)
        finally:
            Path(tmp).unlink(missing_ok=True)
        return {'run':label,'manifest':str(manifest),'recovered':True,
                'qemu_reaped':False,'qemu_exit_code':None,'requires_allow_unclean_storage':True}
    finally:
        for fd in locks:os.close(fd)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True)
    parser.add_argument('--previous-boot-id',help='Explicit old host boot for journal-verified reboot recovery')
    parser.add_argument('--service-unit',help='User service whose old-boot start matches the saved run')
    args=parser.parse_args()
    try:print(json.dumps(recover(ROOT,args.run,previous_boot_id=args.previous_boot_id,service_unit=args.service_unit)))
    except (ValueError,OSError) as error:
        parser.exit(2,f'RECOVERY_REFUSED: {error}\n')
