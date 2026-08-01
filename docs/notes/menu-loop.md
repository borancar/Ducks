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

                if (!in_game_frame(0)) {               /* 0x1387e: lost the game */
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
