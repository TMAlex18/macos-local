# E02: direct Vulkan compute and offscreen rendering

Usage:
  make gpu-compute
  make gpu-render

1. Compute: 256 known 32-bit integer outputs from a compute shader are
   read back via coherent memory and checked elementwise.
2. Render: a 64x64 RGBA8 image is cleared, a triangle is rasterized using
   vertex/fragment shaders, the optimal-tiled image is copied into a host
   staging buffer, and the center/corner pixels are checked (with ±2
   tolerance for RGBA8 rounding at the center).

Results on 2026-10-14 (measured, same laptop):
  Intel ARL iGPU: compute PASS, render PASS.
  RTX 5070 Ti Laptop: compute PASS, render PASS.

The tests are small correctness smoke tests, NOT stable microbenchmarks.
Do not infer Metal compatibility, external-memory interoperability,
cross-GPU copy cost, presentation latency or broad driver conformance.
