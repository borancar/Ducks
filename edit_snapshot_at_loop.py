#!/usr/bin/env python3
"""Take F2 captures at the display loop's frame boundary again, not inside the flip.

Moving the event handling into the flip quietly broke the one invariant snapshot.py
documents: capture only at the main loop's frame boundary. F2 was being serviced
from inside native_page_flip, after the page swap and with IP still at the flip's
entry - so restoring such a snapshot runs the flip a second time, swaps the pages
back, and leaves the display a frame stale until the next flip corrects it. Not
fatal, and harmless to verification since the native and the original body both
draw to the same page, but not a faithful resume either.

So pump() only records the request now, and the display loop honours it where it
always did. The cost is latency: a paced chunk spans dozens of frames, so a capture
can land up to about 0.8s after the key. For the states this is used on - a level,
a HUD, a score screen - that is irrelevant, and correctness of the resume is worth
more than the reaction time.

The shell trigger (`snapshot.request`) was already handled in the loop and is
unaffected.
"""

import sys

PATH = "native.py"

EDITS = [
    ("""        self.quit_requested = False""",
     """        self.quit_requested = False
        # A note string when a capture has been asked for and not yet taken. The
        # request is recorded by pump() - which may run inside the flip hook - and
        # honoured by the display loop, because snapshot.py's only supported
        # capture point is the loop's frame boundary.
        self.snapshot_requested = None"""),

    ("""                if ev.key == pygame.K_F2:
                    take_snapshot(m, args, f"F2 at frame {frames}")""",
     """                if ev.key == pygame.K_F2:
                    # Recorded, not taken: see Native.snapshot_requested.
                    m.snapshot_requested = f"F2 at frame {frames}"
                    print("  [snap] capture requested; taking it at the next "
                          "frame boundary")"""),

    ("""        frames += 1
        m.frames = frames
        if frames in snap_at:
            take_snapshot(m, args, f"--snapshot-at {frames}")""",
     """        frames += 1
        m.frames = frames
        if m.snapshot_requested:
            take_snapshot(m, args, m.snapshot_requested)
            m.snapshot_requested = None
        if frames in snap_at:
            take_snapshot(m, args, f"--snapshot-at {frames}")"""),
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
