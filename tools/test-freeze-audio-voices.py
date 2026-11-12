#!/usr/bin/env python3
"""Actual writer lifecycle test; no QEMU, guest or audio hardware is touched."""
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave

spec=importlib.util.spec_from_file_location('freeze_audio',Path(__file__).with_name('freeze-audio-voices.py'))
freeze=importlib.util.module_from_spec(spec);spec.loader.exec_module(freeze)


def make_wav(value,frames):
    stream=io.BytesIO()
    with wave.open(stream,'wb') as out:
        out.setparams((2,2,48000,0,'NONE','not compressed'))
        out.writeframes(struct.pack('<hh',value,-value)*frames)
    return stream.getvalue()


WRITER=r'''
import io,struct,sys,wave
for value,frames in ((100,2400),(300,1200)):
    if not sys.stdin.readline():break
    data=io.BytesIO()
    with wave.open(data,'wb') as out:
        out.setparams((2,2,48000,0,'NONE','not compressed'))
        out.writeframes(struct.pack('<hh',value,-value)*frames)
    f=open(sys.argv[1],'wb');f.write(data.getvalue());f.flush()
    print('OPEN',flush=True)
    sys.stdin.readline();f.close()
    print('CLOSED',flush=True)
sys.stdin.readline()
'''


class Tests(unittest.TestCase):
    def test_evidence_name_is_a_restricted_current_run_child(self):
        directory=Path('/fixture/run')
        self.assertEqual(freeze.evidence_directory(directory,'pcm-evidence-sampled'),
                         directory/'pcm-evidence-sampled')
        for name in ('','../other','/tmp/other','a/b','.','a.wav','..'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                freeze.evidence_directory(directory,name)

    def test_real_close_reopen_truncate_preserves_both_frozen_voices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'guest-audio.wav';source.write_bytes(make_wav(1,400))
            evidence=root/'pcm-evidence';receipt=root/'done.tgz'
            argv=[sys.executable,'-u','-c',WRITER,str(source)]
            writer=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            binding=freeze.ProcessBinding(writer.pid,argv,sys.executable)
            result=[]
            monitor=threading.Thread(target=lambda:result.append(freeze.monitor(
                binding,source,evidence,receipt,8,poll=.01,quiet=.05)),daemon=True)
            def wait_for(predicate):
                deadline=time.monotonic()+4
                while not predicate():
                    if time.monotonic()>=deadline:self.fail('Timed out waiting for real writer observation')
                    time.sleep(.01)
            def manifest():
                path=evidence/'manifest.json'
                return json.loads(path.read_text()) if path.exists() else {}
            def send():
                writer.stdin.write('next\n');writer.stdin.flush()
            try:
                monitor.start();wait_for(lambda:manifest().get('ready') is True)
                self.assertTrue(manifest()['baseline_exists'])
                self.assertEqual(manifest()['voices'],[])  # baseline is never copied as the tone
                send();self.assertEqual(writer.stdout.readline().strip(),'OPEN')
                wait_for(lambda:manifest().get('qemu_has_source_inode_open') is True)
                self.assertEqual(list(evidence.glob('voice-*.wav')),[])
                send();self.assertEqual(writer.stdout.readline().strip(),'CLOSED')
                wait_for(lambda:(evidence/'voice-1.wav').exists())
                first=(evidence/'voice-1.wav').read_bytes()
                self.assertEqual(first,make_wav(100,2400))
                send();self.assertEqual(writer.stdout.readline().strip(),'OPEN')
                self.assertEqual((evidence/'voice-1.wav').read_bytes(),first)
                self.assertEqual(source.stat().st_size,len(make_wav(300,1200)))
                send();self.assertEqual(writer.stdout.readline().strip(),'CLOSED')
                wait_for(lambda:(evidence/'voice-2.wav').exists())
                receipt.write_bytes(b'completed fixture receipt')
                monitor.join(timeout=4)
                self.assertFalse(monitor.is_alive())
                self.assertEqual(result[0]['state'],'complete_receipt_and_closed_voice')
                self.assertEqual(len(result[0]['voices']),2)
                self.assertEqual((evidence/'voice-1.wav').read_bytes(),first)
                self.assertEqual((evidence/'voice-2.wav').read_bytes(),make_wav(300,1200))
                self.assertFalse(result[0]['pcm_waveform_verified'])
                self.assertTrue(all(v['header']['valid'] for v in result[0]['voices']))
                self.assertNotEqual(result[0]['voices'][0]['sha256'],result[0]['voices'][1]['sha256'])
                self.assertEqual(list(evidence.glob('*.pending')),[])
                with self.assertRaises(FileExistsError):
                    freeze.freeze_closed(binding,source,evidence/'voice-1.wav')
                self.assertEqual((evidence/'voice-1.wav').read_bytes(),first)
            finally:
                if writer.poll() is None:
                    try:send()
                    except BrokenPipeError:pass
                try:writer.wait(timeout=2)
                except subprocess.TimeoutExpired:writer.kill();writer.wait()
                writer.stdin.close();writer.stdout.close();writer.stderr.close()
                monitor.join(timeout=2)

    def test_real_writer_creates_initially_absent_output_without_a_fake_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'guest-audio.wav'
            evidence=root/'pcm-evidence';receipt=root/'done.tgz'
            argv=[sys.executable,'-u','-c',WRITER,str(source)]
            writer=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            binding=freeze.ProcessBinding(writer.pid,argv,sys.executable)
            result=[]
            monitor=threading.Thread(target=lambda:result.append(freeze.monitor(
                binding,source,evidence,receipt,6,poll=.01,quiet=.05)),daemon=True)
            def wait_for(predicate):
                deadline=time.monotonic()+3
                while not predicate():
                    if time.monotonic()>=deadline:self.fail('Missing new-output observation')
                    time.sleep(.01)
            def manifest():
                path=evidence/'manifest.json'
                return json.loads(path.read_text()) if path.exists() else {}
            def send():
                writer.stdin.write('next\n');writer.stdin.flush()
            try:
                monitor.start();wait_for(lambda:manifest().get('state')=='pending_not_created')
                self.assertIsNone(manifest()['baseline'])
                self.assertFalse(manifest()['baseline_exists'])
                self.assertFalse(manifest()['source_exists'])
                self.assertFalse(source.exists())
                send();self.assertEqual(writer.stdout.readline().strip(),'OPEN')
                wait_for(lambda:manifest().get('qemu_has_source_inode_open') is True)
                self.assertEqual(list(evidence.glob('voice-*.wav')),[])
                send();self.assertEqual(writer.stdout.readline().strip(),'CLOSED')
                wait_for(lambda:(evidence/'voice-1.wav').exists())
                receipt.write_bytes(b'completed new-output fixture')
                monitor.join(timeout=3)
                self.assertFalse(monitor.is_alive())
                self.assertEqual(result[0]['state'],'complete_receipt_and_closed_voice')
                self.assertIsNone(result[0]['baseline'])
                self.assertFalse(result[0]['baseline_exists'])
                self.assertTrue(result[0]['source_exists'])
                self.assertEqual((evidence/'voice-1.wav').read_bytes(),make_wav(100,2400))
                self.assertEqual(len(result[0]['voices']),1)
            finally:
                writer.stdin.close()
                try:writer.wait(timeout=2)
                except subprocess.TimeoutExpired:writer.kill();writer.wait()
                writer.stdout.close();writer.stderr.close()
                monitor.join(timeout=2)

    def test_absence_does_not_allow_symlinks_or_nonregular_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'audio.wav'
            self.assertIsNone(freeze.source_stat(source))
            source.symlink_to(root/'missing')
            with self.assertRaises(ValueError):freeze.source_stat(source)
            source.unlink();source.mkdir()
            with self.assertRaises(ValueError):freeze.source_stat(source)

    def test_header_requires_final_lengths_and_fixed_format(self):
        raw=make_wav(1,100)
        self.assertTrue(freeze.wav_header(raw[:44],len(raw))['valid'])
        for offset,value in ((4,0),(24,44100),(40,0)):
            bad=bytearray(raw);struct.pack_into('<I',bad,offset,value)
            self.assertFalse(freeze.wav_header(bad[:44],len(bad))['valid'])
        self.assertFalse(freeze.wav_header(raw[:20],len(raw))['valid'])
        self.assertFalse(freeze.wav_header(raw[:44],len(raw)+4)['valid'])


if __name__=='__main__':unittest.main(verbosity=2)
