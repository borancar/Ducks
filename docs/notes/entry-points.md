# The call chain from the C runtime down

Established 2026-07-28 by breakpointing a live machine over the control socket,
not by reading the disassembly — every frame below was observed.

## The chain

```
image 0x0014e   the C runtime's startup (no prologue; where main is called from)
  └─ 0x144d7    main
       ├─ 0x141fe    the "Press a key to begin" wait, before graphics
       └─ 0x0c156    tentatively game_main
            └─ 0x0c0c2 ... and deeper
```

`0x144d7` is **main**: it is the frame that never leaves the stack, present both
while the startup screen waits for a key and while the game runs, and the only
one the runtime at `0x0014e` calls.

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
