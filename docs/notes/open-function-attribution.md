# Open: find_function_start swallows show_splash

**Found 2026-07-29.** `find_function_start` and `function_extent` both report
that image `0x102d7` is *inside* a function beginning at `0x100f4`:

```
0x102d7: find_function_start -> 0x100f4   extent -> (0x100f4, 0x103e2)
0x100f4: find_function_start -> 0x100f4   extent -> (0x100f4, 0x103e2)
```

It is not. `0x102d7` is a function in its own right:

- it begins `55 8b ec 83 ec 32` — `push bp; mov bp, sp; sub sp, 0x32`, the
  textbook Borland prologue the whole attribution scheme is built on;
- **main calls it**, at `0x14520`, and a breakpoint on its first byte fires with
  `SP` exactly ten below main's `BP` — the six bytes of arguments and the four of
  far return, so the call is real and not a fall-through;
- it ends in its own `pop di / pop si / leave / retf` before `0x103e2`.

So the extent `0x100f4`-`0x103e2` covers two functions, and every byte of
`show_splash` is attributed to its neighbour.

## Why it matters

Not for the natives that exist today — none of them is in this range — but for
three things that read attribution as truth:

- `where` over the control socket mislabels any address in `show_splash`, which
  is how this was noticed.
- `coverage.py` counts those 267 bytes against the wrong function.
- The plane-loop safety argument in [drawing-port-goal](drawing-port-goal.md)
  rests on `function_extent` bounding the references to a shared local. That
  argument was made for the four loops in `0x0d7ee`-`0x0e8ac`, and this failure
  gives no reason to doubt those particular extents — but it does mean the tool
  behind them can be wrong without saying so, which is the same shape as
  `ccde72e`, "function attribution, which was failing silently on big functions".

## What to do

`test_fn_start.py` pins boundary attribution to known answers, and this case is
not among them. **Add `0x102d7 -> 0x102d7` as a pinned case** — it will fail,
which is the point; then fix the walker and watch it go green. Prove the test by
putting the wrong answer back, as [address-spaces](address-spaces.md) records
doing for `test_symbols.py`, rather than trusting a green run.

Deliberately *not* done as part of finding this: a red test committed without the
fix leaves the suite failing for whoever runs it next, and the fix is a change to
the walker every extent in the project depends on. It wants its own change, with
the four plane-loop extents re-checked after.

Not yet known: how many other functions this affects. The cheap census is to run
`find_function_start` over every prologue in the image and list the ones that do
not resolve to themselves — the same independent rule
[address-spaces](address-spaces.md) describes, applied in bulk.
