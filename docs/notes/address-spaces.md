# Three address spaces, and the two ways they were confused

Every wrong address recorded on 2026-07-28 was this, twice, in different clothes.
Worth writing down as a rule rather than as two anecdotes.

## The spaces

| space | example | what it is |
| --- | --- | --- |
| **file offset** | `0x0d256` | position in `Ducks.unpacked.exe`, including its header |
| **image offset** | `0x0c156` | position in the loaded image. **Everything in these notes and in `symbols.py` uses this.** |
| **linear** | `0x0d256` | what Unicorn addresses and what the socket prints: `image + image_base`, and `image_base` is `0x1100` at the usual load segment |
| **segment:offset** | `05da:74b6` | what CS:IP holds. Many of these map to one linear address, and **this is the space a near call wraps in** |

`image_base` is not a constant of the program - it is `load_seg * 16`, and
`unpack_ducks.py` deliberately runs the unpack at two different load segments to
find the relocations. Anything that hardcodes `0x1100` is assuming a load.

## Confusion 1: reading a linear address as an image offset

The control socket's `disasm` prints linear addresses, and so do capstone's
branch operands. `call 0xcaea` in that listing is linear, so the image offset is
`0xcaea - 0x1100 = 0x0b9ea` - a different function. Image `0x0caea` was very
nearly recorded as the game's main routine; image `0x0b9ea` is an eighteen-byte
setter.

**Rule: subtract `image_base` from anything the socket or Unicorn prints before
looking it up in a note.**

## Confusion 2: computing a near-call target in image space

`call rel16` is relative to the next instruction **within its 16-bit segment**,
and offsets wrap at `0x10000`. Image offsets are not segment offsets, so adding
the displacement to an image offset silently gives an address a whole 64 KB out
whenever the segment wraps.

`egg_find_block` was recorded at image `0x15232` on exactly this. That address is
*mid-instruction*, inside `mov word [bx+0x3c7e], 0` in `play_sample`'s voice
table. The function is at `0x05232`.

Doing it properly, for the call at image `0x116a4` with displacement `0x3b8b`,
executing with `CS = 0x05da`:

```
segment base, as an image offset = CS * 16 - image_base = 0x5da0 - 0x1100 = 0x4ca0
offset of the next instruction   = 0x116a7 - 0x4ca0            = 0xca07
target offset                    = (0xca07 + 0x3b8b) & 0xffff  = 0x0592
target image offset              = 0x4ca0 + 0x0592             = 0x05232
```

which is the right answer with no guessing. The `& 0xffff` is the whole point.

**Rule: a near-call target needs the segment it executes in. Without CS you
cannot resolve one, and the stack at runtime is the authority.**

## How they are caught now

`test_symbols.py` requires every `FUNCTIONS` entry to begin with
`push bp; mov bp, sp`, with three hand-checked exceptions. Both bad addresses
land mid-instruction, so the prologue check catches exactly this class. It was
proved by putting the wrong address back and watching it fail, rather than
trusting a green run.

A cheap independent check when CS is not to hand: compute the target in image
space, and if it has no prologue but `target - 0x10000` does, it wrapped. That
agreed with the runtime on all four targets it was tried against - `0x15232`,
`0x14de6`, `0x1537d` and `0x14d04`, all of which wrap - but it is a heuristic,
and the segment arithmetic above is not.
