#!/usr/bin/env python3
"""blast_terrain (0x0751b) against the guest, pixel for pixel.

The bomb and the balloon are the same arm of tool_use: leave a type 0x17 in
scene 1 and cut a hole with the bomb's own first sprite. The hole is the only
thing in the game that turns solid backdrop into empty backdrop, so if it left
anything behind, everything that probes terrain would find it - a bridge end
walking along, for one, which stops dead on the first non-zero pixel.

Both sides get the same backdrop bytes, the same sprite and the same point, and
every pixel of the result is compared. Nothing is asserted about what the hole
should look like; only that the two agree.

Deliberately no solids: blast_terrain restamps all of them afterwards, and
stamp_solid is a separate routine with its own clipping. Comparing that too
would mean a difference could come from either, which is how a probe stops being
able to say what it found.

    make -C reconstruct lib && venv/bin/python test_blast.py
"""
import ctypes
import random
import struct
import sys

from unicorn.x86_const import (UC_X86_REG_CS, UC_X86_REG_DS, UC_X86_REG_ES,
                               UC_X86_REG_SP, UC_X86_REG_SS)

from native import Native

LIB = "reconstruct/libducks.so"
BLAST = 0x0751B
GAME_SEG = 0x4CA0
STACK_SEG, RET = 0x3000, 0x30000 + 0x200
SCRATCH = 0x40000               # guest side: backdrop rows, the sprite, its pixels

W, H = 96, 64                   # the level, in pixels
SPR_W, SPR_H = 21, 17           # the hole's sprite


def guest_blast(m, rows_at, x, y):
    """Run the guest's 0x0751b and read its backdrop back."""
    g = m.dgroup_base
    ss, sp = STACK_SEG, 0xF00
    m.uc.mem_write(RET, b"\xF4")
    m.uc.mem_write(ss * 16 + sp,
                   struct.pack("<HHhhh", RET & 0xF, RET >> 4, x, y, 0))
    m.uc.reg_write(UC_X86_REG_SS, ss)
    m.uc.reg_write(UC_X86_REG_SP, sp)
    m.uc.reg_write(UC_X86_REG_CS, (m.image_base + GAME_SEG) >> 4)
    m.uc.reg_write(UC_X86_REG_DS, g >> 4)
    m.uc.reg_write(UC_X86_REG_ES, g >> 4)
    saved, m.natives = m.natives, {}
    try:
        m.uc.emu_start(m.image_base + BLAST, RET, count=20_000_000)
    finally:
        m.natives = saved
    return [bytes(m.read(rows_at + r * W, W)) for r in range(H)]


def main():
    lib = ctypes.CDLL(LIB)
    m = Native("Ducks.unpacked.exe", blaster=False, max_insns=1 << 62)
    g = m.dgroup_base
    rng = random.Random(BLAST)

    # ---- the guest's side of the state, all in scratch memory
    rows_at = SCRATCH                      # H rows of W bytes
    table_at = rows_at + H * W             # H far pointers
    spr_at = table_at + H * 4              # one 14-byte sprite record
    pix_at = spr_at + 0x10                 # its pixels

    for r in range(H):
        m.write(table_at + r * 4, struct.pack("<HH", (rows_at + r * W) & 0xF,
                                              (rows_at + r * W) >> 4))
    m.write(g + 0x16F5, struct.pack("<HH", table_at & 0xF, table_at >> 4))
    m.write(g + 0x1701, struct.pack("<hh", W, H))            # level_w, level_h
    m.write(g + 0x2031, b"\x00")                             # solid_count
    m.write(g + 0x18EB, struct.pack("<HH", spr_at & 0xF, spr_at >> 4))

    # ---- the port's side
    rows = (ctypes.POINTER(ctypes.c_uint8) * H)()
    keep = [(ctypes.c_uint8 * W)() for _ in range(H)]
    for r in range(H):
        rows[r] = ctypes.cast(keep[r], ctypes.POINTER(ctypes.c_uint8))

    class Desc(ctypes.Structure):
        _fields_ = [("rows", ctypes.c_void_p),
                    ("w", ctypes.c_int16), ("h", ctypes.c_int16)]

    class Sprite(ctypes.Structure):
        _fields_ = [("w", ctypes.c_int16), ("h", ctypes.c_int16),
                    ("ox", ctypes.c_int16), ("oy", ctypes.c_int16),
                    ("unused", ctypes.c_int16), ("pixels", ctypes.c_void_p)]

    class Table(ctypes.Structure):
        _fields_ = [("count", ctypes.c_int16), ("base", ctypes.c_void_p)]

    desc = Desc.in_dll(lib, "backdrop")
    desc.rows, desc.w, desc.h = ctypes.addressof(rows), W, H
    ctypes.c_int16.in_dll(lib, "level_w").value = W
    ctypes.c_int16.in_dll(lib, "level_h").value = H
    ctypes.c_int16.in_dll(lib, "solid_count").value = 0
    table = Table.in_dll(lib, "sprite_table")
    sprite = Sprite()
    table.count, table.base = 1, ctypes.addressof(sprite)
    lib.blast_terrain.argtypes = [ctypes.c_int16] * 3

    bad = cases = 0
    for n in range(200):
        # a sprite with holes in it, so "erase where the sprite has a pixel"
        # is actually being tested rather than "erase a rectangle"
        ox, oy = rng.randrange(0, SPR_W), rng.randrange(0, SPR_H)
        pix = bytes(rng.randrange(0, 2) * rng.randrange(1, 256)
                    for _ in range(SPR_W * SPR_H))
        m.write(spr_at, struct.pack("<hhhhhHH", SPR_W, SPR_H, ox, oy, 0,
                                    pix_at & 0xF, pix_at >> 4))
        m.write(pix_at, pix)
        pbuf = (ctypes.c_uint8 * len(pix)).from_buffer_copy(pix)
        sprite.w, sprite.h, sprite.ox, sprite.oy = SPR_W, SPR_H, ox, oy
        sprite.pixels = ctypes.addressof(pbuf)

        start = [bytes(rng.randrange(0, 256) for _ in range(W)) for _ in range(H)]
        for r in range(H):
            m.write(rows_at + r * W, start[r])
            ctypes.memmove(keep[r], start[r], W)

        # points well inside, on each edge, and off each edge
        x = rng.choice([rng.randrange(0, W), rng.randrange(-SPR_W, 0),
                        rng.randrange(W - SPR_W, W + SPR_W)])
        y = rng.choice([rng.randrange(0, H), rng.randrange(-SPR_H, 0),
                        rng.randrange(H - SPR_H, H + SPR_H)])

        want = guest_blast(m, rows_at, x, y)
        lib.blast_terrain(x, y, 0)
        got = [bytes(keep[r]) for r in range(H)]

        cases += 1
        if got != want:
            bad += 1
            r = next(i for i in range(H) if got[i] != want[i])
            c = next(j for j in range(W) if got[r][j] != want[r][j])
            print(f"  case {n}: blast at ({x},{y}) origin ({ox},{oy}) "
                  f"differs at row {r} col {c}: guest={want[r][c]} c={got[r][c]}")
            if bad > 4:
                break

    print(f"{cases} blast(s) compared C against guest, {bad} differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
