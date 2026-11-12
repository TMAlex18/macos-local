> **Language:** **English** | [简体中文](README.md)

# Configuration Request Slot Self-Authored Guest Regression

`guest.c` is a single-shot self-authored AArch64 EL1 guest that writes the
physical address of a 24B request to `0x4ffff8` over real TCG MMIO. Its build
directly reuses the startup code, runtime, and linker script from
`../E06-platform-contracts/`.

```sh
python3 experiments/E05-config-mailbox/run.py
```

Use `--build-only` to compile without running; `--binary PATH` selects the QEMU
binary under test. By default it runs `build/qemu-bdif-usb/qemu-system-aarch64`
with 512MiB RAM, single-core TCG, and blank test disks scoped to this
directory, without loading Apple firmware or requesting TSS.

In a single execution, the guest checks 14 scenarios: normal `ENOTSUP=45`
completion on operation 2 for types 1/2/3—where type 1 uses the 5MiB capacity
requested by original Stage2 and verifies byte-for-byte that the entire buffer
remains unchanged; `ENOENT=2` completion on operation 1 / type 1 when identity
is absent, and native `EINVAL=22` on operations 1/3 with invalid types; invalid
version, both ends of the type range, unmapped parameter buffer, parameter
buffer straddling the end of RAM, length overflow, unmapped descriptor,
descriptor straddling the end of RAM, and the existing CFG version, CPU count,
and RAM size fields. Every submission also verifies that reading back the slot
always returns zero. Original parameter contents and sentinels on both sides
must remain unchanged; rejected descriptors must remain intact in full; for
incomplete descriptors straddling the end of RAM, only the bytes actually
resident in guest RAM are checked.

Structured results are saved to `runs/e05-config-mailbox-results.json`, and
complete UART/stderr output is saved to `runs/e05-config-mailbox.log`. Passing
requires clean guest exit, all scenarios completed in order, and the final PASS
marker present; a host timeout is always treated as a failure. This validates
normal error semantics and DMA boundaries, and does not imply that operation 3
or any Apple boot path is implemented.
