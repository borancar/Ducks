# Open: bare declarations that should carry initialised data

**Opened 2026-08-07, after `particle_colours` turned out to be one of these.**

## What happened

`particle_colours` (`d+0x18c5`) is eight bytes of **initialised data**. Nothing
in the image ever writes it; the only reference anywhere is the read at
`0x07854`. The port declared it bare:

```c
uint8_t particle_colours[8];        /* 0x18c5 */
```

so it started as eight zeros. That produced two symptoms that did not look
related: particles were the wrong colour, and they drilled straight down through
the ground. The second is the first - colour 0 is empty terrain, so the stain at
`0x0aab3` was *erasing* the pixel each particle landed on instead of staining it,
and the particle fell into the hole it had just made. The fix is the eight bytes
out of the image, `5c 5d 5e 5c 5d 52 64 58`, checked against a live guest.

Note what did **not** catch it. `test_particles.py` compares the C step against
guest `0x0a956` on 400 made-up pools and passes byte-for-byte, because it hands
each particle's colour to both sides in the record. The routine was right; its
input was not. A comparison only covers what it supplies.

## The sweep, and what it found

`test_dgroup.py`'s parser already finds every declaration carrying a DGROUP
offset, and its `DECL` pattern requires a `;` straight after the name - so
everything it returns is bare. Reading the image at `d+offset` for each of them,
158 bare declarations, **seven** have non-zero bytes there:

```
d+0x04fa scroll_smooth       01 00
d+0x0500 out_of_memory       d6 21 95 18
d+0x054a shareware_limit     14
d+0x054c text_colour         6f 00
d+0x0dab g_dab               01 00
d+0x1727 page_back           00 7d
d+0x179f warp_table          00 00 00 01 01 02 03 04 06 08 0a 0c 0d 0e 0f 0f ...
```

## Step 1 done: read against four live guests

Each offset was read out of the image and out of `main-menu`, `snap003`,
`snap012` and `level80-late`. A value that is the same in all five is data
nothing writes; one that varies is written at runtime and the default does not
matter.

```
name             image                    live guests
scroll_smooth    01 00                    01 00 | 01 00 | 01 00 | 01 00     DATA
warp_table       00 00 00 01 01 02 03 04  identical in all four             DATA
shareware_limit  14                       14 | 14 | 14 | 14                 default
out_of_memory    d6 21 95 18              d6 21 a5 19 x4    a relocated pointer
text_colour      6f 00                    02 00 | ff 5b | ff 5b | ff 5b     written
g_dab            01 00                    01 00 | 00 00 | 01 00 | 00 00     written
page_back        00 7d                    00 00 | 00 7d | 00 00 | 00 00     written
```

**Two were real.**

`scroll_smooth` (`0x4fa`) is 1, and only the `c` key and MOUSE SETTINGS ever
change it. Bare, the port ran with SMOOTH SCROLL **off**, so every camera move
was `scroll_follow`'s hard edge push instead of the ease - the view sat still
until the followed point walked out of it and was then shoved. That is what "the
demo is still missing mouse scrolling" turned out to be.

`warp_table` (`0x179f`) is the background warp's per-row x displacement, a
0 -> 16 -> 0 hump indexed `& 0x1f`, read at `0x05e8b` behind `[0x2022]`. It lives
in `sdl_io.c`, not `game.c`, and unlike `particle_colours` it *was* being read -
`compose_scroll` does `dx = base_x + warp_table[phase]` every warped row. Thirty
two zeros meant the warp ran and displaced nothing: a flat wobble rather than a
missing one, which is the harder kind to notice. Its old comment said "nothing
read so far fills it, so the warp is inert here", which was true and was the
bug, not a description of the original.

`shareware_limit` is carried too - `case 1` replaces it from the egg before
anything compares it, so this is tidiness rather than a fix. The other four are
written at runtime and their image bytes are irrelevant.

## A third one, and it was outside the sweep entirely (2026-08-08)

`current_buffer` (`d+0x1721`) is a **relocated far pointer initialiser**:

```
d+0x1721   f1 13 95 18      ->  1895:13f1, which is default_buffer
```

and the relocation table has an entry sitting on the segment half, so the linker
emitted it. `set_buffer` at `0x0b9ea` is the *only* store to `0x1721` in the
whole image - every other reference is `c4 1e 21 17`, `les bx, [0x1721]`. So the
original never publishes the fallback buffer; it starts published.

The port had it bare in `stubs.c`, which made `show_splash` write its sprite
set's palette through a null pointer before anything had called `set_buffer`.
The bring-up patch for that was a `buffer_init()` called from `main` - a routine
that does not exist in the game, invented to fill a gap that was never there.
Both it and `set_buffer` are now in `game.c`, the latter as the eighteen bytes it
actually is, and the pointer carries its initialiser.

**Why the sweep missed it.** `test_dgroup.py`'s parser keys on the DGROUP offset
comment, and this declaration had none - it sat under a `/* the eggs */` banner
in `stubs.c` with `egg_stream`. So the seven found above are seven out of the
*annotated* declarations only, and anything the port declared without pinning it
to an offset was never a candidate. That is a second blind spot beside the one
below, and a worse one, because it hides the variable from the offset-collision
check as well.

## To do

Turn the sweep into a test next to `test_dgroup.py`: read each bare
declaration's image bytes, and fail on any that are non-zero and not in a short
list of known-written ones (`text_colour`, `g_dab`, `page_back`,
`out_of_memory`). That way a new bare declaration over initialised data fails on
the next run instead of being found by its symptoms months later. The four-guest
comparison above is what the known-written list is derived from, so a future
addition to it needs the same evidence rather than an assertion.

And close the blind spot the third one came through. Two parts to it:

- **Declarations with no offset comment.** `current_buffer`, `egg_stream` and the
  two palette arrays all sat in `stubs.c` without one. Moving them to the module
  that owns them and annotating them took the parser from 164 to 167. A count of
  definitions across `game.c`/`sdl_io.c`/`sound.c`/`egg.c` against that 167 says
  how many are still invisible.
- **`DECL` requires the `;` straight after the name**, so any definition with an
  initialiser is skipped: `game_speed = 29`, `gamma_level = 16` and now
  `current_buffer = default_buffer`. For the initialiser hunt that is harmless -
  those are the ones that already have what the hunt is looking for - but they
  are also skipped by the *offset-collision* check in the same pass, so they can
  claim DGROUP bytes that overlap something else and nothing will say so.
