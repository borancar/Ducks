# The menu loop, and why it plays levels

**Mapped 2026-08-01**, by reading `0x1271b` end to end after driving the menu over
the control socket with `--no-demo`. It is `game_main`'s first call, and the
reason the "menu" turned up on the stack above an in-game frame.

## The shape

```
0x1271b  menu_screen_driver(five words from game_main)
0x12723  <---------------------------------------------- top of the loop
  0x12733  call 0x0c716          the screen: draw it, take input, return a record
  0x12739  [bp-4] = the record   far pointer, in dx:ax
  0x1273f  di = 0                "leave the loop" flag
  0x12744  ax = record[+8]       the action code
  0x12748  cmp ax, 0x0a  je      idle timeout      -> the attract branch
  0x1274d  cmp ax, 0x15  je      play this demo    -> 0x127fc
           otherwise             anything else     -> di = 1, leave

  --- 0x0a: the attract branch ------------------------------------------
  0x12758  cmp [0x21ae], 0  jne  show the attract screen: 0x0b9fc(400)
  0x1276c  call 0x126db          pick a demo: rand() % [0x2038], load it
  0x12770  or ax, ax  je         nothing loaded -> "DEMO MISSING"
  0x12774  ... free three buffers, [0x18f5] = 5, [0x1ffc] = 0
  0x12793  si = [0x509]; [0x509] = 0
  0x1279d  call 0x0d7ee(1)       <-- play the level
  0x127a6  [0x509] = si; free three more; release_sounds
  0x127ee  [0x21ae] = ![0x21ae]  toggle: screen next time, demo the time after

  --- 0x15: play the demo the record names -------------------------------
  0x127fc  al = record[+0xb]
  0x12805  call 0x1240f(al)      load that demo; 0 -> "DEMO MISSING"
  0x12811  ... identical to the branch above, down to the same three frees
  0x1283a  call 0x0d7ee(1)       <-- play the level
  0x1287c  join the tail

  0x1287e  show_splash(ds:0x26bd, 100)   "DEMO MISSING"
  0x12892  if (!di) goto 0x12723         else return the record to game_main
```

## What this settles

**The menu and the game are the same loop.** `0x1271b` calls the in-game frame
itself, at `0x1279d` and `0x1283a`, both with argument **1** where `game_main`
passes **0** at `0x1387e`. That is why a stack walk during the idle demo showed
`menu_screen_driver` above `0x0d7ee`, and why the attract demo advances
`level_attempted` exactly as playing does - it is not a special lightweight
preview, it is the game.

**The two branches are the same code twice.** Both free three buffer pairs, set
`[0x18f5] = 5` and `[0x1ffc] = 0`, save `[0x509]` and zero it across the call, and
end in `release_sounds`. The only difference is where the demo index comes from:
`rand() % [0x2038]` when idle, `record[+0xb]` when the screen asked for one.

**`0x0c716` is the screen, not the menu.** It draws, takes input and returns a
record; `0x1271b` only handles two of its action codes and hands everything else
back to `game_main`. So picking PLAY DUCKS or OPTIONS is not dealt with here at
all - it leaves this function as a return value. That is the division of labour
the [homecoming-sequence](homecoming-sequence.md) block sits on the other side
of.

**The record.** `+8` is the action code, `+0xb` a byte parameter - the demo index
on the `0x15` path. `0x0c716`'s own contents are documented elsewhere as holding
`plane_loop_layer`, so the same routine draws menus and between-level screens.

## game_main's side: a switch table at 0x13a70

What `menu_screen_driver` hands back is an action code, and `game_main` dispatches
it through a jump table sitting immediately after its own `retf`:

```
0x1369e  bx = record[+8]
0x136a2  dec bx / cmp bx, 0x13 / jbe        codes 1..20
0x136ab  shl bx, 1
0x136ad  jmp word cs:[bx - 0x1230]          -> the table at image 0x13a70
```

| code | lands on | what it is |
| --- | --- | --- |
| 1 | `0x1377b` | into the episode unpack, then the inner loop - start playing |
| 2 | `0x137f6` | the inner loop head directly |
| 3 | `0x13a54` | `high_score_screen` |
| 4 | `0x136c6` | clears the loop flag - QUIT |
| 5 | `0x13742` | **`save_game_screen`**, then menu <- `ds:0x1989` |
| 6 | `0x1376c` | **`load_game_screen`**, then menu <- `ds:0x1989` |
| 7 | `0x13758` | `show_readme_section(record[+0xb])` |
| 12, 13 | `0x136cb` | clear, set the video mode, black the DAC, menu <- `ds:0x1c3b` |
| 14 | `0x13751` | **`register_screen`** |
| 18 | `0x136b2` | menu <- `record[+4]/[+6]` - **this is how submenus work** |
| 20 | `0x136fe` | the `button_map_a/b/c` pairwise checks - MOUSE BUTTONS |
| 8-11, 15-17, 19 | `0x13a66` | nothing; fall through to the loop |

Unpacked, `game_main` is a menu interpreter with the game as one of its cases:

```c
void far game_main(menu_t far *menu)      /* main passes ds:0x1916, the main menu */
{
    char buf[0x326];
    int running = 1;                      /* si, set at 0x1367e */

    do {
        record_t far *r = menu_screen_driver(menu, &buf[?], 1);  /* five words */

        switch (r->action) {              /* r[+8], via the table at 0x13a70 */

        case 18:  menu = r->submenu;      break;   /* a submenu is data, not code */
        case 4:   running = 0;            break;   /* QUIT */
        case 14:  register_screen();      break;
        case 7:   show_readme_section(r->param);   break;
        case 5:   save_game_screen();     menu = &menu_1989;  break;
        case 6:   load_game_screen();     menu = &menu_1989;  break;
        case 3:   high_score_screen(); f_0f55c(); menu = &main_menu;  break;

        case 20:                          /* MOUSE BUTTONS: reject duplicates */
            if (button_map_a == button_map_b || button_map_a == button_map_c
                || button_map_b == button_map_c) { ... }
            break;

        case 12: case 13:                 /* RESOLUTION */
            clear_vram();
            set_mode_x(r->action == 13);
            dac_set_black(0, 0);
            menu = &menu_1c3b;
            break;

        case 1:                           /* START: unpack the chosen episode */
            [0x1ffc] = [0x1ffa] = 0;
            menu = &menu_1989;
            i = r->param;
            level_attempted   = episode_index[i].first;               /* +4 */
            episode_egg_index = episode_index[i].egg;                 /* +6 */
            shareware_limit   = egg_files[episode_egg_index].limit;   /* +0x10 */
            /* FALL THROUGH */

        case 2:                           /* play, tally, repeat */
            for (;;) {
                if ([0x1ffc]) {                        /* 0x137f6 */
                    sound_play_guarded(0x29, 1);
                    show_splash(res->splash_a0, 200);
                }
                if (f_1102a([0x21a3]))                 /* a screen; non-zero exits */
                    break;
                [0x18f5] = 2;

                if (shareware_limit < level_attempted   /* 0x13841 */
                    && !registered && ![0x1ffc]) {
                    f_09329();                          /* the refusal */
                    egg_load_one(0xfc, 0x48, 0xff);
                    menu = &main_menu;
                    high_score_screen(); f_0f55c();
                    break;
                }

                if (!run_level(0)) {               /* 0x1387e: lost the game */
                    [0x21a3] = 0;
                    if (![0x50b]) {
                        --lives;                        /* [0x2034] */
                        sprintf(buf, "%s: %i", res->label_d4, lives);
                        show_splash(buf, 100);
                        release_sounds();
                        if (lives == 0) {               /* GAME OVER */
                            menu = &main_menu;
                            sound_play_guarded(0x16, 1);
                            show_resource(0x4d, 6, 0x32, 0xff);
                            ...
                        }
                    }
                    break;
                }

                if ([0x1ffc] || [0x1ffe])              /* 0x1388b, 0x13895 */
                    break;

                [0x21a3] = 1;                          /* the level was completed */
                sound_play_guarded(2, 1);
                show_resource(0x4d, 2, 0x32, 0xff);    /* BONUS SCREEN */
                f_0becb();
                ... show_splash, release_sounds ...

                if (episode_end_gate(level_attempted, episode_egg_index)
                    && episode_egg_index == 0) {       /* 0x1390d, 0x1391a */
                    cutscene_rocket_space();           /* the homecoming */
                    f_147c5(0x4a, [0x1fd3], 0xff);
                    cutscene_rocket_landing();
                    cutscene_doorstep();
                    cutscene_welcome_home();
                    release_sounds();
                    cutscene_photos();
                    f_147c5(...);
                    cutscene_night_monster();
                    release_sounds();
                    dac_set_black(0, 0);
                    input_poll(320, 200);
                    high_score_screen(); f_0f55c();
                }
                level_attempted++;                     /* 0x139ab */
            }
            break;

        default:  break;                  /* 8-11, 15-17, 19 */
        }
    } while (running);                    /* 0x13a66 */
}
```

The inner `for (;;)` is drawn from the back edges at `0x13a45`/`0x13a4f` and the
several `jmp 0x13a33` exits; the `break`s above are those jumps. `res` is the
far pointer at `0x1894:0`, which the function indexes for splashes and labels.

`do ... while` rather than `while`, because that is the shape emitted: `si` is set
to 1 at `0x1367e`, the loop top at `0x13681` is entered directly, and the only test
is `or si, si / je / jmp` at `0x13a66`. A `while` would normally begin with a jump
to that test. Not proof - a compiler may fold a first test it can see is true -
but the body-then-test form is what is there.

Three things read off that shape. **Quitting is a case**, not an exit path: code 4
clears `running` and the loop ends on the next test, which is why everything after
`game_main` in `main` - the adverts, the readme, `exit_cleanup` - runs only then.
**Three menu descriptors exist**: `ds:0x1916` (the main menu, which `main` passes
in and case 3 restores), `ds:0x1989` (after starting, saving or loading a game)
and `ds:0x1c3b` (after a resolution change). **Case 1 falls into case 2**, so
"start an episode" is "unpack three globals, then play".

**Code 18 explains why every submenu has the same stack.** Choosing PLAY DUCKS or
OPTIONS does not call anything: it returns a record carrying a pointer to another
menu descriptor, `game_main` swaps it into its own argument, and the next pass
draws that instead. One `run_screen` call site, many menus. SELECT AN EPISODE,
LOAD / SAVE, the readme list and the quit confirmation are all the same code
reading different data - which is why walking the stack in four different
submenu captures named the same two frames every time.

The three screens that *are* separate routines were each caught at their entry by
choosing the item that reaches them: SAVE THIS GAME, LOAD SAVED GAME and REGISTER
DUCKS. All three then run their own `menu_screen_driver` loop for the slot or
name list.

## The menu tree, driven with the keyboard

With `--no-demo` holding the menu still, every option was reached over the
control socket from `snapshots/main-menu.snap`:

```
PLAY DUCKS     START A NEW GAME / LOAD / SAVE / END CURRENT GAME / MAIN MENU
OPTIONS        AUDIO / VIDEO / MOUSE SETTINGS / GAME SPEED / REGISTER DUCKS / DONE!
  AUDIO        SOUNDS: ON / AMBIENCE VOLUME / DONE!
  VIDEO        RESOLUTION / FLYING BLOOD: ON / MENU BOUNCE: ON / GAMMA CORRECT / DONE!
  MOUSE        SMOOTH SCROLL: ON / MOUSE BUTTONS / DONE!
  GAME SPEED   an inline slider of ducks, no submenu
READ ME!       the five readme sections from episode-index, plus CANCEL
QUIT / Esc     QUIT? REALLY? -> YES... REALLY / HEY - ONLY KIDDING
```

Two things about driving it, both learned by getting them wrong:

- **Keys sent during a fade are dropped.** A Down and a Return vanished entirely;
  two seconds between presses fixed it. A key that appears to do nothing here is
  usually a key that arrived too early.
- **Escape resets the hover rather than restoring it.** The first `up` after an
  Escape wraps to the last item, which is how aiming for OPTIONS landed in
  READ ME!. Capture the screen and look before pressing Return.

`GAME SPEED` writes `game_speed` (`[0x1fd4]`) directly, one step per press, and
clamps at **0x1f**: watched going 29 -> 26 over three lefts and then to 31 over
five rights. `page_flip` delays `0x1f - speed` ms, so 31 is no delay at all. See
[open-game-speed](open-game-speed.md).

## One level per pass: the play loop's tail (0x13a33)

Action 2 - the `case 2` the RESUME item reaches, and the one `START A NEW GAME`
falls through into - looks like a loop that plays level after level. It is not.
Every way out of a level converges on **0x13a33**, and what happens there is:

```
0x13a33  cmp word ptr [0x2177], 0     ; a game in progress?
0x13a38  je   0x13a3e
0x13a3a  call 0x128a5 <menus_resume>  ; relabel PLAY DUCKS' first item
0x13a3e  cmp word ptr [0x1ffc], 0     ; secret level pending?
0x13a45  jmp  0x137f6                 ;   then go round again
0x13a48  cmp word ptr [0x1ffe], 0
0x13a4f  jmp  0x137f6
0x13a52  jmp  0x13a66                 ; otherwise leave the case entirely
```

So the ordinary path - level completed, bonus tallied, `level_attempted++` at
0x139ab - **falls out to `menu_screen_driver`**. The loop only repeats itself for
a secret level. That is what `menus_resume` is for and why it is called here:
it sets PLAY DUCKS' first item to PLAY NEXT LEVEL or RETRY LEVEL depending on
`[0x21a3]`, immediately before the driver draws that menu again. A label that
only ever appeared after loading a save would not need deciding once per level.

The port had `case 2` as a real `for (;;)`, which produced the same sequence of
levels while never showing the menu between them, and dropped the tail's
`menus_resume` with it. It now has the tail as written, with the five exits
reaching it by `goto play_tail`.

`[0x2177]` was called `menu_idle_suppress` here, after its use at 0x0c9db where
it holds off the idle demo. Suppressing the demo is a consequence; the flag
means **a game is in progress**, which is how 0x13a33 and the three menu items
built with `&game_in_progress` read it. Renamed.

## The game-over sequence

`0x139b2`, reached when `run_level` returns 0:

```
g_21a3 = 0;
if (!g_50b) --lives;                   /* only the decrement is guarded */
sprintf(buf, "%s: %i", menu_text[53], lives);   /* "LIVES LEFT: 0" */
show_splash(buf, 100);
release_sounds();
if (lives == 0) {                      /* 0x13a01 */
    menu = &main_menu;
    sound_play_guarded(0x16, 1);
    show_resource(0x4d, 6, 50, 0xff);  /* GAME OVER */
    high_score_screen();               /* 0x13a2c - the hall of fame */
    menus_after_game();                /* 0x13a30 */
}
/* falls through to the tail above, which returns to the menu */
```

Captured as snap005-snap010, identified by the return addresses on the guest's
stack rather than by looking at the screens:

| snapshot | stack                                   | screen              |
|----------|-----------------------------------------|---------------------|
| snap005  | `page_flip <- 0x139f9`                  | LIVES LEFT: 0       |
| snap006  | `page_flip <- 0x0c1f2 <- 0x13a28`       | GAME OVER           |
| snap007  | `page_flip <- 0x11d79 <- 0x13a2f`       | high_score_screen   |
| snap008  | `page_flip <- 0x11dc1 <- 0x13a2f`       | the score           |
| snap009  | `page_flip <- 0x0bb1d <- 0x11ef4`       | the board           |
| snap010  | `page_flip <- 0x12736 <- 0x13692`       | the main menu       |

All six read `lives=0 outcome=3`; `[0x2177]` is 1 in the first five and 0 in
snap010, which is `menus_after_game` having run. The port stopped after the
`show_resource`, so the run ended on the GAME OVER screen and never came back.

Three more transcription errors were in the same twenty lines, all found by
reading the disassembly the captures pointed at:

- `while (level_screens(...))` , not `if (...) break`. 0x13835 jumps **back to
  the call**: non-zero is the level-select key, and the screens have to be
  rebuilt for whatever level was just picked.
- The `[0x50b]` guard covers `--lives` only, not the splash and the game-over
  after it. It means "this attempt was free", not "say nothing".
- The ending gate at 0x1390d is two nested tests, not one `&&`. Episode 0 gets
  the cutscenes; **every** episode that passes the gate gets `menu = &main_menu`
  (0x13999), `high_score_screen` and `menus_after_game`.

## The cheat words are CAPITALS, and the emulator could not type one

**2026-08-08.** Reported as "I tried entering the cheatcode in native.py but it
doesn't do anything". Three separate things, found in that order.

**The words, out of a live guest.** `cheat_text` is a 0xfe string table, ten
entries, read through `[0x519]`:

```
[0] BUSHKANGAROO     '#' finishes the level outright                    0x0d012
[1] THECROWDSAYBO    the level picker, and the level-select key         0x1104b
[2] NOSCHOOLCUSTARD  ducks do not die; cleared for a demo               0x07900
[3] ONLYFOREVER      a lost attempt costs no life                       0x139b8
[4] KEYCODE          nothing - written by typed_push and read NOWHERE
[5] COLOURMAP        P draws the 256-colour chart                       0x0cfef
[6] NODNOL           LEFT HANDED: which side the tool is drawn          0x0acb7
[7] INGLESHFELDOR    the play log, which the port does not write        0x1148e
[8] PLAYBACKTIME     the demo picker                                    0x0cadf
[9] YOUINTSEENME     places the 0x4d objects level_load would skip      0x08bdb
```

`[4]` is dead in the shipped build: every direct encoding that could reference
`d+0x050d` was searched for across the image and there is none, and the only
indexed accesses to `[bx + 0x505]` are the four inside `typed_push` itself. So
KEYCODE toggles a flag, flashes the border, and nothing looks at it again.

`[7]` copies itself into `play_log` (`[0x51d]`), which gates a per-level file at
0x1149e and an fprintf on every tool use at 0x0d0db. Both are TODOs in `game.c`,
so the cheat sets the flag here and produces no log.

They are typed at a **menu**, not during play: `typed_push` has exactly one
caller, the `default:` arm of `run_screen`'s key switch.

**The compare is case-sensitive.** `0:0x4c28` is `repe cmpsb` and nothing else -
no case folding - and `0:0x4215`, which `load_eggs_ini` uses for `[EGGS]`, is the
same routine emitted twice. Both are `strcmp`. The port had `strcasecmp` in both
places, so it accepted lowercase where the original does not.

Shown rather than argued. Feeding "colourmap" into the guest at the main menu:

```
typed_push called 9 time(s), with: colourmap
strcasecmp('COLOURMAP', 'colourmap')     <- reached, and returns non-zero
cheat_state: [0,0,0,0,0,0,0,0,0,0]
```

and the same run with capitals ends `[0,0,0,0,0,1,0,0,0,0]`.

**The emulator could not type a capital at all.** `emulation.KEYMAP` holds one
ASCII per key and it is `ord(k)` with `k` lowercase, and the KEYDOWN handler read
modifiers only for its own Ctrl+Alt ungrab. So no shifted character had ever
reached the guest, and the only two things the game reads ASCII for - the cheat
words and the name on the high-score table - were both unreachable in capitals.

Fixed by preferring pygame's `ev.unicode`, which already has shift, caps lock and
the layout applied, and keeping the table's scancode because that is what
`last_scancode` and the button map read. `emulation.shift_ascii` falls back to
the table for anything that is not a single printable ASCII character, so dead
keys and non-ASCII layouts push nothing new. The control socket takes a single
capital as a shifted press for the same reason - `press C` used to fail, since
pygame has no key named "C".

**Why nothing caught this.** Cheats are the only case-sensitive compare in the
game, and no test types anything: `test_leaves` and `test_entity` call routines
directly, and the demos drive themselves from the recorded tables rather than
from the keyboard.

### And the SDL port could not type one either (same day, my regression)

Correcting the compare to `strcmp` broke every cheat in the port, because
`sdl_io.c` had the same limit `emulation.py` did and I only fixed the emulator:

```c
else if (e.key.key < 0x80)   key_push((int16_t) e.key.key);
```

`e.key.key` is the **unshifted** keycode - `SDLK_a` is `'a'` whatever shift is
doing - so the port could only ever produce lowercase. Case-insensitive matching
had been hiding it.

It was hiding more than the cheats. The high-score name entry accepts only
`ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ,.!'-?/:` - every letter uppercase - so a
name could never be typed at all, and `#`, which finishes a level under
BUSHKANGAROO, is shift+3 and was unreachable.

The fix is `SDL_GetKeyFromScancode(e.key.scancode, e.key.mod, false)`, and the
`false` is the whole point: that parameter is `key_event`, and **true** means
"the keycode a key event carries", which for a letter is the unshifted one. The
first attempt passed `true` because it reads as the right thing, and it changed
nothing:

```
key_event=True  mod=shift  -> colourmap3
key_event=False mod=shift  -> COLOURMAP#      <- shift+3 is '#'
key_event=False mod=caps   -> COLOURMAP3      <- caps leaves digits alone
```

That table is why this is worth a note: the wrong flag compiles, runs, and looks
correct in the source. Only running it says which one applies the modifiers.
