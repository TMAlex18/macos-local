# Enable VMApple platform, omit Reims vGPU and Apple-host GPU.
# First-pass machine test on Linux/x86 via QEMU TCG.
CONFIG_VMAPPLE=y
CONFIG_REIMS_VGPU=n
CONFIG_MAC_PVG_MMIO=n
CONFIG_ARM_VIRT=y
# Vendor-specific virtio-blk device subclasses depend on both transports.
CONFIG_VIRTIO_PCI=y
CONFIG_VIRTIO_BLK=y
