# Notes

Current, and kept current. Where these disagree with
[`../sessions/`](../sessions/), these win.

- [drawing-port-goal](drawing-port-goal.md) — why the drawing port exists, the
  four plane loops, and what "progress" means when the speedup is not the point
- [open-readme-crash](open-readme-crash.md) — the one open bug: the readme runs
  out of stack, and every symptom after that follows mechanically
- [control-socket](control-socket.md) — drive a running machine over a Unix
  socket: press keys, capture, ask where it is
- [flip-transient](flip-transient.md) — the "first 120 frames run 4x slow" was
  load on the measuring machine; what is left of it, and why the control hid it
- [verification-lessons](verification-lessons.md) — broken instrumentation gives
  coherent wrong answers; prove the tool before reading its shape
- [accepting-is-not-answering](accepting-is-not-answering.md) — an ignored DOS
  call returns stale registers, and that came out as text in the wrong columns
- [running-a-session](running-a-session.md) — how to launch, what a healthy
  startup prints, and why `mode=0x03` means nothing was tested
- [testing-from-snapshots](testing-from-snapshots.md) — capture a state once
  instead of playing to it every time; all four plane loops verified from one
- [editing-conventions](editing-conventions.md) — the three layers, why behaviour
  goes in `native.py`, and the anchored one-shot edit scripts
