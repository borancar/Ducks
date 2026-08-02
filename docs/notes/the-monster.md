# The thing that eats ducks, and how a type becomes a state machine

**Read 2026-08-03**, out of `snapshots/snap003.snap` — a demo capture taken at
clock 460, late enough in its level that the actor is already on screen.

## What it is

One entity in scene 2, and it is four entity types rather than one. Sampling
once per *game* frame — a code hook on the clock increment at `0x0dcd8`, because
sampling once per display frame lands about 130 ticks apart and sees nothing —
gives this:

| clk | | |
| --- | --- | --- |
| 460–476 | type `0x46` at (107, 88) | stationary |
| 477 | becomes `0x39` | starts moving, 107,88 → 106,88 → … → 99,94 |
| 486 | at (98, 95), one pixel from the duck at (97, 97) | **the duck's type is set to 0** and the monster reverts to `0x46` |
| 487 | | scene 0's count drops 2 → 1; the record is gone |

It happened twice in one run. The second kill, at clock 825, emptied scene 0 —
which is one of `in_game_frame`'s four endings, so **the monster can end a
level**.

## The states are data, not code

`load_animations` fills six parallel tables of 112 entries, and `next_type` at
`d+0x0416` is the one that matters: *what a type becomes when its script runs
out, and a type that points at itself loops*. Read out of the snapshot:

| type | script | `next_type` | |
| --- | --- | --- | --- |
| `0x39` | 6 frames, sprites 181–184 | **itself** | prowling; loops forever |
| `0x4b` | 3 frames, sprites 230–231 | `0x39` | a short one-shot mid-swim |
| `0x46` | 11 frames, sprites 209–215 | `0x39` | the eat |
| `0x47` | 11 frames, sprites 216–222 | `0x39` | the same eat, other facing |

That accounts for every transition observed, and for the durations: the eat ran
22 frames against an 11-entry script, so a script step is two frames. There is no
"monster" routine — the behaviour is a loop type, a few one-shots, and a table
saying what follows what. Whatever decides to *enter* `0x46` is elsewhere and
still unread.

`0x39` and `0x4b` have bit 2 set in `type_flags`, which
[in-game-frame](in-game-frame.md) records as "has a mirrored script in the next
slot" — so their facings come from the flag, while `0x46`/`0x47` are authored as
two separate types. Two mechanisms for the same idea, in one actor.

## Death is a type of 0

The prey is not removed by whatever kills it. Its type is set to `0` on the frame
of contact, and the retire pass — `0x0981b`, called on all six scenes at
`0x0e2c9` — compacts it out on the next one. Both kills show exactly this, one
frame apart. So a scene's entity array only ever shrinks at one place in the
frame, which is worth knowing before porting anything that walks it.

## What the capture was worth

It is the first state ever to run the sprite outline: `draw_entities` prints
"this path is UNVERIFIED — no session has contained an entity of type 0x0f or
0x10" the moment it executes, and no capture before this one did. It is also
what exposed the shadowing bug in `--verify` — see
[verification-lessons](verification-lessons.md).

The tool-event table in this capture is **empty** (`n=0`) and both entries of the
event table are already past at clock 460, so it does nothing for `tool_events`
or `0x0d471`.
