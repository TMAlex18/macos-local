.PHONY: doctor fetch-sources build-armvirt build-vmapple-headless build-reims-vulkan build-reims-qemu-x86 reims-pci-realize arm-uart gpu-compute gpu-render gpu-external-caps gpu-external-import vmapple-cfg vmapple-psci smoke

doctor:
	./tools/doctor.sh

fetch-sources:
	./tools/fetch-sources.sh

build-armvirt:
	./tools/build-armvirt.sh

build-vmapple-headless:
	./tools/build-vmapple-headless.sh

arm-uart: build-armvirt
	./experiments/E04-cpu/run.sh

vmapple-cfg: build-vmapple-headless
	./experiments/E06-vmapple/run.sh

vmapple-psci: build-vmapple-headless
	./experiments/E06-vmapple/run-psci.sh

gpu-compute:
	./experiments/E02-vulkan/run.sh

gpu-render:
	./experiments/E02-vulkan/run-render.sh

gpu-external-caps:
	./experiments/E03-cross-gpu/run-capabilities.sh

gpu-external-import:
	./experiments/E03-cross-gpu/run-buffer-import.sh

smoke: gpu-compute gpu-render arm-uart vmapple-cfg vmapple-psci

build-reims-vulkan:
	./tools/build-reims-vulkan.sh

build-reims-qemu-x86:
	./tools/build-reims-qemu-x86.sh

reims-pci-realize: build-reims-qemu-x86
	python3 ./experiments/E01-build/realize-reims-pci.py

# M1 research-only ARM64 cross-ISA VMApple/Reims integration.
.PHONY: build-reims-qemu-arm build-reims-arm64-vulkan build-reims-qemu-arm16k reims-arm16k-smoke reims-arm-compare

build-reims-qemu-arm:
	./tools/build-reims-qemu-arm.sh

build-reims-arm64-vulkan:
	./tools/build-reims-arm64-vulkan.sh

build-reims-qemu-arm16k:
	./tools/build-reims-qemu-arm16k.sh

reims-arm16k-smoke:
	MACOS_LOCAL_E11_VARIANT=16k python3 experiments/E11-arm-reims/run-machine.py
	MACOS_LOCAL_E11_VARIANT=16k python3 experiments/E11-arm-reims/run-firmware.py
	MACOS_LOCAL_E11_VARIANT=16k python3 experiments/E11-arm-reims/run-mmio.py
	MACOS_LOCAL_E11_VARIANT=16k python3 experiments/E11-arm-reims/run-registers.py
	python3 experiments/E11-arm-reims/run-root-page.py
	python3 experiments/E11-arm-reims/run-fifo.py

reims-arm-compare:
	python3 experiments/E11-arm-reims/run-root-page.py
	python3 experiments/E11-arm-reims/run-fifo.py

.PHONY: reims-core-serial reims-fifo-roundtrip
reims-core-serial:
	./tools/test-reims-core-serial.sh

reims-fifo-roundtrip:
	python3 experiments/E11-arm-reims/run-device-info.py

.PHONY: platform-aes-tests storage-retention-tests virtio-usb-tests virtio-usb-control-tests
platform-aes-tests:
	python3 experiments/E06-platform-contracts/run-aes.py

storage-retention-tests:
	python3 experiments/E05-usbip-kernel/test-storage-retention.py

virtio-usb-control-tests:
	python3 experiments/E05-usbip-kernel/test-virtio-usb-control.py

virtio-usb-tests:
	python3 experiments/E05-virtio-usb/run.py

.PHONY: check-research-patches
check-research-patches:
	python3 tools/check-research-patches.py

# E06 platform contracts; no network or Apple guest material.
.PHONY: platform-contracts platform-timer platform-mmu
platform-contracts:
	python3 experiments/E06-platform-contracts/run.py --suite all --target all

platform-timer:
	python3 experiments/E06-platform-contracts/run.py --suite timer --target all

platform-mmu:
	python3 experiments/E06-platform-contracts/run.py --suite mmu --target all

# E06: architectural PAC baseline versus opt-in HVC state model.
.PHONY: pauth-baseline pauth-model pauth-model-impdef pauth-cost-bench pauth-guards
pauth-baseline:
	python3 experiments/E06-pauth-hvc/run.py

pauth-model:
	python3 experiments/E06-pauth-hvc/run-model.py

# Same model checks with QEMU's non-cryptographic impdef PAC (separate results).
pauth-model-impdef:
	python3 experiments/E06-pauth-hvc/run-model.py impdef

# Self-guest per-call emulation cost of PACIBSP/RETAB for impdef/QARMA3/QARMA5.
pauth-cost-bench:
	python3 experiments/E06-pauth-hvc/bench-pac-cost.py

pauth-guards:
	python3 experiments/E06-pauth-hvc/run-guards.py

# E05 material audit. Extraction is explicit network access; subsequent audits are local.
.PHONY: clock-rate-baseline
clock-rate-baseline:
	python3 experiments/E06-platform-contracts/run-clock.py

# Fractional ARM timer experiment. Dedicated build; x-cntfrq-exact defaults off.
.PHONY: build-qemu-clock-exact clock-math clock-functional clock-rate-exact clock-check
build-qemu-clock-exact:
	./tools/build-qemu-clock-exact.sh
clock-math:
	./tools/test-clock-arithmetic.sh
clock-functional:
	python3 experiments/E06-clock-exact/run-functional.py
clock-rate-exact:
	python3 experiments/E06-clock-exact/run-rate.py
clock-check: build-qemu-clock-exact
	./tools/test-clock-arithmetic.sh
	python3 experiments/E06-clock-exact/run-functional.py
	python3 experiments/E06-clock-exact/run-rate.py

# E05 transport experiments: synthetic ARM guest, local Unix/socketpair only.
.PHONY: build-qemu-bdif-usb bdif-usb-tests bdif-usb-backpressure bdif-usb-control
build-qemu-bdif-usb:
	./tools/build-qemu-bdif-usb.sh
bdif-usb-tests:
	python3 experiments/E05-bdif-usb/run.py --phase corrected-final
bdif-usb-backpressure:
	python3 experiments/E05-bdif-usb/run-backpressure.py
bdif-usb-control:
	python3 experiments/E05-bdif-usb/run-control.py

# Genuine Apple AVPBooter -> Linux kernel USB/IP -> native client queries.
.PHONY: kernel-usbip-adapter-tests
kernel-usbip-adapter-tests:
	python3 experiments/E05-usbip-kernel/test-real-control.py

.PHONY: bdif-block-tests
bdif-block-tests:
	python3 experiments/E05-bdif-block/run.py

.PHONY: storage-cow-tests

# Synthetic storage and paused QEMU only; no Apple firmware or network.
storage-cow-tests:
	python3 experiments/E05-usbip-kernel/validate-storage.py --virtio-status

.PHONY: config-mailbox-tests
config-mailbox-tests:
	python3 experiments/E05-config-mailbox/run.py

.PHONY: avp-rtc-tests
avp-rtc-tests:
	python3 experiments/E05-avp-rtc/run.py

.PHONY: ctrr-contract-tests
ctrr-contract-tests:
	python3 experiments/E05-ctrr/test-contract.py -v

.PHONY: ctrr-qemu-guest-tests
ctrr-qemu-guest-tests:
	python3 experiments/E05-ctrr/run.py

.PHONY: smp-mmu-tests smp-mmu-extended sync-counter-tests
# Self-authored guest only: no Apple firmware, TSS, USB/IP or system changes.
smp-mmu-tests:
	python3 experiments/E06-smp-mmu/run.py

smp-mmu-extended:
	python3 experiments/E06-smp-mmu/run.py --case positive --mode multi --rounds 256

sync-counter-tests:
	python3 -B experiments/E06-sync-counters/run.py

# Current installed-OS orchestration checks. Run sequentially: fixtures can
# share a loopback port. No QEMU, browser, GPU, guest or external network.
# Requires Python 3, POSIX shell/curl tools and clang with UBSan for the C probe.
# Saved private sound-layout cases explicitly skip when their inputs are absent.
.PHONY: installed-offline-tests build-shared-cache-identity webgl-fixture-host-check
installed-offline-tests:
	@mkdir -p build
	@test -f build/macos-shared-cache-identity/shared-cache-identity || ./tools/build-macos-shared-cache-identity.sh
	python3 -B experiments/E05-installed-boot/test-offline.py
	python3 -B experiments/E05-installed-boot/test-guest-ui.py
	python3 -B experiments/E05-installed-boot/test-guest-files.py
	python3 -B experiments/E05-installed-boot/test-network-state.py
	python3 -B experiments/E05-installed-boot/test-timeout-diagnostic.py
	python3 -B experiments/E05-installed-boot/test-gpu-init-timing.py
	python3 -B experiments/E05-installed-boot/test-host-cpu-profile.py
	python3 -B experiments/E05-installed-boot/test-block-sampler.py
	python3 -B experiments/E05-installed-boot/test-sync-stats.py
	python3 -B experiments/E05-installed-boot/test-log-window.py
	python3 -B experiments/E05-installed-boot/test-gpuwatch-offline.py
	python3 -B experiments/E05-installed-boot/test-shared-cache-identity.py
	python3 -B experiments/E05-usbip-kernel/test-qmp-lifecycle.py
	python3 -B experiments/E05-usbip-kernel/test-storage-retention.py
	python3 -B experiments/E05-signed-restore/test-rsp-readonly.py
	python3 -B tools/test-freeze-audio-voices.py

# Self-authored arm64 Mach-O only; no SDK or guest execution.
build-shared-cache-identity:
	./tools/build-macos-shared-cache-identity.sh

# Opt-in real host browser/SwiftShader control, deliberately outside the
# offline aggregate. Not macOS, Metal, physical-GPU or performance evidence.
webgl-fixture-host-check:
	python3 -B experiments/E05-installed-boot/test-webgl-probe.py

.PHONY: el0-context-tests el0-context-extended
# Self-authored guest only. Reuses existing binaries; no network or Apple code.
el0-context-tests:
	python3 experiments/E06-el0-context/run.py
el0-context-extended:
	python3 experiments/E06-el0-context/run.py --case positive --rounds 64

# Opt-in isolated E06 FP/SIMD/TLS contract (no network or Apple code).
.PHONY: el0-fp-tls-tests el0-fp-tls-extended
el0-fp-tls-tests:
	python3 experiments/E06-el0-fp-tls/run.py
el0-fp-tls-extended:
	python3 experiments/E06-el0-fp-tls/run.py --case positive --rounds 64

# E06 complete 32 SIMD Q-register EL0 context, separate from older selected Q8/Q9.
.PHONY: el0-simd32-tests el0-simd32-extended
el0-simd32-tests:
	python3 experiments/E06-el0-simd32/run.py
el0-simd32-extended:
	python3 experiments/E06-el0-simd32/run.py --case positive --rounds 64

.PHONY: el0-migration-tests el0-migration-extended el0-migration-model-tests el0-migration-lazy-fp-tests
el0-migration-tests:
	python3 experiments/E06-el0-simd32/migration.py --case all
el0-migration-extended:
	python3 experiments/E06-el0-simd32/migration.py --case positive --rounds 64
el0-migration-model-tests:
	python3 experiments/E06-el0-simd32/migration.py --pac-model --case all
el0-migration-lazy-fp-tests:
	python3 experiments/E06-el0-simd32/migration.py --lazy-fp --case all

.PHONY: reims-gpu-compute reims-gpu-draw reims-gpu-irq reims-gpu-restore-platform reims-gpu-iosurface reims-gpu-present reims-input reims-window-input reims-input-irq reims-window-input-irq
reims-gpu-compute:
	python3 experiments/E11-arm-reims/run-gpu.py --workload compute --gpu nvidia
	python3 experiments/E11-arm-reims/run-gpu.py --workload compute --gpu intel
reims-gpu-draw:
	python3 experiments/E11-arm-reims/run-gpu.py --workload draw --gpu nvidia
	python3 experiments/E11-arm-reims/run-gpu.py --workload draw --gpu intel
reims-gpu-irq:
	python3 experiments/E11-arm-reims/run-gpu.py --workload compute --gpu nvidia --irq
	python3 experiments/E11-arm-reims/run-gpu.py --workload compute --gpu nvidia --irq --disable-work-irq
	python3 experiments/E11-arm-reims/run-gpu.py --workload draw --gpu nvidia --irq
	python3 experiments/E11-arm-reims/run-gpu.py --workload compute --gpu intel --irq
reims-gpu-restore-platform:
	python3 experiments/E11-arm-reims/run-gpu.py --platform restore-research --workload compute --gpu nvidia --irq
	python3 experiments/E11-arm-reims/run-gpu.py --platform restore-research --workload draw --gpu nvidia --irq
reims-gpu-iosurface:
	python3 experiments/E11-arm-reims/run-gpu.py --platform restore-research --workload iosurface-draw --gpu nvidia --irq
	python3 experiments/E11-arm-reims/run-gpu.py --platform restore-research --workload iosurface-draw --gpu intel --irq
# Opens one owned native window for two five-second frames, then closes it.
reims-gpu-present:
	python3 experiments/E11-arm-reims/run-gpu.py --platform restore-research --workload present-update --gpu nvidia --irq
reims-input:
	python3 experiments/E11-arm-reims/run-gpu.py --workload input --gpu nvidia
reims-input-irq:
	python3 experiments/E11-arm-reims/run-gpu.py --workload input-irq --gpu nvidia --irq
	python3 experiments/E11-arm-reims/run-gpu.py --workload input-irq --gpu nvidia --irq --disable-work-irq
# One owned window receives synthetic A down/up; no global key/focus changes.
reims-window-input:
	python3 experiments/E11-arm-reims/run-gpu.py --workload window-input --gpu nvidia --irq
reims-window-input-irq:
	python3 experiments/E11-arm-reims/run-gpu.py --workload window-input-irq --gpu nvidia --irq
