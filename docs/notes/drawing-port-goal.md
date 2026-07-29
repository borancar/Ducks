# Why the drawing port exists

The point of reversing Ducks' drawing code is to **eventually replace planar
Mode X with flat drawing**. The framing that set the direction: *"we sadly need to
reverse engineer the entire drawing code... for that we need to know what we're
drawing. Also note that guest CPU is running that drawing for sprites, killing
itself."*

That changes what counts as progress. Replacing the entity loop alone costs out
as a ~2% speedup, which on its own looks like a bad trade — but the 4x plane
multiplication *is* Mode X. Flat drawing draws each sprite once rather than once
per plane, so moving the whole pipeline native collapses the plane loop in the
compositors, the sprite loop and the particle loop together. These steps should
not be judged on their own immediate speedup.

## Where it stands

**Corrected 2026-07-29: four is the count for an in-game frame, not for the
program.** Two more plane loops turned up in one session, both drawing the intro
screens and neither native:

| loop | in | shape |
| --- | --- | --- |
| `0x10383`-`0x103b0` | `show_splash` `0x102d7` | `set_plane` + `blit_rows` x4, `page_flip` |
| `0x0b588`-`0x0b5b6` | `0x0b52f`, reached from `show_resource` `0x0c1ad` | the same, from a global viewport |

Neither runs during a level, so no profile taken there would show them — which is
the likely reason four was ever the count. See [entry-points](entry-points.md).

**The real total is unknown.** A census of calls reaching `set_plane` finds **26
call sites**; it rediscovers all four loops below and both of the above, which is
what makes the count of *sites* trustworthy. But a `set_plane` call is not
necessarily a four-plane loop, and the heuristic written to tell them apart —
looking for the `cmp byte [bp-x], 4; jb` back-edge — **missed three of the four
loops below**. It failed its own control, so its answer is not recorded here.
Deciding this properly means reading the 26 sites, or building a classifier that
gets all six known answers right first
([verification-lessons](verification-lessons.md)).

One site to look at early: `0x0e6c1`, a third `set_plane` inside `0x0d7ee`, the
function this note describes as holding exactly two loops.

Everything below still holds for the four; only the claim that they are *all* of
them was wrong.

The four are native and verified, and the compositors'
plane-independent work is hoisted out of them:

| loop | exit | what it drives |
| --- | --- | --- |
| `0x0cd5f` | `0x0cd98` | the layer caller |
| `0x0e4dc` | `0x0e673` | the in-game frame — by far the busiest, `draw_entities` runs ~34,000 times a session mostly from here |
| `0x0d9a2` | `0x0db2c` | the HUD, in the same function as the scroll loop and sharing its `[bp-0x1f]` plane counter |
| `0x0bc4b` | `0x0bca9` | the end-of-level score tally, inside `0x0bba1` |

Because the HUD loop shares the scroll loop's counter, the "nothing reads the
counter after the loop" argument had to be redone for it rather than inherited.

Everything inside all four is native: `set_plane`, `compose_scroll`, `particles`,
`draw_entities` (no longer declining for any entity type), `blit_rows_masked`,
`blit_rows`, `plot_pixel`, `draw_number`, `draw_number2` and the sprite outline.

## Next

With the loops in hand, flat drawing becomes a change inside our own code rather
than a reversing problem: `set_plane` becomes a no-op and the `x & 3` filters
disappear from `blit_sprite`, the outline and the particles.

## Two corrections worth keeping

**The outline at `0x65f1` does run.** An earlier note here recorded it as never
executed and therefore unverifiable. Wrong: it runs in ordinary play (8–16 calls a
session, from the HUD loop at `0x0da30`) and has been byte-compared with zero
mismatches in sessions where it was called 270 and 354 times. What has *not* been
shown to fire is a narrower thing — the shadow path inside `draw_entities`,
reached only when an entity of type `0x0f`/`0x10` precedes another. Don't conflate
"a routine never ran" with "one call site never ran".

**Native micro-optimisation is nearly exhausted as a lever.** About 85% of each
frame is emulated guest CPU. The reason to keep going is reach, not speed.

Type 5 with y < 0 (a balloon floating off the top) retires the entity via
`0x78d4` — verified by `test_retire.py` driving the guest's own code, which is the
pattern to reuse when a game state cannot be produced on demand.

See [verification-lessons](verification-lessons.md) and
[editing-conventions](editing-conventions.md).
