#!/usr/bin/env python3
"""Check the C particle step against the original's, 0x0a956.

There is no native.py twin for this one - it was never on the demo path, because
a demo's ducks are not blown up - so the chain here is short and direct:

    guest 0x0a956  ==  game.c particles_step

Both sides are given the same made-up pool, the same level size, the same
terrain and the same FLYING BLOOD setting, each runs one frame, and then the
pool, the count and the terrain are compared byte for byte. Nothing is asserted
about what a particle ought to do.

Two things about the terrain are modelled rather than measured here, and both
come from run-level.md: a row is its own allocation, so one byte past its end is
the allocator's header and reads as 0x01, and one row past the table is
unknowable and stands in as 0. The retire tests are `>` and not `>=`, so a
particle sitting exactly on the far edge does index one past - which is why the
gap bytes have to be right for the two sides to agree at all.

    make -C reconstruct lib && venv/bin/python test_particles.py
"""
import ctypes
import random
import struct
import sys

from unicorn.x86_const import (UC_X86_REG_CS, UC_X86_REG_DS, UC_X86_REG_ES,
                               UC_X86_REG_SP, UC_X86_REG_SS)

import native
from native import Native

LIB = "reconstruct/libducks.so"
STACK_SEG = 0x3000
SCRATCH_SEG = 0x4000
RET = 0x30000 + 0x200
GAME_SEG = 0x4CA0

ROWS_OFF = 0x0000                # the row table: (h + 1) far pointers
TERR_OFF = 0x1000                # the rows themselves, each with a header byte
POOL_OFF = 0x8000                # the particle pool


def guest_call(m, off):
    """0x0a956 takes no arguments; it reads the pool through DGROUP."""
    ss, sp = STACK_SEG, 0xF00
    m.uc.mem_write(RET, b"\xF4")
    m.uc.mem_write(ss * 16 + sp, struct.pack("<HH", RET & 0xF, RET >> 4))
    m.uc.reg_write(UC_X86_REG_SS, ss)
    m.uc.reg_write(UC_X86_REG_SP, sp)
    m.uc.reg_write(UC_X86_REG_CS, (m.image_base + GAME_SEG) >> 4)
    m.uc.reg_write(UC_X86_REG_DS, m.dgroup_base >> 4)
    m.uc.reg_write(UC_X86_REG_ES, m.dgroup_base >> 4)
    saved, m.natives = m.natives, {}
    try:
        m.uc.emu_start(m.image_base + off, RET, count=2_000_000)
    finally:
        m.natives = saved


def make_case(rng):
    """One pool, one level, one terrain. Coordinates deliberately overshoot the
    level on both axes so the retire arms are reached as often as the rest."""
    w = rng.randrange(20, 60)
    h = rng.randrange(12, 40)
    n = rng.randrange(1, 24)
    terrain = [bytes(rng.choice((0, 0, 0, rng.randrange(1, 256)))
                     for _ in range(w)) for _ in range(h)]
    pool = []
    for _ in range(n):
        pool.append((rng.randrange(-40, w * 8 + 40),      # x, 1/8 pixel
                     rng.randrange(-40, h * 8 + 40),      # y
                     rng.randrange(-16, 16),              # vx
                     rng.randrange(-24, 24),              # vy
                     rng.randrange(0, 256),               # colour
                     rng.choice((1, 2)),                  # f0d, the life
                     rng.choice((0, 1, 1, 1))))           # f0e
    return w, h, terrain, pool, rng.choice((0, 1))


def pack(pool):
    return b"".join(struct.pack("<iihhBBh", *p) for p in pool)


def run_guest(m, w, h, terrain, pool, blood):
    base = SCRATCH_SEG * 16
    # each row preceded by its own header byte, which is what a read one past
    # the previous row's end lands on
    rowaddr = []
    at = TERR_OFF
    for r in terrain:
        m.uc.mem_write(base + at, b"\x01" + r)
        rowaddr.append(at + 1)
        at += len(r) + 1
    m.uc.mem_write(base + at, b"\x00" * (w + 1))     # the row past the table
    rowaddr.append(at + 1)
    m.uc.mem_write(base + ROWS_OFF,
                   b"".join(struct.pack("<HH", a, SCRATCH_SEG) for a in rowaddr))
    m.uc.mem_write(base + POOL_OFF, pack(pool))

    d = m.dgroup_base
    m.uc.mem_write(d + 0x16F5, struct.pack("<HH", ROWS_OFF, SCRATCH_SEG))
    m.uc.mem_write(d + 0x18C1, struct.pack("<HH", POOL_OFF, SCRATCH_SEG))
    m.uc.mem_write(d + 0x18CD, struct.pack("<h", len(pool)))
    m.uc.mem_write(d + 0x18CF, struct.pack("<h", len(pool) + 64))
    m.uc.mem_write(d + 0x1701, struct.pack("<h", w))
    m.uc.mem_write(d + 0x1703, struct.pack("<h", h))
    m.uc.mem_write(d + 0x04F6, struct.pack("<h", blood))

    guest_call(m, 0x0A956)

    count = struct.unpack("<h", m.uc.mem_read(d + 0x18CD, 2))[0]
    out = bytes(m.uc.mem_read(base + POOL_OFF, max(count, 0) * 16))
    soil = [bytes(m.uc.mem_read(SCRATCH_SEG * 16 + a, w)) for a in rowaddr[:h]]
    return count, out, soil


def run_c(lib, w, h, terrain, pool, blood):
    rows = (ctypes.POINTER(ctypes.c_uint8) * (h + 1))()
    keep = []
    for r in list(terrain) + [bytes(w)]:
        b = (ctypes.c_uint8 * w).from_buffer_copy(r)
        keep.append(b)
        rows[len(keep) - 1] = ctypes.cast(b, ctypes.POINTER(ctypes.c_uint8))
    backdrop = D.Desc.in_dll(lib, "backdrop")
    backdrop.rows = rows
    backdrop.w, backdrop.h = w, h

    buf = ctypes.create_string_buffer(pack(pool), len(pool) * 16 + 1024)
    ctypes.c_void_p.in_dll(lib, "particle_array").value = ctypes.addressof(buf)
    ctypes.c_int16.in_dll(lib, "particle_cap").value = len(pool) + 64
    cnt = ctypes.c_int16.in_dll(lib, "particle_count")
    cnt.value = len(pool)
    ctypes.c_int16.in_dll(lib, "level_w").value = w
    ctypes.c_int16.in_dll(lib, "level_h").value = h
    (ctypes.c_int16 * 16).in_dll(lib, "settings")[1] = blood

    lib.particles_step()

    out = bytes(buf.raw[:max(cnt.value, 0) * 16])
    soil = [bytes(keep[y]) for y in range(h)]
    return cnt.value, out, soil


def main():
    global D
    import dump_level as D                                  # noqa: E402

    lib = ctypes.CDLL(LIB)
    m = Native("Ducks.unpacked.exe", blaster=False, max_insns=1 << 62)
    rng = random.Random(0x0A956)

    cases = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    bad = 0
    for case in range(cases):
        w, h, terrain, pool, blood = make_case(rng)
        gc, gp, gs = run_guest(m, w, h, terrain, pool, blood)
        cc, cp, cs = run_c(lib, w, h, terrain, pool, blood)
        if (gc, gp, gs) != (cc, cp, cs):
            bad += 1
            if bad <= 5:
                print(f"  case {case}: {w}x{h}, {len(pool)} particle(s), "
                      f"blood={blood}")
                if gc != cc:
                    print(f"    count   guest={gc} c={cc}")
                elif gp != cp:
                    i = next(k for k in range(len(gp)) if gp[k] != cp[k])
                    print(f"    record  first byte {i} (particle {i // 16}, "
                          f"+{i % 16:#04x}): guest={gp[i]:#04x} c={cp[i]:#04x}")
                else:
                    y = next(k for k in range(h) if gs[k] != cs[k])
                    x = next(k for k in range(w) if gs[y][k] != cs[y][k])
                    print(f"    terrain ({x},{y}): guest={gs[y][x]:#04x} "
                          f"c={cs[y][x]:#04x}")
    print(f"{cases} case(s), {bad} differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
