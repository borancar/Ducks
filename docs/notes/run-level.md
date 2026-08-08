# run_level(), and the shape of the gameplay

**Started 2026-08-02**, immediately after the `menu-done` tag, when nothing here
was ported. It has been appended to since rather than rewritten, so it reads as a
log: the early sections are what the first reads established and several of them
were later corrected in place. **For where this stands now, read the last dated
section first** - as of 2026-08-07 a demo plays, and six routines a demo reaches
are unwritten (1,350 bytes).

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

## 0x07bb2: gravity, terrain and every entity's behaviour

**Read out about half way, 2026-08-05.** This is the per-entity update, 3,082
bytes, and it is where gravity and terrain collision live. Everything it calls
already exists - `sound_play_guarded`, `scene_add`, `particles_spawn`,
`entity_set_type` (sixteen sites), `duck_dies`, `tool_use`, `message_post`,
`rand` - so it is writable without reading anything else first. It is the last
routine that can be said of.

Its shape is four parts:

1. **A preamble** that sets the fall step from `anim_b[type] - 1` through a
   four-entry jump table at image `0x08762`: 1 gives a step of 2, 2 returns at
   once, 3 decrements y and returns, 4 gives a step of 0. Anything else keeps the
   default of 4.
2. **A dispatch on the type**: `0x36`, `0x1e`, `4`, `1`, `2`, `0x1c`, `0x25`,
   `0x26`, `0x33`, then a twenty-entry table at image `0x0873a` for `0x40`-`0x53`,
   then a default. Twelve of those twenty go straight to the shared code.
3. **The shared walk-and-fall core** at `0x80bb`, which is the answer to "why do
   the ducks not fall".
4. **A second dispatch** at `0x832f`, on the same entity's type again, for what to
   do having moved or having been blocked - with a third jump table at `0x3a74`.

### What the core actually does

The facing comes from `type_flags` bit 0 and `[0xdab]` - which side of the cursor
the flock is on - and an entity with the bit clear has its facing forced to 0. The
speed byte at `+0x15` accelerates by one a frame up to the type's step, and half
of it is the movement amount.

Then the step loop, and it is one loop for walking, climbing and falling: `di`
counts down from the movement amount to **-5**, and at each value it probes
`backdrop.rows[e->y + di][e->x + facing]`. A non-zero byte there is solid, so it
is blocked; a zero means it checks every row from `di - 1` down to 0 is also clear
before committing. On a commit it moves x by the facing - clamped to 0 and
`level_w - 1` - adds `di` to y, stores the speed byte, counts a step in `+0x21`,
and resets `di` to -5. So **gravity is not a velocity at all** - and the sign is
the other way round from how it first reads: **y grows downward**, so it tries the
largest *fall* first, then smaller falls, then level, and only then a *climb* of up
to five pixels. One loop, in that order of preference, for falling off a ledge,
walking along, and stepping up onto something.

`[bp-4]` carries the facing and loses one unit of `e->f14` per pass, so the whole
thing runs once per pixel of intended movement.

### Written, and what it taught

All 3,082 bytes are read and transcribed, along with `0x0d715` - the pass that
runs it over one scene, which `run_level` calls on scenes 0, 1 and 2. Six jump
tables were decoded to do it: `0x08762`, `0x0873a`, `0x08714`, `0x086d4`,
`0x086ba` and the fall-step table.

Two mistakes are worth recording, because both looked right:

- **The no-facing branch falls *into* the step loop, it does not skip it.** Read
  as an else, an entity with no facing never moves - which is every duck on a
  level whose second flag is clear, so nothing fell at all. The two paths meet at
  `0x081f2`, and gravity applies whether or not the entity is walking; with a
  facing of 0 it simply probes its own column.
- Splitting `entity_t`'s `unread2` into the three fields type 2 actually uses
  changed the structure's size on this machine, so the *harness's* ctypes mirror
  of it silently stopped matching and printed nonsense positions. A mirror of a
  structure is a second declaration of it, with all that implies.

Exercised on level 1: duck 0 goes 132, 133, 134, 136, 138, 139 and stops, and the
backdrop column under it is clear at 137-139 and solid at 140 - it is standing on
the terrain, and the 1, 1, 2, 2, 1 spacing is the speed byte ramping to its cap and
then being clipped by the ground. Three other ducks land at their own heights.

### The selection and the tool are two different things

**Found 2026-08-05, from "click action is missing from the demos".** A demo's tool
table moves `tool_at` (`[0x1788]`) and nothing else; `level_event` dispatches on
`tool_type` (`[0x1786]`); and **`0x0e088` is the only place one becomes the other**.
Without it a demo's recorded tool changes are invisible - the selection moves and
every click still acts with the first tool in the list.

It also refuses a selection past the end of the list and puts the old one back,
which is how the played input can walk off the end harmlessly, and it starts the
announcement countdown at `[0x178a]` - `2n + 3` frames for slot n - while the tool
scene's second entity becomes type `0x0f`.

Checked on demo 2, which is level 11 with tools 25 and 12 and one tool event: at
frame **355** the table moves the selection to slot 1 and `tool_type` becomes 12,
the diagonal bridge, with the countdown starting at 4.

The frame's tool section is now written properly around it: the selection is
remembered first (`0x0de7c`), the events only fire **while no tool is in progress**
(`0x0deaa`), the cursor entity's type follows the tool - `0x2a` plus which side the
flock is on for a mirrored one, `0x14` otherwise, `0x16` while one is in use - and a
selection made during a tool is put back at `0x0df24`. On the played path the same
guard reaches `level_event(mouse_x, mouse_y)` when `[0x18e1]` says a button went
down, so clicks will work there as soon as `0x0cf07` is written.

### What ends a level, and what ends a demo

**Read 2026-08-05.** Nothing times a demo out. The frame's back edge at `0x0e7d7`
loops while `[0x1798]` is non-zero, so a level ends when something sets
`fade_direction` to -1 and the fade runs `fade_level` down to 0. Three sites do
that, and only two of them can happen on a demo:

- **`0x0de7f`: any key or any mouse button, but only when the argument says demo.**
  That is attract mode's whole exit - touch anything and it fades out.
- **`0x0de73`: the ducks are gone.** A counter runs while `duck_count` is 0 or
  `[0x2016]` is set, and at **0x20 frames** it sets the outcome to 3, which fades
  out at `0x0df46`. This is not gated on the demo flag, so it ends a demo too: a
  recording whose events have all fired simply plays on until the last duck is
  home or dead, and then bows out half a second later.
- **`0x0df27`: one of the four endings**, outcome 1. Those are **skipped entirely
  for a demo** - `0x0df70` jumps past the whole section when the argument is
  non-zero - so a demo never ends "properly", and `run_level` returns
  `[0x200d] == 2`, which a demo therefore never reports.

Two things that look like a clock and are not: `[0x1ff6]` counts down from `0xa00`
one a frame and `[0x1ff8]` likewise, and both are read only by `collide_scenes` as
`(combo_lo >> 4) + (combo_hi >> 8) + 5` - **a score bonus that decays**, so a duck
brought home early is worth more. And `[0x2003]`, the timer the panel shows, only
plays sound `0x1c` when it reaches zero; it does not end anything by itself.

### 0x0d0c8 and 0x0d471: what a click does, and a demo's input

**Written 2026-08-05.** `0x0d0c8` is the click handler - what a tool does at a
point - and `0x0d471` is the demo's version of the same thing, so writing them
together is what makes a recording play.

`0x0d471` is three words a record: **when the first equals the level clock**, the
other two are a point and `level_event` runs on it. Equality, not "past", so a
record whose frame is skipped never fires. That is the whole of a demo's input, and
it is why a demo needs the level's seed and nothing else.

`0x0d0c8` asks one question first - is the target byte of the backdrop zero - and
most arms refuse on solid ground with sound `0x17`. Then y is incremented, so what
is placed sits a row below what was tested, and `y == 0` becomes 1 before any of
it. The six arms: `0x0d` sends the leader (or says there is none), `0x15` acts on
whatever the pointer picked, `0x12` chooses a new leader out of a type 2 or 4,
`0x50` cashes something in for eight points a duck still out, `0x52` adds a duck,
and everything else puts the tool's own entity down - into scene 5 when
`type_flags` bit 1 is set, otherwise into scene 3 with `[0x1fda]`.

Both are compared against the guest the same way as the physics, and both agree.

### Compared against the guest at last: test_entity.py

**2026-08-05.** `test_entity.py` gives both sides the same level - so the terrain
they probe is identical by construction, which `compare_level.py` already proves -
then walks scenes 0, 1 and 2 entity by entity: the guest's own `0x07bb2` runs from
a hand-built far-call frame with the natives unhooked, the port's C runs on the same
input, and all fifteen fields of the entity are compared. It reuses
`test_gameplay.py`'s `guest_call`, which is what makes the guest side trustworthy.

Four level captures, **0 fields differing**: `snap004` 720, `level-start` 1,035,
`click-cave-1` 780, `teleporter-level` 480. Each entity is run four times - as
found, and lifted 8, 20 and 40 rows so the fall is a long one.

**Two things the sabotage pass established, and one it could not.** Inverting the
terrain test gives 26 differences; making the step counter count twice gives 5. But
**widening the climb limit from five pixels to four is invisible on all four
captures** - and chasing why is what corrected the sign above: a climb only happens
when a fall and level ground are both blocked, so no state here ever needs the
fifth pixel of one. The lifted drops did not help, because they exercise falling,
which is the other end of the same loop.

**It then caught a real bug on a capture the first four did not cover.** On level
11 two ducks came out a pixel apart with the wrong facing, and the cause was one
row: `0x07f2d` reads `rows[y + 1]`, not `rows[y]`. A duck looks at the ground
**under its feet** two pixels either side and drifts toward whichever side is open;
probing its own row put the slope one row too high. Six captures now agree - 540 to
1,035 fields each, 0 differing.

**Three of the faults this harness turned up were in the harness.** The ctypes
mirror of `entity_t` went stale when the structure was split. A first pass reported
"0 differ" over fields nothing had touched, which is why the changed-field count is
printed. And comparing `score` between a freshly loaded level and a played snapshot
said nothing until the scalars `level_event` only adds to were seeded from the guest
first. A comparison is a claim about the comparison as much as the code.

So the physics is compared, the comparison bites, and one boundary inside it is
still unexercised. The state that would reach it is an entity walking into a
five-pixel step, which none of these captures contains.

**Seen working, 2026-08-05**: the monster prowls, turns around when it hits a
wall, and falls under gravity - which is the whole of `the-monster.md`'s claim
demonstrated from the other side. It has no code of its own: `next_type` walks its
type through the four states while the shared core moves it and flips its facing on
a block, and that is all "the thing that eats ducks" is.

**Not compared against the guest.** `tool_use` is also still a stub that complains
once, so tools do nothing where an entity would use one. The demo comparison is
what settles both: `run_level(1)` needs no input and reseeds from the level, so the
guest and the port can be stepped from one snapshot and their entity positions
diffed frame by frame.

### The walk button, and what it turns out to move

**2026-08-07.** "Right click doesn't move the hero." It moved nothing, and the
first cause was not in the game at all: **SDL numbers its mouse buttons LEFT,
MIDDLE, RIGHT and INT 33h numbers them LEFT, RIGHT, MIDDLE**, and `sdl_io.c` was
indexing its counters with `button - 1`. `button_map` holds INT 33h numbers -
that is what the MOUSE BUTTONS screen writes - so out of the box the walk was
being read from the middle button and the tool cycle from the right one. Both
buttons did something, which is why it read as "the hero ignores the right
button" rather than as a dead control. `native.py` had it right (`{1: 0, 3: 1,
2: 2}`); the C did not.

Behind that, three things were missing and two were wrong.

**`0x0970c` and `0x09565` are the flock.** The frame calls `0x0970c` every pass:
it clears every duck's rank, demotes each type 2 back to a loose type 4, and then
chains from the mirrored scene's first entity and from the hero. `0x09565` is the
chain - from the head, repeatedly take the nearest duck not yet taken, give it the
head's facing, `f1a = 8`, a rank in `f19`, a `lead` pointer at `+0x1b`, and make it
the head. The three fields it writes are exactly the three `entity_update`'s type 2
arm reads back, so **without it every follower has `f19 == 0` for ever and the
flock never moves at all**, however the hero is driven. Rebuilding from scratch
each frame is what lets a duck be picked up by walking near it and dropped by
walking away with no bookkeeping anywhere else.

**The frame's update section was collapsed.** Scene 0 does not go through
`0x0d715`: `0x0e156` walks it itself, passing the demo flag as `entity_update`'s
third argument - so in a demo the hero's facing comes from `[0x2100]` and in a
played level from the mouse - and the same loop sums x and y of every duck but the
hero to get the flock average, which `[0xdab]` compares against the cursor and
which the demo camera falls back on. `0x0e2c9` then does scenes 2, 1 and 5, and
`0x0e2ea` is the one call anywhere that passes `applying` as 1. `scene_keep_positions`
runs on five scenes, not one, and `0x0e34b` - the collision pass - was not called.

**Two transcription bugs, both the same trap.** `0x07dec` is *one* 32-bit signed
compare of the hero's x against the cursor, done as `jg/jl` on the high words and
`jae/jbe` on the low ones; it had been read as a comparison of y and then x, which
faces the hero the wrong way whenever the cursor is above or below it. And
`0x07ea2` reads the leader's `+0x0c`, which is **prev_x** - where it was when the
frame began, not where it is now. That is what makes a line of ducks trail rather
than pile up, and reading the live x put every follower a step out and flipped the
facing of any that had caught up.

Both were invisible to the existing comparison, and for the same reason: every
snapshot was taken with the button up, so `active` is 0, and the clear at `0x080ed`
puts `f14` back to 0 before anything can tell. **A comparison over states the input
never varies in cannot see the input.**

### Three more things the walk comparison found

`test_entity.py` grew two sections - the chain, which mirrors the guest's whole
scene 0 (leads translated to this side's addresses) and diffs what `flock_link`
did to it, and the walk, which sets the button and the cursor deliberately and
runs every duck. Both bite: the chain caught `f1a` changed from 8 to 7, and the
walk is what caught the two bugs above.

- **The port was drawing from libc's `rand()`** in `entity_update` and five other
  places, where everything else uses `game_rand` - the Borland LCG the guest has.
  A type 4 duck draws one number a frame, so the two sides diverged at random,
  and it looked like agreement whenever the draws happened to line up. The
  harness now syncs the seed (`d+0x3006` against `rand_seed`) *and compares it
  afterwards*, which checks the count of draws as well as the values - that is
  what then found the port drawing nothing for particles because the harness had
  never given it an array.
- **A duck riding the rocket probes off the top of the level.** The step loop
  tries every `d` from the entity's speed down to -4, so an entity on row 0 reads
  `rows[-1]`; in real mode that is the bytes in front of the row table and the
  game never notices, and here it is the segfault the rocket launch produced.
  `terrain_at` bounds it and reads outside the level as empty. What the guest
  reads there is a property of its heap, so this cannot be matched, only chosen.
- **A snapshot taken between levels keeps its scene pointers**, and what they
  point at is freed DOS memory - `level1-bonus` reads `0x81818181` for every
  field. The guest survives that and the port does not, so the harness checks the
  state is a live level before comparing rather than crashing on it.

**Still open, and pre-existing**: on level 4 the port's backdrop differs from the
guest's in 2 of 320 rows, and the bytes there vary run to run, so they are memory
the loader never wrote. `compare_level.py` has always reported it; it shows up
here as `click-castle` differing on ducks standing in those rows. It is a level
loader question, not a walking one, and the harness now says so rather than
letting it look like a physics difference.

## The demos load, so the idle menu plays one

**Written 2026-08-05**: `0x1240f` (`load_demo`), `0x126db` (`pick_random_demo`)
and `0x11013` (the clock seed). The menu's idle branch had been reaching a stub
and printing DEMO MISSING.

**A demo is not a recording of the mouse.** It is a seed, a level number, and
three tables of events - which is why replaying one needs nothing else: the level
plays itself out of those tables, and because `run_level` `srand()`s the demo's own
seed, `rand()` lands in the same places both times.

| table | records | count | who walks it |
| --- | --- | --- | --- |
| `[0x203f]` | six bytes | `[0x2049]` | `0x0d471`, the demo's event fork |
| `[0x203b]` | three bytes | `[0x2047]` | `0x0d4c2`, the tool changes |
| `[0x2043]` | three bytes | `[0x204b]` | the level script the frame advances |

Each record's fields come out of the file in a different order from the record:
the two words read first go to `+2` and `+4`, and the one read last to `+0`.

The six demos in this egg are levels 36, 63, 11, 49 and two more, with event
counts from 2 to 11 and script counts up to 14. The block is `0x52`, and it starts
with two strings: an empty first one means the demo belongs to the egg it was found
in, otherwise it names an egg by id and `find_egg_by_id` goes looking.

**Three more duplicate declarations came out of extending the sweep** to lines that
declare several names at once, which is how `buf_203b` had been hiding as a second
name for `tool_event_table`:

- `g_da1` is **`scenes[5].count`** - whether the level has any mirrored entities,
  which is what the setup's two ending flags and one of the four endings test.
- `g_18f5` and `scroll_shift` were the same byte; so were `g_2036` and `score`.

That makes nine of these in one day, all of one shape.

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

Three more of the frame's parts are written, and they were the ones whose
dependencies already existed:

- **`0x0981b`, the retire pass**, and it is the only place a scene's entity array
  ever shrinks. An entity is dead when its type is 0. Compaction takes one of two
  shapes on the scene's `+6`: set, the survivors shuffle down and keep their order;
  clear, the last entity is swapped into the hole. So **`+6` is not "unread" - it
  is "keep the order"**, and the loader sets it on scene 2. The `flag` field
  follows what it pointed at: `0xff` when that entity is the one dying, and moved
  to the hole when the swap brings the last entity in - which is the hero index for
  scene 0 and what is being eaten for scene 2. An entity that lives gets its
  animation restarted if its type changed since last frame, which is what the copy
  of the type at `+0x27` is for.
- **`0x0af95`**, which is which entity the pointer is over: nearest in the taxicab
  sense with the pointer taken six pixels lower, and only within twelve. With its
  second argument set it also moves scene 3's single entity onto the winner as type
  `0x11` - and **`[0x0d89]` is `scenes[3].count`**, so the highlight is hidden by
  having no entities rather than by a flag.
- **`0x0d4fc`**, one call a frame, which picks *which* scene that runs on from the
  tool in hand: the ducks for `0x12`, the objects for `0x50`, the mirrored-entity
  scene for `0x15` while no tool is in progress.

Exercised rather than verified: killing scene 0's third duck on level 1 takes the
count from 11 to 10, swaps the last entity into the hole, and moves the hero index
from 10 to 2 because the hero *was* the last one; killing the entity the flag
points at clears it to `0xff`. There is no native for any of the three, so
`test_gameplay.py` cannot compare them against the guest - they rest on one
reading.

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

### The panel numbers, and why the duck count froze

**2026-08-07.** Reported: on level 1 ducks fall off the bottom, the death sound
plays, and the counter on the panel does not move.

The counting was right and the drawing was missing. `duck_dies` decrements
`duck_count` *before* it plays its sound, and the fall-off-the-world path at
`0x08565` does the same - `sound_play_guarded(7, 1)` then `duck_count--` - so an
audible death is proof the counter moved. Driving the port's own `level_load` and
`duck_dies` on level 1 confirms it: 11 to 10.

What was absent is the whole of `0x0e485`-`0x0e717`. The frame composed, drew the
scenes and flipped, and nothing else: no `draw_number`, so the two panel numbers
kept whatever the setup drew into the two pages at level start and never changed
again.

The two numbers are not kept the same way, and the asymmetry is the original's:

- the **score** chases its target, a quarter of the gap plus one a frame, and
  what is drawn is that rolling copy - which is why a score rolls up rather than
  jumping. Its redraw flag starts at 2 and is decremented every frame at
  `0x0e711`, so it stops redrawing two frames after the score settles.
- the **duck count** does not chase. `shown_ducks` exists only to notice a change,
  and what `draw_number` is given is the live counter. **Nothing anywhere
  decrements its flag** - it is set to 2 in the setup and set to 2 again on every
  change, so from the first frame of a level that number is redrawn every frame
  for ever.

Written with it, since they are the same section: the half-rate frame skip
(`[bp-0x20]` toggling, which draws nothing at all on the off beat when the played
tool handler has set `[bp-0x12]`), the three message countdowns, the particles,
the message overlays, and the timer bar - five pixels at
`screen_height - 7 - level_timer`.

The **sparkle** is the odd one. `level_flags[3]` enables it, and its column
starts at **screen row 0** rather than at the top of the play area: the address is
the page base plus `x >> 2` with no row term, and only the *count* of rows comes
from the viewport. Reproduced rather than tidied. Its two `rand()` draws had to be
sequenced by hand - C does not order the operands of a subtraction, and those
draws move the shared seed, so the wrong order changes every later `rand()` in the
frame.

One thing corrected on the way: `tool_selected` was being passed `tool_at`, and
`0x0e0e0` passes `[bp-0x12]` - the played handler's out-parameter, which is 0 for
a demo. So the announcement is three frames, not `2 * slot + 3`.

**Verified in halves, and only the first half is checked.** The counting is:
`duck_dies` on a freshly loaded level 1 takes `duck_count` from 11 to 10. The
drawing is a transcription of `0x0e485`-`0x0e717` that compiles and breaks no
existing comparison, but the port's framebuffer is `static` in `sdl_io.c`, so
nothing here can read back what `draw_number` put on the panel. **No harness in
this repository has ever compared the port's screen against anything.** That is
the gap this sat in.

### The four endings, and the one the panel makes visible

**2026-08-07.** Reported after the counter was fixed: the check for not having
enough ducks left is missing. It is the third of four at `0x0df5d`, and none of
them was written.

The strings say what they are, read out of `menu_text` in a snapshot rather than
guessed - the array is a far pointer at `0x1894:0000`, which is `dgroup - 0x10`,
and the offsets in the code are byte offsets into it, so `+0x130` is entry 76:

| | | |
| --- | --- | --- |
| 76 | "The leader's gone!" | hero index `0xff` and `can_finish` |
| 77 | "Nothing left to follow!" | hero gone, `scenes[5].count` now 0, `can_finish_alt` |
| 78 | **"Not enough ducks left!"** | `quota_left > duck_count`, unsigned |
| 79 | "Lost a rocket!" | `g_2018` |
| 80 | "Press ESCAPE to abort this attempt" | the second half of all four |

**They only say so.** Nothing in the block clears `level_running` or moves the
outcome, so the level carries on and the player is the one who decides to give up
- which is what the shared second line is for. That is why "the four endings" is
the wrong name for them; they are the four ways an attempt becomes *unwinnable*.

`ending_said` (`[bp-0x22]`) is a frame local cleared once in the setup, so a level
posts at most one of these however long it runs. The four are sequential `if`s
rather than a chain, so two can fire on the same frame. Skipped entirely for a
demo, and while `g_2016` or `g_1ffe` say the level is already over.

The second one is worth spelling out because its two halves look contradictory:
`can_finish_alt` was only set at setup when `scenes[5].count` was non-zero, and
the check needs that count to be zero *now* - so it means "the level had mirrored
entities and has run out of them".

**Checked by driving it.** Level 1 loads with `duck_count` 11 and `quota_left` 5;
killing ducks one at a time through the port's own `duck_dies`, the condition
first holds after the seventh - 4 left against 5 still needed. The setup adds one
to the quota when the level has a hero, so in play it is 6 and 5.

### The rocket, and the switch inside animate_scene

**2026-08-07.** Reported: the rocket does not fill up or fly away. It was one
`TODO` - `0x0a58e`-`0x0a7ed`, a switch on the entity's type that `animate_scene`
runs when a script reaches its end, before the `next_type` default. The comment
against it said "none of which any menu entity reaches", which was true and is
exactly why it stayed unwritten while the menus were the whole port. A good deal
of the game's behaviour is in there.

**The rocket is a count worn as a type.** `param` is how many ducks it still
wants, and the type it shows is `param + 5` - so `6` to `0x0a` are "five more"
down to "one more", and those are the types `collide_scenes` collides a duck
with. The cycle is three-cornered:

1. a duck arrives - `collide_scenes` sets the rocket to `0x1a` and takes one off
   `param`;
2. `0x1a` here gives it an upward nudge (`f15 = 0xfc`), and `next_type` walks it
   to `0x1b`;
3. `0x1b` puts `param + 5` back on, which is the next sprite down.

When `param` reaches zero it goes instead: `scenery_count` - the rockets the
loader counted at `0x2000` - comes down by one, and **if that was the last one
the level is won**, `level_outcome = 1`, which the frame at `0x0df27` turns into
the 2 `run_level` returns. The rocket becomes type 5, posts `menu_text[44]`
("Rocket launched!"), adds 25 and plays sound `0x0c`.

The type each arm sets survives the default only because `next_type` is the
identity for 5 and for 6..0x0a. That is not a coincidence to rely on quietly, so
it is written down where the arm is.

**Checked by driving it**: on level 1 the rocket starts type `0x0a` with
`param` 5, and feeding it ducks one at a time through the port's own
`entity_set_type`/`animate_scene` walks the type `0xa, 9, 8, 7, 6` and then to 5
on the fifth, with `scenery_count` 1 to 0, `level_outcome` 1 and the score up 25.

Written with it, since a switch is one thing: `0x54` faces either way at random,
`0x46`/`0x47` face left and right, `0x2f` swaps the mirrored pair, `0x20` is the
teleporter's far end (the point `collide_scenes`' `0x37` arm stashed), `0x23`
rises ten, `0x24` is drowning, and `0x4e` is the other way a level is won.

`0x24` is the one with a subtlety: `duck_dies` has already set the type to 3, so
the default must not overwrite it - and the original expresses that by loading
`di` from `g_509`. When ducks do not die `duck_dies` did nothing, and the type
does still have to move on. That is what `chain` is.

### What an outcome does, and the gate that stops a level reacting

**2026-08-07.** The rocket launched and set `level_outcome = 1`, and the level
carried on - because nothing read it. The frame had a branch for outcome 3, the
"ran out of ducks" one, and none for 1.

`0x0df27` is the only place a level ends of its own accord:

| | | |
| --- | --- | --- |
| **1** | a win - the rocket's last duck, or a `0x4e` | alone moves the outcome on to **2**, which is what `run_level` returns |
| **3** | out of ducks | leaves it at 3, so `run_level` returns 0 |

Both then clear `can_finish`, start the fade, and clear **`running`**
(`[bp-0x10]`), which was the other half missing. That local gates everything from
the ducks counter through the tool and the input: `0x0de53` jumps straight to
`0x0df27` when it is clear. So a level that has ended stops reacting - no spawns,
no clicks, no tool - and only the fade and the drawing play out. Without it a
finished level would keep taking input all the way through the fade.

The four "unwinnable" messages are *outside* that gate, which is consistent with
what they are: they are not endings, and they keep being checked.

**Two mistakes on the way, one of which compiled.** The gate was first written as
a `goto` matching the original's jump - but the label had to sit before the
outcome check and the tool section comes *after* it in the C, so the jump ran
backwards and would have looped for ever. It built cleanly. Reordering the block
to the original's own order - counter, tool, input, outcome, endings,
`tool_selected` - removes the need for a jump at all, and is what the original
does anyway.

**Checked by driving it**: five ducks into level 1's rocket leaves type 5,
`scenery_count` 0 and `level_outcome` 1, which the frame's new branch turns into
the 2 `run_level` returns.

### The bonus screen, and a row that pours into another

**2026-08-07.** Reported: after the level-complete screen there is no score
screen. `game_main` calls `f_0becb` there and that was a `{ }` stub. Behind it
were four functions and about 1,500 bytes.

The five labels come out of `menu_text` 66-70 - "Time bonus:", "Survivors
bonus:", "Lives bonus:", "Total:", "Score:" - and the five counters behind them
are a table at `d+0x2159`. **Row 4 is the score**: `[0x2161]` is
`bonus_row[4]`, the score is parked there before the screen runs and read back
out afterwards, so the last row is not a copy of the score but the score itself,
counting up in front of you.

`tally_row` (`0x0bba1`) is one row, and it has two shapes:

- given a **number**, it adds it to the row;
- given another **row**, that row empties as this one fills, and both are drawn
  every frame - which is what makes a bonus visibly pour into the total.

`bonus_tally` (`0x0bdee`) is seven of those in the order they are watched: the
three bonuses count up out of nothing (`level_timer * 5`, `duck_count * 10`,
`lives * 10`), then each pours into Total, then Total pours into Score.

Two details worth keeping. The step is a sixteenth of what is left **plus one**,
so a row starts fast and eases in - and the `gap` argument is re-armed on every
frame that moves, which means it is not a speed at all but the pause *after* the
row lands. And the ticking is sound `0x12` spaced by the size of the step, so the
sound thins out as a row slows.

`skipped` is a pointer shared by all seven passes, so a key or a button does not
skip one row: every later row sees it set and finishes instantly.

`bonus_numbers` (`0x0bd00`) draws each digit twice - sprite `0x70` to wipe the
cell, then `0x71 + digit` over it - into both pages, five rows of six digits.

**The labels needed one line somewhere else.** The bonus screen sets no text
colour at all - it inherits - and `level_palette_build` adds `0x90` to
`text_colour[0]` while `run_level`'s teardown at `0x0e81a` adds `0x70`. Together
they are `0x100`, so the pair cancels and text comes back to where it started.
The port had the first and not the second, so every label was drawn `0x90` up the
palette, into the slice a level uses and the screens after it do not, and none of
them appeared. `0x6f` to `0xff` and back to `0x6f`.

That is the third time a palette bias has hidden something rather than corrupted
it - the same shape as the missing `0xe0`-`0xff` copy that made the HUD numbers
invisible. A colour that lands outside the palette in use does not look wrong, it
looks absent.

**Checked by replaying the arithmetic**: 27 on the clock, 7 ducks home, 5 lives
and a score of 1234 gives 135 + 70 + 50, and Total pours 255 into Score for 1489.
Every pass terminates - 43, 33, 29, 43, 33, 29, 52 steps - which was the thing
worth checking, because a negative amount would step by zero within sixteen of
its target and never arrive. The counts cannot be negative and `level_timer`
stops at 0, so nothing here reaches that; the original has the same shape.

### The bomb, and the tool that had never been written

**2026-08-07.** Reported: on level 2 the bombs drop and do nothing when they
land. Level 2's only tool is `0x18`, and `tool_names[anim_c[0x18]]` is literally
"Bomb".

The chain runs like this, and only the last link was missing. A click reaches
`level_event`, whose default arm puts an entity of the tool's own type into scene
3 and raises `[0x1fda]`. That entity falls, because `anim_b[0x18]` is 0 and so it
takes no early exit from `entity_update`'s movement switch. When it lands,
`entity_update`'s `blocked` handler sees `applying` - the argument the frame
passes as 1 for exactly that scene - and calls **`tool_use`**, which was a stub
that complained once. So the bomb dropped, landed, and told stderr.

`tool_use` (`0x07a36`) has four arms and the bomb's is `0x18`/`0x36`: sound 9,
leave the thing itself in scene 1 as a type `0x17`, and call **`blast_terrain`**
(`0x0751b`), which was one of the six unwritten routines.

**`blast_terrain` is the hole.** It walks a sprite over the backdrop and writes 0
wherever the sprite has a pixel - so the shape of the blast *is* the shape of the
sprite. The sprite is `anim_script[0x17][0]`, read through a fixed address, so it
is the bomb's own first frame whichever tool placed it. "Terrain" is just the
backdrop image the compositor draws and the physics probes, so erasing it is the
whole of the damage.

Then **every solid is stamped back in**. A bomb may not destroy the level's
scenery, and rather than test for that while erasing, the original simply puts
all of it back afterwards.

**Checked by driving it**: level 2's backdrop is 400x160 with 15,591 solid
pixels; one `tool_use(x, y, 0x18)` on the ground leaves 15,383 - 208 erased - and
a type `0x17` in scene 1.

Two of the four arms are still stubs and say so: `0x0739c`, the counterpart that
stamps a sprite *into* the backdrop, which is how the bridges and the brick build
terrain, and `0x0799c`, a bridge's footing check.

### The bridges, and a stub that lied about why it existed

**2026-08-07.** Reported: on level 3 the bridge draws nothing, and the stub said
*"it was stubbed on the understanding that a demo never gets here, so that is
wrong and the demo is no longer faithful"* - which was false twice. It was left
out because nobody had read it, not because of any belief about demos, and there
are demos with bridges. **A diagnostic that misstates its own reason is worse
than none**, so `stubs.c` now has two helpers: `unwritten` says what will not
happen, and `wrong_about_demos` says a belief has been disproved. Only `0x0cf07`
still deserves the second.

**A bridge is two ends walking apart.** `tool_use`'s `0x0c`/`0x19` arm puts both
at the click and marks them alive; `tool_step` (`0x078a6`), one line of the frame
after `input_poll`, grows it while a tool is in progress; `bridge_grow`
(`0x076e2`) steps both ends and lays a sprite where each one *was*; and
`bridge_step_end` (`0x07646`) walks one end `bridge_span` pixels - 4 level, 3
sloping - stopping it dead the moment it is over solid backdrop, with sound
`0x11`. So a bridge stops on the side that hit something while the other carries
on, and there is no bridge object anywhere: it is backdrop, the same as the
ground.

**Each segment's sprite is chosen at random from the level's own set**, which is
what stops a bridge being a row of identical tiles.

`stamp_sprite_into` (`0x0739c`) is `blast_terrain`'s twin, and they differ in
three places: it writes the sprite's pixel where the other writes 0, it clips
against the destination's size rather than the level's, and it takes a pointer
rather than an index. One carves terrain away, the other builds it.

`ground_check` (`0x0799c`) turns out not to be a footing check at all - it is the
anti-stacking warning. It counts non-solid pixels down the column and complains
if it took more than 28 rows to find sixteen, and the three messages escalate:
"Careful... don't stack bridges...", "THIS IS YOUR LAST WARNING! NO BRIDGE
STACKING!", "Oops, how did that happen?". The third calls `0x07955`, which is
now read - see below. It takes x by pointer and never writes through it.

**Checked by driving it**: level 3, a bridge placed over a gap finishes in 8
frames and leaves 148 pixels of new terrain, and stops itself rather than running
on. The first attempt divided by zero - `level_sprites.count` is 0 until
`run_level`'s setup loads the level's sprite set, which the probe was not doing.

With these, **every routine a demo reaches is written**: 98 of 98.

### The balloon, and `sar` is not `/ 2`

**2026-08-07.** Reported: the balloon does not rise. It is `entity_update`'s
`0x36` arm, which sets `f15` to `-2` every frame, and the increment just below
takes it to `-1` before the speed is worked out. The original does that with
`sar ax, 1` at `0x08117`; the port had `/ 2`.

**Those differ on every odd negative.** `sar` floors, so `-1` halves to `-1`; C
truncates toward zero, so `-1 / 2` is `0`. The balloon's speed was therefore
exactly zero and it hung in the air. Every even value agrees, which is why
everything that *falls* looked right and only the one thing that rises at a
single pixel a frame was broken.

`test_entity` compares fifteen fields of every entity against the guest over six
captures and did not catch it, for the reason it always says out loud: none of
those states has an entity with an odd negative speed. Fixed and driven: a
balloon at y=120 now walks 119, 118, 117, 116, 115 and stops on terrain.

### The sound, and what has been ruled out

Reported alongside it: level 3 and SQUEAKY VOICES sound broken, like a wrong
sample rate. One real error was found and fixed, and it is small:

**The card cannot play an arbitrary rate and the original never asks it to.** It
programs a DSP time constant of `256 - 1000000/rate`, and the hardware then runs
at `1000000/(256 - tc)`. The game asks for **11000** and the card therefore plays
at **11111**. The port was handing SDL the 11000 the game asked for, so
everything ran 1% flat. `sound_init` now rounds through the time constant the
same way.

1% is not what "broken" usually means, so the rest of the path was checked and is
correct: the samples decode at the right length (45,818 bytes for `0x4b`, not
truncated by the `int16_t` count), the mixer consumes exactly one sample per
output frame, the volume scaling agrees with the original's `shr` (the low byte
of a logical and an arithmetic shift are the same here), and SDL reports it is
feeding 11111 Hz 8-bit mono into a 44100 Hz device - so it is resampling rather
than playing the data at the device's rate.

`audio_open` now logs the negotiated format, because "SDL quietly did not
resample" is the one failure that would produce exactly this symptom and cannot
be seen from outside.

### Changing tools while playing

**2026-08-07.** Reported: on a level with several tools the arrow keys do
nothing. `0x0cf07` is a played level's whole input and it was the last real stub -
and worse, nothing called it: when the frame was restructured for the outcome
gate, the `else` that reaches it was dropped, so a played level had no key handler
at all.

It is `tool_events`' counterpart. That one moves the selection from a demo's
table; this one moves it from the keyboard and the mouse, and `run_level` calls
exactly one of the two.

**Three ways to change tool**, none of them bounds-checked:

| | |
| --- | --- |
| the cycle button | `button_map[1]`, steps on and wraps at the end |
| left / right arrow | `tool_at--` / `tool_at++`, no wrap |
| digits 1-9 | `tool_at = key - '1'` |

They do not need checking because `tool_selected` refuses a selection past the
end of the list and puts the old one back - which is the same mechanism that lets
a demo's table name a slot the level does not have.

Extended keys arrive as `0x100 | scan code`: `input_poll` folds the BIOS's
zero-then-scancode pair into that, so left is `0x14b` and right `0x14d`. That was
already right; there was simply nothing reading it.

The rest of the switch is the display and debug keys - `,`/`.` walk the gamma and
rebuild the palette, `[`/`]` the game speed, `c` toggles the camera between easing
and the hard window, `D` doubles the sample rate, `#` finishes the level and `P`
pauses. The last two are behind `cheat_state[0]` and `cheat_state[5]`.

`fast`, the caller's local, is **not** a tool slot - `D` toggles it, and it both
doubles the sound rate and lengthens the tool announcement, which is why
`tool_selected` is handed it. An earlier note here called it `tool_slot`.

Checked on level 10, which has three: right, right, right gives 0, 1, 2, 2 - the
fourth refused - then left gives 1, `3` gives 2, `1` gives 0, and left from 0 is
refused.

**The highlight is a scene, and it had nothing moving it.** The panel's tool
icons are drawn once into each page at level start and never again, so what marks
the selected one is the *tool scene* - two entities the setup puts at
`0x82`, drawn over the panel every frame. `0x0e71e` is the only thing that ever
moves them, and it was unwritten: the announcement countdown reaching **1** - not
0 - names the tool in a message and parks both entities at `tool_at * 16 + 0x82`,
which is the same spacing the HUD drew the icons at. Entity 0 becomes type `0x10`,
the box; entity 1 the tool's own type, having been `0x0f` for the length of the
countdown, which is what makes a new tool flash before it settles.

Without it both sat over slot 0 for the whole level - so the first tool was
covered and the highlight never followed the selection, which is exactly how it
was reported. It sits outside the frame skip, because `0x0e4c4` jumps to it.

Checked: on level 10 the highlight walks `0x82`, `0x92`, `0xa2` and back, and
entity 1 carries `0x12`, `0x19`, `0x18` with it.

**And then the other tools vanished, which was one wrong entity index.** The two
entities are the box and the icon, and `0x0e0ea` pushes `[0x1794]` - **entity
zero**, the box - when it sets type `0x0f` for the length of the countdown. This
had entity *one*.

Why that matters is the whole design of the thing. Because the panel is painted
once at level start, the pair sitting on the old slot has to keep drawing what
was already there, and it does: entity 1 still carries the *old* tool's type, so
for those three frames it redraws exactly the icon the HUD put there. Only the
box is hidden. Then both move.

Blanking entity 1 instead paints `0x0f` - sprite 42, the empty slot - over the
old icon for three frames, and the pair then leaves the hole behind for good.
Nothing repaints the panel, so it is permanent. That is "changing the tool clears
the other tools".

So `0x0f` is not "the tool is arriving", it is "hide the box while it does".

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


## ESC does not fade the level out (0x07955)

The port had this in the frame:

```c
if (!demo && last_key == 0x1b)      /* invented */
    fade_direction = -1;
```

which was written to have *some* way off a played level while the endings were
unwired, and then stayed. There is no `cmp last_key, 0x1b` anywhere in
`run_level`; the only read of `[0x18f6]` in the whole 2,830 bytes is 0x0de8c,
which is the demo's exit. ESC belongs to `played_tool_events`, and it does this:

```
case 0x1b: case 0x51: case 0x71:    /* ESC, Q, q */
    kill_all_ducks();               /* 0x0cffe -> 0x07955 */
```

**0x07955 is not a bridge routine.** 71 bytes:

```c
void far kill_all_ducks(void)
{
    int16_t i;
    for (i = 0; i < scenes[0].count; i++)
        duck_dies(&scenes[0].entities[i], 1, 0);   /* force set, silent */
    if (!g_2016)
        sound_play_guarded(5, 1);
    g_2016 = 1;
}
```

Every duck in the flock through `duck_dies` with `force` set - forty particles
each and the type set to 3 - so the flock blows up where it stands. Nothing in
it ends the level. What ends the level is `[0x2016]`, which the frame already
reads: `if (duck_count == 0 || g_2016) over++;` and outcome 3 at `over >= 0x20`.
Those 32 frames are the delay the explosion plays out in, and the fade the port
was doing directly is what skipped it.

`[0x2016]` is written in exactly two places in the image - 1 here at 0x07993,
and 0 at 0x0db5c in `run_level`'s setup - so it is per-attempt, and a second ESC
in the same attempt is silent because the guard suppresses the sound and every
duck is already type 3.

Driven through `libducks.so` on level 1: 11 ducks, 11 alive, 0 particles ->
0 ducks, 0 alive, **440 particles** (11 x 40), `g_2016` 1. A second call adds
nothing. The pool has to be stood up by the harness first; `particle_cap` is 0
until `run_level` allocates it, and a spawn against a zero cap falls through
silently, which is what made the first run report no particles at all.

### The clock, and the third caller (0x0ddfe)

The three call sites are 0x0cffe (ESC), 0x07a22 (the third stacking warning) and
0x0de2e, which sits in a block the port did not have at all:

```c
if (level_timer) {                  /* 0x2003 */
    if (tick) tick--;
    else {
        tick = timer_period;        /* 0x2001 */
        if (--level_timer == 0)
            sound_play_guarded(0x1c, 1);
    }
} else if (!g_2016 && g_1ffe) {     /* 0x0de2e */
    kill_all_ducks();
}
```

So the level clock was never running: `run_level` set `level_timer = 0x1b` and
nothing took it down, which is why the time bonus was always the full 27 x 5.

The `else` reads oddly until `[0x1ffe]` is pinned down. 0x0d876 is
`g_1ffe = g_1ffc; g_1ffc = 0;` - the secret-level flag moved off the pending one
as the level starts - and 0x13895 skips the bonus screen when either is set
while 0x13a48 sends the play loop straight round again. So `[0x1ffe]` means
*this level is the secret one*, and the branch is: an ordinary level's clock
stops at zero and is worth five points a tick, a secret level's clock running
out blows the flock up. That is what makes those levels timed.

### Why the explosion was invisible: 0x0a956 was never written

ESC blew the flock up and nothing appeared to happen, because the particles were
spawned and then frozen. `particles_spawn` fills in `vx`, `vy`, `f0d` and `f0e`,
and in the port **nothing read any of the four** - the pool only ever grew, every
dot sitting at the pixel its duck died on until the level ended.

The step is `0x0a956`, 435 bytes, called once a frame from `0x0e482` between
`animate_scene(&tool_scene)` and the score chase. It was in run_level's list of
six unwritten routines and was never taken off it - not stubbed, not called, so
nothing said so at runtime. What it does:

```c
p->x += p->vx;  p->y += p->vy;  p->vy++;          /* gravity */
if ((uint32_t) p->x > level_w * 8 ||
    (uint32_t) p->y > level_h * 8) { retire; i--; continue; }
if (!terrain_at(p->y >> 3, p->x >> 3)) continue;  /* still in the air */
if (!p->f0e) { retire; continue; }                /* and no i-- - see below */
if (settings[1]) backdrop.rows[py][px] = p->colour;   /* FLYING BLOOD */
p->vy = 8;
if (--p->f0d == 0) { retire; i--; }
```

Three things worth keeping:

- **Both bounds tests are unsigned** (`jb` at 0x0a9ec, `ja` at 0x0aa16). That is
  how off-the-left and off-the-top are caught: a negative coordinate is a huge
  unsigned one, so one compare against the far edge covers both sides. Writing
  it as two signed tests would be tidier and wrong.
- **Terrain is a hit, not a wall.** The particle is not stopped; `vy` is forced
  to 8 and its life comes down by one. `f0d` is 1 or 2 from the spawner, so a
  particle survives one or two landings. What it leaves is the stain - its own
  colour written into the terrain - which is what FLYING BLOOD means, and the
  same setting gates the draw at 0x0e4fd, so with it off there is nothing to see
  at all.
- **The `f0e == 0` retire at 0x0aaf3 does not step `i` back**, where the other
  two do, so the particle swapped into the hole is skipped for a frame. Kept.

`particle_retire` (0x0a85f) is swap-with-last: the last record is copied over the
retired one and the count comes down, which is why the draw can be a flat walk
and why the two arms that retire have to step `i` back.

**Checked against the guest**, `test_particles.py`: 400 made-up pools - random
level sizes, random terrain, coordinates deliberately overshooting both axes,
FLYING BLOOD on and off - one frame each, comparing the count, every surviving
record and the whole terrain. Byte-identical. The retire tests being `>` and not
`>=` means a particle exactly on the far edge indexes one past its row, so the
harness has to give each guest row the 0x01 header byte a farmalloc'd one has, or
the two sides disagree there and nowhere else.

**And end to end**: ESC on `snapshots/snap011.snap` in the guest takes the pool
from 0 to 440 - eleven ducks at forty particles - and then down 434, 408, 315,
161, 40 as they land and expire. The port spawns the same 440.

### The episode intro is not part of a retry (0x11058)

`level_screens` was calling `episode_intro` unconditionally, so abandoning a
level and trying it again replayed the whole episode introduction. The original
gates it:

```
0x1104b  cmp word ptr [0x507], 0     ; the cheat's level picker?
0x11050  je   0x11058
0x11053  call 0x10c06                ;   that instead - not written here
0x11056  jmp  0x11062
0x11058  cmp word ptr [bp + 6], 0    ; the argument
0x1105c  je   0x11062                ;   zero: straight past the intro
0x1105e  call 0x1089b <episode_intro>
```

The argument is `game_main`'s `[0x21a3]`, which `run_level`'s failure path clears
at 0x139b2 and a completed level sets at 0x1389f. So it means *this is not a
retry* - the same bit `menus_resume` reads to choose between PLAY NEXT LEVEL and
RETRY LEVEL - and the parameter is now called `fresh` rather than `demo`, which
is what it had been named here and never was.

`[bp+6]` is written once more, at 0x112a9 in the level-select branch, and that
store is dead: the intro decision is a hundred instructions behind it. Kept
anyway, because it is the original's.

## A demo has two tables, and the frame drives both (0x0dcc9, 0x0ddbc)

The attract mode ran a level with gravity and monsters in it and no player. Three
things were missing, all at the top of `run_level`'s frame, and they come apart
cleanly:

**`level_clock` was never incremented.** `run_level`'s setup sets `[0x201a]` to
0 and `0x0dcd8` is the only `inc` in the image - the port had the store and not
the increment. Both demo input sources fire a record when its frame number
*equals* the clock (`0x0d489` in `demo_events`, `0x0d4d9` in `tool_events`), so
a clock stuck at 0 fires nothing. Every demo's earliest record is well past 0:

```
demo 0: level 36, seed   0,  2 clicks, 0 tool events   frames [48, 80]
demo 1: level 63, seed 117,  3 clicks, 0 tool events   frames [233, 371, 470]
demo 2: level 11, seed  31,  2 clicks, 1 tool event    frames [38, 398]
demo 3: level 49, seed  80, 11 clicks, 6 tool events   frames [108, 133, 157, ...]
```

**The walking is a different table.** `event_table` is the clicks - the tool -
and `[0x2043]` is the hero: three bytes a record, a frame and a heading, walked
by the cursor at `[0x204d]`. When the record under the cursor names this frame
the cursor moves on, and the record it lands on gives the heading, which
`entity_update`'s type 1 arm reads out of `[0x2100]` as the hero's `f14` - but
only when `scripted` is set, so it is demo input and nothing else. `load_demo`
was filling the table and zeroing the cursor, and nothing moved it. Level 11's
demo, run through the block:

```
script: [(54,0) (225,-1) (271,0) (305,-1) (440,0) (790,1) (820,0) (839,-1) (854,0)]
  frame    1: heading +0     frame  440: heading +1
  frame   54: heading -1     frame  790: heading +0
  frame  225: heading +0     frame  820: heading -1
  frame  271: heading -1     frame  839: heading +0
  frame  305: heading +0
```

Headings are -1, 0, +1, stored plus five - a player holding left or right.

The second lookup is **not** bounds-checked: `0x0ddc3` jumps straight into it, so
once the cursor reaches the end the game reads one record past a farmalloc'd
block. Measured across six snapshots the byte it lands on is 02, 00, 00, 00, 03,
00 - the next allocation's contents, not a header, so unlike `terrain_at` there
is nothing to match, and `menu-halloffame-2.snap` has `script_at == script_count`
so the original does get there. Holding the last record is a choice, stated as
one in the comment; every demo in the egg ends on heading 0, so the hero stops.

`game_main` frees all three demo tables and leaves the counts standing and the
pointers dangling. Harmless in real mode, a use-after-free here, so the port
nulls them - nothing outside a demo reads them and every demo reloads them.

**The frame's first instruction is a draw.** `0x0dcce` is `rand() & 0x7f`, and
one frame in 128 it calls `0x1462:0x215` - the random ambience, one preloaded
sound on voice 2 if that voice is free. That routine is also the answer to a
question `sound.c` had left open: it is what reads the watermark at `d+0x2909`,
which `sound_preload` writes, as `rand() % sound_keep_mark`.

The draw matters as much as the sound. It happens before either guard, so the
RNG advances on those frames whether anything is audible or not, and a demo
replays from a seed carried in the recording. A draw the port does not make is a
demo that diverges from what was recorded even after the clicks start landing.

### The particle draw is in level space, the screen is not (0x0ab09)

Level 35 keeps its flock above the view, and aborting it put the bursts where
the ducks would have been if the view had been at the origin. `particles` was:

```c
plot(p->x >> 3, p->y >> 3, p->colour);
```

The pool is in level coordinates. The original turns each one into a screen
coordinate and clips it to the viewport, which is most of what the routine does:

```
0x0ab2f  x = (p->x >> 3) - viewport_game.scroll_x + viewport_game.left
0x0ab39  if (x <  left)   next          ; jb  - unsigned
0x0ab3f  if (x >= right)  next          ; jae
0x0ab5f  y = (p->y >> 3) - viewport_game.scroll_y + viewport_game.top
0x0ab6d  if (y <  top)    next
0x0ab76  if (y >= bottom) next
0x0ab90  plot(x, y, colour)
```

`native.py`'s `native_particles` already had this and says why the form matters:
the shift helper at `0:0x1148` is **SHR, not SAR**, so the fixed point is
unsigned, the arithmetic is 16-bit, and the wrap has to be applied before the
compare - that is what lets one bound reject both sides, since an x left of the
view wraps to a huge value and fails the upper test. The C is a transcription of
that native and now says the same thing.

`particles_step` uses the same helper at 0x0aa40 and 0x0aa5c, so those shifts are
unsigned too. It makes no difference there - the retire tests above them leave
both coordinates non-negative - but the form should not imply otherwise.

**Checked**: `plane_loop 0x0e4dc` is the loop that draws the pool, and byte-
compared against the guest with the viewport scrolled to x=30 and 248 particles
live it matches on all 631 calls. That verifies the native the C was copied
from, not the C: `fb_back` is `static` in `sdl_io.c`, so no harness has ever
compared the port's screen against anything. Getting a state that reaches it at
all needed ESC pressed inside the run - `snap012`'s pool is empty, so
`replay.py --require particles` on it correctly reports that a clean run proved
nothing.

### level_free (0x09329), and what a stub was costing

`f_09329` was a stub, and it is the routine that gives a level back: the
backdrop, the background tile, every scenery image, all five entity arrays and
the tool list. Three callers, and they are the three ways a level stops
mattering:

| site | when |
| --- | --- |
| `0x0e7ec` | `run_level`'s teardown - every attempt, won or lost |
| `0x11484` | `level_screens`, when the level-select key means the level it just loaded will not be played |
| `0x13856` | `game_main`'s shareware refusal |

Only the third was even wired here; the port's teardown did not call it at all,
so every single attempt at a level leaked the whole level.

Two things in it are not memory and are easy to read past:

- `level_flags[0]` and `[3]` are cleared, which is what stops the blink and the
  `0x0e673` branch carrying into whatever comes next.
- The sample rate goes back to 11000, undoing the D key - which doubles it for
  the level it is pressed on and would otherwise leave every later sound fast.

`scenes[4]` is **not** freed. That is the cursor scene and `run_level` owns it.
The order the five arrays go in - 3, 5, 0, 1, 2 - is the original's.

The teardown at `0x0e7da` was also out of order here. It is
`blink_enable = 0`, `sprite_set_free`, `level_free`, `free(particle_array)`,
`free(tool_scene.entities)`, `set_buffer` - and the port had the sprite set
freed third and `blink_enable` not cleared at all.

**Checked** by loading and freeing six levels three times over under
AddressSanitizer and LeakSanitizer: no double free, no use after free, and no
leak. `resource_release` nulls `rows` and so is idempotent, which is what makes
the two image releases safe; the five plain `free`s are not, and nothing calls
this twice without a `level_load` in between.

One harness trap worth keeping: the leak checker first reported one string per
level, from `egg_read_string` in `level_load`. That is `level_text`, which
`level_screens` frees at `0x110b8` - the probe was calling `level_load`
directly and skipping it. The leak was the harness, not the port.

## The HUD is drawn twice a level, and COLOURMAP wrecks half of it (0x0ce2e)

**Reported 2026-08-08 as a port bug: after the COLOURMAP chart the HUD flickers
or is gone. It is the original's behaviour. Recorded so it is not chased again.**

Two things, and neither is a port defect.

**The panel is drawn once into each page at level start and never again.** The
block at `0x0d9a2` blits the 0x4d/0x21 resource into `viewport_panel`, draws the
tool slots and the score/ducks/lives labels, flips, and does it again for the
other page. Per frame the loop draws only the tool cursor, the two numbers, the
three message slots and the timer bar - the background and the labels are not
among them.

Counted rather than read off the listing. Hooking `0x0d9a2` and the flip at
`0x04d4b` and running the guest forward from `snapshots/snap012.snap`:

```
page flips: 720   HUD block entered: 0
```

**The chart is a full-screen image and lands on one page.** `0x0ce2e` builds a
320x200 page, blits it through `viewport_screen` for each of the four planes,
and flips once - so the page it landed on has the chart and the other still has
the frame before it. Rows 160-199 of that image are never painted, so they hold
`alloc_image`'s fill, which is 1.

Reading both pages' bottom 40 rows out of the guest, before and after running
`0x0ce2e` up to the instruction before its `getch`:

```
pages: front 0x7d00  back 0x0000
before:  front panel  38 distinct values, commonest [(7,1739),(5,1663),(8,1400)]
         back  panel  38 distinct values, the same
after:   front panel   1 distinct value,  [(1,12800)]      <- the chart's page
         back  panel  38 distinct values, unchanged
```

So from the moment the chart is dismissed, one page carries a HUD and the other
a flat band of colour 1, and the flip alternates them every frame until the level
ends. That is what the report described, and the port reproduces it exactly.

Deliberately **not** fixed (2026-08-08, the user's call): redrawing the panel
after the chart would be an improvement on the game rather than a correction to
the port. If that decision is ever revisited, the fix is the `0x0d9a2` block
again - both pages, with a flip between - and it belongs behind a note saying it
is a departure.

### getch does not return until a key, and BOTH I/O layers had it wrong

The chart is held on screen by a blocking `getch` - `0:0x2814`, which is
`mov ax, 0x700 / int 21h`, DOS's direct console input without echo. It is the
only caller in the game that reads a key without asking `kbhit` first, and that
is the whole mechanism: everything else polls, this one waits.

Both of the repo's I/O layers returned immediately instead, independently, and
for the same reason - every *other* caller is guarded by `kbhit`, so a
non-blocking read is invisible until this routine exists:

- **`sdl_io.c`** returned 0 on an empty queue. It now waits, pumping SDL every
  10 ms so a key can arrive and the window keeps answering the compositor.
- **`emulation.py`** answered AH=07 with AL=0. It cannot sleep in the hook - the
  event pump is in the outer loop, so nothing would ever deliver a key - so it
  winds IP back over the two-byte INT and stops the slice. `main()` pumps
  pygame, paces on `clock.tick(60)` and comes back to the same instruction.
  `_record_intr` already documents that IP points just past the INT, which is
  what makes the rewind exact. AH=06 is deliberately left non-blocking: with
  DL=0xFF it is a status poll and must answer 0 rather than wait.

Measured in the emulator on `snap012` with `cheat_state[5]` set, counting page
flips per slice: 843 before P, **one** flip for the chart itself, then 0 across
twenty more slices, then play resumes the moment a key is queued.

The general shape is worth keeping: **a routine that is only reached one way
will hide a defect in everything that reaches it another way.** Two separate
backends had the same wrong answer for years because no caller had ever asked
the question this one asks.
