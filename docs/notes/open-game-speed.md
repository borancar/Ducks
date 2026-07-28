# Open: the game's own speed control was never reimplemented

**Noted 2026-07-28.** Ducks has a speed setting. The native page flip does not
honour it, and nothing else does either, so with `--native-flip` on — the default
— changing the speed in-game should have no effect.

## What the control is

From the in-game readme, ADVANCED CONTROLS (page 3 of 3):

```
[<] and [>]  -  Game speed
(The above settings can also be changed on the VIDEO SETTINGS menu)
[d]  -  Toggle double speed
```

## Where it lives

A byte at **DGROUP+0x1fd4**. Written from four sites inside `0x0cf07`
(`0x0d098`, `0x0d09f`, `0x0d0a5`, `0x0d0ac`) — the key handling and/or the menu.
It is read in exactly one place: the top of `page_flip`.

```
0x04d4b  push bp
0x04d51  mov al, byte ptr [0x1fd4]     ; the speed setting
0x04d56  mov dx, 0x1f
0x04d59  sub dx, ax                    ; 0x1f - speed
0x04d5b  push dx
0x04d5c  lcall 0, 0x223e               ; Borland delay(ms), which spins on the PIT
```

So the original throttles by **delaying `(0x1f - speed)` milliseconds at the top
of every page flip**, before waiting for display enable and then for retrace. A
higher stored value is a shorter delay, i.e. a faster game.

## Why it stopped working

`native_page_flip` reproduces the page swap, the CRTC writes and the phase
counter, and deliberately drops both retrace waits — that was the point, they
were 94% of all port I/O. **It also drops the delay**, and paces instead with
`_pace_flip` at a fixed `--flip-hz`, default 70. The speed byte is never read.

Nothing is broken by this: the game runs at a steady 70 Hz, which is what the
retrace wait would have produced at the fastest setting. But a control the game
offers now does nothing.

## What it would take

`_pace_flip` already owns the frame schedule, so the fix belongs there: read the
byte and lengthen the period by `(0x1f - speed)` ms, the same arithmetic the
original does. That keeps the speed control's meaning — an offset in
milliseconds per frame, not a multiplier — and leaves the retrace spin gone.

`--no-native-flip` restores the original behaviour today, including the control,
at the cost of the spin coming back.

## Not verified

- That the in-game control visibly does nothing. This is read off the
  disassembly and off `native_page_flip`'s body; nobody has sat in VIDEO SETTINGS
  and pressed `<` and `>` with the native flip on.
- What `[d]` (double speed) writes, or whether it is the same byte.
- Whether the setting is persisted in `settings.dat` and so survives a restart.

Each is a few minutes with the control socket
([control-socket](control-socket.md)) against a menu snapshot, and worth doing
before implementing, since the honest fix depends on what the four writer sites
in `0x0cf07` actually store.

See [port-io](port-io.md) for why the waits were removed in the first place.
