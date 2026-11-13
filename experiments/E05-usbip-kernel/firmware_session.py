#!/usr/bin/env python3
"""Scoped original AVPBooter QEMU session, no firmware mutation/restore."""
from pathlib import Path
import ctypes
from datetime import datetime,timezone
import fcntl,hashlib,json,math,os,re,signal,socket,stat,struct,subprocess,tempfile,time,sys,threading
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments/E05-bdif-usb'))
from usbip_control import ProtocolError
from real_control import RealFirmwareControl
from virtio_usb_control import KernelVirtioUSBControl
ROM=ROOT/'private/official-restore-research/27.0.1-26A434/VirtualizationResources/AVPBooter.vmapple2.bin'
EXPECTED='6d010540a047ec71a54cb96565171f8f2e0e191dd5dbef2403f3863409afa845'
BLANK_ROOT_BYTES=128*1024**3
STUB_ROOT_BYTES=256*1024**2
LIFECYCLE_SOURCE='owned FirmwareSession QMP connection'
LIFECYCLE_EVENTS={'SHUTDOWN','RESET','POWERDOWN','GUEST_PANICKED','WATCHDOG','SUSPEND','WAKEUP'}
LIFECYCLE_EVENT_LIMIT=64
QMP_TAIL_SECONDS=.25
QMP_TAIL_BYTES=65536
GUEST_SHUTDOWN_WAIT_SECONDS=5


def has_guest_shutdown_evidence(record):
    """A guest-origin QMP shutdown and reaped zero exit, not filesystem proof."""
    observed=record.get('qmp_lifecycle')
    if not isinstance(observed,dict):return False
    event=observed.get('last_shutdown_or_reset_event')
    if not isinstance(event,dict):return False
    data=event.get('data')
    if not isinstance(data,dict):return False
    return (type(record.get('qemu_exit_code')) is int and record['qemu_exit_code']==0 and
            record.get('qemu_reaped') is True and
            record.get('teardown')=='already_exited_or_not_started' and
            observed.get('source')==LIFECYCLE_SOURCE and
            type(record.get('qemu_pid')) is int and record['qemu_pid']>0 and
            observed.get('qemu_pid')==record['qemu_pid'] and
            observed.get('host_quit_requested') is False and
            event.get('event')=='SHUTDOWN' and data.get('guest') is True and
            data.get('reason')=='guest-shutdown')


class FirmwareSessionInterrupted(RuntimeError):
    """Raised by the session signal hook so the context manager can unwind."""

    def __init__(self, signum):
        self.signum = signum
        super().__init__(f'Firmware session interrupted by signal {signum}')


# QARMA5 stays the default for comparability with every earlier run. QEMU's
# impdef algorithm is non-cryptographic but added 48 ns instead of 554 ns per
# PACIBSP/RETAB call in the 2026-10-29 self-guest benchmark on this binary.
PAUTH_ALGORITHMS=('qarma5','qarma3','impdef')
TCG_THREAD_MODES=('auto','multi','single')
VIRTIO_NET_DEVICE='virtio-net-pci,netdev=guestnet,id=guestnet-pci,disable-legacy=on,addr=0x5,x-ascending-capabilities=on'


def validate_net_control_queue_size(user_network, size):
    if type(user_network) is not bool:
        raise ValueError('User networking selection must be boolean')
    if size is not None and (type(size) is not int or size not in (64,512) or not user_network):
        raise ValueError('Network control queue size must be 64 or 512 and requires --user-network')
    return size


def virtio_net_device_argument(control_queue_size=None):
    validate_net_control_queue_size(True,control_queue_size)
    return VIRTIO_NET_DEVICE+(f',ctrl_queue_size={control_queue_size}' if control_queue_size is not None else '')


def tcg_accel_argument(mode:str='auto')->str:
    """Keep QEMU's previous default unless a threading control is explicit."""
    if mode not in TCG_THREAD_MODES:
        raise ValueError('Unknown TCG threading mode')
    return 'tcg' if mode=='auto' else f'tcg,thread={mode}'


def cpu_model(pauth_algorithm:str='qarma5', *, pauth_memo:bool=False,
              pauth_memo_entries:int|None=None,pauth_memo_stats:bool=False,
              webkit_timeout_diagnostic:bool=False,sync_stats:bool=False)->str:
    """The exact -cpu value for one explicit QEMU PAuth algorithm."""
    if pauth_algorithm not in PAUTH_ALGORITHMS:
        raise ValueError('Unknown PAuth algorithm')
    if type(pauth_memo) is not bool or (pauth_memo and pauth_algorithm=='impdef'):
        raise ValueError('PAuth memoization requires an explicit boolean and QARMA3/5')
    if (pauth_memo_entries is not None and
            (type(pauth_memo_entries) is not int or pauth_memo_entries not in (64,1024,16384))):
        raise ValueError('PAuth memo capacity must be 64, 1024 or 16384')
    if type(pauth_memo_stats) is not bool or ((pauth_memo_entries is not None or pauth_memo_stats) and not pauth_memo):
        raise ValueError('Memo capacity/statistics require PAuth memoization')
    if type(webkit_timeout_diagnostic) is not bool:
        raise ValueError('WebKit timeout diagnostic selection must be boolean')
    if type(sync_stats) is not bool:
        raise ValueError('Sync statistics selection must be boolean')
    return (f'max,pauth-{pauth_algorithm}=on,cntfrq=24000000,x-cntfrq-exact=on'
            + (',x-pauth-memo=on' if pauth_memo else '')
            + (f',x-pauth-memo-entries={pauth_memo_entries}' if pauth_memo_entries is not None else '')
            + (',x-pauth-memo-stats=on' if pauth_memo_stats else '')
            + (',x-sync-stats=on' if sync_stats else '')
            + (',x-webkit-timeout-diagnostic=' + str(ROOT / 'private/webkit-watchdog-source/actual-26A434/timeout-profile-guard.json')
               if webkit_timeout_diagnostic else ''))


def window_refresh_value(value,native_window):
    if value is None:return None
    if (type(value) not in (int,float) or not math.isfinite(value) or
            not 0<value<=1000 or not native_window):
        raise ValueError('Explicit display refresh requires a native window and a finite rate in (0,1000] Hz')
    return format(value,'g')


def parse_vcpu_affinity(value):
    """Parse an explicit logical-CPU set; omission performs no affinity syscall."""
    if value is None:
        return None
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]+(?:-[0-9]+)?(?:,[0-9]+(?:-[0-9]+)?)*',value):
        raise ValueError('vCPU affinity must be a CPU list such as 0-7 or 0,2,4-7')
    allowed=os.sched_getaffinity(0)
    selected=set()
    for item in value.split(','):
        bounds=[int(part) for part in item.split('-')]
        first,last=(bounds[0],bounds[-1])
        if first>last or last>max(allowed):
            raise ValueError('vCPU affinity range is outside the current allowed CPU set')
        selected.update(range(first,last+1))
    if not selected or not selected<=allowed:
        raise ValueError('vCPU affinity must be a subset of the current allowed CPU set')
    return sorted(selected)


def host_thread_affinities(pid):
    """Snapshot identities and masks of one process's existing Linux threads."""
    result={}
    for path in sorted(Path(f'/proc/{pid}/task').iterdir(),key=lambda p:int(p.name)):
        tid=int(path.name)
        start=int((path/'stat').read_text().rsplit(')',1)[1].split()[19])
        cpus=sorted(os.sched_getaffinity(tid))
        if int((path/'stat').read_text().rsplit(')',1)[1].split()[19])!=start:
            raise RuntimeError('Host thread identity changed during affinity observation')
        result[tid]={'start_time_ticks':start,'cpus':cpus}
    if pid not in result:
        raise RuntimeError('Owned process main thread is unavailable')
    return result


VIRTIO_SOUND_BACKEND='none,id=guestsound'
VIRTIO_SOUND_DEVICE=('virtio-sound-pci,audiodev=guestsound,id=guestsound-pci,'
                    'addr=0x6,x-ascending-capabilities=on')
VIRTIO_SOUND_CONTROL_TRACE_EVENTS=('virtio_snd_get_config','virtio_snd_handle_code',
                                  'virtio_snd_empty_info')


def virtio_sound_configuration(enabled,backend='none',run_directory=None,*,pcm_trace=False,queue_size=None,streams=None):
    if type(enabled) is not bool:
        raise ValueError('VirtIO sound selection must be boolean')
    if backend not in ('none','wav') or (backend=='wav' and not enabled):
        raise ValueError('WAV sound backend requires explicit VirtIO sound')
    if type(pcm_trace) is not bool or (pcm_trace and not enabled):
        raise ValueError('PCM boundary trace requires explicit VirtIO sound and boolean selection')
    if queue_size is not None and (not enabled or type(queue_size) is not int or queue_size not in (64,512)):
        raise ValueError('Sound queue size requires explicit VirtIO sound and size 64 or 512')
    if streams is not None and (not enabled or type(streams) is not int or streams not in (1,2)):
        raise ValueError('Sound streams require explicit VirtIO sound and count 1 or 2')
    count=streams if streams is not None else 2
    directory=None
    if backend=='wav' or pcm_trace:
        if run_directory is None:
            raise ValueError('Sound file capture requires the current run directory')
        directory=Path(run_directory).absolute()
        if directory.resolve()!=directory:
            raise ValueError('Sound run directory must not contain symlinks')
    result={'requested':enabled,'backend':backend if enabled else None,
            'device':'virtio-sound-pci' if enabled else None,
            'pci_address':'00:06.0' if enabled else None,
            'ascending_capabilities_requested':enabled,
            'host_audio_output':False,'host_audio_input':False,
            'guest_driver_binding':'unknown','guest_default_device':'unknown',
            'pcm_transfer_verified':False,'audible_playback_verified':False,
            'advertised_streams':count if enabled else None,
            'advertised_output_streams':1 if enabled else None,
            'advertised_input_streams':count-1 if enabled else None,
            'streams':{'requested':streams,'default':2,'configured_count':count if enabled else None,
                'runtime_verified':False,
                'scope':'Configured enumeration only; actual QOM/PCM_INFO values and guest binding require live verification. '
                        'An advertised input stream does not imply backend or physical input capability.'},
            'queue_size':{'requested':queue_size,'default':64,
                'configured_maximum':(queue_size or 64) if enabled else None,
                'guest_selected':None,'runtime_verified':False,
                'scope':'Requested advertised capacity only; guest selection, allocation and ring mapping require live verification.'},
            'pcm_trace':{'requested':pcm_trace,
                'output_file':str(directory/'guest-audio-boundary.bin') if pcm_trace else None,
                'format':'VSNDPCM1','max_bytes':8*1024*1024,
                'completion':'unknown','corruption_cause':'unknown',
                'control_events':list(VIRTIO_SOUND_CONTROL_TRACE_EVENTS) if pcm_trace else [],
                'scope':'Optional host PCM boundary evidence at TX pop, lazy populate and accepted writes; '
                        'no playback verdict. Also enables metadata-only item-info acceptance and control '
                        'events in the QEMU trace log. Capture can change timing; default disabled.'},
            'scope':'Requested standard VirtIO sound with a silent none backend; '
                    'guest binding, CoreAudio default devices and PCM require live validation.'}
    if backend=='wav':
        result.update(output_file=str(directory/'guest-audio.wav'),
            file_capture_requested=True,capture_file_verified=False,
            input_backend_available=False,
            capture_format={'sample_rate_hz':48000,'channels':2,'sample_bits':16},
            scope='Requested VirtIO stream enumeration with output captured to the run WAV file. '
                  'WAV input is unavailable; the configured advertised input count is recorded separately. '
                  'No host speaker/microphone. '
                  'File existence or a RIFF header does not prove guest PCM or playback.')
    return result


def virtio_sound_backend_argument(configuration):
    if not configuration['requested']:return None
    if configuration['backend']=='none':return VIRTIO_SOUND_BACKEND
    # QEMU's key/value parser escapes a literal comma by doubling it.
    path=configuration['output_file'].replace(',',',,')
    return ('wav,id=guestsound,path='+path+
            ',out.frequency=48000,out.channels=2,out.format=s16')


def virtio_sound_device_argument(configuration):
    if not configuration['requested']:return None
    # Omission retains the original two-stream device default.
    trace=configuration['pcm_trace']
    device=VIRTIO_SOUND_DEVICE
    if configuration['queue_size']['requested'] is not None:
        device+=f',queue-size={configuration["queue_size"]["requested"]}'
    if configuration['streams']['requested'] is not None:
        device+=f',streams={configuration["streams"]["requested"]}'
    return device + (',x-pcm-trace='+trace['output_file'].replace(',',',,')
                                  if trace['requested'] else '')


def inspect_wav_capture(configuration,*,qemu_reaped):
    """Bounded header/size observation after close, never a PCM success verdict."""
    if configuration.get('backend')!='wav':return {'requested':False}
    path=Path(configuration['output_file'])
    result={'requested':True,'path':str(path),'header_valid':False,
            'pcm_transfer_verified':False,'audible_playback_verified':False,
            'scope':'WAV header and byte counts only; waveform, guest PCM and audible output need independent verification.'}
    if not qemu_reaped:
        result['state']='unclosed';return result
    if not os.path.lexists(path):
        result['state']='not_created';return result
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid():
        result['state']='unexpected_file';return result
    with path.open('rb') as stream:header=stream.read(44)
    result['file_bytes']=info.st_size
    if len(header)!=44:
        result['state']='short_header';return result
    riff,riff_bytes,wave,fmt,fmt_bytes,codec,channels,rate,byte_rate,align,bits,data,data_bytes=struct.unpack(
        '<4sI4s4sIHHIIHH4sI',header)
    result.update(riff_bytes=riff_bytes,data_bytes=data_bytes,channels=channels,
        sample_rate_hz=rate,sample_bits=bits,block_align=align,
        data_present=data_bytes>0)
    result['header_valid']=(riff==b'RIFF' and wave==b'WAVE' and fmt==b'fmt ' and
        data==b'data' and fmt_bytes==16 and codec==1 and channels==2 and
        rate==48000 and bits==16 and align==4 and byte_rate==192000 and
        riff_bytes==info.st_size-8 and data_bytes==info.st_size-44 and data_bytes%align==0)
    result['state']='header_valid' if result['header_valid'] else 'invalid_header'
    return result


def recovery_host_boot():
    """Read the running Linux boot boundary independently of saved run state."""
    boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip().replace('-','')
    match=re.search(r'^btime ([0-9]+)$',Path('/proc/stat').read_text(),re.M)
    if not re.fullmatch('[0-9a-f]{32}',boot_id) or not match:
        raise ValueError('Current host boot identity is unavailable')
    started=int(match.group(1))
    uptime=float(Path('/proc/uptime').read_text().split()[0])
    if abs(time.time()-uptime-started)>5:
        raise ValueError('Host boot time and uptime disagree')
    return {'boot_id':boot_id,'started_epoch_seconds':started}


def host_reboot_recovery_evidence(result_path,result,request):
    previous=request.get('previous_boot_id','').replace('-','')
    unit=request.get('service_unit','')
    if not re.fullmatch('[0-9a-f]{32}',previous) or not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service',unit):
        raise ValueError('Explicit prior boot ID and service unit are required')
    current=recovery_host_boot()
    started=datetime.fromisoformat(result['started_at_utc']).timestamp()
    if (previous==current['boot_id'] or started>=current['started_epoch_seconds'] or
            result_path.stat().st_mtime>=current['started_epoch_seconds']):
        raise ValueError('Run evidence is not from before the current host boot')
    journal=subprocess.run(['journalctl','--user','-b',previous,'-u',unit,
        '--since',f'@{int(started)-30}','--until',f'@{int(started)+1}',
        '-n','64','-o','json','--no-pager'],capture_output=True,text=True,timeout=10)
    if journal.returncode:raise ValueError('Previous-boot service journal is unavailable')
    matches=[]
    for line in journal.stdout.splitlines():
        row=json.loads(line)
        if (row.get('_BOOT_ID')==previous and row.get('USER_UNIT')==unit and
                row.get('_COMM')=='systemd' and row.get('_UID')==str(os.geteuid()) and
                row.get('MESSAGE_ID')=='39f53479d3a045ac8e11786248231fbf' and
                row.get('JOB_TYPE')=='start' and row.get('JOB_RESULT')=='done' and
                -1<=started-int(row.get('__REALTIME_TIMESTAMP','0'))/1e6<=30):
            matches.append({key:row[key] for key in ('__CURSOR','__REALTIME_TIMESTAMP','_BOOT_ID',
                '_UID','_COMM','USER_UNIT','MESSAGE_ID','JOB_TYPE','JOB_RESULT')})
    if len(matches)!=1:raise ValueError('No unique previous-boot service start matches the saved run')
    boundary=request.get('recovery_host_boot',current)
    if (not isinstance(boundary,dict) or boundary.get('boot_id')==previous or
            not re.fullmatch('[0-9a-f]{32}',str(boundary.get('boot_id',''))) or
            not started<boundary.get('started_epoch_seconds',0)<=current['started_epoch_seconds'] or
            (boundary['started_epoch_seconds']==current['started_epoch_seconds'] and boundary['boot_id']!=current['boot_id'])):
        raise ValueError('Invalid saved host reboot boundary')
    return {'previous_boot_id':previous,'service_unit':unit,'recovery_host_boot':boundary,
            'service_start_journal':matches[0]}


def abandoned_run_context(project, manifest, record, *, host_reboot=None):
    """Bind a dead installed-run owner to its saved argv and exact target aliases."""
    manifest=Path(manifest)
    if manifest.parent.parent!=project/'runs/e05-installed-boot':
        raise ValueError('Abandoned recovery is limited to installed-system runs')
    result_path=manifest.parent/'results.json'
    if result_path.resolve(strict=True)!=result_path:
        raise ValueError('Abandoned run results must not contain symlinks')
    info=result_path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_size>1024*1024:
        raise ValueError('Abandoned run results must be an owned bounded regular file')
    raw=result_path.read_bytes();result=json.loads(raw)
    if result.get('label')!=manifest.parent.name or result.get('state')!='observing':
        raise ValueError('Only an interrupted observing run needs abandoned recovery')
    qemu_pid=result.get('qemu_pid')
    # QEMU retains this line when its controller dies before saving cleanup.
    with (manifest.parent/'qemu-stderr.log').open('rb') as stream:
        stream.seek(max(0,os.fstat(stream.fileno()).st_size-4096))
        tail=stream.read().decode(errors='replace')
    termination=re.search(r'qemu-system-aarch64: terminating on signal 15 from pid ([0-9]+) \([^\n]*\)\s*$',tail)
    reboot=None
    if host_reboot is not None:
        reboot=host_reboot_recovery_evidence(result_path,result,host_reboot)
        pids={'qemu':qemu_pid}
    else:
        if not termination:raise ValueError('Missing retained QEMU controller-termination evidence')
        pids={'qemu':qemu_pid,'controller':int(termination.group(1))}
    service=result.get('guest_files_service',{})
    if service.get('requested'):
        pids['guest_files']=service.get('pid')
    if (not Path('/proc').is_dir() or any(type(pid) is not int or pid<=1 for pid in pids.values()) or
            len(set(pids.values()))!=len(pids)):
        raise ValueError('Invalid abandoned process identities')
    if any(Path('/proc',str(pid)).exists() for pid in pids.values()):
        raise ValueError('Abandoned recovery requires every recorded process PID to be absent')
    args=result.get('execution',{}).get('qemu_argv')
    if not isinstance(args,list):raise ValueError('Missing recorded QEMU argv')
    aliases={}
    for i,arg in enumerate(args[:-1]):
        if arg!='-drive':continue
        fields=dict(value.split('=',1) for value in args[i+1].split(',') if '=' in value)
        path=Path(fields.get('file',''))
        if path.name in ('aux.raw','root.raw'):
            if path.name in aliases and aliases[path.name]!=str(path):
                raise ValueError('Conflicting temporary storage paths')
            aliases[path.name]=str(path)
    if set(aliases)!={'aux.raw','root.raw'}:
        raise ValueError('Missing recorded temporary storage aliases')
    work=Path(aliases['aux.raw']).parent
    if (work.parent!=project/'build' or not work.name.startswith('kernel-usb-') or
            Path(aliases['root.raw']).parent!=work):
        raise ValueError('Temporary storage is outside the owned session directory')
    sockets={}
    for i,arg in enumerate(args[:-1]):
        value=args[i+1]
        path=None
        if arg in ('-qmp','-gdb') and value.startswith('unix:'):
            path=Path(value[5:].split(',')[0])
        elif arg=='-chardev' and value.startswith('socket,'):
            fields=dict(item.split('=',1) for item in value.split(',') if '=' in item)
            if 'path' in fields:path=Path(fields['path'])
        if path is not None:
            if path.parent!=work or path.name not in ('u.sock','q.sock','inspect.sock','kernel-usb.sock','current-process.sock'):
                raise ValueError('Recorded socket is outside the known session endpoints')
            sockets[path.name]=str(path)
    expected_directory=project/'private/installed-boot'/manifest.parent.name/'storage'
    if record.get('directory')!=str(expected_directory):
        raise ValueError('Retained directory does not match abandoned run')
    context={'results_sha256':hashlib.sha256(raw).hexdigest(),'pids':pids,
            'temporary_directory':str(work),'temporary_aliases':aliases,
            'temporary_sockets':sockets,
            'termination_log_line':termination.group(0).strip() if termination else None,
            'files':record['files']}
    if reboot is not None:context['host_reboot']=reboot
    return context


def has_abandoned_recovery(project, manifest, record):
    """Fresh dead-owner checks; the caller must also hold both target flocks."""
    recovery=record.get('abandoned_recovery')
    if not isinstance(recovery,dict):return False
    if (recovery.get('schema') not in (1,2) or recovery.get('both_target_flocks_acquired') is not True or
            record.get('qemu_reaped') is not False or
            record.get('qemu_exit_code') is not None or
            record.get('teardown')!='abandoned_owner_exit_unobserved' or
            record.get('temporary_links_removed') is not True):
        raise ValueError('Invalid abandoned recovery boundary')
    context=abandoned_run_context(project,manifest,record,host_reboot=recovery.get('host_reboot'))
    if recovery['schema']==1:context.pop('temporary_sockets',None)
    if any(recovery.get(key)!=value for key,value in context.items()):
        raise ValueError('Abandoned recovery no longer matches retained evidence')
    if record.get('qemu_pid')!=context['pids']['qemu']:
        raise ValueError('Abandoned recovery QEMU identity mismatch')
    if os.path.lexists(context['temporary_directory']):
        raise ValueError('Abandoned temporary directory has not been removed')
    return True


def latest_retained_shutdown(project, selected_manifest, selected_record):
    """Follow later observed runs of the same two active inodes, not snapshots."""
    expected=selected_record['files']
    candidates={selected_manifest}
    for family in ('e05-signed-restore','e05-installed-boot'):
        candidates.update((project/'runs'/family).glob('*/retained-storage.json'))
    latest=None
    for path in candidates:
        try:
            if path.resolve(strict=True)!=path:continue
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_size>65536:
                    continue
                raw=stream.read(65537)
            record=json.loads(raw)
        except (OSError,ValueError):
            if path==selected_manifest:raise
            continue
        if not isinstance(record,dict) or not isinstance(record.get('files'),dict):continue
        if not all(isinstance(record['files'].get(name),dict) for name in expected):continue
        if (record.get('active_target_alias_not_snapshot') is not True or
                any(tuple(record.get('files',{}).get(name,{}).get(key) for key in ('device','inode','bytes'))!=
                    tuple(target[key] for key in ('device','inode','bytes')) for name,target in expected.items())):
            continue
        order=(info.st_mtime_ns,str(path))
        if latest is None or order>latest[0]:latest=(order,path,record,raw)
    if latest is None:raise ValueError('No retained shutdown record matches the current target inodes')
    _,path,record,raw=latest
    return {'run':path.parent.name,'manifest':str(path),'manifest_sha256':hashlib.sha256(raw).hexdigest(),
        'directory':record.get('directory'),'files':record['files'],
        'qemu_exit_code':record.get('qemu_exit_code'),'qemu_reaped':record.get('qemu_reaped'),
        'qemu_pid':record.get('qemu_pid'),'qmp_lifecycle':record.get('qmp_lifecycle'),
        'abandoned_recovery':record.get('abandoned_recovery'),
        'guest_shutdown_observed':has_guest_shutdown_evidence(record),
        'temporary_links_removed':record.get('temporary_links_removed'),'teardown':record.get('teardown'),
        'scope':'Latest owned retention record by write time for these exact two inodes, across restore and cold-boot aliases.'}


class FirmwareSession:
    def __init__(self,logs:Path,trace_block_io:bool=False,aux_size_mib:int=32,
                 ctrr_model:bool=False,pac_model:bool=False,system_disk:bool=False,
                 blank_root_disk:bool=False,resume_storage_run:str|None=None,
                 virtio_usb:bool=False,allow_unclean_storage:bool=False,
                 pauth_algorithm:str='qarma5',tcg_thread:str='auto',
                 usb_input:bool=False,native_window:bool=False,gpu_icd:str|None=None,
                 readonly_rsp:bool=False,user_network:bool=False,exact_half_blend:bool=False,
                 display_refresh_hz:float|None=None,vcpus:int=2,pauth_memo:bool=False,
                 ram_gib:int=4,virtio_sound:bool=False,sound_backend:str='none',
                 sound_pcm_trace:bool=False,sound_queue_size:int|None=None,sound_streams:int|None=None,pauth_memo_entries:int|None=None,
                 pauth_memo_stats:bool=False,vcpu_affinity:str|None=None,
                 webkit_timeout_diagnostic:bool=False,net_control_queue_size:int|None=None,
                 sync_stats:bool=False):
        if type(vcpus) is not int or vcpus not in (2,4,8):
            raise ValueError('Only explicit 2/4/8-vCPU profiles are supported')
        self.vcpus=vcpus
        self.vcpu_affinity_requested=vcpu_affinity
        self.vcpu_affinity_cpus=parse_vcpu_affinity(vcpu_affinity)
        self.vcpu_affinity_observation={'requested':vcpu_affinity,
            'requested_cpu_ids':self.vcpu_affinity_cpus,'state':'pending' if vcpu_affinity is not None else 'not_requested',
            'verified':False,'verification':'startup_only',
            'scope':'Explicit host logical-CPU masks on owned QMP-identified TCG vCPU threads before initial cont. '
                    'Other QEMU/controller thread masks are checked at startup; no core-type inference, '
                    'continuous placement, CPU-time attribution or performance claim. Masks of later-created '
                    'threads, which may inherit their creator\'s affinity, are not verified.'}
        if type(ram_gib) is not int or ram_gib not in (4,8):
            raise ValueError('Only explicit 4/8-GiB RAM profiles are supported')
        self.ram_gib=ram_gib
        self.ram_bytes=ram_gib * 1024**3
        if sum((bool(system_disk),bool(blank_root_disk),resume_storage_run is not None))>1:
            raise ValueError('SystemOS source, blank target and retained target are mutually exclusive')
        if resume_storage_run is not None and (not isinstance(resume_storage_run,str) or
                not re.fullmatch('[A-Za-z0-9_-]{1,60}',resume_storage_run)):
            raise ValueError('Invalid retained storage run label')
        if (blank_root_disk or resume_storage_run is not None) and aux_size_mib!=32:
            raise ValueError('Blank or retained target requires the 32MiB AUX profile')
        if allow_unclean_storage and resume_storage_run is None:
            raise ValueError('Unclean-state reuse requires a retained source run')
        if type(usb_input) is not bool or type(native_window) is not bool:
            raise ValueError('USB input and native window selections must be boolean')
        if gpu_icd is not None:
            gpu_icd=str(gpu_icd)
            if gpu_icd not in ('/usr/share/vulkan/icd.d/nvidia_icd.json',
                               '/usr/share/vulkan/icd.d/intel_icd.json'):
                raise ValueError('Select the local NVIDIA or Intel hardware Vulkan ICD')
            if not Path(gpu_icd).is_file():
                raise ValueError('Selected hardware Vulkan ICD is unavailable')
        self.usb_input=usb_input
        self.native_window=native_window
        self.gpu_icd=gpu_icd
        self.gpu_inventory=None
        if type(readonly_rsp) is not bool:
            raise ValueError('Read-only RSP endpoint selection must be boolean')
        self.readonly_rsp=readonly_rsp
        self.rsp_path=None
        if type(user_network) is not bool:
            raise ValueError('User networking selection must be boolean')
        self.user_network=user_network
        self.net_control_queue_size=validate_net_control_queue_size(user_network,net_control_queue_size)
        self.sound=virtio_sound_configuration(virtio_sound,sound_backend,logs,
                                             pcm_trace=sound_pcm_trace,queue_size=sound_queue_size,streams=sound_streams)
        self.virtio_sound=virtio_sound
        if type(exact_half_blend) is not bool:
            raise ValueError('Exact half-blend selection must be boolean')
        self.exact_half_blend=exact_half_blend
        self.graphics_precision_environment=None
        self.requested_refresh_value=window_refresh_value(display_refresh_hz,native_window)
        self.display_refresh_hz=display_refresh_hz
        self.effective_window_refresh=None
        self.pauth_algorithm=pauth_algorithm
        self.cpu_model=cpu_model(pauth_algorithm,pauth_memo=pauth_memo,
                                 pauth_memo_entries=pauth_memo_entries,pauth_memo_stats=pauth_memo_stats,
                                 webkit_timeout_diagnostic=webkit_timeout_diagnostic,
                                 sync_stats=sync_stats)
        self.webkit_timeout_diagnostic=webkit_timeout_diagnostic
        self.sync_stats=sync_stats
        self.pauth_memo=pauth_memo
        self.pauth_memo_entries=pauth_memo_entries
        self.pauth_memo_stats=pauth_memo_stats
        self.tcg_thread_requested=tcg_thread
        self.accel_argument=tcg_accel_argument(tcg_thread)
        self.vcpu_thread_observation={'observed':False,'state':'not_started'}
        self.logs=logs; logs.mkdir(parents=True,exist_ok=True)
        self.proc=None;self.qmp=None;self.qf=None;self.usb=None;self.err=None;self.temp=None
        self.kernel_usb=None;self.kernel_avp=None;self.virtio_usb=virtio_usb
        self.events=[]
        self.lifecycle_events=[]
        self.lifecycle_event_count=0
        self.last_shutdown_or_reset_event=None
        self.host_quit_requested=False
        self.qmp_tail_drain={'state':'not_attempted'}
        self.qmp_send_failure_drain=None
        self.guest_shutdown_wait={'state':'not_attempted'}
        self.lifecycle_persistence_error=None
        self.lifecycle_sha256=None
        self._teardown='not_closed'
        self.trace_block_io=trace_block_io
        self.ctrr_model=ctrr_model
        self.pac_model=pac_model
        self.system_disk=system_disk
        self.blank_root_disk=blank_root_disk
        self.resume_storage_run=resume_storage_run
        self.allow_unclean_storage=allow_unclean_storage
        self.source_storage_shutdown=None
        self._storage_locks=[]
        self.storage_profile=('retained-root-target' if resume_storage_run is not None else
                              'blank-root-target' if blank_root_disk else
                              'systemos-readonly-source-cow' if system_disk else 'firmware-stub')
        self.storage_info=None
        self.retained_storage=None
        self.reader_proc=None;self.nbd_proc=None;self.system_socket=None
        self.root_overlay=None
        self.reader_log=None;self.nbd_log=None;self.system_descriptor=None
        self._parent_pid=os.getpid()
        self._signal_handlers={}
        self._signal_received=None
        self._signal_handler_active=False
        self._closed=False
        self._closing=False
        if aux_size_mib not in (4,32):raise ValueError('Only controlled 4/32MiB AUX profiles')
        self.aux_size_mib=aux_size_mib

    def capture_vcpu_threads(self):
        """Read host IDs only on this session's owned startup QMP connection."""
        observation={'observed':False,'source':'query-cpus-fast before initial cont',
            'requested_mode':self.tcg_thread_requested,'configured_vcpu_count':self.vcpus,
            'scope':'Host thread IDs assigned to guest vCPUs at the startup pause; '
                    'not the total number of QEMU, I/O or helper threads.'}
        self.vcpu_thread_observation=observation
        try:
            rows=self.request('query-cpus-fast')
        except RuntimeError as error:
            # request() has consumed this complete QMP error. Framing/timeouts
            # still propagate so the owned connection cannot be misused.
            observation.update(state='query_unavailable',error=repr(error))
            if self.tcg_thread_requested!='auto' or self.vcpus!=2:raise
            return observation
        try:
            if not isinstance(rows,list) or not 1<=len(rows)<=64:
                raise ValueError('Unexpected QMP vCPU list')
            cpus=[]
            for row in rows:
                index,thread=row.get('cpu-index'),row.get('thread-id')
                if (type(index) is not int or index<0 or
                        type(thread) is not int or thread<=0):
                    raise ValueError('QMP vCPU index or host thread ID missing')
                cpus.append({'cpu_index':index,'host_thread_id':thread})
            if len({row['cpu_index'] for row in cpus})!=len(cpus):
                raise ValueError('Duplicate QMP vCPU index')
        except (AttributeError,ValueError) as error:
            observation.update(state='invalid_response',error=repr(error))
            if self.tcg_thread_requested!='auto' or self.vcpus!=2:raise ValueError(str(error)) from error
            return observation
        count=len({row['host_thread_id'] for row in cpus})
        observation.update(observed=True,state='observed',vcpus=cpus,
            vcpu_count=len(cpus),distinct_host_thread_ids=count,
            configured_vcpus_match=({row['cpu_index'] for row in cpus}==set(range(self.vcpus))),
            requested_mode_verified=None)
        if self.vcpus!=2 and not observation['configured_vcpus_match']:
            raise RuntimeError('Observed CPU indices do not match the requested vCPU profile')
        if self.tcg_thread_requested!='auto':
            expected=1 if self.tcg_thread_requested=='single' else self.vcpus
            observation['requested_mode_verified']=(observation['configured_vcpus_match'] and count==expected)
            if not observation['requested_mode_verified']:
                observation['state']='requested_mode_mismatch'
                raise RuntimeError('Observed vCPU host threads do not match requested TCG threading mode')
        return observation

    def apply_vcpu_affinity(self):
        """Apply only to verified owned vCPU TIDs while the new VM is paused."""
        observation=self.vcpu_affinity_observation
        if self.vcpu_affinity_cpus is None:
            return observation
        observation.update(state='verifying',verified=False,applied_thread_ids=[])
        try:
            if self.proc is None or self.proc.poll() is not None:
                raise RuntimeError('vCPU affinity requires this session\'s live QEMU child')
            pid=self.proc.pid
            parent=os.getpid()
            status=(Path('/proc')/str(pid)/'status').read_text()
            match=re.search(r'^PPid:\s+(\d+)$',status,re.M)
            if pid==parent or not match or int(match[1])!=parent:
                raise RuntimeError('vCPU affinity target is not the directly owned QEMU child')
            state=self.request('query-status')
            if not isinstance(state,dict) or state.get('running') is not False:
                raise RuntimeError('vCPU affinity must precede the initial cont')
            topology=self.vcpu_thread_observation
            if not topology.get('observed') or not topology.get('configured_vcpus_match'):
                raise RuntimeError('vCPU affinity requires verified QMP CPU indices and host thread IDs')
            targets={row['host_thread_id'] for row in topology['vcpus']}
            before=host_thread_affinities(pid)
            controller_before=host_thread_affinities(parent)
            requested=set(parse_vcpu_affinity(self.vcpu_affinity_requested))
            if not targets<=before.keys() or pid in targets:
                raise RuntimeError('QMP vCPU TIDs are not owned CPU worker threads')
            if any(not requested<=set(before[tid]['cpus']) for tid in targets):
                raise RuntimeError('Requested vCPU mask would expand an existing worker mask')
            observation.update(qemu_pid=pid,controller_pid=parent,state='applying')

            def check_identity(tid):
                path=Path(f'/proc/{pid}/task/{tid}/stat')
                ticks=int(path.read_text().rsplit(')',1)[1].split()[19])
                if self.proc.poll() is not None or ticks!=before[tid]['start_time_ticks']:
                    raise RuntimeError('Owned vCPU thread identity changed before affinity verification')

            for tid in sorted(targets):
                check_identity(tid)
                os.sched_setaffinity(tid,requested)
                observation['applied_thread_ids'].append(tid)
                if os.sched_getaffinity(tid)!=requested:
                    raise RuntimeError('Host did not retain the exact requested vCPU affinity mask')
                check_identity(tid)
            after=host_thread_affinities(pid)
            controller_after=host_thread_affinities(parent)
            other_before={tid:value for tid,value in before.items() if tid not in targets}
            other_after={tid:value for tid,value in after.items() if tid not in targets}
            if other_before!=other_after or controller_before!=controller_after:
                raise RuntimeError('Non-vCPU or controller thread identities/masks changed during affinity setup')
            for tid in targets:
                if (tid not in after or after[tid]['start_time_ticks']!=before[tid]['start_time_ticks'] or
                        after[tid]['cpus']!=sorted(requested)):
                    raise RuntimeError('vCPU affinity startup verification failed')
            observation.update(state='verified',verified=True,
                verified_at_utc=datetime.now(timezone.utc).isoformat(),
                vcpus=[{**row,'before_cpu_ids':before[row['host_thread_id']]['cpus'],
                        'actual_cpu_ids':after[row['host_thread_id']]['cpus']} for row in topology['vcpus']],
                other_qemu_threads=other_after,controller_threads=controller_after,
                non_vcpu_masks_unchanged=True,controller_masks_unchanged=True)
            return observation
        except Exception as error:
            observation.update(state='failed',verified=False,error=repr(error))
            raise

    def capture_pauth_memo_statistics(self):
        """Aggregate counters only, on this session's owned QMP connection."""
        if not self.pauth_memo_stats:
            return {'requested':False,'state':'not_requested'}
        start=time.monotonic()
        rows=self.request('query-cpus-fast')
        if (not isinstance(rows,list) or len(rows)!=self.vcpus or
                {row.get('cpu-index') for row in rows}!=set(range(self.vcpus))):
            raise ValueError('Unexpected CPU set for PAuth memo statistics')
        counters=[]
        for row in rows:
            path=row.get('qom-path')
            if not isinstance(path,str) or not path.startswith('/machine/'):
                raise ValueError('Missing owned QOM CPU path')
            values={}
            for name in ('hits','misses'):
                value=self.request('qom-get',{'path':path,'property':'x-pauth-memo-'+name})
                if type(value) is not int or not 0<=value<2**64:
                    raise ValueError('Invalid aggregate PAuth memo counter')
                values[name]=value
            counters.append({'cpu_index':row['cpu-index'],**values})
        return {'requested':True,'state':'observed',
                'observed_at_utc':datetime.now(timezone.utc).isoformat(),
                'read_seconds':time.monotonic()-start,
                'entries_per_cpu':self.pauth_memo_entries or 64,
                'vcpus':sorted(counters,key=lambda row:row['cpu_index']),
                'scope':'Aggregate per-vCPU counters since CPU reset/post-load; separate QOM reads are not atomic pairs. No keys, pointers or PAC inputs. Counter resets and sampling intervention must be considered before comparing intervals.'}

    def execution_metadata(self):
        """Separate requested acceleration from observed vCPU thread topology."""
        return {'vcpus_requested':self.vcpus,'tcg_thread_requested':self.tcg_thread_requested,
            'ram_gib_requested':self.ram_gib,'ram_bytes_requested':self.ram_bytes,
            'qemu_accel_argument':self.accel_argument,
            'pauth':{'algorithm':self.pauth_algorithm,'memo_requested':self.pauth_memo,
                     'memo_entries_requested':self.pauth_memo_entries,
                     'memo_effective_entries':(self.pauth_memo_entries or 64) if self.pauth_memo else None,
                     'memo_stats_requested':self.pauth_memo_stats,
                     'scope':'Requested CPU configuration; correctness and speed require separate validation'},
            'qemu_argv':list(self.args) if self.proc is not None else None,
            'qemu_vcpu_threads':dict(self.vcpu_thread_observation),
            'vcpu_affinity':dict(self.vcpu_affinity_observation),
            'webkit_timeout_diagnostic':{'requested':self.webkit_timeout_diagnostic,
                'schema':2,
                'publication_protocol':'runtime-pc-slide-armed-bool-v2' if self.webkit_timeout_diagnostic else None,
                'diagnostic_only':self.webkit_timeout_diagnostic,
                'state':'configured_unarmed' if self.webkit_timeout_diagnostic else 'off',
                'original_deadline_performance_pass':False,
                'scope':'Startup configuration only. Probe validates its own metadata; host publishes only an armed boolean, and QEMU derives candidate bases from actual PCs. Runtime counters do not by themselves identify a Safari outcome or an original 3-second performance result.'},
            'presentation':{'native_window_requested':self.native_window,
                'usb_input_requested':self.usb_input,
                'input_pci_address':'00:04.0' if self.usb_input else None,
                'reims_vgpu_window':'1' if self.native_window else '0',
                'vulkan_icd':self.gpu_icd,'gpu_inventory':self.gpu_inventory,
                'exact_half_blend_requested':self.exact_half_blend,
                'reims_vgpu_exact':self.graphics_precision_environment,
                'display_refresh_hz_requested':self.display_refresh_hz,
                'reims_vgpu_window_refresh':self.effective_window_refresh,
                'refresh_scope':'Offered display workload configuration; not measured FPS, physical scanout or latency',
                'precision_scope':'Selected runtime policies, not general Metal bit-exact certification'},
            'readonly_rsp':{'requested':self.readonly_rsp,
                            'path':str(self.rsp_path) if self.rsp_path else None,
                            'scope':'Private local GDB endpoint; read-only is enforced by the sampling client, not by the server protocol'},
            'network':{'requested':self.user_network,'backend':'user' if self.user_network else None,
                'device':'virtio-net-pci' if self.user_network else None,
                'pci_address':'00:05.0' if self.user_network else None,
                'ascending_capabilities_requested':self.user_network,
                'control_queue_size':{'requested':self.net_control_queue_size,'default':64,
                    'configured_maximum':(self.net_control_queue_size or 64) if self.user_network else None,
                    'actual_device_arguments':([self.args[i+1] for i,arg in enumerate(self.args[:-1])
                        if arg=='-device' and self.args[i+1].split(',',1)[0]=='virtio-net-pci']
                        if self.proc is not None else None),
                    'guest_selected':None,'runtime_verified':False,
                    'scope':'Control queue configured capacity and recorded launch argv only. RX/TX queue settings are unchanged; guest queue layout, ACK consumption and networking require runtime validation.'},
                'host_forwarding':False,'physical_bridge':False,'guest_connectivity':'unknown',
                'scope':'Requested configuration only; guest driver, DHCP, DNS and connectivity require runtime validation'},
            'audio':dict(self.sound)}

    def network_arguments(self):
        if not self.user_network:
            return ['-nic','none']
        return ['-netdev','user,id=guestnet',
                '-device',virtio_net_device_argument(self.net_control_queue_size)]

    def sound_arguments(self):
        if not self.virtio_sound:
            return []
        if self.sound['backend']=='wav' and os.path.lexists(self.sound['output_file']):
            raise ValueError('Refusing to overwrite existing run WAV output')
        trace=self.sound['pcm_trace']
        if trace['requested'] and os.path.lexists(trace['output_file']):
            raise ValueError('Refusing to overwrite existing run PCM boundary trace')
        return ['-audiodev',virtio_sound_backend_argument(self.sound),
                '-device',virtio_sound_device_argument(self.sound)]

    def input_arguments(self):
        # Slot 3 remains the restore USB device controller. This independent
        # host controller exposes only synthetic VM-local keyboard/pointer.
        if not self.usb_input:
            return []
        return ['-device','qemu-xhci,id=input-xhci,addr=0x4',
                '-device','usb-kbd,id=input-keyboard,bus=input-xhci.0,port=1',
                '-device','usb-tablet,id=input-tablet,bus=input-xhci.0,port=2']

    def qemu_environment(self):
        env=dict(os.environ,REIMS_VGPU_WINDOW='1' if self.native_window else '0',
                 REIMS_VGPU_LOG='info' if self.native_window else 'warn')
        if self.exact_half_blend:
            modes=[mode.strip() for mode in env.get('REIMS_VGPU_EXACT','').split(',') if mode.strip()]
            if 'half_blend' not in modes:modes.append('half_blend')
            env['REIMS_VGPU_EXACT']=','.join(modes)
        self.graphics_precision_environment=env.get('REIMS_VGPU_EXACT')
        if self.requested_refresh_value is not None:
            env['REIMS_VGPU_WINDOW_REFRESH']=self.requested_refresh_value
        self.effective_window_refresh=env.get('REIMS_VGPU_WINDOW_REFRESH')
        if self.native_window:
            env.pop('SDL_VIDEODRIVER',None)
            env.update(SDL_VIDEO_DRIVER='x11',REIMS_VGPU_WINDOW_BORDERLESS='0',
                       REIMS_VGPU_STATS='1')
        if self.gpu_icd is not None:
            for key in ('VK_ICD_FILENAMES','VK_ADD_DRIVER_FILES','VK_INSTANCE_LAYERS',
                        'VK_LOADER_LAYERS_ENABLE','VK_LOADER_DRIVERS_SELECT',
                        'VK_LOADER_DRIVERS_DISABLE'):
                env.pop(key,None)
            env.update(VK_DRIVER_FILES=self.gpu_icd,VK_LOADER_LAYERS_DISABLE='*')
        return env

    def inspect_selected_gpu(self,env):
        if self.gpu_icd is None:
            return
        inventory=subprocess.run(['vulkaninfo','--summary'],env=env,capture_output=True,
                                 text=True,timeout=30)
        path=self.logs/'vulkan-device.log'
        path.write_text(inventory.stdout+'\nSTDERR:\n'+inventory.stderr)
        names=re.findall(r'deviceName\s*=\s*(.+)',inventory.stdout)
        types=re.findall(r'deviceType\s*=\s*(.+)',inventory.stdout)
        if inventory.returncode or len(names)!=1 or len(types)!=1 or types[0].strip() not in (
                'PHYSICAL_DEVICE_TYPE_DISCRETE_GPU','PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU'):
            raise RuntimeError('Selected ICD must enumerate exactly one hardware GPU; see vulkan-device.log')
        self.gpu_inventory={'name':names[0].strip(),'type':types[0].strip(),
            'icd':self.gpu_icd,'log':str(path),
            'scope':'Independent inventory under the QEMU environment; runtime selection is verified separately.'}

    def _child_parent_death_signal(self):
        """Terminate a helper if this controller dies before normal cleanup."""
        if sys.platform != 'linux':
            return
        libc=ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
            error=ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        # Close the small fork/exec race: if the parent vanished before prctl,
        # terminate this child immediately instead of becoming an orphan.
        if os.getppid()!=self._parent_pid:
            os.kill(os.getpid(), signal.SIGTERM)

    def _handle_signal(self, signum, _frame):
        if self._signal_received is None:
            self._signal_received=signum
        if self._signal_handler_active:
            return
        self._signal_handler_active=True
        raise FirmwareSessionInterrupted(signum)

    def _install_signal_handlers(self):
        if threading.current_thread() is not threading.main_thread():
            return
        for signum in (signal.SIGTERM, signal.SIGINT):
            self._signal_handlers[signum]=signal.getsignal(signum)
            signal.signal(signum,self._handle_signal)

    def _restore_signal_handlers(self):
        for signum,previous in list(self._signal_handlers.items()):
            try:
                if signal.getsignal(signum)==self._handle_signal:
                    signal.signal(signum,previous)
            except (OSError,ValueError):
                pass
        self._signal_handlers.clear()
    def prepare_local_storage(self,work:Path):
        """Create owned sparse files; no firmware or external source is opened."""
        if self.resume_storage_run is not None:
            self._link_retained_storage(work)
            return
        root_bytes=BLANK_ROOT_BYTES if self.blank_root_disk else STUB_ROOT_BYTES
        for name,size in [('aux.raw',self.aux_size_mib*1024**2),('root.raw',root_bytes)]:
            with (work/name).open('xb') as stream:stream.truncate(size)
    def _release_storage_locks(self):
        for fd in self._storage_locks:os.close(fd)
        self._storage_locks=[]
    def _link_retained_storage(self,work:Path):
        """Import only closed owned target inodes, holding their kernel flocks."""
        if self._storage_locks:raise ValueError('Retained storage is already prepared')
        label=self.resume_storage_run
        project=ROOT.resolve()
        manifest=project/'runs/e05-signed-restore'/label/'retained-storage.json'
        directory=project/'private/signed-restore'/label/'storage'
        # Reject symlinks in either complete path, including the directory chain.
        if manifest.resolve(strict=True)!=manifest or directory.resolve(strict=True)!=directory:
            raise ValueError('Retained storage paths must not contain symlinks')
        manifest_fd=os.open(manifest,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
        with os.fdopen(manifest_fd,'r') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_size>65536:
                raise ValueError('Retained manifest must be an owned regular file')
            record=json.load(stream)
        info=directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o077:
            raise ValueError('Retained storage must be an owned private directory')
        exit_code=record.get('qemu_exit_code')
        if (record.get('qemu_reaped') is not True or
                record.get('temporary_links_removed') is not True or
                not isinstance(exit_code,int) or isinstance(exit_code,bool)):
            raise ValueError('Source QEMU must be reaped and its temporary links removed')
        clean=record.get('teardown')=='qmp_quit' and exit_code==0
        guest_shutdown=has_guest_shutdown_evidence(record)
        if not (clean or guest_shutdown) and not self.allow_unclean_storage:
            raise ValueError('Source lacks normal QMP exit or observed guest shutdown; explicit unclean-state reuse is required')
        self.source_storage_shutdown={'run':label,'qemu_exit_code':exit_code,
            'teardown':record.get('teardown'),'normal_qmp_exit':clean,
            'guest_shutdown_observed':guest_shutdown,
            'explicit_unclean_reuse':not (clean or guest_shutdown) and self.allow_unclean_storage}
        if (record.get('profile') not in ('blank-root-target','retained-root-target') or
                record.get('directory')!=str(directory) or
                record.get('method')!='hard_links_to_owned_active_target_inodes' or
                record.get('active_target_alias_not_snapshot') is not True or
                set(record.get('files',{}))!={'aux.raw','root.raw'}):
            raise ValueError('Manifest is not an owned retained raw target')
        linked=[]
        try:
            for name,size in [('aux.raw',32*1024**2),('root.raw',BLANK_ROOT_BYTES)]:
                source=directory/name
                fd=os.open(source,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
                self._storage_locks.append(fd)
                info=os.fstat(fd);expected=record['files'][name]
                if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or
                        info.st_size!=size or expected.get('path')!=str(source) or
                        (expected.get('device'),expected.get('inode'),expected.get('bytes'))!=
                        (info.st_dev,info.st_ino,size) or
                        expected.get('owner_uid',os.geteuid())!=info.st_uid):
                    raise ValueError('Retained file path, inode, size or owner differs from manifest')
                try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError as exc:raise ValueError('Retained target is already in use') from exc
            latest=latest_retained_shutdown(project,manifest,record)
            recovered=(self.allow_unclean_storage and has_abandoned_recovery(
                project,Path(latest['manifest']),latest))
            if (latest['qemu_reaped'] is not True or latest['temporary_links_removed'] is not True or
                    type(latest['qemu_exit_code']) is not int) and not recovered:
                raise ValueError('Latest run of these target inodes must be reaped and its temporary links removed')
            latest_clean=latest['teardown']=='qmp_quit' and latest['qemu_exit_code']==0
            latest_guest_shutdown=has_guest_shutdown_evidence(latest)
            if not (latest_clean or latest_guest_shutdown) and not self.allow_unclean_storage:
                raise ValueError('Latest target run lacks normal QMP exit or observed guest shutdown; explicit unclean-state reuse is required')
            self.source_storage_shutdown={**latest,'selected_source_run':label,
                'selected_source_shutdown':self.source_storage_shutdown,
                'normal_qmp_exit':latest_clean,
                'guest_shutdown_observed':latest_guest_shutdown,
                'explicit_unclean_reuse':not (latest_clean or latest_guest_shutdown) and self.allow_unclean_storage}
            # No source content writes or truncation; only new exclusive links.
            for fd,name in zip(self._storage_locks,('aux.raw','root.raw')):
                target=work/name
                os.link(directory/name,target,follow_symlinks=False)
                linked.append(target)
                info=target.lstat();opened=os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or
                        (info.st_dev,info.st_ino,info.st_size,info.st_uid)!=
                        (opened.st_dev,opened.st_ino,opened.st_size,os.geteuid())):
                    raise ValueError('Retained source identity changed while linking')
        except BaseException:
            for target in linked:target.unlink()
            self._release_storage_locks()
            raise
    def storage_arguments(self,work:Path):
        """The same block layout is exercised by the paused synthetic fixture."""
        if self.system_disk:
            root_pflash=f'file=nbd+unix:///?socket={self.system_socket},format=raw,if=pflash,index=1,readonly=on'
            root_virtio=f'file={self.root_overlay},if=none,id=root,format=qcow2'
        else:
            root_pflash=f'file={work/"root.raw"},format=raw,if=pflash,index=1'
            root_virtio=f'file={work/"root.raw"},if=none,id=root,format=raw'
        args=['-drive',f'file={work/"aux.raw"},format=raw,if=pflash,index=0',
              '-drive',root_pflash]
        if self.system_disk or self.blank_root_disk or self.resume_storage_run is not None:
            args += ['-drive',f'file={work/"aux.raw"},if=none,id=aux,format=raw',
                     '-device','vmapple-virtio-blk-pci,variant=aux,drive=aux,share-rw=on',
                     '-drive',root_virtio,
                     '-device','vmapple-virtio-blk-pci,variant=root,drive=root,share-rw=on']
        return args
    def describe_storage(self,blocks):
        devices={entry['device']:entry['inserted'] for entry in blocks}
        root=devices['root' if self.system_disk or self.blank_root_disk or self.resume_storage_run is not None else 'pflash1']
        self.storage_info={'profile':self.storage_profile,
                           'root_target_bytes':root['image']['virtual-size'],
                           'root_target_format':root['drv'],
                           'pflash1_readonly':devices['pflash1']['ro'],
                           'aux_bytes':devices['pflash0']['image']['virtual-size'],
                           'virtio_aux_and_root_present':'aux' in devices and 'root' in devices,
                           'systemos_source_selected':self.system_disk,
                           'blank_target_at_creation':self.blank_root_disk,
                           'installed_system_proven':False}
        if self.resume_storage_run is not None:
            self.storage_info['source_run']=self.resume_storage_run
            self.storage_info['source_shutdown']=self.source_storage_shutdown
        return self.storage_info
    def retain_blank_storage(self,destination:Path):
        """Keep the owned active target inodes after temporary-session cleanup.

        These links follow subsequent guest writes; they are not snapshots or
        a claim of a clean guest shutdown. Only this session's owned raw target
        or its controlled retained source is accepted here.
        """
        if not (self.blank_root_disk or self.resume_storage_run is not None) or self.system_disk or not self.temp:
            raise ValueError('Retention requires this session\'s owned raw target')
        if self.retained_storage is not None:
            raise ValueError('This session already has retained target links')
        destination=Path(destination).absolute()
        parent=destination.parent
        parent_info=parent.lstat()
        if (not stat.S_ISDIR(parent_info.st_mode) or parent.resolve()!=parent or
                parent_info.st_uid!=os.geteuid() or parent_info.st_mode & 0o077):
            raise ValueError('Retained storage requires an owned private parent directory')
        sources=[]
        for name,size in [('aux.raw',32*1024**2),('root.raw',BLANK_ROOT_BYTES)]:
            source=Path(self.temp.name)/name
            info=source.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or
                    info.st_size!=size or info.st_dev!=parent_info.st_dev):
                raise ValueError('Unexpected target file, size, owner or filesystem')
            sources.append((source,info))
        destination.mkdir(mode=0o700,exist_ok=False)
        linked=[]
        try:
            for source,info in sources:
                source.chmod(0o600)
                target=destination/source.name
                os.link(source,target,follow_symlinks=False)
                linked.append(target)
                linked_info=target.lstat()
                if (linked_info.st_dev,linked_info.st_ino)!=(info.st_dev,info.st_ino):
                    raise RuntimeError('Retained target identity changed')
            self.retained_storage={
                'directory':str(destination), 'profile':self.storage_profile,
                'method':'hard_links_to_owned_active_target_inodes',
                'active_target_alias_not_snapshot':True,
                'blank_target_at_creation':self.blank_root_disk,
                'installed_system_proven':False, 'clean_guest_shutdown_proven':False,
                'qemu_binary':self.args[0],
                'qemu_binary_sha256':hashlib.sha256(Path(self.args[0]).read_bytes()).hexdigest(),
                'files':{source.name:{'path':str(destination/source.name),
                    'device':info.st_dev,'inode':info.st_ino,'bytes':info.st_size}
                    for source,info in sources},
                'qemu_reaped':False, 'temporary_links_removed':False}
            for row in self.retained_storage['files'].values():row['owner_uid']=os.geteuid()
            if self.resume_storage_run is not None:
                self.retained_storage['source_run']=self.resume_storage_run
                self.retained_storage['source_shutdown']=self.source_storage_shutdown
            self._save_retained_storage()
        except BaseException:
            self.retained_storage=None
            for target in linked:target.unlink(missing_ok=True)
            destination.rmdir()
            raise
        return self.retained_storage
    def _save_retained_storage(self):
        if self.retained_storage is not None:
            (self.logs/'retained-storage.json').write_text(
                json.dumps(self.retained_storage,indent=2)+'\n')
    def lifecycle_metadata(self):
        reaped=bool(self.proc and self.proc.poll() is not None)
        report={'source':LIFECYCLE_SOURCE,'path':str(self.logs/'qmp-lifecycle.json'),
            'sha256':self.lifecycle_sha256,'qemu_pid':self.proc.pid if self.proc else None,
            'qemu_exit_code':self.proc.returncode if self.proc else None,
            'qemu_reaped':reaped,'teardown':self._teardown,
            'event_count':self.lifecycle_event_count,'retained_event_count':len(self.lifecycle_events),
            'dropped_event_count':self.lifecycle_event_count-len(self.lifecycle_events),
            'host_quit_requested':self.host_quit_requested,
            'last_shutdown_or_reset_event':self.last_shutdown_or_reset_event,
            'guest_shutdown_wait':dict(self.guest_shutdown_wait),
            'tail_drain':dict(self.qmp_tail_drain),'persistence_error':self.lifecycle_persistence_error,
            'scope':'QMP lifecycle origin and process exit only; filesystem cleanliness is not established.'}
        if self.qmp_send_failure_drain is not None:
            report['send_failure_drain']=dict(self.qmp_send_failure_drain)
        report['guest_shutdown_observed']=has_guest_shutdown_evidence({
            'qmp_lifecycle':report,'qemu_pid':report['qemu_pid'],'teardown':self._teardown,
            'qemu_exit_code':report['qemu_exit_code'],'qemu_reaped':reaped})
        return report

    def _save_qmp_lifecycle(self):
        report=self.lifecycle_metadata()
        report.pop('sha256')
        report['events']=self.lifecycle_events
        raw=(json.dumps(report,indent=2)+'\n').encode()
        try:
            (self.logs/'qmp-lifecycle.json').write_bytes(raw)
            self.lifecycle_sha256=hashlib.sha256(raw).hexdigest()
        except OSError as error:
            # Optional evidence must not strand an otherwise owned VM or locks.
            self.lifecycle_persistence_error=f'{type(error).__name__}: {error}'[:256]

    def _record_qmp_event(self,response,phase):
        self.events.append(response)
        if response.get('event') not in LIFECYCLE_EVENTS:return
        data=response.get('data') or {}
        row={'event':response['event'],'data':{},'receive_phase':phase,
             'observed_at_utc':datetime.now(timezone.utc).isoformat(),
             'observed_monotonic':time.monotonic()}
        if isinstance(data,dict):
            if type(data.get('guest')) is bool:row['data']['guest']=data['guest']
            for key in ('reason','action'):
                if isinstance(data.get(key),str):row['data'][key]=data[key][:128]
        timestamp=response.get('timestamp')
        if isinstance(timestamp,dict):
            row['timestamp']={key:timestamp[key] for key in ('seconds','microseconds')
                              if type(timestamp.get(key)) is int}
        self.lifecycle_event_count+=1
        self.lifecycle_events.append(row)
        del self.lifecycle_events[:-LIFECYCLE_EVENT_LIMIT]
        if row['event'] in ('SHUTDOWN','RESET'):self.last_shutdown_or_reset_event=row
        self._save_qmp_lifecycle()

    def _drain_qmp_tail(self):
        """Read buffered tail only after wait/poll reaped the owned child; no send."""
        if self.proc is None or self.proc.poll() is None:
            self.qmp_tail_drain={'state':'skipped_process_not_reaped'}
            return
        if self.qmp is None or self.qf is None:
            self.qmp_tail_drain={'state':'unavailable'}
            return
        self.qmp_tail_drain={}
        self._read_qmp_backlog(self.qmp_tail_drain,'post_exit_drain')
    def _read_qmp_backlog(self,report,phase):
        """Read what the peer has already sent, within the tail bounds; no send."""
        started=time.monotonic();deadline=started+QMP_TAIL_SECONDS
        report.update(state='bounded_limit',bytes=0,messages=0,
                      max_seconds=QMP_TAIL_SECONDS,max_bytes=QMP_TAIL_BYTES,commands_sent=0)
        previous_timeout=self.qmp.gettimeout()
        try:
            while report['bytes']<QMP_TAIL_BYTES and report['messages']<128:
                remaining=deadline-time.monotonic()
                if remaining<=0:break
                self.qmp.settimeout(remaining)
                line=self.qf.readline(min(16384,QMP_TAIL_BYTES-report['bytes'])+1)
                if not line:
                    report['state']='eof';break
                report['bytes']+=len(line)
                if report['bytes']>QMP_TAIL_BYTES or not line.endswith(b'\n'):
                    report['state']='incomplete_or_oversized_frame';break
                response=json.loads(line)
                if not isinstance(response,dict):raise ValueError('QMP tail is not an object')
                report['messages']+=1
                if 'event' in response:self._record_qmp_event(response,phase)
        except (OSError,ValueError) as error:
            report.update(state='read_error',error=f'{type(error).__name__}: {error}'[:256])
        finally:
            self.qmp.settimeout(previous_timeout)
            report['elapsed_seconds']=round(time.monotonic()-started,6)
    def request(self,command,arguments=None):
        msg={'execute':command}
        if arguments is not None:msg['arguments']=arguments
        try:
            self.qmp.sendall(json.dumps(msg).encode()+b'\n')
        except (BrokenPipeError,ConnectionResetError):
            # QEMU closed QMP before this request. What it sent before closing
            # is still buffered and may hold the guest's SHUTDOWN event, which
            # close() needs to tell a guest power-off from a lost VM (Runs 133,
            # 135 and 137 were recorded as host teardowns without it). Read it
            # once; nothing is sent.
            if self.qmp_send_failure_drain is None and self.qf is not None:
                self.qmp_send_failure_drain={'command':command}
                self._read_qmp_backlog(self.qmp_send_failure_drain,'send_failure_drain')
                self._save_qmp_lifecycle()
            raise
        while True:
            line=self.qf.readline()
            if not line:raise EOFError('QMP closed')
            response=json.loads(line)
            if 'event' in response:
                self._record_qmp_event(response,'request')
                continue
            if 'error' in response:raise RuntimeError(response['error'])
            if 'return' in response:return response['return']
    def __enter__(self):
        if hashlib.sha256(ROM.read_bytes()).hexdigest()!=EXPECTED:raise RuntimeError('ROM pin mismatch')
        self._install_signal_handlers()
        try:
            self.temp=tempfile.TemporaryDirectory(prefix='kernel-usb-',dir=ROOT/'build')
            work=Path(self.temp.name)
            self.prepare_local_storage(work)
            system_disk_url=None
            if self.system_disk:
                descriptor=ROOT/'runs/e05-source-audit/aea-reader-server.json'
                self.system_descriptor=descriptor
                if descriptor.exists():
                    try:
                        old=json.loads(descriptor.read_text())
                        os.kill(int(old.get('pid',-1)),0)
                    except (ProcessLookupError,ValueError,TypeError,KeyError):
                        descriptor.unlink(missing_ok=True)
                    else:
                        raise RuntimeError('An existing AEA reader process is still running')
                self.reader_log=(self.logs/'systemos-aea-reader.log').open('wb')
                reader_env=dict(os.environ,AEA_RESEARCH_MEMBER='043-70867-640.dmg.aea')
                self.reader_proc=subprocess.Popen(
                    [sys.executable,'-u','<external-byof-helper-not-bundled>'],
                    stdout=self.reader_log,stderr=subprocess.STDOUT,
                    env=reader_env,start_new_session=True,
                    preexec_fn=self._child_parent_death_signal)
                deadline=time.monotonic()+180
                while not descriptor.exists():
                    if self.reader_proc.poll() is not None:
                        raise RuntimeError('AEA reader exited before readiness')
                    if time.monotonic()>deadline:
                        raise TimeoutError('AEA reader startup')
                    time.sleep(.1)
                system_disk_url=json.loads(descriptor.read_text())['url']
                self.system_socket=work/'systemos.nbd'
                self.nbd_log=(self.logs/'systemos-nbd.log').open('wb')
                self.nbd_proc=subprocess.Popen([
                    'qemu-nbd','--read-only','--persistent','--shared=2',
                    '--socket',str(self.system_socket),
                    '--image-opts',f'driver=http,url={system_disk_url}',
                ],stdout=self.nbd_log,stderr=subprocess.STDOUT,start_new_session=True,
                    preexec_fn=self._child_parent_death_signal)
                deadline=time.monotonic()+30
                while not self.system_socket.exists():
                    if self.nbd_proc.poll() is not None:
                        raise RuntimeError('qemu-nbd exited before readiness')
                    if time.monotonic()>deadline:
                        raise TimeoutError('qemu-nbd startup')
                    time.sleep(.1)
                self.root_overlay=work/'root-overlay.qcow2'
                backing=f'nbd+unix:///?socket={self.system_socket}'
                overlay=subprocess.run(
                    ['qemu-img','create','-q','-f','qcow2','-b',backing,'-B','raw',str(self.root_overlay)],
                    capture_output=True,text=True)
                if overlay.returncode:
                    raise RuntimeError(f'root overlay creation failed: {overlay.stderr.strip()}')
            usb=work/'u.sock';qmp=work/'q.sock'
            kernel_usb=work/'kernel-usb.sock'
            self.diagnostic_qmp=work/'inspect.sock'
            self.args=[str(ROOT/'build/qemu-bdif-usb/qemu-system-aarch64'),
                '-object',f'memory-backend-memfd,id=ram,size={self.ram_gib}G,share=on',
                '-M',('vmapple,memory-backend=ram,uuid=1' +
                      (',x-ctrr-model=on' if self.ctrr_model else '') +
                      (',x-pac-software-model=on' if self.pac_model else '')),
                '-m',f'{self.ram_gib}G','-smp',str(self.vcpus),'-cpu',self.cpu_model,
                '-accel',self.accel_argument,'-nodefaults',*self.network_arguments(),*self.sound_arguments(),
                '-display','none','-nographic','-monitor','none',
                '-serial',f'file:{self.logs/"firmware-uart.log"}', '-bios',str(ROM),
                '-chardev',f'socket,id=usbr,path={usb},server=on,wait=off',
                '-global','vmapple-bdif.usbdev=usbr',
                '-qmp',f'unix:{qmp},server=on,wait=off',
                '-qmp',f'unix:{self.diagnostic_qmp},server=on,wait=off','-S']
            self.args+=self.storage_arguments(work)
            if self.virtio_usb:
                self.args+=['-chardev',f'socket,id=kernelusbr,path={kernel_usb},server=on,wait=off',
                    '-device','vmapple-virtio-usb-pci,id=kernelusb,chardev=kernelusbr,addr=0x3']
            self.args+=self.input_arguments()
            if self.readonly_rsp:
                self.rsp_path=work/'current-process.sock'
                self.args+=['-gdb',f'unix:{self.rsp_path},server=on,wait=off']
            if self.trace_block_io or self.sound['pcm_trace']['requested']:
                trace_events=self.logs/'trace-events.txt'
                # Keep device activity without logging every protected code
                # fetch. The latter produced gigabytes of trace per boot and
                # substantially perturbed the TCG observation window.
                trace_events.write_text('\n'.join((
                    'bdif_vblk_*', 'bdif_usb_descriptor', 'vmapple_cfg_dma_*',
                    'vmapple_avp_rtc_*', 'vmapple_ctrr_enable',
                    'vmapple_ctrr_write_fault', 'vmapple_ctrr_reg_*',
                    'vmapple_ctrr_service_status', 'virtio_blk_handle_read',
                    'virtio_blk_handle_write', 'virtio_blk_req_complete', 'pl011_write',
                ) if self.trace_block_io else ())+'\n')
                if self.trace_block_io and self.virtio_usb:
                    with trace_events.open('a') as stream:stream.write('vmapple_vusb_*\n')
                if self.trace_block_io and self.native_window:
                    with trace_events.open('a') as stream:
                        stream.write('\n'.join(('apple_gfx_mode_change','apple_gfx_new_frame',
                            'apple_gfx_cursor_set','apple_gfx_read','apple_gfx_write',
                            'apple_gfx_raise_irq','apple_gfx_mmio_iosfc_read',
                            'apple_gfx_mmio_iosfc_write','apple_gfx_iosfc_raise_irq'))+'\n')
                if self.sound['pcm_trace']['requested']:
                    with trace_events.open('a') as stream:
                        stream.write('\n'.join(VIRTIO_SOUND_CONTROL_TRACE_EVENTS)+'\n')
                trace_log=self.logs/('bdif-block-io.log' if self.trace_block_io else 'virtio-sound-control.log')
                self.args+=['-trace',f'events={trace_events},file={trace_log}']
            self.err=(self.logs/'qemu-stderr.log').open('wb')
            qemu_env=self.qemu_environment()
            self.inspect_selected_gpu(qemu_env)
            self.proc=subprocess.Popen(self.args,stdout=subprocess.DEVNULL,stderr=self.err,
                env=qemu_env,
                start_new_session=True,preexec_fn=self._child_parent_death_signal)
            deadline=time.monotonic()+18
            while not(qmp.exists() and usb.exists() and (not self.virtio_usb or kernel_usb.exists())
                      and (not self.readonly_rsp or self.rsp_path.exists())):
                if self.proc.poll() is not None:raise RuntimeError(f'QEMU exited {self.proc.returncode}')
                if time.monotonic()>deadline:raise TimeoutError('QEMU endpoint startup')
                time.sleep(.025)
            self.usb=socket.socket(socket.AF_UNIX);self.usb.settimeout(8);self.usb.connect(str(usb))
            if self.virtio_usb:
                self.kernel_usb=socket.socket(socket.AF_UNIX)
                self.kernel_usb.settimeout(8);self.kernel_usb.connect(str(kernel_usb))
                self.kernel_avp=KernelVirtioUSBControl(self.kernel_usb,timeout=6)
            self.qmp=socket.socket(socket.AF_UNIX);self.qmp.settimeout(18);self.qmp.connect(str(qmp));self.qf=self.qmp.makefile('rb')
            self.greeting=json.loads(self.qf.readline());self.request('qmp_capabilities')
            self.capture_vcpu_threads()
            self.apply_vcpu_affinity()
            self.describe_storage(self.request('query-block'))
            (self.logs/'storage-profile.json').write_text(json.dumps(self.storage_info,indent=2)+'\n')
            self.request('cont')
            self.avp=RealFirmwareControl(self.usb,timeout=6)
            return self
        except BaseException:
            self.close();raise
    def wait_for_guest_shutdown_exit(self):
        """After a guest-origin SHUTDOWN, give the owned QEMU its bounded time to exit.

        True when the guest announced its own shutdown, no host quit has been
        requested and the process is gone. QMP can close before QEMU finishes
        exiting, so close() waits here before it sends another shutdown request
        or a host signal, and a controller whose request found QMP closed can
        ask the same question. The wait happens at most once.
        """
        event=self.last_shutdown_or_reset_event or {}
        data=event.get('data') or {}
        if not (self.proc and not self.host_quit_requested and
                event.get('event')=='SHUTDOWN' and data.get('guest') is True and
                data.get('reason')=='guest-shutdown'):
            return False
        if self.proc.poll() is None and self.guest_shutdown_wait['state']=='not_attempted':
            started=time.monotonic()
            self.guest_shutdown_wait={'state':'waiting','max_seconds':GUEST_SHUTDOWN_WAIT_SECONDS}
            try:
                self.proc.wait(timeout=GUEST_SHUTDOWN_WAIT_SECONDS)
                self.guest_shutdown_wait['state']='exited'
            except subprocess.TimeoutExpired:
                self.guest_shutdown_wait['state']='timed_out'
            finally:
                self.guest_shutdown_wait['elapsed_seconds']=round(time.monotonic()-started,6)
        return self.proc.poll() is not None
    def close(self):
        if self._closed or self._closing:
            return
        self._closing=True
        completed=False
        try:
            teardown='already_exited_or_not_started'
            self.wait_for_guest_shutdown_exit()
            if self.proc and self.proc.poll() is None:
                try:
                    self.host_quit_requested=True
                    self.request('quit');self.proc.wait(timeout=4)
                    teardown='qmp_quit'
                except Exception:
                    if self.proc.poll() is None:
                        self.proc.terminate()
                        teardown='terminate'
                        try:self.proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            self.proc.kill();self.proc.wait();teardown='kill'
            self._teardown=teardown
            self._drain_qmp_tail()
            self._save_qmp_lifecycle()
            for x in (self.qf,self.qmp,self.usb,self.kernel_usb,self.err):
                if x:
                    try:x.close()
                    except OSError:pass
            for proc in (self.nbd_proc,self.reader_proc):
                if proc and proc.poll() is None:
                    try:proc.terminate();proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
            if self.system_descriptor and self.reader_proc:
                try:
                    descriptor=json.loads(self.system_descriptor.read_text())
                    if int(descriptor.get('pid',-1))==self.reader_proc.pid:
                        self.system_descriptor.unlink(missing_ok=True)
                except (FileNotFoundError,ValueError,TypeError,KeyError,json.JSONDecodeError):
                    pass
            for stream in (self.nbd_log,self.reader_log):
                if stream:
                    try:stream.close()
                    except OSError:pass
            if self.temp:self.temp.cleanup()
            if self.retained_storage is not None:
                if self.retained_storage.get('qemu_reaped'):
                    teardown=self.retained_storage.get('teardown',teardown)
                self.retained_storage.update(
                    qemu_reaped=bool(self.proc and self.proc.poll() is not None),
                    qemu_pid=self.proc.pid if self.proc else None,
                    qemu_exit_code=self.proc.returncode if self.proc else None,
                    teardown=teardown,temporary_links_removed=True)
                self.retained_storage['qmp_lifecycle']=self.lifecycle_metadata()
                self.retained_storage['guest_shutdown_observed']=has_guest_shutdown_evidence(self.retained_storage)
                if self._signal_received is not None:
                    self.retained_storage['termination_signal']=self._signal_received
                for row in self.retained_storage['files'].values():
                    info=Path(row['path']).lstat()
                    row['retained_identity_matches']=(
                        stat.S_ISREG(info.st_mode) and info.st_dev==row['device'] and
                        info.st_ino==row['inode'] and info.st_size==row['bytes'])
                    row['allocated_bytes_at_close']=info.st_blocks*512
                self._save_retained_storage()
            completed=True
        finally:
            self._restore_signal_handlers()
            # A failed teardown must never release an inode still used by QEMU.
            if self.proc is None or self.proc.poll() is not None:
                self._release_storage_locks()
            if completed:
                self._closed=True
            self._closing=False
    def __exit__(self,*_):self.close()
