# Ducks! — the game's own code, reconstructed

C reconstructed from the disassembly of **Ducks! v1.2** (Tim Furnish / Hungry
Software, 1998-2000), one file per unit of the original we can argue for. It
builds and plays:

```sh
make run
```

**You need your own copy of the game, and `make run` will fetch it for you.**
Nothing of Ducks! is distributed here — no executable, no `.egg` data, no artwork.
What `make run` does is download the archive from an archive page,
<https://www.kieranmillar.com/ducks/>, and keep the one file the port needs:
`Eggs/Main.egg`, which holds every picture, level, sound and line of text in the
game. That page is not the authors' — it is where a copy can still be found. It needs `curl` and `7z`, does nothing if `Eggs/` is already there, and does
not touch the network at all if `DUCKS_GAME_DIR` points at a copy you already have.

`make` on its own just builds; `make eggs` just fetches.

Every line should be checkable against an image offset, and where a name or a type
is a guess, it says so.

> **This is a distribution branch.** It is generated from the `develop` branch,
> which holds the machinery that produced it: the unpacker, the DOS/VGA emulator
> the port is compared against, the snapshots, every verification harness, and the
> working notes. If you want to know *why* a routine looks like this, or to check
> the port against the original yourself, that is where to go.

The originals are not available. This is what the code must have looked like for
the compiler to have emitted what it did.

**Two backends, one interface.** `game.h` declares what the game needs from below
it; `dos_io.c` is the original reconstructed and `sdl_io.c` is SDL3. The game
should not be able to tell which one it is linked against - that is the test of
whether the split was drawn in the right place, and it is why `input_poll` and the
number drawers stayed above the line.

**Written as C99, not as period C.** The aim is a port that can eventually be
built and run, so a construct is written the clearest modern way that matches the
instructions - compound literals, declarations at point of use, `<stdint.h>`. It
is not an attempt to feed Turbo C++ 3.0 something byte-identical, and a line that
reads oddly for 1998 is fine as long as it says what the code does.

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

## Files

| file | contents so far |
| --- | --- |
| [`game.h`](game.h) | the port's own header: its types, its globals and the interface both backends implement, so `game.c` does not know which it is linked against. Called `dos.h` until 2026-08-08, which described the smaller half of it |
| [`game.c`](game.c) | the game's own code segment, all of it - `main` and `init`, the resource and string loaders, the two fonts, the whole menu system and every screen it reaches, the save format, the hall of fame, the level loader, `run_level` and its frame, every entity's behaviour, the tools, the particles, the six endings and all six cutscenes. Naming them here would rot; they are in address order in the file and in `symbols.py` |
| [`dos_io.c`](dos_io.c) | nineteen functions: `set_bios_mode`, mode, planes, DAC, page flip, the three INT 33h wrappers, and every drawing primitive the native port replaced - `clear_vram`, `plot_pixel` and its stride-90 twin, `palette_fade_step`, `blit_rows`, `blit_rows_masked`, `compose_layer`, `compose_scroll`, `draw_sprite`, `outline_sprite`. No TODOs left in this file |
| [`sound.c`](sound.c) | the sound module, code segments 0x1462 and 0x149e: the id-to-sample map, the eight-voice table, and the mixer. A sample lives in extended memory in the original and in a `malloc` here, which is the only difference that matters - the game asks about sounds by id either way |
| [`sdl_io.c`](sdl_io.c) | the same interface on SDL3: a linear framebuffer, an SDL palette, a 70 Hz deadline in place of the retrace spin, SDL events counted into the INT 33h wrappers' shape, the mouse capture, and the audio device the mixer feeds |
| [`egg.c`](egg.c) | the egg reader: the directory, `egg_find_block`, the chunk decoder and the shifted-string reader. The port maps the file and walks it with a cursor where the original seeks a `FILE *`, which is why the readers take a stream and treat NULL as the egg |

**There is no `stubs.c`.** It held the to-do list in dependency order, and it went
when its last entry did - `sound_set_rate`, to `sound.c`, on 2026-08-08. Nothing
in the port stands in for a routine any more. That state is tagged `no-stubs`.

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
[open-frame-comparison](../docs/notes/open-frame-comparison.md).

`game.c` keeps its functions in **address order**, which within a module is the
order the compiler emits them and therefore the order they were defined in - a
free piece of information, so it is worth not throwing away. `dos_io.c` is grouped
**by device** instead: mode, planes, DAC, flip, mouse. The two files interleave in
the address space, which is a reminder that this split is ours and the original
had one module here, or several we cannot see.

Nothing in the game's own segment is missing now. What is left is not transcription
but verification, and a handful of places where the port knowingly answers
differently from the original - each one commented at the site and listed in
[`docs/notes/`](../docs/notes/), the largest being `alloc_image`'s margin arguments
and what `terrain_at` returns outside the level.

**The level loader is in and is compared.** `level_load` (`0x088fa`) and
`stamp_solid` (`0x07490`) were read out of the disassembly rather than
transcribed from a byte-compared native, so they were the first bodies here to
rest on one reading - and `compare_level.py` is what closed that: it restores a
snapshot, reads the loader's output out of the guest's DGROUP, runs this loader
on the same level and diffs. On a level 53 capture, 20 fields and all 160
backdrop rows identical. (This paragraph claimed the opposite until 2026-08-07;
it was written in the same commit as the harness that disproved it.)
[run_level](../docs/notes/run-level.md) has the shape of that work and
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

## Building it

```sh
cd reconstruct && make        # -> ./ducks, against SDL3
make run
```

`game.c` + `sdl_io.c` + `egg.c` + `sound.c`. Link `dos_io.c` in place of
`sdl_io.c` and the same game would talk to a VGA - that swap is the whole point of
the split, and the fact that it compiles either way is the first real check that
the line was drawn in the right place.

It needs your copy of the game's data: `Eggs/` beside the binary, which `make run`
will fetch, or `DUCKS_GAME_DIR` pointing at a copy you already have. Nothing from
the game is in this repository, and `clean` deliberately leaves `Eggs/` alone.

`make lib` builds the same sources as `libducks.so`, so a harness can call one
function out of the port and compare it against the guest's own bytes under
Unicorn - see `test_toollist.py`. The emulator stays outside the port and the two
do not share memory: `far` is nothing here and a pointer is eight bytes, so only
`viewport_t` has the same layout on both sides.

**It is the game.** The intro, the menus, the levels, the tools, the endings and the
cutscenes: you can start a game, play it, lose your lives, watch the ending and get
your name on the hall of fame. That state is tagged `no-stubs`.

The menus first, since they came first: all fifteen are real - `build_menus`
assembles them out of the string tables at startup and `run_screen` runs whichever
one `game_main` points at - so PLAY DUCKS, OPTIONS, the settings screens, READ ME!
and QUIT DUCKS all navigate, the toggles toggle, and QUIT takes main's teardown
path and exits. That much was tagged `menu-done`.

The cheats work, and they are typed in **capitals** - the compare at `0:0x4c28` is
`repe cmpsb`, with no case folding anywhere in it. `COLOURMAP` then `P` during a
level draws the palette; `THECROWDSAYBO` gives you the level picker;
`PLAYBACKTIME` opens the demo picker. `game.c` lists all ten against the flags they
set, and one of them - `KEYCODE` - is dead in the shipped build: it toggles a word
nothing reads.

The window captures the mouse, because the game keeps the pointer position
itself as a running total of INT 33h deltas and a pointer that stops at the edge
of a window is one the game believes stopped moving. **Ctrl+Alt** lets go, and
takes hold again; so does clicking in the window. Losing focus releases it
without forgetting what you asked for.

READ ME! works end to end: `build_episode_index` (`0x11657`) reads the episode,
readme and demo indexes out of the egg at startup, so SELECT AN EPISODE and
READ ME! list what the file actually holds, and `show_readme_section`
(`0x11efb`) draws a section's pages and turns them.

LOAD / SAVE works too: five slots in GAME1.SG to GAME5.SG, with the name typed
on a screen that is the menu itself with one line being edited. SAVE THIS GAME is
only offered while a game is in progress, which is reachable now that the gameplay
is.

Leave the menu alone for a while and the hall of fame comes up on its own, read
out of `settings.dat` at startup and written back on the way out.

The sliders, REGISTER DUCKS and the hall of fame all work, and every action code
`game_main` switches on reaches something real.

Sound works: 87 samples by id out of the egg's `0x58` blocks, eight voices, and
the original's additive mixer feeding an SDL device at the rate the game asks
for - including `D` mid-level, which doubles that rate exactly as the DSP time
constant did.

## This folder is going to be a branch of its own

`develop` carries the whole repository - the unpacker, the emulator, the native
port, the snapshots, the harnesses and the notes. **`master` will carry this folder
and nothing else**, so somebody who wants the port can clone and build it without
the analysis machinery that produced it.

Nothing about the code changes across that line. What changes is what a stranger
sees first, and it should be the game rather than the toolchain. The harnesses stay
on `develop` with the emulator they need: every one of them drives the original
under Unicorn on one side, so they cannot travel without it.
