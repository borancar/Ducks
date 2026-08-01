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
