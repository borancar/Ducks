#!/usr/bin/env python3
"""Check the entity-retirement path without needing it to happen in a level.

A type-5 entity whose y has gone negative - a balloon that has floated off the
top - is retired by 0x78d4, which the native now reproduces instead of declining.
Waiting for that to occur in play is not something anyone can arrange on demand,
so this drives the guest's own 35 bytes on a synthetic entity and compares the
result against what the native does to the same bytes.

    venv/bin/python test_retire.py
"""
import struct
import sys

from unicorn.x86_const import *

from native import Native

ENT_SEG = 0x4000                 # scratch, well clear of the loaded image
STACK_SEG = 0x3000
RET = 0x30000 + 0x200            # a HLT the call returns to


def call_guest_retire(m, ent_lin, new_type):
    """Run the real 0x78d4 against an entity, exactly as the game calls it."""
    ss, sp = STACK_SEG, 0xF00
    m.uc.mem_write(RET, b"\xF4")                       # HLT
    frame = struct.pack("<HHHHH", RET & 0xF, RET >> 4,  # return IP:CS
                        ent_lin & 0xF, ent_lin >> 4,    # far ptr to the entity
                        new_type)
    m.uc.mem_write(ss * 16 + sp, frame)
    m.uc.reg_write(UC_X86_REG_SS, ss)
    m.uc.reg_write(UC_X86_REG_SP, sp)
    m.uc.reg_write(UC_X86_REG_CS, m.load_seg)
    m.uc.emu_start(m.image_base + 0x78D4, RET, count=200)


def native_retire(m, ent_lin, new_type):
    """What native_draw_entities does for the same case."""
    if struct.unpack("<H", m.read(ent_lin + 0x25, 2))[0] != new_type:
        m.write(ent_lin + 0x25, struct.pack("<H", new_type))
        m.write(ent_lin + 0x1F, b"\x00\x00")


def main():
    m = Native("Ducks.unpacked.exe", blaster=False, max_insns=1 << 62)
    ent = ENT_SEG * 16
    bad = 0

    for label, start_type, sub, new_type in (
            ("type 5 retired to 0",     5, 0x1234, 0),
            ("already type 0, no-op",   0, 0x1234, 0),
            ("type 0x26 retired",    0x26, 0x0002, 0),
    ):
        original = bytes(range(0x29))                  # recognisable filler
        seed = bytearray(original)
        struct.pack_into("<H", seed, 0x25, start_type)
        struct.pack_into("<H", seed, 0x1F, sub)

        m.uc.mem_write(ent, bytes(seed))
        call_guest_retire(m, ent, new_type)
        guest = bytes(m.uc.mem_read(ent, 0x29))

        m.uc.mem_write(ent, bytes(seed))
        native_retire(m, ent, new_type)
        ours = bytes(m.uc.mem_read(ent, 0x29))

        ok = guest == ours
        bad += not ok
        print(f"  {label:24} {'match' if ok else 'MISMATCH'}"
              f"  type {struct.unpack_from('<H', guest, 0x25)[0]:#06x}"
              f" sub {struct.unpack_from('<H', guest, 0x1F)[0]:#06x}")
        if not ok:
            for i in range(0x29):
                if guest[i] != ours[i]:
                    print(f"      +{i:#04x}: guest {guest[i]:#04x} "
                          f"native {ours[i]:#04x}")

    print("all match" if not bad else f"{bad} case(s) differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
