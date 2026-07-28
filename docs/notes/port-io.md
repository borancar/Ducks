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
