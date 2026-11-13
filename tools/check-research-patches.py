#!/usr/bin/env python3
"""Rebuild only changed source files from pinned HEAD plus recorded patches.

Checks patch persistence, not a clean-host full dependency rebuild.
Creates/removes only its own temporary directory inside build/ and writes its
report to runs/.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
BUILD_ROOT=ROOT/'build'
sets=[
 ('reims-arm-guest','sources/reims-arm-guest',[
  'patches/reims/0001-pin-metal2vulkan-git-sources.patch',
  'patches/reims/0002-cross-isa-arm64-guest-pages.patch',
  'patches/reims/0003-iosurface-guest-page-separation.patch',
  'patches/reims/0004-fixture-host-guest-page-boundaries.patch',
  'patches/reims/0005-isolated-task-host-alignment.patch',
  'patches/reims/0006-detached-heap-aliasable-backing.patch',
  'patches/reims/0007-half-float-fetch-source-over.patch']),
 ('metal2vulkan-research','sources/metal2vulkan-research',[
  'patches/metal2vulkan/0001-half-float-fetch-source-over.patch']),
 ('qemu-reims-arm','sources/qemu-reims-arm',[
  'patches/qemu-platform/0003-vmapple-tcg-pac-model-stack.patch',
  'patches/qemu-platform/0004-research-avp-rtc-ctrr-mmio.patch']),
 ('qemu-clock-exact','sources/qemu-clock-exact',[
  'patches/qemu-platform/0003-vmapple-tcg-pac-model-stack.patch',
  'patches/qemu-platform/0005-exact-counter-deadline-clock.patch']),
 ('qemu-bdif-usb','sources/qemu-bdif-usb',[
  'patches/qemu-platform/0003-vmapple-tcg-pac-model-stack.patch',
  'patches/qemu-platform/0005-exact-counter-deadline-clock.patch',
  'patches/upstream-reference/steelbrain-vmapple-usb-chardev-657bb24.patch',
  'patches/qemu-platform/0006-bdif-usb-framing-lifecycle.patch',
  'patches/qemu-platform/0007-bdif-usb-initial-tx-readiness.patch',
  'patches/qemu-platform/0008-bdif-usb-queue-control.patch',
  'patches/qemu-platform/0009-bdif-block-write-stage1-rx-capacity.patch',
  'patches/qemu-platform/0010-vmapple-config-dma.patch',
  'patches/qemu-platform/0011-vmapple-avp-rtc.patch',
  'patches/qemu-platform/0012-vmapple-ctrr-contract-device.patch',
  'patches/qemu-platform/0013-virtio-qmp-backend-features.patch',
  'patches/qemu-platform/0014-vmapple-virtio-capability-order.patch',
  'patches/qemu-platform/0015-vmapple-virtio-usb-transport.patch',
  'patches/qemu-platform/0016-vmapple-ctrr-shared-romd-ram.patch',
  'patches/qemu-platform/0017-bdif-backend-capacity.patch',
  'patches/qemu-platform/0018-pauth-packed-mult.patch',
  'patches/qemu-platform/0019-opt-in-ascending-pci-capabilities.patch',
  'patches/qemu-platform/0020-pauth-packed-qarma5-sbox.patch',
  'patches/qemu-platform/0021-pauth-grouped-permutations.patch',
  'patches/qemu-platform/0022-pauth-per-vcpu-memo.patch',
  'patches/qemu-platform/0023-virtio-snd-pcm-boundary-trace.patch',
  'patches/qemu-platform/0024-pauth-memo-capacity-stats.patch',
  'patches/qemu-platform/0025-pauth-va-projection.patch',
  'patches/qemu-platform/0026-tb-lookup-state-pointer.patch',
  'patches/qemu-platform/0027-virtio-snd-empty-info.patch',
  'patches/qemu-platform/0028-virtio-snd-queue-size.patch',
  'patches/qemu-platform/0029-pauth-memo-16384.patch',
  'patches/qemu-platform/0030-arm-timeout-diagnostic.patch',
  'patches/qemu-platform/0031-arm-timeout-runtime-slide.patch',
  'patches/qemu-platform/0032-virtio-net-control-queue-size.patch',
  'patches/qemu-platform/0033-tcg-sync-source-counters.patch',
  'patches/qemu-platform/0034-arm-gtimer-lockless-counter-read.patch',
  'patches/qemu-platform/0035-tcg-arm-skip-masked-irq-and-exittb-bql.patch',
  'patches/qemu-platform/0036-vmapple-pac-hvc-lockless-and-key-cache.patch',
  'patches/qemu-platform/0037-tcg-no-bql-queued-tlbi-work.patch',
  'patches/qemu-platform/0038-arm-tlbi-dsb-batch-sync.patch',
  'patches/qemu-platform/0039-arm-el10-eret-bql-elision.patch',
  'patches/qemu-platform/0040-tcg-tb-jmp-cache-pcrel-and-size.patch',
  'patches/qemu-platform/0041-tcg-cputlb-16k-subpage-and-arm-el23-fastpath.patch',
  'patches/qemu-platform/0042-arm-pauth-va-cache-and-simd-qarma5.patch',
  'patches/qemu-platform/0043-arm-sme-fmopa-h-preunpack.patch',
  'patches/qemu-platform/0044-tcg-host-tagged-tb-jmp-cache.patch',
  'patches/qemu-platform/0045-arm-ttbr-selective-tlb-flush.patch'])]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--component',choices=[x[0] for x in sets])
args=parser.parse_args()
report=[]
for name,source,patches in sets:
 if args.component and name!=args.component:continue
 src=ROOT/source
 paths=set(subprocess.check_output(['git','-C',str(src),'diff','--name-only'],text=True).splitlines())
 # New files carried by a patch may not be tracked in the upstream worktree.
 for patch in patches:
  if not (ROOT/patch).is_file():
   raise SystemExit(f'{name}: missing {patch} (run ./tools/fetch-sources.sh first)')
  for line in (ROOT/patch).read_text().splitlines():
   if line.startswith('+++ b/'):
    paths.add(line[6:])
 paths=sorted(paths)
 pin=subprocess.check_output(['git','-C',str(src),'rev-parse','HEAD'],text=True).strip()
 BUILD_ROOT.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='patch-reconstruct-',dir=BUILD_ROOT) as temp:
  dest=Path(temp)
  subprocess.run(['git','init','-q',str(dest)],check=True)
  for relative in paths:
   f=dest/relative;f.parent.mkdir(parents=True,exist_ok=True)
   exists=subprocess.check_output(['git','-C',str(src),'ls-tree','HEAD','--',relative],text=True)
   if exists:
    f.write_bytes(subprocess.check_output(['git','-C',str(src),'show',f'HEAD:{relative}']))
  for patch in patches:
   subprocess.run(['git','-C',str(dest),'apply',str(ROOT/patch)],check=True)
  mismatch=[p for p in paths if (dest/p).read_bytes()!=(src/p).read_bytes()]
  if mismatch: raise SystemExit(f'{name}: recorded patches differ from live source: {mismatch}')
 report.append({'component':name,'base_commit':pin,'changed_files':paths,'patches':patches,'passed':True})
 print(f'{name}: {len(paths)} changed files reconstructed exactly from recorded patches')
out=ROOT/('runs/patch-reconstruction-'+args.component+'.json' if args.component else 'runs/v3-patch-reconstruction.json')
# runs/ is ignored by git, so a fresh checkout does not have it yet.
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(report,indent=2)+'\n')
print('PASS: changed tracked sources reproducible; NOT a full clean-host build claim')
