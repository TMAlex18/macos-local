#!/usr/bin/env bash
# Independent transport research binary. No Apple code or real USB controller.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/sources/qemu-bdif-usb"
OUT="$ROOT/build/qemu-bdif-usb"
PIN=1a72df984914fb9929d35b1217619178133d65a4
python3 - "$OUT/qemu-system-aarch64" <<'PY'
import os,sys
from pathlib import Path
binary=str(Path(sys.argv[1]).resolve())
live=[]
for executable in Path('/proc').glob('[0-9]*/exe'):
 try: target=os.readlink(executable)
 except OSError: continue
 if target in (binary,binary+' (deleted)'):live.append(int(executable.parent.name))
if live:
 raise SystemExit(f'Refusing to replace the live QEMU executable (PIDs {sorted(live)}); finish those VMs or use a separate candidate build directory.')
PY
if [ ! -f "$ROOT/patches/upstream-reference/steelbrain-vmapple-usb-chardev-657bb24.patch" ]; then
 echo 'Missing external reference USB patch; run ./tools/fetch-sources.sh first.' >&2
 exit 2
fi
if [ ! -e "$SRC/.git" ]; then
 git -C "$ROOT/sources/reims/vendor/qemu" worktree add --detach "$SRC" "$PIN"
fi
if [ "$(git -C "$SRC" rev-parse HEAD)" != "$PIN" ]; then echo 'Unexpected QEMU pin' >&2; exit 2; fi
if ! python3 "$ROOT/tools/check-research-patches.py" --component qemu-bdif-usb >/dev/null 2>&1; then
 if ! git -C "$SRC" diff --quiet; then echo 'Transport worktree differs from patch record' >&2; exit 2; fi
 for p in "$ROOT/patches/qemu-platform/0003-vmapple-tcg-pac-model-stack.patch" \
          "$ROOT/patches/qemu-platform/0005-exact-counter-deadline-clock.patch" \
          "$ROOT/patches/upstream-reference/steelbrain-vmapple-usb-chardev-657bb24.patch" \
          "$ROOT/patches/qemu-platform/0006-bdif-usb-framing-lifecycle.patch" \
          "$ROOT/patches/qemu-platform/0007-bdif-usb-initial-tx-readiness.patch" \
          "$ROOT/patches/qemu-platform/0008-bdif-usb-queue-control.patch" \
          "$ROOT/patches/qemu-platform/0009-bdif-block-write-stage1-rx-capacity.patch" \
          "$ROOT/patches/qemu-platform/0010-vmapple-config-dma.patch" \
          "$ROOT/patches/qemu-platform/0011-vmapple-avp-rtc.patch" \
          "$ROOT/patches/qemu-platform/0012-vmapple-ctrr-contract-device.patch" \
          "$ROOT/patches/qemu-platform/0013-virtio-qmp-backend-features.patch" \
          "$ROOT/patches/qemu-platform/0014-vmapple-virtio-capability-order.patch" \
          "$ROOT/patches/qemu-platform/0015-vmapple-virtio-usb-transport.patch" \
          "$ROOT/patches/qemu-platform/0016-vmapple-ctrr-shared-romd-ram.patch" \
          "$ROOT/patches/qemu-platform/0017-bdif-backend-capacity.patch" \
          "$ROOT/patches/qemu-platform/0018-pauth-packed-mult.patch" \
          "$ROOT/patches/qemu-platform/0019-opt-in-ascending-pci-capabilities.patch" \
          "$ROOT/patches/qemu-platform/0020-pauth-packed-qarma5-sbox.patch" \
          "$ROOT/patches/qemu-platform/0021-pauth-grouped-permutations.patch" \
          "$ROOT/patches/qemu-platform/0022-pauth-per-vcpu-memo.patch" \
          "$ROOT/patches/qemu-platform/0023-virtio-snd-pcm-boundary-trace.patch" \
          "$ROOT/patches/qemu-platform/0024-pauth-memo-capacity-stats.patch" \
          "$ROOT/patches/qemu-platform/0025-pauth-va-projection.patch" \
          "$ROOT/patches/qemu-platform/0026-tb-lookup-state-pointer.patch" \
          "$ROOT/patches/qemu-platform/0027-virtio-snd-empty-info.patch" \
          "$ROOT/patches/qemu-platform/0028-virtio-snd-queue-size.patch" \
          "$ROOT/patches/qemu-platform/0029-pauth-memo-16384.patch" \
          "$ROOT/patches/qemu-platform/0030-arm-timeout-diagnostic.patch" \
          "$ROOT/patches/qemu-platform/0031-arm-timeout-runtime-slide.patch" \
          "$ROOT/patches/qemu-platform/0032-virtio-net-control-queue-size.patch" \
          "$ROOT/patches/qemu-platform/0033-tcg-sync-source-counters.patch" \
          "$ROOT/patches/qemu-platform/0034-arm-gtimer-lockless-counter-read.patch" \
          "$ROOT/patches/qemu-platform/0035-tcg-arm-skip-masked-irq-and-exittb-bql.patch" \
          "$ROOT/patches/qemu-platform/0036-vmapple-pac-hvc-lockless-and-key-cache.patch" \
          "$ROOT/patches/qemu-platform/0037-tcg-no-bql-queued-tlbi-work.patch" \
          "$ROOT/patches/qemu-platform/0038-arm-tlbi-dsb-batch-sync.patch" \
          "$ROOT/patches/qemu-platform/0039-arm-el10-eret-bql-elision.patch" \
          "$ROOT/patches/qemu-platform/0040-tcg-tb-jmp-cache-pcrel-and-size.patch" \
          "$ROOT/patches/qemu-platform/0041-tcg-cputlb-16k-subpage-and-arm-el23-fastpath.patch" \
          "$ROOT/patches/qemu-platform/0042-arm-pauth-va-cache-and-simd-qarma5.patch" \
          "$ROOT/patches/qemu-platform/0043-arm-sme-fmopa-h-preunpack.patch" \
          "$ROOT/patches/qemu-platform/0044-tcg-host-tagged-tb-jmp-cache.patch" \
          "$ROOT/patches/qemu-platform/0045-arm-ttbr-selective-tlb-flush.patch"; do
  git -C "$SRC" apply "$p"
 done
 python3 "$ROOT/tools/check-research-patches.py" --component qemu-bdif-usb >/dev/null
fi
# The shared ARM profile stays unchanged for the other research binaries.
# This build compiles standard VirtIO sound, still opt-in at VM creation.
mkdir -p "$OUT/device-profile"
python3 - "$ROOT/experiments/E01-build/reims-arm64-tcg.mak" "$OUT/device-profile/reims-arm64-tcg.mak" <<'PY'
from pathlib import Path
import sys
base,target=map(Path,sys.argv[1:])
text=base.read_text().rstrip()+'\nCONFIG_VIRTIO_SND=y\n'
if not target.exists() or target.read_text()!=text:
 target.write_text(text)
PY
ln -sfn "$OUT/device-profile/reims-arm64-tcg.mak" "$SRC/configs/devices/aarch64-softmmu/reims-arm64-tcg.mak"
export PKG_CONFIG_PATH="$ROOT/build/reims-arm-target/dist-vulkan/lib/pkgconfig"
pkg-config --exists --static reims-vgpu
if ! pkg-config --atleast-version=3.7.3 nettle; then
 echo 'Missing nettle development files >=3.7.3 (Ubuntu: nettle-dev)' >&2; exit 3
fi
mkdir -p "$OUT" "$ROOT/runs"
if [ ! -f "$OUT/build.ninja" ]; then
  (cd "$OUT"; "$SRC/configure" --target-list=aarch64-softmmu --enable-tcg \
   --disable-kvm --enable-pvg --disable-docs --disable-gtk --disable-sdl \
   --disable-vnc --disable-werror --disable-tools --disable-user \
   --without-default-devices --with-devices-aarch64=reims-arm64-tcg \
   --enable-nettle --disable-gcrypt --disable-crypto-afalg) \
   > "$ROOT/runs/qemu-bdif-usb-configure.log" 2>&1
elif ! grep -Eq '^#define CONFIG_NETTLE([[:space:]]+1)?$' "$OUT/config-host.h" || \
     grep -Eq '^#define CONFIG_(GCRYPT|AF_ALG)([[:space:]]+1)?$' "$OUT/config-host.h"; then
  "$OUT/pyvenv/bin/meson" setup --reconfigure --clearcache \
    -Dnettle=enabled -Dgcrypt=disabled -Dcrypto_afalg=disabled "$OUT" "$SRC" \
    > "$ROOT/runs/qemu-bdif-usb-configure.log" 2>&1
fi
ninja -C "$OUT" -j 10 qemu-system-aarch64 > "$ROOT/runs/qemu-bdif-usb-build.log" 2>&1
"$OUT/qemu-system-aarch64" --version | head -1
python3 - "$ROOT" "$OUT/qemu-system-aarch64" "$PIN" <<'PY'
import hashlib,json,re,subprocess,sys
from pathlib import Path
root,binary,pin=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
source=json.loads((root/'runs/patch-reconstruction-qemu-bdif-usb.json').read_text())
if len(source)!=1 or source[0]['base_commit']!=pin or not source[0]['passed']:
 raise SystemExit('Missing pinned source reconstruction evidence')
patches=source[0]['patches']
if 'patches/qemu-platform/0013-virtio-qmp-backend-features.patch' not in patches:
 raise SystemExit('VirtIO QMP backend-features fix is not in the build chain')
if 'patches/qemu-platform/0014-vmapple-virtio-capability-order.patch' not in patches:
 raise SystemExit('VMApple capability-order fix is not in the build chain')
if 'patches/qemu-platform/0015-vmapple-virtio-usb-transport.patch' not in patches:
 raise SystemExit('VMApple VirtIO USB transport is not in the build chain')
if 'patches/qemu-platform/0016-vmapple-ctrr-shared-romd-ram.patch' not in patches:
 raise SystemExit('VMApple CTRR shared-ROMD RAM fix is not in the build chain')
if 'patches/qemu-platform/0017-bdif-backend-capacity.patch' not in patches:
 raise SystemExit('BDIF backend capacity fix is not in the build chain')
if 'patches/qemu-platform/0018-pauth-packed-mult.patch' not in patches:
 raise SystemExit('Packed PAuth matrix multiplication is not in the build chain')
if 'patches/qemu-platform/0019-opt-in-ascending-pci-capabilities.patch' not in patches:
 raise SystemExit('Opt-in PCI capability ordering is not in the build chain')
if 'patches/qemu-platform/0020-pauth-packed-qarma5-sbox.patch' not in patches:
 raise SystemExit('Packed QARMA5 substitutions are not in the build chain')
if 'patches/qemu-platform/0021-pauth-grouped-permutations.patch' not in patches:
 raise SystemExit('Grouped PAuth permutations are not in the build chain')
if 'patches/qemu-platform/0022-pauth-per-vcpu-memo.patch' not in patches:
 raise SystemExit('Opt-in per-vCPU PAuth memoization is not in the build chain')
if 'patches/qemu-platform/0023-virtio-snd-pcm-boundary-trace.patch' not in patches:
 raise SystemExit('Opt-in VirtIO sound PCM boundary trace is not in the build chain')
if 'patches/qemu-platform/0024-pauth-memo-capacity-stats.patch' not in patches:
 raise SystemExit('Opt-in PAuth memo capacity/statistics is not in the build chain')
if 'patches/qemu-platform/0025-pauth-va-projection.patch' not in patches:
 raise SystemExit('Shared-rule PAuth VA projection is not in the build chain')
if 'patches/qemu-platform/0026-tb-lookup-state-pointer.patch' not in patches:
 raise SystemExit('Const-pointer TB lookup state is not in the build chain')
if 'patches/qemu-platform/0027-virtio-snd-empty-info.patch' not in patches:
 raise SystemExit('Valid empty VirtIO sound item queries are not in the build chain')
if 'patches/qemu-platform/0028-virtio-snd-queue-size.patch' not in patches:
 raise SystemExit('Configurable VirtIO sound queue capacity is not in the build chain')
if 'patches/qemu-platform/0029-pauth-memo-16384.patch' not in patches:
 raise SystemExit('Optional 16384-entry PAuth memo capacity is not in the build chain')
if 'patches/qemu-platform/0030-arm-timeout-diagnostic.patch' not in patches:
 raise SystemExit('Default-off guarded ARM timeout diagnostic is not in the build chain')
if 'patches/qemu-platform/0031-arm-timeout-runtime-slide.patch' not in patches:
 raise SystemExit('Runtime-PC timeout image validation is not in the build chain')
if 'patches/qemu-platform/0032-virtio-net-control-queue-size.patch' not in patches:
 raise SystemExit('Configurable VirtIO net control capacity is not in the build chain')
if 'patches/qemu-platform/0033-tcg-sync-source-counters.patch' not in patches:
 raise SystemExit('Default-off TCG sync source counters are not in the build chain')
for opt_patch in (
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
 'patches/qemu-platform/0045-arm-ttbr-selective-tlb-flush.patch',
):
 if opt_patch not in patches:
  raise SystemExit(f'Missing required sync optimization patch {opt_patch}')
with binary.open('rb') as stream:
 digest=hashlib.file_digest(stream,'sha256').hexdigest()
record={'base_commit':pin,'binary':str(binary.relative_to(root)),
 'binary_sha256':digest,'patches':patches,
 'patch_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in patches}}
config=binary.parent/'config-host.h'
config_text=config.read_text()
def defined(name):
 return re.search(r'^#define '+re.escape(name)+r'(?:[ \t]+1)?$',config_text,re.M) is not None
if not defined('CONFIG_NETTLE') or any(defined(name) for name in ('CONFIG_GCRYPT','CONFIG_AF_ALG')):
 raise SystemExit('Required Nettle cipher backend not selected; build record refused')
base_profile=root/'experiments/E01-build/reims-arm64-tcg.mak'
profile=binary.parent/'device-profile/reims-arm64-tcg.mak'
device_config=binary.parent/'aarch64-softmmu-config-devices.mak'
if 'CONFIG_VIRTIO_SND=y' not in device_config.read_text().splitlines():
 raise SystemExit('VirtIO sound was not enabled in the generated target configuration')
device_help=subprocess.check_output([str(binary),'-device','help'],text=True)
if re.search(r'^name "virtio-sound-pci"',device_help,re.M) is None:
 raise SystemExit('Built QEMU does not register the standard VirtIO sound PCI device')
record['cipher_backend']={'implementation':'nettle',
 'pkg_config_version':subprocess.check_output(['pkg-config','--modversion','nettle'],text=True).strip(),
 'config_header':str(config.relative_to(root)),
 'config_header_sha256':hashlib.sha256(config.read_bytes()).hexdigest(),
 'af_alg_enabled':False,'gcrypt_enabled':False}
record['device_profile']={'path':str(profile.relative_to(root)),
 'sha256':hashlib.sha256(profile.read_bytes()).hexdigest(),
 'base_profile':str(base_profile.relative_to(root)),
 'base_profile_sha256':hashlib.sha256(base_profile.read_bytes()).hexdigest(),
 'extra_config':['CONFIG_VIRTIO_SND=y'],
 'generated_device_config':str(device_config.relative_to(root)),
 'generated_device_config_sha256':hashlib.sha256(device_config.read_bytes()).hexdigest(),
 'virtio_sound_registered':True,
 'scope':'Compiled device availability only; sound remains an explicit runtime option.'}
reims_library=root/'build/reims-arm-target/dist-vulkan/lib/libreims_vgpu.a'
reims_source=root/'runs/patch-reconstruction-reims-arm-guest.json'
if not reims_source.is_file():
 raise SystemExit('Missing Reims source reconstruction evidence')
reims_evidence=json.loads(reims_source.read_text())
if len(reims_evidence)!=1 or not reims_evidence[0]['passed']:
 raise SystemExit('Invalid Reims source reconstruction evidence')
record['reims_library']={'path':str(reims_library.relative_to(root)),
 'sha256':hashlib.sha256(reims_library.read_bytes()).hexdigest(),
 'source_reconstruction':str(reims_source.relative_to(root)),
 'patches':reims_evidence[0]['patches'],
 'patch_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                 for name in reims_evidence[0]['patches']}}
compiler_source=root/'runs/patch-reconstruction-metal2vulkan-research.json'
compiler_evidence=json.loads(compiler_source.read_text())
if len(compiler_evidence)!=1 or not compiler_evidence[0]['passed']:
 raise SystemExit('Invalid metal2vulkan source reconstruction evidence')
compiler=compiler_evidence[0]
record['reims_library']['metal2vulkan']={'base_commit':compiler['base_commit'],
 'source_reconstruction':str(compiler_source.relative_to(root)),
 'patches':compiler['patches'],
 'patch_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                 for name in compiler['patches']}}
(root/'runs/qemu-bdif-usb-build.json').write_text(json.dumps(record,indent=2)+'\n')
PY
