#!/usr/bin/env python3
"""Show the game's ending without finishing all eighty levels.

    venv/bin/python show_cutscene.py                    # play them in a window
    venv/bin/python show_cutscene.py --capture debug/   # write PNGs instead

Five screens sit in `main_menu`'s inner loop after the game call returns
(`0x1392f` onwards), behind a gate that only opens when the *last* episode ends:
`episode_end_gate` (`0x11c75`) returns the matched episode record's terminator
flag, and only `DUCKING HELL` has it set. So reaching them by playing means
finishing level 80 - 60 of which need registering - and the way to see one
otherwise is the documented answer for a state that cannot be produced on demand:
drive the guest's own code, as `test_retire.py` does. Nothing here reimplements a
screen.

Each screen takes no arguments and is reached by `push cs; call near`, so the
call is two words on the stack and a new CS:IP; the callee's own `retf` pops
them. The return address is the instruction after main_menu's call site, and it
is also where this stops - run past it and the guest carries on through the rest
of the sequence and draws the *next* screen, which is what made four of the five
first appear to be the same picture.

`--chunk` is small for a reason. A display frame is a fixed instruction budget,
not a game frame: at the default, 0x0f5b1's entire screen ran inside 21 of them
and every capture came back black. Pacing does not fix that - it only inserts
sleeps - so the budget is what has to shrink. See
docs/notes/homecoming-sequence.md.
"""
import argparse
import os
import sys
import time

from unicorn import UC_HOOK_CODE
from unicorn.x86_const import (UC_X86_REG_CS, UC_X86_REG_IP, UC_X86_REG_SP,
                               UC_X86_REG_SS)

CS = 0x05DA                 # the segment main and main_menu run in
SEG_BASE = 0x4CA0           # CS * 16 - image_base, as an image offset
RET = 0x13932               # the instruction after main_menu's call at 0x1392f

SCREENS = [
    (0x0F5B1, "cutscene_rocket_space", "id 0x32 - the rocket over a starfield"),
    (0x0FC8B, "cutscene_rocket_landing", "ids 0x33/0x34 - down on the grass"),
    (0x0F9FD, "cutscene_doorstep", "ids 0x37/0x38 - the doorway"),
    (0x0F825, "cutscene_welcome_home", "id 0x36 - Welcome Home!"),
    (0x0F913, "cutscene_photos", "ids 0x3a-0x3c - the photographs"),
]


def far_call(m, target_img):
    """Push what `push cs; call near` leaves, and jump. CS first, then the IP."""
    ss, sp = m._reg(UC_X86_REG_SS), m._reg(UC_X86_REG_SP)
    for word in (CS, (RET - SEG_BASE) & 0xFFFF):
        sp = (sp - 2) & 0xFFFF
        m.write(ss * 16 + sp, word.to_bytes(2, "little"))
    m.uc.reg_write(UC_X86_REG_SP, sp)
    m.uc.reg_write(UC_X86_REG_CS, CS)
    m.uc.reg_write(UC_X86_REG_IP, (target_img - SEG_BASE) & 0xFFFF)
    return m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)


def build(args, native, snapshot):
    nargs = native.make_parser().parse_args([])
    nargs.flip_hz = 0.0          # the caller paces; the guest must not sleep too
    nargs.chunk = args.chunk
    nargs.native_sound = bool(args.window)
    man, blobs = snapshot.load(args.snapshot)
    m, img = native.build_machine(nargs)
    snapshot.restore(m, man, blobs, force=True, verbose=False)
    addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
    for _ in range(3):           # let the restored state reach a frame boundary
        addr, _ok = native.step_frame(m, addr, nargs, img)
    return m, img, nargs, addr


def one(args, native, snapshot, pygame, screen, target, name, what):
    m, img, nargs, addr = build(args, native, snapshot)
    done = []
    m.uc.hook_add(UC_HOOK_CODE, lambda *a: done.append(True),
                  begin=m.image_base + RET, end=m.image_base + RET)
    addr = far_call(m, target)
    print(f"  {name} ({target:#07x}) - {what}")

    clock = pygame.time.Clock() if screen else None
    if screen:
        pygame.display.set_caption(f"{name}  {what}")
    for n in range(1, args.frames + 1):
        if screen:
            for e in pygame.event.get():
                if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN
                                             and e.key == pygame.K_ESCAPE):
                    return False
                if (e.type == pygame.MOUSEBUTTONDOWN
                        or (e.type == pygame.KEYDOWN
                            and e.key == pygame.K_SPACE)):
                    print("    skipped")
                    return True
        addr, ok = native.step_frame(m, addr, nargs, img)
        if not ok:
            print("    !! the machine stopped")
            return True
        frame = native.make_surface(m)
        if screen:
            # scale() into an existing destination needs matching formats, and
            # make_surface() hands back the guest's own depth.
            screen.blit(pygame.transform.scale(frame, screen.get_size()), (0, 0))
            pygame.display.flip()
            clock.tick(70)
        elif n % args.every == 0:
            path = os.path.join(args.capture, f"{name}_f{n:04d}.png")
            pygame.image.save(frame, path)
        if done:
            print(f"    returned to {RET:#07x} after {n} frame(s), "
                  f"{getattr(m, 'flips', 0)} flips")
            if screen:
                time.sleep(0.6)
            return True
    print(f"    did not return within {args.frames} frames "
          f"({getattr(m, 'flips', 0)} flips)")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot", default="snapshots/snap001.snap",
                    help="a menu capture to call from (default snap001)")
    ap.add_argument("--capture", default="",
                    help="write PNGs to this directory instead of opening a "
                         "window")
    ap.add_argument("--every", type=int, default=30,
                    help="with --capture, save every Nth frame (default 30)")
    ap.add_argument("--frames", type=int, default=2000,
                    help="give up on a screen after this many (default 2000)")
    ap.add_argument("--chunk", type=int, default=20000,
                    help="guest instructions per display frame; small so a "
                         "frame is about a game frame (default 20000)")
    ap.add_argument("--only", default="",
                    help="comma-separated image offsets, e.g. 0x0f825")
    ap.add_argument("--scale", type=int, default=3)
    args = ap.parse_args()
    args.window = not args.capture

    if args.capture:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        os.makedirs(args.capture, exist_ok=True)

    import pygame
    import native
    import snapshot

    pygame.init()
    pygame.font.init()
    screen = (pygame.display.set_mode((320 * args.scale, 200 * args.scale))
              if args.window else None)

    wanted = [int(s, 0) for s in args.only.split(",") if s.strip()]
    todo = [s for s in SCREENS if not wanted or s[0] in wanted]
    if not todo:
        sys.exit(f"no screen matches {args.only!r}")
    for target, name, what in todo:
        if not one(args, native, snapshot, pygame, screen, target, name, what):
            break
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
