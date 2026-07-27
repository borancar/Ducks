#!/usr/bin/env python3
"""Drop what the split left behind in compose_scroll_shared.

The plane read is dead there now - it is the per-plane half that needs it - and the
inherited docstring line about one call filling one plane describes the other half.

    venv/bin/python edit_hoist_cleanup.py
"""
import sys

SRC = "native.py"

EDITS = [
    ("""    pixel wins unless it is zero, in which case the wrapped background shows
    through. Column steps 4, so one call fills a single Mode X plane.
    \"\"\"
    g = m.dgroup_base""",
     """    pixel wins unless it is zero, in which case the wrapped background shows
    through, which is the part compose_scroll_plane does.
    \"\"\"
    g = m.dgroup_base"""),
    ("""    mask_x, mask_y = u16(g + 0x1729), u16(g + 0x172B)
    plane = u8(g + 0x177D)
    warp_on = u16(g + 0x2022) != 0""",
     """    mask_x, mask_y = u16(g + 0x1729), u16(g + 0x172B)
    warp_on = u16(g + 0x2022) != 0"""),
]


def main():
    src = open(SRC).read()
    for old, new in EDITS:
        if src.count(old) != 1:
            print(f"anchor found {src.count(old)} times, nothing written")
            return 1
        src = src.replace(old, new, 1)
    open(SRC, "w").write(src)
    print("native.py updated: dead plane read and stale docstring line removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
