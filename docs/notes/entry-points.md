# The call chain from the C runtime down

Established 2026-07-28 by breakpointing a live machine over the control socket,
not by reading the disassembly — every frame below was observed.

Names live in [`symbols.py`](../../symbols.py), which the control socket's
`where` — and so `stack`, `until`, `finish` and `step` — prints alongside the
offset. A name ending in `?` there is tentative and is printed as such.

## The chain

```
image 0x0014e   the C runtime's startup (no prologue; where main is called from)
  └─ 0x144d7    main
       ├─ 0x141fe    the "Press a key to begin" wait, before graphics
       └─ 0x0c156    a loader pass, NOT game_main - see below
            └─ 0x0c0c2 ... and deeper
```

`0x144d7` is **main**: it is the frame that never leaves the stack, present both
while the startup screen waits for a key and while the game runs, and the only
one the runtime at `0x0014e` calls.

## 0x0c156 is a loader pass, not the main routine

It was briefly marked "tentatively game_main" on the strength of being called
from main with no arguments. Disassembling it settles that it is not — it is 86
bytes:

```
sub sp, 0x302              ; a 770-byte scratch buffer
call 0x0b9ea               ; register it: set_buffer(ss:buf)
mov [0x54d], 4             ; set a flag for the duration
  loop i over [0x20ad]     ; the open egg-file count
    call 0x0c0c2(0, 0x48, i)
mov [0x54d], <old>         ; restore the flag
call 0x0b9ea(ds:0x13f1)    ; restore the buffer
```

`[0x20ad]` is the open-egg-file count from [episode-index](episode-index.md), and
the shape matches the index builder's `0x15232(0x5a, 1, si)` — a pass over every
open egg for one resource type, `0x48` here against `0x5a` there. So main calls a
sequence of these, and **the real game loop is a different call from main** —
look at what else `0x144d7` calls around `0x1452a`.

## What main passes to 0x0c156: nothing

Caught at the function's first byte with a breakpoint, so the arguments were
still where the caller left them. `SP` was `0x0ffc` and main's `BP` `0x1000` — a
gap of exactly **four bytes**, which is the far return address `05da:f88a` and
nothing else. Registers carry nothing either: `ax=0 bx=0 dx=0`, `ds` the DGROUP,
`es` the stack segment.

**So the indexes and the egg are not arguments.** They are globals, which is why
every consumer reads them straight out of DGROUP — `[0x20a9]` the open egg files,
`[0x20ba]` the episodes, `[0x20be]` the readme sections
([episode-index](episode-index.md)).

## It does not set the video mode

At the instant `0x0c156` is entered, the machine is already `mode=0x13` with 116
page flips behind it. Graphics is up well before the call. An earlier reading that
put the mode change underneath `0x0c156` was from a *later* call — the function is
entered repeatedly — and **who sets the mode first is still unattributed**. All
nine `INT 10h` sites are in one wrapper, `0x202c`, so breakpointing that from a
cold boot is the way to answer it.

## What it does first

```
push bp / mov bp, sp
sub sp, 0x302              ; 770 bytes of locals
push ss / lea ax, [bp-0x302] / push ax
call <image 0x0b9ea>       ; set_buffer(far *)
```

`0x0b9ea` is eighteen bytes: it stores the far pointer into `[0x1721]`/`[0x1723]`
and returns. That is the same `[0x1721]` the palette builder at `0x0b0c5` reads
through `les bx, ptr [0x1721]`, so it is a current-buffer register, and
`0x0c156`'s first act is to publish its own stack buffer into it.

**`0x0b9ea` is emphatically not a main routine**, and was nearly written down as
one — see below.

## The address-space trap

The socket's `disasm` prints **linear** addresses, and so do capstone's branch
operands, while every note and every offset in this project is an **image**
offset. The two differ by `image_base`, `0x1100`. `call 0xcaea` in that listing
means linear `0x0caea`, which is image `0x0b9ea` — a different function
altogether, and the one at image `0x0caea` would have been misidentified as the
game's main routine.

The instruction column is tagged `i+0x…` for exactly this reason. The operands
are not. Subtract `0x1100` from a branch target before looking it up.
