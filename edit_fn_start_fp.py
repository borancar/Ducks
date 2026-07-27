#!/usr/bin/env python3
"""Undo Borland's FP encoding before sweeping, or the sweep desyncs.

The linker overwrote each FWAIT + ESC pair with a two-byte INT, leaving the x87
ModRM and displacement in place. So in the file image those operand bytes look
like code: CD 36 46 FC sweeps as `int 0x36; inc si` where the instruction is
really `FWAIT; FLD word [bp-4]`. Any function containing floating point therefore
cannot be swept from its entry, which is exactly the case the FP report needs -
it was labelling three sites in 0x0c716 as three separate unknown functions.

install_native_fp already knows the mapping, because reversing it is how the x87
instructions were put back. Applying the same substitution to a scratch copy makes
the sweep work, and it is length-preserving, so every offset still lines up.

    venv/bin/python edit_fn_start_fp.py
"""
import sys

SRC = "native.py"

OLD = '''def _entry_table(img):'''

NEW = '''def _fp_normalised(img):
    """A copy of the image with Borland's INT-encoded x87 turned back into x87.

    INT 34h..3Bh stand in for FWAIT + ESC D8..DF, and INT 3Dh for a lone FWAIT;
    both are two bytes, as is the substitution, so nothing shifts. Built once per
    image and used only for sweeping - it is never executed.

    Safe because those vectors are Borland's floating point and nothing else in
    this binary: the interrupt inventory found no other use of 34h-3Dh.
    """
    key = len(img)
    norm = _fp_norm_cache.get(key)
    if norm is None:
        buf = bytearray(img)
        for i in range(len(buf) - 1):
            if buf[i] != 0xCD:
                continue
            n = buf[i + 1]
            if 0x34 <= n <= 0x3B:
                buf[i], buf[i + 1] = 0x9B, 0xD8 + n - 0x34
            elif n == 0x3D:
                buf[i], buf[i + 1] = 0x9B, 0x90      # FWAIT, then a NOP
        norm = bytes(buf)
        _fp_norm_cache[key] = norm
    return norm


def _entry_table(img):'''

CACHE_OLD = '''_fn_entries = {}
_fn_start_cache = {}
'''
CACHE_NEW = '''_fn_entries = {}
_fn_start_cache = {}
_fp_norm_cache = {}
'''

SWEEP_OLD = '''    md = _disasm16()
    if md is None:
        return True                       # unconfirmable; take the byte match
    for i in md.disasm(bytes(img[start:off + 16]), start):
'''
SWEEP_NEW = '''    md = _disasm16()
    if md is None:
        return True                       # unconfirmable; take the byte match
    img = _fp_normalised(img)
    for i in md.disasm(img[start:off + 16], start):
'''


def main():
    src = open(SRC).read()
    for old, new in ((CACHE_OLD, CACHE_NEW), (OLD, NEW), (SWEEP_OLD, SWEEP_NEW)):
        if src.count(old) != 1:
            print(f"anchor found {src.count(old)} times, nothing written: "
                  f"{old[:40]!r}")
            return 1
        src = src.replace(old, new, 1)
    open(SRC, "w").write(src)
    print("native.py updated: sweeps run over FP-normalised bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
