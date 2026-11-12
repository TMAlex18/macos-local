# E06: integrated EL0 / MMU / PAuth / timer context switching

Self-authored AArch64 firmware; **one vCPU, two EL0 tasks**. No Apple binary,
TSS, USB device, physical disk change, or emulator source modification.

    make el0-context-tests       # 3 profiles x 4 positive/negative cases
    make el0-context-extended    # same 3 positive profiles, 64 IRQs per task

Profiles: GPU-less VMApple with direct architectural key writes; Reims
VMApple with direct key writes; Reims with the opt-in local HVC key-state
model. All explicitly use QARMA5. The third profile tests our HMAC-based
model's EL0/EL1 key-bank transitions, not Apple cryptographic equivalence.

The guest uses the same EL0 code/data/stack virtual addresses but distinct
physical data/stack pages, fixed translation tables and ASIDs 0x31/0x32.
Context switches do not flush all TLB entries. An EL1 timer handler saves
and restores the shared vector frame plus SP_EL0, selects the next page
table and key/diversifier, then ERETs. The user program repeatedly executes
BLRAA and PACIASP/AUTIASP around a real stack frame. Positive cases require
interrupts inside each task's signed frame, correct private data, separate
signatures, exact IRQ/SVC counts and successful return to user execution.

Each user task also tries an EL1-only data read, a store to its read-only
code page, and a branch into an executable-looking RET in a UXN data page.
EL1 checks the originating EL, exact expected instruction/FAR and permission
syndrome. Only those deliberate probes resume after the known fault.

Negative cases are independent variants: restore the wrong key/diversifier;
remove UXN; or leave GIC PPI27 disabled. The first must raise a genuine PAC
failure, the second must expose a missing protection fault, and the third
must hit the guest's bounded no-preemption check. Host timeout, linker
failure and arbitrary nonzero exits are never accepted as a negative pass.

Files:
- context.c: tiny test scheduler, page setup and checked exception handlers.
- user.S: unprivileged test code, signed indirect call and stack frame.
- link.ld: one 16KiB user code page plus privileged firmware/BSS.
- run.py: builds, runs and writes timestamped results below runs/.

Reuses E06-platform-contracts runtime/start.S unchanged. No new dependency.
Not covered: FP/SIMD/SVE/TLS context, nested IRQ, multicore EL0 execution,
real hardware cache behavior, Apple KDF, 24MHz combined profile or macOS.
The two QEMU builds share much code, so they are not independent hardware
implementations. Timing and work-loop counts are not performance results.
