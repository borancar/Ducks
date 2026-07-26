#!/usr/bin/env python3
"""
Locate the sound-availability logic in the unpacked executable.

Guessing at mechanisms has been repeatedly wrong, so instead find the code that
references the sound-related strings and read what it actually tests. Ducks is a
Borland small/large-model DOS program: string constants live in DGROUP and are
referenced by 16-bit offset, so scanning the code for those offsets as immediate
operands finds the referencing sites.

Usage:
    python find_sound_code.py ["some string"]
"""
import struct
import sys

import capstone

EXE = "Ducks.unpacked.exe"
TARGETS = [
    b"Press ESC to skip sound check",
    b"Error initializing sound card",
    b"Base IO %Xh",
    b"Invalid or non-existant BLASTER",
    b"BLASTER",
]
if len(sys.argv) > 1:
    TARGETS = [sys.argv[1].encode()]

data = open(EXE, "rb").read()
(cblp, cp, crlc, cparhdr, mn, mx, ss, sp, csum, ip, cs,
 lfarlc, ovno) = struct.unpack_from("<13H", data, 2)
img = data[cparhdr * 16:]
dgroup = struct.unpack_from("<H", img, cs * 16 + ip + 1)[0]
code_end = dgroup * 16
print(f"image {len(img)} bytes; DGROUP {dgroup:#06x}; "
      f"code 0..{code_end:#x}; data {code_end:#x}..{len(img):#x}\n")

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_16)

for target in TARGETS:
    pos = img.find(target)
    if pos < 0:
        print(f"=== {target!r}: NOT FOUND ===\n")
        continue
    dg_off = pos - code_end
    print(f"=== {target!r} ===")
    print(f"  image offset {pos:#07x}, DGROUP offset {dg_off:#06x}")

    needle = struct.pack("<H", dg_off & 0xFFFF)
    hits = []
    start = 0
    while True:
        i = img.find(needle, start, code_end)
        if i < 0:
            break
        hits.append(i)
        start = i + 1
    print(f"  referenced as an immediate at {len(hits)} code sites: "
          f"{[hex(h) for h in hits[:12]]}")

    for h in hits[:4]:
        lo = max(0, h - 48)
        print(f"\n  --- disassembly around {h:#07x} ---")
        for insn in md.disasm(img[lo:h + 48], lo):
            mark = "   <== string offset here" if lo <= h < insn.address + insn.size and insn.address <= h else ""
            print(f"    {insn.address:06x}: {insn.bytes.hex():<14} "
                  f"{insn.mnemonic} {insn.op_str}{mark}")
    print()
