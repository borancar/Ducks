#!/usr/bin/env python3
"""How much of Ducks now runs as Python rather than as emulated 8086.

Two things make a raw "bytes of the image" percentage misleading, so this reports
against a denominator that excludes both. The image is 114812 bytes but DGROUP
starts at 0x18950, so a seventh of it is data; and inside the code region the space
between function prologues includes jump tables and padding that no CPU executes as
part of any function. The denominator here is the sum of the extents of the 390
real functions, measured with function_extent and walked forward so they cannot
overlap - 28 of the 423 prologue byte matches sit inside another function, and
counting those as functions too gives more "function bytes" than the code region
has.

It counts only code that has been *replaced* - where the guest's bytes no longer
execute. Functions that have been decoded and written up but still run on the
emulated CPU are not in here, which is why this number is smaller than a count of
"functions we understand".

    venv/bin/python coverage.py
"""
import struct
import sys
from collections import defaultdict

from native import (NATIVE_TABLE, SETUP_NATIVES, XMS_NATIVES, FILE_NATIVES,
                    KEYBOARD_NATIVES, MOUSE_NATIVES, SOUND_NATIVES, PLANE_LOOPS,
                    STUBBED_INT_SITES, function_extent, _entry_table,
                    _function_map)

EXE = "Ducks.unpacked.exe"
DGROUP = 0x18950

# Borland runtime versus the game's own code. The runtime routines were replaced to
# get the interrupts out, and they are not what "reversing Ducks" means, so they are
# counted apart from the game's own drawing.
RUNTIME = set(SETUP_NATIVES + XMS_NATIVES + FILE_NATIVES + KEYBOARD_NATIVES)


def main():
    d = open(EXE, "rb").read()
    img = d[struct.unpack_from("<13H", d, 2)[3] * 16:]
    offs, _ = _entry_table(img)

    # Denominator: the function map, whose spans are disjoint by construction. The
    # first version of this script summed every prologue match's extent and got
    # more "function bytes" than the code region has - which is what turned up the
    # bug in find_function_start.
    _, spanlist = _function_map(img)
    spans = {s: e - s for s, e in spanlist}
    total_fn = sum(spans.values())
    # Strictly inside a span, which is not the same as "not a span's start": a few
    # matches are skipped for having no terminating return rather than for being
    # nested, and lumping the two together mislabels both.
    nested = sum(1 for o in offs if any(s < o < e for s, e in spanlist))
    unswept = len(offs) - len(spans) - nested

    groups = [("game routines", NATIVE_TABLE), ("sound", SOUND_NATIVES),
              ("mouse", MOUSE_NATIVES), ("Borland runtime + BIOS/DOS",
                                         sorted(RUNTIME))]
    replaced, by_group = {}, defaultdict(lambda: [0, 0])
    for name, table in groups:
        for off, fn, _, _ in table:
            s, end = function_extent(img, off)
            n = (end - s) if end and s == off else 0
            replaced[off] = n
            by_group[name][0] += 1
            by_group[name][1] += n

    loops = sum(exit_off - head for head, (exit_off, _) in PLANE_LOOPS.items())
    stubs = 2 * len(STUBBED_INT_SITES)
    fn_bytes = sum(replaced.values())

    print(f"image                 {len(img):>7} bytes")
    print(f"  code region         {DGROUP:>7} bytes  (DGROUP starts at "
          f"{DGROUP:#07x})")
    print(f"  in real functions   {total_fn:>7} bytes  ({len(spans)} functions; "
          f"{len(offs)} prologue byte matches: {nested} inside another "
          f"function, {unswept} with no terminating return)\n")

    print("replaced by native code:")
    for name, _ in groups:
        n, b = by_group[name]
        print(f"  {name:<28} {n:>3} functions  {b:>6} bytes")
    print(f"  {'plane loops (inline)':<28} {len(PLANE_LOOPS):>3} loops      "
          f"{loops:>6} bytes")
    print(f"  {'interrupt sites (inline)':<28} {len(STUBBED_INT_SITES):>3} sites"
          f"      {stubs:>6} bytes")

    conv = fn_bytes + loops + stubs
    print(f"\n  total                                      {conv:>6} bytes")
    print(f"  as a share of code in known functions      "
          f"{100 * conv / total_fn:>5.1f}%")
    print(f"  as a share of the code region              "
          f"{100 * conv / DGROUP:>5.1f}%")
    print(f"  as a share of the whole image              "
          f"{100 * conv / len(img):>5.1f}%")

    game = by_group["game routines"][1] + by_group["sound"][1] \
        + by_group["mouse"][1] + loops
    print(f"\n  of that, the game's own code (not runtime) {game:>6} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
