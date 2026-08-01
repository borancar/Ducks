# Reconstructed source

C reconstructed from the disassembly, one file per unit of the original we can
argue for. Every line should be checkable against an image offset, and where a
name or a type is a guess, it says so.

The originals are not available. This is what the code must have looked like for
the compiler to have emitted what it did.

**Two backends, one interface.** `dos.h` declares what the game needs from below
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
| [`dos.h`](dos.h) | the types and the interface both backends implement, so `game.c` does not know which it is linked against |
| [`game.c`](game.c) | twenty-two functions: `close_egg_files`, `resource_load_full`, `resource_load`, `input_poll`, `make_rect`, `particles`, `draw_entities`, `show_resource_loop`, `draw_number`, `egg_load_pass_0x48`, `show_resource`, `draw_number2`, `cutscene_welcome_home`, `cutscene_photos`, `show_splash`, `episode_end_gate`, `menu_screen_driver`, `game_main`, `scan_save_slots`, `save_settings`, `init`, `main` |
| [`dos_io.c`](dos_io.c) | nineteen functions: `set_bios_mode`, mode, planes, DAC, page flip, the three INT 33h wrappers, and every drawing primitive the native port replaced - `clear_vram`, `plot_pixel` and its stride-90 twin, `palette_fade_step`, `blit_rows`, `blit_rows_masked`, `compose_layer`, `compose_scroll`, `draw_sprite`, `outline_sprite`. No TODOs left in this file |
| [`sdl_io.c`](sdl_io.c) | the same interface on SDL3: a linear framebuffer, an SDL palette, a 70 Hz deadline in place of the retrace spin, and SDL events counted into the INT 33h wrappers' shape. **Compiles** - `cc -c sdl_io.c $(pkg-config --cflags sdl3)` |

`game.c` keeps its functions in **address order**, which within a module is the
order the compiler emits them and therefore the order they were defined in - a
free piece of information, so it is worth not throwing away. `dos_io.c` is grouped
**by device** instead: mode, planes, DAC, flip, mouse. The two files interleave in
the address space, which is a reminder that this split is ours and the original
had one module here, or several we cannot see.

Still missing and worth adding as they are read: the in-game frame at `0x0d7ee`
with the four plane loops inside it, `run_screen` (`0x0c716`), the other four
cutscene screens, and `high_score_screen`.

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
- `g_201c` is compared with `jle`: signed.
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

`game.c` + `sdl_io.c` + `stubs.c`. Link `dos_io.c` in place of `sdl_io.c` and the
same game would talk to a VGA - that swap is the whole point of the split, and the
fact that it compiles either way is the first real check that the line was drawn
in the right place.

**It is not the game yet.** Most of the segment is unread, and `stubs.c` is the
list of what: `run_screen` above all, since every menu is that one function on
different data, and then the egg reader, which is what makes a resource exist at
all. `in_game_frame` is deliberately a no-op returning "the run ended", so
`game_main`'s inner loop falls through to the screens either side of it - the
menus are what this is for at the moment, not the gameplay.

What happens if you run it today: `init` spins until a key, then `game_main` calls
`run_screen`, which is stubbed to answer QUIT, so it takes main's teardown path
and exits. That is a full pass through the program's real control flow with almost
none of its content - which is the point of getting it to link this early. The
shape is testable before the pixels are.
