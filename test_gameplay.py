#!/usr/bin/env python3
"""Drive the gameplay natives with made-up inputs and compare them to the guest.

`native.py --verify` is the better check when it can be had: it compares against
the original on a real call, in a real state, and it needs no invented anything.
What it cannot do is reach a case the run never takes. Loading the level 80
snapshot and running twelve seconds calls three of the thirty routines under
`run_level()`, and the one branch of `scroll_follow` it does take is a camera
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

from unicorn.x86_const import (UC_X86_REG_AX, UC_X86_REG_CS, UC_X86_REG_DS,
                               UC_X86_REG_DX, UC_X86_REG_ES, UC_X86_REG_SP,
                               UC_X86_REG_SS)

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
    # A native that returns something is claiming AX, and for a routine that
    # only reads - the tool-list queries, text_width - that is the whole answer.
    regs = outcome if isinstance(outcome, tuple) else \
        (outcome,) if outcome is not None else ()
    for (a, _), b in zip(watch, before):
        m.uc.mem_write(a, b)

    guest_call(m, off, frame)
    theirs = [bytes(m.uc.mem_read(a, n)) for a, n in watch]
    real = [m.uc.reg_read(r) & 0xFFFF for r in (UC_X86_REG_AX, UC_X86_REG_DX)]
    for i, v in enumerate(regs):
        if (v & 0xFFFF) != real[i]:
            return (f"{label}: {'AX' if i == 0 else 'DX'} "
                    f"native={v & 0xFFFF:#06x} guest={real[i]:#06x}")

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


def _seed_tool_list(m, rng):
    d = m.dgroup_base
    n = rng.randrange(0, 9)
    m.uc.mem_write(SCRATCH_SEG * 16,
                   b"".join(struct.pack("<h", rng.randrange(0, 112))
                            for _ in range(max(1, n))))
    m.uc.mem_write(d + 0x1782, struct.pack("<HH", 0, SCRATCH_SEG))
    m.uc.mem_write(d + 0x178B, bytes([n]))
    m.uc.mem_write(d + 0x03A7, rng.randbytes(112))


def case_tool_list_has(m, rng):
    _seed_tool_list(m, rng)
    frame = struct.pack("<h", rng.randrange(0, 112))
    return 0x0D55D, frame, [], "tool_list_has"


def case_tool_list_any_flagged(m, rng):
    _seed_tool_list(m, rng)
    return 0x0D591, b"", [], "tool_list_any_flagged"


def case_text_width(m, rng):
    """A random string against a random font table, empty string included."""
    d = m.dgroup_base
    m.uc.mem_write(d + 0x054D, b"".join(
        struct.pack("<H", rng.randrange(0, 40)) + bytes(6)
        for _ in range(256)))
    n = rng.randrange(0, 24)
    m.uc.mem_write(SCRATCH_SEG * 16,
                   bytes(rng.randrange(1, 256) for _ in range(n)) + b"\0")
    return 0x06D52, struct.pack("<HH", 0, SCRATCH_SEG), [], "text_width"


def _make_image(m, rng, w, h):
    """An image_t with its rows spread out, at SCRATCH_SEG."""
    im = SCRATCH_SEG * 16
    rows = im + 0x20
    pix = SCRATCH_SEG * 16 + 0x400
    m.uc.mem_write(im, struct.pack("<HH", 0x20, SCRATCH_SEG))
    m.uc.mem_write(im + 0x0C, struct.pack("<HH", w, h))
    # Sixteen rows always, whatever the height says, so clearing one row too
    # many shows up as a difference rather than as memory nobody looks at.
    for i in range(16):
        at = pix + i * 0x100
        m.uc.mem_write(rows + i * 4,
                       struct.pack("<HH", at - SCRATCH_SEG * 16, SCRATCH_SEG))
        m.uc.mem_write(at, rng.randbytes(0x100))
    return im


def case_image_clear(m, rng):
    w, h = rng.randrange(0, 64), rng.randrange(0, 16)
    im = _make_image(m, rng, w, h)
    frame = struct.pack("<HHH", 0x00, SCRATCH_SEG, rng.randrange(0, 256))
    watch = [(SCRATCH_SEG * 16 + 0x400 + i * 0x100, max(1, w))
             for i in range(16)]
    return 0x06A49, frame, watch, "image_clear"


def case_build_washed_ramp(m, rng):
    d = m.dgroup_base
    m.uc.mem_write(d + 0x14B1, rng.randbytes(0x30))
    m.uc.mem_write(d + 0x1FD5, bytes([rng.randrange(0, 0x20)]))
    return 0x0876A, b"", [(d + 0x0DAD, 0x30)], "build_washed_ramp"


def case_scroll_axis_snap(m, rng):
    p = SCRATCH_SEG * 16
    m.uc.mem_write(p, struct.pack("<i", rng.randrange(-4096, 4096)))
    frame = struct.pack("<iiHHh",
                        rng.randrange(-5000, 5000),
                        rng.randrange(-800, 800),
                        0, SCRATCH_SEG,
                        rng.randrange(-32, 4096))
    return 0x05F15, frame, [(p, 4)], "scroll_axis_snap"


def case_scene_swap_pair(m, rng):
    """Entities carrying a mix of the two types and of ones that must not move."""
    d = m.dgroup_base
    n = rng.randrange(0, 12)
    blob = bytearray(rng.randbytes(max(1, n) * 0x29))
    for i in range(n):
        t = rng.choice([0x2C, 0x2D, 0x2C, 0x2D, rng.randrange(0, 0x60)])
        blob[i * 0x29 + 0x25:i * 0x29 + 0x27] = struct.pack("<h", t)
    m.uc.mem_write(SCRATCH_SEG * 16, bytes(blob))
    m.uc.mem_write(d + 0x0D83, struct.pack("<HH", 0, SCRATCH_SEG))
    m.uc.mem_write(d + 0x0D7D, struct.pack("<h", n))
    return (0x0A3A7, b"", [(SCRATCH_SEG * 16, max(1, n) * 0x29)],
            "scene_swap_pair")


def case_tool_events(m, rng):
    """The level's scheduled tool changes, with records that actually fire.

    The tables in the demo captures fire at clocks 337 to 642 and a --verify run
    reaches about 180, so this is the only thing that exercises the write at all.
    Half the records are put on the current tick deliberately: two on the same
    tick leave the last one selected, which is the guest's behaviour and not
    obviously deliberate, so it is worth pinning.
    """
    d = m.dgroup_base
    clock = rng.randrange(0, 700)
    n = rng.randrange(0, 10)
    recs = b""
    for _ in range(n):
        when = clock if rng.randrange(0, 2) else rng.randrange(0, 700)
        recs += struct.pack("<H", when) + bytes([rng.randrange(0, 256)])
    m.uc.mem_write(SCRATCH_SEG * 16, recs or b"\0\0\0")
    m.uc.mem_write(d + 0x203B, struct.pack("<HH", 0, SCRATCH_SEG))
    m.uc.mem_write(d + 0x2047, struct.pack("<H", n))
    m.uc.mem_write(d + 0x201A, struct.pack("<H", clock))
    m.uc.mem_write(d + 0x1788, bytes([rng.randrange(0, 256)]))
    return 0x0D4C2, b"", [(d + 0x1788, 1)], "tool_events"


def case_entity_copy(m, rng):
    """entity_copy(scene, from, to) over a scene of random bytes.

    Random bytes rather than plausible entities on purpose: the fields it does
    NOT copy are the interesting part, and only junk in them shows that the gaps
    are really gaps. Copying an entity onto itself is included.
    """
    n = rng.randrange(1, 10)
    hdr = SCRATCH_SEG * 16
    ents = 0x100
    m.uc.mem_write(hdr, struct.pack("<hhhhHH", n, n, 0, 0, ents, SCRATCH_SEG))
    m.uc.mem_write(SCRATCH_SEG * 16 + ents, rng.randbytes(n * 0x29))
    src = rng.randrange(0, n)
    dst = rng.choice([src, rng.randrange(0, n)])
    frame = struct.pack("<HHhh", 0, SCRATCH_SEG, src, dst)
    return (0x06F4F, frame,
            [(SCRATCH_SEG * 16 + ents, n * 0x29)], "entity_copy")


def case_particles_spawn(m, rng):
    """A burst into a pool that is sometimes too small to hold it.

    The seed at d+0x3006 is in the watched set, so compare() restores it before
    the guest runs and both sides draw the same numbers - and a native that made
    the wrong number of draws would leave the seed somewhere else and be caught
    by that alone.
    """
    d = m.dgroup_base
    cap = rng.randrange(0, 40)
    live = rng.randrange(0, cap + 1) if cap else 0
    pool = SCRATCH_SEG * 16

    m.uc.mem_write(pool, rng.randbytes(max(1, cap) * 16))
    m.uc.mem_write(d + 0x18C1, struct.pack("<HH", 0, SCRATCH_SEG))
    m.uc.mem_write(d + 0x18CD, struct.pack("<h", live))
    m.uc.mem_write(d + 0x18CF, struct.pack("<h", cap))
    m.uc.mem_write(d + 0x18C5, rng.randbytes(8))
    m.uc.mem_write(d + 0x3006, rng.randbytes(4))

    frame = struct.pack("<hhh", rng.randrange(-300, 600),
                        rng.randrange(-300, 600), rng.randrange(0, 45))
    return (0x077AE, frame,
            [(pool, max(1, cap) * 16), (d + 0x18CD, 2), (d + 0x3006, 4)],
            "particles_spawn")


CASES = [case_scroll, case_scroll_axis, case_entity_set_type,
         case_particles_spawn,
         case_entity_copy,
         case_tool_events,
         case_scroll_axis_snap, case_scene_swap_pair,
         case_tool_list_has, case_tool_list_any_flagged, case_text_width,
         case_image_clear, case_build_washed_ramp,
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
