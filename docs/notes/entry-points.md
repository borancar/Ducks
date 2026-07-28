# The call chain from the C runtime down

Established 2026-07-28 by breakpointing a live machine over the control socket,
not by reading the disassembly — every frame below was observed.

Names live in [`symbols.py`](../../symbols.py), which the control socket's
`where` — and so `stack`, `until`, `finish` and `step` — prints alongside the
offset. A name ending in `?` there is tentative and is printed as such.

## main, in full

Every branch target below was resolved by the socket rather than by hand:

```
0x144d7  push bp / mov bp, sp
0x144da  push 05da:f82d
0x144e0  lcall install_int23         ; the Ctrl-C handler
0x144e8  call init                   ; the whole startup screen
0x144ec  push [video_mode]           ; [0x4fe], not a literal
0x144f1  call set_mode_x
0x144f7  mov [0x20a7], 1
0x144fd  push 0 / push 0
0x14502  call dac_set_black
0x14508  push 200 / push 320         ; the resolution
0x1450f  call input_poll
```

### Why input_poll takes a resolution

Because **the game keeps the cursor position itself**, and the resolution is the
clamp bound. The width and height are loaded into SI and DI at the top and are
not passed on - `mouse_motion` receives only two far pointers, `&mouse_dx` and
`&mouse_dy`:

```
mov si, [bp+6] / mov di, [bp+8]        ; 320, 200 - kept in registers
push ds / push 0x18dd                  ; &mouse_dy
push ds / push 0x18db                  ; &mouse_dx
call mouse_motion                      ; 8 bytes popped: the two pointers only
...
mov ax, [mouse_dx] / cdq
add [mouse_x], ax / adc [mouse_x+2], dx    ; accumulate into a 32-bit position
mov ax, [mouse_dy] / cdq
add [mouse_y], ax / adc [mouse_y+2], dx
cmp si, [mouse_x] / ja ok / mov ax, si / dec ax / mov [mouse_x], ax   ; clamp to w-1
cmp di, [mouse_y] / ja ok / mov ax, di / dec ax / mov [mouse_y], ax   ; clamp to h-1
                                                                      ; and to 0 below
```

That is the whole reason. INT 33h is only ever asked for **relative** motion -
the README notes the game never calls function `0x03`, so absolute position is
irrelevant to it - which means the position is the game's own running total, and
a total accumulated from deltas has to be bounded by something. The bound is
passed in rather than assumed, which is why the same 320 x 200 appears in
`init`'s key-wait loop **before the video mode has been set at all**.

The position is kept as a **32-bit** value with the clamp done on the pair, so a
fast drag cannot wrap it - the high word is tested first, and both bounds close
at zero as well as at the resolution.

Buttons go through an indirection on the way: `[0x20e4]`, `[0x20e6]` and
`[0x20e8]` hold which INT 33h button index means what, so the mapping is data
rather than code.

So main installs a Ctrl-C handler, runs `init`, and sets the video mode - the
mode number coming from `[0x4fe]` rather than being hardcoded.

Immediately after the first `input_poll` it calls **`scan_save_slots`**
(`0x13fea`), which is the `GAME1.SG` ... `GAME5.SG` probing visible in every
startup log. The names are not five constants: `save_name` holds the template
`"GAME-.SG"` and the loop patches the digit into offset 4 -
`mov [0x21a9], al` after `add al, 0x30` - then calls `fopen(name, "rb")` and, if
the handle is non-null, reads the slot. The bound is `cmp [bp-2], 6`, so slots 1
to 5.

**It takes no arguments and returns nothing.** The prologue is
`push bp; mov bp, sp; sub sp, 6; push si` - six bytes of locals and no reference
to `[bp+6]` - and the epilogue is `pop si; leave; retf` with no `mov ax`
beforehand, so AX is whatever the last call left. Its **only output is a single
global**:

```
call 0x14e88(FILE*)          ; a value out of the save
mov si, ax
cmp si, [0x2055]
jbe  skip
mov [0x2055], si             ; keep the maximum across all five slots
skip:
lcall fclose
```

Everything else it reads it throws away - two values fetched through `0x14f4b`
are each handed straight to `0x0edb` and not stored, which has the shape of read
a string, free it. So the whole point of the scan is that one running maximum.

`[0x2055]` reads **0** on a machine with no save files, which is consistent but
does not identify it; `0x14e88` would have to be read to know what the value is.
Recorded as `max_save_value?`, tentative.

## The chain

```
image 0x0014e   the C runtime's startup (no prologue; where main is called from)
  └─ 0x144d7    main
       ├─ 0x141fe    init — the whole startup screen
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
the shape matches the index builder's `egg_find_block(0x5a, 1, si)` at `0x05232` — a pass over every
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

## init, and the sound check

`0x141fe` is the program's initialisation, not merely a key wait - the name it
was first given, `press_any_key_wait`, described its last three instructions. It
owns the entire startup screen. Its first act is to print `DUCKS v1.21`
(`DGROUP+0x2808`), which is the first line the program shows at all, and in order
it then:

- allocates **three** 22-byte objects into `init_objects` (`[0x210c]`, far
  pointers with a stride of 4), sets `+0xc = 316` and `+0xe = 15` on each, and
  initialises them through `0x15388`
- prints the remaining banners
- calls `detect_hardware`, stores the result in `sound_available`, and inits the
  sound module only if it is non-zero
- spins on `input_poll` until `last_key` is set

Caught by arming the BLASTER parser and the DSP write and pressing the key:

`0x141fe` is not only a key wait, which is what it was first named for. Caught by
arming the BLASTER parser and the DSP write and pressing the key:

```
main 0x144d7
  └─ 0x141fe  init
       └─ 0x14974  detect_hardware
            ├─ 0x148a2  detect_soundblaster      <- the sound check
            │    └─ 0x149ea  dsp_write           (polls 0x22c, writes the byte)
            │         └─ 0x15b37  parse_blaster_env
            │              ├─ 0x0388b  getenv("BLASTER")
            │              └─ 0x15a49  blaster_env_field
            └─ the XMS half, inline
```

`detect_hardware` is the whole three-line block on the startup screen: it calls
the sound check, gives up printing nothing if that fails, then checks XMS -
`HIMEM.SYS not installed` if absent - and finally prints `Free XMS memory: %uk`.

### What follows the sound check

The rest of `init`, read at the breakpoint on the return:

```
mov [sound_available], ax          ; detect_hardware's result
cmp [sound_available], 0
je  skip                           ; no sound - skip the init entirely
push 0x2af8 / lcall 0x15ae:0x346   ; sound module init
skip:
call print_newline
push 0xf / lcall set_text_colour   ; colour 15
push ds / push 0x28e7 / lcall puts ; "Press a key to begin..."
call print_newline
loop:
  push 200 / push 320
  call input_poll                  ; the same poll the mouse wrappers hang off
  cmp [last_key], 0
  je loop
leave / retf                       ; main resumes at 0x144ec
```

`[0x18f6]` is `last_key` and holds the ASCII of the key: read at the breakpoint,
after a Return had been sent, it contained `0x0d`. The loop simply spins on it.

The word pushed to the sound module, `0x2af8`, is **11000** decimal, which fits
the card being retuned to 11111 Hz immediately after. It is passed as a single
word so it could in principle be a near pointer, but `DGROUP+0x2af8` lands
mid-way through `"HIMEM.SYS not installed"`, which is not a string anyone meant
to pass - so the numeric reading is the sane one. Not proven.

`parse_blaster_env` copies the variable into a 128-byte local and pulls out one
field at a time through `blaster_env_field`, which takes the letter: `0x41` `A`
for the base address, `0x49` `I` for the IRQ, and so on, each with an
out-parameter. That is why a breakpoint on it fires repeatedly - it is called per
field, not once - and why the routine that looked like a `getenv` wrapper is not
one. The real `getenv` is `0x0388b`.

## main sets the video mode itself

`0x0c156` does not: the machine is already `mode=0x13` with 116 page flips behind
it when that function is entered. Catching the `0x03 -> 0x13` transition on a cold
boot names the real path — watching the machine's `mode` rather than
breakpointing the INT 10h wrapper, which is called 1,493 times a session for
cursor moves and palette blocks:

```
main 0x144d7  at 0x144f4
  └─ 0x13519  set_mode_x
       └─ 0x04d04  set_bios_mode
            └─ int86 0x0293a  ->  INT 10h, AX=0x0013
```

So **main sets the mode directly, at `0x144f4`** — before the loader passes it
calls at `0x1452a`. `set_bios_mode` builds `AH=0, AL=mode` into a register struct
and hands it to `int86`; `set_mode_x` then unchains the result in place:

```
out 0x3c4, 4 / out 0x3c5, 6      ; sequencer memory mode: chain-4 off
out 0x3d4, 0x14 / out 0x3d5, 0   ; CRTC underline = 0
out 0x3d4, 0x17 / ...            ; CRTC mode control
```

which matches the IN/OUT inventory in [port-io](port-io.md), where the image's
only `0x3c2` write and one of its `0x3c4`/`0x3c5` pairs are attributed to
`0x13519`.

**A wrap worth knowing about.** `0x13519`'s call decodes statically as image
`0x14d04`, and the runtime says `0x04d04` — 64 KB apart. `call rel16` wraps within
its segment, and image offsets are not segment offsets, so a statically computed
near-call target can land a segment away. The stack is the authority.

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
