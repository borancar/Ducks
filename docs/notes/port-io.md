# What is left on the wire

`port_report()` runs at the end of every session and every replay: each port
touched, its share of all traffic, its rate, reads and writes kept apart. The
share is the useful column — a session is as long as someone played it, so counts
alone say little, but a port holding 94% of the traffic names itself as the next
thing to replace.

## Two eras, and the same 94%

`emulation.py` has always counted into `port_in`/`port_out`; nothing read them
until 2026-07-28.

**Before the native page flip**, `0x3da` was 94-95% of all port I/O: the retrace
spin, ~1836 reads per page flip, the guest waiting for hardware that does not
exist. Replacing the flip removed it.

**After it**, a 57-second played session showed 401,427 accesses and `0x3c9` —
DAC data — at **94.4%**, 379,056 writes. The same share had reappeared on a
different port.

`trace_ports.py` attributed 56,064 of 57,600 writes in a menu-to-level replay to
one site, `0x0b16f` inside the fade at `0x0b10b`:

```
0x0b15f  mov al, [si + 0x10e1]     ; the stored palette
0x0b165  imul word ptr [0x1798]    ; x the fade level
0x0b169  sar ax, 6                 ; / 64
0x0b16f  out dx, al                ; 768 of these per call
0x0b171  cmp si, 0x300 / jl
```

A whole 256-colour upload per call, each byte an emulated `OUT` landing in a
Python callback.

## Where the traffic is not

Steady play is nearly silent. Twenty frames of `level-start` is 61 accesses a
frame and **all of it the sound IRQ handler** at `0x1580c` — a DSP status poll
and two end-of-interrupts. The menu is 8 a frame of the same. The DAC flood is
entirely screen transitions.

## The fade native

`dac_loop_fade` replaces the loop, not the function: the loop is inline in a fade
state machine that also decides when to stop and what to do next, and none of
that is worth reimplementing. Same technique as the four plane loops — hook the
body head, run the remaining iterations, step IP to the exit.

`--no-native-dac` turns it off. Two smaller DAC loops in the same function
(`0x0b1c9`, `0x0b202`, 48 writes each) and one in `0x056d2` (768, and the 1,536
writes still visible in the report below) are left alone: under 3% of the DAC
traffic between them, and each needs its own handler.

**Verified 44,544 times, 0 mismatched.** That number is 58 uploads x 768, because
in verify mode the real loop runs and re-enters the hooked head on every
iteration — so the native was checked from *every* possible starting `SI`, not
just from zero. The harness compares all 256 palette entries, the write index, a
partial latch, the converted-colour count and the three registers the loop
leaves behind; a native with the arithmetic right and `SI` wrong would still
fail.

## The result, stated honestly

200 frames from `main-menu-unhovered`:

| | without | with |
| --- | --- | --- |
| all port I/O | 68,861 | **13,334** |
| `0x3c9` | 57,600 | 1,536 |
| `0x3da` share | 13.3% | 72.5% |
| wall clock, best of 3 | 15.35s | 15.28s |

**81% of all port traffic is gone, and the run is not measurably faster.** Three
alternating pairs gave 15.28/16.03/15.89 against 15.35/17.66/16.21 — the spread
between runs is larger than the difference between configurations. 56,064 writes
cost on the order of 1% of a run that is dominated by drawing, and the native
itself takes 9.3 ms across 73 calls.

That is worth having anyway, on this project's own terms: reach is the goal, not
speedup ([drawing-port-goal](drawing-port-goal.md)), and it makes the remaining
traffic legible. But it is not a speedup, and the timing was measured three times
alternating rather than once because of [flip-transient](flip-transient.md).

## 0x3da, chased: it is one routine, and it is CGA snow avoidance

A static scan for `mov dx, 0x3da` (`ba da 03`) finds **three sites in the whole
image**, which is a complete answer where sampling a run is not:

| site | in | what |
| --- | --- | --- |
| `0x01dc3` | `0x01d8e` | the snow-avoidance blit |
| `0x04d66` | `0x04d4b` | page_flip waiting for display enable |
| `0x04daf` | `0x04d4b` | page_flip waiting for retrace |

Two are inside the function the native flip already replaces. Everything left is
`0x01d8e`, a **word-wise memmove** with a flag that selects a CGA-safe path:

```
0x01dbf  rep movsw                 ; flag clear: the fast path
...
0x01dce  cli
0x01dcf  in al, dx / ror al,1 / jb   ; wait for bit 0 to fall
0x01dd4  in al, dx / ror al,1 / jae  ; wait for it to rise
0x01dd9  movsw                     ; copy ONE word
0x01dda  sti
0x01ddb  loop 0x1dce
```

**Two port reads and a cli/sti per word copied**, and the `ES == DS` branch at
`0x01ddf` splits it into `lodsw`/`stosw` with two waits each — *four* reads per
word. This is the 1980s technique for avoiding CGA snow, which no VGA needs.

Traced from program start rather than from a snapshot: all 1,047 reads in a
400-frame boot land in the first 20 frames, split 524/523 between `0x01dcf` and
`0x01dd4` — the two-reads-per-word path, ~523 words. Every snapshot in the
library is captured after this, which is why replaying them only ever showed
page_flip.

That also explains the number that started this: two different played sessions
each reported **exactly 9,475** reads of `0x3da`. An identical count across
different play is not a coincidence, it is a deterministic startup path.

**A note on measuring it.** `replay.py` forces `--flip-hz 0` and `trace_ports.py`
does not, and an unpaced retrace spin burns far more reads waiting for the same
wall-clock retrace — 9,156 against 576 on the same snapshot and frame count. The
port is a poll against a clock, so its count is a function of pacing, not only of
the guest. Compare like with like.

## 0x3c9, finished: four loops, and one that no play-through can reach

A static scan for `mov dx, 0x3c8` finds ten sites; attribution puts every
remaining data write in three more loops, all the same shape as the fade —
zero-extend a stored byte, shift right by two for a 6-bit DAC value, write it —
differing only in source and range:

| loop | source | range | what |
| --- | --- | --- | --- |
| `0x056e0` | `[si+0x10e1]` | 0 → 0x300 | a full palette, unscaled |
| `0x0b1c9` | `[si+0x0dad]` | 0 → 0x30 | 16 colours at DAC index 0x40 |
| `0x0b202` | `[si+0x10e1]` | 0xc0 → 0xf0 | the same 16, from the other palette |

So one parameterised handler rather than three near-copies. `sar ax, 2` is
arithmetic, but AH is zeroed first and the byte is unsigned, so no sign case
exists here — unlike the fade, where the level is a variable and the general form
was worth writing.

With all four in, **`0x3c9` disappears from the port table completely**; only the
75 index writes to `0x3c8` remain, which are single `out`s outside any loop.

### The two that could not be reached

`0x0b1c9` and `0x0b202` are a blink: they alternate 16 colours at DAC index 0x40
between two palettes on a random 2-to-260-frame interval. They sit behind
`[0x2157] != 0`, and **no play-through reaches them.** None of the eight
snapshots in the library hit either; a 419-second session across menus, levels
and bonus screens hit neither; and all five captures taken during it read
`[0x2157] = 0`.

Chasing the flag explains why. `[0x2157]` is only ever assigned from `[0x201e]`
(at `0x0d854`), and the only write to `[0x201e]` in the image stores **zero** (at
`0x0932f`). The branch is dead in this build.

Unreachable is not verified — `0 mismatches` on code that never ran means
nothing. The documented answer for a state that cannot be produced on demand is
to drive the guest's own code on synthetic input, as `test_retire.py` does. So:
restore a level, force `[0x201e]` and `[0x2157]` to 1 and the countdown to 0, and
let the ordinary harness run. Forcing the flag cannot make the comparison pass
falsely — it is still native-against-original on the same call — it can only make
it happen at all.

**2,352 calls to `0x0b1c9`, 2,304 to `0x0b202`, 4,656 comparisons, 0 mismatched.**

### Pencilled for later: it is not a blink, and something else is

Called a "blink" on first reading. It is not. Dumping both sets shows `0x0b202`
writes the level's **normal** ramp and `0x0b1c9` writes a washed copy of the same
ramp, built once at `0x0876a`:

```
v   = [0x14b1 + i]                the level's own ramp
a   = (v >> 1) + (v >> 2) + 0x40  contrast x 0.75, lifted by 64
out = min(a * (gamma + 6) / 19, 255)
```

`(gamma + 6) / 19` is the same factor the normal palette gets, so stripped of
gamma the alternate is simply `v * 0.75 + 64` — dark entries lifted to a grey
floor, bright ones unchanged. These are the **terrain** colours, so the whole
scene would lift at once. A full-screen wash, not a local shimmer.

The colours themselves, for three levels:
<https://claude.ai/code/artifact/eac90152-cb72-4f51-8353-0583d3688cae>

**The open thread.** Chasing this started from a hunch that the cave captures were
about teleporting. Six captures across two sessions all read `[0x2157] = 0`, and
the loops have never executed — so whatever a teleporter does on screen, it is
not these. Two candidates, and the second is cheap to rule out first:

- a sprite or tile animation, no palette involvement at all;
- the fade at `0x0b15f` being driven rapidly, which would show as a burst of
  `dac_loop 0x0b15f` calls against a small frame count in the exit report.

The second already looks unlikely. A 94-second session played from
`teleporter-level.snap` recorded **58** calls to `0x0b15f` and 2 to `0x056e0` — the handful of
ordinary screen fades you would expect, not a burst — and zero to either blink
loop. So if something visible happened on that screen, the palette was barely
touched while it did, which points at the first candidate.

**And a caveat on the deadness.** "`[0x201e]` is only ever written zero" comes
from a static scan for the two-byte address literal, which would miss a write
made through a pointer. Six captures reading zero corroborates it; it is not
proof of a negative.

## 0x3da, removed: the waits are NOPed out

Not replaced natively — **deleted from the guest**. `--snow-nops` (on by default)
overwrites all three ten-byte wait blocks with `0x90`. The copy itself stays the
guest's own `movsw`, so the direction flag, the overlap handling and the count
are untouched; only the waiting disappears.

The whole ten bytes go, not just the `in`: leaving `ror al, 1` and its
conditional jump behind would spin on a stale `AL`. Each site is verified byte
for byte before it is written, because these addresses hold compressed data until
the game is unpacked — a site that does not match is skipped and reported, the
same way the interrupt stubs handle it.

`page_flip`'s own two reads are deliberately left alone. They are inside the
function `--native-flip` replaces so they never execute, and NOPing the retrace
wait would silently unpace the guest's own flip — which is the whole point of the
`--no-native-flip` control.

**Checked on what it copies, not on what it counts.** A patch to a routine that
moves memory has to be judged on the memory. Hooking the blit's entry to decode
its arguments and its exit to compare the destination against a Python memmove,
over a 400-frame boot:

| | calls | words | mismatched | 0x3da reads |
| --- | --- | --- | --- | --- |
| `--no-snow-nops` | 523 | 523 | 0 | 1,047 |
| default | 523 | 523 | **0** | **0** |

Identical work, identical results, no traffic. (523 calls copying one word each —
the caller invokes it per word, which is why two reads per word mattered.)

### A restore used to undo it

Restoring any snapshot captured before the patch brought the original bytes back,
so `replay.py --snow-nops` on an existing capture quietly ran without it. Which
way to fix that is settled by what [testing-from-snapshots](testing-from-snapshots.md)
already claims: hooks and natives are *deliberately* not captured, "which come
from `build_machine()` and the flags — that is what lets a capture made with
everything on be replayed with one piece off". A guest-memory patch is
configuration in exactly that sense.

So `snapshot.restore()` now calls `m.after_restore()` if the machine has one, and
`Native.after_restore()` re-applies. Verified both ways: default is NOPs after a
restore of a pre-patch capture, `--no-snow-nops` is the original bytes. Anything
else that is configuration-rather-than-state can hang off the same seam.

This is a startup cost of about half a second, not a per-frame one, so it was
worth doing for tidiness rather than for speed — said out loud rather than
discovered later.

## Reading the report

The line `N palette byte(s) did NOT reach port 0x3c9` is there so that absence is
not mistaken for the game having stopped fading — with the native on, that traffic
is missing by design.
