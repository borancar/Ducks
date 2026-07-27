# Notes

Current, and kept current. Where these disagree with
[`../sessions/`](../sessions/), these win.

- [drawing-port-goal](drawing-port-goal.md) — why the drawing port exists, the
  four plane loops, and what "progress" means when the speedup is not the point
- [open-readme-crash](open-readme-crash.md) — the one open bug: navigating the
  in-game readme jumps into DGROUP data
- [open-flip-transient](open-flip-transient.md) — the first ~120 frames after a
  native flip run 4x slow, emulator-side; reproduces from a snapshot
- [verification-lessons](verification-lessons.md) — broken instrumentation gives
  coherent wrong answers; prove the tool before reading its shape
- [running-a-session](running-a-session.md) — how to launch, what a healthy
  startup prints, and why `mode=0x03` means nothing was tested
- [testing-from-snapshots](testing-from-snapshots.md) — capture a state once
  instead of playing to it every time; all four plane loops verified from one
- [editing-conventions](editing-conventions.md) — the three layers, why behaviour
  goes in `native.py`, and the anchored one-shot edit scripts
