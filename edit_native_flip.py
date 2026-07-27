#!/usr/bin/env python3
"""Serve the game's page flip natively, and present from it.

`0x04d4b` is the page flipper, recovered in full:

    delay(0x1f - [0x1fd4]);              /* 0x223e, Borland delay(), PIT-timed */
    while (inp(0x3da) & 1) ;             /* wait for display enable to fall */
    swap(&[0x1725], &[0x1727]);          /* the two page start addresses */
    outpw(0x3d4, (hi << 8) | 0x0c);      /* CRTC start address high */
    outpw(0x3d4, (lo << 8) | 0x0d);      /* ... and low */
    while (!(inp(0x3da) & 8)) ;          /* wait for vertical retrace */
    [0xd61] = ([0xd61] + 1) % 10;        /* frame phase, 0..9 */

Two measurements motivated this. The retrace wait spins **~1836 reads of 0x3da per
flip**, every one a Python callback, and it accounted for 94-95% of all port I/O in
both a menu and a level state. Worse than its own cost, it consumes the guest
instruction budget that would otherwise draw: the game reached a true 70 flips per
second while the display loop presented only ~8 of them, because each display frame
was one fixed chunk of instructions and most of that chunk went into spinning.

So the flip becomes the frame boundary. The native swaps the pages, programs the
CRTC through the same `_crtc_write` the OUT would have reached - so `start_addr`,
the addressing unit and the rest of the VGA state stay consistent - advances the
phase counter, and then presents. Every game frame reaches the screen instead of
one in eight.

**Both waits are dropped deliberately, and nothing paces the game now except
pygame's own present.** That is the requested first cut: see what frame rate the
host gives, then add limiting. `--no-native-flip` restores the guest's own flip,
waits and all, which is the way to compare.

`m.present` is set by main() rather than reached from here, so a headless
replay.py run simply does not present - the flip still swaps and counts.

The display loop still presents when the guest did *not* flip during a chunk: text
screens, loading, and any run with this native off. Without that, a state that
never flips would freeze the window.

page_flip goes in VERIFY_SKIP because a plane comparison cannot judge it. The flip
draws nothing, so the planes match trivially, while the part that actually changed
- the timing - is invisible to that check. Saying "verified" off the back of it
would be exactly the mistake docs/notes/verification-lessons.md warns about.
"""

import sys

PATH = "native.py"

EDITS = [
    # ------------------------------------------------------ verify exclusion
    ("""VERIFY_SKIP = {
    "plot_pixel",""",
     """VERIFY_SKIP = {
    # Nothing to compare: the flip draws no pixels, so the planes always match,
    # and the timing it changes is not something a plane diff can see.
    "page_flip",
    "plot_pixel","""),

    # ------------------------------------------------------------ the flag
    ("""                 native_setup=False, skip_natives=(), persist=True, **kw):
        self.native_sound = native_sound""",
     """                 native_setup=False, native_flip=False, skip_natives=(),
                 persist=True, **kw):
        self.native_flip = native_flip
        self.flips = 0
        self.native_sound = native_sound"""),

    ("""        if self.native_setup:
            table += SETUP_NATIVES""",
     """        if self.native_setup:
            table += SETUP_NATIVES
        if self.native_flip:
            table += FLIP_NATIVES"""),

    # ------------------------------------------------------------ the native
    ("""def native_clear_vram(m, args):""",
     '''def native_page_flip(m, args):
    """0x04d4b: swap the video pages, program the CRTC, and present.

    The original delays (0x1f - [0x1fd4]) ms on the PIT, waits for display enable
    to fall, swaps [0x1725] with [0x1727], writes the new value to CRTC 0x0c/0x0d
    as two word OUTs, waits for vertical retrace, then advances a 0..9 phase
    counter at [0xd61].

    Everything except the two waits is reproduced. The waits are dropped on
    purpose: the retrace spin was ~1836 port reads per flip - 94% of all port I/O
    - and it burned the instruction budget that would otherwise draw. Presenting
    here instead makes the guest's own flip the frame boundary, so every game
    frame reaches the screen rather than the one-in-eight a fixed-size chunk
    happened to catch.

    The CRTC goes through _crtc_write rather than being poked directly, so
    start_addr and the addressing unit are derived the same way they are for a
    real OUT - assuming byte addressing here is what rendered every other frame
    black when this was first got wrong in emulation.py.
    """
    g = m.dgroup_base
    front = struct.unpack("<H", m.read(g + 0x1725, 2))[0]
    back = struct.unpack("<H", m.read(g + 0x1727, 2))[0]
    m.write(g + 0x1725, struct.pack("<H", back))
    m.write(g + 0x1727, struct.pack("<H", front))

    # The new visible page is what [0x1725] now holds; high byte to index 0x0c,
    # low byte to 0x0d, exactly as the two word OUTs did.
    m._crtc_write(0x0C, (back >> 8) & 0xFF)
    m._crtc_write(0x0D, back & 0xFF)

    phase = (struct.unpack("<H", m.read(g + 0x0D61, 2))[0] + 1) % 10
    m.write(g + 0x0D61, struct.pack("<H", phase))

    m.flips += 1
    present = getattr(m, "present", None)
    if present is not None:
        present()
    return None


def native_clear_vram(m, args):'''),

    # --------------------------------------------------------- the table
    ("""XMS_NATIVES = [""",
     """# Enabled with --native-flip. One entry: the page flipper at 0x04d4b, which is
# reached from 31 sites in the image by the `push cs; call near` idiom, three of
# them the instruction immediately after a plane loop's exit.
FLIP_NATIVES = [
    (0x04D4B, "page_flip", native_page_flip, "far"),
]

XMS_NATIVES = ["""),

    # ------------------------------------------------------------ the CLI
    ("""    ap.add_argument("--native-fp", default=True,""",
     """    ap.add_argument("--native-flip", default=True,
                    action=argparse.BooleanOptionalAction,
                    help="serve the game's page flip natively and present from "
                         "it, so every game frame reaches the screen. Drops the "
                         "retrace spin and the delay, so nothing limits the "
                         "frame rate but the host")
    ap.add_argument("--native-fp", default=True,"""),

    ("""               native_setup=args.native_setup,""",
     """               native_setup=args.native_setup,
               native_flip=args.native_flip,"""),

    # ------------------------------- present(), and only when the game did not
    ("""    bw, bh = base_size()
    screen = pygame.display.set_mode((bw * args.scale, bh * args.scale))
    pygame.display.set_caption("Ducks! - native I/O")
    clock = pygame.time.Clock()""",
     '''    bw, bh = base_size()
    screen = pygame.display.set_mode((bw * args.scale, bh * args.scale))
    pygame.display.set_caption("Ducks! - native I/O")
    clock = pygame.time.Clock()

    def present():
        """Put the current state on screen. Called per page flip when the flip is
        native, and once per chunk otherwise."""
        nonlocal screen, bw, bh
        nb = base_size()
        if nb != (bw, bh):
            bw, bh = nb
            screen = pygame.display.set_mode((bw * args.scale, bh * args.scale))
        surf = make_surface(m, font, CELL).convert(screen)
        pygame.transform.scale(surf, screen.get_size(), screen)
        pygame.display.flip()

    # Reached from native_page_flip. Set here rather than in build_machine
    # because it closes over the window, which a headless replay does not have.
    m.present = present'''),

    ("""        nb = base_size()
        if nb != (bw, bh):
            bw, bh = nb
            screen = pygame.display.set_mode((bw * args.scale, bh * args.scale))
        surf = make_surface(m, font, CELL).convert(screen)
        pygame.transform.scale(surf, screen.get_size(), screen)
        pygame.display.flip()
        frames += 1""",
     """        # The game's own flip presents now, so only present here if it did not
        # flip during this chunk - a text screen, a load, or --no-native-flip.
        # Without this a state that never flips would leave a frozen window.
        if m.flips == flips_before:
            present()
        frames += 1"""),

    ("""    while running:
        addr, running = step_frame(m, addr, args, img)""",
     """    while running:
        flips_before = m.flips
        addr, running = step_frame(m, addr, args, img)"""),
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
