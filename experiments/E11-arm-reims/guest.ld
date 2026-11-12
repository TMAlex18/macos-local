ENTRY(_start)
SECTIONS {
  . = 0x00100000;
  .text : { *(.text.start) *(.text*) }
  .rodata : { *(.rodata*) }
  .data : { *(.data*) }
  .bss : {
    . = ALIGN(16);
    __bss_start = .;
    *(.bss*) *(COMMON)
    . = ALIGN(16);
    __bss_end = .;
  }
  /DISCARD/ : { *(.comment) *(.eh_frame*) }
}
