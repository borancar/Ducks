# The three files Ducks! reads and writes

`Eggs/Main.egg`, `GAME1.SG`–`GAME5.SG` and `settings.dat` — everything the game
keeps on disk. Written from the loaders in
[`reconstruct/`](../../reconstruct/), each field checked against the routine
that reads it, and the block inventory measured from the shipped egg rather
than assumed.

## Three primitives, and two endiannesses

Every one of these files is read with the same three calls, which is why the
formats look alike:

| | |
| --- | --- |
| **byte** | `egg_read_byte` |
| **word** | `egg_read_word` — **big endian**, `hi << 8 \| lo` |
| **string** | `egg_read_string` — a big-endian word of length, then that many bytes, **each one more than the character it stands for** |

That last one is the whole of the game's text obfuscation: `Vtjoh` is `Using`.
It is not applied selectively — it is the only string reader there is, so egg
text, save-slot names and hall-of-fame names all carry it.

The directory's offsets are the exception: they are **little endian**, 32-bit.
So one file has both orders in it. Whoever wrote the packer wrote the sizes by
hand and let the compiler write the offsets.

`egg_read_string` on a stream that has ended reads `0xff` twice, which is `-1`
as a word; the original then allocates nothing and writes a terminator one byte
in front of the allocation.

---

# 1. The egg

```
  count       2 bytes, BIG endian          303 in Main.egg, the "303 slices"
                                           the startup banner prints
  directory   count entries of 7 bytes
                +0  type     one ASCII letter
                +1  unused   always 0 in this egg
                +2  index    which one of that type
                +3  offset   4 bytes LITTLE endian, from the END of the
                             directory rather than the start of the file
  data        the blocks, in no particular order
```

A block is found by walking the directory for a `(type, index)` pair. There is
no length field: a block ends where the reader stops.

## What the letters mean

Measured from `Main.egg`:

| type | | count | what it is |
| --- | --- | --- | --- |
| `0x41` | `A` | 2 | the secret-level picture, for levels 29 and 62 only |
| `0x42` | `B` | 20 | level backgrounds |
| `0x43` | `C` | 6 | per-level sprite sets — the tiles a level stamps |
| `0x44` | `D` | 14 | a level's information page |
| `0x45` | `E` | 4 | episode text |
| `0x46` | `F` | 1 | the font |
| `0x47` | `G` | 1 | the animation table — every entity type in the game |
| `0x48` | `H` | 27 | string tables **and** readme pages, split by index |
| `0x4c` | `L` | 84 | the levels: 1–80, then 200–203 |
| `0x4d` | `M` | 22 | full-screen pictures |
| `0x51` | `Q` | 9 | solids — the objects stamped into a level's terrain |
| `0x52` | `R` | 6 | recorded demos |
| `0x53` | `S` | 4 | sprite sets |
| `0x54` | `T` | 8 | tile sets |
| `0x57` | `W` | 4 | the episode pictures, one per episode |
| `0x58` | `X` | 87 | sounds |
| `0x59` | `Y` | 2 | the secret-level text, again only 29 and 62 |
| `0x5a` | `Z` | 2 | index 1 is the egg's information block; index 255 its kind |

## Pictures — `M`, `B`, `Q`, `T`, `W`, `A`

```
  width     2 bytes big endian
  height    2 bytes big endian
  colours   1 byte      how many palette entries follow
  palette   3 bytes each
  pixels    chunked, see below
```

The pixels are the only compressed thing in the file, and it is not a general
compressor:

```
  table   16 bytes    a translation from nibble to colour index
  count   2 bytes     how many PIXELS this chunk covers, not bytes
  data    4 bits a pixel, LOW nibble first, each indexing the table
```

So each chunk says "the next N pixels use only these sixteen colours" and
spends half a byte on each. A run of sky costs a table and then nothing much; a
busy row starts a new chunk. A chunk boundary drops any half-used byte.

## Sprite sets — `S`, `C`

```
  count     2 bytes big endian
  sprites   count of:
              w, h        1 byte each
              ox, oy      1 byte each - the origin
              pixels      w*h bytes, RAW - not chunked
  n         1 byte      how many palette entries follow
  first     1 byte      the colour index they start at
  palette   n*3 bytes
```

Sprite pixels go through `fread`, not the chunk decoder. In `S` set 0 the
palette slice is 32 colours starting at 80, and every pixel in all 273 sprites
is either 0 — transparent — or in `80..111`, so the set is self-contained. The
palette bytes are 8-bit RGB here, not the 6-bit DAC values a picture carries.

## String tables — `H`, high indices

```
  count     1 byte
  strings   count of the shifted-string form
```

| index | table |
| --- | --- |
| `0xfb` | the v1.2 extended text |
| `0xfd` | `menu_text` — 83 strings, every message the game shows |
| `0xfe` | the ten cheat words |
| `0xff` | the tool names, indexed by `anim_c[type]` |

## Text pages — `D`, `E`, `Y`, and `H` at low indices

```
  count     1 byte
  lines     count of the shifted-string form
```

A line beginning with a digit sets the colour for that line and the ones after
it; the digit is stripped. The reader wraps each line to a maximum width, so
what is stored is paragraphs and what is drawn is lines.

## The font — `F`

```
  count     1 byte
  glyphs    count of:
              w, h     1 byte each
              codes    1 byte each, terminated by 0 - every character
                       that shares this bitmap
              pixels   w*h bytes, raw
```

The code list is why one bitmap can serve several characters. Only the first
code of each glyph is kept in `font_codes`.

## The animation table — `G`

One block describes every entity type in the game:

```
  types     2 bytes big endian      91 in this egg; more than 0x6f is fatal
  records   types of:
              n         2 bytes     how many steps in the script
              script    n words     a sprite index per step
              anim_a    1 byte      collision reach in x
              anim_b    1 byte
              anim_c    1 byte      index into the tool-name table
              flags     1 byte      bit 0 moves, bit 1 mirrored-pair tool,
                                    bit 2 the facing-right art is in the
                                    NEXT slot
              next_type 2 bytes     what it becomes when the script ends
```

The reader appends `999` to each script as a terminator. A type whose
`next_type` is itself loops forever. **Bit 2 is why three of the 91 indices are
not types at all** — `0x29`, `0x3a` and `0x4c` hold artwork reached as
`anim_script[type + 1]` and nothing is ever set to them.

## Levels — `L`

```
  tile_set     1 byte      which T block
  sprite_set   1 byte      which C block
  w, h         1 byte each in TILES, not pixels
  map          w*h bytes
  background   1 byte      which B block
  tool_count   1 byte
  tools        tool_count bytes - entity types, offered in the pen
  records      1 byte
  entities     records of:  x  2 bytes big endian
                            y  2 bytes big endian
                            type 1 byte
  hero         1 byte      which entity of scene 0 is the leader
  text         string      the level's name
  flags        7 bytes     see game.h - lightning, slippery, warp, ...
  bg_drift     1 byte
  timer        2 bytes
  facing       1 byte      the hero's, less one
  ambience_on  1 byte
  ambience_n   1 byte
  ambience     ambience_n bytes - the sound ids for this level
  solid_count  1 byte
  solids       solid_count of:  id 1 byte, x 2 bytes, y 2 bytes
  next_level   1 byte
  fracs        4 bytes     1/256ths, and the stream order is [3],[0],[1],[2]
```

Two things in that layout carry meaning a field name would not:

**The entity records are positional.** `ENTITY_TELEPORTER_ENTRY` reads
`o[1].x`/`o[1].y` — the record *after* it — as its destination. All 23 entries
in this egg are immediately followed by an exit, with no exceptions across the
84 levels. Reorder those records and ducks teleport to whatever is next.

**`next_level` is how the secret levels are reached.** Levels 19, 46, 71 and 52
name 200, 201, 202 and 203, which is the whole reason those four blocks exist
outside the 1–80 the episodes cover.

## Sounds — `X`

```
  length    2 bytes big endian    up to 65535, NOT 32767
  pcm       length bytes, signed 8-bit
```

Played at 11000 Hz — `sound_init(11000)` — and doubled by the `D` cheat. The
biggest sample here is index 76 at **57,363 bytes**, and five exceed 32767,
which is why the length must be read unsigned: as a signed word it arrives
negative. (All 87 blocks check out — the length word equals the distance to the
next block in every one, which is how this parse was verified rather than
assumed.)

## Demos — `R`

```
  want         string      an egg id, or empty for "the egg I was found in"
  id           string      that egg's name, for the error
  seed         1 byte      what run_level srand()s
  level        1 byte
  event_count  1 byte
  events       event_count of THREE WORDS, stored out of order: the two that
               come first go to +2 and +4, the one that comes last to +0
  tool_count   1 byte
  tool_events  tool_count of: 1 byte then 1 word, again reordered
  script_count 1 byte
  script       script_count of 3 bytes - the hero's walk
```

Six demos in this egg, playing levels 36, 63, 11, 49, 53 and 26 — all real
levels; nothing in the attract mode visits a secret one.

## The information block — `Z` index 1

One per egg, and the only block the startup sequence *requires*:

```
  version      1 byte      4 to 6, or the game refuses the file
  limit        1 byte      the shareware level cap - 20 here
  episodes     1 byte
  readmes      1 byte
  episodes     episodes of:  name string, first 1 byte, last 1 byte
  label        string      "Full Version"
  credit       string      "Tim Furnish / Hungry Software 1998-2000"
  complete     1 byte      the first egg must be 1 and the rest must not be
  id           string      an opaque key; two eggs sharing one is fatal
  readmes      readmes of:  name string, first 1 byte, last 1 byte
  demos        1 byte
```

`first` and `last` are **bytes**, which is what lets an episode name levels up
to 255. The four episodes here cover 1–10, 11–30, 31–50 and 51–80.

`Z` index 255 is one byte: the egg's kind, plus `0xd0`. An egg without one is
kind 1.

---

# 2. `settings.dat`

Written whole on exit, read whole at startup. No directory, no lengths — a flat
run of bytes in one fixed order.

```
  header       "!" then a NUL
  settings     5 bytes     the on/off toggles
  video_mode   1 byte
  button_map   3 bytes     which physical button is apply/cycle/other
  ambience     1 byte
  game_speed   1 byte
  gamma        1 byte
  registered   1 byte
  if registered:
    owner_name string
    owner_key  2 bytes big endian
  hall of fame 10 rows of:  score  2 bytes big endian
                            name   string
                            serial 2 bytes big endian
```

**The `!` is a version stamp, not a magic number.** The reader consumes the
leading string looking for it; if it never sees one the file is the *old*
format, where the button map is derived from `settings[2]` rather than stored
and there is no gamma byte. The game announces the conversion —
`Old settings.dat format! Converting file...` — and writes the new form on exit.

The port refuses a file with no header at all, which the original does not do.
That is deliberate: the original keeps `fgetc`'s `-1` in a byte, where it is
`0xff` rather than zero, so an empty file spins forever and a truncated one
reads `-1` into every field after the cut. A `-1` in `button_map` then sends
`extra_text[5]` — longer than the room reserved for it — into the menus, and the
heap is corrupted before a window exists.

`serial` ties a board row to a saved game; see below.

---

# 3. `GAME1.SG` … `GAME5.SG`

Five slots. The filename is not five constants — `save_name` holds the template
`"GAME-.SG"` and the digit is patched into offset 4.

```
  header       "Ducks Saved Game v1.2" then a NUL
  slot_name    string      what the player typed, shown in the menu
  egg_id       string      which egg this game belongs to
  egg_name     string      that egg's filename, for the error
  serial       2 bytes big endian
  score        2 bytes big endian
  next_life    2 bytes big endian
  level        1 byte
  lives        1 byte
  completed    1 byte
  secret_from  1 byte      v1.2 only
```

**The header doubles as the version stamp, and the test is a `2` anywhere in
it.** With one, the file carries the trailing `secret_from_level` byte; without,
it stops after `completed`. So the version check is a substring search over a
banner rather than a number in a field.

`level` is a byte, which is what allows a save to sit on a secret level —
200–203 fit.

## What `serial` is for

Not a slot number: a monotonic counter shared between `settings.dat` and every
save file. `serial_high` is the largest seen in either, and a game mints its
serial from it **once** — later saves of the same game keep it, so it identifies
the *game*, not the file.

Finishing a game writes that serial into the hall-of-fame row. Loading a save
looks for its serial on the board, and a match means this game has already been
finished:

> Attention!
> The game you're loading has already been finished, and has resulted in a
> score being added to the high score table.

Which is a neat trick with two files and four bytes: it detects replaying a
finished game without storing any history.

---

## What is not settled

- **`anim_b`** in the animation record is read into an array and nothing has
  been found that uses it.
- The **`+1` byte** of every directory entry is zero throughout this egg, so
  whether it means anything cannot be told from one file.
- Block `E` (episode text) and the low-index `H` pages are read by the same
  text-page reader; which of the two an index belongs to is decided by the
  caller, not by anything in the file.
