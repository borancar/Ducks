#!/usr/bin/env python3
"""Add the native for 0x0bb3b - the fixed-width decimal number drawer.

Anchored replacements, asserted before anything is written, and one write at the
end: a failed anchor leaves native.py exactly as it was.

    venv/bin/python edit_draw_number.py
"""
import sys

SRC = "native.py"

NEW_FUNCS = '''
def native_draw_number(m, args):
    """Native replacement for the fixed-width number drawer at 0x0bb3b.

        [+0x06] word    : value
        [+0x08] word    : x of the leftmost digit cell
        [+0x0a] word    : y, sign-extended to a long by the original
        [+0x0c] far ptr : viewport / clip rectangle
        [+0x10] byte    : colour offset, as draw_sprite takes it
        [+0x12] word    : how many digits to draw

    The score and the level counter, drawn eight times a frame - not hot. It is
    here because it was the last thing inside the scroll caller's four-plane loop
    that was not native, and that loop is the point.
    """
    s16 = lambda a: struct.unpack("<h", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    return draw_number(m, value=s16(args + 0x00), x=s16(args + 0x02),
                       y=s16(args + 0x04), clip=far(args + 0x06),
                       colour=m.read(args + 0x0A, 1)[0],
                       digits=s16(args + 0x0C))


def draw_number(m, value, x, y, clip, colour, digits):
    """Draw `digits` decimal digits of `value`, right-aligned, 12 pixels apart.

    Every digit is a sprite: glyph 0x71 plus the digit, out of the same table
    header at DGROUP:0x18e9 the entity loop draws from. The original walks from
    the least significant digit upwards, drawing at x + i * 12 with i counting
    down from digits - 1, so the field is fixed-width and right-aligned with no
    leading-zero suppression: a score of nothing draws six noughts.

    Goes through blit_sprite rather than the draw_sprite native, so a six-digit
    score costs one native dispatch instead of seven.
    """
    g = m.dgroup_base
    for i in range(digits - 1, -1, -1):
        # idiv truncates toward zero; Python's // floors. No caller passes a
        # negative today, but the two disagree silently on both the digit and
        # the quotient, so the original's arithmetic is what gets reproduced.
        q = -(-value // 10) if value < 0 else value // 10
        blit_sprite(m, index=(0x71 + value - q * 10) & 0xFFFF,
                    x=x + i * 12, y=y, table=g + 0x18E9, clip=clip,
                    colour=colour)
        value = q


'''

EDITS = [
    # The native goes after blit_sprite, which it calls, and before the entity
    # loop's type knob.
    ("# All entity types are handled and verified. Kept as a knob because "
     "restricting",
     NEW_FUNCS.lstrip("\n") + "# All entity types are handled and verified. "
     "Kept as a knob because restricting"),
    # Registered next to the blitter it drives.
    ('    (0x063D6, "draw_sprite", native_draw_sprite, "far"),\n',
     '    (0x063D6, "draw_sprite", native_draw_sprite, "far"),\n'
     '    (0x0BB3B, "draw_number", native_draw_number, "far"),\n'),
]


def main():
    src = open(SRC).read()
    for old, new in EDITS:
        if old not in src:
            print(f"anchor missing, nothing written: {old[:60]!r}")
            return 1
        if src.count(old) != 1:
            print(f"anchor is not unique ({src.count(old)}): {old[:60]!r}")
            return 1
        src = src.replace(old, new, 1)
    open(SRC, "w").write(src)
    print("native.py updated: draw_number added and registered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
