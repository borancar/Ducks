# Running a session, and reading what it prints

From the repository root, after the unpacking step:

```sh
venv/bin/python native.py
```

Every native is on by default (each is a `BooleanOptionalAction`, so each has a
`--no-` form), and `--exe` already defaults to `Ducks.unpacked.exe`. The older
explicit spellings (`--native-sound`, `--blaster` and friends) still work, so
commands recorded in old logs remain valid.

The `--no-` forms are the way to test whether a native is responsible for
something — turn one off, reproduce, compare.

## The unpacked image is required

`--native-setup` writes one-shot interrupt stubs into the image. With the packed
original, the machine starts on the DIET stub and the game is still compressed
when the hooks go in, so every site fails verification and is skipped. The
`[ints] n/23` line reports this directly.

## A healthy startup prints ten lines

pygame, `[nsnd]`, `[bank]`, `[ints] 23/23`, `[xms]`, `[fp]`, four
`[loop] plane loop`, then `35 routine(s) serviced natively`. Fewer means a flag
was turned off, or something failed.

## `mode=0x03` in the `[stat]` lines means nothing was tested

The game sits at a text screen until a key is pressed. One 15-minute session
recorded zero comparisons for exactly this reason. `mode=0x13` is the sign it is
actually playing — check for it before believing a verification run that reports
no mismatches.

## Unattended runs

`--run-seconds N` bounds a session. Use a generous limit for anything a person is
playing: a short limit quits the game mid-play and is indistinguishable from a
crash. Short limits are for unattended measurement only. Run with `python -u` when
redirecting to a log, or nothing appears until exit — which is exactly when live
diagnosis is wanted.

## Instrumentation is off by default

Tracing and profiling hooks stay off unless asked for, and are toggleable at
runtime rather than launch-only, so a specific slow moment can be captured without
replaying to reach it: **F5** toggles, **F6** reports, and `touch trace.on` /
`trace.off` / `trace.report` do the same from a shell without stealing window
focus.

This matters because leftover diagnostic hooks — a per-write hook over all of
video memory, a sound-buffer watch — were still firing during performance testing
and muddied what was actually slow. When reporting on performance, say which hooks
are still live.

See [editing-conventions](editing-conventions.md) and
[verification-lessons](verification-lessons.md).
