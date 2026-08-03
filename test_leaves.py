#!/usr/bin/env python3
"""Check the C transcriptions of run_level's leaves against the Python natives.

The natives in native.py were byte-compared against the guest - on snapshots
under --verify, and on made-up inputs in test_gameplay.py. The C in game.c was
then written from them, and a transcription is a second reading: it can be wrong
in ways the first one was not. This closes that gap, so the chain is

    guest  ==  native.py  ==  game.c

with the middle link already established and only the right-hand one new here.

Both sides run on the same made-up state - the machine's DGROUP for the native,
the library's globals for the C - and only the answers are compared. Nothing is
asserted about what those should be.

    make -C reconstruct lib && venv/bin/python test_leaves.py
"""
import ctypes
import random
import struct
import sys

from native import Native

LIB = "reconstruct/libducks.so"
ARGS_SEG = 0x3800
SCRATCH_SEG = 0x4000


def i32(lib, name):
    return ctypes.c_int32.in_dll(lib, name)


def i16(lib, name):
    return ctypes.c_int16.in_dll(lib, name)


def u8(lib, name):
    return ctypes.c_uint8.in_dll(lib, name)


# ------------------------------------------------------------------- cases


def case_scroll_follow(m, lib, rng):
    d = m.dgroup_base
    vw, vh = rng.randrange(16, 321), rng.randrange(16, 201)
    ww = rng.choice([vw, vw + rng.randrange(0, 400), rng.randrange(16, 640)])
    wh = rng.choice([vh, vh + rng.randrange(0, 400), rng.randrange(16, 480)])
    sx, sy = rng.randrange(-64, 512), rng.randrange(-64, 512)
    sh, sm = rng.randrange(0, 6), rng.randrange(0, 2)
    x, y = rng.randrange(-200, 700), rng.randrange(-200, 700)

    m.uc.mem_write(d + 0x1701, struct.pack("<hh", ww, wh))
    m.uc.mem_write(d + 0x1735, struct.pack("<hh", vw, vh))
    m.uc.mem_write(d + 0x1739, struct.pack("<ii", sx, sy))
    m.uc.mem_write(d + 0x18F5, bytes([sh]))
    m.uc.mem_write(d + 0x04FA, struct.pack("<h", sm))
    m.uc.mem_write(ARGS_SEG * 16, struct.pack("<ii", x, y))
    m.natives[0x0600D][1](m, ARGS_SEG * 16)
    theirs = struct.unpack("<ii", m.uc.mem_read(d + 0x1739, 8))

    i16(lib, "level_w").value, i16(lib, "level_h").value = ww, wh
    i16(lib, "view_w").value, i16(lib, "view_h").value = vw, vh
    i32(lib, "scroll_x").value, i32(lib, "scroll_y").value = sx, sy
    u8(lib, "scroll_shift").value = sh
    i16(lib, "scroll_smooth").value = sm
    lib.scroll_follow(ctypes.c_int32(x), ctypes.c_int32(y))
    ours = (i32(lib, "scroll_x").value, i32(lib, "scroll_y").value)

    return "scroll_follow", theirs, ours, f"vw={vw} ww={ww} shift={sh} sm={sm}"


def case_scroll_axis_snap(m, lib, rng):
    focus = rng.randrange(-5000, 5000)
    extent = rng.randrange(-800, 800)
    span = rng.randrange(-32, 4096)
    start = rng.randrange(-4096, 4096)

    p = SCRATCH_SEG * 16
    m.uc.mem_write(p, struct.pack("<i", start))
    m.uc.mem_write(ARGS_SEG * 16,
                   struct.pack("<iiHHh", focus, extent, 0, SCRATCH_SEG, span))
    m.natives[0x05F15][1](m, ARGS_SEG * 16)
    theirs = struct.unpack("<i", m.uc.mem_read(p, 4))

    out = ctypes.c_int32(start)
    lib.scroll_axis_snap(ctypes.c_int32(focus), ctypes.c_int32(extent),
                         ctypes.byref(out), ctypes.c_int16(span))
    return ("scroll_axis_snap", theirs, (out.value,),
            f"focus={focus} extent={extent} span={span}")


def case_bg_scroll_reset(m, lib, rng):
    d = m.dgroup_base
    drift = rng.randrange(0, 256)
    bw, bh = rng.randrange(1, 640), rng.randrange(1, 480)

    m.uc.mem_write(d + 0x202C, bytes([drift]))
    m.uc.mem_write(d + 0x1717, struct.pack("<HH", bw, bh))
    m.uc.mem_write(d + 0x177E, b"\xAA\xBB\xCC\xDD")
    m.natives[0x0D6C3][1](m, ARGS_SEG * 16)
    theirs = tuple(m.uc.mem_read(d + 0x177E, 4))

    u8(lib, "bg_drift").value = drift
    i16(lib, "bg_w").value, i16(lib, "bg_h").value = bw, bh
    lib.bg_scroll_reset()
    ours = (u8(lib, "bg_scroll_x").value, u8(lib, "bg_scroll_y").value,
            u8(lib, "bg_step_x").value, u8(lib, "bg_step_y").value)
    return "bg_scroll_reset", theirs, ours, f"drift={drift} bw={bw} bh={bh}"


def case_palette_apply_gamma(m, lib, rng):
    d = m.dgroup_base
    src = rng.randbytes(768)
    g = rng.randrange(0, 0x20)

    m.uc.mem_write(SCRATCH_SEG * 16, src)
    m.uc.mem_write(d + 0x1721, struct.pack("<HH", 0, SCRATCH_SEG))
    m.uc.mem_write(d + 0x1FD5, bytes([g]))
    m.natives[0x0B0C5][1](m, ARGS_SEG * 16)
    theirs = bytes(m.uc.mem_read(d + 0x10E1, 768))

    buf = (ctypes.c_uint8 * 768).from_buffer_copy(src)
    ctypes.c_void_p.in_dll(lib, "current_buffer").value = ctypes.addressof(buf)
    u8(lib, "gamma_level").value = g
    lib.palette_apply_gamma()
    ours = bytes(ctypes.cast(ctypes.addressof(ctypes.c_uint8.in_dll(
        lib, "palette_stored")), ctypes.POINTER(ctypes.c_uint8 * 768))[0])
    return "palette_apply_gamma", theirs, ours, f"gamma={g}"


class Entity(ctypes.Structure):
    """The port's entity_t. Not the guest's: `far` is nothing here and the
    compiler pads, so this is 42 bytes where the original is 41. That is why
    every comparison below is field by field and not a memcmp."""
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32),
                ("unread", ctypes.c_uint8 * 4),
                ("prev_x", ctypes.c_int32), ("prev_y", ctypes.c_int32),
                ("f14", ctypes.c_int8), ("f15", ctypes.c_uint8),
                ("f16", ctypes.c_uint8), ("param", ctypes.c_int16),
                ("unread2", ctypes.c_uint8 * 6), ("frame", ctypes.c_int16),
                ("f21", ctypes.c_int16), ("f23", ctypes.c_int16),
                ("type", ctypes.c_int16), ("f27", ctypes.c_int16)]


class Scene(ctypes.Structure):
    _fields_ = [("capacity", ctypes.c_int16), ("count", ctypes.c_int16),
                ("flag", ctypes.c_int16), ("unread6", ctypes.c_int16),
                ("entities", ctypes.POINTER(Entity))]


# guest offset, ctypes name, struct code - every field either side touches
FIELDS = [(0x00, "x", "<i"), (0x04, "y", "<i"), (0x08, None, None),
          (0x0C, "prev_x", "<i"), (0x10, "prev_y", "<i"),
          (0x14, "f14", "<b"), (0x15, "f15", "<B"), (0x16, "f16", "<B"),
          (0x17, "param", "<h"), (0x1F, "frame", "<h"), (0x21, "f21", "<h"),
          (0x23, "f23", "<h"), (0x25, "type", "<h"), (0x27, "f27", "<h")]


def case_entity_copy(m, lib, rng):
    n = rng.randrange(1, 8)
    src, dst = rng.randrange(0, n), rng.randrange(0, n)
    blob = rng.randbytes(n * 0x29)

    hdr, ents = SCRATCH_SEG * 16, 0x100
    m.uc.mem_write(hdr, struct.pack("<hhhhHH", n, n, 0, 0, ents, SCRATCH_SEG))
    m.uc.mem_write(SCRATCH_SEG * 16 + ents, blob)
    m.uc.mem_write(ARGS_SEG * 16, struct.pack("<HHhh", 0, SCRATCH_SEG, src, dst))
    m.natives[0x06F4F][1](m, ARGS_SEG * 16)
    theirs = []
    for i in range(n):
        e = SCRATCH_SEG * 16 + ents + i * 0x29
        theirs += [struct.unpack(f, m.uc.mem_read(e + o, struct.calcsize(f)))[0]
                   for o, nm, f in FIELDS if nm]

    arr = (Entity * n)()
    for i in range(n):
        for o, nm, f in FIELDS:
            if nm:
                setattr(arr[i], nm,
                        struct.unpack(f, blob[i * 0x29 + o:
                                              i * 0x29 + o
                                              + struct.calcsize(f)])[0])
    sc = Scene(n, n, 0, 0, arr)
    lib.entity_copy(ctypes.byref(sc), ctypes.c_int16(src), ctypes.c_int16(dst))
    ours = [getattr(arr[i], nm) for i in range(n) for o, nm, f in FIELDS if nm]

    return "entity_copy", theirs, ours, f"n={n} {src}->{dst}"


def case_particles_spawn(m, lib, rng):
    """Both sides draw from the same seed, so the draw ORDER is checked too."""
    d = m.dgroup_base
    cap = rng.randrange(1, 40)
    live = rng.randrange(0, cap + 1)
    cols = rng.randbytes(8)
    seed = rng.randrange(0, 1 << 32)
    x, y, n = (rng.randrange(-300, 600), rng.randrange(-300, 600),
               rng.randrange(0, 45))

    m.uc.mem_write(SCRATCH_SEG * 16, bytes(cap * 16))
    m.uc.mem_write(d + 0x18C1, struct.pack("<HH", 0, SCRATCH_SEG))
    m.uc.mem_write(d + 0x18CD, struct.pack("<h", live))
    m.uc.mem_write(d + 0x18CF, struct.pack("<h", cap))
    m.uc.mem_write(d + 0x18C5, cols)
    m.uc.mem_write(d + 0x3006, struct.pack("<I", seed))
    m.uc.mem_write(ARGS_SEG * 16, struct.pack("<hhh", x, y, n))
    m.natives[0x077AE][1](m, ARGS_SEG * 16)
    theirs = (bytes(m.uc.mem_read(SCRATCH_SEG * 16, cap * 16))
              + bytes(m.uc.mem_read(d + 0x18CD, 2))
              + bytes(m.uc.mem_read(d + 0x3006, 4)))

    pool = (ctypes.c_uint8 * (cap * 16))()
    ctypes.c_void_p.in_dll(lib, "particle_array").value = ctypes.addressof(pool)
    i16(lib, "particle_count").value = live
    i16(lib, "particle_cap").value = cap
    ctypes.memmove(ctypes.addressof(ctypes.c_uint8.in_dll(
        lib, "particle_colours")), cols, 8)
    ctypes.c_uint32.in_dll(lib, "rand_seed").value = seed
    lib.particles_spawn(ctypes.c_int16(x), ctypes.c_int16(y),
                        ctypes.c_int16(n))
    ours = (bytes(pool)
            + struct.pack("<h", i16(lib, "particle_count").value)
            + struct.pack("<I", ctypes.c_uint32.in_dll(lib,
                                                       "rand_seed").value))
    return "particles_spawn", theirs, ours, f"cap={cap} live={live} n={n}"


CASES = [case_scroll_follow, case_scroll_axis_snap, case_bg_scroll_reset,
         case_palette_apply_gamma, case_entity_copy, case_particles_spawn]


def main():
    m = Native("Ducks.unpacked.exe", blaster=False, max_insns=1 << 62)
    lib = ctypes.CDLL(LIB)
    rng = random.Random(20260803)

    bad = 0
    counts = {}
    for _ in range(400):
        for make in CASES:
            name, theirs, ours, how = make(m, lib, rng)
            counts[name] = counts.get(name, 0) + 1
            if tuple(theirs) != tuple(ours):
                bad += 1
                if bad < 8:
                    print(f"  MISMATCH {name}: {how}")
                    print(f"    native.py {theirs}")
                    print(f"    game.c    {ours}")
    for name in sorted(counts):
        print(f"  {name:<22} {counts[name]} cases")
    print(f"{sum(counts.values())} comparisons, {bad} differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
