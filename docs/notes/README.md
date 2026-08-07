# Notes

Current, and kept current. Where these disagree with
[`../sessions/`](../sessions/), these win.

- [drawing-port-goal](drawing-port-goal.md) — why the drawing port exists, the
  four plane loops, and what "progress" means when the speedup is not the point
- [port-io](port-io.md) — what is left on the wire: the retrace spin, then the
  palette fade, and 81% of traffic removed for no measurable speedup
- [open-readme-crash](open-readme-crash.md) — the one open bug: the readme runs
  out of stack, and every symptom after that follows mechanically
- [open-dgroup-initialisers](open-dgroup-initialisers.md) — bare declarations
  over initialised data: `particle_colours` was one, and seven more need reading
- [control-socket](control-socket.md) — drive a running machine over a Unix
  socket: press keys, capture, ask where it is
- [flip-transient](flip-transient.md) — the "first 120 frames run 4x slow" was
  load on the measuring machine; what is left of it, and why the control hid it
- [address-spaces](address-spaces.md) — image, linear and segment:offset,
  and the two ways they got confused into wrong addresses
- [entry-points](entry-points.md) — the chain from the C runtime: main at
  0x144d7, game_main tentatively 0x0c156, and the linear/image address trap
- [menu-loop](menu-loop.md) — `0x1271b`: the menu and the game are one loop, and
  the menu tree as driven from the keyboard
- [homecoming-sequence](homecoming-sequence.md) — the five screens after the game
  call: the ending, gated on finishing the last episode
- [episode-index](episode-index.md) — the egg's directory: episode names and
  readme titles, every string shifted by one
- [open-episode-index](open-episode-index.md) — the 303-slice index built
  from MAIN.EGG at startup, worth extracting; and why `snap` cannot capture there
- [open-game-speed](open-game-speed.md) — the game's `[<]`/`[>]` speed control
  was never reimplemented; the native flip drops the delay it worked through
- [open-function-attribution](open-function-attribution.md) — find_function_start
  reports show_splash as part of its neighbour, and nothing catches it
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
