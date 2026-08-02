# in_game_frame, and the shape of the gameplay

**Started 2026-08-02**, immediately after the `menu-done` tag. Nothing here is
ported yet; this is what the first read established, so the next one does not
have to start from the top.

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

**2026-08-02, the same day.** Thirteen of the ninety-one are Python natives in
`native.py` now, taken innermost first: `scene_keep_positions`,
`scroll_axis_toward`, `scroll_axis_snap`, `scroll_follow`, `entity_set_type`,
`scene_swap_pair`, `egg_block_end`, `rle_reset`, `set_buffer`,
`cursor_to_centre`, `bg_scroll_reset`, `palette_apply_gamma`,
`build_washed_ramp`, `tool_list_has`, `tool_list_any_flagged`, `text_width`,
`image_clear`. Nineteen more have all their callees native and are ready.

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

## The order to take it

The event tables and the tool list are filled by the level loader, so reading
that first is what stops the rest being guesswork:

1. **`0x0993b`** (2,668 bytes) and **`0x0a410`** — the level load. Everything
   above indexes what these build.
2. **`0x0d0c8`** (937 bytes) and **`0x0cf07`** (449) — what the events do.
3. The HUD group, `0x0d5c5` (254 bytes) and `0x0d6c3`/`0x0d715`, which draw the
   panel; `draw_number2` beside them is already ported.
4. `in_game_frame` itself last, when its callees exist.

Two of the four plane loops live inside `0x0d7ee`, so
[open-function-attribution](open-function-attribution.md) and the plane-loop
extents matter here in a way they have not so far.
