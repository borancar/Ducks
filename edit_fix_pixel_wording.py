#!/usr/bin/env python3
"""Say only what was observed about [0x53e], not what was inferred from it.

The failing run printed the offset word, 0x0ac1. Which plotter that is depends on
the segment, which the message did not print - both 0x5761 and 0x57a1 are a
paragraph-aligned distance from 0x0ac1, so the offset alone does not decide it.
The fix resolves the whole pointer either way; the comment should not claim a
segment nobody looked at.

    venv/bin/python edit_fix_pixel_wording.py
"""
import sys

SRC = "native.py"

OLD = '''    It has to be resolved as a far pointer and turned back into an image offset:
    the offset word on its own is 0x0ac1, because the segment sits 0x4ca
    paragraphs above the load segment. Comparing that word against 0x5761
    recognised neither plotter and silently dropped the pixel run.
'''

NEW = '''    It has to be resolved as a far pointer and turned back into an image offset.
    The offset word on its own was 0x0ac1 in a live level - matching neither
    plotter, because the segment is not the one image offsets are measured from -
    and comparing that word alone silently dropped the pixel run. Which of the two
    it resolves to is not settled by the offset: both are a paragraph-aligned
    distance from 0x0ac1, so the pointer has to be followed, and the message below
    prints both halves if it ever leads somewhere else.

    The slot is 0000:0000 until a level starts, so it cannot be checked without
    playing one; see probe_plot_ptr.py.
'''


def main():
    src = open(SRC).read()
    if src.count(OLD) != 1:
        print(f"anchor found {src.count(OLD)} times, nothing written")
        return 1
    open(SRC, "w").write(src.replace(OLD, NEW, 1))
    print("native.py updated: [0x53e] comment says what was observed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
