#!/usr/bin/env python3
"""Resolve [0x53e] as the far pointer it is, not as a bare offset.

The pixel run in the scroll caller's plane loop goes through `lcall [0x53e]`.
That slot holds a far pointer, and its segment is not the one the code offsets in
this port are measured against: the live value is 0x0ac1 with a segment 0x4ca
paragraphs above the load segment, which resolves to image offset 0x5761 -
plot_pixel. Matching on the offset word alone recognised neither plotter, so the
native skipped all five pixels while the original drew them; --verify caught it as
5 mismatched bytes per call.

    venv/bin/python edit_fix_plot_pixel_stride.py
"""
import sys

SRC = "native.py"

OLD = '''# The two single-pixel plotters, by row stride. They are the same routine bar the
# multiply, and the game keeps a pointer to the one that matches the resolution
# in [0x53e] - which is how 0x5761 gets away with no [0x4fe] check inside it. So
# the stride comes from the pointer, not from a guess.
PLOT_PIXEL_STRIDE = {0x05761: 80, 0x057A1: 90}


def plot_pixel_stride(m):
    """The row stride of whichever plotter is installed at [0x53e], or None.

    None means the pointer holds something neither of them, which would be a hole
    in this reading of the code rather than a stride to invent - so it is said out
    loud once and the caller skips the pixels instead of writing wrong ones.
    """
    off = struct.unpack("<H", m.read(m.dgroup_base + 0x53E, 2))[0]
    stride = PLOT_PIXEL_STRIDE.get(off)
    if stride is None and not m.plot_pixel_warned:
        m.plot_pixel_warned = True
        known = ", ".join(f"{k:#07x}" for k in PLOT_PIXEL_STRIDE)
        print(f"  [pixel] [0x53e] holds {off:#07x}, which is neither plotter "
              f"({known}); the native plane loop is skipping its pixel runs")
    return stride
'''

NEW = '''# The two single-pixel plotters, by image offset and row stride. They are the same
# routine bar the multiply, and the game keeps a far pointer to the one that
# matches the resolution in [0x53e] - which is how 0x5761 gets away with no
# [0x4fe] check inside it. So the stride comes from the pointer, not a guess.
PLOT_PIXEL_STRIDE = {0x05761: 80, 0x057A1: 90}


def plot_pixel_stride(m):
    """The row stride of whichever plotter is installed at [0x53e], or None.

    It has to be resolved as a far pointer and turned back into an image offset:
    the offset word on its own is 0x0ac1, because the segment sits 0x4ca
    paragraphs above the load segment. Comparing that word against 0x5761
    recognised neither plotter and silently dropped the pixel run.

    None means the pointer resolves to neither, which would be a hole in this
    reading of the code rather than a stride to invent - so it is said out loud
    once and the caller skips the pixels rather than writing them wrong.
    """
    off, seg = struct.unpack("<HH", m.read(m.dgroup_base + 0x53E, 4))
    target = seg * 16 + off - m.image_base
    stride = PLOT_PIXEL_STRIDE.get(target)
    if stride is None and not m.plot_pixel_warned:
        m.plot_pixel_warned = True
        known = ", ".join(f"{k:#07x}" for k in PLOT_PIXEL_STRIDE)
        print(f"  [pixel] [0x53e] is {seg:04x}:{off:04x} = image {target:#07x}, "
              f"which is neither plotter ({known}); the native plane loop is "
              f"skipping its pixel runs")
    return stride
'''


def main():
    src = open(SRC).read()
    if src.count(OLD) != 1:
        print(f"anchor found {src.count(OLD)} times, nothing written")
        return 1
    open(SRC, "w").write(src.replace(OLD, NEW, 1))
    print("native.py updated: [0x53e] resolved as a far pointer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
