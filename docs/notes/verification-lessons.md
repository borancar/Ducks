# Prove the instrument before reading its shape

Three times in one session, confident structural conclusions were drawn from
measurements the instrumentation was producing wrongly:

- `--trace-calls` read the caller's return address *after* `_on_native` had
  already advanced SP, so caller attribution was garbage — negative offsets
  pointing into the interrupt vector table. The callers were reported before
  anyone noticed.
- The verify harness ignored the handler's return value, counting every `DECLINE`
  as a mismatch (463 of them). Those counts were then used to argue an entity type
  was mis-decoded, and the native was restricted to two types. All types were
  correct.
- A debug log flag stayed set past the call being inspected, so blits from later
  unrelated calls appeared in the window. The conclusion drawn was that the
  original emitted extra draws per entity. It did not.

**Why it happens:** in each case the numbers had a plausible *shape*, and the
reasoning went from shape to conclusion without first asking whether the tool was
measuring the intended thing. Broken instrumentation produces coherent-looking
wrong answers, which is exactly what makes it dangerous.

A fourth, from the other direction: the page-flip "transient" was measured by an
instrument that was **working correctly**, and the conclusion was still wrong. The
quantity it reported — wall time per frame — depends on the host as much as on the
program, and the host was not idle. See
[flip-transient](flip-transient.md): the check that would have caught it was
already in hand, because blocks entered per frame stayed flat the whole time.
**A wall-clock number is a measurement of the program only if the machine is
controlled**, and a control that is immune to the loose variable — there, a path
that spins until the retrace, so contention never moves it — confirms nothing.

**How to apply.** Before inferring anything from new instrumentation, prove it
measures what it claims on a case whose answer is already known. When a
measurement contradicts a careful reading of the disassembly, suspect the
measurement first — here the disassembly was right every time. And say
"unverified" rather than "verified" for a path with zero recorded executions:
`0 mismatches` on code that never ran means nothing.

## A verification can compare a native against itself

**2026-08-03.** `--verify` runs the native, restores, lets *the original body*
run, and compares. But the original body is guest code, and guest code calls
other routines — some of which are natives too. When it does, the dispatcher
services them, and that side of the comparison is Python as well.

`draw_entities` compared clean over **9,864 calls with its sprite outline writing
0xff instead of 0**. The outline is reached two ways: inline from
`draw_entities`, and as the native for `0x065f1` that the guest's own body calls
— one implementation behind both. Sabotage it and both sides move together.
Removing an entire outline direction was equally invisible. Nothing in the output
suggested the comparison was hollow; it reported 2.6 GB of planes compared.

Handing the inner routine back with `--skip-natives outline_sprite` makes the
guest run its own code, and then the same sabotage reports
`native=0xff real=0x00` immediately — and unmodified, 7,728 calls compare clean.
That is the first real verification the outline has ever had.

So `--verify` now records which natives ran while a body was being replayed and
says so. It is not proof of circularity — the sound mixer fires from an interrupt
inside that window without being part of the body — but it names what to check.
Across the four demo captures it flags `draw_entities` ← `draw_sprite`,
`outline_sprite`; `draw_number` ← `draw_sprite`; `mouse_motion` ← `int86`. All
of those had been reading as verified.

`test_gameplay.py` never had this problem: it unhooks the whole native table
around the guest call, which is the same fix made unconditional.

**How to apply.** A leaf native — one whose original body calls nothing that is
itself native — is safely compared. Anything above a leaf is not, until the
natives underneath it are skipped. Check the SHADOWED line before believing a
clean run.

## A decline is a coverage hole, not an answer

**2026-08-03.** DECLINE lets a native handle only the cases it has been proven
on, and the count is reported, which is right. What is easy to forget is that
the *decision* to decline is itself unchecked: `compare()` restores memory and
records "declined" without ever asking what the guest would have done.

`tool_use` showed it. Its inlined ground check scans down from y + 1, and
starting from y instead changes nothing any comparison can see - the scan's only
observable effect is whether the native declines. Three other sabotages of the
same routine were caught immediately. So a routine can be well covered on the
paths it takes and completely unexamined on the boundary that decides which
paths those are.

**How to apply.** When a native declines on a computed condition rather than a
constant one, that condition needs checking some other way - or say plainly that
it is not checked. Declining on "this tool is not one I do" is safe; declining on
"I scanned more than 28 rows" is a claim.

## The port's heap is not the original's, and ASan is the instrument

**2026-08-07.** Reported: "double free or corruption (out)" on quitting and
restarting a level, and separately that some levels' sound was broken. **One bug,
both symptoms**, and ASan named it in one run where an afternoon of reading had
not.

`egg_fread` took `int16_t` counts. Borland's `fread` takes `size_t`, which is
*unsigned* 16-bit, and one caller can exceed 32767: a sound. Nine samples in this
egg do - `0x4b`, level 3's ambience, is 45,818 bytes. So the length arrived
negative, the product became astronomical, `cursor + want` wrapped past the guard,
and the copy either ran wild or clamped to "the rest of the egg" and poured 1.7 MB
into a 45 KB buffer. That is why the audio was noise - the samples loaded *after* a
big one were overwritten with file bytes - and why the first `free` after a restart
found a mangled chunk header.

**A signedness error in a signature, a long way from where it hurt.**

ASan then found two more, both of the same different kind: code that is
out-of-bounds in the original too, and harmless there only because DOS gave it one
flat heap.

- `bridge_step_end` tests its bounds once and then steps up to four pixels, so a
  bridge running off the edge probes past the row, or past the row table.
- `level_load` writes the hero's facing to `entities[flag]` behind a test of
  `flag != 0`, but the "no hero" sentinel is `0xff`. Level 4 has no hero, so the
  original writes `entities[255]` of a sixteen-entry array.

Both are now bounded, and both say in place that this is a **choice rather than a
match**: what the original read or wrote there is a property of its heap and
cannot be reproduced, only decided. `terrain_at` had already set that precedent.

The port's `alloc_image` gives every row its own `calloc` where the original had
one block, which is what turns "a few pixels past a row" from invisible into a
corrupted malloc header. That makes the port *stricter* than the original, which
is worth having: it finds the latent bugs. But it means a faithful transcription
can crash where the original did not, and the fix is to bound the access and say
so - not to widen the allocation and keep the bug.

**How to apply.** `make -C reconstruct asan && ./ducks-asan`. It costs nothing
until something is wrong, and "corruption at the next free" is exactly the class
of bug that reading cannot find, because the report names a victim and never the
culprit.

## Verification that works here

- `--verify --verify-only <names>` byte-compares a native against the original
  body on every call: it runs the native into a snapshot, restores, lets the
  original body run, and compares on return.
- For game states that cannot be produced on demand, drive the guest's own code on
  synthetic input instead — `test_retire.py` is the worked example.
- For anything derived from static analysis, pin it to known answers in a test.
  `test_fn_start.py` does this for function-boundary attribution, which had been
  failing silently on large functions.

See [drawing-port-goal](drawing-port-goal.md) and
[running-a-session](running-a-session.md).
