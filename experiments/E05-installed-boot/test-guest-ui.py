#!/usr/bin/env python3
"""Owned process/peer and receipt fixtures; no real VM is contacted."""
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

spec=importlib.util.spec_from_file_location('guest_ui',Path(__file__).with_name('guest_ui.py'))
ui=importlib.util.module_from_spec(spec);spec.loader.exec_module(ui)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.label='owned-fixture';self.pid=os.getpid()
        self.logs=self.root/'runs/e05-installed-boot'/self.label;self.logs.mkdir(parents=True)
        self.exe=self.root/'build/qemu-bdif-usb/qemu-system-aarch64';self.exe.parent.mkdir(parents=True);self.exe.write_bytes(b'fixture')
        self.endpoint=self.root/'build/kernel-usb-fixture/inspect.sock';self.endpoint.parent.mkdir(mode=0o700)
        self.args=[str(self.exe),'-serial',f'file:{self.logs}/firmware-uart.log','-qmp',f'unix:{self.endpoint},server=on,wait=off']
        self.result={'state':'observing','qemu_pid':self.pid,'execution':{'qemu_argv':self.args}}
        (self.logs/'results.json').write_text(json.dumps(self.result))
        self.proc=self.root/'proc'/str(self.pid);self.proc.mkdir(parents=True)
        (self.proc/'exe').symlink_to(self.exe)
        (self.proc/'cmdline').write_bytes(b'\0'.join(os.fsencode(x) for x in self.args)+b'\0')
        (self.proc/'status').write_text(f'Uid:\t{os.geteuid()}\t{os.geteuid()}\t{os.geteuid()}\t{os.geteuid()}\n')
        (self.proc/'stat').write_text(f'{self.pid} (fixture) '+' '.join(['S']+['0']*18+['12345']+['0']*10))
        patcher=patch.multiple(ui,ROOT=self.root,QEMU=self.exe,PROC_ROOT=self.root/'proc')
        patcher.start();self.addCleanup(patcher.stop)
        self.requests=[];self.accepted_inputs=[];self.server=None;self.thread=None
    def server_start(self, mode='normal'):
        self.server=socket.socket(socket.AF_UNIX);self.server.bind(str(self.endpoint));self.server.listen(1);self.server.settimeout(2)
        def serve():
            try:
                connection,_=self.server.accept()
                with connection:
                    connection.sendall(b'{"QMP":{}}\n')
                    stream=connection.makefile('rb')
                    while raw:=stream.readline():
                        request=json.loads(raw);self.requests.append(request)
                        command=request['execute']
                        if command=='screendump':
                            Path(request['arguments']['filename']).write_bytes(b'P6\n20 10\n255\n'+b'\0'*600)
                        if command=='input-send-event' and mode=='drop':break
                        reply={'return':{},'id':request['id']}
                        if (command=='input-send-event' and mode=='reject_down' and
                                request['arguments']['events'][0]['data'].get('down')):
                            reply={'error':{'class':'GenericError','desc':'SECRET RESPONSE CONTENT'},'id':request['id']}
                        if callable(mode):
                            answer=mode(request)
                            if answer is None:break
                            reply={**answer,'id':request['id']}
                        if command=='input-send-event' and 'return' in reply and 'error' not in reply:
                            self.accepted_inputs.append(request['arguments']['events'])
                        connection.sendall((json.dumps(reply)+'\n').encode())
                    stream.close()
            except (OSError,ValueError):pass
        self.thread=threading.Thread(target=serve,daemon=True);self.thread.start()
        self.addCleanup(self.server.close);self.addCleanup(lambda:self.thread.join(timeout=3))
    def journal(self):return [json.loads(line) for line in (self.logs/'ui-actions.jsonl').read_text().splitlines()]
    def test_stale_or_wrong_process_rejected_before_connect(self):
        for field in ('cmdline','exe','stat'):
            with self.subTest(field=field):
                target=self.proc/field
                if target.is_symlink():saved=os.readlink(target);target.unlink();target.symlink_to(self.root/'wrong-executable')
                else:saved=target.read_bytes();target.unlink()
                with patch.object(ui.socket,'socket') as connect,self.assertRaises(ui.UIError):ui.GuestUI(self.label)
                connect.assert_not_called()
                if field=='exe':target.unlink();target.symlink_to(saved)
                else:target.write_bytes(saved)
    def test_screenshot_and_click_receipts_and_paired_release(self):
        self.server_start()
        with ui.GuestUI(self.label) as client:
            frame=client.screenshot('before')
            self.assertEqual((frame['width'],frame['height']),(20,10))
            result=client.pointer(19,9,'before.png',click=True)
            self.assertTrue(result['release_confirmed'])
            with self.assertRaises(ui.UIError):client.pointer(20,9,'before.png',click=True)
            with self.assertRaises(ui.UIError):client.screenshot('before')
        events=[r['arguments']['events'] for r in self.requests if r['execute']=='input-send-event']
        self.assertEqual(events[0][0]['data']['value'],32767)
        self.assertEqual([row[0]['data']['down'] for row in events[1:]],[True,False])
        records=self.journal();before={r['request'] for r in records if r['phase']=='before_send'}
        after={r['request'] for r in records if r['phase']=='reply'}
        self.assertEqual(before,after)
    def test_broken_connection_preserves_before_send_and_unknown_receipt(self):
        self.server_start('drop')
        with ui.GuestUI(self.label) as client,self.assertRaises(ui.UIError):client.keys(['down'])
        records=self.journal()
        unknown=next(row for row in records if row['phase']=='reply_missing')
        self.assertTrue(any(row.get('request')==unknown['request'] and row['phase']=='before_send' for row in records))
        self.assertTrue(any(row['kind']=='release' and row['status']=='unknown' for row in records))
    def test_broken_pipe_during_send_has_already_persisted_receipt(self):
        self.server_start()
        with ui.GuestUI(self.label) as client:
            original=client.sock
            class Broken:
                def settimeout(self,value):original.settimeout(value)
                def sendall(self,raw):raise BrokenPipeError('fixture send failed')
                def close(self):original.close()
            client.sock=Broken()
            with self.assertRaises(ui.UIError):client.keys(['ret'])
        records=self.journal()
        failed=next(row for row in records if row['phase']=='reply_missing')
        self.assertTrue(any(row.get('request')==failed['request'] and row['phase']=='before_send' for row in records))
        self.assertFalse(any(r['execute']=='input-send-event' for r in self.requests))
    def test_qmp_peer_pid_must_match_selected_process(self):
        self.server_start()
        with patch.object(ui.struct,'unpack',return_value=(self.pid+1,os.geteuid(),os.getegid())),self.assertRaises(ui.UIError):
            with ui.GuestUI(self.label):pass
        self.assertEqual(self.requests,[])
    def test_complete_rejection_still_releases_and_does_not_log_response_payload(self):
        self.server_start('reject_down')
        with ui.GuestUI(self.label) as client,self.assertRaises(ui.UIError):client.keys(['down'])
        inputs=[r for r in self.requests if r['execute']=='input-send-event']
        self.assertEqual([r['arguments']['events'][0]['data']['down'] for r in inputs],[True,False])
        self.assertNotIn('SECRET RESPONSE CONTENT',(self.logs/'ui-actions.jsonl').read_text())
    def test_restricted_chord_releases_in_reverse_order(self):
        self.server_start()
        with ui.GuestUI(self.label) as client:
            client.keys(['meta_l','alt','esc'])
            with self.assertRaises(ui.UIError):client._request('stop')
        inputs=[r for r in self.requests if r['execute']=='input-send-event']
        self.assertEqual([e['data']['key']['data'] for e in inputs[-1]['arguments']['events']],['esc','alt','meta_l'])

    def pause_once(self,*,on_up=False,forever=False,query_drop=False):
        rejected=False;queries=0
        def answer(request):
            nonlocal rejected,queries
            command=request['execute']
            if command=='query-status':
                queries+=1
                if query_drop:return None
                return {'return':{'status':'paused' if forever or queries==1 else 'running',
                                  'running':not (forever or queries==1)}}
            if command=='input-send-event':
                down=request['arguments']['events'][0]['data'].get('down')
                if forever or (not rejected and down is not on_up):
                    rejected=True
                    return {'error':{'class':'GenericError','desc':'VM not running'}}
            return {'return':{}}
        return answer

    def test_rejected_down_retries_only_after_running_without_duplicate_acceptance(self):
        self.server_start(self.pause_once())
        with ui.GuestUI(self.label) as client:result=client.keys(['down'])
        self.assertTrue(result['release_confirmed'])
        inputs=[r for r in self.requests if r['execute']=='input-send-event']
        self.assertEqual(inputs[0]['arguments'],inputs[1]['arguments'])
        self.assertEqual([r[0]['data']['down'] for r in self.accepted_inputs],[True,False])
        self.assertEqual([r['execute'] for r in self.requests],
            ['qmp_capabilities','input-send-event','query-status','query-status','input-send-event','input-send-event'])

    def test_rejected_keyup_is_released_after_the_pause(self):
        self.server_start(self.pause_once(on_up=True))
        with ui.GuestUI(self.label) as client:result=client.keys(['meta_l','n'])
        self.assertTrue(result['release_confirmed'])
        self.assertEqual(len(self.accepted_inputs),2)
        self.assertEqual([r[0]['data']['down'] for r in self.accepted_inputs],[True,False])
        self.assertEqual([e['data']['key']['data'] for e in self.accepted_inputs[-1]],['n','meta_l'])

    def test_first_input_preparation_does_not_consume_pause_retry_budget(self):
        self.drain_server()
        clock=time.monotonic;offset=0
        with ui.GuestUI(self.label,keyboard_drain=True) as client,patch.object(ui.time,'sleep'), \
             patch.object(ui.time,'monotonic',side_effect=lambda:clock()+offset):
            write=client.journal.write;verify=client.verify_process;mark=client.drain.mark
            for stage in ('journal','binding','drain_mark'):
                with self.subTest(stage=stage):
                    delayed=False
                    def delay():
                        nonlocal offset,delayed
                        if not delayed:offset+=ui.INPUT_RETRY_SECONDS+1;delayed=True
                    def journal(**fields):
                        write(**fields)
                        if stage=='journal' and fields.get('command')=='input-send-event':delay()
                    def binding():
                        verify()
                        if stage=='binding':delay()
                    def drain_mark():
                        result=mark()
                        if stage=='drain_mark':delay()
                        return result
                    with patch.object(client.journal,'write',side_effect=journal), \
                         patch.object(client,'verify_process',side_effect=binding), \
                         patch.object(client.drain,'mark',side_effect=drain_mark):
                        result=client.type_ascii('a')
                    self.assertTrue(delayed)
                    self.assertTrue(result['release_confirmed'])
        self.assertEqual(self.accepted_inputs,[[ui.key_event('a',down)] for _ in range(3) for down in (True,False)])
        self.assertFalse(any(r['execute']=='query-status' for r in self.requests))
        self.assertFalse(any(r['phase']=='not_sent' for r in self.journal()))

    def test_retry_local_delay_is_not_reported_as_observed_pause(self):
        def answer(request):
            if request['execute']=='query-status':return {'return':{'running':True,'status':'running'}}
            if request['execute']=='input-send-event' and request['arguments']['events'][0]['data']['down']:
                return {'error':{'class':'GenericError','desc':'VM not running'}}
            return {'return':{}}
        self.server_start(answer)
        clock=time.monotonic;offset=0
        with ui.GuestUI(self.label) as client, \
             patch.object(ui.time,'monotonic',side_effect=lambda:clock()+offset):
            write=client.journal.write
            for stage in ('rejection_receipt','retry_preflight'):
                with self.subTest(stage=stage):
                    start=len(self.requests);downs=0
                    def journal(**fields):
                        nonlocal offset,downs
                        write(**fields)
                        if fields.get('command')=='input-send-event' and fields['arguments']['events'][0]['data']['down']:
                            downs+=1
                        if ((stage=='rejection_receipt' and fields.get('failure_class')=='vm_not_running') or
                                (stage=='retry_preflight' and fields.get('command')=='input-send-event' and downs==2 and
                                 fields['arguments']['events'][0]['data']['down'])):
                            offset+=ui.INPUT_RETRY_SECONDS+1
                    with patch.object(client.journal,'write',side_effect=journal),self.assertRaises(ui.UIError) as caught:
                        client.keys(['down'])
                    self.assertEqual(caught.exception.failure_class,'input_retry_timeout')
                    self.assertTrue(caught.exception.release_confirmed)
                    self.assertEqual(self.journal()[-1]['failure_class'],'input_retry_timeout')
                    expected=['input-send-event']+(['query-status'] if stage=='retry_preflight' else [])+['input-send-event']
                    self.assertEqual([r['execute'] for r in self.requests[start:]],expected)
        self.assertEqual(self.accepted_inputs,[[ui.key_event('down',False)]]*2)
        not_sent=[r for r in self.journal() if r['phase']=='not_sent']
        self.assertEqual(len(not_sent),1)
        self.assertEqual(not_sent[0]['failure_class'],'input_retry_timeout')

    def test_persistent_pause_times_out_without_cont_or_input_acceptance(self):
        self.server_start(self.pause_once(forever=True))
        started=time.monotonic()
        with patch.object(ui,'INPUT_RETRY_SECONDS',.04),patch.object(ui,'INPUT_POLL_SECONDS',.005), \
             ui.GuestUI(self.label) as client,self.assertRaises(ui.UIError) as caught:
            client.keys(['down'])
        self.assertLess(time.monotonic()-started,.5)
        self.assertEqual(caught.exception.failure_class,'vm_paused_timeout')
        self.assertEqual(self.journal()[-1]['failure_class'],'vm_paused_timeout')
        self.assertFalse(caught.exception.release_confirmed)
        self.assertEqual(self.accepted_inputs,[])
        self.assertEqual({r['execute'] for r in self.requests},{'qmp_capabilities','input-send-event','query-status'})

    def test_pause_deadline_inside_a_status_poll_is_still_a_paused_timeout(self):
        # The retry deadline can expire after a poll is journaled but before it is sent.
        self.server_start(self.pause_once(forever=True))
        clock=time.monotonic;offset=0;polls=0
        with patch.object(ui,'INPUT_RETRY_SECONDS',.2),patch.object(ui,'INPUT_POLL_SECONDS',.005), \
             ui.GuestUI(self.label) as client, \
             patch.object(ui.time,'monotonic',side_effect=lambda:clock()+offset):
            write=client.journal.write
            def journal(**fields):
                nonlocal offset,polls
                write(**fields)
                if fields.get('command')=='query-status' and fields.get('phase')=='before_send':
                    polls+=1
                    if polls==2:offset+=ui.INPUT_RETRY_SECONDS+1
            with patch.object(client.journal,'write',side_effect=journal),self.assertRaises(ui.UIError) as caught:
                client.keys(['down'])
        self.assertEqual(caught.exception.failure_class,'vm_paused_timeout')
        self.assertEqual(self.journal()[-1]['failure_class'],'vm_paused_timeout')
        not_sent=[r for r in self.journal() if r['phase']=='not_sent']
        self.assertTrue(not_sent)
        self.assertEqual(not_sent[0]['failure_class'],'vm_paused_timeout')
        self.assertEqual(self.accepted_inputs,[])
        self.assertEqual(len([r for r in self.requests if r['execute']=='input-send-event']),2)

    def test_near_match_error_is_not_retried(self):
        def answer(request):
            if request['execute']=='input-send-event' and request['arguments']['events'][0]['data']['down']:
                return {'error':{'class':'GenericError','desc':'VM not running '}}
            return {'return':{}}
        self.server_start(answer)
        with ui.GuestUI(self.label) as client,self.assertRaises(ui.UIError):client.keys(['down'])
        self.assertEqual([r['execute'] for r in self.requests],['qmp_capabilities','input-send-event','input-send-event'])
        self.assertEqual([r[0]['data']['down'] for r in self.accepted_inputs],[False])

    def test_unknown_status_query_response_never_replays_input(self):
        self.server_start(self.pause_once(query_drop=True))
        with ui.GuestUI(self.label) as client,self.assertRaises(ui.UIError) as caught:client.keys(['down'])
        self.assertEqual(caught.exception.failure_class,'transport_unknown')
        self.assertEqual([r['execute'] for r in self.requests],['qmp_capabilities','input-send-event','query-status'])
        self.assertEqual(self.accepted_inputs,[])

    def test_constant_release_known_keys_has_no_down_or_private_history(self):
        self.server_start()
        with patch.object(ui.time,'sleep'),ui.GuestUI(self.label) as client:result=client.release_known_keys()
        self.assertTrue(result['release_confirmed'])
        self.assertEqual(len(self.accepted_inputs),len(ui.KEYS))
        self.assertTrue(all(len(events)==1 and not events[0]['data']['down'] for events in self.accepted_inputs))
        keys=[events[0]['data']['key']['data'] for events in self.accepted_inputs]
        self.assertEqual(keys[:4],list(ui.MODIFIER_KEYS))
        self.assertEqual(set(keys),ui.KEYS)
        self.assertEqual(result['release_confirmation'],'qmp_request_accepted')

    def drain_server(self,outcome=None,*,initial=None,reject_enable=None):
        self.trace=self.logs/'bdif-block-io.log';self.trace.write_text('hid_kbd_queue_empty queue empty\n')
        self.args.extend(['-trace',f'events={self.logs}/trace-events.txt,file={self.trace}'])
        (self.logs/'results.json').write_text(json.dumps(self.result))
        (self.proc/'cmdline').write_bytes(b'\0'.join(os.fsencode(x) for x in self.args)+b'\0')
        self.trace_states=initial or {'hid_kbd_queue_full':False,'hid_kbd_queue_empty':True}
        self.initial_trace_states=dict(self.trace_states)
        sent=0
        def answer(request):
            nonlocal sent
            command=request['execute'];args=request['arguments']
            if command=='trace-event-get-state':
                name=args['name'];return {'return':[{'name':name,'state':'enabled' if self.trace_states[name] else 'disabled'}]}
            if command=='trace-event-set-state':
                if args['enable'] and args['name']==reject_enable:return {'error':{'class':'GenericError','desc':'fixture unavailable'}}
                self.trace_states[args['name']]=args['enable'];return {'return':{}}
            if command=='input-send-event':
                sent+=1
                value=outcome(sent,request) if outcome else 'hid_kbd_queue_empty queue empty\n'
                if value:
                    with self.trace.open('a') as stream:stream.write(value)
            return {'return':{}}
        self.server_start(answer)

    def test_keyboard_drain_observes_fresh_edges_and_restores_original_switches(self):
        self.drain_server()
        with patch.object(ui.time,'sleep'),ui.GuestUI(self.label,keyboard_drain=True) as client:
            result=client.type_ascii('A_2')
            self.assertTrue(result['keyboard_queue_drained'])
            self.assertEqual(result['release_confirmation'],'hid_queue_drained')
            self.assertEqual(result['gui_effect'],'unverified')
            for name in ('input_event_key_qcode','hid_kbd_queue_*'):
                with self.assertRaises(ui.UIError):client._request('trace-event-get-state',{'name':name})
        self.assertEqual(self.trace_states,self.initial_trace_states)
        drained=[x for x in self.journal() if x['kind']=='keyboard_drain' and x.get('status')=='drained']
        self.assertEqual(len(drained),6)
        self.assertTrue(all(x['end_offset']>x['start_offset'] for x in drained))
        self.assertTrue(all(b['start_offset']>=a['end_offset'] for a,b in zip(drained,drained[1:])))

    def test_keyboard_overflow_after_empty_is_not_hidden_or_retried(self):
        self.drain_server(lambda n,_:'hid_kbd_queue_empty queue empty\n'+('hid_kbd_queue_full queue full\n' if n==1 else ''))
        with ui.GuestUI(self.label,keyboard_drain=True) as client,self.assertRaises(ui.UIError) as caught:
            client.type_ascii('ab')
        self.assertEqual(caught.exception.failure_class,'keyboard_queue_full')
        # One accepted down plus one best-effort up; neither is replayed, and 'b' is not sent.
        self.assertEqual(self.accepted_inputs,[[ui.key_event('a',True)],[ui.key_event('a',False)]])
        self.assertEqual(self.trace_states,self.initial_trace_states)

    def test_keyboard_drain_timeout_ignores_old_empty_and_does_not_replay(self):
        self.drain_server(lambda n,_:'' if n==1 else 'hid_kbd_queue_empty queue empty\n')
        with patch.object(ui,'KEYBOARD_DRAIN_SECONDS',.03),patch.object(ui,'INPUT_POLL_SECONDS',.005), \
             ui.GuestUI(self.label,keyboard_drain=True) as client,self.assertRaises(ui.UIError) as caught:
            client.type_ascii('ab')
        self.assertEqual(caught.exception.failure_class,'keyboard_drain_timeout')
        self.assertEqual(self.accepted_inputs,[[ui.key_event('a',True)],[ui.key_event('a',False)]])
        self.assertEqual(self.trace_states,self.initial_trace_states)
        self.assertTrue(any(x.get('status')=='timeout' for x in self.journal()))

    def test_keyboard_trace_setup_failure_restores_already_changed_switch(self):
        self.drain_server(initial={name:False for name in ui.KEYBOARD_TRACE_EVENTS},reject_enable='hid_kbd_queue_empty')
        with self.assertRaises(ui.UIError):
            with ui.GuestUI(self.label,keyboard_drain=True):pass
        self.assertEqual(self.trace_states,self.initial_trace_states)
        self.assertEqual(self.accepted_inputs,[])

    def test_keyboard_drain_forbids_secret_before_read_or_input_and_cli_before_connect(self):
        path=self.secret_file();self.drain_server()
        with ui.GuestUI(self.label,keyboard_drain=True) as client,patch.object(ui,'private_value') as private:
            with self.assertRaises(ui.UIError):client.type_secret(path,'password')
            private.assert_not_called()
        self.assertEqual(self.accepted_inputs,[])
        self.assertEqual(self.trace_states,self.initial_trace_states)
        self.assertNotIn('S3cr!t-OnlyFixture',(self.logs/'ui-actions.jsonl').read_text())
        with patch.object(ui,'GuestUI') as client,contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            ui.main(['--run',self.label,'--keyboard-drain','type-secret','--file',str(path),'--key','password'])
        client.assert_not_called()

    def test_keyboard_trace_truncation_stops_before_next_input(self):
        self.drain_server()
        with ui.GuestUI(self.label,keyboard_drain=True) as client:
            client.keys(['a'])
            self.trace.write_text('')
            with self.assertRaises(ui.UIError) as caught:client.keys(['b'])
            self.assertEqual(caught.exception.failure_class,'keyboard_trace_unavailable')
        self.assertEqual(self.accepted_inputs,[[ui.key_event('a',True)],[ui.key_event('a',False)]])
        self.assertEqual(self.trace_states,self.initial_trace_states)

    def secret_file(self):
        path=self.root/'private/ui-fixture.json';path.parent.mkdir()
        path.write_text(json.dumps({'password':'S3cr!t-OnlyFixture'}));path.chmod(0o600)
        return path

    def test_secret_input_omits_contents_lengths_and_per_key_journal_entries(self):
        path=self.secret_file();before=path.read_bytes();self.server_start()
        output=io.StringIO()
        with contextlib.redirect_stdout(output),patch.object(ui.time,'sleep'),ui.GuestUI(self.label) as client:
            result=client.type_secret(path,'password')
        journal=(self.logs/'ui-actions.jsonl').read_text()
        self.assertNotIn('S3cr!t-OnlyFixture',journal+output.getvalue()+json.dumps(result))
        records=self.journal()
        self.assertEqual(len([r for r in records if r['kind']=='secret_text']),2)
        self.assertFalse(any(r.get('command')=='input-send-event' for r in records))
        self.assertFalse(any('length' in r or 'text' in r for r in records if r['kind']=='secret_text'))
        self.assertEqual(path.read_bytes(),before)
        self.assertTrue(result['release_confirmed'])

    def test_paced_secret_input_applies_hold_and_gap_and_validates_bounds(self):
        path=self.secret_file();self.server_start();sleeps=[]
        with patch.object(ui.time,'sleep',side_effect=sleeps.append),ui.GuestUI(self.label) as client:
            with patch.object(ui,'private_value') as private:
                with self.assertRaises(ui.UIError):client.type_secret(path,'password',hold=0.0)
                with self.assertRaises(ui.UIError):client.type_secret(path,'password',gap=-0.1)
                private.assert_not_called()
            result=client.type_secret(path,'password',hold=0.35,gap=0.25)
        self.assertTrue(result['release_confirmed'])
        self.assertEqual(sleeps[:4],[0.35,0.25,0.35,0.25])
        self.assertNotIn('S3cr!t-OnlyFixture',(self.logs/'ui-actions.jsonl').read_text())

    def test_secret_failure_is_redacted_and_keeps_unknown_effect_receipt(self):
        path=self.secret_file();self.server_start('drop')
        with ui.GuestUI(self.label) as client,self.assertRaises(ui.UIError) as caught:
            client.type_secret(path,'password')
        self.assertNotIn('S3cr!t-OnlyFixture',str(caught.exception))
        self.assertIsNone(caught.exception.__context__)
        records=self.journal()
        self.assertFalse(any(r.get('command')=='input-send-event' for r in records))
        self.assertEqual([r['status'] for r in records if r['kind']=='secret_text' and r['phase']=='after'],['unknown'])
        self.assertEqual(records[-1]['failure_class'],'transport_unknown')

    def test_secret_pause_recovery_has_only_aggregate_receipts(self):
        path=self.secret_file();self.server_start(self.pause_once(on_up=True))
        with patch.object(ui.time,'sleep'),ui.GuestUI(self.label) as client:result=client.type_secret(path,'password')
        self.assertTrue(result['release_confirmed'])
        self.assertTrue(any(r['execute']=='query-status' for r in self.requests))
        journal=(self.logs/'ui-actions.jsonl').read_text()
        self.assertNotIn('S3cr!t-OnlyFixture',journal+json.dumps(result))
        self.assertFalse(any(r.get('command') in ('input-send-event','query-status') for r in self.journal()))
        self.assertEqual(len([r for r in self.journal() if r['kind']=='secret_text']),2)

    def test_secret_other_rejection_records_safe_class_and_confirmed_release_only(self):
        path=self.secret_file();self.server_start('reject_down')
        with ui.GuestUI(self.label) as client,self.assertRaises(ui.UIError):client.type_secret(path,'password')
        journal=(self.logs/'ui-actions.jsonl').read_text()
        self.assertNotIn('SECRET RESPONSE CONTENT',journal)
        self.assertNotIn('S3cr!t-OnlyFixture',journal)
        self.assertEqual(self.journal()[-1]['failure_class'],'qmp_rejected')
        self.assertTrue(self.journal()[-1]['release_confirmed'])

    def test_private_mode_and_scope_rejected_without_input(self):
        path=self.secret_file();path.chmod(0o644);self.server_start()
        with ui.GuestUI(self.label) as client:
            with self.assertRaises(ui.UIError):client.type_secret(path,'password')
            with self.assertRaises(ui.UIError):client.type_secret(self.logs/'results.json','password')
        self.assertFalse(any(r['execute']=='input-send-event' for r in self.requests))
        self.assertTrue(all(r['status']=='not_sent' for r in self.journal() if r['kind']=='secret_text' and r['phase']=='after'))

    def test_ascii_mapping_covers_printable_range_and_releases_modifiers(self):
        for char in map(chr,range(32,127)):
            self.assertTrue(all(key in ui.KEYS for key in ui.character_keys(char)))
        self.server_start()
        with patch.object(ui.time,'sleep'),ui.GuestUI(self.label) as client:
            client.type_ascii('A_2')
        inputs=[r['arguments']['events'] for r in self.requests if r['execute']=='input-send-event']
        self.assertEqual(len(inputs),6)
        self.assertEqual([e['data']['key']['data'] for e in inputs[1]],['a','shift'])

    def native_child(self,args,**kwargs):
        png=Path(args[-1]);ui.Image.new('RGB',(20,10),(12,34,56)).save(png);png.chmod(0o600)
        return ui.subprocess.CompletedProcess(args,0,json.dumps({'qemu_pid':self.pid,
            'window_id':'0x123','width':20,'height':10,'full_desktop_captured':False}))

    def test_native_capture_publishes_bound_metadata_without_screendump_or_input(self):
        native=ui.load_native_capture();self.server_start()
        with patch.object(ui,'load_native_capture',return_value=native), \
             patch.object(native,'unique_window',return_value=0x123), \
             patch.object(ui.subprocess,'run',side_effect=self.native_child) as capture, \
             ui.GuestUI(self.label) as client:
            frame=client.native_screenshot('native')
            self.assertEqual(frame['start_ticks'],12345)
            self.assertFalse(frame['full_desktop_captured'])
            self.assertEqual(json.loads((self.logs/'native.json').read_text()),frame)
            with self.assertRaises(ui.UIError):client.native_screenshot('native')
        self.assertEqual(capture.call_count,1)
        self.assertEqual([r['execute'] for r in self.requests],['qmp_capabilities'])
        self.assertEqual(self.journal()[-1]['status'],'captured')

    def test_native_capture_rejects_process_change_after_image_before_metadata(self):
        native=ui.load_native_capture();self.server_start()
        def changed(args,**kwargs):
            result=self.native_child(args,**kwargs)
            stat=self.proc/'stat';stat.write_text(stat.read_text().replace('12345','12346'))
            return result
        with patch.object(ui,'load_native_capture',return_value=native), \
             patch.object(native,'unique_window',return_value=0x123), \
             patch.object(ui.subprocess,'run',side_effect=changed), \
             ui.GuestUI(self.label) as client,self.assertRaises(ui.UIError):
            client.native_screenshot('changed')
        self.assertFalse((self.logs/'changed.json').exists())
        self.assertEqual(self.journal()[-1]['status'],'unknown')

    def test_native_capture_rejects_window_change_after_image(self):
        native=ui.load_native_capture();self.server_start()
        with patch.object(ui,'load_native_capture',return_value=native), \
             patch.object(native,'unique_window',side_effect=[0x123,0x456]), \
             patch.object(ui.subprocess,'run',side_effect=self.native_child), \
             ui.GuestUI(self.label) as client,self.assertRaises(ui.UIError):
            client.native_screenshot('changed-window')
        self.assertFalse((self.logs/'changed-window.json').exists())

    def test_native_capture_rechecks_peer_before_reading_window(self):
        self.server_start()
        with ui.GuestUI(self.label) as client, \
             patch.object(ui.struct,'unpack',return_value=(self.pid+1,os.geteuid(),os.getegid())), \
             patch.object(ui.subprocess,'run') as capture,self.assertRaises(ui.UIError):
            client.native_screenshot('wrong-peer')
        capture.assert_not_called()

    def test_native_window_selection_requires_exactly_one_matching_pid(self):
        native=ui.load_native_capture()
        def property(*args):
            if args[0]=='-root':return '_NET_CLIENT_LIST(WINDOW): window id # 0x123, 0x456'
            return f'_NET_WM_PID(CARDINAL) = {self.pid if args[1]=="0x123" else self.pid+1}'
        with patch.object(native.x11,'_property',side_effect=property):
            self.assertEqual(native.unique_window(self.pid),0x123)
            with self.assertRaisesRegex(native.CaptureError,'no_owned_window'):native.unique_window(self.pid+2)
        with patch.object(native.x11,'_property',side_effect=lambda *args:
                '_NET_CLIENT_LIST(WINDOW): window id # 0x123, 0x456' if args[0]=='-root' else f'_NET_WM_PID(CARDINAL) = {self.pid}'):
            with self.assertRaisesRegex(native.CaptureError,'multiple_owned_windows'):native.unique_window(self.pid)

    def test_native_capture_never_reads_root_drawable(self):
        native=ui.load_native_capture();lib=MagicMock();lib.XOpenDisplay.return_value=1
        def geometry(display,window,root,*args):root._obj.value=window;return 1
        lib.XGetGeometry.side_effect=geometry
        with patch.object(native,'ROOT',self.root),patch.object(native,'unique_window',return_value=0x123), \
             patch.object(native.ctypes.util,'find_library',return_value='fixture'), \
             patch.object(native.C,'CDLL',return_value=lib), \
             self.assertRaisesRegex(native.CaptureError,'root_window_rejected'):
            native.capture(0x123,self.pid,self.logs/'root.png')
        lib.XGetImage.assert_not_called()


if __name__=='__main__':unittest.main(verbosity=2)
