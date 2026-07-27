#!/usr/bin/env python3
"""Make a quit request survive step_frame's return value.

F12 stopped working when the event handling moved into the flip. pump() sets
main()'s `running` to False through `nonlocal`, but it now runs *inside* the flip
hook, part-way through a slice - and the very next statement in the loop is

    addr, running = step_frame(m, addr, args, img)

which overwrites the request with step_frame's True. The keypress was read and
acted on; the decision was then discarded a microsecond later. Nothing about the
key handling was wrong, which is why the game's own keys kept working.

So the request goes on the machine instead of only in a local: `quit_requested` is
set by pump(), respected by the loop, and checked inside step_frame's slice loop so
that `emu_stop()` ending one slice is not followed by the next one starting. Without
that last part, quitting would still take up to a further chunk - about a second at
70 Hz pacing - to take effect.
"""

import sys

PATH = "native.py"

EDITS = [
    # ------------------------------------------------------- the flag's home
    ("""        self.flips = 0
        self.flip_hz = 0.0""",
     """        self.flips = 0
        # Set by pump() when F12 or a window close is seen, which may happen from
        # inside the flip hook mid-slice. It lives here rather than only in
        # main()'s `running` local because that local is reassigned from
        # step_frame's return value immediately afterwards.
        self.quit_requested = False
        self.flip_hz = 0.0"""),

    # ------------------------------------------------------ pump records it
    ("""                running = False
                m.uc.emu_stop()   # end the slice now, not in a second""",
     """                running = False
                m.quit_requested = True   # survives step_frame's return value
                m.uc.emu_stop()   # end the slice now, not in a second"""),

    # ------------------------------------------- step_frame stops slicing
    ("""        if m.finished:
            print(f"  [dos] program exited: {m.finished}")
            return addr, False
        addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
        m.service_sound()""",
     """        if m.finished:
            print(f"  [dos] program exited: {m.finished}")
            return addr, False
        if m.quit_requested:
            # Seen by pump() inside the flip hook. emu_stop() ended this slice;
            # without this the next one would start and quitting would take
            # another chunk - about a second under 70 Hz pacing - to be noticed.
            return addr, False
        addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
        m.service_sound()"""),

    # ----------------------------------------------- the loop respects it
    ("""        flips_before = m.flips
        addr, running = step_frame(m, addr, args, img)""",
     """        flips_before = m.flips
        addr, ok = step_frame(m, addr, args, img)
        # Deliberately not `running = ok`: pump() may have set running False from
        # inside the flip hook during this very slice, and step_frame's return
        # value knows nothing about that.
        running = ok and not m.quit_requested"""),
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
