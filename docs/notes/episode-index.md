# The episode index, and the egg's obfuscated strings

**Read out 2026-07-28**, from a live machine over the control socket rather than
from a snapshot — this happens in text mode, where no capture can be taken
([open-episode-index](open-episode-index.md)).

## Finding it

The console brackets the work, which is what made it findable: `Building episode
index...` is printed before, `MAIN.EGG: Full Version` after. That string is at
`DGROUP+0x25b1` and is pushed at image `0x1167f`, inside **`0x11657`** — the
routine that prints the banner and then walks the index.

## The strings are shifted by one

Every string in the egg's directory has each byte incremented. `USBJOJOH!MFWFMT`
is `TRAINING LEVELS`; `21` is a space. Subtracting one is the entire cipher.
They are length-prefixed: one byte of length, then that many shifted characters.

Read live from `2254:0000`, the block behind the index pointer:

| offset | len | decoded |
| --- | --- | --- |
| `+0x0029` | 15 | `TRAINING LEVELS` |
| `+0x003c` | 11 | `SHALLOW END` |
| `+0x004b` | 15 | `SO FAR SO GOOD?` |
| `+0x005e` | 12 | `DUCKING HELL` |
| `+0x006e` | 12 | `Full Version` |
| `+0x007c` | 39 | `Tim Furnish / Hungry Software 1998-2000` |
| `+0x00b2` | 14 | `DUCKS OVERVIEW` |
| `+0x00c4` | 11 | `THE OBJECTS` |
| `+0x00d3` | 15 | `HOW TO REGISTER` |
| `+0x00e6` | 6 | `THANKS` |
| `+0x00f0` | 18 | `DUCKS EDITOR SUITE` |

So the four episodes, the version label and credit the startup screen shows, and
the five readme page titles all live in one directory block. `DUCKS OVERVIEW` is
the page the [readme crash](open-readme-crash.md) happens on.

## What the loop does

```
0x11669  mov [0x20c2], 0            ; total, accumulated below
0x11681  lcall 0, 0x2012            ; print "Building episode index..."
0x11699  xor si, si
0x1169e: ...                        ; body, per entry
0x117a3  cmp si, [0x20ad]           ; the entry count
0x117a9  jmp 0x1169e
0x117ac  ax = [0x20c2] * 0x0e
0x117b5  lcall 0, 0x13f2            ; allocate, result -> [0x20ba]/[0x20bc]
```

| variable | meaning |
| --- | --- |
| `[0x20a9]` | far pointer to the entry array — `2254:0004` in the run read here |
| `[0x20ad]` | number of entries the loop walks |
| `[0x20ba]`/`[0x20bc]` | far pointer to a second array, allocated after the loop |
| `[0x20c2]` | entries counted during the walk; `x 0x0e` sizes the second array |
| `[0x20c4]` | a second running total |
| `[0x20c6]`/`[0x20c8]` | the source being read; `lcall 0, 0x3791` pulls the next value from it |

Entries are indexed with `imul 0x17`, so the stride is **23 bytes**. Fields seen
used: `+4` and `+6` (words, printed together), `+0x10` (a byte, written from the
reader), `+0x14` (a word, and the entries where it is non-zero are the ones
counted into `[0x20c2]`). The second array's records are **14 bytes**.

**Not yet established:** how the 23-byte entries relate to the string block — the
strings begin at `+0x1a` from `2254:0000`, which is inside what would be the
second stride, so either the entries are variable-length or the names are
referenced rather than inline. Read the body at `0x116e4`-`0x11733` next; it was
skipped here.

## Reproducing it

The block was pulled with `read <addr> <len>` over the control socket in 1 KB
requests, then every byte decremented and scanned for a length byte followed by
that many printable characters. The false positives at the tail of such a scan
(`"DMS`, `"TMQDFHR`) are the heuristic finding structure in binary, not more
names.
