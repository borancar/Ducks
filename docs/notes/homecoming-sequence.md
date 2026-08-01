# The homecoming sequence: the game's ending, behind level 80

**Read out 2026-07-31.** [entry-points](entry-points.md) records five unnamed
calls clustered at `0x1392f` in `main_menu` and notes only that all five contain a
four-plane loop. They are a cutscene, and this is what they draw.

| call site | function | resource ids | what is on it |
| --- | --- | --- | --- |
| `0x1392f` | `cutscene_rocket_space` `0x0f5b1` | `0x32` | a rocket crossing a starfield, then leaving the frame |
| `0x13946` | `cutscene_rocket_landing` `0x0fc8b` | `0x33`, `0x34` | down on the grass at dusk; the rockets are sprites, 12 `draw_sprite` calls |
| `0x1394a` | `cutscene_doorstep` `0x0f9fd` | `0x37`, `0x38` | a lit doorway: a duck in silhouette, then the same door with the duck revealed |
| `0x1394e` | `cutscene_welcome_home` `0x0f825` | `0x36` | the flock under a **"Welcome Home!"** banner |
| `0x13957` | `cutscene_photos` `0x0f913` | `0x3a`, `0x3b`, `0x3c` | three polaroids, one more on each screen, each arriving on a DAC-white flash and a sound |

`release_sounds` (`0x146cd`) runs between the fourth and the fifth, and
`0x147c5` twice earlier in the block. In call order the screens read as a
homecoming: fly back, land, arrive at the door, the welcome, then photographs of
it.

All five are the same shape — `clear_vram`, load one resource by id,
`set_plane` + `blit_rows` four times into each of the two video pages,
`page_flip`, hold, release. That is why every one of them turned up in the
`set_plane` census, and it is five more plane loops for
[drawing-port-goal](drawing-port-goal.md) to cover.

The ids form one block, `0x32`–`0x3c`, with `0x35` and `0x39` unused. The logo
and title (`0x4d:5`, `0x4d:8`) and the quit-path adverts (`0x4d:0x64`–`0x67`) are
elsewhere, so this block is the cutscene's own.

## The trigger: finishing the whole game

**Settled 2026-08-01, by playing the training episode and reading the gate's
verdict off a live machine.** It is the ending of the game — reachable only by
finishing `DUCKING HELL`, level 80.

The block is guarded by two tests:

```
0x13904  push [0x94] / push [0x2032]
0x1390d  call 0x11c75            <- episode_end_gate
0x13913  or ax, ax / jne 0x1391a
0x13917  jmp 0x139ab             <- zero: skip the whole sequence
0x1391a  cmp word [0x94], 0
0x1391f  jne 0x13999             <- non-zero: skip it too
0x13921  ...the homecoming block
```

`0x11c75` walks the episode index at `[0x20ba]` in its 14-byte records, looking
for one whose **last level** (`+0x08`) is `[0x2032]`, the level just attempted,
and whose `+0x06` — the always-zero high word of `first` — equals `[0x94]`. On a
match it plays a sound and shows that episode's own splash. Then:

```
0x11d01  mov ax, es:[bx + 0xc]     ; the record's flag field
0x11d05  mov [bp-2], ax            ; and that is what it returns
```

`+0x0c` is the flag [episode-index](episode-index.md) records as **set only on
the last record**. So the gate answers "was that the final episode?", not "did an
episode end", and only `DUCKING HELL` (51-80, flag 1) passes it.

Measured, rather than read: `snapshots/snap005.snap` is the end of the training
episode. Restored, `[0x2032]` reads **10** — `TRAINING LEVELS` ends at 10 — and
`[0x94]` reads 0, so the search matches record 0 and the splash is drawn. A
breakpoint on `0x13913` then catches **`AX = 0x0000`**, and the jump at `0x13917`
skips the sequence. The stack at that moment ran
`0x11bee <- 0x11c75 <- ret 0x13910 <- main`, which is what identified the
"That's enough training" screen as the gate's own.

**The reading this replaces was wrong in an instructive way.** From the record
layout alone the gate looks like "the level just attempted is the last level of
some episode", which would fire at the end of every episode — and that is what
was written here first. The search key is not the verdict: the function matches
on the last level and *returns the terminator flag*. Boran called the answer -
"the whole game needs to be finished" - before the measurement, against that
reading.

Two earlier observations fit this and only look like separate facts:

- An idle menu, demoing for minutes, reaches none of them.
- A played game ending in a game over reaches none of them either, with all five
  armed throughout. Dying never sets `[0x2032]` to an episode's last level, so
  the search finds nothing at all and the gate returns zero for that reason
  instead.

So this is the game's ending, sitting behind the 60 registered-only levels -
content a shareware copy cannot reach, like the quit-path adverts in
[entry-points](entry-points.md).

**Getting to level 80 without playing 79 levels.** `[0x2032]` is the level about
to be attempted, and `main_menu` writes it from the chosen episode record's
*first* level at `0x137a7`, so a poke only sticks once you are inside the inner
loop - on the screen offering the next level, not on the episode select. From
there `write d+0x2032 50 00` over the control socket is enough, and
`snapshots/snap010.snap` holds the result. Two cautions, both observed: the idle
attract demo plays levels and carries the counter with it, so a poke left sitting
drifts; and level 80 is the first state ever to run the background warp, which is
its own unfinished business (see the root README).

**The `0x0fc8b` loose end.** It is the only one of the five that never reached
its own return, in four separate runs — the others exit after a fixed hold. It
either waits on something the synthetic call does not supply, or its hold counts
from state the real sequence sets first. A breakpoint on its loop back-edge would
say which.

## Seeing them: [`show_cutscene.py`](../../show_cutscene.py)

```sh
venv/bin/python show_cutscene.py                        # play them in a window
venv/bin/python show_cutscene.py --capture debug/       # PNGs instead
venv/bin/python show_cutscene.py --only 0x0f825         # just the one
```

Each takes no arguments and is reached by `push cs; call near`, so the call is
two words on the stack and a new `CS:IP`; the callee's own `retf` pops them. The
game draws every pixel — this is the [testing-from-snapshots](testing-from-snapshots.md)
answer for a state that cannot be produced on demand, the same pattern
`test_retire.py` uses, and it is not a reimplementation of anything.

## Three ways the measurement was wrong first

Worth recording as three more entries in [verification-lessons](verification-lessons.md),
because each produced a confident, coherent, wrong answer.

**A breakpoint on `page_flip` proved nothing.** It was chosen as the control that
must fire, and it never did — because `--native-flip` means the guest never
executes `0x04d4b` at all. A control has to be code the guest still runs; an
address inside the in-game frame function fired on the next frame, and only then
did "none of the five fired" mean anything.

**Four of the five appeared to draw the same picture.** They did not: past the
return address the guest carried on through the rest of `main_menu`'s sequence
and drew `0x0fc8b`'s screen underneath the one being watched. Trapping the return
separated them. The give-away was in the instrument already — every run logged
loads of `0x33`/`0x34` at the end, whichever screen it had been asked for.

**`0x0f5b1` rendered black and was nearly written up as "not in this egg"**,
which is a real thing that happens here — quit-path advert (8) is exactly that.
Hooking the loader's result said `AX=1`: it loaded. The screen had run 227 flips
inside 21 display frames, so every sample fell outside it. A display frame is a
fixed instruction budget, not a game frame, and pacing does not change that —
only shrinking the budget does. [testing-from-snapshots](testing-from-snapshots.md)
already warns about this from the bonus screens; it cost a round here anyway.

One instrument limit worth stating rather than leaving implied: the load hook
watches `0x05a67`, and `cutscene_doorstep` loads through `0x05a95` instead, so
its `0x37`/`0x38` come from the disassembly rather than from a run.

## Also settled on the way

`0x1271b`, `main_menu`'s first call, is on the stack above **both** the menu
compositor (returning to `0x12736`) and the in-game frame (returning to
`0x12766`), so one routine drives the menu and the demo level. Recorded as
`menu_screen_driver?`, tentative — the two observations are real, the name is a
reading of them.
