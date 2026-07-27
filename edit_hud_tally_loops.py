#!/usr/bin/env python3
"""Move the HUD and score-tally plane loops to the native side.

These are the other two of the four. Both are pure drawing, like the two already
ported, so both are checkable by the existing plane comparison - which is what
separates them from the rest of the function the HUD loop sits in, where the game's
rules live.

The HUD needs one new routine: 0x0d757, a third number drawer. Same digit layout as
0x0bb3b but with the clip, table and colour fixed and glyph 0x70 laid behind every
digit, which is why the HUD's numbers sit on a tile and the frame's do not.

    venv/bin/python edit_hud_tally_loops.py
"""
import sys

SRC = "native.py"

# --------------------------------------------------------- blit_rows core

ROWS_OLD = '''def native_blit_rows(m, args):
    """Native replacement for the row blitter at 0x05c09.

        [+0x06] far ptr -> far ptr -> array of far row pointers
        [+0x0a] word    : first destination row
        [+0x0c] word    : last destination row (exclusive)
        [+0x0e] word    : first x
        [+0x10] word    : last x (exclusive)
        [+0x1e] word    : first source row index

    No transparency test - it copies unconditionally. Source steps 4 bytes per
    pixel (staying within one plane) while the destination steps 1, so each row
    is a strided gather into a contiguous run, which Python slicing does in one
    go instead of one emulated iteration per pixel.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    table = far(far(args + 0x06 - 6))
    row0, row1 = u16(args + 0x0A - 6), u16(args + 0x0C - 6)
    x0, x1 = u16(args + 0x0E - 6), u16(args + 0x10 - 6)
    srcrow = u16(args + 0x1E - 6)
'''

ROWS_NEW = '''def native_blit_rows(m, args):
    """Stack-reading shim for the row blitter at 0x05c09.

        [+0x06] far ptr -> far ptr -> array of far row pointers
        [+0x0a] word    : first destination row, then last, first x, last x
        [+0x1e] word    : first source row index
    """
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    return blit_rows(m, table=far(far(args + 0x00)), rect=args + 0x04,
                     srcrow=u16(args + 0x18))


def blit_rows(m, table, rect, srcrow):
    """The row blitter at 0x05c09, with its arguments resolved.

    No transparency test - it copies unconditionally. Source steps 4 bytes per
    pixel (staying within one plane) while the destination steps 1, so each row is
    a strided gather into a contiguous run, which Python slicing does in one go
    instead of one emulated iteration per pixel.

    `rect` addresses the four-word destination rectangle, first and last row then
    first and last x, as blit_rows_masked takes it.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    row0, row1 = u16(rect + 0x00), u16(rect + 0x02)
    x0, x1 = u16(rect + 0x04), u16(rect + 0x06)
'''

# --------------------------------------------------------- 0x0d757

NUM2 = '''
def native_draw_number2(m, args):
    """Native replacement for the HUD's number drawer at 0x0d757.

        [+0x06] word : value      [+0x08] word : digits
        [+0x0a] word : x          [+0x0c] word : y, sign-extended by the original

    The same digit layout as draw_number at 0x0bb3b - glyph 0x71 plus the digit,
    12 pixels apart, least significant first, no leading-zero suppression - but
    with the clip, sprite table and colour fixed, and glyph 0x70 drawn behind each
    digit first. That backdrop is the visible difference between the HUD's numbers
    and the in-game frame's.
    """
    s16 = lambda a: struct.unpack("<h", m.read(a, 2))[0]
    return draw_number2(m, value=s16(args + 0x00), digits=s16(args + 0x02),
                        x=s16(args + 0x04), y=s16(args + 0x06))


def draw_number2(m, value, digits, x, y):
    """`digits` digits of `value`, each on a glyph-0x70 tile, 12 pixels apart."""
    g = m.dgroup_base
    for i in range(digits - 1, -1, -1):
        q = -(-value // 10) if value < 0 else value // 10
        # The tile goes down first and the digit over it, which is the order the
        # original draws them in and the only order that leaves the digit visible.
        for index in (0x70, (0x71 + value - q * 10) & 0xFFFF):
            blit_sprite(m, index=index, x=x + i * 12, y=y, table=g + 0x18E9,
                        clip=g + 0x1741, colour=0x90)
        value = q


'''

# --------------------------------------------------------- the two loops

LOOPS = '''
def plane_loop_hud(m):
    """The four-plane HUD loop at 0x0d9a2, done in one native call.

    Shares a function - and a plane counter at [bp-0x1f] - with the scroll loop at
    0x0e4dc. Per plane:

        set_plane(plane)                                   # 0x57ee
        blit_rows(rows from [bp-0x52], ds:0x1741, 0)       # 0x5c09, the panel
        for i in 0 .. [0x178b]-1:                          # the collected items
            draw_sprite(0x2a, 0x82 + i*16, 0x10, .., 0x90)     # the empty slot
            outline_sprite(item, same position)                # 0x65f1
            draw_sprite(item, same position, 0x90)
        draw_sprite(7,    0xd4,  0x23, .., 0x90)
        draw_sprite(0x4f, 0x105, 0x23, .., 0x90)
        draw_number2([0x2036], 6, 0x80,  0x22)             # 0xd757
        draw_number2([0x2007], 2, 0xe1,  0x22)
        draw_number2([0x2034], 2, 0x113, 0x22)
        draw_sprite(0xae, 0x135, 7, .., 0x90)

    Each item's sprite is its type - a word from the array at [0x1782] - looked up
    in the per-type sprite tables at DGROUP 0x9a, taking the first entry. This is
    the call site that exercises the outline: 8 to 16 calls a session in ordinary
    play, which is why 0x65f1 is verified even though the shadow path inside
    draw_entities has never been shown to fire.
    """
    g = m.dgroup_base
    u8 = lambda a: m.read(a, 1)[0]
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    frame = m._reg(UC_X86_REG_SS) * 16 + m._reg(UC_X86_REG_BP)
    rows = far(frame - 0x52)          # the panel's row table, held in a local
    items = u8(g + 0x178B)
    types = far(g + 0x1782)

    def sprite_for(i):
        t = u16(types + i * 2)
        return u16(far(g + 0x9A + t * 4))

    for plane in range(4):
        set_plane(m, plane)
        blit_rows(m, table=rows, rect=g + 0x1741, srcrow=0)
        for i in range(items):
            x = 0x82 + i * 16
            blit_sprite(m, index=0x2A, x=x, y=0x10, table=g + 0x18E9,
                        clip=g + 0x1741, colour=0x90)
            idx = sprite_for(i)
            outline_sprite(m, index=idx, x=x, y=0x10, table=g + 0x18E9,
                           clip=g + 0x1741)
            blit_sprite(m, index=idx, x=x, y=0x10, table=g + 0x18E9,
                        clip=g + 0x1741, colour=0x90)
        for index, x, y in ((0x07, 0x0D4, 0x23), (0x4F, 0x105, 0x23),
                            (0xAE, 0x135, 0x07)):
            blit_sprite(m, index=index, x=x, y=y, table=g + 0x18E9,
                        clip=g + 0x1741, colour=0x90)
        draw_number2(m, u16(g + 0x2036), 6, 0x080, 0x22)
        draw_number2(m, u16(g + 0x2007), 2, 0x0E1, 0x22)
        draw_number2(m, u16(g + 0x2034), 2, 0x113, 0x22)


def plane_loop_tally(m):
    """The four-plane loop at 0x0bc4b, inside the end-of-level score tally.

    The smallest of the four, and everything in it was already native:

        set_plane(plane)                                        # 0x57ee
        draw_number(SI,       0x96, [bp+8]*20 + 0x46,  6)       # 0xbb3b
        if DI: draw_number([bp-0xe], 0x96, [bp+0xa]*20 + 0x46, 6)

    The two values are in registers and locals rather than globals - SI is the
    running total being counted up, DI a flag for whether a second row is shown -
    so they are read from the frame and the CPU at the loop head, as the guest's own
    loop does on each iteration. Neither is touched inside the loop.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    frame = m._reg(UC_X86_REG_SS) * 16 + m._reg(UC_X86_REG_BP)
    first = m._reg(UC_X86_REG_SI)
    second_on = m._reg(UC_X86_REG_DI)
    y1 = (u16(frame + 8) * 0x14 + 0x46) & 0xFFFF
    y2 = (u16(frame + 0x0A) * 0x14 + 0x46) & 0xFFFF
    second = u16(frame - 0x0E)

    for plane in range(4):
        set_plane(m, plane)
        draw_number(m, first, 0x96, y1, g + 0x1769, 0, 6)
        if second_on:
            draw_number(m, second, 0x96, y2, g + 0x1769, 0, 6)


'''

TABLE_OLD = '''PLANE_LOOPS = {
    0x0CD5F: (0x0CD98, plane_loop_layer),
    0x0E4DC: (0x0E673, plane_loop_scroll),
}
'''
TABLE_NEW = '''PLANE_LOOPS = {
    0x0CD5F: (0x0CD98, plane_loop_layer),
    0x0E4DC: (0x0E673, plane_loop_scroll),
    0x0D9A2: (0x0DB2C, plane_loop_hud),
    0x0BC4B: (0x0BCA9, plane_loop_tally),
}
'''

NATIVE_OLD = '''    (0x0BB3B, "draw_number", native_draw_number, "far"),
'''
NATIVE_NEW = '''    (0x0BB3B, "draw_number", native_draw_number, "far"),
    (0x0D757, "draw_number2", native_draw_number2, "far"),
'''

EDITS = [
    (ROWS_OLD, ROWS_NEW),
    # draw_number2 goes next to draw_number, which it mirrors.
    ("# All entity types are handled and verified. Kept as a knob because "
     "restricting",
     NUM2.lstrip("\\n") + "# All entity types are handled and verified. Kept as "
     "a knob because restricting"),
    (TABLE_OLD, LOOPS.lstrip("\\n") + TABLE_NEW),
    (NATIVE_OLD, NATIVE_NEW),
]


def main():
    src = open(SRC).read()
    for old, new in EDITS:
        n = src.count(old)
        if n != 1:
            print(f"anchor found {n} times, nothing written: {old[:70]!r}")
            return 1
        src = src.replace(old, new, 1)
    open(SRC, "w").write(src)
    print("native.py updated: HUD and tally plane loops native, 0x0d757 added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
