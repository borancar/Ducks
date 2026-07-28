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

## The 23-byte entries are loaded files, not episodes

That is what made the count confusing: `[0x20ad]` is **1**, because one egg file
is open. Entry 0, read live, with `+0x0c` giving the game away:

```
+0x00  dword  19a5:2e40   far pointer into DGROUP - per-file state
+0x04  dword  2277:0004   far pointer to the file name, "MAIN.EGG", unshifted
+0x08  word   0x0004
+0x0a  word   0x33b4
+0x0c  word   0x012f = 303    the slice count the banner printed
+0x0e  word   0x084b
+0x10  byte   written by the loop from the information block
+0x11  word   0x0006
+0x13  byte   format version - the loop rejects anything outside 4..6
+0x14  word   non-zero, so this file contributes to the [0x20c2] total
+0x16  byte
```

So the loop walks *open egg files*, fetches each one's information block, checks
the version, and tallies how many episodes it holds.

## The episode index itself

`[0x20c2]` came out as **4**, so `[0x20c2] * 0x0e` allocated four 14-byte records
at `33a4:0004`. Read live and cross-checked against the names they point to:

| record | name | first | last | ordinal | flag |
| --- | --- | --- | --- | --- | --- |
| 0 | `TRAINING LEVELS` | 1 | 10 | 0 | 0 |
| 1 | `SHALLOW END` | 11 | 30 | 1 | 0 |
| 2 | `SO FAR SO GOOD?` | 31 | 50 | 2 | 0 |
| 3 | `DUCKING HELL` | 51 | 80 | 3 | 1 |

```
+0x00  dword  far pointer to the name, decoded - "TRAINING LEVELS" plainly
+0x04  word   first level  (0x06 is always zero, so this may be a long)
+0x08  word   last level
+0x0a  word   episode ordinal
+0x0c  word   flag, set only on the last episode
```

**Eighty levels in four episodes.** That also answers how the 23-byte entries
relate to the shifted string block: they do not. The shifted strings live in the
egg's information block, and the names the second array points at are **already
decoded** — something copies them out and subtracts the one on the way.

The remaining unknown is small: which routine does that copy, and what the flag
at `+0x0c` means. `1` on `DUCKING HELL` alone is consistent with "last", and with
a shareware lock, and nothing here distinguishes them.

## Reproducing it

The block was pulled with `read <addr> <len>` over the control socket in 1 KB
requests, then every byte decremented and scanned for a length byte followed by
that many printable characters. The false positives at the tail of such a scan
(`"DMS`, `"TMQDFHR`) are the heuristic finding structure in binary, not more
names.
