# Closed headlessly: the "4x slow first 120 frames" was the measuring machine

**Opened 2026-07-28, closed headlessly 2026-07-28.** The claim was that the first
~120 game frames after a native page flip cost 80 ms each, decaying to 21 ms, and
that it reproduced from `snapshots/menu-flip-transient.snap`. **It does not
reproduce.** Re-run on an idle machine, the same snapshot with the same command is
flat from the first bucket. What was measured was contention on the host.

The interactive half of the report - a visibly slow moment on the between-level
menu that then settles, seen while playing - is *not* covered by this and stays
open. See [What is still open](#what-is-still-open).

## What the re-run shows

Same snapshot, same code (`b70086d`, tree clean), unpaced, on a freshly booted
idle host. Timed at **both** markers in one run - the interval between native page
flips, and the interval between plane loops, which is the marker the original
table used:

| flips | ms/frame, flip marker | ms/frame, plane-loop marker |
| --- | --- | --- |
| 0-19 | 21.0 | 21.1 |
| 20-39 | 20.1 | 20.1 |
| 100-119 | 20.3 | 20.3 |
| 500-519 | 20.2 | 20.2 |

The two markers agree to 0.1 ms, so neither instrument is the story. There is a
~5% first-bucket warm-up and nothing else. `level-start.snap` is flat at 6.0 ms
per plane loop from bucket zero as well.

The `--no-native-flip` control is *unchanged* from the original measurement: flat
28.5-28.7 ms across 430 intervals. So the steady-state comparison in the original
note still stands and is the useful part of it - **our flip is faster than the
guest's own, 20 ms against 28.6.**

## The lead was wrong, and cheaply so

The hypothesis was repeated Unicorn translation-cache invalidation - same blocks,
re-translated - and the prescribed next step was to log every `ctl_remove_cache`
with a frame number. Done, wrapping `ctl_remove_cache`, `hook_add` and `hook_del`
at the `Uc` class level and attributing each call to its Python caller:

- **29 invalidations during build and restore**, all from the hook installs.
- **0 for the entire 525-flip run.** Not "few" - none. `_patch_fp_site` never
  fires from a restored snapshot, because the sites in a captured image are
  already patched: the patch is two bytes of guest memory and it restores with
  them, which [testing-from-snapshots](testing-from-snapshots.md) already says is
  why the FP site table need not be captured.

So the mechanism could not have been operating, whatever the timings had shown.

## Why the original numbers looked the way they did

Host CPU contention reproduces the reported shape closely. With 16 spinners run
for the first 3 seconds of an otherwise identical measurement:

| flips | ms/frame |
| --- | --- |
| 0-9 | 34.7 |
| 30-39 | 36.5 |
| 50-59 | 24.3 |
| 60-69 | **20.0** |
| 100+ | 19.5 |

Elevated while the load lasts, decaying, then settling on exactly the same floor
the clean run reaches. That is the reported signature: **the steady state is the
same, only the approach differs**, and blocks entered per frame stay flat
throughout, because the guest really is doing the same work either way. The
original run's steady state, 21 ms, is today's steady state. Only its early
buckets were inflated.

**And the control could not have revealed it.** `--no-native-flip` was flat at
28.5 ms then and now - but the guest's flip spins on the retrace port until the
wall clock says the period has passed, so that path is wall-clock-bound and
absorbs host contention without the number moving. The native flip is CPU-bound
and shows contention directly. The original note flagged 28.5 ms as suspiciously
exactly two retrace periods and warned the control might be masking variation;
that warning was right, and the thing it was masking was the state of the host.

## What is still open

The observation that started this: *while playing*, the between-level menu is
visibly slow for a moment and then settles. That is a claim about the interactive
process, and nothing here measures it. The headless replay of a capture of that
moment is flat, which means either the effect is not in the guest state at all -
it is in the window, the mixer, the compositor, or whatever else the interactive
process is doing in its first seconds - or it was the same host contention seen
from the other side.

To settle it, the interactive process has to report its own per-flip times; a
snapshot cannot carry this, because whatever causes it is not part of the machine
being captured.

## The lesson

Recorded in [verification-lessons](verification-lessons.md) as well, because it is
that note's point arriving from a new direction: an instrument can be perfectly
correct and still measure the wrong thing, when the quantity it reports depends on
something outside the experiment. **A wall-time measurement is only a measurement
of the program if the host is controlled**, and the way to notice is that the
comparison chosen as the control was immune to exactly the variable that was
loose. Check the machine is idle, and prefer a marker that is not wall time -
blocks entered per frame was flat all along and was saying so.
