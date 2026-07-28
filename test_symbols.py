#!/usr/bin/env python3
"""Keep symbols.py honest against the tables it is naming.

Names are printed next to addresses in the socket's `where`, and so in `stack`,
`until`, `finish` and `step`. A stale name is worse than none, for the reason
test_fn_start.py gives about labels: it reads as a fact.

Two things can drift. A native can be added to native.py and never named, so its
address reports bare while every neighbour reports a name. Or a name can be
changed in one place and not the other, so the socket and the exit report
disagree about the same routine - which is exactly the kind of small
inconsistency that costs an hour later.

    venv/bin/python test_symbols.py
"""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import native                                                     # noqa: E402
import symbols                                                    # noqa: E402


# Entries that are genuine call targets without a Borland frame, each checked by
# hand: the C runtime's entry, an inner entry inside the INT 10h wrapper, and a
# leaf that keeps no frame pointer.
NO_PROLOGUE = {0x0014E, 0x02067, 0x029FC}


def image():
    import struct
    d = open("Ducks.unpacked.exe", "rb").read()
    return d[struct.unpack_from("<13H", d, 2)[3] * 16:]


def natives():
    return (native.NATIVE_TABLE + native.SOUND_NATIVES
            + native.MOUSE_NATIVES + native.KEYBOARD_NATIVES
            + native.FILE_NATIVES + native.XMS_NATIVES
            + native.SETUP_NATIVES + native.FLIP_NATIVES)


def main():
    bad = []

    for off, name, _fn, _kind in natives():
        got = symbols.name(off)
        if got is None:
            bad.append(f"{off:#07x} is a native called {name!r} with no symbol")
        elif got != name:
            bad.append(f"{off:#07x} is {name!r} in native.py "
                       f"but {got!r} in symbols.py")

    for head in list(native.PLANE_LOOPS) + list(native.DAC_LOOPS):
        if symbols.name(head) is None:
            bad.append(f"{head:#07x} is a hooked loop head with no symbol")

    # A loop head is not a function entry, and putting one in FUNCTIONS would
    # make `where` claim a function starts where it does not.
    for head in list(native.PLANE_LOOPS) + list(native.DAC_LOOPS):
        if head in symbols.FUNCTIONS:
            bad.append(f"{head:#07x} is a loop head but sits in FUNCTIONS")

    # Tentative names must be marked, not silently asserted.
    for off, name in {**symbols.FUNCTIONS, **symbols.LOOPS}.items():
        if name.endswith("?") and "(tentative)" not in symbols.describe(off):
            bad.append(f"{off:#07x} is tentative but does not print as such")

    # Every FUNCTIONS entry must actually begin a function. This is the check
    # that catches a near-call target read off a disassembly: `call rel16` wraps
    # within its segment, and image offsets are not segment offsets, so a target
    # computed in image space can be a whole 64 KB out. egg_find_block was
    # recorded at 0x15232 - mid-instruction inside play_sample - when it is at
    # 0x05232, and only the prologue check found it.
    img = image()
    for off, name in symbols.FUNCTIONS.items():
        if off in NO_PROLOGUE:
            continue
        if img[off:off + 3] != b"\x55\x8b\xec":
            bad.append(f"{off:#07x} {name!r} does not start with push bp; "
                       f"mov bp, sp - it is {img[off:off+4].hex()}")

    # Variables live in a third address space - DGROUP offsets - and the only
    # thing that can be checked without a running machine is that they are
    # plausible offsets and that tentative ones say so.
    for off, name in symbols.VARIABLES.items():
        if not 0 <= off < 0x10000:
            bad.append(f"{off:#07x} {name!r} is not a DGROUP offset")
        if name.endswith("?") and "(tentative)" not in symbols.describe_variable(off):
            bad.append(f"d+{off:#07x} is tentative but does not print as such")
    dupes = [v for v in set(symbols.VARIABLES.values())
             if list(symbols.VARIABLES.values()).count(v) > 1]
    for v in dupes:
        bad.append(f"variable name {v!r} is used more than once")

    n = len(natives()) + len(native.PLANE_LOOPS) + len(native.DAC_LOOPS)
    if bad:
        print(f"FAIL: {len(bad)} problem(s) across {n} named address(es)")
        for b in bad:
            print(f"  {b}")
        return 1
    print(f"OK: {n} natives and loop heads all named, the names agree with "
          f"native.py, and {len(symbols.VARIABLES)} variables check out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
