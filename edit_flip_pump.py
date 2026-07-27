#!/usr/bin/env python3
"""Service the window from the page flip, not once per instruction chunk.

Pacing the flip exposed a second consequence of the flip becoming the frame
boundary. A chunk of guest instructions now spans dozens of paced frames - 53 of
them on level-fast, 0.76s of wall time - so pumping SDL events once per chunk
would leave input responding less than twice a second. Keys and mouse motion
would arrive in bursts and F12 would take most of a second to register.

So the event handling moves into a `pump()` closure that the flip calls, putting
input at the game's own frame rate. The main loop calls the same closure, which
keeps text screens and any run with `--no-native-flip` behaving as before.

The body is moved rather than retyped: this script cuts the existing event block
out of the loop and re-indents it, so the handling itself is unchanged and cannot
drift from what was there.

Order inside the flip is present, pace, pump: the frame goes up, we sleep out its
slot, and input is read as late as possible so the guest resumes with the freshest
state. Quitting from inside a hook also calls `emu_stop()`, or the chunk would run
on for its remaining instructions before anyone noticed.
"""

import sys

PATH = "native.py"

START = "        for ev in pygame.event.get():\n"
END = "        # Shell-side control, so tracing can be driven without window focus.\n"
ANCHOR_DEF = """    # Reached from native_page_flip. Set here rather than in build_machine
    # because it closes over the window, which a headless replay does not have.
    m.present = present
"""
ANCHOR_QUIT = """            if ev.type == pygame.QUIT or (
                    ev.type == pygame.KEYDOWN and ev.key == pygame.K_F12):
                running = False
"""
ANCHOR_FLIP = """    # After presenting, so the frame is on screen for its slot rather than
    # sleeping before anyone can see it.
    _pace_flip(m)
    return None
"""


def main():
    src = open(PATH).read()

    for a in (START, END, ANCHOR_DEF, ANCHOR_QUIT, ANCHOR_FLIP):
        n = src.count(a)
        if n != 1:
            print(f"anchor occurs {n} times, expected 1:\n{a}")
            return 1

    i, j = src.index(START), src.index(END)
    if not i < j:
        print("the event block does not precede the shell-trigger block")
        return 1
    block = src[i:j]

    # Stop the emulation slice as well as the loop: `running = False` alone is not
    # seen until the chunk finishes, which is up to a second away now.
    block = block.replace(ANCHOR_QUIT, ANCHOR_QUIT.replace(
        "                running = False\n",
        "                running = False\n"
        "                m.uc.emu_stop()   # end the slice now, not in a second\n"))

    dedented = "".join(
        (line[4:] if line.startswith("    ") else line)
        for line in block.splitlines(keepends=True))

    pump = (
        '    def pump():\n'
        '        """Service the window: events in, and the quit key.\n'
        '\n'
        '        Called by native_page_flip once per game frame, and by the display\n'
        '        loop when the guest did not flip. A paced chunk spans dozens of\n'
        '        frames, so pumping only per chunk would leave input responding less\n'
        '        than twice a second.\n'
        '        """\n'
        '        nonlocal running\n'
        + dedented + '\n'
    )

    src = src[:i] + "        pump()\n" + src[j:]
    src = src.replace(ANCHOR_DEF, ANCHOR_DEF + "\n" + pump + "    m.pump = pump\n")
    src = src.replace(ANCHOR_FLIP,
                      """    # After presenting, so the frame is on screen for its slot rather than
    # sleeping before anyone can see it.
    _pace_flip(m)
    # Input last, so the guest resumes with the freshest state rather than with
    # whatever was current 14 ms ago.
    pump = getattr(m, "pump", None)
    if pump is not None:
        pump()
    return None
""")

    open(PATH, "w").write(src)
    print(f"{PATH}: event handling moved into pump(), called from the flip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
