# The homecoming sequence: the game's ending, behind level 80

**Read out 2026-07-31, extended 2026-08-01.** [entry-points](entry-points.md)
records five unnamed calls clustered at `0x1392f` in `game_main` and notes only
that all five contain a four-plane loop. They are a cutscene - six of them, as it
turned out - and this is what they draw.

| call site | function | resource ids | what is on it |
| --- | --- | --- | --- |
| `0x1392f` | `cutscene_rocket_space` `0x0f5b1` | `0x32` | a rocket crossing a starfield, then leaving the frame |
| `0x13946` | `cutscene_rocket_landing` `0x0fc8b` | `0x33`, `0x34` | down on the grass at dusk; the rockets are sprites, 12 `draw_sprite` calls |
| `0x1394a` | `cutscene_doorstep` `0x0f9fd` | `0x37`, `0x38` | a lit doorway: a duck in silhouette, then the same door with the duck revealed |
| `0x1394e` | `cutscene_welcome_home` `0x0f825` | `0x36` | the flock under a **"Welcome Home!"** banner |
| `0x13957` | `cutscene_photos` `0x0f913` | `0x3a`, `0x3b`, `0x3c` | three polaroids, one more on each screen, each arriving on a DAC-white flash and a sound |
| `0x1396e` | `cutscene_night_monster` `0x100f4` | — | **an animation**: at night, a monster runs towards the house. Added 2026-08-01; see below |

`release_sounds` (`0x146cd`) runs between the fourth and the fifth and again
after the sixth, and `0x147c5` twice earlier in the block. In call order the
screens read as a homecoming: fly back, land, arrive at the door, the welcome,
photographs of it - and then, at night, something coming for the house.

The first five are the same shape — `clear_vram`, load one resource by id,
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
and whose `+0x06` — the **egg file index**, which `game_main` copies into
`[0x94]` and uses to index `egg_files` — equals `[0x94]`. On a
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
to be attempted, and `game_main` writes it from the chosen episode record's
*first* level at `0x137a7`, so a poke only sticks once you are inside the inner
loop - on the screen offering the next level, not on the episode select. From
there `write d+0x2032 50 00` over the control socket is enough, and
`snapshots/level80-ducking-hell.snap` holds the result.

Two cautions. **A poke left sitting drifts**, because the attract demo plays real
levels and takes the counter with it: a menu capture replayed headlessly for 700
frames, with nobody at the keyboard, ran 2057 in-game frames and moved `[0x2032]`
from 0 to 26. So poke it immediately before starting the level and read it back.
(First written here as a guess from a counter that moved by two while a person
was also playing, which proved nothing either way; the replay is what settles
it.) And level 80 is the first state ever to run the background warp, which is
its own unfinished business - see the root README.

## Watched for real, 2026-08-01

Boran finished level 80 and the sequence played, exactly as the gate predicted.
The run in full, captured as it went:

| snapshot | screen |
| --- | --- |
| `level80-late` | level 80 in play, four ducks left |
| `ending-bonus` | **BONUS SCREEN** - time, survivors, lives, total, score |
| `ending-completed` | **EPISODE COMPLETED!** |
| `ending-landing` | `cutscene_rocket_landing`, running |
| `ending-highscore` | **NEW HIGH SCORE! ENTER YOUR NAME**, after the photographs |

**`0x100f4` is a sixth screen, and it is not the high score.** Position said it
was: it is the call immediately after `cutscene_photos`, and the high-score
screen is what came next. Two breakpoints say otherwise.

- The stack under `ending-highscore` runs
  `game_main (ret 0x139a5) <- 0x11d54 <- 0x12dfb <- menu_screen_driver`, and
  `0x139a5` is the return from `0x139a2 call 0x11d54`, not from `0x1396e`.
- Breaking on `0x100f4` itself, the far return on its own stack frame reads
  `05da:ecd1` = image **`0x13971`**, so it really is the call at `0x1396e` - and
  what it draws is **an animation: at night, a monster running towards the
  house**, watched on the way past. It is the only animated screen in the
  sequence; the other five hold a still.

So the ending is six screens, and `0x11d54` at `0x139a2` is what follows them.
It is **both** high-score screens rather than one: driven with a Return at the
name entry it goes on to draw `DUCKS HALL OF FAME`, and pausing there puts
`0x11d54` on the stack again, returning to `0x11ef4` inside itself.
`ending-highscore` and `ending-halloffame` hold the two halves. What it does at
its other three call sites - one of them before a level even starts - has not
been watched, so presumably it tests whether the score qualifies before drawing
anything.

`0x0f55c`, called immediately after it at `0x139a6` and paired with it at all
four sites, is still unidentified. It is not the table.

**The menu shows the same table, and not through this routine.** Left alone, the
menu puts `DUCKS HALL OF FAME` up by itself now and then. Two captures of that,
`menu-halloffame` and `menu-halloffame-2`, both walk back to
`menu_screen_driver` (`0x1271b`) under `game_main`'s outer-loop call at
`0x1368f` - `high_score_screen` is nowhere on either stack. So the attract cycle
draws its own copy, and the guess that `0x11d54`'s other three call sites were
doing it is wrong.

The two captures also settle something smaller: they were caught at different
depths, one returning to `0x12736` and the other to `0x12766`, which are
`0x1271b`'s two internal call sites. The table appears under both, so those sites
are not one screen apiece - `0x1271b` cycles menu, demo level and table through
the same machinery.

This is the third time in this note position has been wrong and a breakpoint has
been right.

`EPISODE COMPLETED!` is `episode_end_gate`'s own splash - the same slot that
showed "That's enough training" for `TRAINING LEVELS`, this time from the record
whose flag is set. So the whole chain is now observed rather than read: last
level -> bonus -> the episode's splash -> flag 1 -> the block.

**The driven screens are not the whole picture.** `ending-landing` has **two ducks
standing on the grass**, waving, beside the rockets; `show_cutscene.py` draws the
same screen with rockets alone. The 12 `draw_sprite` calls place characters from
state the real sequence sets up before calling, and a synthetic `push cs; call
near` supplies none of it. The tool is still the way to *see* these screens
without 80 levels, but it shows a backdrop, not the finished frame - and anything
claimed about their content should come from a real run.

That also answers the loose end below, or nearly: `0x0fc8b` never reached its own
return under the synthetic call, in four separate runs, while the others exit
after a fixed hold. Animating against state that was never set up is a better
explanation than a missing input, and a real capture of it running would settle
it.

**The `0x0fc8b` loose end.** A breakpoint on its loop back-edge, against
`ending-landing` rather than a driven call, is the thing to try.

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
return address the guest carried on through the rest of `game_main`'s sequence
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

`0x1271b`, `game_main`'s first call, is on the stack above **both** the menu
compositor (returning to `0x12736`) and the in-game frame (returning to
`0x12766`), so one routine drives the menu and the demo level. Recorded as
`menu_screen_driver?`, tentative — the two observations are real, the name is a
reading of them.
