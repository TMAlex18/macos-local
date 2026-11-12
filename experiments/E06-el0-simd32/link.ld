ENTRY(_start)
SECTIONS {
  . = 0x00100000;
  .text : { KEEP(*(.text.boot)) *(.vectors) *(.text*) }
  .rodata : { *(.rodata*) }
  .data : { *(.data*) }
  . = ALIGN(16384);
  __user_page_start = .;
  .user : { KEEP(*(.user.text)) }
  __user_code_end = .;
  ASSERT(__user_code_end - __user_page_start <= 16384, "user code exceeds one guest page")
  . = ALIGN(16384);
  .bss (NOLOAD) : {
    __bss_start = .;
    *(.bss*) *(COMMON)
    . = ALIGN(8);
    __bss_end = .;
  }
  ASSERT(__bss_end < 0x00200000, "payload exceeds firmware RAM")
  /DISCARD/ : { *(.comment) *(.eh_frame*) *(.note*) }
}
