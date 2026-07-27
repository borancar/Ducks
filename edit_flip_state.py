#!/usr/bin/env python3
"""Initialise the flip-pacing state in the constructor, not only in build_machine.

`_pace_flip` guards `flip_hz` with getattr but then increments `flip_late`
unguarded, so a machine built by any path other than `build_machine` - a test, or
a future harness - would raise the first time a frame missed its slot. The
defaults belong with the object; the flag still lands in build_machine.
"""

import sys

PATH = "native.py"

EDITS = [
    ("""        self.native_flip = native_flip
        self.flips = 0""",
     """        self.native_flip = native_flip
        # Flip pacing. build_machine sets flip_hz from --flip-hz; these defaults
        # mean an unpaced machine still counts flips correctly.
        self.flips = 0
        self.flip_hz = 0.0
        self.flip_due = None
        self.flip_late = 0
        self.flip_slept = 0.0"""),

    ("""    m.flip_hz = getattr(args, "flip_hz", 0.0) or 0.0
    m.flip_due = None
    m.flip_late = 0
    m.flip_slept = 0.0""",
     """    m.flip_hz = getattr(args, "flip_hz", 0.0) or 0.0"""),
]


def main():
    src = open(PATH).read()
    for old, _ in EDITS:
        n = src.count(old)
        if n != 1:
            print(f"anchor occurs {n} times, expected 1:\n{old}")
            return 1
    for old, new in EDITS:
        src = src.replace(old, new)
    open(PATH, "w").write(src)
    print(f"{PATH}: {len(EDITS)} edit(s) applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
