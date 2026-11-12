ENTRY(_start)
SECTIONS {
  . = 0x00100000;
  .text : { KEEP(*(.text.boot)) *(.vectors) *(.text*) }
  .rodata : { *(.rodata*) }
  .data : { *(.data*) }
  . = ALIGN(16384);
  .bss (NOLOAD) : {
    __bss_start = .;
    *(.bss*) *(COMMON)
    . = ALIGN(8);
    __bss_end = .;
  }
  ASSERT(__bss_end < 0x00200000, "payload exceeds VMApple firmware RAM")
  /DISCARD/ : { *(.comment) *(.eh_frame*) *(.note*) }
}
