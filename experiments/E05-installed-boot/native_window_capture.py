#!/usr/bin/env python3
"""Read one X11 client selected and process-bound by guest_ui, never the root.

The calling GuestUI session owns run/process/peer validation. This bounded
subprocess only reads its explicit client drawable and sends no QMP or input.
"""
import argparse
import ctypes as C
import ctypes.util
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('e11_window_capture',ROOT/'experiments/E11-arm-reims/window_capture.py')
x11=importlib.util.module_from_spec(spec);spec.loader.exec_module(x11)


class CaptureError(RuntimeError):pass


def unique_window(pid):
    candidates=[]
    for window in re.findall(r'0x[0-9a-fA-F]+',x11._property('-root','_NET_CLIENT_LIST')):
        owner=re.search(r'=\s*(\d+)\s*$',x11._property('-id',window,'_NET_WM_PID'))
        if owner and int(owner.group(1))==pid:candidates.append(int(window,16))
    if not candidates:raise CaptureError('no_owned_window')
    if len(candidates)!=1:raise CaptureError('multiple_owned_windows')
    return candidates[0]


def capture(window,pid,destination):
    if unique_window(pid)!=window:raise CaptureError('window_identity_changed')
    destination=Path(destination).absolute()
    if (destination.resolve()!=destination or not destination.is_relative_to(ROOT/'runs/e05-installed-boot')
            or destination.suffix!='.png' or destination.exists()):
        raise CaptureError('invalid_destination')
    library=ctypes.util.find_library('X11')
    if library is None:raise CaptureError('x11_unavailable')
    lib=C.CDLL(library)
    lib.XOpenDisplay.argtypes=[C.c_char_p];lib.XOpenDisplay.restype=C.c_void_p
    lib.XCloseDisplay.argtypes=[C.c_void_p]
    lib.XSetErrorHandler.argtypes=[C.c_void_p];lib.XSetErrorHandler.restype=C.c_void_p
    lib.XGetGeometry.argtypes=[C.c_void_p,C.c_ulong,C.POINTER(C.c_ulong),C.POINTER(C.c_int),
                              C.POINTER(C.c_int)]+[C.POINTER(C.c_uint)]*4
    lib.XGetImage.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.c_int,C.c_uint,C.c_uint,C.c_ulong,C.c_int]
    lib.XGetImage.restype=C.POINTER(x11._XImage)
    lib.XDestroyImage.argtypes=[C.POINTER(x11._XImage)]
    handler=C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_void_p)(lambda _d,_e:0)
    previous=lib.XSetErrorHandler(C.cast(handler,C.c_void_p))
    display=lib.XOpenDisplay(None)
    if not display:
        lib.XSetErrorHandler(previous)
        raise CaptureError('x11_unavailable')
    try:
        root,x,y=C.c_ulong(),C.c_int(),C.c_int()
        width,height,border,depth=(C.c_uint() for _ in range(4))
        if not lib.XGetGeometry(display,window,C.byref(root),C.byref(x),C.byref(y),
                                C.byref(width),C.byref(height),C.byref(border),C.byref(depth)):
            raise CaptureError('invalid_image')
        if window==root.value:raise CaptureError('root_window_rejected')
        if not (0<width.value<=8192 and 0<height.value<=8192 and width.value*height.value<=8_388_608):
            raise CaptureError('invalid_image')
        if unique_window(pid)!=window:raise CaptureError('window_identity_changed')
        native=lib.XGetImage(display,window,0,0,width.value,height.value,C.c_ulong(-1).value,2)
        if not native:raise CaptureError('invalid_image')
        try:
            img=native.contents
            layout={name:int(getattr(img,name)) for name in ('width','height','format','depth','byte_order',
                    'bits_per_pixel','bytes_per_line','red_mask','green_mask','blue_mask')}
            if (img.width!=width.value or img.height!=height.value or img.format!=2 or
                    img.bits_per_pixel not in (16,24,32) or img.byte_order not in (0,1) or
                    img.bytes_per_line<img.width*(img.bits_per_pixel//8) or
                    img.bytes_per_line*img.height>64*1024*1024 or not img.data):
                raise CaptureError('invalid_image')
            raw=C.string_at(img.data,img.bytes_per_line*img.height)
        finally:lib.XDestroyImage(native)
        if unique_window(pid)!=window:raise CaptureError('window_identity_changed')
        rgba=x11._decode(raw,layout)
        png=x11._png(rgba,width.value,height.value)
        fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o600)
        with os.fdopen(fd,'wb') as output:output.write(png)
        return dict(window_id=hex(window),qemu_pid=pid,width=width.value,height=height.value,
                    ximage_layout=layout,capture_time_utc=datetime.now(timezone.utc).isoformat(),
                    full_desktop_captured=False)
    finally:
        lib.XCloseDisplay(display);lib.XSetErrorHandler(previous)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window-id',type=lambda value:int(value,0),required=True)
    parser.add_argument('--qemu-pid',type=int,required=True)
    parser.add_argument('--png',required=True)
    args=parser.parse_args()
    try:print(json.dumps(capture(args.window_id,args.qemu_pid,args.png)))
    except Exception as error:
        print(json.dumps({'reason':str(error) if isinstance(error,CaptureError) else 'capture_failed'}))
        raise SystemExit(1)
