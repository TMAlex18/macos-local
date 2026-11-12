#!/usr/bin/env python3
"""Read only the native X11 client window of this runner's own QEMU child.

The desktop connector cannot capture individual windows. XGetImage instead
reads the owned client drawable, never the root/desktop. A separate helper
process bounds Xlib calls so an unresponsive display cannot stall the runner.
"""
import argparse
import ctypes as C
import ctypes.util
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import zlib

ROOT = Path(__file__).resolve().parents[2]
BINARIES = {str((ROOT / f"build/{name}/qemu-system-aarch64").resolve())
            for name in ("qemu-reims-arm16k", "qemu-bdif-usb")}


def capture_owned_window(proc, output_dir, *, foreground="red"):
    """Return capture/match metadata while the supplied Popen child is alive."""
    if not isinstance(proc, subprocess.Popen) or proc.poll() is not None:
        return dict(captured=False, matched=False, reason="owned QEMU is not running")
    if not isinstance(proc.args, (tuple, list)) or str(Path(proc.args[0]).resolve()) not in BINARIES:
        raise ValueError("capture requires this experiment's own QEMU Popen handle")
    if foreground not in ("red", "green"):
        raise ValueError("unsupported expected triangle color")
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--qemu-pid", str(proc.pid),
             "--output-dir", str(output_dir), "--foreground", foreground],
            capture_output=True, text=True, timeout=4)
        if result.returncode:
            return dict(captured=False, matched=False, reason=result.stderr.strip()[-500:])
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return dict(captured=False, matched=False, reason="owned-window capture deadline")


def _verify_child(pid):
    stat = Path(f"/proc/{pid}/stat").read_text()
    # Fields after the final ')' start at state (3), then parent PID (4).
    parent = int(stat.rsplit(")", 1)[1].split()[1])
    executable = str(Path(f"/proc/{pid}/exe").resolve())
    if parent != os.getppid() or executable not in BINARIES:
        raise ValueError("target is not the calling runner's live QEMU child")


def _property(*args):
    result = subprocess.run(["xprop", *args], capture_output=True, text=True, timeout=1)
    return result.stdout if result.returncode == 0 else ""


class _XImage(C.Structure):
    # Prefix of /usr/include/X11/Xlib.h::_XImage, through the RGB masks.
    _fields_ = [(name, C.c_int) for name in ("width", "height", "xoffset", "format")] + [
        ("data", C.c_void_p)] + [(name, C.c_int) for name in (
            "byte_order", "bitmap_unit", "bitmap_bit_order", "bitmap_pad",
            "depth", "bytes_per_line", "bits_per_pixel")] + [
        ("red_mask", C.c_ulong), ("green_mask", C.c_ulong), ("blue_mask", C.c_ulong)]


def _decode(raw, layout):
    masks = [layout[name] for name in ("red_mask", "green_mask", "blue_mask")]
    if not all(masks):
        raise ValueError("unsupported XImage color masks")
    shifts = [(mask & -mask).bit_length() - 1 for mask in masks]
    maxima = [mask >> shift for mask, shift in zip(masks, shifts)]
    if any(not mask or maximum & (maximum + 1) for mask, maximum in zip(masks, maxima)):
        raise ValueError("unsupported XImage color masks")
    width, height = layout["width"], layout["height"]
    step = layout["bits_per_pixel"] // 8
    order = "little" if layout["byte_order"] == 0 else "big"
    rgba = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            offset = y * layout["bytes_per_line"] + x * step
            pixel = int.from_bytes(raw[offset:offset + step], order)
            out = (y * width + x) * 4
            for channel, (mask, shift, maximum) in enumerate(zip(masks, shifts, maxima)):
                value = (pixel & mask) >> shift
                rgba[out + channel] = (value * 255 + maximum // 2) // maximum
            rgba[out + 3] = 255  # X11 RGB masks carry no defined alpha channel.
    return bytes(rgba)


def _pattern(rgba, width, height, foreground="red"):
    # The guest already checks every source pixel exactly. Window scaling and
    # filtering are checked independently by safe interior/exterior patches,
    # with foreground/blue dominance rather than a color-calibration assertion.
    classes = bytearray(width * height)
    bounds = [width, height, -1, -1]
    foreground_count = blue = 0
    for i in range(width * height):
        r, g, b = rgba[4 * i:4 * i + 3]
        value, other = (r, g) if foreground == "red" else (g, r)
        code = (1 if value >= 128 and value >= 2 * other + 32 and value >= 2 * b + 32 else
                2 if b >= 128 and b >= 2 * r + 32 and b >= 2 * g + 32 else 0)
        classes[i] = code
        if code:
            x, y = i % width, i // width
            bounds = [min(bounds[0], x), min(bounds[1], y), max(bounds[2], x), max(bounds[3], y)]
            foreground_count += code == 1
            blue += code == 2
    left, top, right, bottom = bounds
    rw, rh = right - left + 1, bottom - top + 1
    samples = []
    if rw >= 32 and rh >= 32:
        points = [(0.5, 0.3, 1), (0.5, 0.5, 1), (0.3, 0.75, 1), (0.7, 0.75, 1),
                  (0.5, 0.05, 2), (0.5, 0.95, 2), (0.1, 0.5, 2), (0.9, 0.5, 2),
                  (0.25, 0.25, 2), (0.75, 0.25, 2)]
        for u, v, expected in points:
            x, y = left + round(u * (rw - 1)), top + round(v * (rh - 1))
            values = [classes[yy * width + xx]
                      for yy in range(max(0, y - 1), min(height, y + 2))
                      for xx in range(max(0, x - 1), min(width, x + 2))]
            samples.append(dict(x=x, y=y, expected=foreground if expected == 1 else "blue",
                                matched=sum(c == expected for c in values) >= 0.8 * len(values)))
    ratio = foreground_count / (foreground_count + blue) if foreground_count + blue else 0
    return dict(matched=bool(samples and all(s["matched"] for s in samples)
                             and foreground_count + blue >= 512 and 0.15 < ratio < 0.45),
                content_bounds=bounds, foreground=foreground,
                foreground_pixels=foreground_count,
                red_pixels=foreground_count if foreground == "red" else 0,
                green_pixels=foreground_count if foreground == "green" else 0,
                blue_pixels=blue, foreground_fraction_of_classified_pixels=ratio, samples=samples,
                scope=f"Owned native-window triangle geometry and {foreground}/blue regions; not calibrated color, physical scanout or latency.")


def _png(rgba, width, height):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    lines = b"".join(b"\0" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(lines)) + chunk(b"IEND", b""))


def _capture(pid, destination, foreground="red"):
    _verify_child(pid)
    destination = Path(destination).resolve()
    if not destination.is_relative_to(ROOT / "runs"):
        raise ValueError("capture output must be inside this workspace's runs directory")
    candidates = []
    for window in re.findall(r"0x[0-9a-fA-F]+", _property("-root", "_NET_CLIENT_LIST")):
        owner = re.search(r"=\s*(\d+)\s*$", _property("-id", window, "_NET_WM_PID"))
        if owner and int(owner.group(1)) == pid:
            candidates.append(int(window, 16))
    if not candidates:
        return dict(captured=False, matched=False, reason="owned client window not registered yet")

    lib = C.CDLL(ctypes.util.find_library("X11"))
    lib.XOpenDisplay.argtypes = [C.c_char_p]; lib.XOpenDisplay.restype = C.c_void_p
    lib.XCloseDisplay.argtypes = [C.c_void_p]
    lib.XSetErrorHandler.argtypes = [C.c_void_p]; lib.XSetErrorHandler.restype = C.c_void_p
    lib.XGetGeometry.argtypes = [C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong),
                                C.POINTER(C.c_int), C.POINTER(C.c_int)] + [C.POINTER(C.c_uint)] * 4
    lib.XGetImage.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_ulong, C.c_int]
    lib.XGetImage.restype = C.POINTER(_XImage)
    lib.XDestroyImage.argtypes = [C.POINTER(_XImage)]
    error_handler = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(lambda _d, _e: 0)
    previous = lib.XSetErrorHandler(C.cast(error_handler, C.c_void_p))
    display = lib.XOpenDisplay(None)
    if not display:
        lib.XSetErrorHandler(previous)
        return dict(captured=False, matched=False, reason="X11 connection unavailable")
    last = dict(captured=False, matched=False, reason="owned client not ready for image capture")
    try:
        for window in candidates[:4]:
            _verify_child(pid)
            root, x, y = C.c_ulong(), C.c_int(), C.c_int()
            width, height, border, depth = (C.c_uint() for _ in range(4))
            if not lib.XGetGeometry(display, window, C.byref(root), C.byref(x), C.byref(y),
                                   C.byref(width), C.byref(height), C.byref(border), C.byref(depth)):
                continue
            if not (32 <= width.value <= 2048 and 32 <= height.value <= 2048
                    and width.value * height.value <= 2_000_000):
                continue
            native = lib.XGetImage(display, window, 0, 0, width.value, height.value, C.c_ulong(-1).value, 2)
            if not native:
                continue
            try:
                img = native.contents
                layout = {name: int(getattr(img, name)) for name in (
                    "width", "height", "format", "depth", "byte_order", "bits_per_pixel",
                    "bytes_per_line", "red_mask", "green_mask", "blue_mask")}
                if (img.width != width.value or img.height != height.value or img.format != 2
                        or img.bits_per_pixel not in (16, 24, 32) or img.byte_order not in (0, 1)
                        or img.bytes_per_line < img.width * (img.bits_per_pixel // 8)
                        or img.bytes_per_line * img.height > 16_000_000 or not img.data):
                    raise ValueError("unsupported or inconsistent owned XImage layout")
                raw = C.string_at(img.data, img.bytes_per_line * img.height)
            finally:
                lib.XDestroyImage(native)
            _verify_child(pid)
            rgba = _decode(raw, layout)
            oracle = _pattern(rgba, width.value, height.value, foreground)
            destination.mkdir(parents=True, exist_ok=True)
            raw_path, preview = destination / "native-window.ximage", destination / "native-window.png"
            raw_path.write_bytes(raw)
            preview.write_bytes(_png(rgba, width.value, height.value))
            last = dict(captured=True, matched=oracle["matched"], method="XGetImage of owned X11 client window",
                        qemu_pid=pid, window_id=hex(window), ximage_layout=layout, oracle=oracle,
                        capture_time_utc=datetime.now(timezone.utc).isoformat(),
                        raw=str(raw_path.relative_to(ROOT)), preview=str(preview.relative_to(ROOT)),
                        full_desktop_captured=False)
            if oracle["matched"]:
                return last
        return last
    finally:
        lib.XCloseDisplay(display)
        lib.XSetErrorHandler(previous)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu-pid", type=int, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--foreground", choices=("red", "green"), default="red")
    args = parser.parse_args()
    try:
        print(json.dumps(_capture(args.qemu_pid, args.output_dir, args.foreground)))
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
