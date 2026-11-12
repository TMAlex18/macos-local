#!/usr/bin/env python3
"""Queue A key events only to this runner's owned, focused X11 QEMU client.

No XTest, global key state, focus changes, pointer movement, or other windows.
XSendEvent acceptance is not guest receipt: the guest independently reads HID.
"""
import argparse
import ctypes as C
import ctypes.util
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

from window_capture import BINARIES, _property, _verify_child


def send_owned_key(proc, window_id, *, down):
    if not isinstance(proc, subprocess.Popen) or proc.poll() is not None:
        return dict(sent=False, reason="owned QEMU is not running")
    if not isinstance(proc.args, (tuple, list)) or str(Path(proc.args[0]).resolve()) not in BINARIES:
        raise ValueError("input requires this experiment's own QEMU Popen")
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
            "--qemu-pid", str(proc.pid), "--window-id", str(window_id),
            "--down" if down else "--up"], capture_output=True, text=True, timeout=4)
        if result.returncode:
            return dict(sent=False, reason=result.stderr.strip()[-500:])
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        # A queued event may already have taken effect. Never retry it here.
        return dict(sent=False, reason="owned event deadline; outcome unknown, no retry")


class _Key(C.Structure):
    _fields_ = [("type", C.c_int), ("serial", C.c_ulong), ("send_event", C.c_int),
        ("display", C.c_void_p), ("window", C.c_ulong), ("root", C.c_ulong),
        ("subwindow", C.c_ulong), ("time", C.c_ulong), ("x", C.c_int), ("y", C.c_int),
        ("x_root", C.c_int), ("y_root", C.c_int), ("state", C.c_uint),
        ("keycode", C.c_uint), ("same_screen", C.c_int)]


class _Event(C.Union):
    _fields_ = [("key", _Key), ("pad", C.c_long * 24)]


def _send(pid, window_id, down):
    _verify_child(pid)
    owner = re.search(r"=\s*(\d+)\s*$", _property("-id", hex(window_id), "_NET_WM_PID"))
    if not owner or int(owner.group(1)) != pid:
        raise ValueError("target client is not owned by this QEMU")
    lib = C.CDLL(ctypes.util.find_library("X11"))
    lib.XOpenDisplay.argtypes = [C.c_char_p]; lib.XOpenDisplay.restype = C.c_void_p
    lib.XCloseDisplay.argtypes = [C.c_void_p]
    lib.XGetInputFocus.argtypes = [C.c_void_p, C.POINTER(C.c_ulong), C.POINTER(C.c_int)]
    lib.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]; lib.XKeysymToKeycode.restype = C.c_ubyte
    lib.XDefaultRootWindow.argtypes = [C.c_void_p]; lib.XDefaultRootWindow.restype = C.c_ulong
    lib.XSendEvent.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_long, C.POINTER(_Event)]
    lib.XSync.argtypes = [C.c_void_p, C.c_int]
    lib.XSetErrorHandler.argtypes = [C.c_void_p]; lib.XSetErrorHandler.restype = C.c_void_p
    errors = []
    handler = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(lambda _d, _e: errors.append(True) or 0)
    previous = lib.XSetErrorHandler(C.cast(handler, C.c_void_p))
    display = lib.XOpenDisplay(None)
    if not display:
        lib.XSetErrorHandler(previous)
        raise ValueError("X11 display unavailable")
    try:
        focus, revert = C.c_ulong(), C.c_int()
        lib.XGetInputFocus(display, C.byref(focus), C.byref(revert))
        if focus.value != window_id:
            raise ValueError("owned client lacks native keyboard focus; no global focus change attempted")
        code = lib.XKeysymToKeycode(display, 0x61)  # X11 keysym a
        if not code: raise ValueError("X11 A keycode unavailable")
        event = _Event()
        event.key = _Key(type=2 if down else 3, send_event=1, display=display,
            window=window_id, root=lib.XDefaultRootWindow(display), subwindow=0,
            time=0, x=1, y=1, x_root=0, y_root=0, state=0, keycode=code, same_screen=1)
        _verify_child(pid)
        queued = lib.XSendEvent(display, window_id, 0, 1 if down else 2, C.byref(event))
        lib.XSync(display, 0)
        if not queued or errors: raise ValueError("owned X11 key event was not accepted")
        _verify_child(pid)
        return dict(sent=True, method="XSendEvent to owned X11 client; propagate=false",
                    qemu_pid=pid, window_id=hex(window_id), key="a", down=down,
                    x11_keycode=int(code), native_keyboard_focus_owned=True,
                    event_time_utc=datetime.now(timezone.utc).isoformat(),
                    global_input_changed=False, focus_changed=False,
                    scope="Application-targeted synthetic event; guest HID receipt checked separately")
    finally:
        lib.XCloseDisplay(display); lib.XSetErrorHandler(previous)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu-pid", type=int, required=True)
    parser.add_argument("--window-id", type=lambda value: int(value, 0), required=True)
    keys = parser.add_mutually_exclusive_group(required=True)
    keys.add_argument("--down", action="store_true"); keys.add_argument("--up", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(_send(args.qemu_pid, args.window_id, args.down)))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr); raise SystemExit(2)
