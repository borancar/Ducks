#!/usr/bin/env python3
"""Make find_function_start reliable, and stop it failing silently.

Two faults. Its window was 0x600 bytes, smaller than several functions in this
image - the in-game frame at 0x0d7ee is 4287 bytes, so every address past its
first 1536 resolved to None and dropped out of the reports that attribute an
address to a function. And the nearest 55 8b ec backwards is not necessarily an
entry: those three bytes occur in data and inside longer instructions.

The fix indexes all 423 prologues once, bisects to the nearest one at or before
the target, and confirms it by sweeping forward - stopping at any return that
lands on another known entry, so a sweep cannot run out of one function and claim
an address in the next.

    venv/bin/python edit_fn_start.py
"""
import sys

SRC = "native.py"

OLD = '''def find_function_start(img, off, limit=0x600):
    """Scan back for a Borland function prologue (push bp; mov bp,sp)."""
    for back in range(0, limit):
        i = off - back
        if i < 2:
            break
        if img[i] == 0x55 and img[i + 1] == 0x8B and img[i + 2] == 0xEC:
            return i
        # Also accept the retf/ret that ends the previous function, meaning the
        # next byte begins this one.
        if img[i] in (0xCB, 0xC3) and img[i + 1] == 0x55:
            return i + 1
    return None
'''

NEW = '''# Big enough for the largest function in this image: the in-game frame runs
# 0x0d7ee-0x0e8ac, 4287 bytes. The old window was 0x600, so addresses deep inside
# it resolved to nothing at all.
FUNCTION_SCAN_LIMIT = 0x2000

_fn_entries = {}
_fn_start_cache = {}
_cs16 = None


def _disasm16():
    """A 16-bit capstone, or None if it is not installed.

    Imported lazily: this is only wanted to confirm a prologue for the exit
    reports, so the game should not pay for the import in order to run.
    """
    global _cs16
    if _cs16 is None:
        try:
            import capstone
            _cs16 = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_16)
            _cs16.detail = False
        except Exception:
            _cs16 = False
    return _cs16 or None


def _entry_table(img):
    """Every `push bp; mov bp,sp` in the image, sorted, with a set for lookups.

    Built once - 423 of them here - so resolving an address is a bisect plus a
    confirmation or two. Confirming every byte match inside a window instead meant
    thousands of forward sweeps for any address sitting in data.
    """
    key = len(img)
    table = _fn_entries.get(key)
    if table is None:
        offs = [o for o in range(len(img) - 2)
                if img[o] == 0x55 and img[o + 1] == 0x8B and img[o + 2] == 0xEC]
        table = (offs, set(offs))
        _fn_entries[key] = table
    return table


def _sweep_reaches(img, start, off, entries):
    """Does a linear sweep from `start` land exactly on the instruction at `off`?

    This is what separates a function entry from three bytes that merely read
    55 8b ec. It is not proof - x86 sweeps resynchronise, so a false start can
    still reach the target by accident - but a candidate that does not reach it is
    definitely not the function containing it.

    A return whose next byte begins another known entry ends the sweep: past that
    point the code belongs to the next function, not this one. That is what keeps
    a frameless function's addresses from being credited to its predecessor.
    """
    md = _disasm16()
    if md is None:
        return True                       # unconfirmable; take the byte match
    for i in md.disasm(bytes(img[start:off + 16]), start):
        if i.address == off:
            return True
        if i.address > off:
            return False                  # swept straight past it: desynced
        if i.mnemonic in ("ret", "retf") and (i.address + i.size) in entries:
            return False                  # the function ended before `off`
    return False


def find_function_start(img, off, limit=FUNCTION_SCAN_LIMIT, tries=8):
    """The Borland prologue beginning the function that contains `off`, or None.

    None now means what it says: no indexed entry within `limit` bytes sweeps to
    `off`. With the old 0x600 window it usually just meant the function was too
    big, which is a different thing and was indistinguishable from outside.
    """
    key = (len(img), off, limit)
    if key in _fn_start_cache:
        return _fn_start_cache[key]

    offs, entries = _entry_table(img)
    idx = bisect.bisect_right(offs, off)
    found = None
    for k in range(idx - 1, max(-1, idx - 1 - tries), -1):
        cand = offs[k]
        if off - cand > limit:
            break
        if cand == off or _sweep_reaches(img, cand, off, entries):
            found = cand
            break
    _fn_start_cache[key] = found
    return found
'''

IMPORT_OLD = "import argparse\nimport os\n"
IMPORT_NEW = "import argparse\nimport bisect\nimport os\n"


def main():
    src = open(SRC).read()
    for old, new in ((OLD, NEW), (IMPORT_OLD, IMPORT_NEW)):
        if src.count(old) != 1:
            print(f"anchor found {src.count(old)} times, nothing written: "
                  f"{old[:40]!r}")
            return 1
        src = src.replace(old, new, 1)
    open(SRC, "w").write(src)
    print("native.py updated: find_function_start indexes, confirms, and no "
          "longer fails silently")
    return 0


if __name__ == "__main__":
    sys.exit(main())
