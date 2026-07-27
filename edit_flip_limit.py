#!/usr/bin/env python3
"""Pace the native page flip at 70 Hz, the rate the retrace wait used to give.

With the flip native and both waits gone, nothing limited the game: it ran ~250
flips a second on a level and ~270 in the unhovered menu, against the ~70 Hz the
retrace wait had been enforcing. The hovered menu was the exception at ~65 fps -
it costs about one retrace period all by itself, which is why it looked right.

So the pacing comes back, as a sleep rather than a spin. The guest is idle either
way; the difference is that a sleep costs no instructions and no port callbacks,
where the original burned ~1836 reads of 0x3da per flip and the instruction budget
that would otherwise draw.

70 Hz is not arbitrary: it is the VGA mode 13h/Mode X frame rate the game was
written for, and the rate `emulation.py` synthesises on 0x3da bit 3.

**Overruns reset the schedule instead of accumulating debt.** Carrying the deficit
forward would make the game run frames back-to-back to catch up, which is both
wrong and unlike the original: a missed retrace is simply waited out until the next
one. That is exactly the frameskip on the heavier level, and `flip_late` now counts
it, so "this level cannot hold 70 Hz" becomes a number instead of an impression.

`--flip-hz` sets the target; `--flip-hz 0` restores the unlimited behaviour. Note
that replay.py overrides the default to 0, because a measurement harness that
sleeps to a target rate measures the target rather than the work.
"""

import sys

PATH = "native.py"

EDITS = [
    # -------------------------------------------------------------- the flag
    ("""    ap.add_argument("--native-fp", default=True,""",
     """    ap.add_argument("--flip-hz", type=float, default=70.0,
                    help="pace the native page flip at this rate (70 Hz is the "
                         "Mode X frame rate the game was written for); 0 leaves "
                         "it unlimited")
    ap.add_argument("--native-fp", default=True,"""),

    # ------------------------------------------- state, set before any frame
    ("""    # Recorded so a snapshot can refuse to restore onto a different image.
    m.exe_path = args.exe""",
     """    # Recorded so a snapshot can refuse to restore onto a different image.
    m.exe_path = args.exe
    # Flip pacing. flip_due is the wall-clock time the next flip is owed at;
    # flip_late counts the frames that missed their slot, which is this
    # emulator's measure of the game not holding its frame rate.
    m.flip_hz = getattr(args, "flip_hz", 0.0) or 0.0
    m.flip_due = None
    m.flip_late = 0
    m.flip_slept = 0.0"""),

    # ------------------------------------------------------------- the pacer
    ("""def native_page_flip(m, args):""",
     '''def _pace_flip(m):
    """Hold the frame until its slot, the way the retrace wait used to.

    Returns nothing; updates the schedule. Three cases, and the third is the
    interesting one:

    - ahead of the slot: sleep the remainder, then advance the schedule by one
      period. This is the normal case and what keeps the game at its intended
      speed.
    - a little behind (inside one period): advance the schedule without sleeping,
      so the cadence is kept rather than drifting later every frame.
    - far behind (more than a period): resynchronise on now. Carrying the debt
      forward would run frames back-to-back to catch up, which the original never
      does - it waits for the next retrace and simply shows fewer frames.
    """
    hz = getattr(m, "flip_hz", 0.0)
    if not hz:
        return
    period = 1.0 / hz
    now = time.perf_counter()
    due = m.flip_due
    if due is None:
        m.flip_due = now + period
        return
    if now < due:
        time.sleep(due - now)
        m.flip_slept += due - now
        m.flip_due = due + period
    elif now < due + period:
        m.flip_late += 1
        m.flip_due = due + period
    else:
        m.flip_late += 1
        m.flip_due = now + period


def native_page_flip(m, args):'''),

    ("""    m.flips += 1
    present = getattr(m, "present", None)
    if present is not None:
        present()
    return None""",
     """    m.flips += 1
    present = getattr(m, "present", None)
    if present is not None:
        present()
    # After presenting, so the frame is on screen for its slot rather than
    # sleeping before anyone can see it.
    _pace_flip(m)
    return None"""),

    # ------------------------------------------------------------ the report
    ('''    print(f"\\n=== finished after {frames} frames, {m._elapsed():.1f}s ===")
    if m.native_calls:''',
     '''    print(f"\\n=== finished after {frames} frames, {m._elapsed():.1f}s ===")
    if m.flips:
        el = max(m._elapsed(), 1e-6)
        rate = m.flips / el
        print(f"  page flips      : {m.flips} ({rate:.1f}/s"
              + (f", target {m.flip_hz:.0f}" if m.flip_hz else ", unlimited")
              + f"), {m.flip_late} late, {m.flip_slept:.1f}s slept")
        if m.flip_hz and m.flip_late > m.flips * 0.05:
            print(f"  ^ {100 * m.flip_late / m.flips:.0f}% of frames missed "
                  f"their slot: this state cannot hold {m.flip_hz:.0f} Hz here")
    if m.native_calls:'''),
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
