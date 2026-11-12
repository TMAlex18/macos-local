.arch armv8.3-a
.text
.macro op name, insn
.global \name,\name\()_instruction
\name:
\name\()_instruction:
    \insn x0,x1
    ret
.endm
op do_pacia,pacia
op do_pacib,pacib
op do_pacda,pacda
op do_pacdb,pacdb
op do_autia,autia
op do_autib,autib
op do_autda,autda
op do_autdb,autdb
.global do_xpaci
do_xpaci:
    xpaci x0
    ret
.global do_pacga
do_pacga:
    pacga x0,x0,x1
    ret
