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
