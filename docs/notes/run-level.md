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
