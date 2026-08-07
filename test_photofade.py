#!/usr/bin/env python3
"""The photograph fade at 0x0f8bd, against the guest.

cutscene_photos slams the DAC to white and then calls this once a frame. It is
the only fade in the game that goes *from* white rather than to black, and the
only palette work that reaches the hardware through the ports rather than
through palette_upload - which is why a no-op `outp` in the SDL backend left the
photographs wearing whatever palette the cutscene before them had set.

Two things are checked, and they are not equally strong:

- **The state machine, C against guest.** fade_level and fade_direction after
  one call, over every level from -1 to 16. Both sides are set up the same way
  and both are read back, so this is a real comparison.
- **The ramp.** The guest's own OUT writes are collected by hooking the
  instruction, and compared against the expression the C contains. That is the
  guest against a Python twin of the C, not against the C itself: `palette` is
  static in sdl_io.c, so nothing can read back what the port's `outp` produced.
  Same limit as fb_back, and recorded for the same reason.

    make -C reconstruct lib && venv/bin/python test_photofade.py
"""
import ctypes
import random
import struct
import sys

from unicorn import UC_HOOK_INSN
from unicorn.x86_const import (UC_X86_INS_OUT, UC_X86_REG_CS, UC_X86_REG_DS,
                               UC_X86_REG_ES, UC_X86_REG_SP, UC_X86_REG_SS)

from native import Native

LIB = "reconstruct/libducks.so"
STACK_SEG = 0x3000
RET = 0x30000 + 0x200
GAME_SEG = 0x4CA0
OFF = 0x0F8BD


def guest_step(m, writes, level, direction, pal):
    """One call of the guest's 0x0f8bd on a hand-built state."""
    g = m.dgroup_base
    m.uc.mem_write(g + 0x1798, struct.pack("<h", level))
    m.uc.mem_write(g + 0x179A, struct.pack("<b", direction))
    # palette_apply_gamma runs first and rebuilds d+0x10e1 out of the buffer at
    # d+0x1721, so it is the buffer that has to hold the colours - writing
    # 0x10e1 directly would be overwritten before it was read.
    off, seg = struct.unpack("<HH", m.read(g + 0x1721, 4))
    m.uc.mem_write(seg * 16 + off, pal)

    writes.clear()
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
        m.uc.emu_start(m.image_base + OFF, RET, count=20_000_000)
    finally:
        m.natives = saved
    return (struct.unpack("<h", m.read(g + 0x1798, 2))[0],
            struct.unpack("<b", m.read(g + 0x179A, 1))[0],
            bytes(m.read(g + 0x10E1, 768)))


def expected(level, stored):
    """What the C writes: (c * level) >> 6 plus (0xf - level) * 4, both 8-bit."""
    white = ((0x0F - level) << 2) & 0xFF
    out = []
    for c in stored:
        lit = (c * level) & 0xFFFF
        if lit >= 0x8000:                      # imul then sar, so signed
            lit -= 0x10000
        out.append((white + ((lit >> 6) & 0xFF)) & 0xFF)
    return out


def main():
    lib = ctypes.CDLL(LIB)
    m = Native("Ducks.unpacked.exe", blaster=False, max_insns=1 << 62)

    writes = []

    def on_out(uc, port, size, value, user):
        if port in (0x3C8, 0x3C9):
            writes.append((port, value & 0xFF))

    m.uc.hook_add(UC_HOOK_INSN, on_out, None, 1, 0, UC_X86_INS_OUT)

    gamma = struct.unpack("<h", m.read(m.dgroup_base + 0x1FD5, 2))[0]
    rng = random.Random(0x0F8BD)
    bad = cases = ramps = 0

    for level in range(-1, 17):
        pal = bytes(rng.randrange(0, 64) for _ in range(768))
        want_level, want_dir, stored = guest_step(m, writes, level, 1, pal)
        guest_ramp = [v for p, v in writes if p == 0x3C9]

        ctypes.c_int16.in_dll(lib, "gamma_level").value = gamma
        ctypes.c_int16.in_dll(lib, "fade_level").value = level
        ctypes.c_int8.in_dll(lib, "fade_direction").value = 1
        buf = (ctypes.c_uint8 * 768).from_buffer_copy(pal)
        ctypes.c_void_p.in_dll(lib, "current_buffer").value = \
            ctypes.addressof(buf)
        lib.photo_fade_step()
        got_level = ctypes.c_int16.in_dll(lib, "fade_level").value
        got_dir = ctypes.c_int8.in_dll(lib, "fade_direction").value
        got_stored = bytes((ctypes.c_uint8 * 768).in_dll(lib, "palette_stored"))

        cases += 1
        if (got_level, got_dir) != (want_level, want_dir):
            bad += 1
            print(f"  level {level}: state guest=({want_level},{want_dir}) "
                  f"c=({got_level},{got_dir})")
        if got_stored != stored:
            bad += 1
            i = next(k for k in range(768) if got_stored[k] != stored[k])
            print(f"  level {level}: palette_stored[{i}] "
                  f"guest={stored[i]:#04x} c={got_stored[i]:#04x}")
        if guest_ramp:
            ramps += 1
            # Above 0xe the routine hands over to palette_upload instead, and
            # that is a DAC loop of its own - so the writes seen are the plain
            # 6-bit palette, not the ramp.
            want = ([c >> 2 for c in stored] if want_level >= 0x0F
                    else expected(want_level, stored))
            if guest_ramp != want:
                bad += 1
                i = next(k for k in range(len(want))
                         if k >= len(guest_ramp) or guest_ramp[k] != want[k])
                print(f"  level {level}: ramp[{i}] guest={guest_ramp[i]:#04x} "
                      f"expression={want[i]:#04x}")

    print(f"{cases} state case(s) compared C against guest, "
          f"{ramps} with DAC writes; {bad} differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
