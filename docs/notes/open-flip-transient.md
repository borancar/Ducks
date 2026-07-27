# Open: the first ~120 frames after a native flip are 4x too slow

**Open as of 2026-07-28.** Noticed while playing: the hovered/between-level menu is
visibly slow for the first moment and then settles. Reproduces from
`snapshots/menu-flip-transient.snap`, so it needs no play-through.

    venv/bin/python replay.py snapshots/menu-flip-transient.snap --frames 30

## The measurements

Per drawing-loop interval - one game frame - unpaced (`--flip-hz 0`), timed at the
plane loop so the marker works with or without the native flip:

| frames | native flip | `--no-native-flip` |
| --- | --- | --- |
| 0-9 | 80 ms | 28.5 ms |
| 10-29 | 76 ms | 28.5 ms |
| 30-59 | 75 ms | 28.6 ms |
| 60-119 | 40 ms | 28.8 ms |
| 120-199 | **21 ms** | 28.6 ms |

So our flip ends up *faster* than the guest's own - 21 ms against 28.6 - and the
problem is only the approach to it.

**The guest is not doing more work.** With a block hook counting basic blocks
entered, blocks per frame are flat while wall time falls 4x:

| frames | ms/frame | blocks/frame | code bytes/frame |
| --- | --- | --- | --- |
| 0-9 | 120.8 | 22748 | 403511 |
| 30-59 | 40.5 | 20895 | 381307 |
| 120-200 | 30.2 | 20751 | 380752 |

Same code, same volume, four times the wall time. **The cost is emulator-side.**

## Ruled out

- **Sound.** `--no-native-sound` still shows it (78 -> 19 ms), and so does
  `--no-blaster` with the card model gone entirely (110 -> 28.6 ms, and there
  blocks/frame is *exactly* flat at 20327 after the first bucket). The
  `sound_gather` calls per flip decaying 3.6 -> 0.9 is a symptom of frames getting
  cheaper, not a cause.
- **Drawing.** `plane_loop` runs exactly 1.0 times per flip throughout, and native
  time per frame is only 3.2 ms of the early 80.

## The lead, and a warning about the comparison

Something invalidating Unicorn's translation cache repeatedly during the first ~120
frames would look exactly like this: same blocks, re-translated over and over. Known
callers of `ctl_remove_cache` are the hook installs and `_patch_fp_site`, which
patches each FP site on **first execution** - so a stream of them early is expected.
Next step: log every `ctl_remove_cache` with the frame number, and try
`--no-native-fp`.

**But do not trust the `--no-native-flip` column too far.** 28.5 ms is suspiciously
exactly two retrace periods (2 x 14.29 ms at 70 Hz). The guest's flip waits for the
next retrace, so its frame time is *quantised* to multiples of the period, and that
quantisation could be masking the same underlying variation rather than proving its
absence. A fair comparison needs the underlying cost measured with the wait removed
but the flip otherwise untouched - which is a third configuration neither column is.

## Why it matters, and why it can wait

At the 70 Hz pacing this is played at, 80 ms a frame means missing four slots out of
five, which is what you see. It self-clears in under two seconds and the steady state
is better than what the guest managed, so it is a startup artefact rather than a
regression in throughput.

See [testing-from-snapshots](testing-from-snapshots.md) for the harness, and
[verification-lessons](verification-lessons.md) for why the block counter went in
before the conclusion did.
