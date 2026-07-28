# Open: extract the episode index

**Pencilled 2026-07-28.** During startup, before the graphics mode is set, the
game builds an episode index from `EGGS\MAIN.EGG` and prints:

```
Using file EGGS\MAIN.EGG - 303 slices
Building episode index...
```

That index is worth having as data in its own right — it is the table of what is
in the 2.4 MB egg, and everything the game loads later goes through it. Extract it
next time this point is reached.

## Getting back to the point

`MAIN.EGG` is opened as handle 5 and the index is built while the machine is
still in `mode=0x03`. Two routes:

- **Boot again.** The startup path is deterministic — two separate sessions
  recorded byte-identical console output and *exactly* 9,475 reads of 0x3da, so
  reaching this point again is just running the program.
- **`--snapshot-at N`**, which captures at the main loop's frame boundary. Around
  frame 275 with `--flip-hz 70` is inside the index build.

~~**What does not work: `snap` over the control socket, or F2.**~~
**Wrong, corrected 2026-07-29.** The claim was that both set
`snapshot_requested`, which is serviced only at the top of `native_page_flip`, so
a state that never flips can never be captured. There is a second service point:
the main loop takes the request too, and its comment says exactly why — it
"covers `--no-native-flip` and states that never flip, such as a text screen,
which would otherwise ignore F2 entirely".

It was not even stale. The fallback landed in `a80c82d`, and `e966f50` — the
commit that wrote this paragraph — came after it. The limitation was asserted
from a reading of one service point without grepping for the other, and it has
been sitting here blocking the extraction this note asks for.

Demonstrated on 2026-07-29: F2 pressed while the machine was paused at a
breakpoint produced `snapshots/snap001.snap` with the flip counter unmoved at 1
before and after, and `replay.py --compare-restore` reports the round trip
byte-identical. That proves the fallback fires for a non-flipping state; it does
not *directly* prove text mode, which is one cold boot away from being settled
and is the thing to check first.

The frame-boundary argument in
[testing-from-snapshots](testing-from-snapshots.md) is unaffected and still the
reason the flip is the preferred capture point.

## What to pull out

Unknown until it is read. The file is open on handle 5 when the index is built,
so the reads that build it can be watched with `--trace-file`, and the resulting
structure lives in DGROUP. 303 slices is the count to check any decode against.

See [open-game-speed](open-game-speed.md) for the other thing waiting on a
startup-time value.
