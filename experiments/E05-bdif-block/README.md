# BDIF persistent block read/write contract (synthetic ARM guest)

`make bdif-block-tests` builds and executes 9 small ARM programs on owned
32MiB raw files. The guest submits 48-byte BDIF requests and validates status
and readback; after exit the host checks bytes, neighboring sentinels and
unchanged file length. Apple code and real user disks are not used.

Cases: AUX offset16MiB512B write, ROOT write, exact-end sector, read-only,
beyond-end, invalid DMA data, unsupported flags, invalid completion address,
and unreadable request descriptor. All final cases pass. Preserved pre-fix
binary fails the positive AUX-write test; no fake success is returned.

The implementation acquires write permissions only where the backend supports
writing, checks descriptor/status/length/bounds, performs actual blk_pwrite,
and flushes before successful completion. This is synchronous bring-up code,
not a high-throughput storage design, crash/power-loss certification or full
Apple storage-protocol implementation. It does not implement erase/trim,
atomics, migration or every future descriptor type.
