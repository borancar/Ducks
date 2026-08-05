#!/usr/bin/env python3
"""Render a snapshot's screen to a PNG, without playing anything.

The port is being written against screens nobody can see from a disassembly, and
a capture holds the four VGA planes and the DAC - so this turns any snapshot into
the picture the player was looking at when it was taken.

    venv/bin/python shot.py snapshots/snap003.snap
    venv/bin/python shot.py snapshots/*.snap -o debug/

Headless: no window, no audio device. The output is the unscaled mode picture,
320x200 or 360x240, exactly as emulation.py's F10 capture writes it.

**A shot is as copyrighted as the executable it came from** - it is the game's
own artwork. `debug/` and `*.png` are git-ignored, the same as `snapshots/`.
"""
import argparse
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("snapshots", nargs="+")
    ap.add_argument("-o", "--outdir", default="debug")
    args = ap.parse_args()

    import pygame

    import emulation
    import native
    import snapshot

    pygame.init()
    m, _ = native.build_machine(native.make_parser().parse_args(["--flip-hz", "0"]))
    os.makedirs(args.outdir, exist_ok=True)

    for path in args.snapshots:
        man, blobs = snapshot.load(path)
        snapshot.restore(m, man, blobs, verbose=False)
        name = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(args.outdir, name + ".png")
        if m.text_mode:
            print(f"{out}: text mode, nothing to draw")
            continue
        surface = emulation.make_surface(m)
        pygame.image.save(surface.convert(24), out)
        print(f"{out}  {surface.get_size()[0]}x{surface.get_size()[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
