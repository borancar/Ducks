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
`0x1462:0x215` are the sound module, `0:0x147d` is `rand` and is called eleven
times, and one call is **indirect through `[0x53e]`** — a function pointer that
nothing has yet been seen to set.

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
