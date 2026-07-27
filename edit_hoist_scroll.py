#!/usr/bin/env python3
"""Hoist the scrolling compositor's plane-independent work out of the loop.

The plane loop called compose_scroll four times with the same arguments. Each call
read the same two row tables, fetched an overlapping window of the same foreground
rows, and expanded the same background rows again. Only the column selection and
the destination write really differ per plane.

Same treatment compose_layer already had: a shared half and a per-plane half. A
lone call runs both, so there is one definition of the algorithm rather than a
hoisted copy and a per-call copy - and the only caller of 0x05dc4 in the image is
the plane loop, which wants all four planes anyway.

Two surgical edits, either side of the warp warning, so that text is left alone.

    venv/bin/python edit_hoist_scroll.py
"""
import sys

SRC = "native.py"

HEAD_OLD = '''def compose_scroll(m, argx, argy):
    """The scrolling compositor at 0x05dc4, with its arguments resolved.

    Split out of the native above so the scroll caller's plane loop can compose
    without paying a second dispatch per plane.

    Like compose_layer,'''

HEAD_NEW = '''def compose_scroll(m, argx, argy):
    """One plane of the scrolling compositor at 0x05dc4.

    Runs both halves of the split below, so a single call behaves exactly as it
    always did and there is one definition of the algorithm rather than a hoisted
    copy and a per-call copy. What it costs is reading the full row window instead
    of this plane's quarter of it - the right trade at one call site, since the
    only caller of 0x05dc4 in the image is the plane loop and that wants all four.
    """
    shared = compose_scroll_shared(m, argx, argy)
    if shared is None:
        return None
    compose_scroll_plane(m, shared, m.read(m.dgroup_base + 0x177D, 1)[0] & 3)
    return None


def compose_scroll_shared(m, argx, argy):
    """Everything the scrolling compositor computes that is not per-plane.

    Reading the two row tables, fetching the foreground rows and expanding the
    background rows are all independent of the selected plane, and the loop was
    paying for them four times a frame.

    Returns None when there is nothing to draw, which is what the whole call did
    in that case.

    Like compose_layer,'''

TAIL_OLD_START = "    stride = 90 if u16(g + 0x4FE) else 80\n"

TAIL_NEW = '''    row_bytes = 90 if u16(g + 0x4FE) else 80
    dst = (far(g + 0x16F1) - 0xA0000 + row0 * row_bytes
           + u16(g + 0x1727) + (s16(g + 0x1731) >> 2))
    nrows = max(0, row_end - row0)
    if dst < 0 or nrows == 0:
        return None

    # One read has to cover every plane, so it runs to the widest byte any of the
    # four will ask for rather than to a single plane's span.
    spans = [max(0, (right - p + 3) // 4) for p in range(4)]
    width = max([p + 4 * (s - 1) + 1 for p, s in enumerate(spans) if s] or [0])
    if width <= 0:
        return None
    fg_rows = read_row_table(m, far(g + 0x16F5), argy + nrows)
    bg_rows = read_row_table(m, far(g + 0x170B), mask_y + 1)
    fg_data = bulk_rows(m, fg_rows[argy:argy + nrows], argx, width)
    if len(fg_data) < nrows:
        return None
    fg = np.frombuffer(b"".join(fg_data), dtype=np.uint8).reshape(nrows, -1)

    # The background rows, expanded once. Only the distinct ones are read: the
    # wrap mask means the same few recur down the region.
    by = (((argy >> 1) + u8(g + 0x177F)
           + np.arange(row0, row_end, dtype=np.int32)) & mask_y)
    uniq, inv = np.unique(by, return_inverse=True)
    bg = np.stack([np.frombuffer(m.cached_read(bg_rows[b], mask_x + 1),
                                 dtype=np.uint8) for b in uniq])[inv]

    shifts = None
    if warp_on:
        # Each row takes its x displacement from a 32-entry table, stepped per
        # row. Advanced in Python because the phase is re-masked to 0x1f every
        # row, which is not a plain arithmetic progression - the reason this is not
        # simply base_x + table[(phase + r * step) & 0x1f].
        #
        # Still UNVERIFIED, for the same reason as ever: the warp has never run.
        warp = m.read(g + 0x179F, 32)
        shifts = np.empty(nrows, dtype=np.int32)
        ph = phase
        for r in range(nrows):
            ph &= 0x1F
            shifts[r] = base_x + warp[ph]
            ph = (ph + step) & 0xFF
    return dst, row_adv, right, mask_x, base_x, fg, bg, shifts, nrows


def compose_scroll_plane(m, shared, plane):
    """One plane's worth of the scrolling compositor, from the hoisted arrays.

    The plane picks its columns as a stride of the window that was read once, and
    the background displacement is applied to that selection. Foreground pixel
    wins unless it is zero, in which case the wrapped background shows through.
    """
    dst, row_adv, right, mask_x, base_x, fg_all, bg, shifts, nrows = shared
    if not m.active_planes:
        return
    span = max(0, (right - plane + 3) // 4)
    fg = fg_all[:, plane::4][:, :span]
    ncols = fg.shape[1]
    if ncols == 0:
        return
    cols = np.arange(ncols, dtype=np.int32) * 4 + plane
    if shifts is None:
        sel = bg[:, (cols + base_x) & mask_x]
    else:
        sel = np.take_along_axis(bg, (cols[None, :] + shifts[:, None]) & mask_x,
                                 axis=1)
    out = np.where(fg == 0, sel, fg)
    stride = ncols + row_adv
    if stride <= 0:
        return
    for p in m.active_planes:
        pl = m.planes[p]
        # Write the rows that fit, as the row-at-a-time version did, rather than
        # dropping the whole region when the last one runs past the plane.
        fit = min(nrows, (len(pl) - dst - ncols) // stride + 1)
        if fit <= 0:
            continue
        view = np.frombuffer(pl, dtype=np.uint8)
        np.lib.stride_tricks.as_strided(
            view[dst:], shape=(fit, ncols), strides=(stride, 1))[:] = out[:fit]
    m.native_pixels += nrows * ncols
    m.rows_done = nrows
'''

LOOP_OLD = '''    for plane in range(4):
        set_plane(m, plane)
        compose_scroll(m, scroll_x, scroll_y)
'''

LOOP_NEW = '''    # The compositor's plane-independent work, done once instead of four times -
    # which is the point of owning the loop. None means it would draw nothing, so
    # the per-plane call is skipped rather than repeated four times to find out.
    # (m.warp_calls therefore counts loops now, not calls; it exists only to say
    # whether the warp has ever run.)
    shared = compose_scroll_shared(m, scroll_x, scroll_y)

    for plane in range(4):
        set_plane(m, plane)
        if shared is not None:
            compose_scroll_plane(m, shared, plane)
'''


def main():
    src = open(SRC).read()
    if src.count(HEAD_OLD) != 1:
        print("head anchor missing, nothing written")
        return 1
    if src.count(LOOP_OLD) != 1:
        print("loop anchor missing, nothing written")
        return 1

    # The tail runs from the resolution check to the end of the function, which is
    # the next top-level def.
    start = src.index(TAIL_OLD_START, src.index(HEAD_OLD))
    end = src.index("\n\ndef ", start) + 1
    src = src[:start] + TAIL_NEW + src[end:]
    src = src.replace(HEAD_OLD, HEAD_NEW, 1).replace(LOOP_OLD, LOOP_NEW, 1)
    open(SRC, "w").write(src)
    print("native.py updated: compose_scroll split into shared and per-plane")
    return 0


if __name__ == "__main__":
    sys.exit(main())
