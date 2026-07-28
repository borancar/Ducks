# Open bug: navigating the in-game readme runs out of stack

**Open as of 2026-07-27, mechanism found 2026-07-28.** Navigating the in-game
readme crashes:

```
[cpu] Invalid instruction (UC_ERR_INSN_INVALID) at 19a5:210a
```

## It now reproduces in two seconds, with nobody playing

`snapshots/readme-before-crash.snap` is the readme at DUCKS OVERVIEW page 3 of 3,
whose footer offers only `UP: Last Page` and `ESC: Done`. Pressing **Down** there
is the input that breaks it. With the control socket:

```sh
venv/bin/python replay.py snapshots/readme-before-crash.snap --frames 300 \
      --control-socket /tmp/ducks.sock &
printf 'key down\n' | nc -U /tmp/ducks.sock
```

Six frames later it is dead. See [control-socket](control-socket.md).

## No native is responsible

The bisect this note asked for, one reproduction each, all crashing at the same
address:

| off | result |
| --- | --- |
| nothing (the control) | CRASHED at 19a5:1d82 |
| each of `--no-native-file`, `-keyboard`, `-mouse`, `-sound`, `-xms`, `-setup`, `-fp`, `-plane-loop`, `-flip` | CRASHED at 19a5:1d82 |
| `--no-blaster`, `--no-sound-bank` | CRASHED at 19a5:1d82 |
| **all of them off at once** | CRASHED at 19a5:1d82 |

Unanimous, so the fault is below the native port entirely.

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

## What is left

**Why is the stack exhausted?** 2.2 KB is what the program has: the MZ header asks
for `1c72:0080`, relocated to `1d82:0080`, and the runtime moves SP up to about
`0x08a2` at startup. The call chain that runs out is not recursive - it is an
ordinary nest of four or five calls that simply starts too deep.

The question to answer next is whether it starts too deep *here*. If Borland's
startup sizes the stack from what DOS reports free, and our shim reports something
different from a real DOS, the stack would be short by construction and this crash
would be ours after all - which the flag bisect cannot see, because every
configuration shares the same shim underneath. Compare the memory this machine
grants at startup against what the header asks for, and find what sets SP to
`0x08a2`.

## Instruments available

`crash_report()` on any fault; the wild-jump trap; the iret guard;
`--trace-blocks` for the last 24 basic blocks. `block_ring` is a plain deque, so a
one-off script can widen it - and at six frames a full `UC_HOOK_CODE` ring is
affordable, which is what produced the table above.

The high-water trick is worth keeping: in a stable main loop the stack's deepest
unwind happens on the first iteration and is never exceeded, so **every later rise
in max SP is an anomaly**, and there were exactly two in the whole run.
