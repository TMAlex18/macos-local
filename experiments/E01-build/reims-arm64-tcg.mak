# Experimental ARM64/TCG VMApple with Modern Reims apple-gfx-mmio.
# Does NOT prove guest page interpretation or Metal compatibility.
CONFIG_ARM_VIRT=y
CONFIG_VMAPPLE=y
CONFIG_VIRTIO_PCI=y
CONFIG_VIRTIO_BLK=y
CONFIG_VIRTIO_NET=y
CONFIG_MAC_PVG_MMIO=y
# Input-capable build; experiments retain -nodefaults and opt in explicitly.
CONFIG_USB_XHCI_PCI=y
CONFIG_USB_HID=y
CONFIG_USB_TABLET_VMAPPLE=y
