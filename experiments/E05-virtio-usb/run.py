#!/usr/bin/env python3
"""真实自制ARM guest验证普通VirtIO USB透明字节通道；不运行Apple材料。"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUNTIME = ROOT / "experiments/E06-platform-contracts"
BUILD = ROOT / "build/e05-virtio-usb"
LARGE = 512 * 1024
SUCCESS = "E05 VIRTIO USB REAL DMA SG IRQ REARM BACKPRESSURE PASS"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def compile_guest(case="positive"):
    BUILD.mkdir(parents=True, exist_ok=True)
    cc = ["clang", "--target=aarch64-none-elf", "-O2", "-ffreestanding", "-fno-builtin",
          "-fno-stack-protector", "-fno-pic", "-fno-pie", "-mgeneral-regs-only", "-Wall", "-Wextra",
          "-I", str(RUNTIME)]
    objects = []
    negative = case == "q0-small"
    defines = ["-DSMALL_Q0=1"] if negative else []
    prefix = "q0-small-" if negative else ""
    for source in (RUNTIME / "start.S", RUNTIME / "runtime.c", HERE / "fixture.c"):
        obj = BUILD / (prefix + source.name.replace(".", "_") + ".o")
        subprocess.run([*cc, *defines, "-c", str(source), "-o", str(obj)], check=True)
        objects.append(str(obj))
    elf, image = BUILD / (prefix + "fixture.elf"), BUILD / (prefix + "fixture.bin")
    subprocess.run(["ld.lld", "--build-id=none", "-T", str(RUNTIME / "link.ld"), *objects, "-o", str(elf)], check=True)
    subprocess.run(["llvm-objcopy", "-O", "binary", str(elf), str(image)], check=True)
    return image


def request_payload(phase):
    return bytes(((i * 13 + phase * 37) ^ 0xa6) & 255 for i in range(48 if phase == 1 else 17))


def response_payload(phase):
    request = request_payload(phase)
    return bytes((i * 17 + 0x5a + request[i % len(request)]) & 255 for i in range(64 if phase == 1 else 113))


def recv_exact(peer, length):
    data = bytearray()
    while len(data) < length:
        chunk = peer.recv(min(65536, length - len(data)))
        if not chunk:
            raise RuntimeError(f"fixture chardev EOF after {len(data)}/{length} bytes")
        data.extend(chunk)
    return bytes(data)


def recv_frame(peer, destination, expected):
    prefix = recv_exact(peer, 4)
    length = struct.unpack("<I", prefix)[0]
    if not 2 <= length <= 1024 * 1024:
        raise RuntimeError(f"response outer length out of fixture contract: {length}")
    body = recv_exact(peer, length)
    destination.write_bytes(prefix + body)  # actual wire, never an expected image
    if body != expected:
        raise RuntimeError("actual response differs from independent fixture byte oracle")
    return dict(wire=str(destination.relative_to(ROOT)), body_bytes=length,
                body_sha256=hashlib.sha256(body).hexdigest(), exact_bytes_matched=True)


def wait_marker(proc, out, marker, deadline):
    while marker not in out.read_text():
        if proc.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("guest did not reach " + marker)
        time.sleep(0.005)


def connect_owned(path, proc, deadline):
    peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    peer.settimeout(5)
    while True:
        try:
            peer.connect(str(path)); return peer
        except (FileNotFoundError, ConnectionRefusedError):
            if proc.poll() is not None or time.monotonic() > deadline:
                peer.close(); raise RuntimeError("owned fixture socket unavailable: " + str(path))
            time.sleep(0.005)


def production_observer():
    path = ROOT / "experiments/E05-usbip-kernel/virtio_observation.py"
    spec = importlib.util.spec_from_file_location("vusb_fixture_production_observation", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def observe_qmp(qmp, proc, production, *, phase, ready):
    before = qmp.request("query-status")
    devices = production.capture_virtio_devices(qmp, allow_usb=True)
    after = qmp.request("query-status")
    usb = [device for device in devices if device["kind"] == "usb"]
    if len(usb) != 1:
        raise RuntimeError("production QMP capture did not return exactly one USB device")
    usb = usb[0]; status, queues = usb["status"], usb["queues"]
    objects = ("guest-features", "host-features", "backend-features")
    feature_objects = all(isinstance(status.get(name), dict) and
                          isinstance(status[name].get("transports"), list) for name in objects)
    driver_ok = any("DRIVER_OK:" in value for value in status["status"]["statuses"])
    valid = (status["device-id"] == 0x100 and feature_objects and status["num-vqs"] == 2 and
             len(queues) == 2 and [q["queue-index"] for q in queues] == [0, 1] and
             all(q["vring-num"] == 256 for q in queues) and not status["broken"] and
             proc.poll() is None and before["running"] == ready and after["running"] == ready)
    if ready:
        valid &= (status["started"] and driver_ok and all(q["vring-desc"] != 0 for q in queues) and
                  any("VIRTIO_F_VERSION_1" in v for v in status["guest-features"]["transports"]))
    else:
        valid &= (not status["started"] and not driver_ok and
                  all(q["vring-desc"] == 0 and q["used-idx"] == 0 for q in queues))
    snapshot = dict(phase=phase, passed=bool(valid),
                    capture_api="capture_virtio_devices(..., allow_usb=True)",
                    qmp_status_before=before, qmp_status_after=after,
                    qemu_alive_after_queries=proc.poll() is None,
                    captured_device_ids=[device["status"]["device-id"] for device in devices], usb=usb)
    if not valid:
        raise RuntimeError("QMP USB status/features/queues invalid in " + phase)
    return snapshot


def run_fixture(binary, image, run, production, *, negative=False):
    actions, responses, qmp_transcript = [], [], []
    error = None; timeout = False; qmp_quit_ack = False; backpressure = {}; diagnostics = []
    with tempfile.TemporaryDirectory(prefix="wire-", dir=BUILD) as temp:
        temp = Path(temp); wire_socket, qmp_socket = temp / "wire.sock", temp / "qmp.sock"
        for name, size in (("aux.raw", 4 << 20), ("root.raw", 32 << 20)):
            with (temp / name).open("wb") as stream: stream.truncate(size)
        cmd = [str(binary), "-object", "memory-backend-memfd,id=ram,size=4G,share=on",
               "-machine", "vmapple,memory-backend=ram,uuid=1,x-ctrr-model=on,x-pac-software-model=on",
               "-cpu", "max,pauth-qarma5=on,cntfrq=24000000,x-cntfrq-exact=on", "-accel", "tcg",
               "-m", "4G", "-smp", "2", "-nodefaults", "-nic", "none", "-display", "none",
               "-nographic", "-monitor", "none", "-serial", "stdio", "-S",
               "-semihosting-config", "enable=on,target=native", "-bios", str(image),
               "-drive", f"file={temp / 'aux.raw'},format=raw,if=pflash,index=0",
               "-drive", f"file={temp / 'root.raw'},format=raw,if=pflash,index=1",
               "-qmp", f"unix:{qmp_socket},server=on,wait=off",
               "-chardev", f"socket,id=kernelusbr,path={wire_socket},server=on,wait=off",
               "-device", "vmapple-virtio-usb-pci,id=fixture-usb,addr=3,chardev=kernelusbr"]
        out_path, err_path = run / "uart.log", run / "qemu-stderr.log"
        with out_path.open("w") as out, err_path.open("w") as err:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                    env=dict(os.environ, REIMS_VGPU_WINDOW="0", REIMS_VGPU_LOG="warn"))
            deadline = time.monotonic() + 45; qmp = None; peer = None; stream = None
            try:
                qmp = connect_owned(qmp_socket, proc, deadline); stream = qmp.makefile("r", encoding="utf-8")
                qmp_transcript.append(json.loads(stream.readline()))
                def command(name, arguments=None):
                    req = dict(execute=name, id=len(qmp_transcript))
                    if arguments is not None: req["arguments"] = arguments
                    qmp_transcript.append(dict(request=req)); qmp.sendall((json.dumps(req) + "\n").encode())
                    while True:
                        line = stream.readline()
                        if not line: raise RuntimeError("owned fixture QMP EOF")
                        reply = json.loads(line); qmp_transcript.append(reply)
                        if reply.get("id") == req["id"]:
                            if "error" in reply: raise RuntimeError(str(reply["error"]))
                            return reply.get("return")
                class OwnedQMP:
                    def request(self, name, arguments=None): return command(name, arguments)
                command("qmp_capabilities"); command("query-pci")
                diagnostics.append(observe_qmp(OwnedQMP(), proc, production, phase="reset_stopped", ready=False))
                peer = connect_owned(wire_socket, proc, deadline)
                peer.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                command("cont")
                for phase in ((1,) if negative else (1, 2)):
                    wait_marker(proc, out_path, f"VUSB REQUEST{phase} READY", deadline)
                    if phase == 1:
                        diagnostics.append(observe_qmp(OwnedQMP(), proc, production, phase="guest_driver_ok", ready=True))
                    payload = request_payload(phase); body = struct.pack("<IBB", len(payload), 4, 1) + payload
                    frame = struct.pack("<I", len(body)) + body
                    source = run / f"request{phase}.wire"; source.write_bytes(frame)
                    chunks = [frame[:1], frame[1:2], frame[2:4], frame[4:11], frame[11:]] if phase == 1 else [frame]
                    for chunk in chunks:
                        peer.sendall(chunk)
                        if phase == 1: time.sleep(0.01)
                    actions.append(dict(phase=phase, request=str(source.relative_to(ROOT)),
                                        body_bytes=len(body), write_chunk_bytes=[len(c) for c in chunks]))
                    if not negative:
                        responses.append(recv_frame(peer, run / f"response{phase}.wire", b"\x01\x81" + response_payload(phase)))
                if not negative:
                    wait_marker(proc, out_path, "VUSB LARGE QUEUED", deadline)
                    no_read_start = time.monotonic()
                    wait_marker(proc, out_path, "VUSB BACKPRESSURE HELD WITHOUT USED OR ISR", min(deadline, no_read_start + 3))
                    while time.monotonic() - no_read_start < 2:
                        if proc.poll() is not None: raise RuntimeError("guest ended during response backpressure")
                        if "VUSB LARGE COMPLETED" in out_path.read_text(): raise RuntimeError("guest completed large response before host read")
                        time.sleep(0.01)
                    before_read = out_path.read_text()
                    backpressure = dict(host_read_delayed_seconds=time.monotonic() - no_read_start,
                                        receiver_so_rcvbuf=peer.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF),
                                        guest_held_without_used_or_isr="VUSB BACKPRESSURE HELD WITHOUT USED OR ISR" in before_read,
                                        guest_completion_before_first_read="VUSB LARGE COMPLETED" in before_read,
                                        response_payload_bytes=LARGE)
                    expected = b"\x01\x81" + bytes(((i * 29 + (i >> 8) + 0x73) ^ 0xa6) & 255 for i in range(LARGE))
                    responses.append(recv_frame(peer, run / "response-large.wire", expected))
                proc.wait(timeout=max(0.01, deadline - time.monotonic()))
            except (subprocess.TimeoutExpired, TimeoutError) as exc:
                timeout = True; error = str(exc)
            except Exception as exc:
                error = str(exc)
            finally:
                if proc.poll() is None and qmp is not None and stream is not None:
                    try: command("quit"); qmp_quit_ack = True
                    except Exception: pass
                    try: proc.wait(timeout=3)
                    except subprocess.TimeoutExpired: proc.kill(); proc.wait()
                elif proc.poll() is None: proc.kill(); proc.wait()
                if peer is not None: peer.close()
                if stream is not None: stream.close()
                if qmp is not None: qmp.close()
        stdout, stderr = out_path.read_text(), err_path.read_text()
        (run / "qmp.json").write_text(json.dumps(qmp_transcript, indent=2) + "\n")
        captures = [dict(zip(("queue", "used_index", "used_length", "intid", "bitmap", "irq_count"),
                            (int(x, 16) for x in row))) for row in re.findall(
            r"VUSB IRQ QUEUE=(0x[0-9a-f]+) USED=(0x[0-9a-f]+) LENGTH=(0x[0-9a-f]+) INTID=(0x[0-9a-f]+) BITS=(0x[0-9a-f]+) COUNT=(0x[0-9a-f]+)", stdout)]
        expected_used = [(0, 1, 54), (1, 1, 0), (0, 2, 23), (1, 2, 0), (1, 3, 0)]
        passed = (error is None and not timeout and proc.returncode == 0 and SUCCESS in stdout and
                  "FAIL:" not in stdout and len(responses) == 3 and
                  [(r["queue"], r["used_index"], r["used_length"]) for r in captures] == expected_used and
                  all(r["intid"] == 67 and r["bitmap"] & 1 for r in captures) and
                  backpressure.get("host_read_delayed_seconds", 0) >= 2 and
                  backpressure.get("guest_held_without_used_or_isr") and
                  not backpressure.get("guest_completion_before_first_read", True))
        negative_error = None
        if negative:
            def scalar(name):
                match = re.search(r"^" + name + r"=(0x[0-9a-f]+)$", stdout, re.M)
                return int(match.group(1), 16) if match else None
            negative_error = dict(device_status=scalar("VUSB_NEGATIVE_DEVICE_STATUS"),
                                  q0_used_index=scalar("VUSB_NEGATIVE_USED_INDEX"),
                                  config_intid=scalar("VUSB_NEGATIVE_CONFIG_INTID"),
                                  config_bitmap=scalar("VUSB_NEGATIVE_CONFIG_BITMAP"),
                                  dma_and_guards_unchanged="VUSB NEGATIVE DMA TARGET GUARDS UNCHANGED PASS" in stdout,
                                  source_error="Q0 requires a writable chain large enough for the body" in stderr)
            passed = (error is None and not timeout and proc.returncode == 1 and
                      "E05 VIRTIO USB SMALL Q0 NEEDS RESET EXPECTED" in stdout and
                      SUCCESS not in stdout and "FAIL:" not in stdout and not captures and not responses and
                      negative_error["device_status"] is not None and negative_error["device_status"] & 64 and
                      negative_error["q0_used_index"] == 0 and negative_error["config_intid"] == 67 and
                      negative_error["config_bitmap"] is not None and negative_error["config_bitmap"] & 2 and
                      negative_error["dma_and_guards_unchanged"] and negative_error["source_error"])
    # Check after TemporaryDirectory exits, including all sockets and stub disks.
    removed = not temp.exists()
    diagnostic_passed = len(diagnostics) == 2 and all(s["passed"] for s in diagnostics)
    return dict(passed=bool(passed and removed and diagnostic_passed), error=error, host_timeout=timeout, guest_exit=proc.returncode,
                owned_process_reaped=proc.poll() is not None, qmp_quit_ack_on_recovery=qmp_quit_ack,
                temporary_storage_removed=removed, command=cmd,
                host_actions=actions, actual_responses=responses, guest_irq_captures=captures,
                backpressure=backpressure, expected_guest_exit=1 if negative else 0, negative_device_error=negative_error,
                qmp_diagnostics=dict(passed=diagnostic_passed, snapshots=diagnostics,
                                     commands=["x-query-virtio", "x-query-virtio-status", "x-query-virtio-queue-status"],
                                     guest_mmio_or_isr_read=False),
                uart=str(out_path.relative_to(ROOT)), stderr=str(err_path.relative_to(ROOT)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("positive", "q0-small"), default="positive")
    parser.add_argument("--build-only", action="store_true", help="仅编译自制guest，不运行QEMU")
    parser.add_argument("--binary", type=Path, default=ROOT / "build/qemu-bdif-usb/qemu-system-aarch64")
    parser.add_argument("--expect-binary-sha256", help="可选的main已确认新binary摘要")
    args = parser.parse_args(); image = compile_guest(args.case)
    if args.build_only:
        print(json.dumps(dict(firmware=str(image.relative_to(ROOT)), sha256=digest(image), qemu_started=False), indent=2)); return 0
    binary = args.binary.resolve(); binary_hash = digest(binary)
    if args.expect_binary_sha256 and args.expect_binary_sha256 != binary_hash:
        raise SystemExit("binary与指定摘要不符；未运行VM")
    record = json.loads((ROOT / "runs/qemu-bdif-usb-build.json").read_text())
    if str(binary.relative_to(ROOT)) != record["binary"] or binary_hash != record["binary_sha256"]:
        raise SystemExit("binary与当前固定build record不符；未运行VM")
    production = production_observer()
    qmp_build = production.require_fixed_qmp_build(binary, require_usb=True)
    models = subprocess.run([str(binary), "-device", "help"], capture_output=True, text=True, check=True).stdout
    if 'name "vmapple-virtio-usb-pci"' not in models:
        raise SystemExit("当前binary没有新opt-in device；未运行VM")
    run = ROOT / "runs/e05-virtio-usb" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run.mkdir(parents=True, exist_ok=False); print("run:", run.relative_to(ROOT), flush=True)
    result = run_fixture(binary, image, run, production, negative=args.case == "q0-small")
    report = dict(time_utc=datetime.now(timezone.utc).isoformat(),
                  scope="自制ARM EL1 guest的透明VirtIO USB DMA/SG/队列完成/真实INTx及背压；不是Apple USB服务或restore成功",
                  case=args.case,
                  apple_material_used=False, binary=str(binary.relative_to(ROOT)), binary_sha256=binary_hash,
                  build_record="runs/qemu-bdif-usb-build.json", firmware=str(image.relative_to(ROOT)),
                  production_qmp_build_guard=qmp_build,
                  firmware_sha256=digest(image), requested_features=["VIRTIO_F_VERSION_1"],
                  pci=dict(address="00:03.0", vendor="0x106b", device="0x1a01", intx_intid=67, trigger="level"),
                  queues=dict(count=2, split_ring_size=256, q0="device-writable SG capacity14" if args.case == "q0-small" else "device-writable request header6 + payload SG",
                              q1="device-readable response header2 + payload SG; used.len0"),
                  fixture=dict(request_payload_bytes=[48] if args.case == "q0-small" else [48, 17],
                               small_response_payload_bytes=[] if args.case == "q0-small" else [64, 113],
                               large_response_payload_bytes=0 if args.case == "q0-small" else LARGE,
                               q0_writable_capacity=14 if args.case == "q0-small" else 0x20000, host_outer_prefix="u32 LE body length, not present in guest chains"),
                  result=result, passed=result["passed"])
    path = run / "results.json"; path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("PASS" if report["passed"] else "FAIL", path.relative_to(ROOT), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
