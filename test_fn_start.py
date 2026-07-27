#!/usr/bin/env python3
"""Pin find_function_start to answers established elsewhere.

Every routine this port replaces was located by reading the disassembly, and the
addresses inside them come from the same reading - so they make a ground truth the
helper can be measured against. That matters because the helper labels addresses
in the exit reports, and a wrong label is worse than no label: it reads as a fact.

    venv/bin/python test_fn_start.py
"""
import bisect
import struct
import sys

import capstone

from native import (find_function_start, function_extent, _entry_table,
                    _fp_normalised, FUNCTION_SCAN_LIMIT)

EXE = "Ducks.unpacked.exe"

# Function entries this port has replaced or decoded. Each must resolve to itself.
ENTRIES = {
    0x04D2A: "clear_vram",       0x05761: "plot_pixel 320",
    0x057A1: "plot_pixel 360",   0x057EE: "set_plane",
    0x05AC2: "blit_rows_masked", 0x05C09: "blit_rows",
    0x05D3A: "compose_layer",    0x05DC4: "compose_scroll",
    0x063D6: "draw_sprite",      0x065F1: "outline_sprite",
    0x078D4: "set_entity_type",  0x0AB09: "particles",
    0x0ABA5: "draw_entities",    0x0BB3B: "draw_number",
    0x0D7EE: "in-game frame",
}

# Addresses inside a function, and which function they belong to. The frame
# function is the case the old 0x600 window could not reach: 0x0e4dc is 3310
# bytes past its prologue, and 0x0e8ab is 4285.
INSIDE = {
    0x0E4DC: 0x0D7EE,   # the scroll plane loop's head
    0x0E4E8: 0x0D7EE,   # its set_plane call
    0x0E620: 0x0D7EE,   # its draw_number call
    0x0E673: 0x0D7EE,   # the loop exit
    0x0E8AB: 0x0D7EE,   # the function's one and only leave;retf
    0x0D9A2: 0x0D7EE,   # the other plane loop in the same function
    0x0BC75: 0x0BBA1,   # draw_number call site in the score tally
    0x0AF7B: 0x0ABA5,   # draw_sprite call site inside the entity loop
}


# Function extents, by size in bytes. The two that hold plane loops are the
# reason this exists: deciding that nothing after a loop reads its counter needs
# the far end of the function, and the window used by hand overshot the frame
# function by 1.4 KB.
EXTENTS = {
    0x0E4DC: 4287,   # the in-game frame, 0x0d7ee-0x0e8ac: two plane loops
    0x0CD5F: 1816,   # the layer caller, 0x0c716-0x0ce2d
    0x0BBA1:  351,   # the score tally, another plane loop
    0x0BB3B:  102,   # draw_number
    0x078D4:   35,   # set_entity_type, the 35 bytes test_retire.py drives
    0x05761:   64,   # plot_pixel 320, ending exactly where its 360 twin starts
    0x063D6:  539,   # draw_sprite, ending exactly where outline_sprite starts
}


def old_find_function_start(img, off, limit=0x600):
    """What the helper did before, kept here to quantify the difference."""
    for back in range(0, limit):
        i = off - back
        if i < 2:
            break
        if img[i] == 0x55 and img[i + 1] == 0x8B and img[i + 2] == 0xEC:
            return i
        if img[i] in (0xCB, 0xC3) and img[i + 1] == 0x55:
            return i + 1
    return None


def main():
    d = open(EXE, "rb").read()
    img = d[struct.unpack_from("<13H", d, 2)[3] * 16:]
    bad = 0

    print(f"scan limit {FUNCTION_SCAN_LIMIT:#x}\n")
    print("entries resolve to themselves:")
    for off, name in sorted(ENTRIES.items()):
        got = find_function_start(img, off)
        ok = got == off
        bad += not ok
        print(f"  {off:#07x} {name:<18} -> "
              f"{'self' if ok else (f'{got:#07x}' if got else 'None')}"
              f"  {'ok' if ok else 'WRONG'}")

    print("\naddresses inside a function resolve to its entry:")
    for off, want in sorted(INSIDE.items()):
        got = find_function_start(img, off)
        ok = got == want
        bad += not ok
        print(f"  {off:#07x} -> {(f'{got:#07x}' if got else 'None'):<9}"
              f" want {want:#07x}  {'ok' if ok else 'WRONG'}")

    # The real coverage measure. Sampling arbitrary byte offsets says nothing:
    # most of them are mid-instruction, where None is the right answer and the old
    # helper's confident reply was simply wrong. So sweep each function from its
    # own entry and ask about the addresses that are actually instructions.
    #
    # Sweeping the raw bytes to get those boundaries does not work either. Borland
    # encodes x87 as a two-byte INT followed by the ModRM the FWAIT+ESC pair used
    # to carry, so a raw sweep decodes operand bytes as instructions and invents
    # boundaries that no CPU ever executes - 0x0044b, inside the FP site at
    # 0x00449, is one. The ground truth has to come from the same normalised bytes
    # the helper uses, or it is measuring phantoms.
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_16)
    md.detail = False
    img = _fp_normalised(img)
    offs, _ = _entry_table(img)
    tested = ok_new = ok_old = 0
    misses = []
    for e, nxt in zip(offs, offs[1:]):
        if nxt - e > FUNCTION_SCAN_LIMIT:
            continue
        for i in list(md.disasm(bytes(img[e:nxt]), e))[::7]:
            tested += 1
            got = find_function_start(img, i.address)
            ok_new += got == e
            ok_old += old_find_function_start(img, i.address) == e
            if got != e:
                misses.append((i.address, e, got))
    print(f"\ninstruction boundaries inside a known function: {tested} tested")
    print(f"  now: {ok_new} correct ({100 * ok_new / tested:.1f}%)")
    print(f"  before: {ok_old} correct ({100 * ok_old / tested:.1f}%)")
    for a, e, g in misses[:6]:
        print(f"   {a:#07x} in {e:#07x} -> {g if g is None else hex(g)}")
    bad += len(misses)

    # Extents, against an independent rule: the next prologue that resolves to
    # itself. function_extent instead sweeps to the first return landing on an
    # indexed prologue, so agreement between the two is real corroboration rather
    # than the same idea checked twice.
    print("\nfunction extents, and the two rules agreeing:")
    for off, want_size in EXTENTS.items():
        start, end = function_extent(img, off)
        k = bisect.bisect_right(offs, start)
        alt = next((e for e in offs[k:] if find_function_start(img, e) == e), None)
        ok = end is not None and end - start == want_size and end == alt
        bad += not ok
        print(f"  {off:#07x} -> {start:#07x}-{(end - 1) if end else 0:#07x}"
              f"  {(end - start) if end else 0:>5} bytes  want {want_size:>5}"
              f"  next-entry rule {'agrees' if end == alt else 'DISAGREES'}"
              f"  {'ok' if ok else 'WRONG'}")

    # And nothing may ever claim an entry that comes after the address itself.
    ahead = [o for o in range(0, len(img), 97)
             if (f := find_function_start(img, o)) is not None and f > o]
    print(f"\nentries reported after their own address: {len(ahead)}")
    bad += len(ahead)

    print("\nall correct" if not bad else f"\n{bad} case(s) wrong")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
