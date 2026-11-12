#!/usr/bin/env python3
"""Offline tests for gpu_init_timing.py. Every log line here is written for the test."""
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('gpu_init_timing', HERE / 'gpu_init_timing.py')
timing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timing)

REQUEST = 'GPUProcessProxy is taking a background assertion because a web process is requesting a connection'


def line(clock, process, pid, message, level='Df'):
    return f'2026-01-02 10:00:{clock} {level} {process}[{pid}:1a2b] {message}'


def assertion(clock, owner, owner_pid, gpu_pid):
    return line(clock, owner, owner_pid, "[com.apple.WebKit:ProcessSuspension] 0x1 - ProcessAssertion::acquireSync "
                f"Successfully took RBS assertion 'GPUProcess Foreground Assertion' for process with PID={gpu_pid}")


def launch(gpu_pid, constructed, launched, owner='Safari', owner_pid=500):
    return [
        line(constructed, 'com.apple.WebKit.GPU', gpu_pid, '(WebKit) 0x2 - GPUProcess::GPUProcess:'),
        line(launched, owner, owner_pid, '[com.apple.WebKit:Process] 0x3 - GPUProcessProxy::didFinishLaunching:'),
        assertion(launched, owner, owner_pid, gpu_pid),
    ]


def successful_connection(gpu_pid=700):
    return launch(gpu_pid, '01.000', '02.000') + [
        # The GPU-side entry is logged first although it happened 29 ms after the request.
        line('10.029', 'com.apple.WebKit.GPU', gpu_pid,
             '[com.apple.WebKit:Process] 0x2 - GPUProcess::createGPUConnectionToWebProcess: processIdentifier=2'),
        line('10.000', 'Safari', 500, f'[com.apple.WebKit:ProcessSuspension] 0x3 - {REQUEST}'),
        line('11.500', 'com.apple.WebKit.GPU', gpu_pid, '[com.apple.xpc:connection] [0x4] activating connection: '
             'mach=true listener=false peer=false name=com.apple.audio.AudioComponentRegistrar'),
        line('11.700', 'com.apple.WebKit.GPU', gpu_pid,
             '[com.apple.coreaudio:audiocomp]   SandboxHelperClient.cpp:45    Creating SandboxHelper connection', 'I '),
        line('12.700', 'com.apple.WebKit.GPU', gpu_pid,
             '[com.apple.coreaudio:audiocomp]   SandboxHelperClient.cpp:54    Destroying SandboxHelper connection', 'I '),
        line('13.250', 'Safari', 500, '[com.apple.WebKit:Process] WebContent[600]: GPUProcessConnection::didInitialize(18)'),
        'a line without a timestamp',
    ]


def unresponsive_exit(clock, owner='Safari', owner_pid=500):
    return line(clock, owner, owner_pid,
                '[com.apple.WebKit:Process] 0x3 - GPUProcessProxy::gpuProcessExited: reason=Unresponsive', 'E ')


class GpuInitTiming(unittest.TestCase):
    def test_successful_first_connection_is_timed_by_timestamp_not_line_order(self):
        report = timing.analyse('\n'.join(successful_connection()))
        self.assertEqual(report['outcome'], 'initialized')
        self.assertEqual(report['first_gpu']['gpu_pid'], 700)
        self.assertEqual(report['first_gpu']['owner'], {'process': 'Safari', 'pid': 500})
        self.assertEqual(report['first_gpu']['seconds'], {
            'constructor_to_proxy_launched': 1.0,
            'ui_request_to_did_initialize': 3.25, 'gpu_entry_to_did_initialize': 3.221,
            'gpu_entry_to_registrar_activation': 1.471, 'registrar_activation_to_helper_create': 0.2,
            'helper_create_to_helper_destroy': 1.0, 'helper_destroy_to_did_initialize': 0.55,
            'ui_request_to_unresponsive_exit': None, 'gpu_entry_to_unresponsive_exit': None})
        self.assertEqual(report['first_gpu']['events']['ui_request']['line'], 5)
        self.assertEqual((report['lines'], report['timestamped_lines']), (10, 9))
        self.assertEqual(report['gpu_unresponsive_exits_reported_by_ui_process'], 0)

    def test_unresponsive_gpu_process_reports_time_to_exit_and_no_initialisation(self):
        lines = launch(700, '01.000', '02.000') + [
            line('10.000', 'Safari', 500, f'[com.apple.WebKit:ProcessSuspension] 0x3 - {REQUEST}'),
            line('10.010', 'com.apple.WebKit.GPU', 700,
                 '[com.apple.WebKit:Process] 0x2 - GPUProcess::createGPUConnectionToWebProcess: processIdentifier=2'),
            # The replacement process exists before the exit of the first one is reported.
            *launch(701, '13.000', '13.400'),
            unresponsive_exit('13.005'),
            line('13.006', 'Safari', 500, '[com.apple.WebKit:Process] 0x5 - [PID=600] WebProcessProxy::didBecomeUnresponsive:', 'E '),
            # A later initialisation belongs to the replacement and must not rescue the first process.
            line('20.000', 'Safari', 500, '[com.apple.WebKit:Process] WebContent[601]: GPUProcessConnection::didInitialize(19)'),
        ]
        report = timing.analyse('\n'.join(lines))
        self.assertEqual(report['outcome'], 'terminated_unresponsive')
        seconds = report['first_gpu']['seconds']
        self.assertEqual((seconds['ui_request_to_unresponsive_exit'], seconds['gpu_entry_to_unresponsive_exit']), (3.005, 2.995))
        self.assertIsNone(seconds['ui_request_to_did_initialize'])
        self.assertIsNone(report['first_gpu']['events']['did_initialize'])
        self.assertEqual(report['gpu_processes_constructed'], 2)
        self.assertEqual(report['gpu_unresponsive_exits_reported_by_ui_process'], 1)
        self.assertEqual(report['web_process_unresponsive_notifications'], 1)

    def test_gpu_process_of_another_client_is_not_taken_for_safari(self):
        other = launch(650, '00.100', '00.500', owner='nsattributedstringagent', owner_pid=450)
        other.append(unresponsive_exit('00.900', 'nsattributedstringagent', 450))
        report = timing.analyse('\n'.join(other + successful_connection(700)))
        self.assertEqual(report['first_gpu']['gpu_pid'], 700)
        self.assertEqual(report['outcome'], 'initialized')
        self.assertEqual(report['gpu_unresponsive_exits_reported_by_ui_process'], 0)
        self.assertEqual(report['gpu_unresponsive_exits_all_clients'], 1)
        self.assertEqual(timing.analyse('\n'.join(other))['outcome'], 'no_gpu_process_owned_by_ui_process')

    def test_gpu_process_started_by_another_client_in_between_does_not_cut_the_events(self):
        lines = successful_connection()
        # Another client's GPU process appears after Safari's was launched and before Safari's request.
        lines[3:3] = launch(800, '05.000', '06.000', owner='nsattributedstringagent', owner_pid=900)
        report = timing.analyse('\n'.join(lines))
        self.assertEqual(report['gpu_processes_constructed'], 2)
        self.assertEqual(report['outcome'], 'initialized')
        self.assertEqual(report['first_gpu']['gpu_pid'], 700)
        self.assertEqual(report['first_gpu']['seconds']['ui_request_to_did_initialize'], 3.25)
        self.assertEqual(report['first_gpu']['seconds']['helper_create_to_helper_destroy'], 1.0)

    def test_launch_without_a_connection_request(self):
        report = timing.analyse('\n'.join(launch(700, '01.000', '02.000')))
        self.assertEqual(report['outcome'], 'no_connection_requested')
        self.assertIsNone(report['first_gpu']['seconds']['ui_request_to_did_initialize'])

    def test_archive_input_output_file_and_refusals(self):
        text = '\n'.join(successful_connection()).encode()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            archive = directory / 'trial.tgz'
            with tarfile.open(archive, 'w:gz') as tar:
                info = tarfile.TarInfo('trial/webkit-stream.log')
                info.size = len(text)
                tar.addfile(info, io.BytesIO(text))
            out = directory / 'report.json'
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(timing.main([str(archive), '--out', str(out)]), 0)
            self.assertEqual(json.loads(stdout.getvalue()), json.loads(out.read_text()))
            self.assertEqual(json.loads(out.read_text())['archive_member'], 'trial/webkit-stream.log')
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(timing.main([str(archive), '--out', str(out)]), 2)
            self.assertIn('GPU_INIT_TIMING', stderr.getvalue())
            empty = directory / 'empty.tgz'
            with tarfile.open(empty, 'w:gz'):
                pass
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(timing.main([str(empty)]), 2)
                self.assertEqual(timing.main([str(directory / 'missing.tgz')]), 2)


if __name__ == '__main__':
    unittest.main()
