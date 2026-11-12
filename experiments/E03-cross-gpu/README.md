# E03: Intel / NVIDIA cross-GPU interoperability study

Status: **capability query only** (not yet buffer import/export execution).

Run: make gpu-external-caps

Observed Vulkan on 2026-10-14:
- For both Intel ANV and NVIDIA, buffer OPAQUE_FD and DMA_BUF_EXT for
  transfer-src/dst report exportable and importable.
- For optimal-tiled RGBA8 transfer images, OPAQUE_FD reported both on both
  GPUs; NVIDIA reported DMA_BUF export+import; Intel returned
  VK_ERROR_FORMAT_NOT_SUPPORTED (-11) for the query as structured.

This does not exclude modified queries with explicit DRM format modifiers,
different usage/layout, a different buffer format, or alternative sync/copy.
No fd has been exported or imported across devices in this experiment.
Driver-specific handle semantics and lifecycle still require an actual test.

Next: perform a real buffer import and data check (both directions);
then test image modifier intersections and GPU-to-GPU synchronization.


## Actual 4096-byte DMA_BUF buffer exchange

Run: make gpu-external-import

The smoke program creates valid Vulkan logical devices with queues, exports
host-visible/coherent memory from one driver, attempts to import that DMA_BUF
in the other driver, and reads the known byte pattern if both mappings work.

Observed 2026-10-14:
- NVIDIA -> Intel: import allocation/binding succeeded; vkMapMemory on the
  imported VkDeviceMemory returned VK_ERROR_MEMORY_MAP_FAILED (-5).
- Intel -> NVIDIA: vkAllocateMemory with VkImportMemoryFdInfoKHR returned
  VK_ERROR_OUT_OF_DEVICE_MEMORY (-2).

Thus **this exact CPU-visible buffer-sharing method did not succeed in either
direction**. Device-local memory, explicit copy queues, DRM modifiers and
external semaphore synchronization were not exercised; no general statement
about cross-GPU interchange is justified.

An earlier experimental version omitted the required VkDevice queue creation
info and segfaulted in user space. We corrected the harness, independently
retested both GPUs with the known-good offscreen renderer, and reran the
DMA_BUF experiment without repeating the segmentation fault. Do not attribute
that earlier harness error to hardware incompatibility.
