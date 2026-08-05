# run_level(), and the shape of the gameplay

**Started 2026-08-02**, immediately after the `menu-done` tag. Nothing here is
ported yet; this is what the first read established, so the next one does not
have to start from the top.

**Renamed 2026-08-03**, from `in_game_frame`, once reading it end to end showed
it is not a frame — see [the map](#the-map). `level` was the first choice and
lasted an hour: it collided with the common noun everywhere the prose needed it,
and shadowed a parameter in `episode_end_gate`, which is now `number`.

## It is not one function

`0x0d7ee` is 4,287 bytes — the largest in the segment — and it calls **fifty
distinct routines**, about twenty of them still unread. Reading it alone would
not be enough to port it, and porting it first would mean guessing at the
structures its callees index.

The far calls out of it are worth noting separately: `0x1462:0x1a5` and
`0x1462:0x215` are the sound module, and `0:0x147d` is `rand`, called eleven
times here and twenty-five across the whole graph.

~~One call is indirect through `[0x53e]`, and nothing has been seen to set it.~~
**Wrong, corrected the same day.** `[0x53e]` is `plot`, which `set_mode_x` points
at `plot_pixel` or its stride-90 twin — it is in `dos_io.c` with that comment
already. Asserted from one call site without grepping for the address.

## It does no I/O

**Walked 2026-08-02.** Ninety-one functions are reachable from `0x0d7ee`. Between
them they make **fifteen port accesses and no software interrupts at all**, and
every one of those accesses is in a routine that is already ported:

| | |
| --- | --- |
| `clear_vram` `0x04d2a` | 1 |
| `page_flip` `0x04d4b` | 4 |
| `palette_upload` `0x056d2` | 2 |
| `set_plane` `0x057ee` | 2 |
| `palette_fade_step` `0x0b10b` | 6 |

So the gameplay proper — the other eighty-six — touches nothing but memory and
the Borland runtime. That is what makes taking it **piece-wise under Unicorn**
practical: a function can be run in the guest against a snapshot and against the
C reconstruction from the same state, and the two compared, with nothing to
answer but the runtime far calls. `native.py --verify-only` already does exactly
that for the plane loops.

The runtime calls that would need answering, by how often they appear across the
graph: `rand` (25), `free` (20), `fgetc` (17), the struct-push helper (11),
`malloc` (9). The rest are arithmetic, `memcpy`/`memset`, the string routines and
`sprintf` — all pure. `rand` is the only one that has to be pinned for a
comparison to mean anything.

## The prologue

Read out, and it says what the frame is set up from:

```c
hud_x        = video_mode * 0x14 + 0x135;   /* [bp-2], and +5 at [bp-4] */
score_start  = g_2036;                      /* [bp-6] */
...
blink_enable = [0x201e];                    /* 0x2157 - the blink the fade
                                             * has never been seen to run */
button_a_down = 0;
sprite_set_load([0x2103], 0x43, &[0x1fec], episode_egg_index);
g_1ffe = g_1ffc;  g_1ffc = 0;
clear_vram();
resource_load(&panel, 0x4d, 0x21, 0, 1, 0xff, 1);   /* the status panel */
```

So a level brings its own sprite set — block type `0x43`, chosen by `[0x2103]` —
and the panel is resource `0x4d`:`0x21`.

## The tool selector: d+0x1782

Four small routines around `0x0d4c2`–`0x0d5c4` all walk one array, and together
they identify it:

| | |
| --- | --- |
| `[0x1782]` | far pointer to an array of words |
| `[0x178b]` | how many, a byte |
| `[0x1788]` | which one is selected, a byte |
| `[0x1786]` | the selected word, copied out of the array |

- `0x0d55d(type)` — does any entry equal `type`?
- `0x0d591()` — does any entry have bit 1 set in `type_flags` (`d+0x3a7`)?
  That is the same per-type table `load_animations` fills and `draw_entities`
  reads bit 2 of, so **the array holds entity types**.
- `0x0d4c2()` — a level event table at `[0x203b]`, `[0x2047]` records of three
  bytes: when `rec[0]` matches `[0x201a]`, set the selection to `rec[2]`.
- `0x0d471()` — a second event table at `[0x203f]`, `[0x2049]` records of six
  bytes: when `rec[0]` matches `[0x201a]`, call `0x0d0c8(rec[1], rec[2])`.

`[0x201a]` is what both compare against and is presumably the level clock.

## What has moved, and how it was checked

**2026-08-02, the same day.** Seventeen of the ninety-one are Python natives in
`native.py` now, taken innermost first: `scene_keep_positions`,
`scroll_axis_toward`, `scroll_axis_snap`, `scroll_follow`, `entity_set_type`,
`scene_swap_pair`, `egg_block_end`, `rle_reset`, `set_buffer`,
`cursor_to_centre`, `bg_scroll_reset`, `palette_apply_gamma`,
`build_washed_ramp`, `tool_list_has`, `tool_list_any_flagged`, `text_width`,
`image_clear`, `tool_events`. Nineteen more have all their callees native and
are ready.

**2026-08-03: three demo captures changed what is reachable.** `run_level(1)`
is only ever called from the menu's idle timeout and the PLAYBACKTIME picker, so
until there were snapshots of one, every `[bp+6]` branch was dead to `--verify` -
and so was `tool_events`, which had sat written and unregistered on the belief
that it ran once at level start. It runs every frame; what no snapshot reached
was the demo. The captures also fixed the vacuous `scroll_follow` check: on a
420x260 level the camera actually moves, and feeding it the y coordinate where x
belonged now mismatches, where on level 80 it had not.

They did not fix everything. The tool-event tables in all three fire at clocks
337 to 642 and a `--verify` run reaches about 180, so those ~690 calls a run all
take the do-nothing path; one capture has no tool events at all. **Called is not
exercised**, and `test_gameplay.py` is what actually pins the write.

**A snapshot cannot check most of them.** Twelve seconds of `--verify` on the
level 80 snapshot is thirteen frames, and of the thirty routines that were ready
at the start it called three. Everything that runs while something is *loading* -
which is most of this list - is never reached at all, because every snapshot is
of a screen that has already settled.

Worse, one of the three was passing vacuously. Level 80 is 320 wide in a 320-wide
view and the duck does not move when nothing is driving it, so the camera has
converged and every call to `scroll_follow` is a no-op: feeding it the y
coordinate where x belonged changed nothing. That is the shape of a verification
that holds no matter what the code does.

So `test_gameplay.py` is the other half, and the two are complementary:

| | `native.py --verify` | `test_gameplay.py` |
| --- | --- | --- |
| state | real, from a snapshot | made up |
| coverage | whatever the run happens to do | every branch, by construction |
| what it proves | the native is right *here* | the two agree *generally* |

It writes the arguments where a call would have left them, runs the native, puts
the memory back, runs the guest's own body from the same state with the native
table unhooked, and compares. Nothing is asserted about the right answer - only
that the two agree, which is all that can be checked without a second reading to
be wrong in the same way.

It earned its keep on the first run. `scroll_axis_toward`'s rounding mask is
**not** `(1 << shift) - 1`: the guest computes `2 << (shift - 1)` in 16 bits with
the count in `cl`, so a shift of 0 - which is what the variable is initialised to
- asks for `2 << 255`, the hardware masks the count to 31, the result is 0, and
the decrement leaves 0xffff. Level 80 uses shift 2, where the two agree.

Two things had to be declared per native rather than blanket, both for the same
reason - what is right for a drawing routine is wrong for a gameplay one:

- **`VERIFY_REGIONS`**, what to watch besides the planes, *given the call's own
  arguments*. A scene's entities are `farmalloc`'d, so watching DGROUP finds
  nothing; comparing all 2 MB would cost half a gigabyte a second at these call
  rates.
- **`VERIFY_RETURNS`**, whether the return value can be compared. Three of these
  write nothing at all and answer in AX. The input shims cannot be compared this
  way at all: the native answers from a queue it has already emptied, and the
  original then goes to the hardware and quite properly gets a different answer.

## The map

**Read out end to end, 2026-08-03.** Two long jumps cut it in three, and the
proportions are the first thing worth knowing:

| | | |
| --- | --- | --- |
| `0x0d7ee`–`0x0dcc6` | 1,241 bytes | set the level up, once |
| `0x0dcc9`–`0x0e7d7` | **2,830 bytes** | the frame, over and over |
| `0x0e7da`–`0x0e8ac` | 211 bytes | tear it down and say what happened |

The back edge is the `jmp` at `0x0e7d7`, taken while `[0x1798]` is set. So
**two-thirds of the largest function in the segment is one loop**, and the name
`in_game_frame` was wrong by a level: it is not the frame, it is the whole of
playing a level, and it returns when the level is over.

### It takes one argument, and the argument is "this is a demo"

`[bp+6]`, read at seven places, and every one of them picks between a level that
is driving itself and a level someone is playing:

| | `[bp+6] != 0` — demo | `== 0` — played |
| --- | --- | --- |
| the tool | `0x0d4c2`, the level's own table | `0x0cf07`, the input |
| the event | `0x0d471`, the second table | `0x0d0c8(cursor)`, when `[0x18e1]` |
| the camera | the followed entity, or the flock's average | the cursor |
| scene 4 (`d+0xd93`) | not drawn | drawn |
| `0x07bb2` | gets it as its third argument | |

Returns `[0x200d] == 2`.

### Setting up

The prologue's locals say what a level is measured in: two HUD columns from the
video mode (`[0x4fe] * 0x14 + 0x135`, and five past it), the displayed score
starting at `[0x2036]`, the displayed duck count at `[0x2007]`, and a sparkle
seed of `rand() & 0x3ff`.

Then, in order: the level's sprite set (`0x0615a`, block type `0x43`); carry
`[0x1ffc]` into `[0x1ffe]` and clear it; **`srand([0x2039])`**; `clear_vram`; the
status panel as resource `0x4d`:`0x21`; `0x0d5c5` to build the HUD; the tool
selection reset to entry 0; a two-entity scene at `d+0x178c` for the tool
cursor; three overlay images through `0x0881d`, whose countdowns live at
`d+0x2154`; the "you have got X" message if the tool list is not empty; two
page-flips of a four-plane loop drawing the intro panel; `bg_scroll_reset`; the
clock zeroed and `[0x2003]` set to `0x1b`; two tool queries deciding `[0x2009]`
and `[0x200b]`; a far allocation of `(scene0.count + [0x18d1]) * 0x28 * 16`
bytes at `[0x18c1]`; `cursor_to_centre` and two `scroll_axis_snap`s; and the
ambience if `[0x1fd3]` and `[0x2015]`.

**`srand([0x2039])` is the one to notice.** The level carries its own seed, and
it is set once here, immediately before a loop that calls `rand()` eleven times a
frame - for the sparkle, for the ambient quack, for where a duck appears. That is
what makes a recorded demo replay identically: nothing about the randomness is
random across runs, and a demo needs only the input, not the outcomes. It also
means anything compared against the guest has to be compared from the *same*
seed, which `--verify` gets for free by sharing the machine.

### The frame

Roughly in order. The four `0x0a410` calls are the four ways a level ends, and
each takes a pair of strings out of `menu_text` at `0x1894:0`:

| at | | |
| --- | --- | --- |
| `0x0dcc9` | clock | `rand() & 0x7f == 0` plays a quack; `[0x201a]++` |
| `0x0dce6` | spawns | a duck into scene 0 while the hero is type `0x4f`; a thing into scene 2 while the tool is `0x50`, with a random ±1 in its byte at `-0x15` |
| `0x0dd83` | countdown | `[0x2005]` down to zero, then sound `0x26` and entity `[0xd7f]` becomes type `0x4b` |
| `0x0ddbc` | script | the table at `[0x2043]`, three bytes a record, advanced when `rec[0]` is the clock; `[0x2100] = rec[2] - 5` |
| `0x0ddfe` | timer | `[0x2003]` down one per `[0x2001]` frames; sound `0x1c` at zero |
| `0x0de40` | input | `input_poll(level_w, level_h)`, then `0x078a6` |
| `0x0de79` | tool | the demo/played split, then the cursor entity at `[0xd9b]` becomes `[0xdab] + 0x2a` if the tool is flagged and `0x14` if not - or `0x16` while `[0x1fd8]`/`[0x1fda]`, which also puts the selection back |
| `0x0df5d` | **the four endings** | hero gone and `[0x2009]` → `+0x130`; hero gone, `[0xda1]` clear and `[0x200b]` → `+0x134`; `[0x2013] > [0x2007]` → `+0x138`; `[0x2018]` → `+0x13c` |
| `0x0e088` | tool change | logged through `fprintf` when `[0x51d]`, then `[0x178a] = [bp-0x12] * 2 + 3` and sound 3 |
| `0x0e11b` | positions | `scene_keep_positions` on five of the six scenes, then `0x0970c` |
| `0x0e156` | the flock | `0x07bb2` per entity of scene 1, summing x and y of everything but the hero; two `__ldiv`s make the average, and `[0xdab]` is whether it is right of the cursor - which is what chose the cursor's sprite above |
| `0x0e234` | log | the hero's byte at `+0x14`, when it changes inside 0..1 |
| `0x0e2c9` | retire | `0x0d715` on three scenes, `0x0981b` on all six |
| `0x0e346` | level update | `0x0d4fc`, then `0x0993b` |
| `0x0e34e` | camera | the cursor is copied into entity `[0xd9b]`, then `scroll_follow` on whichever of the two the argument picked, with a 20-frame hold in `[bp-0x28]` |
| `0x0e42d` | animate | `animate_scene` on all six, plus the tool scene when there is one, then `0x0a956` |
| `0x0e485` | counters | the score chases `[0x2036]` by a quarter of the gap plus one a frame, so it rolls rather than jumps; both counters light a six-frame redraw flag |
| `0x0e4b4` | **frame skip** | `[bp-0x20] ^= 1`, and on the off beat with `[bp-0x12]` set, everything below is skipped |
| `0x0e4e3` | **the plane loop** | four passes: `compose_scroll`, `particles`, `draw_entities` on five scenes with a 20-byte viewport pushed by value, scene 4 when played and the tool scene, the three overlays through `blit_rows_masked` while their timers last, `draw_number` for each lit counter, and a run of `plot` for the timer bar |
| `0x0e673` | sparkle | one pixel of `0x6f` down a column at the seed, which then walks ±1 - or is redrawn from `rand() & 0x3ff` every 32nd frame |
| `0x0e71a` | flip | `page_flip` |
| `0x0e71e` | tool arrival | `[0x178a]` reaching 1 announces the tool and parks its two entities at `[0x1788] * 16 + 0x82` |
| `0x0e7b1` | fade | `palette_fade_step(0)`, two counters down, and round again while `[0x1798]` |

### Tearing down

Free the sprite set (`0x088b3`), the level (`0x09329`), the pair table and the
tool scene; put the text colours back. If `[0x1ffc]` says the level was
completed, either jump to `[0x2102]` or load and show resource `0x41`:`[0x2032]`
— the between-levels cutscene — and set `[0x1ffe]`. Close the log. Return.

### The runtime it leans on

`0:0x146c` **srand** and `0:0x147d` **rand** (the 0x015a4e35 LCG); `0:0x11cc`,
the helper that copies a struct onto the stack, which is how the 20-byte
viewport reaches `draw_entities`; `0:0x1059` `__ldiv`; `0:0x13f2` and `0:0xedb`,
the far allocator and its free; `0:0x33cb` `fprintf` and `0:0x3007` `fclose`,
which only run when `[0x51d]` is set — there is a play log, and nothing has been
seen to turn it on.

## Doing the demo first

**2026-08-03.** `run_level(1)` needs no input, so it is the version to get
running, and it is measurably less work: of the 35 routines still unwritten, 23
run in a demo.

Establishing *which* 23 took two goes, and the first was wrong. Tracing the four
demo captures says twelve never run - but every capture is mid-level, so nothing
in `run_level`'s setup or teardown can appear in a trace taken from one. Letting
the main menu time out into a demo and tracing that instead shows `0x0881d`,
`0x0d5c5`, `0x0615a`, `0x04f4b` and `0x0a85f` all running, the last of them 520
times. **Five of the twelve were wrong**, and every one of them is a routine that
runs once at level start or often under a condition the captures did not meet.

So the two kinds of evidence are not equal, and the stubs in `stubs.c` say which
they rest on. `0x0cf07` is off the demo path *by reading* - it is the played
branch of the `[bp+6]` fork, and `0x0ce2e` is reached only from it. The rest are
merely unobserved, and each complains the first time it is called, because a
stub that silently does nothing is how a demo quietly stops matching.

(The main-menu capture carries `game_in_progress` from having been taken under
`--no-demo`, so the idle timeout never fires from it. The measurement clears it
explicitly. That is not the restore-side fix that was rejected - there, our lie
and a genuinely paused game are indistinguishable.)

## The setup is written; the frame is not

**2026-08-05.** `run_level` is a real function in `game.c` now rather than a stub,
and its two halves are in different states. The **setup** - `0x0d7ee`-`0x0dcc6`,
1,241 bytes - is read out and transcribed in full, because everything it calls
exists. In order: the sparkle seed from `rand() & 0x3ff`, `blink_enable` from the
level's first flag, the level's own sprite set, `srand(level_seed)`, `clear_vram`,
the panel as `0x4d`:`0x21`, `level_palette_build`, the tool cursor as two entities
in a scene of its own with the second carrying the tool type, three message slots
ten rows apart starting 34 rows up from the bottom of the play area, the "you have
got X" line, the HUD drawn **once into each video page with a flip between**, the
background scroll reset, `warp_step` 7 or 1 on the level's last flag, the two
flags the endings test, `quota_left++` when the level has a hero, the particle
pool sized `(scene0.capacity + pair_slots) * 0x28`, the camera snapped to the
mouse, and the ambience.

The **frame** is a stand-in inside that function, marked as one: it composes what
the setup built, animates the scenes, moves the camera and waits for ESC.

The camera is the one part of the frame that is real, because everything it needs
existed. `0x0e34e` puts the mouse into the cursor entity - which is
`scenes[4].entities[0]`, the same object `[0xd9b]` names - and then, when someone
is playing, `scroll_follow(mouse_x, mouse_y)`. That is what makes a level wider
than the screen scroll: on level 11, 380 across against a 320 view, the mouse
crossing the level walks `scroll_x` from 0 to its maximum of 60. A demo instead
follows `scenes[3]`'s entity when `[0x1fda]` says so, or the hero duck while it is
facing somewhere, each with a twenty-frame hold, and falls back to the flock's
average - which the unwritten part of the frame computes, so a demo holds on the
hero here rather than averaging. The real one is 2,830
bytes and needs nineteen routines that do not exist, 7,464 between them - the
largest `0x07bb2` (3,082) and `0x0d0c8` (937). Until then a level can be looked at
but not played, and pressing ESC is how it ends rather than one of the four
endings clearing `[0x1798]`.

Four globals that had been declared twice are gone with this: `view_top`,
`view_bottom`, `view_left` and `view_right` were the first four fields of
`viewport_game`, and `particle_count` and `particle_array` have moved out of
`stubs.c`, since they are state the setup owns rather than something standing in.

**And a fifth, which crashed the level.** `bg_w` and `bg_h` at `0x1717` and
`0x1719` are `background.w` and `background.h` - `background` is the descriptor at
`0x170b`, and `+0x0c`/`+0x0e` are a `desc_t`'s size fields. Declared separately
they were always zero, and `bg_scroll_reset` divides by both, so the first thing
the setup did after the HUD was raise SIGFPE. `load_background` in the original
reads them rather than writing them, which is the tell: it computes
`wrap_x = bg_w - 1` from a value `resource_load` had already filled in.

Verified against `snap004`: the guest's tile is 128x64 with wraps 127 and 63 and
both scroll steps 0 at drift 4, and the port now agrees on all five.

That is the fifth instance of one pattern - a structure field declared a second
time as a scalar - and every one of them was silent until something read the copy
nobody wrote. When a DGROUP offset lands inside a record the port already has,
it is that field.

**The next crash was a table nobody loaded.** The setup's "you have got X" line is
`message_post(menu_text[7], tool_names[anim_c[tool_type]])`, and `tool_names` was
never filled: `load_string_tables` loaded three of the four tables and carried a
note claiming the tool names went through a different loader that had not been
read. They do not - `0x094cd` calls the same `load_string_table` with block `0xff`
and "Can't find tool names" as its message, and it is the *first* of four, not
absent. So the array was null and a level with tools read off it.

Level 1 survived because it has no tools. Levels 11 and 12 have two each, which is
what made this look like a scrolling fault - and scrolling is fine: the
compositor's worst-case read on both is the last byte of the backdrop, 379 of 380
and 439 of 440 across, 159 of 160 down.

**Two more had to come out before a level with tools would start**, both found
from a backtrace rather than by reading - build with `-ggdb` and let gdb name the
frame, which took one run where markers took three:

- `0x14284` in `init` calls `0x15388`, and that is **`alloc_image` at `0x05388`**:
  a near call wraps inside its own 64 KB segment, so the offset on a listing is a
  segment offset and not an image one. It had been a do-nothing stub named
  `f_15388`, which left all three message images without rows.
- `init_objects[3]` and `message_image[3]` both claimed `0x210c`. `init` filled
  one, `message_post` read the other, so `image_clear` got a null descriptor. That
  is the **sixth** instance of a structure or array declared twice - the fifth was
  `bg_w`/`bg_h` an hour earlier.

With those gone the setup runs to the frame on levels 1, 5, 11, 12, 40 and 80.

### The sweep, and what it found

Six bugs from one mistake was enough, so `test_dgroup.py` now reads every DGROUP
offset out of `reconstruct/`'s own declaration comments, gives each declaration
the size it has **in the guest** - a far pointer is four bytes there, the records
are packed - and fails if one lands inside another. Ten pairs on the first run,
and they were four different things:

- **Five parallel tables are 111 entries, not 112.** `load_animations`'s bound is
  `0x6f` and the offsets are `0x6f` apart: `anim_a` `0x25a`, `anim_b` `0x2c9`,
  `anim_c` `0x338`, `type_flags` `0x3a7`, `next_type` `0x416`, which ends at
  `0x4f4` right below the particle flag at `0x4f6`. `anim_script` at `0x9a` *is*
  112, because `0x25a - 0x9a` is exactly 112 far pointers. The note above said 112
  for all six and was wrong about five of them.
- **`0x0d7f` is `scenes[2].flag`**, not a global of its own. That matters: the
  guest's `scene_alloc` clears it to `0xff`, which is how a level starts with
  nothing being eaten, and a separate copy would have kept the last level's value.
- **`0x0d93` is `scenes[4]`**, which the port had as a parallel `cursor_scene`. It
  is a `#define` onto `scenes[4]` now, so the frame's "draw scene 4" and the
  menus' "draw the cursor" are the same object again.
- **`text_colour[1]` and the font's first glyph share `0x54d`** - and that one is
  the original's own doing, not a mistake here: nothing draws character 0, because
  `charmap` sends everything unknown to `0x1b`. Marked `alias` so the test leaves
  it alone.

The last three were artefacts of reading `0x1894:0000` - a *segment* - as a
DGROUP offset, which the test now knows not to do.

## The level format, and where a level comes from

**Read out 2026-08-05**, all 2,607 bytes of `0x088fa`. It is not reached from
`run_level` at all: `game_main` calls `f_1102a` just before `run_level(0)`, and
that calls this. So by the time the frame loop starts, everything below has
already happened - which is why `run_level`'s setup can look as thin as it does.

It takes no arguments. `egg_find_block(0x4c, level_attempted,
episode_egg_index)` opens the level's own `'L'` block and everything after that
is a straight sequential read, so **the stream order is the file format**:

| read | into | |
| --- | --- | --- |
| byte | `[bp-0x15]` | the tile-set id, used at the very end as block `0x54` |
| byte | `[0x2103]` | the sprite-set id, which `run_level`'s prologue loads as block `0x43` |
| byte, byte | a `desc_t` | the map's width and height in tiles, through `image_alloc` |
| w×h bytes | that grid | one tile index per cell, row-major |
| byte | `[bp-6]` | the background id, for `load_background` at the end |
| byte | `[0x178b]` | how many tools, then one byte each into a `farmalloc`'d array at `[0x1782]`. Tool `0x50` is remembered as `0x10` extra entity slots |
| byte | | how many entity records follow |
| n × (word, word, byte) | the scenes | x, y, type - the switch below |
| byte | `[0x2007]` | how many ducks, then that many (word x, word y) added to scene 0 as type 4 |
| byte | `[0xd67]` | which of those ducks is the hero: `entity_set_type(&scene0[i], 1)`, and only when no `0x4f` was loaded |
| word-prefixed string | `[0x200f]` | through `0x04f4b`, which is already `egg.c`'s string reader |
| 7 bytes | `[0x201e]`..`[0x202a]` | the level's flags, stored one per **word**. `[0x2022]`, the background warp, is the fourth |
| byte | `[0x202c]` | `bg_drift` |
| word | `[0x2001]` | frames per timer tick |
| byte | | the hero's facing, less one |
| byte, byte, n bytes | `[0x2015]`, a local | the ambience flag and the ambient sound ids |
| byte | `[0x2031]` | how many solid objects; `farmalloc(n * 0x20)` at `[0x202d]`, then per object a byte id at `+0x1e` and words x at `+0x16`, y at `+0x18` |
| byte | `[0x2102]` | where a completed level jumps to |
| 4 bytes | `[0x13ed]`, `[0x13e1]`, `[0x13e5]`, `[0x13e9]` | each `fild`'d and divided by the constant at `[0x232a]`, which is **256.0** - so they are fractions in 1/256ths. Nothing here reads them back |

**The entity switch.** Every arm adds to scene 2 (`0xd7b`); what differs is what
else it does. `0x4f` sets a flag worth `0x10`, which later becomes eight more
ducks scattered across the level; `0x42` widens scene `0xd6f` from 5 to
`duck_count + 5`; `0x4d` is added only when `[0x517]` is set or this is not the
level at `[0x1ffa]`; `0x51` is added plainly, but if it is the **first** entity
of scene 2 the loader also drops a type `0x53` at (0xa0, 0x64) into scene 0,
faces the first entity by `(rand() & 2) - 1`, and counts one extra duck. Types
**6 to
0x0a** share one arm - the jump table at image `0x9321` has four entries and all
four point at `0x08c04` - which bumps `[0x2000]`, takes the layer as `type - 5`,
and adds that into `[0x2013]`, the counter one of the four endings compares
against the duck count. Everything else falls through to a default that tests
`type_flags[type] & 2` and routes to scene `0xd9f` or to scene `0xd7b`.

**Then it builds what gets drawn.** `level_w = map_w * 0x14` and
`level_h = map_h * 0x14`, so **a tile is 20x20**. The tile set arrives as block
`0x54` and is painted into `backdrop` (`[0x16f5]`) twenty rows at a time by
`0:0x4bc1`, a `memcpy`; the map and the tile set are both released straight
afterwards, so neither survives the load. Each solid object is
`resource_load`ed into its 32-byte record - which is a `desc_t` with x, y and
then `right = x + w`, `bottom = y + h` filled in after - and `0x07490` stamps it
into `backdrop` **only where the destination byte is still zero**, so an object
goes behind whatever the tiles already put there. The eight ducks the `0x4f`
flag bought are then placed by a retry loop: `rand() & 0xff + 0x20` by
`rand() & 0x3f + 2`, rejected unless the backdrop pixel there is 0 - so they
land in open space, and the level's own seed decides where.

**Two `scene_t` fields are not what `dos.h` calls them.** `+4` ("flag", set to
`0xff` by `scene_alloc`) holds the hero duck's index for scene 0, which is what
`[0xd67]` is; and `+6` ("unread6") is written 1 for scene 2 at `0x08af5`.

### It builds viewport_game

The root README recorded `viewport_game` (`0x172d`) as "built somewhere else and
still unread". It is built here, in the last twenty instructions before the
ambience loop:

```c
left = level_w > screen_width ? 0 : (screen_width - level_w) / 2;
di   = screen_height - 0x28;                      /* the 40-row panel */
top  = level_h > di ? 0 : (di - level_h) / 2;
make_rect(&viewport_game, top, di - top, left, screen_width - left);
```

So a level smaller than the screen is centred in what is left above the panel,
and one larger than it starts hard against the edge.

### Checked against the guest, 2026-08-05

`compare_level.py` restores a snapshot, reads the loader's output out of the
guest's DGROUP, runs the port's `level_load` on the same level and diffs. On
`snapshots/snap004.snap` - level 1, captured while the ducks were still falling -
**20 fields and all 160 backdrop rows are identical**. That covers the level size,
the sprite-set id, the tools, the seven flags, the four fractions, the timer, the
solid objects, every scene capacity, the viewport, and every byte the tile paint
and the object stamping produced.

`snapshots/level-start.snap` - level 4, mid-play - agrees on everything except
**two backdrop rows**, which is consistent with the backdrop being written during
play rather than with a loader fault: the fresh capture matches byte for byte and
that one does not. Not proven, and worth settling when something is known to
write the backdrop mid-level.

**`quota_left` is not a loader output and is not compared.** The loader sets it
from the scenery layers, `run_level`'s setup then adds one at `0x0dbf5` when the
level has a hero duck (`[0xd67] != 0xff`), and `collide_scenes` decrements it as
ducks get home. Level 1 has a hero and reads 6 against the loader's 5; level 4
has none and reads 10 against 10. That increment is the only part of the setup
that has been read out precisely so far.

### The two screens before a level

`snap001` to `snap003` are the screens `f_1102a` shows between the menu and the
level, and they say where the loader sits in that sequence: **`snap001` has
`duck_count` 0** - the level is not loaded yet - while `snap002` and `snap003`
both have 11 with the clock still at 0. So the episode intro comes first, then
the load, then the information screen. All three already have `lives` at 5, so
whatever starts a new game sets it before any of this.

**Found at `0x137e0`**, in `game_main`'s START case: `lives = 5`, `score = 0`,
`[0x21a3] = 1`, `[0x201c] = 0x1388` - which is the first extra-life threshold,
5000 points, tested at `0x138cb` - and then `menus_resume()`. Five stores the
port did not have, which is why its HUD read 00 lives.

**It took the right instrument to find.** A census of every function that names
`[0x2034]` missed it, because that scan walks recognised function extents and
`game_main`'s did not cover the site. A byte scan of the whole code region for
the store patterns - `C7 06 34 20`, `A3 34 20`, `FF 06 34 20` - found six sites
in one pass, including this one, the extra life at `0x138cd` and the loss at
`0x139bf`. When the question is "what writes this address", scan the image, not
the functions.

### The episode intro is a map of the level

**Read out 2026-08-05.** `f_1102a` loads the level *first* (`0x1108b`), then a
picture - resource `0x4a` at the episode's ordinal, its own colours at `0x80`,
with `0x4d`:1 as the fallback, which in this egg is always taken because **type
`0x4a` has no blocks in it at all** - and then calls `0x0b284` to draw into it.

`0x0b284` is the level in miniature: **every fourth pixel** of the backdrop, so a
quarter of the size each way, centred in 320x200, with the background tile
showing through wrapped wherever the backdrop is transparent. Over that goes a
marker per duck - sprite 80, or 81 for the hero, the entity whose type is 1 - and
one per scenery item of types 6 to `0x0a`, all five of which share sprite 82 (the
jump table at image `0x0b525` has five entries and they are the same address).
Positions are the entity's own, quartered. Last a one-pixel frame in colour 0
with a one-pixel shadow down and right.

`0x10ba4` is `episode_for_level`: the episode whose range contains
`level_attempted` **and** whose egg matches, last match winning.

### 0x1102a, written out: two screens with the level loaded between them

**Written 2026-08-05** as `level_screens`, in the order the original does it: the
level's name bounces in as a banner (`0x110a4`) *before* the picture it will be
stamped onto exists, the level loads, the picture arrives with the map drawn into
it, the name and the status line go over it, that fades in and waits for a key
and fades out, and then - only if the level carries a `0x44` block, which
`0x11259` settles before the first screen is even shown - the instructions page
appears with the same banner over a fresh picture and one animated entity at
(0xa0, 0x3c).

Levels 1, 2 and 5 have that block; level 12 does not, so it goes straight from
the map screen into play.

`0x07259` is the tool row's halo: the same clipping and walk as
`sprite_to_image_plain`, but it writes colour 0 at the four neighbours of every
non-zero source pixel and nothing at the pixel itself - `outline_sprite` into an
image rather than through the plot pointer. The tools go in a row centred on the
screen, sixteen apart at y=0xb4, each haloed and then drawn, and the icon is the
first frame of the tool type's own script.

**Checked against `snapshots/snap006.snap` - a level with two tools - the whole
map screen is 64,000 of 64,000 pixels:** the name banner, the picture, the map,
both icons with their outlines, and the status line.

**And against `snapshots/snap003.snap`: 63,961 of 64,000 pixels, 187 of 200
rows identical.** The 39 that differ sit at x 159-161, y 48-60 - the animated
entity, which the frame loop draws to the screen rather than into the picture, so
it cannot be in a comparison of the picture. That covers `load_text_page`, the
banner, the overlay and the picture together.

Two stores at the top were easy to miss and matter: `text_outline = 0` and
`text_fill = 0x6f` at `0x11041`, which is the colour `load_text_page` then draws
the instructions in. The tail reseeds the level - `[0x2039]` from the runtime's
clock at `0x11488`, which is what `run_level` srand()s, so two goes at one level
differ - and gates a play log on `[0x513]`, which nothing sets.

**`f_1102a` builds two screens, and there is a third before them.** The captures
say which is which: `snap001` is "EPISODE 1" with `duck_count` still 0, so it is
drawn *before* the level loads, by `0x10c06` (1,037 bytes, gated on `[0x507]`).
`snap002` and `snap003` are the same *instructions* screen at two moments - the
wavy title, a stone background and the level's text - and it exists only when the
level carries a `0x44` block (`0x11259`). `snap002` looks nearly empty because
`fade_start_colour = 0x10` keeps the title's low entries out of the fade, so the
banner is at full brightness while the rest is still coming up.

The map screen is the one between them, and no capture has it - it fades in,
holds for a key at `0x11280`, and fades out. Its parts: the picture, the map,
the collected tools at y=0xb4 through `0x7259` (323 bytes, unwritten), and the
status line from `0x0b739` - `"%s: %i  -  %s: %i  -  %s: %i"` with the game's own
words for LEVEL, SCORE and LIVES, centred on its own measured width.

### The episode intro, and the banner that animates

**Read and verified 2026-08-05.** The screen before an episode's first level is
`0x1089b`, not `0x10c06` - the two are alternative branches at the top of
`f_1102a` and `[0x507]` picks between them, with normal play always taking this
one because `[bp+6]` is `g_21a3`, which starting a game sets. It runs only when
`level_attempted` is an episode's `first` and the egg matches.

It keeps its palette in **768 bytes of its own stack**, published with
`set_buffer`, so the colours it loads at 0x20 leave `default_buffer` alone -
**and puts `default_buffer` back at `0x10ab3` before it returns**, which it has
to, because the buffer it published is about to stop existing. `run_level`'s
teardown does the same at `0x0e814`, after a level has been played through
`level_palette`.

So the current buffer is a stack discipline, not one global: each screen
publishes its own and hands the shared one back. The captures show all three -
`snap001` points at a stack address, `snap002` and `snap003` at
`default_buffer` (`d+0x13f1`), `snap004` at `level_palette` (`d+0x0de1`).
Leaving out either restore is not a palette that looks slightly wrong, it is
every later palette write landing in dead stack: the level's tiles, its
background, its solids and the status panel all load through whatever
`current_buffer` says. The
picture is resource **`0x57`:ordinal**; if the egg has none it falls back to two
plain `show_splash` calls.

**`0x103e2` is not a picture, it is an animation.** Each character of the title
gets eight bytes of state - position, speed, amplitude, and which side of y=0x1e
it was on - and starts off-screen, at `-8` per character or `0x5a + 8` per
character when a colour is asked for. Every crossing of that line flips the
amplitude and takes one off it, so the bounce dies away; between crossings the
speed walks toward the amplitude one step a frame. When every character has been
still for four frames in a row it returns, leaving its last frame in a 320x45
image the caller then overlays. That is why the titles bounce in one after
another, and why the caller gets a finished picture and animates nothing itself.

Its `colour` argument doubles as the palette: non-zero and it first derives a
second ramp at entries `0x10`-`0x1f` from the first sixteen, blue scaled by 0.8
and the rest by 1.4. That is why the two banners on the episode screen differ.

**Checked against `snapshots/snap005.snap`: the picture with both banners over it
is the captured screen, 64000 of 64000 pixels.** That covers `0x103e2`,
`image_overlay`, the resource load and the glyph advance in one comparison.

**And it was still wrong, in a way that comparison cannot see.** `0x104bc` calls
`palette_set_black(0)`, and the branch above it lands *on* that call rather than
past it, so it runs whether or not a colour was asked for. Transcribed inside the
`if` instead, the large font's own first colour - a purple - stayed in entry 0,
which is what `image_clear` fills the banner with, so the whole background went
purple behind the letters. The root README records `show_splash` needing exactly
the same call for exactly the same reason.

A pixel comparison is blind to this: the indices written are identical either
way, and only the palette differs. Comparing the **palette buffer** as well
catches it - 255 of 256 entries agree now, and the one that does not is entry
0xff, which nothing writes and which in the guest holds whatever was on its
stack, since that buffer is a stack local.

### 0x10c06, the other branch, is a level picker

**Read 2026-08-05, partly.** `0x10c06` is not a title card. It loads the same
`0x4d`:1 picture, draws four centred strings into it - `menu_text[55]` at y=10,
`menu_text[63]` at 0xa8, the **episode's own name** out of `episode_index[si]` at
0xb2, and `menu_text[64]` at 0xbc - and then draws **one box per level in the
episode** through `0x10abc`, ten to a row, 32 pixels apart, at
`x = 2 + col * 32`, `y = 0x16 + row * 32`, each 0x1a tall. The box colour comes
from a two-entry table at `d+0x204f`, chosen by whether that level is the one
being pointed at.

Then it loops on the mouse: `level_attempted` is set to -1, `input_poll` runs,
the previously highlighted box is redrawn, and the box under the pointer -
`(mouse_y - 0x14) / 0x20` by `mouse_x / 0x20` - becomes the new
`level_attempted`. So the screen the capture shows as "EPISODE 1" is where a
level is chosen, which is why `snap001` has the level set but nothing loaded.

`0x10abc` is read. The hover-and-click half of `0x10c06`, its scene animation and
its exit are not, and neither is written.

Written: `0x0b284`, `0x10ba4`, `0x0b739`, and `0x1081c` - `image_overlay`, which
stamps one image onto another transparent-where-zero and slides by twenty rows in
the 360x240 mode, which is how 320x200 artwork lands right in either resolution.
Not written: `0x10c06` (the EPISODE screen) and `0x103e2` (the text layout), about
2.1 KB between them.

### The helpers, and what was already written

`0x08885` is `image_alloc` and `0x04f4b` is the string reader: both were already
in the reconstruction, and both were on a "still to write" list because that list
was keyed on `symbols.py` names rather than on image offsets. The same mistake as
`make_rect` in `e395416`. Counting by offset, 25 of the 91 under `run_level` are
unwritten, not 32, and `sprite_set_load` (`0x0615a`) is written too - so
`run_level`'s whole setup needs only `0x0d5c5`.

Genuinely unwritten here: `0x088fa` itself, `0x07490` (the stamp), `0x09329`
(the free), and `0x04de6` - `fatal`, which restores text mode, prints
`OH NO: %s (%s)` under `DUCKS fatal error!` and exits 1. The loader's three
failure messages are "Can't find level", "Can't load tiles" and "Can't load
solid object image", each with the id as the parenthesised argument.

### The four fractions are a colour grade

**Answered 2026-08-05**, by `0x0d5c5` - the last unwritten piece of `run_level`'s
setup, and the reason a level drawn without it has black where its numbers and
its cursor should be. It builds the palette a level is played through:

- every one of the first 224 entries is tinted toward the level's three
  fractions (`[0x13e1]`, `[0x13e5]`, `[0x13e9]`) in proportion to the fourth
  (`[0x13ed]`), weighted per entry by the average of its own red and green. So
  the four bytes the level block carries, which the loader stores and nothing
  was seen to read, are a **colour grade**;
- the result goes to a **second palette buffer at `d+0x0de1`**, which
  `set_buffer` then publishes - `snap004` confirms it, its current buffer is
  exactly that address, while the menus use `default_buffer` at `d+0x13f1`;
- `text_fill` gains `0x90` and `text_outline` becomes `0x5b`;
- and entries **`0x50`-`0x6f` are duplicated at `0xe0`-`0xff`**. That is what
  makes the `0x90` colour bias work: sprite pixels are already palette indices
  in the sprite set's own `0x50`-`0x6f` slice - the digit glyphs measure
  `0x52`-`0x65` - so `+0x90` lands on the copy. Without it every sprite drawn
  with a bias indexes an empty part of the palette, which is exactly what a port
  missing this routine shows.
- two level flags override slices outright: `[0x2026]` restores `0x40`-`0x4f`
  and `[0x2028]` restores `0x00`-`0x0f` and takes the top copy from the source.

Written into `game.c` and checked against `snap004`: entries `0xe0`-`0xff` agree
byte for byte. 88 entries still differ, all of them in `0x70`-`0xcf`, and they
are missing from the **source** rather than mis-blended - the two screens
`f_1102a` shows before a level load resources of their own, and until those are
written that part of `default_buffer` is never filled.

## 0x0993b is the collision pass

**Mapped 2026-08-03.** 2,668 bytes, and two nested loops cover all but 96 of
them - the outer from `0x09948`, the inner from `0x09984`. It is scene 0 against
scene 2, every duck against every object, and the body is one large switch on
the object's type.

```
for si in scene 0:                            /* d+0x0d6b */
    if type not in (1, 2, 4, 0x40, 0x41, 0x53):  continue
    for di in scene 2:                        /* d+0x0d83 */
        if |s0[si].x - s2[di].x| >= anim_a[s2[di].type]:  continue
        if |s0[si].y - s2[di].y| >= 3:                    continue
        if s0[si].type == 0:                              continue
        switch (s2[di].type) { ... }          /* 0x38, 0x0b, 6..0x0a, ... */
```

**`anim_a` is the collision half-width.** The table at `d+0x25a` is one of the
six `load_animations` fills, and `dos.h` has carried "read nowhere yet" against
it since it was written. This is where it is read: the horizontal reach of an
object, per type. The vertical test is a constant 3, so the boxes are wide and
flat - which is what a duck walking into something on the same row needs.

Both distance tests are `cdq; xor; sub`, the compiler's absolute value.

The switch is what the game's rules actually are, and it is most of the 2,668
bytes: `entity_set_type` 20 times, `sound_play_guarded` a dozen, and `duck_dies`
once. Reading the arms is the work; the frame around them is settled.

## The order to take it

The event tables and the tool list are filled by the level loader, so reading
that first is what stops the rest being guesswork:

1. **`0x0993b`** (2,668 bytes) and **`0x0a410`** — the level load. Everything
   above indexes what these build.
2. **`0x0d0c8`** (937 bytes) and **`0x0cf07`** (449) — what the events do.
3. The HUD group, `0x0d5c5` (254 bytes) and `0x0d6c3`/`0x0d715`, which draw the
   panel; `draw_number2` beside them is already ported.
4. `run_level()` itself last, when its callees exist.

Two of the four plane loops live inside `0x0d7ee`, so
[open-function-attribution](open-function-attribution.md) and the plane-loop
extents matter here in a way they have not so far.
