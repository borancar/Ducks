#!/usr/bin/env python3
"""Take F2 captures at the top of the page flip: exact, and one frame late at most.

Three candidate capture points, and the first two were both wrong in different
ways:

- **Inside the flip, after the swap** (where pump() runs). Restoring runs the flip
  again, swapping the pages a second time, so the resume is not faithful.
- **The display loop's frame boundary.** Exact, but under 70 Hz pacing that loop
  iterates about 1.3 times a second, so a keypress lands up to 0.8s late - far too
  coarse to catch a moment on screen, as an attempt to capture the end of a score
  tally showed.
- **The top of the flip, before the swap.** Exact *and* prompt: execution resumes
  at this function's entry with the pages still unswapped, so the flip runs exactly
  once, and the wait is at most one frame - 14 ms.

The third is also a better-defined boundary than the display loop ever was. The
loop's boundary falls wherever a fixed count of guest instructions runs out, which
is arbitrary and mid-frame; the flip's entry is where the game itself considers a
frame finished, with the x87 stack empty and no native part-way through reading its
arguments.

The display-loop path stays as a fallback, for `--no-native-flip` and for states
that never flip at all - a text screen would otherwise ignore F2 forever. Whichever
happens first clears the request.
"""

import sys

PATH = "native.py"

EDITS = [
    # ------------------------------------------- where captures should be written
    ("""    # Recorded so a snapshot can refuse to restore onto a different image.
    m.exe_path = args.exe""",
     """    # Recorded so a snapshot can refuse to restore onto a different image.
    m.exe_path = args.exe
    # Reached from the flip, which has no access to the parsed arguments.
    m.snapshot_dir = args.snapshot_dir"""),

    # ------------------------------------------------------------- the helper
    ("""def native_page_flip(m, args):""",
     '''def _take_requested_snapshot(m):
    """Write a pending capture. Only called from the top of the flip.

    That is the one point where the resume is exact: execution restarts at the
    flip's entry with the pages still unswapped, so the flip runs once, as it
    would have. It is also a true frame boundary - the game has finished drawing
    and asked to show it - which the display loop's instruction-count boundary
    never was.
    """
    note, m.snapshot_requested = m.snapshot_requested, None
    p = snapshot.save(m, snapshot.next_path(
        getattr(m, "snapshot_dir", snapshot.SNAP_DIR)), note=note)
    print(f"  [snap] wrote {p} ({os.path.getsize(p) / 1e6:.1f} MB, "
          f"frame {getattr(m, 'frames', 0)}, mode {m.mode:#04x}, {note})")
    return p


def native_page_flip(m, args):'''),

    # --------------------------------------------- honour it before the swap
    ('''    g = m.dgroup_base
    front = struct.unpack("<H", m.read(g + 0x1725, 2))[0]''',
     '''    # Before anything is changed, so the state written is the one the game is
    # about to show and a restore of it flips exactly once.
    if m.snapshot_requested:
        _take_requested_snapshot(m)

    g = m.dgroup_base
    front = struct.unpack("<H", m.read(g + 0x1725, 2))[0]'''),

    # ------------------------------------------------------ what F2 now says
    ("""                    m.snapshot_requested = f"F2 at frame {frames}"
                    print("  [snap] capture requested; taking it at the next "
                          "frame boundary")""",
     """                    m.snapshot_requested = f"F2 at frame {frames}"
                    print("  [snap] capture requested; taking it at the next "
                          "page flip")"""),

    # ------------------------------------ the loop path is now the fallback
    ("""        if m.snapshot_requested:
            take_snapshot(m, args, m.snapshot_requested)
            m.snapshot_requested = None""",
     """        # Fallback only: the flip normally honours this within a frame. This
        # covers --no-native-flip and states that never flip, such as a text
        # screen, which would otherwise ignore F2 entirely.
        if m.snapshot_requested:
            take_snapshot(m, args, m.snapshot_requested)
            m.snapshot_requested = None"""),
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
