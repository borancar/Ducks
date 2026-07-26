#!/usr/bin/env python3
"""Probe how Unicorn's UC_MODE_16 handles real-mode segmentation.

We need to know whether a write through DS lands at (DS<<4)+offset (real-mode
behaviour, what a DOS binary expects) or at just the offset (segment ignored).
Everything downstream depends on the answer, so verify before building on it.
"""
from unicorn import *
from unicorn.x86_const import *

# mov ax,0x1234 / mov ds,ax / mov bx,0x10 / mov word [bx],0xbeef / hlt
CODE = bytes.fromhex("b83412" "8ed8" "bb1000" "c707efbe" "f4")

uc = Uc(UC_ARCH_X86, UC_MODE_16)
uc.mem_map(0, 0x200000)
uc.mem_write(0x500, CODE)          # place code at 0000:0500
uc.reg_write(UC_X86_REG_CS, 0)
uc.reg_write(UC_X86_REG_IP, 0x500)
uc.reg_write(UC_X86_REG_SS, 0)
uc.reg_write(UC_X86_REG_SP, 0xfff0)

try:
    uc.emu_start(0x500, 0x500 + len(CODE))
except UcError as e:
    print("UcError:", e)

at_flat = uc.mem_read(0x12350, 2)   # (0x1234<<4) + 0x10
at_off  = uc.mem_read(0x10, 2)
print(f"DS=0x1234, wrote word to [bx=0x10]")
print(f"  linear 0x12350 (seg<<4 + off) = {at_flat.hex()}")
print(f"  linear 0x00010 (off only)     = {at_off.hex()}")
if at_flat == b"\xef\xbe":
    print("=> REAL-MODE segmentation works: seg<<4 + off")
elif at_off == b"\xef\xbe":
    print("=> segment IGNORED - would need manual segment handling")
else:
    print("=> write landed somewhere unexpected")

# Also confirm a far jump updates CS as expected, and that reads of CS work.
print("CS after run:", hex(uc.reg_read(UC_X86_REG_CS)),
      "IP:", hex(uc.reg_read(UC_X86_REG_IP)))
