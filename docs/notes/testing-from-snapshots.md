# Testing from snapshots instead of playing

**Added 2026-07-27 and exercised the same day. Round trip byte-identical, and all
four plane loops verified clean from snapshots with nobody at the keyboard - the
in-game ones from a menu capture, because the idle menu runs a demo level itself.
See [Status](#status).**

## Why

A native can only be verified where it is called, and the calls live in states that
have to be played to: the in-game frame loop during a level, the HUD loop twice a
level, the tally loop at the end of one. Every verification run therefore cost a
play-through, and the failure mode is quiet — a 900-second session that never left
the text screen recorded zero comparisons and looked like a clean run.

Snapshots change the unit of work from "play to the state" to "capture the state
once".

## How

```sh
venv/bin/python native.py                    # F2 at the moments that matter
venv/bin/python replay.py snapshots/snap001.snap --frames 200 \
      --verify-only compose_scroll,draw_entities
```

`F2` and `touch snapshot.request` both capture; `--snapshot-at 400,900` captures by
frame number unattended. `native.py --load-snapshot <path>` resumes a capture
interactively, which is the way to check that a restored state is actually the one
you meant to keep.

`replay.py` is headless and returns non-zero on failure. Unrecognised flags go to
`native.py`'s parser, so the same state can be replayed with a native on and off —
that is what makes an attribution stick.

## Three assertions, and why each is needed

- `--verify-only <names>`: byte-compares a native against the original body. Sound
  under any clock, because the comparison is between the native and the real code
  **on the same call within one run** - which is why freezing the clock was not
  needed for this to be a test.
- `--require <names>`: fails if a routine never ran. Without it, a state that does
  not reach the routine reports zero mismatches and reads as a pass. Same trap as
  `mode=0x03` in [running-a-session](running-a-session.md).
- `--compare-restore`: save, restore into a second machine, save again, diff every
  byte. This tests the snapshot machinery rather than the game, which
  [verification-lessons](verification-lessons.md) says to do before believing
  anything measured through it.

## What is captured, and the three deliberate omissions

Guest memory (2 MB, one flat mapping), the register file with flags, the four VGA
planes and their register state, polled input state, every XMS block, the Sound
Blaster model, open files with positions, and the sample bank.

Not captured: hooks and natives, which come from `build_machine()` and the flags -
that is what lets a capture made with everything on be replayed with one piece off;
playing voices, which are stopped with the guest voice table reset to agree, since a
table left saying "busy" never frees and the game stops starting sounds; and the FP
site table, which does not need capturing because a patched site is two bytes in
guest memory and restores with it.

**Capture only at a frame boundary.** The x87 stack is empty there and no native is
part-way through reading its arguments off the live frame. The tag word is checked
and warns if it is not - a snapshot that looks fine and restores wrong is exactly
the failure this project keeps relearning to avoid.

## Verify the loop, not the routines inside it

The first real verification run compared **nothing** and said so. The natives named
- `compose_layer`, `draw_entities`, `draw_sprite` - never came through the native
dispatcher, because a native plane loop calls their resolved-argument cores as plain
Python. Nothing is intercepted, so nothing is verified.

`--verify-only plane_loop` is the gate for all four loops, and it is the right thing
to check anyway: the loop's harness snapshots at the head and diffs the planes at
the exit, which covers everything the loop calls.

## The menu demo is the way into the drawing path

**Left idle, the menu starts a level as a demo and autocontrols it.** So the in-game
drawing states need no player at all: replay a menu snapshot taken with the mouse at
rest, give it a hundred frames, and the game walks itself into a level.

That is the automation route this note was written to look for, and it was found by
accident - a menu snapshot replayed for 100 frames turned out to be running
`plane_loop 0x0e4dc` 562 times, loading files and playing samples, and ended on a
snowy level with a live HUD.

The corollary matters too: **a menu snapshot is not a stable menu baseline.** A
capture with the mouse hovering a menu item stays in the menu; one with the mouse at
rest does not. Comparing the two measures menu-versus-level, not the cost of
hovering, and an attempt to answer the hover/no-hover question from session 1 that
way is invalid - the two runs are not doing the same work.

## Status

Proven, from one menu snapshot captured at frame 153, `mode=0x13`, replayed
headlessly:

- `--compare-restore`: **byte-identical** round trip - memory, planes, XMS,
  registers, VGA, DOS and card state all survive save/load/save.
- 100 frames with `--verify-only plane_loop`: **692 comparisons, 0 mismatched, 0
  declined**, made up of 467 on `plane_loop_scroll` (`0x0e4dc`, the in-game frame),
  223 on `plane_loop_layer` (`0x0cd5f`) and 2 on `plane_loop_hud` (`0x0d9a2`).
- Those 2 HUD comparisons are not a thin sample - the loop draws once into each of
  the two video pages at level start and never again, so two is the whole population
  for a level.
- `--require plane_loop 0x0e4dc` confirmed the in-game loop actually ran, which is
  what makes the zero mismatches mean something.

**All four plane loops are now verified from snapshots**, none of it needing a
play-through to reproduce:

| loop | comparisons | from |
| --- | --- | --- |
| `plane_loop_scroll` `0x0e4dc` | 467 | `main-menu-unhovered`, via the demo |
| `plane_loop_layer` `0x0cd5f` | 223 | the same run |
| `plane_loop_hud` `0x0d9a2` | 2 | the same run - and 2 is the whole population |
| `plane_loop_tally` `0x0bc4b` | 318 + 325 | `level1-bonus`, `level2-bonus` |

The bonus screens also put ~2000 `draw_number` calls a run through the fixed-width
decimal path with *changing* values, which no earlier state exercised. They are also
the first state measured that cannot hold 70 Hz: 15.0 ms a frame, ~7% of slots
missed.

## The snapshot library

| snapshot | state |
| --- | --- |
| `main-menu-hover` | menu, PLAY DUCKS lit; hovering suppresses the demo so it stays a menu |
| `main-menu-unhovered` | menu, idle; drifts into the demo, which is the way into the in-game path |
| `level-fast` | level 1, green hill - the cheaper level to draw |
| `level-frameskip` | level 2, cave - heavier, and where the timer drains quicker |
| `level1-bonus` | end of level 1, counters mid-count |
| `level2-bonus` | end of level 2, same loops, different values |

Each carries its own description, `elapsed` and `chain4`, and each was verified
byte-for-byte after being renamed.

**To look at a captured state, render it with `--frames 0`.** Anything else runs the
game on from there, and unpaced even one display frame is dozens of game frames -
which is how two perfectly good bonus-screen captures got reported as black.

Still untested: whether demo play and human play exercise the same paths. Every
in-game result above except the bonus screens comes from the demo.

## Two bugs the byte-level tests could not have caught

Both were found by running the thing, and neither would ever have failed
`--compare-restore`. Worth reading as a pair, because they fail in the same way for
the same reason: the check compares what it was given, and says nothing about what
it was not given.

**`sb.pcm` came back immutable.** It is a bytearray the card model appends to; the
encoder tagged bytes and bytearray identically, so the first sound service after a
restore died on `.extend`. `--compare-restore` passed on that snapshot - it compares
content, not Python types, and base64 of a bytearray equals base64 of the same
bytes. Restore now coerces each value to the type the freshly built machine already
has, which also keeps snapshots taken before the fix loadable.

**`chain4` was not captured, and the screen was black.** It decides whether the
display reads the linear aperture or interleaves the four planes. A fresh machine
starts chained and the game unchains it entering Mode X, so a restored Mode X state
drew perfectly into the planes while the display read an aperture Mode X never
fills. Byte-identical round trip, 97 plane-loop comparisons clean, black screen -
because verification diffs the *planes* and never goes through the display path. It
took looking at it. For snapshots that predate the fix, restore infers chain4 from
whether the planes hold anything, and says so.

The general lesson, which is [verification-lessons](verification-lessons.md) again
from the other side: a passing comparison bounds only what it compared. Ask what the
instrument cannot see before reading its silence as agreement - and render the
screen when the claim is about the screen.

See [editing-conventions](editing-conventions.md) for why the `native.py` half of
this was applied by an anchored one-shot script.
