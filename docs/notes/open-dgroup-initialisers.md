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

These are **candidates, not findings**. Several are plainly re-set before use -
`text_colour` is written at the top of `level_screens`, `shareware_limit` comes
off the egg when a game starts - and a default that is overwritten costs
nothing. `warp_table` is the one that looks most like `particle_colours`: 32
bytes of a smooth ramp, which is data, not a default.

## To do

1. For each of the seven, decide by reading rather than by guessing: find every
   write to that offset in the image. No write at all means it is data and the
   port has to carry it.
2. Turn the sweep into a test next to `test_dgroup.py`, with the ones that are
   genuinely re-set at startup listed as known-and-fine, so a new bare
   declaration over initialised data fails instead of being found by its
   symptoms months later.
