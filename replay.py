#!/usr/bin/env python3
"""Run a captured machine state headlessly, so a test does not need a player.

`native.py` earns the states: press F2 in a level, on the HUD, on the tally
screen. This runs them - no window, no keyboard, no sound device - and reports
what happened, with an exit code a script can act on.

    venv/bin/python replay.py snapshots/snap001.snap --frames 200 \\
          --verify-only compose_scroll,draw_entities

Any flag this does not recognise is handed to `native.py`'s own parser, so every
`--no-` form works here too. That is the point of replaying rather than
re-playing: the same captured state can be run with a native on and off, and the
difference attributed to that native rather than to having played differently.

Three things it can assert, each turning a non-zero exit:

- `--verify` / `--verify-only`: byte-compare natives against the code they
  replace. This needs no determinism to be meaningful - the comparison happens
  inside one run, between a native's output and the original body's output on the
  same call, so the host clock and the game's RNG cannot make it flaky.
- `--require`: names that must actually have been called. A verification run over
  a state that never reaches the routine reports zero mismatches and proves
  nothing; this is what stops that reading as a pass.
- `--compare-restore`: capture, restore into a second machine, capture again, and
  diff every byte. This is the check on the snapshot machinery itself rather than
  on the game - a restore that silently drops a register or a plane would
  otherwise show up much later as an unexplained mismatch.

Frames, not seconds, bound the run: a replay is a measurement, and the same
number of frames does the same amount of work whatever the host is doing.
"""

import argparse
import copy
import os
import sys
import time

# Chosen before pygame is imported, because native.py imports it at module
# scope: with the real drivers this opens a window and grabs the audio device,
# which is exactly what a test must not do.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame                                                    # noqa: E402
from unicorn.x86_const import UC_X86_REG_CS, UC_X86_REG_IP       # noqa: E402

import native                                                    # noqa: E402
import snapshot                                                  # noqa: E402

# Provenance, not machine state: these differ between two captures of the same
# machine and must not count as a round-trip difference.
PROVENANCE = ("captured", "note", "warnings", "frames")


def round_trip(m, nargs, tmp):
    """Save the live machine, restore it into a second one, and diff.

    What this proves: save -> load -> save loses nothing. Every byte of memory,
    every plane, every XMS block and every register comes back.

    What it deliberately does not prove: that the first restore reproduces the
    machine the snapshot was taken from. It cannot - restoring resets the voice
    table on purpose, because nothing is playing afterwards and a table that says
    otherwise never frees those slots. So the comparison is between the restored
    machine and a round trip of *that*, which is the property tests depend on.
    """
    b_man, b_blobs = snapshot.capture(m)
    snapshot.write(b_man, b_blobs, tmp)
    # Second machine with our sound off: NativeVoices reopens the global pygame
    # mixer in its constructor, which would disturb the first machine's for no
    # benefit here.
    quiet = copy.copy(nargs)
    quiet.native_sound = False
    m2, _img = native.build_machine(quiet)
    snapshot.restore_file(m2, tmp, force=True, verbose=False)
    c_man, c_blobs = snapshot.capture(m2)
    for k in PROVENANCE:
        b_man.pop(k, None)
        c_man.pop(k, None)
    return snapshot.compare(b_man, b_blobs, c_man, c_blobs)


def main():
    ap = argparse.ArgumentParser(
        description="Replay a captured machine state headlessly. Unrecognised "
                    "flags are passed through to native.py.")
    ap.add_argument("snapshot", help="path to a .snap written by native.py")
    ap.add_argument("--frames", type=int, default=100,
                    help="display frames to run (default 100)")
    ap.add_argument("--require", default="",
                    help="comma-separated native names that must be called at "
                         "least once, e.g. plane_loop 0x0e4dc,draw_sprite")
    ap.add_argument("--png", default="",
                    help="write the final screen here, for eyeballing")
    ap.add_argument("--compare-restore", action="store_true",
                    help="check the snapshot machinery itself: restore, "
                         "re-capture, and diff every byte")
    ap.add_argument("--force", action="store_true",
                    help="restore even if the snapshot names a different image")
    ap.add_argument("--snapshot-out", default="",
                    help="capture again at the end of the run, e.g. to advance a "
                         "state a few hundred frames and keep the result")
    args, extra = ap.parse_known_args()

    nargs = native.make_parser().parse_args(extra)
    # A harness that sleeps to a target frame rate measures the target rather than
    # the work, so pacing is off here unless it was asked for explicitly.
    if not any(a.startswith("--flip-hz") for a in extra):
        nargs.flip_hz = 0.0
    pygame.init()
    pygame.font.init()

    man, blobs = snapshot.load(args.snapshot)
    print(f"  [snap] {args.snapshot}: {snapshot.describe(man)}")

    m, img = native.build_machine(nargs)
    snapshot.restore(m, man, blobs, force=args.force)

    # The machinery check, before running anything: a restore that lost state
    # would make every later result suspect, and this is cheap.
    if args.compare_restore:
        tmp = args.snapshot + ".roundtrip.tmp"
        try:
            diffs = round_trip(m, nargs, tmp)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        if diffs:
            print(f"  [snap] round trip is NOT lossless, {len(diffs)} "
                  f"difference(s):")
            for d in diffs[:20]:
                print(f"    {d}")
            return 2
        print("  [snap] round trip is byte-identical: memory, planes, XMS, "
              "registers, VGA, DOS and card state all survive save/load/save")

    if nargs.verify_only:
        print(f"  [verify] checking only {sorted(m.verify_only)}")

    before = dict(m.native_calls)
    addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
    ran = 0
    running = True
    # Restart the machine's clock so native_time_report()'s "% of run" is against
    # the replay rather than against the seconds spent building and restoring.
    # t0 is a host reading, not machine state, which is why it is not restored.
    m.t0 = time.perf_counter()
    wall = time.perf_counter()
    while running and ran < args.frames:
        addr, running = native.step_frame(m, addr, nargs, img)
        if m.voices is not None:
            m.voices.reap()
        ran += 1
        m.frames = ran

    wall = time.perf_counter() - wall
    print(f"\n=== replayed {ran} frame(s) in {wall:.2f}s "
          f"({ran / max(wall, 1e-6):.1f} fps), mode {m.mode:#04x} ===")
    fired = {k: m.native_calls.get(k, 0) - before.get(k, 0)
             for k in m.native_calls}
    fired = {k: v for k, v in sorted(fired.items(), key=lambda kv: -kv[1]) if v}
    for k, v in list(fired.items())[:15]:
        print(f"  {k:<24} {v}")
    if len(fired) > 15:
        print(f"  ... and {len(fired) - 15} more")

    if getattr(m, "flips", 0):
        print(f"  page flips {m.flips} "
              f"({m.flips / max(wall, 1e-6):.1f}/s, "
              f"{1000 * wall / m.flips:.1f} ms per game frame"
              + (f", target {m.flip_hz:.0f} Hz" if m.flip_hz else ", unlimited")
              + f"), {m.flip_late} late")

    # native.py's own reporters, rather than second implementations here. Both
    # return immediately when the flag that feeds them was not passed.
    m.native_time_report()
    m.port_report()
    m.call_report(img)

    if args.png:
        surf = native.make_surface(m)
        pygame.image.save(surf, args.png)
        print(f"  wrote {args.png}")

    if args.snapshot_out:
        snapshot.save(m, args.snapshot_out,
                      note=f"replayed {ran} frames from "
                           f"{os.path.basename(args.snapshot)}")
        print(f"  wrote {args.snapshot_out}")

    rc = 0
    if not running:
        print("  FAIL: the machine stopped before the frame budget was used "
              "(crash, or the program exited)")
        rc = 1

    missing = [n.strip() for n in args.require.split(",")
               if n.strip() and not fired.get(n.strip())]
    if missing:
        print(f"  FAIL: never called: {', '.join(missing)}")
        rc = 1
    elif args.require:
        print(f"  required routines all ran: {args.require}")

    if m.verify:
        print(f"  [verify] {m.verify_calls} compared, {m.verify_bad} "
              f"mismatched, {m.verify_declined} declined")
        for who in sorted(m.verify_shadow):
            shadow = ",".join(sorted(m.verify_shadow[who]))
            print(f"  [verify] SHADOWED: while {who}'s original body was being "
                  f"replayed, these natives also ran: {shadow}")
            print(f"  [verify]   any of them the body itself calls is the same "
                  f"Python on both sides and verified against itself; "
                  f"--skip-natives <name> hands one back to the guest. "
                  f"(Interrupt-driven ones - the sound mixer - land in this "
                  f"window without being part of the body.)")
        if m.verify_bad:
            print("  FAIL: a native disagreed with the code it replaces")
            rc = 1
        elif not m.verify_calls:
            # Zero comparisons is not a pass. This is the mode=0x03 trap in
            # another guise: nothing ran, so nothing was checked.
            print("  FAIL: nothing was compared - the state does not reach "
                  "the routine, so this run proves nothing")
            rc = 1
        else:
            print("  PASS: every comparison matched")
    if rc == 0:
        print("  OK")
    return rc


if __name__ == "__main__":
    sys.exit(main())
