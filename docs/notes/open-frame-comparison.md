# Open: frame-by-frame comparison, and the level 11 burst that was not a bug

**Opened 2026-08-07. Nothing to fix here yet - this is the pencilled note so the
same investigation is not run twice.**

## The report, and what it turned out to be

> in level 11, after the ducks have settled due to gravity, there's one extra
> spawner where there was no duck

It is the original's. Hooking the guest's own `particles_spawn` and pressing ESC
on `snapshots/snap012.snap` - level 11, eight ducks, settled at frame 245:

```
=== 8 call(s) to particles_spawn after ESC ===
   n=40  at ( 213, 143)   returns to 0x07950
   n=40  at ( 196, 148)   returns to 0x07950
   n=40  at ( 201, 149)   returns to 0x07950
   n=40  at ( 197, 148)   returns to 0x07950
   n=40  at ( 266, 121)   returns to 0x07950
   n=40  at ( 276, 121)   returns to 0x07950
   n=40  at ( 196, 148)   returns to 0x07950
   n=40  at ( 188, 144)   returns to 0x07950
8 burst(s) at 7 distinct place(s)
   ( 196, 148) has 2 bursts on the same pixel
```

Eight bursts, every one from `duck_dies` (`0x07950` is its call site), at
**seven** places: two ducks are standing on the same pixel. Nothing separates
ducks from each other - `collide_scenes` is scene 0 against scene 2, objects
only - so a pair sliding into the same hollow and coming to rest together is
ordinary. Level 11 has eight ducks and shows seven.

The port does the same: eight bursts, seven places, its duplicate at
`(197,148)`. And `test_entity.py snapshots/snap012.snap` is clean on that exact
state - `level_event` 104 checks, `collide_scenes` 44, `flock_link` 144, the
walk 640 fields, 576 general fields, none differing.

The suspicion that was wrong: that the settling was drifting the flock into a
heap the original would not make. It makes the same heap.

## What this needs, and why it is still open

Every comparison the repo has is **one call deep**. `test_entity.py` gives both
sides the same entity and the same terrain, steps each once, and diffs the
fields. That cannot catch a difference that only shows after a hundred frames of
compounding - a rounding step in the walk, a `sar` that should be a divide, an
RNG draw taken in a different order - because both sides are re-synchronised
from the snapshot before every single case.

What would catch it: **restore one snapshot into both, then run N frames on each
without re-synchronising, and report the first frame and the first field that
disagree.** The state to diff each frame is small and already known - the six
scenes' entity arrays, `duck_count`, `score`, the viewport's scroll, the
particle pool.

Two things make it harder than it sounds, both already known here:

- **The seed.** `level_seed` comes off the runtime clock at `0:0x17df`, so two
  runs of the same level do not agree unless it is forced on both sides.
  `test_entity.py` has `seed_sync`/`seed_same` for exactly this; a frame runner
  needs the same, and needs the two RNGs to stay in step draw for draw, which
  means every routine that draws has to be faithful in the *order* of its draws
  and not only in its result. `native_particles_spawn`'s comment already says so.
- **Input.** A played level reads the mouse and the keyboard every frame. Either
  drive both from a recorded event list or compare only demos, where the table
  at `[0x2100]` is the input and is identical on both sides.

## Pencilled: the demo, after three fixes

Three real gaps were found by reading, and each made the attract mode visibly
wrong on its own - `level_clock` never incremented so no recorded input fired at
all, the hero's script table at `[0x2043]` never walked so the flock never moved,
and `scroll_smooth` declared bare so the camera shoved instead of easing. See
[run-level](run-level.md) and
[open-dgroup-initialisers](open-dgroup-initialisers.md).

The camera still does not feel right after those. Nothing obvious is left to
read: the demo camera block at `0x0e34e` is transcribed, `scroll_axis` and
`scroll_follow` are both byte-compared leaves in `test_leaves.py`, and the two
demo tables now drive what they should. So this is the first thing the frame
runner above should be pointed at, and it is a good first target for one:

- **A demo is the easy case.** No mouse, no keyboard - the input is the two
  tables and they are identical on both sides, so the "drive both from a
  recorded list" problem does not arise.
- **The seed is in the recording.** `load_demo` reads `level_seed` out of the
  block (31 for the level 11 demo), so both sides start from the same seed
  without any forcing - as long as every routine that draws does so in the same
  order, which is now one more thing the run would be testing.
- **What to diff each frame**: `viewport_game.scroll_x`/`scroll_y` first, since
  that is the complaint, then the scene 0 entity array, then `level_clock`,
  `script_at` and `g_2100`. The first frame where the scroll differs says
  whether the camera is being told the wrong thing or told the right thing and
  moving wrongly, and those have different causes.

## Technique worth reusing

To find out which of four call sites fired, `game.c` was temporarily given a
`DUCKS_TRACE_SPAWN` env flag. It answered the question in one run and was then
removed - the port stays pristine. The version that costs the port nothing, and
is what to reach for next time, is to wrap `native.py`'s own handler instead:

```python
_real = native.native_particles_spawn

def logging_spawn(m, args):
    x, y, n = struct.unpack("<hhh", m.uc.mem_read(args, 6))
    ...
    return _real(m, args)

for off, entry in list(m.natives.items()):
    if entry[0] == "particles_spawn":
        m.natives[off] = (entry[0], logging_spawn) + tuple(entry[2:])
```

The far-call frame is already unpacked by then. A `UC_HOOK_CODE` on `0x077ae`
does *not* work for a routine native.py has taken over - the first attempt read
six bytes of nothing and reported `n=11479` eight times, which is the failure
mode [prove the instrument first](verification-lessons.md) is about.

## A probe that produced convincing false differences (springs, 2026-08-08)

Worth recording because it is exactly the trap a frame runner will fall into.

The report was that a sprung duck only goes up. To check it, a probe loaded
level 44 - the one level with both springs - into the guest and the port, copied
the guest's scene 0 and scene 2 into the port, put a duck on each spring, ran
`collide_scenes` on both and then stepped the duck for twenty frames. It
reported the port setting the duck's type to 2 where the guest set 0x40, which
looks exactly like the bug being hunted.

It was the probe. Driving the port's own `collide_scenes` on a hand-built state
- one duck, one spring, nothing else - gives `0x41`/`0x40`, `f15 = 0xf9` and the
object at `0x3e`/`0x3f`, which is the guest's answer and the disassembly's.

What the probe got wrong was everything it did not copy: `test_entity.py`
marshals the `lead` pointers between the two address spaces and syncs the other
scenes and the scalars, and this copied two scenes and nothing else. A duck with
a stale `lead` behaves differently, and the difference surfaces somewhere that
looks unrelated.

So for the frame runner: **copying the entity arrays is not copying the state.**
Reuse `test_entity.py`'s marshalling rather than writing it again, and before
believing any difference it reports, reproduce it on the smallest state that
shows it - which is what settled this in one run after an hour of not settling
it.

## What the springs actually do

Not a bug, and worth writing down so it is not re-investigated. Drawn out, the
two springs are mirror images: `0x3c` is anchored bottom-right and `0x3d`
bottom-left. Neither `collide_scenes` arm sets a horizontal term - both write
`f15 = 0xf9` and `f21 = 0` and change the types, and that is all.

The direction is in the type the duck becomes, and **only the hero gets one**:

    0x3c ->  hero 0x33, any other duck 0x41
    0x3d ->  hero 0x1c, any other duck 0x40

`entity_update`'s first switch is a compare chain, and extracting all of it gives
`{1, 2, 4, 0x1c, 0x1e, 0x25, 0x26, 0x33, 0x36}`. `0x1c` sets `f14` to `+2` while
`f21 < 0xf` and `+1` after; `0x33` sets `-2` then `-1`. `0x40` and `0x41` are not
in that switch at all, so an ordinary duck keeps whatever facing it was walking
with, and the only thing the spring gives it is the upward `f15`.

The landing arm at `0x08427`, which `0x40` and `0x41` do reach, is
`if (f21 > 0x32) duck_dies(); else { type = 2; f14 = 0; }` - no horizontal term
there either.
