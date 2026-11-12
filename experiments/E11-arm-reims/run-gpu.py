#!/usr/bin/env python3
"""Self-authored ARM guest probes: Reims GPU/display and VM-local xHCI/HID input."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import tempfile
import time
import zlib
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
TEST = Path(__file__).resolve().parent
PLATFORMS = {
    "graphics": dict(binary="build/qemu-reims-arm16k/qemu-system-aarch64",
                     memory="512M", vcpus=1, cpu="max",
                     machine="vmapple,memory-backend=ram", models_configured=False),
    "restore-research": dict(binary="build/qemu-bdif-usb/qemu-system-aarch64",
                             memory="4G", vcpus=2,
                             cpu="max,pauth-qarma5=on,cntfrq=24000000,x-cntfrq-exact=on",
                             machine="vmapple,memory-backend=ram,uuid=1,x-ctrr-model=on,x-pac-software-model=on",
                             models_configured=True),
}
SHADERS = ROOT / "sources/reims-arm-guest/tests/suite/fixtures/shaders"


def checked(argv, **kwargs):
    return subprocess.run([str(a) for a in argv], check=True, **kwargs)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def actual_device_evidence(stderr, gpu):
    """Keep QEMU's runtime selection evidence separate from loader inventory."""
    driver = "VK_" + gpu["driver_id"]
    lines = []
    for line in stderr.splitlines():
        device = re.search(r"deviceID (0x[0-9a-fA-F]+)", line)
        if (gpu["name"] in line or
                (device and int(device.group(1), 16) == int(gpu["device_id"], 16)
                 and driver in line)):
            lines.append(line)
    return dict(matched=bool(lines), lines=lines,
                method="QEMU runtime stderr GPU name or matching Vulkan driver ID + device ID")


def save_pixels(stdout, run, case):
    """Only decode the guest's actual readback, never generate an expected image."""
    pattern = r"PIXELS_(RGBA|BGRA|RGBA16F)_BEGIN\n((?:[0-9a-f]{512}\n)+)PIXELS_\1_END\n"
    matches = list(re.finditer(pattern, stdout))
    if not matches:
        return None, stdout
    frames = []
    for index, match in enumerate(matches):
        layout, hex_data = match.group(1), match.group(2)
        memory = bytes.fromhex(hex_data)
        if len(memory) != 64 * 64 * (8 if layout == "RGBA16F" else 4):
            raise ValueError("guest pixel readback has the wrong byte count")
        rgba = (bytes(round(max(0.0, min(1.0, value[0])) * 255)
                      for value in struct.iter_unpack("<e", memory)) if layout == "RGBA16F" else
                memory if layout == "RGBA" else b"".join(
            memory[i + 2:i + 3] + memory[i + 1:i + 2] + memory[i:i + 1] + memory[i + 3:i + 4]
            for i in range(0, len(memory), 4)))
        name = case if len(matches) == 1 else f"{case}-pass{index + 1}"
        frames.append(write_pixels(memory, rgba, layout, run, name))
    metadata = dict(frames[0])
    if len(frames) > 1:
        metadata["frames"] = frames
    return metadata, re.sub(pattern, "[guest pixel readback saved]\n", stdout)


def write_pixels(memory, rgba, layout, run, name):
    raw, preview = run / f"{name}.{layout.lower()}", run / f"{name}.png"
    raw.write_bytes(memory)
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data +
                struct.pack(">I", zlib.crc32(kind + data)))
    scanlines = b"".join(b"\0" + rgba[y * 256:(y + 1) * 256] for y in range(64))
    preview.write_bytes(b"\x89PNG\r\n\x1a\n" +
                        chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 6, 0, 0, 0)) +
                        chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))
    counts = dict(red=0, blue=0, other=0)
    for i in range(0, len(rgba), 4):
        p = rgba[i:i + 4]
        if p == b"\0\xff\0\xff":
            counts["green"] = counts.get("green", 0) + 1
            continue
        counts["red" if p == b"\xff\0\0\xff" else
               "blue" if p == b"\0\0\xff\xff" else "other"] += 1
    metadata = dict(width=64, height=64, format="RGBA16Float" if layout == "RGBA16F" else layout + "8Unorm", counts=counts,
                    raw=str(raw.relative_to(ROOT)), preview=str(preview.relative_to(ROOT)),
                    raw_bytes=len(memory), raw_sha256=hashlib.sha256(memory).hexdigest(),
                    preview_format="RGBA8Unorm",
                    preview_conversion="little-endian binary16 RGBA to clamped/rounded unorm8" if layout == "RGBA16F" else
                                       "swap B/R channels only" if layout == "BGRA" else "identity")
    # The full UART remains in the raw log; avoid duplicating its pixel body in JSON/stdout.
    return metadata


def run_window_case(cmd, env, run, *, updating=False, native_input=False):
    # The helper is provided separately and accepts only this owned Popen.
    from window_capture import capture_owned_window
    capture = dict(captured=False, matched=False)
    timed_out = False
    captures, input_events = {}, []
    with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8", dir=run) as out, \
            tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8", dir=run) as err:
        proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        print(f"QEMU PID: {proc.pid}; run: {run.relative_to(ROOT)}", flush=True)
        deadline = time.monotonic() + (20 if native_input else 90)
        try:
            for foreground in (("red", "green") if updating else ("red",)):
                stage = dict(captured=False, matched=False)
                mismatched = 0
                while proc.poll() is None and time.monotonic() < deadline:
                    destination = run / ("native-window-" + foreground if updating else "native-window")
                    stage = capture_owned_window(proc, destination, foreground=foreground)
                    if stage.get("matched"):
                        break
                    mismatched += bool(stage.get("captured"))
                    time.sleep(0.05)
                stage["live_nonmatching_captures_before_match"] = mismatched
                captures[foreground] = stage
                if not stage.get("matched"):
                    break
                if native_input and foreground == "red":
                    from window_input import send_owned_key
                    for down, marker in ((True, "INPUT NATIVE KEY DOWN READY"), (False, "INPUT NATIVE KEY UP READY")):
                        while marker not in Path(out.name).read_text():
                            if proc.poll() is not None or time.monotonic() > deadline:
                                raise RuntimeError("guest did not reach " + marker)
                            time.sleep(0.01)
                        action = send_owned_key(proc, stage["window_id"], down=down)
                        input_events.append(action)
                        if not action.get("sent"):
                            raise RuntimeError("owned window event: " + action.get("reason", "not sent"))
            capture = dict(captures=captures, input_events=input_events) if native_input else captures if updating else captures["red"]
            proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
        except Exception as exc:
            capture = (dict(captures=captures, input_events=input_events, error=str(exc)) if native_input else
                       dict(captured=False, matched=False, error=str(exc)))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        out.seek(0); err.seek(0)
        return out.read(), err.read(), proc.returncode, timed_out, capture


def run_input_case(cmd, env, run, work, *, keyboard_only=False, missing_irq=False):
    """Send only VM-local QMP events; the guest must receive real USB reports."""
    path, out_path, err_path = work / "input-qmp.sock", work / "input-out.log", work / "input-err.log"
    transcript = []
    timed_out = False
    error = None
    with out_path.open("w") as out, err_path.open("w") as err:
        proc = subprocess.Popen([*cmd, "-qmp", f"unix:{path},server=on,wait=off"], env=env,
                                stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        deadline = time.monotonic() + 60
        try:
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(5)
                while True:
                    try:
                        connection.connect(str(path)); break
                    except (FileNotFoundError, ConnectionRefusedError):
                        if proc.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("owned input VM did not open QMP")
                        time.sleep(0.01)
                stream = connection.makefile("r", encoding="utf-8")
                transcript.append(json.loads(stream.readline()))
                def qmp(name, arguments=None):
                    request = dict(execute=name, id=len(transcript))
                    if arguments is not None: request["arguments"] = arguments
                    transcript.append(dict(request=request))
                    connection.sendall((json.dumps(request) + "\n").encode())
                    while True:
                        line = stream.readline()
                        if not line: raise RuntimeError("owned QMP closed")
                        response = json.loads(line); transcript.append(response)
                        if response.get("id") == request["id"]:
                            if "error" in response: raise RuntimeError(str(response["error"]))
                            return response.get("return")
                qmp("qmp_capabilities")
                phases = ((False, "INPUT QMP DOWN READY"),) if missing_irq else ((False, "INPUT QMP DOWN READY"), (True, "INPUT QMP UP READY"))
                for released, marker in phases:
                    while marker not in out_path.read_text():
                        if proc.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("guest did not reach " + marker)
                        time.sleep(0.01)
                    if not released:
                        qmp("query-pci"); qmp("query-mice")
                        qmp("human-monitor-command", {"command-line": "info usb"})
                    events = [{"type": "key", "data": {"down": not released, "key": {"type": "qcode", "data": "a"}}}]
                    if not released and not keyboard_only:
                        events += [{"type": "abs", "data": {"axis": "x", "value": 12345}},
                                   {"type": "abs", "data": {"axis": "y", "value": 23456}}]
                    if not keyboard_only:
                        events += [{"type": "btn", "data": {"button": "left", "down": not released}}]
                    qmp("input-send-event", {"events": events})
                proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        except (subprocess.TimeoutExpired, TimeoutError):
            timed_out = True
        except Exception as exc:
            error = str(exc)
        finally:
            if proc.poll() is None: proc.kill(); proc.wait()
    (run / "qmp-input.json").write_text(json.dumps(dict(transcript=transcript, error=error), indent=2) + "\n")
    return out_path.read_text(), err_path.read_text(), proc.returncode, timed_out, error


def usb_irq_captures(stdout):
    return [dict(zip(("phase", "intid", "iman", "usbsts", "count"),
                     (int(v, 16) for v in values))) for values in re.findall(
        r"INPUT IRQ CAPTURE PHASE=([0-9a-f]{8}) INTID=([0-9a-f]{8}) IMAN=([0-9a-f]{8}) USBSTS=([0-9a-f]{8}) COUNT=([0-9a-f]{8})", stdout)]


def valid_usb_irq_captures(captures):
    return ([c["phase"] for c in captures] == [1, 2] and
            all(c["intid"] == 67 and c["iman"] & 3 == 3 and c["usbsts"] & 8 and
                c["count"] >= c["phase"] for c in captures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", choices=("nvidia", "intel"), default="nvidia")
    parser.add_argument("--workload", choices=("compute", "draw", "iosurface-draw", "present", "present-update", "input", "input-irq", "window-input", "window-input-irq"), default="compute")
    parser.add_argument("--platform", choices=tuple(PLATFORMS), default="graphics",
                        help="self-authored probe on the graphics build or current restore QEMU/CPU/memory/model configuration; disks remain synthetic stubs")
    parser.add_argument("--surface-format", choices=("bgra8", "rgba16float"), default="bgra8",
                        help="present format; rgba16float uses fourcc RGhA and exact binary16 guest checks")
    parser.add_argument("--expect-binary-sha256", help="abort before any VM/GPU work unless the selected platform binary has this SHA256")
    parser.add_argument("--heap", action="store_true", help="compute through a 20MiB type13 detached heap over pair-swapped 16KiB guest pages")
    parser.add_argument("--irq", action="store_true", help="require ISR-verified completion: GPU SPI49 or input-irq xHCI INTx SPI67")
    parser.add_argument("--disable-work-irq", action="store_true",
                        help="IRQ negative control: mask the work SPI; require guest exit1 with valid GPU output or USB DMA event")
    args = parser.parse_args()
    if args.heap and (args.workload != "compute" or not args.irq or args.disable_work_irq):
        parser.error("--heap requires --workload compute --irq with enabled work IRQ")
    native_usb_irq = args.workload == "window-input-irq"
    native_input = args.workload in ("window-input", "window-input-irq")
    windowed = args.workload in ("present", "present-update") or native_input
    updating = args.workload == "present-update" or native_input
    input_irq = args.workload == "input-irq"
    input_probe = args.workload in ("input", "input-irq")
    input_hardware = input_probe or native_input
    work_irq_stamps = {3, 9} if updating else {3, 5} if args.workload == "iosurface-draw" else {3}
    platform = PLATFORMS[args.platform]
    binary = ROOT / platform["binary"]
    half_float = args.surface_format == "rgba16float"
    surface_workload = args.workload in ("iosurface-draw", "present", "present-update", "window-input", "window-input-irq")
    if half_float and args.workload != "present":
        parser.error("--surface-format rgba16float requires the single-frame present workload")
    pixel_format = "RGBA16Float" if half_float else "BGRA8Unorm" if surface_workload else "RGBA8Unorm"
    surface_contract = dict(fourcc=0x52476841 if half_float else 0x42475241,
                            mtl_format=115 if half_float else 80, bytes_per_element=8 if half_float else 4,
                            bytes_per_row=512 if half_float else 256, image_bytes=32768 if half_float else 16384,
                            allocation_bytes=49152 if half_float else 32768)
    binary_sha256 = digest(binary)
    if args.expect_binary_sha256 and binary_sha256 != args.expect_binary_sha256:
        parser.error(f"binary SHA256 differs: {binary_sha256}")
    if args.disable_work_irq and not args.irq:
        parser.error("--disable-work-irq requires --irq")
    if args.disable_work_irq and args.workload == "iosurface-draw":
        parser.error("IOSurface workload has its own retain/release control; IRQ negative uses compute/draw")
    if windowed and (not args.irq or args.disable_work_irq):
        parser.error("present requires --irq and an enabled work IRQ")
    if args.workload == "input" and (args.platform != "graphics" or args.irq):
        parser.error("input uses the input-capable graphics build and polls xHCI transfer events; omit --irq")
    if input_irq and (args.platform != "graphics" or not args.irq):
        parser.error("input-irq requires the input-capable graphics build and --irq")
    if native_input and args.platform != "graphics":
        parser.error("window-input requires the input-capable graphics build; restore binary is retained unchanged")
    mode = "irq-disabled" if args.disable_work_irq else "irq" if args.irq else "poll"
    suffix = "" if args.platform == "graphics" else "-" + args.platform
    if args.heap: suffix += "-heap"
    if half_float: suffix += "-rgba16float"
    build = ROOT / (f"build/e11-{args.workload}" + ("-irq" if args.irq else "") + suffix)
    build.mkdir(parents=True, exist_ok=True)
    run = (ROOT / f"runs/e11-arm-reims-{args.workload}" /
           (args.gpu + "-" + (args.platform + "-" if args.platform != "graphics" else "") +
            ("rgba16float-" if half_float else "") + (mode + "-" if args.irq else "") +
            ("heap-" if args.heap else "") +
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")))
    run.mkdir(parents=True, exist_ok=False)
    print(f"run: {run.relative_to(ROOT)}", flush=True)
    if input_hardware:
        models = checked([binary, "-device", "help"], capture_output=True, text=True).stdout
        (run / "graphics-device-models.log").write_text(models)
        if not all(f'name "{name}"' in models for name in ("qemu-xhci", "usb-kbd", "usb-tablet")):
            raise SystemExit("input-capable graphics build required; rebuild with tools/build-reims-qemu-arm16k.sh")
    env = os.environ.copy()
    # One hardware ICD for both the independent inventory and actual QEMU process.
    # No software ICD and no externally selected explicit layers (e.g. nullhw).
    for key in ("VK_ICD_FILENAMES", "VK_ADD_DRIVER_FILES", "VK_INSTANCE_LAYERS",
                "VK_LOADER_LAYERS_ENABLE", "VK_LOADER_DRIVERS_SELECT", "VK_LOADER_DRIVERS_DISABLE"):
        env.pop(key, None)
    icd = Path(f"/usr/share/vulkan/icd.d/{args.gpu}_icd.json")
    if not icd.is_file():
        raise SystemExit(f"hardware ICD absent: {icd}")
    env.update(VK_DRIVER_FILES=str(icd), VK_LOADER_LAYERS_DISABLE="*",
               REIMS_VGPU_WINDOW="0", REIMS_VGPU_LOG="warn")
    if windowed:
        env.pop("SDL_VIDEODRIVER", None)  # SDL2 spelling is not the SDL3 contract.
        env.update(SDL_VIDEO_DRIVER="x11", REIMS_VGPU_WINDOW="1", REIMS_VGPU_STATS="1",
                   REIMS_VGPU_LOG="info", REIMS_VGPU_WINDOW_BORDERLESS="0")
    inventory = checked(["vulkaninfo", "--summary"], env=env, capture_output=True, text=True)
    (run / "vulkan-device.log").write_text(inventory.stdout + "\nSTDERR:\n" + inventory.stderr)
    names = re.findall(r"deviceName\s*=\s*(.+)", inventory.stdout)
    types = re.findall(r"deviceType\s*=\s*(.+)", inventory.stdout)
    if len(names) != 1 or len(types) != 1 or types[0].strip() not in (
            "PHYSICAL_DEVICE_TYPE_DISCRETE_GPU", "PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU"):
        raise SystemExit("one real hardware Vulkan device required; see vulkan-device.log")
    gpu = dict(name=names[0].strip(), type=types[0].strip(), icd=str(icd),
               inventory_log=str((run / "vulkan-device.log").relative_to(ROOT)),
               isolation=dict(VK_DRIVER_FILES=str(icd), VK_LOADER_LAYERS_DISABLE="*"),
               vendor_id=re.search(r"vendorID\s*=\s*(\S+)", inventory.stdout).group(1),
               device_id=re.search(r"deviceID\s*=\s*(\S+)", inventory.stdout).group(1),
               driver_id=re.search(r"driverID\s*=\s*(\S+)", inventory.stdout).group(1),
               driver_info=re.search(r"driverInfo\s*=\s*(.+)", inventory.stdout).group(1).strip())

    shaders = ([('shader', SHADERS / 'compute_add.metallib')] if args.workload == "compute" else
               [('vertex', SHADERS / 'render_vbuf.metallib'), ('fragment', SHADERS / 'render_color.metallib')])
    if input_probe: shaders = []
    shader_asm = build / "shader.S"
    shader_asm.write_text('.section .rodata\n' + ''.join(
        f'.global {name}_start, {name}_end\n{name}_start:\n.incbin "{path}"\n{name}_end:\n'
        for name, path in shaders))
    checked(["clang", "--target=aarch64-none-elf", "-c", shader_asm, "-o", build / "shader.o"])
    common_defines = ["-DUSE_IRQ=1"] if args.irq else []
    if args.heap: common_defines.append("-DUSE_HEAP=1")
    if input_probe: common_defines.append("-DUSE_BSS_INIT=1")
    if input_irq: common_defines.append("-DUSE_USB_IRQ=1")
    if surface_workload:
        common_defines.append("-DUSE_IOSURFACE=1")
    if half_float: common_defines.append("-DSURFACE_RGBA16FLOAT=1")
    if windowed:
        common_defines.append("-DUSE_DISPLAY=1")
    if updating:
        common_defines.append("-DUSE_DISPLAY_UPDATE=1")
    if native_input: common_defines.append("-DUSE_NATIVE_INPUT=1")
    if native_usb_irq: common_defines.append("-DUSE_NATIVE_USB_IRQ=1")
    checked(["clang", "--target=aarch64-none-elf", *common_defines, "-c", TEST / "guest-start.S",
             "-o", build / "start.o"])
    cases = []
    specifications = ((
        ("positive", [], "REIMS ARM GPU HEAP 20MIB COMPUTE AND GUARDS PASS" if args.heap else "REIMS ARM GPU COMPUTE 256 VALUES AND GUARDS PASS"),
        ("no-exec", ["-DNO_EXEC=1"], "REIMS ARM NO EXEC OUTPUT UNCHANGED PASS"))
        if args.workload == "compute" else (
        ("positive", [], "REIMS ARM GPU DRAW 4096 PIXELS AND GUARD PASS"),
        ("no-draw", ["-DNO_DRAW=1"], "REIMS ARM NO DRAW CLEAR ONLY 4096 PIXELS AND GUARD PASS")))
    if args.irq:
        specifications = (("irq-disabled", ["-DBLOCK_WORK_SPI=1"], "REIMS IRQ REQUIRED MISSING EXPECTED"),) if args.disable_work_irq else (specifications[0],)
    if args.workload == "iosurface-draw":
        specifications = (("positive", [], "REIMS ARM IOSURFACE GPU DRAW RELEASE REUSE PASS"),)
    if windowed:
        specifications = (("positive", [], "REIMS ARM NATIVE WINDOW USB IRQ KEY TO GREEN PASS" if native_usb_irq else "REIMS ARM NATIVE WINDOW USB KEY TO GREEN PASS" if native_input else "REIMS ARM IOSURFACE TWO FRAME PRESENT RETIRE PASS" if updating else "REIMS ARM IOSURFACE WINDOW PRESENT PASS"),)
    if input_probe:
        specifications = ((("irq-disabled", ["-DBLOCK_WORK_SPI=1"], "REIMS INPUT IRQ REQUIRED MISSING EXPECTED"),)
                          if args.disable_work_irq else (("positive", [], "REIMS ARM XHCI INTERRUPT IN GIC KEY DOWN UP PASS"
                                                        if input_irq else "REIMS ARM XHCI USB HID INPUT REPORTS PASS"),))
    for case, define, marker in specifications:
        checked(["clang", "--target=aarch64-none-elf", "-O2", "-ffreestanding",
                 "-fno-builtin", "-fno-stack-protector", "-mgeneral-regs-only", "-Wall", "-Wextra",
                 *common_defines, *define, "-c", TEST / ("probe-input.c" if input_probe else "probe-compute.c" if args.workload == "compute" else "probe-draw.c"), "-o", build / f"{case}.o"])
        extra_objects = []
        if native_input:
            input_object = build / "input-core.o"
            checked(["clang", "--target=aarch64-none-elf", "-O2", "-ffreestanding", "-fno-builtin",
                     "-fno-stack-protector", "-mgeneral-regs-only", "-Wall", "-Wextra",
                     "-DE11_INPUT_LIBRARY=1", *(["-DUSE_USB_IRQ=1", "-DUSE_NATIVE_USB_IRQ=1"] if native_usb_irq else []),
                     "-c", TEST / "probe-input.c", "-o", input_object])
            extra_objects.append(input_object)
        checked(["ld.lld", "-T", TEST / "guest.ld", build / "start.o", build / f"{case}.o",
                 *extra_objects, build / "shader.o", "-o", build / f"{case}.elf"])
        image = build / f"{case}.bin"
        checked(["llvm-objcopy", "-O", "binary", build / f"{case}.elf", image])
        # Disposable stub disks never contain Apple media and are removed on every exit.
        with tempfile.TemporaryDirectory(prefix="e11-disks-", dir=build) as disks:
            aux, root = Path(disks) / "aux.raw", Path(disks) / "root.raw"
            for path, size in ((aux, 4 << 20), (root, 32 << 20)):
                with path.open("wb") as f:
                    f.truncate(size)
            cmd = [str(binary), "-object", f'memory-backend-memfd,id=ram,size={platform["memory"]},share=on',
                   "-machine", platform["machine"], "-m", platform["memory"], "-smp", str(platform["vcpus"]),
                   "-cpu", platform["cpu"], "-accel", "tcg", "-nodefaults", "-nic", "none",
                   "-display", "none", "-nographic", "-monitor", "none", "-serial", "stdio",
                   "-semihosting-config", "enable=on,target=native", "-bios", str(image),
                   "-drive", f"file={aux},format=raw,if=pflash,index=0",
                   "-drive", f"file={root},format=raw,if=pflash,index=1"]
            if windowed:
                cmd += ["-global", "apple-gfx-mmio.displays=1", "-global", "apple-gfx-mmio.resolution=320x240"]
            if input_hardware:
                cmd += ["-device", "qemu-xhci,id=input-xhci,addr=3", "-device",
                        "usb-kbd,id=input-keyboard,bus=input-xhci.0,port=1"]
                if input_probe and not input_irq: cmd += ["-device", "usb-tablet,id=input-tablet,bus=input-xhci.0,port=2"]
                if native_input: cmd += ["-trace", "enable=input_event_key_qcode"]
            timed_out = False
            window = None
            input_error = None
            try:
                if input_probe:
                    stdout, stderr, code, timed_out, input_error = run_input_case(cmd, env, run, Path(disks),
                                                                                  keyboard_only=input_irq, missing_irq=args.disable_work_irq)
                elif windowed:
                    stdout, stderr, code, timed_out, window = run_window_case(cmd, env, run, updating=updating, native_input=native_input)
                else:
                    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=90)
                    stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout = (exc.stdout or b"").decode(errors="replace")
                stderr = (exc.stderr or b"").decode(errors="replace")
                code = None
            native_actions, native_error = [], None
            if native_input and window:
                native_actions = window.get("input_events", [])
                native_error = window.get("error")
                window = window.get("captures", {})
            log = run / f"{case}.log"
            log.write_text(stdout + "\nSTDERR:\n" + stderr)
            selection = actual_device_evidence(stderr, gpu)
            expected_exit = 1 if args.disable_work_irq else 0
            passed = (not timed_out and code == expected_exit and marker in stdout
                      and "reims-vgpu: fatal" not in stderr and (input_probe or selection["matched"]))
            if case == "no-exec":
                passed &= "GPU COMPUTE 256 VALUES AND GUARDS PASS" not in stdout
            pixels, brief_uart = (save_pixels(stdout, run, case) if not input_probe and args.workload != "compute" else (None, stdout))
            if updating:
                passed &= bool(pixels and [f["counts"] for f in pixels.get("frames", [])] == [
                    dict(red=1152, blue=2944, other=0), dict(red=0, green=1152, blue=2944, other=0)])
            elif not input_probe and args.workload != "compute":
                passed &= bool(pixels and all(frame["counts"] == dict(
                    red=0 if case == "no-draw" else 1152, blue=4096 if case == "no-draw" else 2944, other=0)
                    for frame in pixels.get("frames", [pixels])))
            surface_commands = [dict(zip(("command", "mapping_id", "status", "read_counter", "mapped"),
                                         (int(v, 16) for v in values))) for values in re.findall(
                r"REIMS IOSURFACE COMMAND=([0-9a-f]{8}) ID=([0-9a-f]{8}) STATUS=([0-9a-f]{8}) READ=([0-9a-f]{8}) MAPPED=([0-9a-f]{8})", stdout)]
            surface_metadata = None
            if surface_workload:
                match = re.search(r"REIMS SURFACE FOURCC=([0-9a-f]{8}) MTL_FORMAT=([0-9a-f]{8}) BPE=([0-9a-f]{8}) ROW_BYTES=([0-9a-f]{8}) IMAGE_BYTES=([0-9a-f]{8}) ALLOCATION_BYTES=([0-9a-f]{8})", stdout)
                surface_metadata = dict(zip(surface_contract, (int(v, 16) for v in match.groups()))) if match else None
                passed &= surface_metadata == surface_contract
                passed &= bool(pixels and all(frame["format"] == pixel_format for frame in pixels.get("frames", [pixels])))
            if args.workload == "iosurface-draw":
                passed &= (len(pixels.get("frames", [])) == 2 if pixels else False)
                passed &= [(c["command"], c["mapping_id"], c["status"], c["read_counter"], c["mapped"]) for c in surface_commands] == [
                    (1, 1, 0, 1, 1), (2, 1, 0xe00002d5, 2, 1), (2, 1, 0, 3, 0),
                    (1, 1, 0, 4, 1), (2, 1, 0, 5, 0)]
                passed &= "REIMS IOSURFACE RELEASE REMAP NEW BACKING AND RETIRED GUARD PASS" in stdout
            display_state = None
            display_states = []
            if windowed:
                mode_changes = re.findall(r"display on port 0: mode (\d+)x(\d+), pixel format (0x[0-9a-f]+)", stderr)
                expected_mode = ("64", "64", hex(surface_contract["fourcc"]))
                passed &= expected_mode in mode_changes
                matches = re.findall(r"REIMS DISPLAY PRESENT BEFORE=([0-9a-f]{8}) AFTER=([0-9a-f]{8}) MAPPING=([0-9a-f]{8}) INTID=([0-9a-f]{8}) PORTS=([0-9a-f]{8}) RAISED=([0-9a-f]{8})", stdout)
                display_states = [dict(zip(("present_count_before", "present_count_after", "mapping_id", "intid", "port_bitmap", "raised_interrupts"), (int(v, 16) for v in values))) for values in matches]
                display_state = display_states[-1] if display_states else None
                stats = [line for line in stderr.splitlines() if "reims-vgpu stats: port 0:" in line]
                window_gpu_stats = [line for line in stats if
                    (rate := re.search(r"window ([0-9.]+)/s", line)) and float(rate.group(1)) > 0 and
                    re.search(r"window GPU [0-9.]+ ms avg", line)]
                expected_mappings = [1, 2] if updating else [1]
                passed &= len(display_states) == len(expected_mappings) and all(
                    d["present_count_before"] == i and d["present_count_after"] == i + 1 and
                    d["mapping_id"] == expected_mappings[i] and d["intid"] == 49 and
                    d["port_bitmap"] & 1 and d["raised_interrupts"] & 2
                    for i, d in enumerate(display_states))
                if updating:
                    passed &= bool(window and all(window.get(color, {}).get("captured") and
                        window[color].get("matched") and window[color].get("oracle", {}).get("foreground") == color
                        for color in ("red", "green")))
                    if window and "red" in window and "green" in window:
                        passed &= window["red"].get("window_id") == window["green"].get("window_id") and window["red"].get("qemu_pid") == window["green"].get("qemu_pid")
                    # Two frames can complete in one statistics period. The
                    # per-frame proof is the two native colors/counts/IRQs.
                    passed &= bool(window_gpu_stats)
                else:
                    passed &= bool(window and window.get("captured") and window.get("matched") and window_gpu_stats)
                passed &= "windows open through SDL's x11 driver" in stderr
                passed &= "REIMS DISPLAY CONNECT ACK RUNNING PASS" in stdout and "REIMS DISPLAY FRAME HOLD END" in stdout
                if updating:
                    events = [(c["command"], c["mapping_id"], c["status"], c["mapped"]) for c in surface_commands]
                    passed &= len(events) >= 4 and events[:3] == [(1, 1, 0, 1), (2, 1, 0xe00002d5, 1), (1, 2, 0, 1)]
                    passed &= bool(events and events[-1] == (2, 1, 0, 0) and all(e == (2, 1, 0xe00002d5, 1) for e in events[3:-1]))
                    passed &= "REIMS IOSURFACE INDEPENDENT IDS AND PAGE TABLES PASS" in stdout and "REIMS DISPLAY RETIRED BACKING AFTER GREEN HOLD PASS" in stdout
                    passed &= stdout.count("REIMS DISPLAY FRAME HOLD END") == (1 if native_input else 2)
                else:
                    passed &= [(c["command"], c["status"], c["mapped"]) for c in surface_commands] == [(1, 0, 1)]
            irq_capture = [dict(zip(("expected_stamp", "intid", "bitmap", "memory_stamp", "fault", "count"),
                                    (int(v, 16) for v in values))) for values in re.findall(
                r"REIMS IRQ CAPTURE EXPECTED=([0-9a-f]{8}) INTID=([0-9a-f]{8}) BITS=([0-9a-f]{8}) MEMORY=([0-9a-f]{8}) FAULT=([0-9a-f]{8}) COUNT=([0-9a-f]{8})", stdout)]
            valid_stamps = {r["expected_stamp"] for r in irq_capture if r["intid"] == 49 and
                            r["bitmap"] & 1 and r["memory_stamp"] == r["expected_stamp"] and r["fault"] == 0}
            if args.irq and not input_irq:
                passed &= {1, 2}.issubset(valid_stamps)
                if args.disable_work_irq:
                    passed &= (3 not in valid_stamps and "REIMS GPU RESULT ORACLE PASS" in stdout and
                               "REIMS IRQ DISABLED GPU MEMORY STAMP3 READY WITHOUT WORK ISR" in stdout and
                               "REIMS GPU IRQ WORK COMPLETION PASS" not in stdout)
                else:
                    passed &= work_irq_stamps.issubset(valid_stamps) and "REIMS GPU IRQ WORK COMPLETION PASS" in stdout
            cases.append(dict(case=case, passed=passed, guest_exit=code, timed_out=timed_out,
                              expected_exit=expected_exit,
                              uart=brief_uart, log=str(log.relative_to(ROOT)),
                              firmware_sha256=digest(image), actual_gpu_selection=selection,
                              **({"surface_metadata": surface_metadata} if surface_workload else {}),
                              **({"irq_capture": irq_capture, "gpu_work_irq_verified":
                                  work_irq_stamps.issubset(valid_stamps),
                                  "gpu_work_irq_stamps_verified": sorted(valid_stamps & work_irq_stamps),
                                  "missing_work_irq_expected": args.disable_work_irq} if args.irq and not input_irq else {}),
                              **({"pixels": pixels} if pixels else {})))
            if input_irq:
                reports = {label: [int(v, 16) for v in values.split()] for label, values in re.findall(
                    r"INPUT IRQ (KEY DOWN|KEY UP|MISSING KEY DOWN) REPORT=([0-9a-f ]+)", stdout)}
                captures = usb_irq_captures(stdout)
                if args.disable_work_irq:
                    valid = (reports == {"MISSING KEY DOWN": [0, 0, 4, 0, 0, 0, 0, 0]} and not captures and
                             "INPUT IRQ DISABLED DMA EVENT READY GIC PENDING WITHOUT ISR" in stdout and
                             "REIMS ARM XHCI INTERRUPT IN GIC KEY DOWN UP PASS" not in stdout)
                else:
                    valid = (reports == {"KEY DOWN": [0, 0, 4, 0, 0, 0, 0, 0], "KEY UP": [0] * 8} and
                             valid_usb_irq_captures(captures))
                cases[-1]["passed"] &= input_error is None and valid
                cases[-1]["input"] = dict(error=input_error, guest_reports=reports, irq_capture=captures,
                    transport="USB HID interrupt-IN / xHCI event ring / PCI INTA / level GIC SPI INTID67",
                    source="VM-local QMP keyboard events; no native-window or desktop input",
                    missing_irq_expected=args.disable_work_irq,
                    data_ready_without_isr_verified=args.disable_work_irq and valid,
                    normal_completion_wait_reads_only_isr_state=True,
                    qmp_log=str((run / "qmp-input.json").relative_to(ROOT)))
                passed = cases[-1]["passed"]
            elif input_probe:
                reports = {label: [int(v, 16) for v in values.split()] for label, values in re.findall(
                    r"INPUT (KEY DOWN|KEY UP|POINTER DOWN|POINTER UP) REPORT=([0-9a-f ]+)", stdout)}
                transfer = re.search(r"INPUT XHCI TRANSFER EVENTS=([0-9a-f]{8})", stdout)
                expected = {"KEY DOWN": [0, 0, 4, 0, 0, 0, 0, 0], "KEY UP": [0] * 8,
                            "POINTER DOWN": [1, 0x39, 0x30, 0xa0, 0x5b, 0], "POINTER UP": [0, 0x39, 0x30, 0xa0, 0x5b, 0]}
                cases[-1]["passed"] &= input_error is None and reports == expected and bool(transfer and int(transfer.group(1), 16) >= 10)
                cases[-1]["input"] = dict(error=input_error, guest_reports=reports,
                    usb_transfer_events=int(transfer.group(1), 16) if transfer else None,
                    transport="guest xHCI EP0 control transfers / USB HID GET_REPORT",
                    source="VM-local QMP input-send-event; no native-window or desktop input",
                    qmp_log=str((run / "qmp-input.json").relative_to(ROOT)))
                passed = cases[-1]["passed"]
            if native_input:
                report_prefix = r"INPUT IRQ" if native_usb_irq else r"INPUT NATIVE"
                reports = {label: [int(v, 16) for v in values.split()] for label, values in re.findall(
                    report_prefix + r" (KEY DOWN|KEY UP) REPORT=([0-9a-f ]+)", stdout)}
                trace = [line for line in stderr.splitlines() if "input_event_key_qcode" in line and "qcode a," in line]
                directions = re.findall(r"qcode a, down ([01])", "\n".join(trace))
                cases[-1]["passed"] &= (native_error is None and reports == {
                    "KEY DOWN": [0, 0, 4, 0, 0, 0, 0, 0], "KEY UP": [0] * 8} and
                    directions == ["1", "0"] and [a.get("down") for a in native_actions] == [True, False] and
                    all(a.get("sent") and a.get("native_keyboard_focus_owned") and
                        not a.get("global_input_changed", True) and not a.get("focus_changed", True) for a in native_actions))
                cases[-1]["native_window_input"] = dict(error=native_error, actions=native_actions,
                    guest_reports=reports, qemu_input_callback_trace=trace,
                    transport="owned XSendEvent -> SDL/Reims -> QEMU input -> guest xHCI/HID",
                    qmp_input_events_sent=False, green_draw_requires_guest_key_down=True,
                    release_requires_guest_hid_report=True)
                if native_usb_irq:
                    captures = usb_irq_captures(stdout)
                    markers = ("REIMS IRQ CAPTURE EXPECTED=00000003", "REIMS DISPLAY PRESENT BEFORE=00000000",
                               "INPUT IRQ CAPTURE PHASE=00000001", "REIMS IRQ CAPTURE EXPECTED=00000009",
                               "REIMS DISPLAY PRESENT BEFORE=00000001", "INPUT IRQ CAPTURE PHASE=00000002",
                               "REIMS ARM NATIVE WINDOW USB IRQ KEY TO GREEN PASS")
                    offsets = [stdout.find(m) for m in markers]
                    ordered = min(offsets) >= 0 and offsets == sorted(set(offsets))
                    cases[-1]["passed"] &= valid_usb_irq_captures(captures) and ordered
                    cases[-1]["native_window_input"].update(
                        transport="owned XSendEvent -> SDL/Reims/QEMU -> USB HID interrupt-IN -> level INTID67 ISR -> green GPU/display edge INTID49 -> owned native frame",
                        usb_irq_capture=captures, usb_endpoint_transfer="interrupt-IN", green_gpu_requires_usb_isr67=True,
                        ordered_guest_markers=list(zip(markers, offsets)), ordered_guest_markers_verified=ordered)
                passed = cases[-1]["passed"]
            if args.workload == "iosurface-draw":
                cases[-1]["iosurface"] = dict(commands=surface_commands, descriptor_version=2,
                    descriptor_mapped_writeback_verified=[c["mapped"] for c in surface_commands] == [1, 1, 0, 1, 0], mapping_id=1,
                    nonplanar_single_image=True, host_test_pattern_command_used=False,
                    gpu_passes=len(pixels.get("frames", [])) if pixels else 0,
                    busy_while_held_verified=any(c["command"] == 2 and c["status"] == 0xe00002d5 and c["mapped"] == 1 for c in surface_commands),
                    release_and_new_backing_verified="REIMS IOSURFACE RELEASE REMAP NEW BACKING AND RETIRED GUARD PASS" in stdout,
                    retired_backing_checked_after_second_gpu_completion=(not args.irq or 5 in valid_stamps) and
                        "REIMS IOSURFACE RELEASE REMAP NEW BACKING AND RETIRED GUARD PASS" in stdout,
                    retired_backing_poison="0x13579bdf",
                    guest_backing_physical_addresses=["0x70100000", "0x70110000"],
                    allocation_bytes_per_mapping=surface_contract["allocation_bytes"], image_bytes=surface_contract["image_bytes"],
                    descriptor_guest_physical_address="0x70088400",
                    descriptor_table_guest_physical_address="0x70084000",
                    surface_page_table_guest_physical_address="0x7008c000")
            if windowed:
                cases[-1]["display"] = dict(shared_state=display_state, shared_states=display_states,
                    **({"native_window_captures": window} if updating else {"native_window_capture": window}),
                    window_gpu_stats=window_gpu_stats, window_stats=stats,
                    sdl_driver="x11", resolution_requested=[320, 240], guest_hold_seconds=5,
                    host_mode_changes=[dict(width=int(w), height=int(h), fourcc=f) for w, h, f in mode_changes],
                    iosurface_commands=surface_commands, mapping_held_through_guest_exit=True)
                if updating:
                    cases[-1]["display"].update(retired_mapping_id=1, active_mapping_id=display_state["mapping_id"] if display_state else None,
                        guest_total_hold_seconds=5 if native_input else 10,
                        retired_backing_poison="0x13579bdf", retired_backing_checked_after_green_hold=
                        "REIMS DISPLAY RETIRED BACKING AFTER GREEN HOLD PASS" in stdout,
                        transient_busy_retries_after_swap=sum(c["status"] == 0xe00002d5 for c in surface_commands[3:]),
                        independent_surface_page_tables=["0x7008c000", "0x70094000"],
                        independent_surface_backings=["0x70100000", "0x70110000"])
            print(f"{case}: {'PASS' if passed else 'FAIL'}\n{brief_uart}", flush=True)
            if not passed:
                break
    report = dict(time_utc=datetime.now(timezone.utc).isoformat(),
                  scope=("self-authored ARM64 USB HID interrupt-IN / xHCI PCI INTx / GIC ISR; VM-local QMP keyboard only"
                         if input_irq else "self-authored ARM64 guest xHCI DMA/USB HID reports from VM-local QMP input; native SDL event leg not exercised"
                         if input_probe else f"self-authored ARM64 guest normal Reims {args.workload} submission and guest readback; no macOS/Metal certification"),
                  binary=str(binary.relative_to(ROOT)), binary_sha256=binary_sha256,
                  platform=dict(name=args.platform, cpu_requested=platform["cpu"],
                                memory=platform["memory"], configured_vcpus=platform["vcpus"],
                                probe_uses_cpu0_only=True, machine=platform["machine"],
                                ctrr_and_pac_models_configured=platform["models_configured"],
                                model_semantics_exercised=False,
                                extra_input_devices=(["qemu-xhci@PCI slot3", "usb-kbd@port1", "usb-tablet@port2"] if input_probe and not input_irq else
                                                     ["qemu-xhci@PCI slot3", "usb-kbd@port1"] if native_input or input_irq else []),
                                storage="synthetic 4MiB AUX and 32MiB root pflash stubs; no restore storage/transport workflow"),
                  shaders=[dict(role=name, path=str(path.relative_to(ROOT)), sha256=digest(path)) for name, path in shaders],
                  gpu=gpu,
                  runtime_policy=dict(reims_vgpu_exact=env.get("REIMS_VGPU_EXACT")),
                  completion_mode="usb-intx67-and-gpu-display-edge49" if native_usb_irq else "xhci-intx-gic-irq" if input_irq else "xhci-event-ring-poll" if input_probe else "irq" if args.irq else "poll", missing_work_irq_control=args.disable_work_irq,
                  guest_page_size=16384, host_page_size=os.sysconf("SC_PAGE_SIZE"),
                  work=(dict(kind="input", controller="qemu-xhci", pci_address="00:03.0",
                             usb_devices=["usb-kbd"] if input_irq else ["usb-kbd", "usb-tablet"],
                             usb_transfer="interrupt-IN endpoint from configuration descriptor" if input_irq else "EP0 HID GET_REPORT",
                             keyboard_usage=4, **({"gic_intid": 67, "trigger": "level", "pci_interrupt": "INTA"} if input_irq else
                                                {"pointer_xy": [12345, 23456], "button": "left"}), phases=["down"] if args.disable_work_irq else ["down", "up"],
                             xhci_dma_page_size=4096, gpu_work_exercised=False)
                        if input_probe else dict(kind="heap-compute" if args.heap else "compute", shader="add_one",
                            threads=5242880 if args.heap else 256, threads_per_group=256 if args.heap else 64,
                            fifo_opcodes=[56, 51, 67, 32], output_bytes_checked=20971520 if args.heap else 16384,
                            input_bytes_checked=20971520 if args.heap else 16384,
                            **(dict(heap_bytes=20971520, heap_type=13, child_buffer_type=14,
                                    guest_pages=1280, physical_page_order="adjacent pairs swapped",
                                    outer_guard_bytes_checked=32768) if args.heap else {})) if args.workload == "compute" else
                        dict(kind=args.workload, width=64, height=64, format=pixel_format, vertices=3,
                             **({"surface": surface_contract} if surface_workload else {}),
                             fifo_opcodes=[56, 51, 67, 1, 2, 8, 37] if updating else [56, 51, 67, 1, 2, 8] if windowed else [56, 51, 67, 37, 32] if args.workload == "iosurface-draw" else [56, 51, 67, 32], render_draw_opcode="0x01 DrawPrimitives16",
                             output_pixels_checked=8192 if args.workload == "iosurface-draw" or updating else 4096,
                             output_guard_bytes_checked=32768 if args.workload == "iosurface-draw" or updating else 16384)),
                  oracle=("one owned VM: directed native A down/up -> standard HID interrupt-IN / real IAR67; guest green GPU submission follows down ISR; GPU/display IAR49, two full-frame oracles and matching red/green native captures; same PID/XID and retired backing checks"
                          if native_usb_irq else "guest requires actual IAR67, xHCI IP/EINT and matching successful interrupt-IN Transfer Event plus eight DMA report bytes in the ISR; normal acknowledgement enables the next release; masked-SPI negative requires data/event/GIC pending but exit1 without ISR"
                          if input_irq else "guest verifies successful xHCI control transfer events and DMA-written USB HID keyboard usage4 down/up plus pointer12345/23456 and left-button down/up reports; no host writes to guest report buffers"
                          if input_probe else f"guest checks all {5242880 if args.heap else 256} uint32 outputs == ((i*1664525)^1013904223)+1 modulo 2^32, input unchanged, guards unchanged"
                          if args.workload == "compute" else
                          "owned native-window A events traverse SDL/Reims/QEMU and real guest USB HID reports; guest draws green only after usage4 down and must read release; independent red/green native captures plus display IRQ/counts and retired backing checks"
                          if native_input else
                          f"guest checks red then green {pixel_format} frames/guards over independent IOSurfaces, separate mapped-present counts/IRQs and retired ID1 poison after green hold; host independently captures matching red then green pixels of the same owned X11 window"
                          if updating else
                          f"guest checks one {pixel_format} triangle frame/guard and mapped-present count/mapping/display IRQ; host independently requires matching owned X11 window pixels plus window GPU stats"
                          if windowed else
                          f"guest checks two {pixel_format} frames against exact triangle half-planes, both guards, Map/Busy/release/remap and retired backing after the second GPU completion; no host test-pattern command"
                          if args.workload == "iosurface-draw" else
                          "guest checks all pixel centers against triangle half-planes: red inside, blue outside; no-draw all blue; exact 0/255 RGBA8, no center on an edge; guard unchanged"),
                  cases=cases, passed=len(cases) == len(specifications) and all(c["passed"] for c in cases))
    (run / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
