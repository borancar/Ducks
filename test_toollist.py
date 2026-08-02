#!/usr/bin/env python3
"""Compare the ported tool-list queries against the guest's own bytes.

This is the shape every piece of the gameplay can be checked in, and it answers
the two questions that come up first:

**No emulator goes into the port.** Unicorn stays out here, in the harness, and
the port is built as a shared library so one function of it can be called. The
port does not know it is being tested.

**The two do not share memory, and cannot.** `far` is nothing here and a pointer
is eight bytes rather than four, so DGROUP and the port's globals are different
shapes - making them the same would mean undoing the port. So the harness owns
both sides and marshals: it writes the inputs into guest memory in the guest's
layout, and sets the same inputs on the library's globals in the library's
layout. Only the answers are compared.

Both functions are pure reads over the list, so nothing has to be answered while
they run - see docs/notes/in-game-frame.md on why that is true of most of the
gameplay.

    make -C reconstruct lib && venv/bin/python test_toollist.py
"""
import ctypes
import random
import struct
import sys

from unicorn.x86_const import (UC_X86_REG_AX, UC_X86_REG_CS, UC_X86_REG_DS,
                               UC_X86_REG_SP, UC_X86_REG_SS)

from native import Native

LIB = "reconstruct/libducks.so"
LIST_SEG = 0x4000                # scratch, well clear of the loaded image
STACK_SEG = 0x3000
RET = 0x30000 + 0x200            # a HLT the call returns to

TOOL_LIST = 0x1782               # far pointer to the array
TOOL_COUNT = 0x178B              # a byte
TYPE_FLAGS = 0x03A7              # one byte a type, as load_animations fills it

HAS = 0x0D55D
ANY_FLAGGED = 0x0D591


def call_guest(m, addr, args):
    """Run one of the guest's routines and hand back AX.

    A far call frame by hand: the return address first, then the arguments in
    the order Turbo C pushes them, which is right to left.
    """
    ss, sp = STACK_SEG, 0xF00
    m.uc.mem_write(RET, b"\xF4")                       # HLT
    frame = struct.pack("<HH", RET & 0xF, RET >> 4)
    for a in args:
        frame += struct.pack("<H", a & 0xFFFF)
    m.uc.mem_write(ss * 16 + sp, frame)
    m.uc.reg_write(UC_X86_REG_SS, ss)
    m.uc.reg_write(UC_X86_REG_SP, sp)
    m.uc.reg_write(UC_X86_REG_CS, m.load_seg)
    m.uc.reg_write(UC_X86_REG_DS, m.dgroup_base >> 4)   # both read DGROUP
    m.uc.emu_start(m.image_base + addr, RET, count=100000)
    return m.uc.reg_read(UC_X86_REG_AX) & 0xFFFF


def seed_guest(m, tools, flags):
    """The inputs, in the guest's layout."""
    at = LIST_SEG * 16
    m.uc.mem_write(at, b"".join(struct.pack("<h", t) for t in tools))
    m.write(m.dgroup_base + TOOL_LIST,
            struct.pack("<HH", at & 0xF, at >> 4))
    m.write(m.dgroup_base + TOOL_COUNT, bytes([len(tools)]))
    m.write(m.dgroup_base + TYPE_FLAGS, bytes(flags))


def seed_lib(lib, tools, flags):
    """The same inputs, in the library's."""
    arr = (ctypes.c_int16 * max(1, len(tools)))(*tools)
    ctypes.c_void_p.in_dll(lib, "tool_list").value = ctypes.addressof(arr)
    ctypes.c_uint8.in_dll(lib, "tool_count").value = len(tools)
    ctypes.memmove(ctypes.addressof(ctypes.c_uint8.in_dll(lib, "type_flags")),
                   bytes(flags), len(flags))
    return arr                                          # keep it alive


def main():
    m = Native("Ducks.unpacked.exe", blaster=False, max_insns=1 << 62)
    lib = ctypes.CDLL(LIB)
    lib.tool_list_has.restype = ctypes.c_int16
    lib.tool_list_has.argtypes = [ctypes.c_int16]
    lib.tool_list_any_flagged.restype = ctypes.c_int16

    rng = random.Random(20260802)
    bad = cases = 0

    for _ in range(400):
        n = rng.randrange(0, 9)
        tools = [rng.randrange(0, 112) for _ in range(n)]
        flags = [rng.randrange(0, 256) for _ in range(112)]
        want = rng.choice(tools + [rng.randrange(0, 112)]) if tools \
            else rng.randrange(0, 112)

        seed_guest(m, tools, flags)
        keep = seed_lib(lib, tools, flags)                # noqa: F841

        for label, guest, ours in (
                ("tool_list_has", call_guest(m, HAS, [want]),
                 lib.tool_list_has(want)),
                ("any_flagged", call_guest(m, ANY_FLAGGED, []),
                 lib.tool_list_any_flagged())):
            cases += 1
            if guest != (ours & 0xFFFF):
                bad += 1
                if bad < 6:
                    print(f"  MISMATCH {label}: n={n} want={want} "
                          f"guest={guest} port={ours}")
                    print(f"    tools={tools}")

    print(f"{cases} comparisons, {bad} differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
