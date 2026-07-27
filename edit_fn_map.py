#!/usr/bin/env python3
"""Stop find_function_start believing any byte match that is queried directly.

It short-circuited on `cand == off`, so an address that happens to be in the
prologue table got itself back as its own function start - with no confirmation at
all. 28 of this image's 423 `55 8b ec` matches sit inside another function, and each
of those was its own answer. It showed up in coverage.py, which measured more bytes
"inside functions" than the code region has, because the extents overlapped.

The fix inverts the structure. Instead of searching backwards from the query and
confirming a candidate, the functions are walked forward once into disjoint spans -
a match inside a span already measured is not an entry - and a query is a bisect
into that map. An address in no span gets None, which is the honest answer for
padding and jump tables.

    venv/bin/python edit_fn_map.py
"""
import sys

SRC = "native.py"

OLD_START = "def _sweep_reaches(img, start, off, entries):"
OLD_END = "\n\ndef function_extent("

NEW = '''def _sweep_end(md, norm, entries, start, limit):
    """Where the function at `start` ends, by sweeping to its terminating return.

    Borland emits one epilogue per function and packs functions back to back, so
    the end is the first return whose next byte begins another prologue match.
    None means no such return inside `limit` - the sweep desynced, or this is not
    a function at all.
    """
    for i in md.disasm(norm[start:start + limit], start):
        if i.mnemonic in ("ret", "retf") and (i.address + i.size) in entries:
            return i.address + i.size
    return None


def _function_map(img, limit=None):
    """Disjoint (start, end) spans for every real function, built once.

    Walking forward is what makes them disjoint, and disjoint is what makes them
    trustworthy: a prologue byte match that falls inside a function already
    measured is not an entry, whatever its bytes say. 28 of the 423 matches here
    are exactly that.

    Returns (starts, spans) for bisecting, or None when capstone is absent and
    nothing can be swept.
    """
    limit = FUNCTION_SCAN_LIMIT if limit is None else limit
    key = (len(img), limit)
    cached = _fn_map_cache.get(key)
    if cached is None:
        md = _disasm16()
        if md is None:
            return None
        offs, entries = _entry_table(img)
        norm = _fp_normalised(img)
        spans, i = [], 0
        while i < len(offs):
            start = offs[i]
            end = _sweep_end(md, norm, entries, start, limit)
            i += 1
            if end is None:
                continue
            spans.append((start, end))
            while i < len(offs) and offs[i] < end:
                i += 1        # inside this function, so not an entry
        cached = ([s for s, _ in spans], spans)
        _fn_map_cache[key] = cached
    return cached


def _enclosing(img, off, limit):
    """The (start, end) span containing `off`, or None."""
    themap = _function_map(img, limit)
    if themap is None:
        return None
    starts, spans = themap
    k = bisect.bisect_right(starts, off) - 1
    if k < 0:
        return None
    start, end = spans[k]
    return (start, end) if start <= off < end else None


def _find_function_start_bytes(img, off, limit):
    """The old backwards byte scan, for when capstone is not installed.

    Kept because it is better than nothing, and honest about being worse: it
    returns the nearest matching byte triple, which is only usually the entry.
    """
    for back in range(0, limit):
        i = off - back
        if i < 2:
            break
        if img[i] == 0x55 and img[i + 1] == 0x8B and img[i + 2] == 0xEC:
            return i
        if img[i] in (0xCB, 0xC3) and img[i + 1] == 0x55:
            return i + 1
    return None


def find_function_start(img, off, limit=FUNCTION_SCAN_LIMIT):
    """The Borland prologue beginning the function that contains `off`, or None.

    Answered from the function map, so an address is only reported as its own entry
    when it really starts a function - not merely because those three bytes read as
    a prologue. None means the address is in no function: padding, a jump table, or
    data.
    """
    key = (len(img), off, limit)
    if key in _fn_start_cache:
        return _fn_start_cache[key]
    span = _enclosing(img, off, limit)
    if span is None and _function_map(img, limit) is None:
        found = _find_function_start_bytes(img, off, limit)
    else:
        found = span[0] if span else None
    _fn_start_cache[key] = found
    return found

'''

EXTENT_OLD_START = "def function_extent(img, off, limit=FUNCTION_SCAN_LIMIT):"
EXTENT_NEW = '''def function_extent(img, off, limit=FUNCTION_SCAN_LIMIT):
    """(start, end) of the function containing `off`; end is exclusive.

    Both ends come from the same forward walk, so extents cannot overlap and their
    sum cannot exceed the code they are measured from - which is how the old
    version's mistake surfaced, in coverage.py.

    Cross-checked against an independent rule, "the next prologue that resolves to
    itself"; test_fn_start.py keeps the two agreeing.

    (None, None) when `off` is in no function.
    """
    span = _enclosing(img, off, limit)
    return span if span else (None, None)
'''

CACHE_OLD = "_fp_norm_cache = {}\n"
CACHE_NEW = "_fp_norm_cache = {}\n_fn_map_cache = {}\n"


def main():
    src = open(SRC).read()
    if src.count(CACHE_OLD) != 1 or src.count(OLD_START) != 1:
        print("anchors missing, nothing written")
        return 1

    # Replace _sweep_reaches .. find_function_start wholesale.
    a = src.index(OLD_START)
    b = src.index(OLD_END, a) + 1
    src = src[:a] + NEW + src[b:]

    # And function_extent, which is now a lookup.
    a = src.index(EXTENT_OLD_START)
    b = src.index("\n\ndef ", a) + 1
    src = src[:a] + EXTENT_NEW + src[b:]

    src = src.replace(CACHE_OLD, CACHE_NEW, 1)
    open(SRC, "w").write(src)
    print("native.py updated: the function map decides, not the byte match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
