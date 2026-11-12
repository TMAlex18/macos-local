# Deliberate first build: ARM TCG without Reims or VMApple.
# Build confirms system emulator/toolchain; not macOS compatibility.
CONFIG_ARM_VIRT=y
CONFIG_VMAPPLE=n
CONFIG_REIMS_VGPU=n
