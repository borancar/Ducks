#!/usr/bin/env python3
"""Move the scroll caller's four-plane loop (0x0e4dc) to the native side.

Three of the routines the loop calls are only reachable through a stack-reading
shim, so each gets a resolved-argument core split out first - the same shape as
blit_sprite under draw_sprite. Then the loop itself, transcribed from
0x0e4dc-0x0e673, and registered in PLANE_LOOPS.

Anchored replacements, all asserted before anything is written, one write at the
end: a failed anchor leaves native.py exactly as it was.

    venv/bin/python edit_plane_loop_scroll.py
"""
import sys

SRC = "native.py"

# ---------------------------------------------------------------- plot_pixel

PLOT_OLD = '''def native_plot_pixel(m, args):
    """Native replacement for the single-pixel plot at 0x05761.

        [+0x06] word : x      [+0x08] word : y      [+0x0a] byte : colour

    Writes nothing unless x & 3 equals the current plane, so the game calls it
    up to four times per pixel.

    Note this routine computes its row stride as 80 unconditionally, with no
    [0x4fe] resolution check - unlike every other drawing routine here. That
    looks like an oversight in the original for 360-wide mode, but the native
    reproduces it rather than silently correcting it: the point is to be
    identical, and --verify would flag any "improvement" as a mismatch.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    x, y = u16(args + 0), u16(args + 2)
    if m.read(g + 0x177D, 1)[0] != (x & 3):
        return None
    off, seg = struct.unpack("<HH", m.read(g + 0x16F1, 4))
    o = seg * 16 + off - 0xA0000 + y * 80 + (x >> 2) + u16(g + 0x1727)
    colour = m.read(args + 4, 1)[0]
'''

PLOT_NEW = '''# The two single-pixel plotters, by row stride. They are the same routine bar the
# multiply, and the game keeps a pointer to the one that matches the resolution
# in [0x53e] - which is how 0x5761 gets away with no [0x4fe] check inside it. So
# the stride comes from the pointer, not from a guess.
PLOT_PIXEL_STRIDE = {0x05761: 80, 0x057A1: 90}


def plot_pixel_stride(m):
    """The row stride of whichever plotter is installed at [0x53e], or None.

    None means the pointer holds something neither of them, which would be a hole
    in this reading of the code rather than a stride to invent - so it is said out
    loud once and the caller skips the pixels instead of writing wrong ones.
    """
    off = struct.unpack("<H", m.read(m.dgroup_base + 0x53E, 2))[0]
    stride = PLOT_PIXEL_STRIDE.get(off)
    if stride is None and not m.plot_pixel_warned:
        m.plot_pixel_warned = True
        known = ", ".join(f"{k:#07x}" for k in PLOT_PIXEL_STRIDE)
        print(f"  [pixel] [0x53e] holds {off:#07x}, which is neither plotter "
              f"({known}); the native plane loop is skipping its pixel runs")
    return stride


def native_plot_pixel(m, args):
    """Stack-reading shim for the single-pixel plot at 0x05761.

        [+0x06] word : x      [+0x08] word : y      [+0x0a] byte : colour

    Stride 80 unconditionally, because that is what this one of the two does.
    """
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    return plot_pixel(m, u16(args + 0), u16(args + 2),
                      m.read(args + 4, 1)[0], 80)


def plot_pixel(m, x, y, colour, stride):
    """One pixel, with its arguments resolved.

    Writes nothing unless x & 3 equals the current plane, so the game calls it up
    to four times per pixel. Split out of the native above so the scroll caller's
    plane loop can draw its pixel run without a dispatch per pixel.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    if m.read(g + 0x177D, 1)[0] != (x & 3):
        return None
    off, seg = struct.unpack("<HH", m.read(g + 0x16F1, 4))
    o = seg * 16 + off - 0xA0000 + y * stride + (x >> 2) + u16(g + 0x1727)
'''

# ----------------------------------------------------------- blit_rows_masked

MASKED_OLD = '''def native_blit_rows_masked(m, args):
    """Native replacement for the masked row blitter at 0x05ac2.

    Same arguments and layout as blit_rows (0x05c09), but source bytes of zero
    are transparent and leave the destination untouched. Read the existing row,
    overlay the non-zero source pixels, write it back in one go.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    table = far(far(args + 0x00))
    row0, row1 = u16(args + 0x04), u16(args + 0x06)
    x0, x1 = u16(args + 0x08), u16(args + 0x0A)
    srcrow = u16(args + 0x18)
'''

MASKED_NEW = '''def native_blit_rows_masked(m, args):
    """Stack-reading shim for the masked row blitter at 0x05ac2.

        [+0x06] far ptr -> far ptr -> array of far row pointers
        [+0x0a] word    : first destination row, then last, first x, last x
        [+0x1e] word    : first source row index
    """
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    return blit_rows_masked(m, table=far(far(args + 0x00)), rect=args + 0x04,
                            srcrow=u16(args + 0x18))


def blit_rows_masked(m, table, rect, srcrow):
    """The masked row blitter at 0x05ac2, with its arguments resolved.

    Same layout as blit_rows (0x05c09), but source bytes of zero are transparent
    and leave the destination untouched. Read the existing row, overlay the
    non-zero source pixels, write it back in one go.

    `rect` is the address of the four-word destination rectangle - first and last
    row, first and last x. The guest pushes a verbatim copy of it, so the plane
    loop passes its DGROUP source address instead of copying it again.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    row0, row1 = u16(rect + 0x00), u16(rect + 0x02)
    x0, x1 = u16(rect + 0x04), u16(rect + 0x06)
'''

# ------------------------------------------------------------- compose_scroll

SCROLL_OLD = '''def native_compose_scroll(m, args):
    """Native replacement for the scrolling compositor at 0x05dc4.

        [+0x06] word : x scroll
        [+0x08] word : y scroll

    Like compose_layer,'''

SCROLL_NEW = '''def native_compose_scroll(m, args):
    """Stack-reading shim for the scrolling compositor at 0x05dc4.

        [+0x06] word : x scroll
        [+0x08] word : y scroll
    """
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    return compose_scroll(m, u16(args + 0), u16(args + 2))


def compose_scroll(m, argx, argy):
    """The scrolling compositor at 0x05dc4, with its arguments resolved.

    Split out of the native above so the scroll caller's plane loop can compose
    without paying a second dispatch per plane.

    Like compose_layer,'''

SCROLL_ARGS_OLD = "    argx, argy = u16(args + 0), u16(args + 2)\n"

# ------------------------------------------------------------- the plane loop

LOOP_NEW = '''# The scene layers the scroll caller draws, in the order it draws them: six
# 12-byte records live at DGROUP:0xd63 and it takes 1, 0, 2, 3, 5, then 4 - the
# last of those (0xd93) only when its argument is zero, and it is also the only
# one the layer caller at 0x0cd5f draws. A seventh scene of its own sits at
# 0x178c behind a flag, with the other viewport.
SCROLL_LAYERS = (0x0D6F, 0x0D63, 0x0D7B, 0x0D87, 0x0D9F)


def plane_loop_scroll(m):
    """The four-plane drawing loop at 0x0e4dc, done in one native call.

    The busier of the game's two: this is the in-game frame, and it is where
    draw_entities gets most of its ~34000 calls a session.

        for plane in 0..3:
            set_plane(plane)                             # 0x57ee
            compose_scroll([0x1739], [0x173d])           # 0x5dc4
            if [0x4f6]: particles()                      # 0xab09
            draw_entities(scene, ds:0x172d, 0)           # 0xaba5, five layers
            if arg == 0:   draw_entities(ds:0xd93,  ds:0x172d, 0x90)
            if [0x178b]:   draw_entities(ds:0x178c, ds:0x1741, 0x90)
            for i in 0..2:                               # three flashing panels
                if [0x2154+i]: blit_rows_masked(...)     # 0x5ac2
            if [bp-8]:     draw_number([bp-6],   0x80, 0x22, ..., 6)
            if [bp-0xa]:   draw_number([0x2007], 0xe1, 0x22, ..., 2)
            for x in [bp-2]..[bp-4]: (*[0x53e])(x, y, 0) # a five-pixel run

    Everything it calls was already native, so the loop was the last emulated
    thing in the in-game drawing path - and the reason every fixed cost inside it
    was paid four times a frame instead of once.

    Six of the enclosing function's locals and its argument are read off the
    frame: the plane counter at [bp-0x1f] is deliberately not written back, as
    nothing after the loop reads it and both plane loops in that function
    re-initialise it at their head.
    """
    g = m.dgroup_base
    u8 = lambda a: m.read(a, 1)[0]
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    s16 = lambda a: struct.unpack("<h", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    frame = m._reg(UC_X86_REG_SS) * 16 + m._reg(UC_X86_REG_BP)
    arg = u16(frame + 6)
    px0, px1 = u16(frame - 2), u16(frame - 4)
    number = s16(frame - 6)
    number_on, level_on = u16(frame - 8), u16(frame - 0x0A)

    scroll_x, scroll_y = u16(g + 0x1739), u16(g + 0x173D)
    # 16-bit and wrapping, as the original computes it.
    pixel_y = (u16(g + 0x53A) + 0xFFF9 - u16(g + 0x2003)) & 0xFFFF
    stride = plot_pixel_stride(m)
    panels = [i for i in range(3) if u8(g + 0x2154 + i)]

    for plane in range(4):
        set_plane(m, plane)
        compose_scroll(m, scroll_x, scroll_y)
        if u16(g + 0x4F6):
            native_particles(m, 0)                  # reads no arguments
        for scene in SCROLL_LAYERS:
            draw_entities(m, scene=g + scene, view=g + 0x172D, colour=0)
        if arg == 0:
            draw_entities(m, scene=g + 0x0D93, view=g + 0x172D, colour=0x90)
        if u8(g + 0x178B):
            draw_entities(m, scene=g + 0x178C, view=g + 0x1741, colour=0x90)
        for i in panels:
            blit_rows_masked(m, table=far(far(g + 0x210C + i * 4)),
                             rect=g + 0x2118 + i * 0x14, srcrow=0)
        if number_on:
            draw_number(m, number, 0x80, 0x22, g + 0x1741, 0x90, 6)
        if level_on:
            draw_number(m, u16(g + 0x2007), 0xE1, 0x22, g + 0x1741, 0x90, 2)
        if stride is not None:
            for px in range(px0, px1):
                plot_pixel(m, px, pixel_y, 0, stride)


'''

LOOP_TABLE_OLD = (
    "# Loop head -> (exit address, handler). Replaced at the instruction, like the\n"
    "# interrupt stubs: there is no function entry to hook, the loop is inline.\n"
    "PLANE_LOOPS = {0x0CD5F: (0x0CD98, plane_loop_layer)}\n")
LOOP_TABLE_NEW = (
    '# Loop head -> (exit address, handler). Replaced at the instruction, like the\n'
    '# interrupt stubs: there is no function entry to hook, the loop is inline.\n'
                  'PLANE_LOOPS = {\n'
                  '    0x0CD5F: (0x0CD98, plane_loop_layer),\n'
                  '    0x0E4DC: (0x0E673, plane_loop_scroll),\n'
                  '}\n')

EDITS = [
    (PLOT_OLD, PLOT_NEW),
    (MASKED_OLD, MASKED_NEW),
    (SCROLL_OLD, SCROLL_NEW),
    (SCROLL_ARGS_OLD, ""),
    # The loop goes in before the table that names it.
    (LOOP_TABLE_OLD, LOOP_NEW + LOOP_TABLE_NEW),

    # Two loops now, so say which one in the counters and the verify lines -
    # "plane_loop" alone would lump their timings together, and the hoist
    # decisions ahead depend on telling them apart. The --verify-only key stays
    # "plane_loop" so it selects both.
    ('        self.native_calls["plane_loop"] += 1\n',
     '        label = f"plane_loop {head:#07x}"\n'
     '        self.native_calls[label] += 1\n'),
    ('        self.native_secs["plane_loop"] += time.perf_counter() - t0\n',
     '        self.native_secs[label] += time.perf_counter() - t0\n'),
    ('            print(f"  [verify] plane_loop raised {e!r}")\n',
     '            print(f"  [verify] plane_loop {head:#07x} raised {e!r}")\n'),
    ('                print(f"  [verify] plane_loop MISMATCH {bad} bytes, first "\n',
     '                print(f"  [verify] plane_loop {head:#07x} MISMATCH {bad} "\n'
     '                      f"bytes, first "\n'),
    ('                print(f"  [verify] plane_loop: match #{self.verify_calls}")\n',
     '                print(f"  [verify] plane_loop {head:#07x}: match "\n'
     '                      f"#{self.verify_calls}")\n'),

    # One-shot warning flag for an unrecognised [0x53e].
    ("        self.warp_calls = 0\n",
     "        self.warp_calls = 0\n"
     "        self.plot_pixel_warned = False\n"),
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
    print("native.py updated: plane_loop_scroll added, three cores split out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
