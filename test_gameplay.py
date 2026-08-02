#!/usr/bin/env python3
"""Drive the gameplay natives with made-up inputs and compare them to the guest.

`native.py --verify` is the better check when it can be had: it compares against
the original on a real call, in a real state, and it needs no invented anything.
What it cannot do is reach a case the run never takes. Loading the level 80
snapshot and running twelve seconds calls three of the thirty routines under
`in_game_frame`, and the one branch of `scroll_follow` it does take is a camera
that has already converged - every call is a no-op, so the comparison holds no
matter what the native does with the other axis. That is exactly the shape of a
verification that passes while proving nothing.

So this is the other half. Both sides run in the same machine and neither of them
is written twice: the routine's arguments are made up, written where a call would
have left them, and

  1. the Python native runs and what it wrote is copied out,
  2. the memory is put back the way it was,
  3. the guest's own body runs from the same state, with the native table
     unhooked so the original code really runs,
  4. the two are compared.

Nothing is asserted about what the answer should be - only that the two agree,
which is the only thing that can be checked without a second reading to be wrong
in the same way.

The state is invented rather than snapshotted, which is the trade: the case may
be one the game never produces. That is acceptable for agreement (both sides see
the same nonsense) and is why this does not replace --verify.

    venv/bin/python test_gameplay.py
"""
import random
import struct
import sys

from unicorn.x86_const import (UC_X86_REG_CS, UC_X86_REG_DS, UC_X86_REG_ES,
                               UC_X86_REG_SP, UC_X86_REG_SS)

import native
from native import DECLINE, Native

STACK_SEG = 0x3000               # scratch, well clear of the loaded image
ARGS_SEG = 0x3800                # where the made-up call frame is put
SCRATCH_SEG = 0x4000             # entities and other things pointed at
RET = 0x30000 + 0x200            # a HLT the call returns to


def guest_call(m, off, frame):
    """Run the original body from a hand-built far-call frame.

    The native table is taken out for the duration. Without that, a routine that
    calls another native - scroll_follow calls scroll_axis_toward - would be
    compared against a body that is itself partly the Python, and would agree
    with itself.
    """
    ss, sp = STACK_SEG, 0xF00
    m.uc.mem_write(RET, b"\xF4")
    m.uc.mem_write(ss * 16 + sp,
                   struct.pack("<HH", RET & 0xF, RET >> 4) + frame)
    m.uc.reg_write(UC_X86_REG_SS, ss)
    m.uc.reg_write(UC_X86_REG_SP, sp)
    m.uc.reg_write(UC_X86_REG_CS, m.load_seg)
    m.uc.reg_write(UC_X86_REG_DS, m.dgroup_base >> 4)
    m.uc.reg_write(UC_X86_REG_ES, m.dgroup_base >> 4)
    saved, m.natives = m.natives, {}
    try:
        m.uc.emu_start(m.image_base + off, RET, count=2_000_000)
    finally:
        m.natives = saved


def compare(m, off, name, frame, watch, label):
    """One case. Returns "" if the two agree, else what differs."""
    m.uc.mem_write(ARGS_SEG * 16, frame)
    before = [bytes(m.uc.mem_read(a, n)) for a, n in watch]

    _, handler, _ = m.natives[off]
    outcome = handler(m, ARGS_SEG * 16)
    if outcome is DECLINE:
        for (a, _), b in zip(watch, before):
            m.uc.mem_write(a, b)
        return "declined"
    ours = [bytes(m.uc.mem_read(a, n)) for a, n in watch]
    for (a, _), b in zip(watch, before):
        m.uc.mem_write(a, b)

    guest_call(m, off, frame)
    theirs = [bytes(m.uc.mem_read(a, n)) for a, n in watch]

    for (a, n), want, got in zip(watch, ours, theirs):
        for j in range(n):
            if want[j] != got[j]:
                return (f"{label}: {a + j:#07x} (+{j:#x}) "
                        f"native={want[j]:#04x} guest={got[j]:#04x}")
    return ""


def sign32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


# --------------------------------------------------------------------- cases


def case_scroll(m, rng):
    """scroll_follow(long x, long y), and scroll_axis_toward under it.

    Both branches: the flag at d+0x4fa picks smooth easing or the hard window,
    and only the smooth one is ever seen in a snapshot. The level and view sizes
    are drawn so the view is sometimes larger than the level, which is the case
    the game itself produces on the horizontal axis of level 80 - a 320-wide
    level in a 320-wide view, where the room to scroll is zero.
    """
    d = m.dgroup_base
    vw = rng.randrange(16, 321)
    vh = rng.randrange(16, 201)
    ww = rng.choice([vw, vw + rng.randrange(0, 400), rng.randrange(16, 640)])
    wh = rng.choice([vh, vh + rng.randrange(0, 400), rng.randrange(16, 480)])
    m.uc.mem_write(d + 0x1701, struct.pack("<hh", ww, wh))
    m.uc.mem_write(d + 0x1735, struct.pack("<hh", vw, vh))
    m.uc.mem_write(d + 0x1739, struct.pack("<ii",
                                           rng.randrange(-64, 512),
                                           rng.randrange(-64, 512)))
    m.uc.mem_write(d + 0x18F5, bytes([rng.randrange(0, 6)]))
    m.uc.mem_write(d + 0x04FA, struct.pack("<h", rng.randrange(0, 2)))

    frame = struct.pack("<ii", rng.randrange(-200, 700),
                        rng.randrange(-200, 700))
    return 0x0600D, frame, [(d + 0x1739, 8)], "scroll_follow"


def case_scroll_axis(m, rng):
    """scroll_axis_toward on its own, so the clamp gets the extremes."""
    d = m.dgroup_base
    m.uc.mem_write(d + 0x18F5, bytes([rng.randrange(0, 6)]))
    p = SCRATCH_SEG * 16
    m.uc.mem_write(p, struct.pack("<i", rng.randrange(-4096, 4096)))
    frame = struct.pack("<iiHHh",
                        rng.randrange(-5000, 5000),
                        rng.randrange(-400, 400),
                        0, SCRATCH_SEG,
                        rng.randrange(-32, 4096))
    return 0x05F7F, frame, [(p, 4)], "scroll_axis_toward"


def case_entity_set_type(m, rng):
    """entity_set_type(entity_t far *, int16_t), including setting it unchanged."""
    e = SCRATCH_SEG * 16
    m.uc.mem_write(e, bytes(rng.randrange(0, 256) for _ in range(0x29)))
    was = struct.unpack("<h", m.uc.mem_read(e + 0x25, 2))[0]
    kind = rng.choice([was, rng.randrange(-4, 120)])
    frame = struct.pack("<HHh", 0, SCRATCH_SEG, kind)
    return 0x078D4, frame, [(e, 0x29)], "entity_set_type"


def case_egg_block_end(m, rng):
    d = m.dgroup_base
    m.uc.mem_write(d + 0x20B6, struct.pack("<H", rng.randrange(0, 0x10000)))
    return 0x0537D, b"", [(d + 0x20B6, 2)], "egg_block_end"


def case_rle_reset(m, rng):
    d = m.dgroup_base
    m.uc.mem_write(d + 0x20CE, rng.randbytes(4))
    return 0x0580B, b"", [(d + 0x20CE, 4)], "rle_reset"


def case_set_buffer(m, rng):
    frame = struct.pack("<HH", rng.randrange(0, 0x10000),
                        rng.randrange(0, 0x10000))
    return 0x0B9EA, frame, [(m.dgroup_base + 0x1721, 4)], "set_buffer"


def case_cursor_to_centre(m, rng):
    im = SCRATCH_SEG * 16
    m.uc.mem_write(im + 0x0C, struct.pack("<HH", rng.randrange(0, 0x10000),
                                          rng.randrange(0, 0x10000)))
    frame = struct.pack("<HH", 0, SCRATCH_SEG)
    return 0x0C20E, frame, [(m.dgroup_base + 0x18D3, 8)], "cursor_to_centre"


def case_bg_scroll_reset(m, rng):
    """The whole range of the level's byte, not just 0..8.

    Only 0..8 is meaningful - two base-3 digits - but nothing checks that, and a
    negative one divides the other way round, so the guest is the authority on
    what the rest does.
    """
    d = m.dgroup_base
    m.uc.mem_write(d + 0x202C, bytes([rng.randrange(0, 256)]))
    m.uc.mem_write(d + 0x1717, struct.pack("<HH", rng.randrange(1, 640),
                                           rng.randrange(1, 480)))
    m.uc.mem_write(d + 0x177E, rng.randbytes(4))
    return 0x0D6C3, b"", [(d + 0x177E, 4)], "bg_scroll_reset"


def case_palette_apply_gamma(m, rng):
    d = m.dgroup_base
    m.uc.mem_write(SCRATCH_SEG * 16, rng.randbytes(0x300))
    m.uc.mem_write(d + 0x1721, struct.pack("<HH", 0, SCRATCH_SEG))
    m.uc.mem_write(d + 0x1FD5, bytes([rng.randrange(0, 0x20)]))
    return 0x0B0C5, b"", [(d + 0x10E1, 0x300)], "palette_apply_gamma"


CASES = [case_scroll, case_scroll_axis, case_entity_set_type,
         case_egg_block_end, case_rle_reset, case_set_buffer,
         case_cursor_to_centre, case_bg_scroll_reset,
         case_palette_apply_gamma]


def main():
    m = Native("Ducks.unpacked.exe", blaster=False, max_insns=1 << 62)
    rng = random.Random(20260802)

    bad = declined = 0
    counts = {}
    for _ in range(300):
        for make in CASES:
            off, frame, watch, label = make(m, rng)
            counts[label] = counts.get(label, 0) + 1
            why = compare(m, off, label, frame, watch, label)
            if why == "declined":
                declined += 1
            elif why:
                bad += 1
                if bad < 8:
                    print("  MISMATCH " + why)

    for label in sorted(counts):
        print(f"  {label:<22} {counts[label]} cases")
    print(f"{sum(counts.values())} comparisons, {declined} declined, "
          f"{bad} differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
