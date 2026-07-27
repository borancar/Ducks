#!/usr/bin/env python3
"""Record why the HUD loop runs exactly twice a level.

Two comparisons in a 531-frame session reads like thin coverage until you find the
outer loop: the HUD is drawn once into each of the two video pages at level start,
with a page flip between, and is not touched again. The score that visibly updates
every frame is the scroll loop's draw_number, not this. So two is the whole
population per level, not a sample of it.

    venv/bin/python edit_hud_note.py
"""
import sys

SRC = "native.py"

OLD = '''    Each item's sprite is its type - a word from the array at [0x1782] - looked up
    in the per-type sprite tables at DGROUP 0x9a, taking the first entry. This is
    the call site that exercises the outline: 8 to 16 calls a session in ordinary
    play, which is why 0x65f1 is verified even though the shadow path inside
    draw_entities has never been shown to fire.
    """
'''

NEW = '''    Each item's sprite is its type - a word from the array at [0x1782] - looked up
    in the per-type sprite tables at DGROUP 0x9a, taking the first entry. This is
    the call site that exercises the outline: 8 to 16 calls a session in ordinary
    play, which is why 0x65f1 is verified even though the shadow path inside
    draw_entities has never been shown to fire.

    It runs exactly twice a level, which is worth knowing before reading a
    verification count. An outer loop over [bp-0x29] draws the whole HUD once into
    each of the two video pages, with a page flip (0x14d4b) between them, and that
    is the only time it is drawn - which is also why the loop is jumped into at
    0x0db39 rather than fallen into. The score that updates visibly every frame is
    the scroll loop's draw_number, not this one. So two comparisons in a session is
    the entire population for one level, not a thin sample of a busy loop.
    """
'''


def main():
    src = open(SRC).read()
    if src.count(OLD) != 1:
        print(f"anchor found {src.count(OLD)} times, nothing written")
        return 1
    open(SRC, "w").write(src.replace(OLD, NEW, 1))
    print("native.py updated: HUD cadence recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
