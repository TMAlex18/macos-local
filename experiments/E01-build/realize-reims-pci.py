#!/usr/bin/env python3
"""Paused headless QEMU device-realization/QMP probe, NO macOS guest."""
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
binary = root / "build/qemu-reims-x86/qemu-system-x86_64"
rom = root / "build/reims-target/dist-vulkan/share/reims-vgpu/ReimsVgpu.rom"
assert binary.is_file() and rom.is_file()
args = [
    str(binary), "-machine", "q35,memory-backend=ram",
    "-object", "memory-backend-memfd,id=ram,size=512M,share=on",
    "-m", "512M", "-smp", "1", "-accel", "tcg",
    "-nodefaults", "-nic", "none", "-serial", "none",
    "-display", "none", "-monitor", "none", "-S",
    "-device", f"apple-gfx-pci,romfile={rom}",
    "-qmp", "stdio",
]
qmp = '\n'.join(json.dumps({"execute": command}) for command in
                ["qmp_capabilities", "query-pci", "quit"]) + "\n"
env = os.environ.copy()
env["REIMS_VGPU_WINDOW"] = "0"
try:
    proc = subprocess.run(
        args, input=qmp, text=True, capture_output=True, env=env, timeout=20
    )
except subprocess.TimeoutExpired:
    print("FAIL: paused headless QEMU did not quit from QMP within 20 s")
    raise SystemExit(9)
log = root / "runs/e01-reims-pci-qmp.log"
log.write_text(proc.stdout + "\n=== STDERR ===\n" + proc.stderr)
print("qemu_exit:", proc.returncode)
if proc.returncode != 0:
    print("qemu stderr:\n", proc.stderr[-3000:])
    raise SystemExit(2)
responses = []
for line in proc.stdout.splitlines():
    try:
        responses.append(json.loads(line))
    except json.JSONDecodeError:
        pass

def find_pci(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get("id"), dict) and obj["id"].get("vendor") == 0x106B and obj["id"].get("device") == 0xEEEE:
            return True
        return any(find_pci(x) for x in obj.values())
    if isinstance(obj, list):
        return any(find_pci(x) for x in obj)
    return False

found = any(find_pci(response.get("return")) for response in responses
            if isinstance(response, dict))
print("qmp_response_count:", len(responses))
print("apple_gfx_pci_106b_eeee:", found)
if not found:
    print("QMP output tail:", proc.stdout[-1800:])
    raise SystemExit(3)
print("PASS: QEMU realized Reims vGPU PCI device and QMP returned expected PCI IDs")
