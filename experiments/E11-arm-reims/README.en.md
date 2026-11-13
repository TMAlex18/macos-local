> **Language:** **English** | [简体中文](README.md)

# E11 — Modern Reims, ARM VMApple and x86_64 Linux TCG

Goals: build and validate modern Reims apple-gfx-mmio/IOSurface devices in
the same QEMU process that executes our self-authored ARM64 firmware on
an x86_64 Linux host.

The standalone firmware runners in this section do not boot Apple firmware,
XNU or a macOS GUI. A self-authored ARM64
guest now submits compute and offscreen triangle workloads, including a real
IOSurface descriptor/mapping path and native-window frame updates, through Reims to
the hardware Vulkan GPU and verifies the returned data. This does not establish macOS
Metal or WindowServer compatibility.

## Source and build separation

- Modern QEMU worktree: sources/qemu-reims-arm; pinned to 1a72df9849;
  patches: patches/qemu-platform/0003-vmapple-tcg-pac-model-stack.patch and
  0004-research-avp-rtc-ctrr-mmio.patch (the build script's fixed chain).
- Modern Reims ARM-guest worktree: sources/reims-arm-guest; same upstream
  superproject pin as the original Reims worktree, plus:
    0001 Cargo dependency source pin,
    0002 ARM64 guest APV page-size feature,
    0003 IOSurface guest-page/host-page separation.
    0004 fixture host/guest boundaries, 0005 isolated-task host alignment,
    0006 shareable temporary backing for detached heaps on Linux,
    0007 pinned framebuffer-fetch compiler overlay and GPU consumer checks.
- Test binary A: build/qemu-reims-arm/qemu-system-aarch64
  (default Reims Linux x86 guest 4KiB pages, for the negative comparison).
- Test binary B: build/qemu-reims-arm16k/qemu-system-aarch64
  (Linux x86 host, ARM64 guest APV pages 16KiB).

## Build and run

From project root:

    make build-reims-arm64-vulkan
    make build-reims-qemu-arm16k
    make reims-arm16k-smoke

Test harness parts:

- run-machine.py: paused QMP, VMApple + gfx and IOSurface MMIO.
- run-firmware.py: VMApple config, UART, 2-vCPU PSCI with vGPU initialized.
- run-mmio.py: real ARM CPU reads Reims MMIO.
- run-registers.py: real ARM CPU writes/reads Reims version register.
- run-root-page.py: differential page-address decoding, 4KiB vs 16KiB.
- run-fifo.py: differential root-FIFO enable/disable, 4KiB vs 16KiB.
- run-device-info.py: Nop/GetDeviceInfo roundtrip with no-doorbell control.
- run-gpu.py: real hardware Vulkan compute/draw/present, guest-side checks,
  and explicit VM-local USB HID input/interrupt probes.
- Self-authored source: probe-mmio.S, probe-registers.S, probe-root-page.S,
  probe-fifo.S, probe-compute.c, probe-draw.c; the two GPU fixtures share
  guest-common.h, guest-start.S and guest.ld. No Apple binary artifacts included.

All logs in runs/, generated images/binaries in build/, ignored by Git.
Do not claim the 16KiB guest profile is production-valid until the complete
Apple-specific GPU protocol and IOSurface lifecycle work is checked.

## Distinct contract categories

- Guest APV/FIFO/IOSurface *page numbering* is 16KiB for an ARM64 guest.
- Host allocation/mmap granule remains the host OS page size (4KiB here).
- Platform-specific CPU register operations remain tied to host architecture.
- Reims 16KiB version is a *per-build* feature, not a runtime negotiation.

The original modern QEMU has HVF-only MAC guest hooks; these were guarded
for Linux TCG but not replaced with accurate Apple HVC/PAuth semantics.

See docs/reports/M1-ARM-REIMS.en.md for test outcomes and unresolved suite failures.


## v3 Command Transport and Alignment Fixes

- Patch 0004 persists guest/host-aware test fixtures; patch 0005 separates
  host-pointer alignment from guest range granularity for isolated tasks.
- `make reims-core-serial`: ARM16K and x86 Core regression suites.
- `make reims-fifo-roundtrip`: real guest Nop/GetDeviceInfo command and
  completion/result memory checks, plus the no-doorbell negative control.
- This is command transport and device information, not rendering.

See docs/reports/V3-ALIGNMENT-AND-PROGRESS.en.md and current docs/STATUS.en.md.

## ARM Guest → Hardware GPU → Guest Compute Round-Trip

Run from the project root:

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload compute --gpu nvidia
```

This depends on the pinned ARM16KiB build above plus `clang`/`ld.lld`/`llvm-objcopy`,
`vulkaninfo`, and a hardware Vulkan ICD. The runner permits selecting only a
single NVIDIA or Intel ICD, disables external Vulkan layers, and requires the
resulting device inventory in that environment to expose a single
integrated/discrete GPU; CPU/`llvmpipe` devices are rejected. Each QEMU case
must also capture a matching GPU name or driver ID + device ID in live runtime
stderr, or the entire run fails. `--gpu intel` selects Intel; both hardware
backends have been verified live. Each of the two cases enforces a 90-second
host ceiling and a 30-second in-guest timer deadline per command.

Inside the bare-metal guest, `probe-compute.c` constructs 16KiB page tables,
shared input/output buffers, a shader function, and a compute pipeline using
normal `DefineTask2 (56)`, `SetObjectList (51)`, `ExecIndirect3 (67)`, and
finally `DeleteTask (32)`. The shader directly references pinned Reims's
self-authored compute_add.metal
and its prebuilt metallib, with no dependency on Apple guest shaders or a new
compiler toolchain.

The guest generates 256 `uint32` inputs `((i * 1664525) ^ 1013904223)`, pre-fills
the output buffer with `0xdeadbeef`, and dispatches 256 threads of `add_one` at
64 threads per threadgroup. Once the completion stamp arrives, the guest
independently computes each expected `input + 1` value (32-bit wraparound) and
verifies that the entire input page is unchanged, the remaining 15KiB of output
sentinels are unchanged, the FIFO has been consumed, and the fault register is
zero. The host loads only the self-authored firmware and shader; it neither
writes expected outputs nor reads results back to grade on the guest's behalf.
Resource synchronization flags are `invalidateHost | synchronize`, so the
completion stamp covers result writeback.

The `no-exec` negative control retains the identical resources and inputs, skips
`ExecIndirect3`, and instead submits an ordinary `Nop` and waits for its stamp;
the guest must read back the untouched `0xdeadbeef` sentinel and must not print
`compute PASS`. This verifies that a FIFO round-trip alone cannot masquerade as
GPU output.

On 2026-10-23, both positive and negative-control cases passed on both hardware
backends: RTX 5070 Ti Laptop GPU (`10de:2f18`, discrete, driver `595.91.07`) and
Intel(R) Graphics (ARL) (`8086:7d67`, integrated, Mesa `26.0.8-1ubuntu0.3`).
Each single-ICD device inventory matched the live QEMU stderr driver/device ID
and name; positive first/last outputs were `3c6ef360` / `252255ad`, and
negative-control outputs were `deadbeef`. The guest checks every element, not
just those two sample words. The 16KiB guest / 4KiB host contracts remain
unchanged, with no modifications to Reims/QEMU library implementations or
dependencies.

Reproducibility entry points and retained evidence:

- NVIDIA structured results
  and
  Intel structured results:
  hardware isolation, binary/shader/firmware digests, per-case outcomes, live
  GPU selection evidence, and guest UART logs.
- Each run creates a fresh `runs/e11-arm-reims-compute/<gpu>-<UTC>/` directory
  saving `vulkan-device.log`, `positive.log`, `no-exec.log`, and `results.json`
  without overwriting prior evidence.
- `build/e11-compute/` holds the self-authored ELF/bin artifacts; the two
  temporary stub disks are cleaned up at the end of each case.

This is a bounded integer-increment compute smoke test; the standalone draw path
follows below. Neither covers IOSurface presentation, multi-queue workloads, or
the full macOS Metal protocol. The Reims Vulkan translation layer and
`metal2vulkan` retain their own documented precision and performance boundaries.

### 20MiB Detached Heap: Ordinary Construction Failure and Fix

On 2026-10-31, normal OS cold-boot Run 56 aborted while creating a 20MiB heap in
`newHeapWithDescriptor:` with `a remap ... 0x1400000 bytes (kern_return 1)`.
Core `resources/heap/detach.rs` first allocates temporary zero pages, creates
the heap over those address ranges, immediately detaches them, and swaps in
guest pages only on the first `prepare`. On Linux, `allocate_anywhere` provides
only an anonymous address reservation that is absent from the file-backing
table, leaving `backing::gather` unable to establish a shared alias via
`remap_shared_overwrite`. The immediately preceding `RGBA16Unorm` texture
dimension-mismatch warning was not a precondition for this crash: the ordinary
heap consumer below reproduces the exact same fatal error without creating any
textures. Run 56's `apport` logged `core limit 0` and ignored the unpackaged
binary, so no core dump was retained for post-mortem inspection.

The fix is pinned in `patches/reims/0006-detached-heap-aliasable-backing.patch`:
`HostVm` adds `allocate_aliasable` for callers with an explicit shared-alias
requirement, defaulting on Mach to the existing allocator while reusing the
existing `memfd` allocator on Linux, with the Vulkan wrapper preserving
mapping-change notifications. Only the temporary backing for detach-heaps uses
this entry point; ordinary reservations, remap failure semantics, heap
detach/replace behavior, and keep-alive lifetimes remain unchanged. No guest
data is copied, no errors are ignored, no texture formats are substituted, and
no test oracles are relaxed.

```sh
bash tools/build-reims-arm64-vulkan.sh
bash tools/build-qemu-bdif-usb.sh
python3 experiments/E11-arm-reims/run-gpu.py --workload compute --heap --platform restore-research --gpu nvidia --irq
```

`--heap` reuses the same self-authored `compute_add` shader and runner,
creating a type-13 20MiB heap and a type-14 buffer aliasing it. Physical
addresses across the 1280 guest 16KiB pages are reversed within each adjacent
pair to verify that non-contiguous guest pages are genuinely gathered into a
GPU-usable alias; a separate input buffer also spans 20MiB. The GPU computes
5242880 `uint32` elements at 256 threads per group, and the guest walks its
virtual-to-physical page mapping word-by-word to verify that every output equals
`input + 1`, the entire input buffer is untouched, and both trailing 16KiB guard
pages after the heap and input buffers remain intact. Real stamp 3 IRQ 49, zero
faults, and `DeleteTask` completion are all required to pass; resource creation
or FIFO completion alone is insufficient.

Before/after comparison using the identical guest firmware SHA-256
`728e3d1410c9fd376c71119e2fe3d67a0e704db30a490f798b8fe108b3ab7ef7`:

- Pre-fix 6731911f result:
  initialization IRQs 1/2 completed, then the first GPU exec aborted with the
  same 20MiB remap fatal error while creating the heap (QEMU exit `-6`).
- Post-fix f5dfbf2d result:
  all 20MiB of output, 20MiB of input, 32KiB of guards, and real IRQ 49 passed
  with clean exit `0`.
- RGhA regression on the same new binary:
  raw `RGBA16Float` pixels/guards, GPU/display IRQs, and the red-on-blue image
  captured from the owned native window all passed.

Host-layer verification covers
11 Linux VM tests,
12 backing tests,
7 remap tests, and
33 Core heap tests. Newly added real
`memfd` 20MiB tests check bidirectional shared writes, alias survival after
dropping source and intermediate views, out-of-bounds / interior-hole rejection
and cleanup, while the existing second-segment `mmap` failure test continues to
verify rollback of earlier segments and an unchanged backing table. Both
self-authored QEMU instances exited cleanly with temporary stub disks removed.
Scope was not expanded here to the full workspace or multi-GPU matrix.

Exact patch reconstruction
and the QEMU build record record source
provenance and linked-library digests. For this build, the QEMU SHA-256 was
`f5dfbf2d299d88b9df390c1414e573a4e5f749d55fe0bf36b760f9288b633f2d` and the
static Reims library SHA-256 was
`1f49fdb5f786d39cb96c0122606888a96fcb29be29beed47195c2b96c1a82464`.
This resolves the reproduced heap-construction abort while preserving Run 56's
original artifacts and failure record. Mainline Run 57 subsequently obtained
legible LanguageChooser rendering and localized pointer feedback
independently; that constitutes initial GUI validation rather than a full
desktop, and absence of a crash alone is not treated as proof that the original
20MiB heap path was exercised.

### RGBA16Float Framebuffer Fetch and Source-Over Blending

After advancing past the language selection page, normal OS initial-setup Run 58
aborted during render pipeline creation with
`FramebufferFetch / NotImplemented / Color(0)`. In pinned dependency
`ecb80e019d54b8eb1585974fb2a6cc37bac359cc`, NVIDIA ordered framebuffer fetch uses
pixel interlock / storage images; the upstream implementation accepted only
replace-mode blending on ordinary half-float attachments, or `no-fast-math`
`half4` blending under a specific `Exact` policy. The ordinary path requiring
premultiplied source-over blending after fetching was missing, and the preceding
`shader-side-depth-test` warning was not a condition for that rejection.

Pre-fix rejection in the self-authored fixture
reproduced the failure under the identical pipeline layout: `colour0`
`RGBA16Float` (115) with RGB and alpha both set to `One`/`OneMinusSourceAlpha`/`Add`
and write mask `0xf`, `colour1` `RG16Float` (65) and `colour2` `RGBA16Float`
(115) both with mask `0`, depth/stencil 260, and no vertex descriptor. The
fixture uses the repository's existing `fetch_half_fragment` and
`fetch_float_fragment` metallibs; its final executable form reuses E11's
existing buffer-driven vertex shader to avoid strict `FunctionStorageAssumed`
rejection from an unrelated test vertex shader. No Apple shaders were extracted
or committed.

`patches/metal2vulkan/0001-half-float-fetch-source-over.patch` reads the
destination inside the same ordered interlock, computes
`source + destination * (1 - source.a)`, and writes the blended value back.
Existing depth/stencil, coverage, and write-mask guards remain in place;
non-fetch optional half-precision paths keep their prior behavior without
disabling blending or downgrading reads to unordered. The existing `Exact` half
model remains opt-in, default float arithmetic preserves source/conversion
precision notes, and the shader cache semantic version bumps `1691→1692`. The
dependency commit pin itself is unchanged: `tools/prepare-metal2vulkan-research.sh`
verifies the identical pin and applies the patch, and Reims patch 0007 consumes
it via a Cargo `[patch]` overlay without mutating shared Cargo download caches.

Live NVIDIA release consumer tests execute through Reims's serialized
descriptors, render encoder, and GPU blit readback. Every positive case verifies
all 16×16 pixels plus both unused color attachments: two draws within the same
render pass must observe the first draw's written value; depth rejection, first
depth write blocking the second draw, stencil rejection, and first stencil
increment blocking the second draw each carry an independent oracle. For
example, an initial pixel `(0.25,0.5,0.75,0.5)` after two fetch/blend draws
becomes `(0.37109375,0.7421875,1.11328125,0.7421875)`, and every raw binary16
byte must match exactly.

| Self-authored fragment | Default policy | Explicit `REIMS_VGPU_EXACT=half_blend` |
| --- | --- | --- |
| fast-math half4 | 5 GPU positive cases pass | 5 GPU positive cases pass |
| fast-math float4 | 5 GPU positive cases pass | 5 GPU positive cases pass |
| no-fast-math float4 | 5 GPU positive cases pass | 5 GPU positive cases pass |
| no-fast-math half4 | Retains original strict `HalfOutputPrecision` rejection without submitting that command buffer | 5 GPU positive cases pass |

The default release log and
Exact release log record every
row and active policy. Total process wall times across the two batches were
4.68s and 1.48s with peak RSS of 363856KiB and 360304KiB, and the slowest single
pipeline creation took 0.666s. Those numbers reflect bounded execution in a
warm-cache environment rather than cold-start performance benchmarks. Exact
binary16 agreement on the chosen test inputs does not prove bit-exactness
against Metal across arbitrary inputs, fast-math contractions, or rounding
modes; the fragments tested here have no external memory side effects, and GPU
coverage was not expanded to exported depth/stencil or shader-side stand-in
combinations. If a stock caller uses `no-fast-math` `half4`, it must explicitly
opt into the existing `half_blend` `Exact` policy; neither firmware, guest code,
nor mainline runtime defaults were modified to enable it implicitly.

Reproduce from the project root (the second command explicitly selects `Exact`):

```sh
e11_fetch_test=fetched_half_float_source_over_keeps_order_tests_and_unused_attachments
env -u REIMS_VGPU_EXACT CARGO_TARGET_DIR="$PWD/build/reims-arm-target" VK_DRIVER_FILES=/usr/share/vulkan/icd.d/nvidia_icd.json VK_LOADER_LAYERS_DISABLE='*' REIMS_VGPU_WINDOW=0 cargo +1.98.1 test --manifest-path sources/reims-arm-guest/Cargo.toml --release --locked -p reims-vgpu-vulkan --features reims-vgpu-core/arm64-guest-pages --lib "$e11_fetch_test" -- --test-threads=1 --nocapture
env REIMS_VGPU_EXACT=half_blend CARGO_TARGET_DIR="$PWD/build/reims-arm-target" VK_DRIVER_FILES=/usr/share/vulkan/icd.d/nvidia_icd.json VK_LOADER_LAYERS_DISABLE='*' REIMS_VGPU_WINDOW=0 cargo +1.98.1 test --manifest-path sources/reims-arm-guest/Cargo.toml --release --locked -p reims-vgpu-vulkan --features reims-vgpu-core/arm64-guest-pages --lib "$e11_fetch_test" -- --test-threads=1 --nocapture
```

10 storage-guard tests,
real AIR compilation and spirv-val on the new pipelines,
existing ordered-fetch compile tests,
and
existing half-precision / Exact regressions
all passed, as did `clippy --lib -- -D warnings` on the compiler crate. Scope
was not expanded to the full workspace or multi-GPU matrix.

The production build integrates both the
dependency patch reconstruction record
and the
Reims patch reconstruction record.
Including existing `virtio-net` patch 0018, the new canonical QEMU SHA-256 is
`0eb337028d1ef95e3de168584739d827a1abd5725bd5a0516b24fc7028fcf04e` and the
static Reims library SHA-256 is
`44d3fe9ef21ec0df8658b5488754bf3da9bc5460f41a9114bff6f396026eab53`, with full
provenance recorded in the build record.
On that same binary, both the
20MiB heap regression
and the
RGhA native-window regression
passed with `Exact` explicitly unset and recorded as `null` in the results.
Self-authored QEMU instances exited cleanly and temporary disks were removed.
Run 58's original abort evidence is retained; follow-up stock OS Run 59 and the
live setup flow are validated independently on the main line.

## ARM Guest Offscreen Triangle and Pixel Readback

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload draw --gpu nvidia
python3 experiments/E11-arm-reims/run-gpu.py --workload draw --gpu intel
```

This shares the pinned binary, hardware ICD isolation, per-QEMU-process device
identity verification, 16KiB guest / 4KiB host contracts, timeouts, and
temporary-disk cleanup with `compute`. Each run creates a fresh
`runs/e11-arm-reims-draw/<gpu>-<UTC>/` directory; the legacy compute entry point
is unified under `run-gpu.py --workload compute` (which remains the default
workload).

`probe-draw.c` uses the pinned suite's
render_vbuf.metal,
render_color.metal,
prebuilt metallibs, and `basic` pipeline descriptor layout from `render_lib.py`.
The guest constructs its own page tables, shared buffer, RGBA8 texture view,
both shader functions, render pipeline, and vertex/color resources, submitting
`DrawPrimitives16 (0x01)` on a render encoder via normal `ExecIndirect3`. Using
a 64×64 single-sample offscreen target configured for clear-blue / store, the
GPU rasterizes a red triangle and synchronizes the result back into the guest
buffer.

Guest vertex clip coordinates are `(-.75,-.75),(.75,-.75),(0,.75)`, mapping to
pixel-space `(8,56),(56,56),(32,8)`. Evaluating three integer half-plane
predicates at pixel centers across all 4096 pixels, the guest requires
`y<56 && 2*x+y>=71 && 2*x-y<=55` to be red and every other pixel to be blue.
Because no pixel center lies on a triangle edge, the check is insensitive to
tie-breaking rules; color channels are exact `0` or `1`, so RGBA8 verification
needs zero rounding tolerance. The guest also verifies that the trailing 16KiB
guard page after the render target is unchanged, the FIFO is consumed, and fault
status is zero.

The `no-draw` negative control omits only the draw command while preserving the
identical render pass, pipeline, clear color, synchronization, and completion
path; the guest must verify that all 4096 pixels are blue. Observing 1152 red /
2944 blue in the positive case versus 4096 blue in the control demonstrates that
rasterization acts independently of both the pass clear and prior compute runs.
The host neither writes expected images nor grades on the guest's behalf; the
runner merely saves `.rgba` and `.png` previews from the guest's bounded UART
RGBA dump and cross-checks framing and pixel counts.

On 2026-10-23, both positive and control cases passed on NVIDIA and Intel with
per-case live GPU identity evidence saved alongside the results. Generated PNGs
were visually confirmed as a red triangle on a blue background, derived directly
from real guest readback:

- NVIDIA results
  and
  rendered image.
- Intel results
  and
  rendered image.
- `build/e11-draw/` retains reproducible self-authored ELF/bin binaries; each
  result directory preserves full UART logs, hardware inventory,
  shader/binary/firmware digests, and raw pixels from both cases.

Validated scope covers ordinary offscreen drawing, GPU completion, and the guest
pixel oracle; this is not a macOS GUI and does not cover window presentation,
complex blending or texture sampling, or the complete Metal protocol.

## GPU Completion IRQ Mode

Polling remains the default so that the baseline evidence above stays unchanged.
Passing `--irq` runs a positive workload case in which completion is determined
by the guest's real EL1 interrupt handler before executing the same numerical or
pixel oracle:

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload compute --gpu nvidia --irq
python3 experiments/E11-arm-reims/run-gpu.py --workload compute --gpu nvidia --irq --disable-work-irq
python3 experiments/E11-arm-reims/run-gpu.py --workload draw --gpu nvidia --irq
python3 experiments/E11-arm-reims/run-gpu.py --workload compute --gpu intel --irq
```

`guest-irq.h` and the conditionally compiled EL1 vector table reuse the GICv3
Group1/SRE/PMR/EOImode0 setup validated in E06. Because Reims signals QEMU via a
BH pulse wired to SPI INTID 49, the interrupt is configured as edge-triggered.
The ISR reads `ICC_IAR1_EL1=49`, reads-to-clear the stamp bitmap at
`0x30201018`, captures the memory stamp at `0x70004000` along with zero fault
status, and then writes EOIR. Note that the root FIFO always maps to bitmap bit
0—stamp value 3 does not correspond to bit 3. Offscreen workloads do not read
the display's read-to-clear bitmap, nor do they treat the fault register as an
ACK.

Only a single command is kept in flight at a time, and initialization IRQs for
stamps 1 and 2 must be consumed first; stamp 3 for the real GPU workload must be
captured within a single ISR invocation observing
`INTID49 + bit0 + memory stamp3 + fault0`. Wait loops inspect only ISR-latched
state rather than polling the GPU memory stamp, bounding wait time via `CNTVCT`
without issuing `WFI`. Hardware notification coalescing is permitted rather than
assuming a fixed IRQ count per command. Structured output records
`completion_mode`, `irq_capture`, and `gpu_work_irq_verified` instead of merely
checking for a non-zero total IRQ count.

`--disable-work-irq` disables GIC SPI 49 only after initialization IRQs have
already been consumed. GPU work is submitted and finishes normally; after two
seconds the guest verifies memory stamp 3 and zero faults, runs the full
numerical oracle, and must exit **1** due to the missing work ISR. The runner
recognizes this specific guest exit as a passing negative control; host
timeouts, data mismatches, or unconsumed initialization IRQs all fail. Post-check
resource deletion is allowed to poll to completion without retroactively
satisfying the missing work IRQ.

Live coverage on 2026-10-23:

- NVIDIA compute IRQ:
  stamp 3 ISR plus all 256 outputs and guards passed.
- NVIDIA disabled-SPI control:
  initialization IRQs passed, GPU outputs matched, work ISR was absent, and the
  guest exited 1 as required.
- NVIDIA draw IRQ:
  stamp 3 ISR, all 4096 pixels, and guards passed, retaining real readback
  PNG/RGBA files.
- Intel compute IRQ:
  passed the identical notification and numerical contract.

Adding the IRQ path required zero changes to QEMU or Reims, and recompiling a
representative default-poll compute payload produced bytes identical to the
previously verified firmware. IRQ mode remains a bounded single-FIFO,
single-in-flight smoke test and does not cover interrupt coalescing under load,
display interrupts, or multi-queue scheduling.

## Graphics Coexistence on the Current Restore-Research Platform

`--platform restore-research` runs against the signed-restore binary
`build/qemu-bdif-usb/qemu-system-aarch64` configured with 4GiB RAM, two vCPUs,
exact 24MHz timing, QARMA5, and explicit PAC/CTRR models. It still executes this
directory's self-authored EL1 payloads on CPU0 alone with temporary 4MiB AUX and
32MiB root stub disks—without Apple payloads, TSS requests, restore transport,
or system-disk workflows. Having the protection-model devices instantiated on
the board does not mean these graphics payloads exercise their key or protection
semantics.

```sh
make reims-gpu-restore-platform
```

NVIDIA
compute IRQ
and
draw IRQ
each passed the same stamp 3 ISR contract and full numerical/pixel checks using
the exact binary digest from the current restore build. This demonstrates that
the platform build with restore devices wired in coexists cleanly with the
small-scale graphics path above, without proving stock Apple graphics driver
operation or end-to-end restore. The default `--platform graphics` keeps the
earlier 512MiB / single-vCPU graphics baseline.

Both stock Apple DeviceTrees and captured live DTs declare GIC as the interrupt
controller with interrupt cells `[49,0]` for `gfx` and `[48,0]` for `IOSurface`,
matching QEMU's SPI routing numbers. Public IOKit constants treat mode `0` as
edge-compatible; generic DT parsing merely forwards the specifier and does not
by itself prove what `ICFGR` value the matching `AppleGIC` binary writes at
runtime. There is currently no evidence of a level-vs-pulse conflict between
Apple's configuration and QEMU's BH pulse; this self-authored IRQ fixture
explicitly configures edge triggering.

## IOSurface Descriptor → GPU → Guest and Resource Reuse

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload iosurface-draw --platform restore-research --gpu nvidia --irq
```

This workload reuses the red triangle and independent pixel oracle, switching
the backing resource path to the real `IOSurface` host device while keeping the
shared-buffer texture-view path in `draw` separate. Over MMIO at `0x30211000`,
`guest-iosurface.h` sets up a 16KiB command ring, descriptor table, v2
descriptor, and backing page table. Every table, ring, and PFN is indexed in
**guest 16KiB** pages while host allocation/`mmap` stays **4KiB**. Registers use
QEMU's existing low-32-bit address ABI, and all guest addresses reside within
that range.

The surface is a non-planar single-image BGRA buffer measuring 64×64 at 256
bytes per row; setting descriptor `plane_count=0` selects the base dimensions,
and the texture binds plane 0. Backing memory spans 32KiB: the first 16KiB hold
pixels and the trailing 16KiB serve as a guard page. After ordinary Map command
1, the guest must observe ring status 0, an incremented read counter, and **host
writeback of the descriptor `mapped` bit**—which together with the rendered
pixels proves that the descriptor was genuinely consumed.

The task creates a **type-11 mapper-ref texture** referencing mapping ID 1 via
ordinary IOSurface texture operation 12 and a BGRA8 render pipeline, without
creating a shared-buffer texture view or invoking `CMD_TEST` (which fills a CPU
test pattern). Once ordinary `ExecIndirect3` / `DrawPrimitives16` completes, the
guest checks the entire backing: all 4096 pixels still split into 1152 red /
2944 blue, the guard page is intact, and the real ISR for GPU stamp 3 is
verified.

Within the same session, the guest then exercises the full resource lifecycle:

1. While the mapper texture still holds the surface, Unmap command 2 must return
   `Busy (e00002d5)` with the `mapped` bit remaining `1`.
2. After deleting the mapper texture via `DeleteResource (37)`, Unmap must
   return `0` and clear the `mapped` bit.
3. The guest fills the retired backing with a distinct poison sentinel,
   re-points mapping ID 1 to a second guest backing allocation, re-Maps it, and
   draws again. Both the second 4096-pixel/guard check and the GPU stamp 5 ISR
   must pass while all 32KiB of the retired first backing remain filled with the
   poison sentinel.
4. Finally, the guest deletes the second texture, Unmaps cleanly to `0` clearing
   `mapped`, deletes the task, and disables the ring and descriptor table.

On 2026-10-23, this passed on both NVIDIA and Intel using the current
`restore-research` binary (4GiB RAM, 2 configured CPUs, 24MHz, executing the
self-authored guest on CPU0 only, with no Apple payloads or TSS and zero changes
to QEMU, Reims, or dependencies). IOSurface ring completions are observed via
bounded read-counter/status checks; the completion IRQ verified here is GPU SPI
49 without adding new IOSurface SPI 48 coverage:

- Live results and all five ring commands.
- Intel IOSurface resource / release-and-reuse results.
- Rendered image on first backing
  and
  rendered image on second backing after release/remap.

Raw BGRA bytes from each pass are saved over guest UART as `.bgra`; PNG previews
apply only a BGRA→RGBA channel swizzle to actual readback bytes rather than
synthesizing reference images. Structured results retain per-pass image digests,
`mapped`-bit writeback, `Busy` refusal, and release/reuse evidence alongside
explicit raw/preview format metadata; poison preservation on the retired first
backing is verified after the second GPU completion and pixel check finish. Full
UART logs stay in the same unique run directory while temporary stub disks are
removed as usual, and a representative shared-buffer `draw` IRQ regression on
the same platform also passed. This closes the small-scale IOSurface resource
loop without covering scanout, window presentation, multi-planar surfaces,
cross-process sharing, or the complete macOS Metal/IOSurface protocol.

## Windowed Present Entry Point for the Self-Authored Guest

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload present --platform restore-research --gpu nvidia --irq
```

This entry point opens an ordinary resizable 320×240 Reims window, holds it for
roughly 5 seconds, and closes it automatically; actual client geometry is
recorded from live capture. QEMU itself continues to run with `-display none`,
setting `REIMS_VGPU_WINDOW=1`, `SDL_VIDEO_DRIVER=x11` (the SDL3 driver name),
and statistics/info logging solely on the child process without altering host
desktop display settings.

The guest first completes the same IOSurface GPU draw, real stamp 3 IRQ, and
full source-pixel verification, then defines kernel task 0 and performs the
`connect`/`expectedAck` handshake over a separate 16KiB display shared page.
Ordinary display opcode 8 presents mapping ID 1 while keeping the surface and
backing alive until guest exit. Passing requires `presentCount` in the shared
page to increment by exactly 1, `presentedMappingID` to equal 1, and ISR 49 to
observe `present` bit 2 via both the separate display port bitmap and an atomic
fetch-and-clear of `raisedInterrupts`. Neither a FIFO stamp, zero fault status,
nor a bare display IRQ alone suffices to prove native window presentation.

Holding a live `Popen` handle with stdout/stderr redirected to temporary files
to prevent pipe stalls, the runner invokes `window_capture.py` while the process
is alive. Window presentation is accepted only when native pixel matching on the
owned QEMU window, SDL's live `x11` driver log, window frame counts, and window
GPU timing statistics all hold simultaneously. Guest-exported PNGs remain
source-pixel evidence and do not substitute for native window capture. Using
`_NET_WM_PID`, the capture helper restricts inspection strictly to that child
process's client window without capturing the full desktop.

On 2026-10-23, the first live `restore-research` / NVIDIA / IRQ window run
passed: guest `presentCount` advanced `0→1`, `presentedMappingID` read `1`, and
ISR 49 observed both port bit 0 and raised `present` bit 2. The owned X11 client
measured 320×200 with interior and exterior triangle regions matching in native
capture, and the same QEMU instance logged window frames and window GPU time
(2.0 frames/s and 0.11ms GPU time in the first reporting interval—evidence of
live windowed frame execution rather than a throughput or latency benchmark).
After holding the frame for its 5-second window, the guest exited cleanly and
the window closed:

- Complete structured results
  and
  owned native-window capture.

This establishes the self-authored guest's IOSurface → present → native-window
path; it does not establish a macOS GUI, physical display scanout, color
calibration, latency bounds, or display protocol behavior under load.
`window_capture.py` provides bounded independent observation of the owned client
window while keeping guest source pixels and protocol ACKs as distinct lines of
evidence.

### RGhA / RGBA16Float Single-Frame Contract

On 2026-10-31, targeting `pixel format 0x52476841` observed during normal macOS
cold boots, single-frame `present` gained opt-in `--surface-format rgba16float`
while retaining the default BGRA8 path. Reims's
`crates/reims-vgpu-core/src/display/guest_display/present.rs` explicitly maps
fourcc `RGhA` to `MTLPixelFormatRGBA16Float=115` at 8 bytes per pixel and
validates IOSurface bytes-per-element on mapped presentation. Although upstream
`interplay_scanout_rgha_rendered` already covers GPU clear/mapper/present in
isolation, it does not replace a combined ARM/QEMU, full source-pixel, real ISR,
and native-window check.

```sh
e11_qemu_sha=6731911f4c04d8c7c300661da334c44b2550a22c88c249bb635f610792831355
python3 experiments/E11-arm-reims/run-gpu.py --workload present --platform restore-research --gpu nvidia --irq --expect-binary-sha256 "$e11_qemu_sha"
python3 experiments/E11-arm-reims/run-gpu.py --workload present --platform restore-research --gpu nvidia --irq --surface-format rgba16float --expect-binary-sha256 "$e11_qemu_sha"
```

Both bounded cases passed sequentially on the canonical
`build/qemu-bdif-usb/qemu-system-aarch64` binary matching the SHA-256 above. The
half-float surface measures 64×64 with `rowbytes` 512 and a 32768-byte image
followed by a 16KiB guard; the v2 descriptor, 16KiB guest page table, type-11 /
op-12 mapper texture, and render pipeline format all switch to `RGhA` / `115`.
The CPU initializes only sentinels and submits the existing shader and triangle;
once GPU rendering finishes, the guest independently checks all 64 bits of every
one of the 4096 raw pixels against the triangle half-planes—1152 red and 2944
blue, requiring binary16 `0` and `1` channels to match `0x0000` and `0x3c00`
bit-for-bit with the entire guard page untouched. No reference image is
generated on the CPU.

Both cases verify Map status 0 / `mapped` writeback, real GPU stamp 3 INTID 49,
display `presentCount` `0→1` / mapping 1, and separate port bit 0 / `present`
bit 2, while host logs report 64×64 surfaces with fourccs `0x42475241` and
`0x52476841` respectively. Independent `XGetImage` captures of each QEMU-owned
320×200 native window matched the red-on-blue triangle geometry/region oracle
with window GPU execution statistics recorded. Both QEMU processes exited
cleanly and temporary stub disks were removed by the runner:

- Default BGRA control results.
- RGBA16Float results,
  raw half-float readback,
  and
  independent native-window capture.

`.rgba16f` stores the raw little-endian RGBA binary16 bytes; the PNG converts
that readback into a `unorm8` preview rather than serving as native-window
evidence. Structured results record source-side surface metadata, host display
mode, and raw byte digests. This closes the small-scale `RGhA` IOSurface → GPU
draw → display / native-window contract without proving macOS 1920×1080
multi-queue rendering, `WindowServer`/`LanguageChooser`, full GUI operation,
HDR, color management, or arbitrary half-float values; GPU and pixel-format
matrices were not expanded in this step.

The two-frame update entry point on a single window is:

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload present-update --platform restore-research --gpu nvidia --irq
```

Using mapping IDs 1 and 2 with two valid descriptors and independent page
tables/backings, the guest first presents a red triangle for ~5 seconds, then
renders a green triangle on the GPU and switches display presentation to ID 2.
Both rounds of full guest pixel/guard checks, GPU IRQs, and separate display
events must pass with `presentCount` advancing `0→1→2`, while the host
sequentially requires red and green native captures from the identical PID and
window ID, saved under separate directories—the green oracle rejects stale red
frames.

After deleting the first task texture, Unmap on ID 1 must still return `Busy`
while ID 1 remains held by active display scanout. Once presentation switches to
ID 2, the guest permits in-flight window GPU work to retain the old resource
briefly, performing bounded `Busy` retries until ordinary Unmap succeeds and
clears the `mapped` bit; it then poisons the retired ID 1 backing, holds the
green frame for 5 seconds, and verifies that the poisoned region was never
written. ID 2 stays live until guest exit without mutating ID 1's page tables
while in use and without introducing a third frame or extra ACKs.

On 2026-10-23, the single live `restore-research` / NVIDIA / IRQ two-frame run
passed, closing the window after clean guest exit. Both rounds of full source
pixel/guard checks and GPU work IRQs (stamps 3 and 9) passed, and the two
display events confirmed `count 0→1 / mapping 1` followed by `count 1→2 /
mapping 2`. Native red and green captures came from the identical QEMU PID and
window ID; during the green phase, 38 initial captures observed stale/unupdated
pixels before the live green frame matched, confirming that the prior red image
cannot satisfy the green oracle. Window GPU execution statistics were recorded
across both presentation intervals.

ID 1 returned `Busy` as required after deleting the task texture; after
switching to ID 2, the very first ordinary Unmap succeeded and cleared the
`mapped` bit without needing transient `Busy` retries on this run. The guest
then poisoned the retired backing and confirmed after holding the green frame
for 5 seconds that the retired region remained untouched. Separate ID 2 page
tables and descriptors stayed valid throughout without mutating the old mapping
tables or adding extra ACKs or a third frame:

- Two-frame complete results.
- Native red frame
  and
  same-window native green frame.

The single-frame entry point remains intact with its own standalone compile/link
check from the shared source, and pass 1 of the live two-frame run exercised the
shared connect/ack/present path as well. This validates bounded frame updates
and old-surface retirement rather than sustained rendering load, reclamation
stress, macOS GUI behavior, or the full `WindowServer` protocol.

## Window Input, QEMU Consumers, and Guest USB Reports

In the pinned source tree, ordinary keyboard and pointer paths are already wired
end-to-end: SDL window events invoke Reims callbacks into the event queue / BH
in `apple-gfx.c`, which forwards them into the QEMU input subsystem. Keyboard
events map USB HID usage page `0x07` codes onto QEMU Linux/`qcode` keys, and
pointer events scale into absolute guest coordinates according to the active
guest display mode. Keyboard delivery requires normal window focus, and pointer
delivery requires an active guest mode with cursor coordinates inside the image
region. When no guest input device handler is attached, QEMU's input layer drops
the events, so window responsiveness on the host never by itself proves guest
delivery.

The initial minimal build omitted xHCI/HID device models, and `-nodefaults`
prevents VMApple from auto-instantiating USB controllers or devices. Build
config `reims-arm64-tcg.mak` now enables `USB_XHCI_PCI`, `USB_HID`, and
`USB_TABLET_VMAPPLE`. At that point only the `graphics` binary was rebuilt
(digest
`fff712338f53852ee60b1bda5ddbaf44e53031e16d15340fb1ef5d57e15ebc2c`); earlier
`graphics` GPU records used
`cf6d4da1bc1d6890f94a59b59e1c53a0997ec62048f50281ed98f8e94363381c` while the
contemporary `bdif` binary was
`0273d0cccbb82554f6753cf54d70c5703e5627e52cc4fdbac8d64c4f49b08973`. Those
historical artifacts remain attributed to the exact binaries under which they
were recorded rather than retroactively claimed against newer builds.

Both research binaries were subsequently rebuilt with Nettle 3.10.2 explicitly
enabled for the ordinary user-supplied AES datapath, yielding `graphics` SHA-256
`d392abb3c29eae658ab256550ac81a435e2c6354d157a8a959cb3b3542010fce` and `bdif`
SHA-256
`b0123d45ebc127f6e1b46727c937174100ddd45b0d9adc17e2ce0e7c52ce91da`. A
headless NVIDIA compute IRQ smoke run
on the rebuilt `graphics` binary passed on RTX 5070 Ti Laptop GPU
(`0x10de:0x2f18`) with all 256 outputs, guard checks, and real INTID 49 / stamp
3 completion passing and guest exit `0`. That smoke check covers only the
headless `graphics` profile without rerunning the full window/input matrix on
the rebuilt binary. All prior workloads keep `-nodefaults` and their original
device sets; only the input workloads explicitly attach USB devices.

Headless device-level verification:

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload input --platform graphics --gpu nvidia
```

`probe-input.c` configures the virtual `qemu-xhci` controller on PCI slot 3,
sets up command, event, and EP0 transfer rings, enumerates and configures two
standard USB HID devices, and inspects DMA reports via real `GET_REPORT` control
transfers. Scoping QMP commands strictly to its owned VM without touching the
host keyboard or pointer, the runner injects synthetic `A` down/up events,
absolute coordinates `12345`/`23456`, and left-button down/up events. The guest
itself must read usage `4` followed by an all-zero keyboard report along with
the matching tablet coordinates and button states—the host never writes guest
report buffers. On 2026-10-23, all 14 xHCI transfer events and payload checks
passed; see
results and QMP actions.
This validates EP0 `GET_REPORT` transfers and event-ring polling rather than HID
interrupt-IN endpoints or controller IRQs. Note that the xHCI controller's 4KiB
DMA page contract is distinct from both 16KiB guest APV pages and 4KiB host
`mmap` granularity.

End-to-end native-window keyboard loop:

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload window-input --platform graphics --gpu nvidia --irq
```

This profile attaches a single standard USB keyboard. `window_input.py` accepts
only the live `Popen` instance and its client XID, re-verifying parent/executable
identity, `_NET_WM_PID`, and active native keyboard focus on that exact window
immediately prior to injection. It sends solely two `XSendEvent(propagate=false)`
events—targeted `A` key press and release—without calling `XTest`, moving the
host pointer, altering global window focus, or touching any other window. If
focus does not belong to that client or SDL rejects the event, the test fails
and cleans up without retrying or falling back to global input injection.

The guest first presents a red frame; **it renders the green frame only after
reading `A` down over real USB HID**, and it must also read the subsequent
all-zero key-release report. On the host, native pixel checks verify both the
red and green frames on the identical owned window while the guest continues to
verify GPU/display IRQs, count/mapping updates, and retirement of the first
surface. Normal runtime is ~10 seconds with an 8-second in-guest deadline per
key phase and a 20-second overall host ceiling; no QMP input substitutes for the
native window key event.

On 2026-10-23, a single live invocation completed the full path: X11 keycode 38
targeted the owned window, QEMU's callback trace logged `a down1/up0`, the guest
observed usage `4` followed by all-zero, and both native window captures
matched. The initial post-run checker falsely flagged a failure because both
frames landed within a single statistics reporting interval; the sole result
artifact preserves `initial_checker_passed=false`, the legacy predicate, and
`reassessed_from_existing_run=true`. Re-evaluating the untouched run artifacts
under the corrected predicate kept every two-frame, same-PID/XID, HID, and IRQ
requirement intact, dropping only the flawed "two distinct stats intervals"
assumption while still requiring real window GPU statistics. No window was
reopened, and raw UART, stderr, `XImage`, and PNG artifacts were untouched:

- Complete native-input results.
- Pre-input native red frame
  and
  post-input native green frame.

This establishes the self-authored guest's synthetic native-window key event →
SDL/Reims → QEMU → USB report → green GPU frame pipeline. It does not validate
physical keyboards, native mouse/scroll events, macOS USB drivers, or live Apple
OS interaction, and QMP pointer results remain separate from native keyboard
results. Because the native loop above uses EP0 `GET_REPORT`, standard HID
interrupt-IN transfers and controller IRQs are verified separately by the
headless cases below, followed by the combined same-window interrupt-IN case.

### HID Interrupt-IN, xHCI Completion Interrupts, and GIC

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload input-irq --platform graphics --gpu nvidia --irq
python3 experiments/E11-arm-reims/run-gpu.py --workload input-irq --platform graphics --gpu nvidia --irq --disable-work-irq
```

Under `-nodefaults`, this profile attaches only `qemu-xhci` on PCI slot 3 and
`usb-kbd` on port 1, opening no window and sending no desktop events. The GPU
does not participate in this case; the retained Vulkan inventory merely records
the shared runner environment and is not evidence of GPU execution. No device
source edits or extra rebuilds were required for this IRQ probe.

Pinned board-level interrupt routing follows
VMApple `create_pcie`,
GPEX swizzle, and
xHCI PCI interrupt wiring:
xHCI advertises INTA (PCI pin 1, internal pin 0), yielding GPEX index
`(slot3 + pin0) % 4 = 3` wired to VMApple GIC input `0x20 + 3`, so guest `IAR`
reads SPI **INTID 67**. Because `pci_set_irq(level)` drives a level-sensitive
line, GPU INTID 49's edge configuration cannot be reused here. Although the
board also instantiates a GICv2m MSI bridge, MSI/MSI-X is out of scope for this
case and the guest explicitly verifies that MSI/MSI-X enable bits are `0`.

`probe-input.c` reuses standard control transfers to complete bus enumeration
and reads the live USB configuration descriptor, where `usb-kbd` exposes
endpoint `0x81` (interrupt-IN, 8-byte max packet, high-speed `bInterval=7`).
`guest-usb-irq.h` issues a `Configure Endpoint` command to establish DCI 3 with
its own transfer ring, posts a `Normal` TRB, and enables xHCI interrupter 0 /
`INTE` alongside level-triggered SPI 67. Guest RAM allocations remain
16KiB-aligned while honoring xHCI's 4KiB `PAGESIZE` contract without conflating
the two.

In the positive case, wait loops inspect only ISR-recorded completion state
rather than polling USB report memory or the event ring. The ISR must read
`IAR = 67`, observe `IMAN.IP`/`IE` and `USBSTS.EINT`, validate the `Transfer
Event` TRB's type, slot, DCI, TRB pointer, success completion code, and zero
residual length, and then latch the DMA report bytes. Only after clearing
`IP`/`EINT` and advancing `ERDP` with `EHB` cleared does the ISR write EOIR; the
guest posts the next TRB after receiving `A` down and requires `A` up to pass
through the identical ISR and report validation. Waits are bounded to 5 seconds
by the guest counter rather than relying on host timeouts.

On 2026-10-23, the
positive run
observed two `IAR = 67` interrupts with `IMAN=3`, `USBSTS=8`, count advancing
`1→2`, and 8-byte reports transitioning usage `4` → all-zero, exiting cleanly
with `0`. Keeping the controller and endpoint active, the
disabled-SPI negative control
waited 5 seconds and verified that usage `4` in DMA memory, the successful
`Transfer Event`, `IMAN.IP`, and GIC pending state were all asserted while ISR
count remained `0`, exiting `1` as required so it cannot masquerade as a
passing input run. Each run retains full UART logs and QMP action records. This
validates standard HID keyboard interrupt notifications and post-ack re-arming;
taken alone, this headless case neither combines native SDL window events with
interrupt-IN in one run nor proves macOS USB driver attachment.

### Same-Window Interrupt-IN Input and Dual GPU/Display Interrupts

```sh
python3 experiments/E11-arm-reims/run-gpu.py --workload window-input-irq --platform graphics --gpu nvidia --irq
```

While preserving the legacy EP0 `window-input` entry point, this workload wires
targeted `A` down/up events from the owned window into the standard USB
interrupt-IN endpoint. A shared guest ISR reads `IAR` once per exception: INTID
67 dispatches to the USB handler to clear the level-sensitive `IP`/`EINT`/`EHB`
sources first, whereas INTID 49 dispatches to read-and-clear the separate GPU
and display event registers, before the common dispatcher writes EOIR. The two
GIC register banks retain their respective level-67 and edge-49 configurations
independently.

The guest first presents a real GPU-rendered red frame, posts the USB IN TRB,
and announces readiness. Once the host captures the red native window image from
that owned window, it injects a single targeted `A` key-down event; only after
ISR 67 delivers usage `4` with a matching `Transfer Event` may the guest submit
green GPU rendering work. Second-pass GPU/display INTID 49 notifications, the
full-frame pixel oracle, `presentCount` `1→2`, separate mapping ID 2, and
retirement of the first surface remain governed by the earlier contracts, and
`A` key-up must likewise arrive via ISR 67 as an all-zero report. Finally, the
host must capture a matching green native window image from the identical
PID/XID with zero QMP input commands issued. The runner simultaneously enforces
the UART causal order `USB down ISR → green GPU → green display → USB release`,
two real `IAR = 67` deliveries, and GPU/display INTID 49 evidence so spliced
independent runs cannot pass.

Execution continues to target strictly the already-focused window owned by this
QEMU child without changing window focus or injecting global desktop input. The
window closes automatically at completion; normal runtime is ~10 seconds under a
20-second host ceiling and a 5-second in-guest timer per USB wait phase.

On 2026-10-23, a single live NVIDIA combined invocation
passed on the first run,
exiting `0` and closing its window with no reassessment or rerun. Live QEMU
stderr confirmed execution on RTX 5070 Ti Laptop GPU, and the single UART stream
completed in strict order: `red present → USB down ISR67 → green GPU stamp9
ISR49 → green present count2/mapping2 → USB release ISR67`. Both USB interrupts
recorded `IMAN=3` and `USBSTS=8` with reports transitioning usage `4` →
all-zero; both full GPU frames verified 1152 foreground pixels and 2944 blue
background pixels alongside passing guard and retired-ID-1 release/poison
checks.

Independent
native red capture
and
native green capture
came from the 320×200 client area of the identical PID `175280` / XID
`0x1c0002f`, with red pixel count dropping to `0` in the green capture. Input
consisted solely of one targeted `A` down/up pair sent to that already-focused
owned window, with zero global input or focus changes and zero QMP input
injection; window GPU statistics provided independent submission evidence. This
completes the combined single-session native window input → USB interrupt →
guest GPU frame generation → native window display loop within self-authored ARM
guest scope, without substituting for macOS USB or `WindowServer` driver
validation.

## Installed-OS WebGL2 fixed readback validation

The installed-OS path is separate from the standalone firmware runners above.
The [self-authored page](../E05-installed-boot/guest-diag/webgl-probe.html)
(`webgl2-six-tiles-v1`, source SHA256
`ac540a7ce9765c35f666d8cadfc9239a22be48c1604a7cde494beb1c041d0e4e`)
compiles a six-color WebGL2 shader and reads a 96×64 RGBA8 offscreen FBO and
default framebuffer. Each raw output is 24576 bytes, with bottom-left row
origin. The [independent host oracle](../../tools/validate-webgl-probe.py)
checks both entire outputs, fixed dimensions/format, shader/program status,
GL errors, context loss and upload metadata; it does not use a screenshot as
the pixel oracle.

The retained host control
ran actual Chrome with explicit SwiftShader and the real GuestFileService
loopback GET/PUT path. Both outputs match the independent expected SHA256
`d8da4828f987b4c3334729f48fdeb6036bd810c23d16526fcca6373c203355da`.
Eight negative controls cover byte/row changes, truncation, missing output,
lost context, incorrect dimensions/name and symlinks; all reject as expected.
Two glslang shader checks and `node --check` pass. Browser children and the
file service are stopped; a separate real-child cleanup-race regression
passes without rerunning the render control. This control uses a software
renderer, and establishes no macOS, physical GPU, Metal or performance result.
The masked Chrome string `WebKit WebGL` is not evidence of Safari execution.

Run128 completed the installed-OS consumer on the existing 4a43 main binary,
network CTRL 512 and the guarded finite 30 s diagnostic. The
actual macOS oracle
passes both entire 24576 B outputs with zero byte differences and the same
independent expected hash above. Actual context creation/dimensions,
shader/program status, GL error checks and PUTs pass. Native pixels show
upper CMY / lower RGB tiles. The API reports `Apple GPU`; this does not establish
physical GPU execution, scanout, FPS, latency or general WebGL/Metal coverage.

The Safari analysis
retains one Safari-owned GPU 1124: first client UI request → DidInitialize 3.781 s,
then the WebGL page connection create → DidInitialize 13.716 s. These gaps are
not measured blocking intervals. There are zero GPU watchdog requests but
two WebProcess unresponsive notifications. Final global site0 has one hit
on CPU2, site1 has none and rejections are zero, with unchanged per-CPU epochs.
No PID attribution or original Safari 3 s performance pass follows.

The two raw PUTs and JSON arrive during the ordinary watcher; its retained
timeline is 133 s, despite the 120 s request. No optional late window, reload or
authored PCM warmup was used. This new shader workload is not a matched
Run127 performance reproduction. The host transport index
records seven targeted methods (URL 3 / legacy directory 1 / GET-PUT-render 2 / helper 1)
and separate shell syntax; synthetic transfer patterns are not rendered
outputs. Existing URLs remain unchanged; no global URL whitelist was added.

Run128 application
records guest-origin shutdown, QEMU/files exit 0 / reaped, no host quit and
alias cleanup. Controller 1 is the unavailable post-exit final capture;
`errors=[]`, filesystem cleanliness is unproved and the 5 s wait was not attempted.
No VM is active. The user has requested project maintenance after this run,
with no Run129 or new feature experiments planned. Maintenance is next and
has not yet been executed; the long-term goal is not marked complete or paused.
