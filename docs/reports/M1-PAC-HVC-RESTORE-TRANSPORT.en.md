> **Language:** **English** | [简体中文](M1-PAC-HVC-RESTORE-TRANSPORT.md)

# M1: PAuth/HVC Software Reference Model and Restore Transport Audit

Round date: 2026-10-14. Continuing Master Plan v3; still in M1 research without booting Apple boot firmware, XNU, or the macOS GUI.

## 1. Separating and Testing Two Capabilities First

Both VMApple paths ran the identical self-authored ARM64 firmware, confirming that IA/IB/DA/DB signing and authentication, wrong-key and wrong-modifier rejection, key restoration, XPACI, and PACGA key dependence all execute properly.

Originally, Apple CPU PAC HVCs 0xC1000000–0xC1000006, NOP 0xC10000F0, and sampled unknown/OEM calls all returned -1 instead of performing their key-management operations. In source code, the HVC conduit enters the PSCI dispatcher whose unknown-function branch returns NOT_SUPPORTED; looking only at the EL2 branch in `pre_hvc` would misdiagnose this as UNDEF.

Consequently, the gap is not "Intel hosts cannot execute PAC instructions," rather the absence of guest-requested hypervisor services. Authentication exceptions triggered by instruction-level negative cases are genuine guest exceptions rather than fabricated harness checks.

## 2. Adding a Default-Off Software Model

The `x-pac-software-model=on` switch is explicitly an experimental model; it remains off by default so existing platform and graphics tests are not silently altered.

The model maintains and updates real key state rather than returning 0 for every HVC or patching out wait/authentication routines inside the Apple kernel. It covers initial state, default input keys, A/B/G updates, EL0 diversifier, temporary EL1 override using the EL0 key bank, NOP, and failure handling. Non-PAuth CPUs are rejected, and live migration is explicitly blocked while migration state serialization remains unimplemented.

**Cryptographic boundary (critical):** This implementation generates a per-VM random master secret, derives 128-bit keys via role-separated HMAC-SHA256, and executes signing/authentication through QEMU's PAC instruction algorithm. It is not a reproduction of Apple's hardware key derivation, cannot claim bit-exact identity with Apple PAC outputs, and is not macOS 27 ABI certification.

QEMU's default `max` CPU uses non-cryptographic impdef PAC; the initial baseline pass served only for behavioral checks. Current model verification explicitly selects `max,pauth-qarma5=on` and checks guest ID registers to avoid conflating fast hashing with cryptographic PAC. Nor does this project claim that QARMA5 is Apple's proprietary cipher.

## 3. Verified Boundaries

- Initialization, defaults, and A/B/G rekey operations mutate their corresponding key banks, and restoring default inputs returns to the original state.
- Prior signatures fail authentication after rekeying; valid keys authenticate cleanly.
- Setting the EL0 diversifier from EL1 does not pollute normal EL1 keys; switching occurs only under explicit override.
- Executing a real ERET into EL0 to sign, then entering EL1 via SVC, matches the signature obtained under EL1 override with the identical diversifier, while the normal EL1 bank is restored automatically.
- EL0 cannot invoke EL1 HVC services directly; attempting to do so raises an actual synchronous exception verified by the test.
- Unknown HVCs leave keys untouched, invalid boolean values are rejected, and existing PSCI calls remain operational.
- Initializing the second vCPU obtains the same VM-wide default inputs, while rekeying on the second vCPU does not alter the first vCPU's state.
- Across two CPU_OFF/CPU_ON cycles on the second vCPU, each bring-up requires fresh initialization without leaking stale managed state.
- Three negative configuration guards — mode-off, non-PAuth CPU, and only-migratable — each reject execution as expected.

This is a multi-assertion dual-vCPU bare-metal experiment, not a full OS or hardware certification. The EL0 checks here ran with the MMU disabled and cannot be used to claim joint verification of EL0 address spaces, multicore TLBs, and PAC.

## 4. Lifecycle and Unimplemented Semantics

The model supports per-CPU reset within the current tests, yet lacks migration state serialization, persistent PAC identity, and verification of full-system warm reset, combined guest direct MSR key writes with managed HVC services, or arbitrary EL/interrupt nesting. OEM clock, boot-session UUID, and WFK/KICK services remain unimplemented.

Returning -3 before initialization and -2 for invalid booleans are conventions of this model. Historical documentation does not specify all of these error paths, so they must not be documented as established Apple behavior.

Service numbers come from Apple's public XNU headers at fetched main-branch commit f6217f891ac0bb64f3d375211650a4c1ff8ca1ea; this is not an empirical test against the closed-source 27.0.1 kernel. Legacy vmapple_pac.md details came from a mirror while the upstream path returned 404, and that provenance difference is preserved.

## 5. E05: Linux Restore Lacks More Than a Download Link

Direct audit of `hw/vmapple/bdif.c` in the locally pinned QEMU: DEVID_USB is defined, yet command dispatch handles only ROOT and AUX, with no USB command branch. That means the current restore path still lacks virtual USB/DFU transport rather than merely missing an IPSW file.

Commit 657bb2423ec8d572b22a97dd33563dbba1680e6a and its USB chardev patch were retrieved from a third-party research repository; `git apply --check` passes against current sources, **without applying the patch or running it locally**. Although that repository's author reported DFU transfers on Asahi Linux, its journal does not prove completion of native Linux restore.

The three next-step material tracks remain distinct:

1. Obtaining a matching AVPBooter and pre-initialized VM from an Apple reference environment, kept strictly as an optional source without assuming the user owns a Mac.
2. Investigating extraction of Virtualization.framework assets from inside official OS artifacts; tooling documentation outlines DMG/AEA inspection directions, though a matching AVPBooter has neither been located nor extracted locally, and the absence of a matching filename in the top-level ZIP must not be misread as absence across all nested filesystems.
3. Native Linux restore requires AVPBooter + BDIF USB/USBIP + an accurate restore state machine + matching signing tickets + real AUX/identity/system disk. Genuine guest responses must be verified rather than synthesizing guessed DFU status packets and treating signed restore as complete.

This PAC/HVC and transport audit neither downloaded a full IPSW, executed Apple firmware, modified host USB modules, nor bypassed restore signatures. During wrap-up, another set of uncommitted workspace scripts was found to have extracted five small components into private and generated DeviceTree and clock reports; those files and existing hashes were checked read-only without overwriting, deleting, or staging them into this commit. Neither AVPBooter nor an installed VM has been obtained yet.

## 6. Reproducible Entry Points

    make pauth-baseline
    make pauth-model
    make pauth-guards
    make platform-contracts
    make reims-fifo-roundtrip
    make check-research-patches

The current QEMU build uses cumulative patch 0003, which supersedes rather than stacks sequentially on top of the original architectural patch 0002. Independent negative-control and platform forks remain preserved. Source code and test scripts live in experiments/E06-pauth-hvc; canonical state is tracked in PLAN-STATE.json and this round's results JSON.

## 7. Other Uncommitted Work Observed at Wrap-Up

Four files — extract-small-boot-components.py and audit-device-tree.py under E05, plus clock.c and run-clock.py under E06-platform-contracts — were not added by this pass and remain uncommitted. Sizes and hashes of DeviceTree, LLB, iBoot, iBSS, and iBEC in private match the existing report; none of these Apple components were executed and Apple signatures were not independently verified. The DeviceTree report records a 24MHz timebase, and the clock report records rate drift under the 24MHz profile; because this pass neither reran nor fixed that experiment, those findings are not claimed as clock validation results of this round.
