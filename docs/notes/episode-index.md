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

## Two indexes, not one

The routine builds **two** arrays, allocates them the same way, and fills them in
two passes over the same information block. They share a record layout.

| | pointer | sized by | count | contents |
| --- | --- | --- | --- | --- |
| first | `[0x20ba]` = `33a4:0004` | `[0x20c2]` | 4 | episodes |
| second | `[0x20be]` = `33a8:0004` | `[0x20c4]` | 5 | readme sections |

Both are `count * 0x0e` bytes from the same allocator at `0x13f2`, and both fall
back to the same error call when it returns null.

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
+0x04  word   first level
+0x06  word   egg file index - game_main copies it to [0x94] and indexes
               egg_files by it (stride 0x17), so it is not the high word of a
               long. Zero throughout this build, which has one egg
+0x08  word   last level
+0x0a  word   episode ordinal
+0x0c  word   flag, set only on the last episode
```

## The readme index, and the crash

The second array, read the same way:

| record | name | first | last | ordinal | flag |
| --- | --- | --- | --- | --- | --- |
| 0 | `DUCKS OVERVIEW` | 1 | **3** | 0 | 0 |
| 1 | `THE OBJECTS` | 11 | 19 | 1 | 0 |
| 2 | `HOW TO REGISTER` | 21 | 21 | 2 | 0 |
| 3 | `THANKS` | 30 | 35 | 3 | 0 |
| 4 | `DUCKS EDITOR SUITE` | 100 | 100 | 4 | 1 |

Same fields, so `first`/`last` are page numbers here rather than level numbers,
and the flag is again set only on the last record.

**`DUCKS OVERVIEW` runs pages 1 to 3**, and
[open-readme-crash](open-readme-crash.md) is captured on "DUCKS OVERVIEW, Page 3
of 3" — the last page of the first section — pressing Down. So the crash is a
navigation step off the end of a record's page range, and this table is the thing
the range comes from. Whether that is *why* the stack is exhausted is not
established: the connection is that the input which breaks it is exactly the one
that runs past `last`, which is a lead worth following rather than a conclusion.

Note also that the ranges are not contiguous — 1-3, then 11-19, then 21 — so page
numbers are addresses into the egg, not positions in a list, and "next page" from
3 is not 4.

**Eighty levels in four episodes.** That also answers how the 23-byte entries
relate to the shifted string block: they do not. The shifted strings live in the
egg's information block, and the names the second array points at are **already
decoded** — something copies them out and subtracts the one on the way.

## The flag at `+0x0c` means "last"

**Settled 2026-07-29, by playing it.** The reading recorded here was that `1` on
`DUCKING HELL` alone is consistent with "last" *and* with a shareware lock, and
that nothing in the tables distinguishes them. Playing does: `SO FAR SO GOOD?` —
record 2, flag `0` — refuses to start without registering and says so, only in
the full version. A lock would have to be set on that record. It is not, so the
flag is the terminator: set on the last record of each array, which is why the
readme array carries it on `DUCKS EDITOR SUITE` and nowhere else.

That relocates the question rather than closing it, and narrows where to look.
The gate does not fall on an episode edge: reported from the game's own message,
it lands somewhere inside `SHALLOW END`, which is levels 11 to 30 — the exact
level is not pinned. So it cannot be a test on the episode ordinal, and no
per-episode field can express it. It is a **level-number threshold**, compared
against a registration state held outside the index; the 14-byte record has no
part in it. Worth noting that the machine these tables were read from prints
`MAIN.EGG: Full Version` and the gate still applies, so that label describes the
egg's contents and not whether the copy is registered.

The remaining unknown from the original read is smaller: which routine copies the
names out of the information block and subtracts the one on the way.

## The registration state is `[0x548]`

**Found 2026-07-29.** The paragraph above predicted "a level-number threshold
against a registration state held outside the index". Both halves are now
located, by scanning for every instruction touching the flag — eleven sites, and
the three writers name it.

`[0x548]` is non-zero when the copy is registered. `[0x542]`/`[0x544]` is a far
pointer to the owner's name, read out of the egg stream by `egg_read_byte` at
`0x13ef3` and freed through `0x00edb` when the flag is cleared. `init` prints the
pair:

```c
puts("Registered to: ");                          /* DGROUP+0x2865 */
set_text_colour([0x548] ? 0x0f : 0x8f);           /* 0x8f is bright/blinking */
printf("%s\r\n\r\n", [0x548] ? (char far *)[0x542] : "UNREGISTERED");
```

The threshold test is at `0x13841`, and it consults the flag two instructions
later:

```
mov al, [0x54a]          ; a level number
cmp ax, [0x2032]         ; the threshold
jge  skip
cmp  [0x548], 0          ; ... and only when unregistered
jne  skip
cmp  [0x1ffc], 0
jne  skip
call 0x09329
call egg_load_one(0xfc, 0x48, 0xff)
```

So a level number against a threshold, gated on registration, with the content
pulled from the egg — the shape this note predicted, found where it said to look.

**The number is 20, and the block is the refusal. Settled 2026-07-29** by
rendering the intro screen the game shows on its way in — drawn from inside
`egg_load_pass_0x48`, captured to a snapshot and re-rendered from the planes
offline. (First recorded here as `show_resource(0x4d, 5)`; the stack says
otherwise, see [entry-points](entry-points.md). The text below and the live read
of `[0x54a]` are unaffected by which call drew it.) It says so in plain text:

```
        Using data file MAIN.EGG (supplied with the game)
                Final build: 1 August 2000

           20 levels classed as shareware
           80 levels for registered version
```

`[0x54a]` reads **20** on a live machine, so it is the shareware *limit*, not a
level number as first recorded here; `[0x2032]` is the level being attempted, and
reads 0 before one is loaded. With those the right way round the gate reads:

```
mov al, [0x54a]      ; 20, the limit
cmp ax, [0x2032]     ; against the level being attempted
jge  skip            ; 20 >= level, so allowed
cmp  [0x548], 0      ; unregistered?
jne  skip
...                  ; the refusal
```

So the block runs when the level **exceeds** 20 on an unregistered copy — the
refusal, not the reminder this note first guessed at. That matches the boundary
observed in play: `SHALLOW END` is levels 11 to 30, and it stops part-way
through, at 20. `SO FAR SO GOOD?` starts at 31 and was never reachable.

`main` also consults the flag twice: registered copies **skip** the third splash
and the block of four `show_resource` calls. The extra intro screens are the
unregistered build's ([entry-points](entry-points.md)).

## Reproducing it

The block was pulled with `read <addr> <len>` over the control socket in 1 KB
requests, then every byte decremented and scanned for a length byte followed by
that many printable characters. The false positives at the tail of such a scan
(`"DMS`, `"TMQDFHR`) are the heuristic finding structure in binary, not more
names.
