# Open: alloc_image's four arguments, and the backdrop's missing margin

**Opened 2026-08-08, while transcribing the COLOURMAP chart at 0x0ce2e.**

## What was found

`alloc_image` (0x05388, 745 bytes) takes a descriptor and four `int16_t`, and
the port ignored all four. Reading 0x053b4 onwards, three of them are real:

```
desc[+0x10] = a,  desc[+0x12] = b,  desc[+0x14] = c    ; kept on the descriptor
desc->w += a * 2                                       ; a margin on each side
desc->h += b                                           ; and one below
rows = malloc(h * 4); each row = malloc(w)
every byte of every row = 1                            ; 0x054f6
e -> fatal(out_of_memory, ...) on a failed malloc rather than returning 0
```

**The fill of 1 is fixed** (2026-08-08). It was `calloc`, so the port filled with
colour 0, and it showed: the colour chart paints 160x160 of a 320x200 page, and
the original has colour 1 around it. Verified against the guest - stop the
original one instruction before its flip, read the page back, and all 38400
pixels outside the chart are 1 while all 25600 inside match the swatches.

## What is still open

Every call in the port passes `(0, 0, 0, 1)`, so the margins are zero and the
fix above makes those exact. **One caller in the original does not**: the
backdrop, at 0x090f7, is

```
alloc_image(&backdrop, 1, 1, 0xa, 1)
```

so the original's backdrop is `level_w + 2` wide and `level_h + 1` tall, with
`0xa` kept at +0x14. The port allocates `level_w` by `level_h` through its own
`image_alloc` helper, which hardcodes `(0, 0, 0, 1)`.

That is not a quiet edit. `terrain_at` indexes the backdrop for every collision
in the game, and it already has hand-written answers for out-of-range reads:

```c
if (y < 0 || y >= backdrop.h) return 0;    /* the row table - unknowable */
if (x < 0 || x >= backdrop.w) return 1;    /* the row's own header */
```

Those two lines were written to match observed behaviour, and the second one -
returning **1** for an out-of-range column - is suspicious in exactly the right
way now that we know the original allocates a one-column margin filled with 1
and then walks into it. It looks like the port is emulating the margin at the
index rather than having it.

So the question to settle before changing anything: does the original's terrain
walk *rely* on reading the margin, and does the port's bounds check give the
same answer in every case, or only in the ones that have been exercised? The
tile paint at 0x09167 writes `level_w` columns and `level_h` rows, so with the
margin the last column and row of the allocation stay at 1 - which is what the
port's `x >= backdrop.w -> 1` reproduces for the columns and its
`y >= backdrop.h -> 0` does *not* reproduce for the row.

**That asymmetry is the thing to look at first.** If the margin row exists and is
filled with 1, a walk that steps off the bottom of the map reads 1 in the
original and 0 in the port, and 0 versus 1 is empty versus solid.

## How to check it

`test_entity.py` already drives `entity_update` and `collide_scenes` on a real
level against the guest, one call deep. The case that would show this is a duck
at the very bottom row of a level, where the walk reads `y = level_h`. Find a
level where that happens - or place one there - and compare. Do not change
`image_alloc` first: measure, then change, or the test is measuring the change.
