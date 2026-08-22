# Open: the program runs out of stack, and the readme is how to see it

**Opened 2026-07-27 as "the readme crash". Mechanism found 2026-07-28. Rewritten
2026-08-22**, when the reconstruction turned out not to have it and the readme
turned out not to be the cause. The file keeps its name because every link to it
does, including two in [`../sessions/`](../sessions/), which are not maintained.

**The question is now:** the program has about 2.2 KB of stack and an ordinary
nest of four or five calls exhausts it. Is that what a real DOS would have given
it, or does our shim report something that makes Borland's startup size the stack
short? Everything below is evidence toward that, and the readme is the cheapest
way to see it happen.

## It reproduces in two seconds, with nobody playing

`snapshots/readme-before-crash.snap` is the readme at DUCKS OVERVIEW page 3 of 3,
whose footer offers only `UP: Last Page` and `ESC: Done`. Pressing **Down** there
is the input that breaks it. With the control socket:

```sh
venv/bin/python replay.py snapshots/readme-before-crash.snap --frames 300 \
      --control-socket /tmp/ducks.sock &
printf 'key down\n' | nc -U /tmp/ducks.sock
```

Six frames later it is dead:

```
[cpu] Invalid instruction (UC_ERR_INSN_INVALID) at 19a5:210a
```

See [control-socket](control-socket.md).

## No native is responsible

The bisect, one reproduction each, all crashing at the same address:

| off | result |
| --- | --- |
| nothing (the control) | CRASHED at 19a5:1d82 |
| each of `--no-native-file`, `-keyboard`, `-mouse`, `-sound`, `-xms`, `-setup`, `-fp`, `-plane-loop`, `-flip` | CRASHED at 19a5:1d82 |
| `--no-blaster`, `--no-sound-bank` | CRASHED at 19a5:1d82 |
| **all of them off at once** | CRASHED at 19a5:1d82 |

Unanimous, so the fault is below the native port entirely — which is also why the
bisect cannot see the shim: every configuration shares it.

## What actually happens

**The stack runs out.** SP's high-water mark across the whole run is `0x08a2`, so
there are about 2.2 KB below the stack's base. Handling Down walks all the way
down it. Per-instruction, with SS = `0x1d82` throughout:

```
  0x0028  0110:34b8  image 0x034b8   call 0x4879       <- 40 bytes left
  0x001e  0110:3787  image 0x03787   call 0x4891
  0x0012  0110:37f3  image 0x037f3   call 0x4801
  0x0000  0110:3733  image 0x03733   call 0x502a       <- exhausted
  0xfff4  0110:3f60  image 0x03f60   push [bp + 0xc]   <- wrapped past zero
  ...
  0xfffe  0110:3ffa  image 0x03ffa   retf
  0x0002  0064:3736  image 0x02c76   ret 8             <- CS is wrong
```

That `retf` gets the right IP (`0x3736`) and the wrong CS: **`0x0064` instead of
`0x0110`**. The word holding the return CS sits at the very top of the segment,
where SP wrapped, so it did not survive. From there nothing is recoverable, and
the rest is mechanical:

1. `ret 8` executes at `0064:3736`. The linear address happens to land on real
   code, which is why the block trace shows a plausible-looking `ret 8` in
   `0x02c36` - but CS is already garbage, so it returns to `0064:0005`.
2. That is linear `0x00645`, zero-filled memory below the image. `00 00` decodes
   as `add [bx+si], al`, so control crawls ~700 bytes of nothing.
3. At `0x000d0` the crawl reaches **the interrupt vector table**, where the ten
   Borland FP vectors (INT 34h-3Dh) all hold `16cf:2152`. As code, `52 21 cf 16`
   is `push dx; and di, cx; push ss` - so it pushes DX and SS ten times.
4. At `0x000fa` it hits the byte `cf` inside the INT 3Eh vector. **`0xCF` is
   `iret`.** It pops the values just pushed: IP <- SS = `0x1d82`, CS <- DX =
   `0x19a5`, FLAGS <- SS = `0x1d82`, which has **TF and DF set**.
5. Single-stepping, it crawls ~900 bytes of DGROUP BSS raising 440 INT 01h traps,
   and dies on an invalid opcode at DGROUP+0x210a.

So every reported symptom is accounted for, including the two that looked most
mysterious: the alternating `19a5`/`1d82` filling the stack are DX and SS pushed
by the vector table, and TF is set because an `iret` restored a flags word that
was really a segment register.

**And that is why the iret guard never fired.** The guard checks the two `iret`s
in the image. This one is not in the image - it is a byte inside a vector table
entry, at linear `0x000fa`.

## It is not only the readme

**Seen 2026-07-31**, and this is now the load-bearing observation rather than a
footnote. A played session that never opened the readme ended the same way. It was
`native.py --load-snapshot` at the main menu, played through a level to a game
over and back to the menu, and it died with this signature:

```
[wild] control just left the code: executing 0x1b7d2 = DGROUP+0x1d82, which is data
[crash] CS:IP 19a5:1d82 -> image 0x1a6d2 = DGROUP+0x1d82 (DATA, not code)
[crash] SS:SP 1d82:ffe2 BP 19a5 FLAGS 1d82 [TF DF SF]
```

Same `19a5`/`1d82`, same `TF` arriving in a restored flags word, and **`SP` at
`0xffe2`** — wrapped past zero, which is the stack exhaustion this note
describes, not something that merely resembles it.

The last thing in the log before the wild jump is the sample loader: XMS handle
30 allocated and resized, sound `#112` played, then the jump. So it happens
during a load, on a path with nothing to do with the readme viewer.

Two unrelated paths running out of the same 2.2 KB is what says the stack is short
**for the program as a whole**, and points the investigation at what sets `SP` to
`0x08a2` rather than at any one caller.

Not yet known: the exact input, and whether this reproduces. The session was
being played by hand while breakpoints were armed for unrelated work, and no
snapshot was taken near the end — the capture that would make this a test does
not exist yet. Worth taking one at the next game over.

## The reconstruction does not have it, and that is not a fix

`reconstruct/` does not crash here. That is worth stating precisely, because it is
not a change anyone made and it does not close this note.

`show_readme_section` (`0x11efb`) has been touched by exactly two commits — the
one that created `game.c` and `ad44529`, which wrote the body — and neither was a
bug fix. The port has never crashed in it.

The reason is that the code which runs out of stack is not code the port contains.
Every frame in the trace above executes at **CS = `0x0110`**, which is the load
segment itself: the MZ header asks for `1c72:0080` and it is relocated to
`1d82:0080`, so the delta is `0x110` and image offset equals IP in that segment.
The game's own code segment starts at image `0x04ca0`, which at this load is CS
`0x5da`. So `0x034b8`, `0x03787`, `0x037f3`, `0x03733`, `0x03f60` and `0x03ffa`
are all outside the reconstructed segment — they are in one of the five segments
[reconstruction](reconstruction.md) puts deliberately out of scope. A port that
does not contain the runtime cannot inherit a bug in it.

**One address in that trace is not accounted for.** The last call before the stack
gives out is `call 0x502a`, and as an image offset `0x0502a` falls *inside* the
game's own segment, between `egg_read_string` (`0x04f4b`) and `close_egg_files`
(`0x051b7`). A near call from CS `0x0110` reaching game-segment bytes at a second
set of offsets is either a fact about how this binary is linked or a
misreading of the trace, and it is not settled which. The image is larger than
64 KB, so both addressings of those bytes are physically possible; that does not
make them both intended. See [address-spaces](address-spaces.md), which is about
exactly this class of mistake. Resolving `0x502a` is cheap and worth doing before
leaning on the paragraph above.

## Ruled out

- Every native (the table above).
- Both `iret`s in the image (`0x00eb4`, `0x15916`). The earlier conclusion that
  one of them was responsible came from popping `19a5:1d82` from below SP; those
  two words are SS and DS, the values this program pushes most often, so the match
  was coincidence. Refuted by instrument - see
  [verification-lessons](verification-lessons.md).
- Borland's `getvect`/`setvect` (`0x00f2a`/`0x00f39`), the previous lead. They are
  in the block trace only because the wreck crawls through `0x00f31`, mid-way
  through `mov al, [bp+6]`, where the bytes happen to decode as `push es; int
  0x21`. Hooking both and logging every call shows neither is called on this path.
- **Walking off the end of a record's page range.** This note carried that as its
  standing lead until 2026-08-22, on the strength of
  [episode-index](episode-index.md): `DUCKS OVERVIEW` is pages 1 to 3, the capture
  is on page 3, and Down is the step past `last`. **Nothing steps past `last`.**
  The viewer guards it, in the original as well as the port — `has_next` is
  `(readme_index[n].last != at)`, and Down with `has_next` clear plays the refusal
  sound instead of advancing. The footer at the capture offering only
  `UP: Last Page` and `ESC: Done` is that same flag, and is the direct evidence
  the guarded branch is the one taken. The range was never run off; it only
  determined which branch Down reached.

## A lead, and it is tentative

Both known reproductions pass through the sound path at the moment they die. The
readme one takes `sound_play_guarded(0x17, 1)` — the refusal — because that is
what Down does on the last page. The 2026-07-31 one had just played sound `#112`
off a freshly resized XMS handle.

That is two for two, and it is also two, which is not many. The mechanism here is
depth rather than any particular callee, so this may be nothing more than the
sound path being the deepest thing a menu does. Worth measuring rather than
believing: instrument SP at entry to `sound_play` and see whether it is routinely
close to the floor, or whether these two were unlucky.

## What is left

**Why is the stack exhausted?** 2.2 KB is what the program has: the MZ header asks
for `1c72:0080`, relocated to `1d82:0080`, and the runtime moves SP up to about
`0x08a2` at startup. The call chain that runs out is not recursive - it is an
ordinary nest of four or five calls that simply starts too deep.

The question to answer is whether it starts too deep *here*. If Borland's startup
sizes the stack from what DOS reports free, and our shim reports something
different from a real DOS, the stack would be short by construction and this crash
would be ours. The flag bisect cannot see that, because every configuration shares
the shim underneath.

In order:

1. **Find what sets `SP` to `0x08a2`**, and whether any DOS call feeds it. The
   startup interrupts are all hooked and answered in Python already — the memory
   ones are `INT 21h AH=4Ah` (the heap shrink) and the free-memory report — so
   what the guest is told is inspectable without any new instrument.
2. **Compare that against the header.** The MZ header's own stack request is a
   fixed number in the file; if the runtime ends up with less than the header
   asked for, the shim is the only thing in between.
3. **Resolve `call 0x502a`**, per the section above, before treating "the runtime
   is where this lives" as established.

If the shim turns out to be honest, this is Ducks' own bug, the port is unaffected
either way, and the note can close with the original named as the owner.

## Instruments available

`crash_report()` on any fault; the wild-jump trap; the iret guard;
`--trace-blocks` for the last 24 basic blocks. `block_ring` is a plain deque, so a
one-off script can widen it - and at six frames a full `UC_HOOK_CODE` ring is
affordable, which is what produced the table above.

The high-water trick is worth keeping: in a stable main loop the stack's deepest
unwind happens on the first iteration and is never exceeded, so **every later rise
in max SP is an anomaly**, and there were exactly two in the whole run.
