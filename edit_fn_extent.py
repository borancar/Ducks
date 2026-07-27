#!/usr/bin/env python3
"""Add function_extent: where a function ends, not just where it starts.

find_function_start answers half the question. Deciding whether a local is read
after a loop, or whether a byte pattern is inside the function you think it is,
needs the other end - and until now that was done by hand each time, with an
arbitrary window that overshot the in-game frame by 1.4 KB.

    venv/bin/python edit_fn_extent.py
"""
import sys

SRC = "native.py"

ANCHOR = '''# ---------------------------------------------------------------- natives ---
'''

NEW = '''def function_extent(img, off, limit=FUNCTION_SCAN_LIMIT):
    """(start, end) of the function containing `off`; end is exclusive.

    Borland emits one epilogue per function and packs functions back to back, so
    the end is the first return - swept from the entry - whose next byte begins
    another indexed prologue. Everything after that belongs to the next function.

    Cross-checked against an independent rule, "the next prologue that resolves to
    itself": the two agree on every function this port has named, including the
    two that hold plane loops. test_fn_start.py keeps them agreeing.

    (None, None) when the entry cannot be found, and (start, None) when no such
    return appears within `limit` - which would mean the sweep desynced or the
    function is larger than the window, and either way is not a boundary to guess.
    """
    start = find_function_start(img, off, limit)
    if start is None:
        return None, None
    md = _disasm16()
    if md is None:
        return start, None
    _, entries = _entry_table(img)
    norm = _fp_normalised(img)
    for i in md.disasm(norm[start:start + limit], start):
        if i.mnemonic in ("ret", "retf") and (i.address + i.size) in entries:
            return start, i.address + i.size
    return start, None


# ---------------------------------------------------------------- natives ---
'''


def main():
    src = open(SRC).read()
    if src.count(ANCHOR) != 1:
        print(f"anchor found {src.count(ANCHOR)} times, nothing written")
        return 1
    open(SRC, "w").write(src.replace(ANCHOR, NEW, 1))
    print("native.py updated: function_extent added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
