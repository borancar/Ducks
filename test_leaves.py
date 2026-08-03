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


CASES = [case_scroll_follow, case_scroll_axis_snap, case_bg_scroll_reset,
         case_palette_apply_gamma]


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
