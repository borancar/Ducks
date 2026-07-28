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

**What does not work: `snap` over the control socket, or F2.** Both set
`snapshot_requested`, which is serviced at the top of `native_page_flip` — and in
text mode there are no page flips, so the request is never taken. The capture
point was deliberately moved there because it is the only exact frame boundary
([testing-from-snapshots](testing-from-snapshots.md)); the cost is that it cannot
capture a text-mode state at all.

## What to pull out

Unknown until it is read. The file is open on handle 5 when the index is built,
so the reads that build it can be watched with `--trace-file`, and the resulting
structure lives in DGROUP. 303 slices is the count to check any decode against.

See [open-game-speed](open-game-speed.md) for the other thing waiting on a
startup-time value.
