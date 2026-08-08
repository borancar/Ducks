# The reconstruction: how it was read, and how far it is verified

**This is the analysis behind [`reconstruct/`](../../reconstruct/).** It lives on
`develop`, because the distribution branch carries the port and not the argument
for it: someone who wants to build and play should not have to read this, and
someone who wants to know *why* a routine looks the way it does should not have
to guess.

Moved out of `reconstruct/README.md` on 2026-08-08, unchanged except for this
header and the link targets.

## How much of the file split can be recovered

Some of it, and the boundaries that *are* recoverable are hard ones.

Turbo C++ in a far-code memory model gives each translation unit its own code
segment, and a call between segments has to be a far call with the target's
segment encoded in the instruction. So every `lcall seg:off` in the image names
the segment its callee lives in, and collecting the distinct values maps the
program's code segments:

| segment | image range | far-called entry points | what is in it |
| --- | --- | --- | --- |
| `0x0000` | `0x00000`-`0x04ca0` | 42 | the C runtime and library: `install_int23`, `puts`, `delay_ms`, `farmalloc`, `egg_read_byte` |
| `0x04ca` | `0x04ca0`-`0x14620` | 4 | **the game**: `main`, `game_main`, the menus, the cutscenes, the drawing |
| `0x1462` | `0x14620`-`0x149e0` | 7 | sound API and hardware detection: `sound_play`, `release_sounds`, `detect_hardware` |
| `0x149e` | `0x149e0`-`0x159a0` | 14 | the mixer and voices: `play_sample`, `stop_sound_by_id`, `is_sound_playing` |
| `0x159a` | `0x159a0`-`0x15a40` | 7 | XMS: `xms_present`, `xms_get_entry` |
| `0x15a4` | `0x15a40`-`0x1c07c` | 1 | `parse_blaster_env` and the BLASTER parsing |

Those six divisions are facts about the binary, not guesses: code in different
segments cannot be in the same translation unit.

**What this does not give us** is the split *inside* a segment. `0x04ca0`-`0x14620`
is a full 64 KB - the segment size limit - and holds essentially the whole game.
Either that is one enormous module, or the linker combined several same-named
segments, and the far calls cannot tell us which.

Weaker signals for boundaries within a segment, none of them conclusive:

- **Functions are emitted in source order within a module**, so a run of related
  functions at consecutive addresses is at least consistent with one file.
- **String literals cluster the same way.** `DEMO MISSING` at `DGROUP+0x26b0` sits
  next to the menu code's other text, and the block order follows link order.
- **Calls that cross a suspected boundary are ordinary near calls** here, so they
  prove nothing either way - which is exactly why the segment evidence is the
  only hard part.

So six units are certain, and the file name below is ours, not the original
author's.

## Scope: the game segment only

Only `0x04ca` is reconstructed here. The other five are **deliberately out of
scope**:

- `0x0000` is Borland's C runtime and library. Reconstructing it would be
  reconstructing somebody else's compiler, and the parts that matter -
  `delay_ms`, `int86`, the FP emulator's patch points - are already described in
  the root README where they were replaced.
- `0x1462`, `0x149e`, `0x159a` and `0x15a4` are the sound API, the mixer, XMS and
  the BLASTER parser. Those are documented as behaviour in the root README and
  reimplemented in `nsound.py`, `sb.py` and `xms.py`, which is a more useful form
  than C that cannot be compiled.

What is left - `0x04ca0` to `0x14620` - is the game itself, and that is the thing
worth having as source.

## How much of it exists

Measured by the image offset each body carries, not by name - names are what made
every earlier count of this wrong, and one of them counted an offset that appeared
only in a comment. As of 2026-08-08:

| | | |
| --- | --- | --- |
| the game's own segment, `0x04ca0`-`0x14620` | **194 of 194 functions** | **63,880 of 63,880 bytes** |
| sound, mixer, XMS and the C runtime above it | a deliberate handful | the rest is not coming |

The second row is small on purpose and will stay that way: it is Borland's
runtime and a Sound Blaster, and the port has a host libc and SDL instead. Only
the sound API itself was worth reconstructing, and it is in `sound.c`.

**Written is not verified, and the gap between them is the interesting number.**
194 of 194 is how much has a C body. What has been *compared against the guest* is
smaller and worth much more:

| harness | what it settles |
| --- | --- |
| `test_leaves.py` | 2,400 comparisons of the C leaves against the byte-compared natives |
| `test_entity.py` | `entity_update`, `level_event`, `collide_scenes`, `flock_link` and the walk, field by field on a real level |
| `test_blast.py` | `blast_terrain` and `stamp_sprite_into`, 400 calls, every pixel |
| `test_particles.py` | the particle step over 400 made-up pools |
| `test_photofade.py` | the photograph fade's state machine, and its DAC ramp |
| `compare_level.py` | the level loader: 20 fields and all 160 backdrop rows |
| `test_dgroup.py` | that no two declarations claim the same DGROUP bytes |

Everything else is a careful reading, and this session was a reminder of what that
is worth: a `do`/`while` read as a `for` in `entity_update`, a `strcasecmp` where
the original has `repe cmpsb`, and an `alloc_image` that fills with 1 rather than 0
all survived being read and were caught by being run.

**Where the comparisons are blind**, because it keeps mattering: they are all one
call deep. Both bugs found in `entity_update`'s fall-and-climb probe - the `sar`
that should not have been a divide, and the `do`/`while` - needed an entity in the
air at the moment of the step, and no captured snapshot has one. A frame-by-frame
runner is the missing instrument; see
[open-frame-comparison](open-frame-comparison.md).

`game.c` keeps its functions in **address order**, which within a module is the
order the compiler emits them and therefore the order they were defined in - a
free piece of information, so it is worth not throwing away. `dos_io.c` is grouped
**by device** instead: mode, planes, DAC, flip, mouse. The two files interleave in
the address space, which is a reminder that this split is ours and the original
had one module here, or several we cannot see.

Nothing in the game's own segment is missing now. What is left is not transcription
but verification, and a handful of places where the port knowingly answers
differently from the original - each one commented at the site and listed in
[`docs/notes/`](), the largest being `alloc_image`'s margin arguments
and what `terrain_at` returns outside the level.

**The level loader is in and is compared.** `level_load` (`0x088fa`) and
`stamp_solid` (`0x07490`) were read out of the disassembly rather than
transcribed from a byte-compared native, so they were the first bodies here to
rest on one reading - and `compare_level.py` is what closed that: it restores a
snapshot, reads the loader's output out of the guest's DGROUP, runs this loader
on the same level and diffs. On a level 53 capture, 20 fields and all 160
backdrop rows identical. (This paragraph claimed the opposite until 2026-08-07;
it was written in the same commit as the harness that disproved it.)
[run_level](run-level.md) has the shape of that work and
the order to take it.

**Where a body came from native.py rather than from the disassembly, the comment
says so.** Those natives are byte-compared against the original on every call, so
they are a verified description of behaviour - but they are vectorised Python, and
the shape of the C written from them is a reading, not a transcription. The one
exception is loud: `compose_scroll`'s warp branch has never been byte-compared,
because until level 80 it had never run.

## Conventions

- **Every function carries the image offset it was read from**, so any line can be
  checked against the disassembly.
- **A missing body is a `TODO`, never an ellipsis.** `...` reads as "and so on",
  which is a claim that the rest is obvious; it usually means nobody looked. A
  `TODO` says which address range is unread and what is known about it, so the
  gap can be closed by someone who did not write it.
- **Anything unverified says so** in a comment rather than being smoothed over -
  a name we guessed, a structure field we inferred, a branch never seen to run.
- **Fixed-width types from `<stdint.h>`**, because the width is the point: this is
  16-bit code, and `int` would read as 32 on anything modern. `char` survives only
  where the data really is text - a string, a `sprintf` target - so a `char *` in
  here means characters and a `uint8_t *` means bytes.
- **A type comes from how the code reads a variable, never from what it stores.**
  `mov byte [x], 0xff` is identical for a signed -1 and an unsigned 255; `cbw`
  after a load is what makes it signed, `mov ah, 0` what makes it unsigned, and the
  store width what gives the size. Put the evidence in the comment.
- **`extern` is a claim about where something is defined, so make it only when it
  is true.** The globals are defined in `game.c`, which the write-scan settled -
  nothing outside the game's own segment assigns to any of them - so `game.c`
  declares them plainly and `dos_io.c` declares them `extern`. They were `extern`
  in `game.c` for one commit, purely because that made a fragment look compilable.
- **A type invented to make two of our own signatures agree is the same mistake.**
  `blit_rows` was given a `rect_t` and the screen players a `viewport_t`, so a
  `screen_rect()` converter appeared between them - a function the original does
  not have, introduced to fix a problem that did not exist until the reconstruction
  created it. There is one record; the blitters read its first four words.
- **Never write a call that is not in the binary.** Some constructs compile to a
  runtime helper or to inline code, and naming a familiar library function instead
  is fiction: it puts a symbol in the reconstruction that the original does not
  call. Write the *source construct* that produced the instructions, and describe
  the helper in a comment. `input_poll` had `memcpy(&p, &pressed_count, 6)` in it
  for exactly one commit; what the code does is call the block-copy helper at
  `0x00ff4` with the length in `CX`, which is what a **struct assignment**
  compiles to - `memcpy` would pass the length as an argument.

## The type pass, done 2026-08-01

Every global in `game.c` was checked against how the code reads it, by
disassembling each Borland prologue to its `ret` and collecting, per DGROUP
offset: the access widths, `les`/`lds` (a far pointer), `adc` (32-bit
arithmetic), `cbw`/`cwd` and signed jumps (signed), zeroed high halves and
unsigned jumps (unsigned).

**Width is now evidence-backed throughout. Signedness is established for about a
third**; the rest are `int16_t` because that is what a Turbo C++ `int` is, which
is a default and not a finding, and the block comment says so.

Thirteen types changed. The ones worth knowing:

- `mouse_x`, `mouse_y` are **`uint32_t`**, not signed: 32-bit from the `add`/`adc`
  pairs, and compared with `ja`. A negative delta wraps high and the same clamp
  catches it, which is why the position never goes below zero without an explicit
  test for it.
- `game_speed`, `current_plane`, `shareware_limit`, `g_1fd3`, `g_2038`, `g_18f5`
  are byte-sized with the high half zeroed on every read - `uint8_t`.
- `max_save_value` and `g_2036` are compared with `jbe`/`jb`: unsigned.
- `next_life` (`0x201c`) is compared with `jle`: signed.
- `fade_level`, `level_attempted` and `last_key` are words that some sites read a
  byte at a time, which is worth knowing before assuming a 16-bit access.

**The first attempt at this was wrong and is worth remembering.** It inferred
32-bit from "the offset two along is also touched", which made `page_front` and
`page_back` - two adjacent words - into one long. Adjacency is not evidence; the
`adc` is.

## The shared library, and why the harnesses live here

`make lib` builds the same sources as `libducks.so`, so a harness can call one
function out of the port and compare it against the guest's own bytes under
Unicorn - see `test_toollist.py`. The emulator stays outside the port and the two
do not share memory: `far` is nothing here and a pointer is eight bytes, so only
`viewport_t` has the same layout on both sides.

That is why none of the comparisons can travel to the distribution branch. Every
one of them drives the original under Unicorn on one side, so they need
`native.py`, `emulation.py`, `snapshot.py` and the snapshots to mean anything.
The port can be built and played on `master`; it can only be *checked* here.

