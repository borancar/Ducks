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
0x144e9  call init                   ; the whole startup screen
0x144ec  push [video_mode]           ; [0x4fe], not a literal
0x144f1  call set_mode_x
0x144f7  mov [0x20a7], 1
0x144fd  push 0 / push 0
0x14502  call dac_set_black          ; image 0x0572a
0x14508  push 200 / push 320         ; the resolution
0x1450f  call input_poll             ; image 0x06869
0x14516  call scan_save_slots        ; image 0x13fea
0x14519  push 100 / push ds:0x28ff   ; a far pointer and 100
0x14520  call 0x102d7                ; unnamed; prologue reserves 0x32 locals
0x14527  call 0x0c156                ; the loader pass
0x1452f  call 0x04ca0 (0x2b, 1)      ; unnamed; tests [0x4f4] first
```

Every address above is the **call instruction**, taken from the disassembly of
this run of bytes rather than from a backtrace — see
[the addresses a stack walk gives](#the-addresses-a-stack-walk-gives-are-return-addresses).
Each is preceded by the `push cs` of the `push cs; call near` idiom, one byte
earlier. The three call targets that wrap 64 KB in image space (`0x0572a`,
`0x06869`, `0x0c156`) were resolved by the prologue rule from
[address-spaces](address-spaces.md), checked first against the four targets
already known.

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

## main, in full

**Mapped 2026-07-29.** Near targets resolved at `CS=0x05da`, the segment main was
observed running in, so the segment base is image `0x4ca0`; `lcall S, O` resolves
to image `S*16 + O`, because the encoded segment is relocated at load. The check
that both rules are right is `install_int23` landing on its already-known
`0x00eb5` — get either wrong and it does not.

```
0x144d7  main
--- startup -----------------------------------------------------------
0x144e0  lcall install_int23         (far 04ca:f82d)
0x144e9  call  init                  ()
0x144f1  call  set_mode_x            ([0x4fe])
0x14502  call  dac_set_black         (0, 0)          <- the screen is black from here
0x1450f  call  input_poll            (320, 200)
0x14516  call  scan_save_slots       ()
--- the intro: two screen players, interleaved with sounds ------------
0x14520  call  show_splash           (ds:0x28ff, 100)
0x14527  call  egg_load_pass_0x48    ()
0x1452f  call  sound_play_guarded    (0x2b, 1)
0x1453f  call  show_resource         (0x4d, 5,     50, 0xff)
0x1455c  call  show_splash           (es:[bx+0x9c], 100)
0x14567  call  sound_play_guarded    (0x28, 1)
0x14577  call  show_resource         (0x4d, 8,    100, 0xff)
0x1457d  cmp [0x548], 0 / jne 0x145a1  ------------+
0x1459b  call  show_splash           (es:[bx+0xf8], 100)
0x145a6  call  sound_play_guarded    (0x0b, 1)     |
0x145b1  call  0x13676  ?            (ds:0x1916) <-+
0x145b7  cmp [0x548], 0 / jne 0x145fa  ------------+
0x145c9  call  show_resource         (0x4d, 0x64, 250, 0xff)
0x145da  call  show_resource         (0x4d, 0x65, 250, 0xff)
0x145eb  call  show_resource         (0x4d, 0x66, 250, 0xff)
0x145f4  call  0x11efb  ?            (2)           |
0x14605  call  show_resource         (0x4d, 0x67, 250, 0xff)  <-+
--- the game -----------------------------------------------------------
0x1460b  lcall 0x146cd               ()             no arguments
--- teardown -----------------------------------------------------------
0x14613  call  set_bios_mode         (3)            back to text
0x1461a  call  0x140b1  ?            ()
0x1461e  call  0x051b7  ?            ()
0x14621  lcall 0x01e6b               ()             exit; no prologue
0x14627  retf
```

Observed end to end on 2026-07-29 with somebody at the window: the splashes
appear, the sounds play between them, and it arrives at the menu — so the order
above is not only read, it was watched.

**`lcall 0x146cd` is where the game must live.** It takes no arguments, and
everything after it restores text mode and exits, so the menu and gameplay have
nowhere else to be. That is inference from position, not observation — a
breakpoint on `0x146cd` settles it in seconds and has not been done. Its first
instructions are `push bp; mov bp, sp; push si; cmp byte [0x2908], 0`, gated on
`sound_state`. This is the "the real game loop is a different call from main"
that the section below has been pointing at.

**`[0x548]` is the registration flag**, and it gates twice — the third splash
with its sound, and the block of four `show_resource` calls with consecutive
indices. Both are `jne`, so a **registered** copy skips them: the extra intro
screens belong to the unregistered build. It read `0` on the observed run, which
is why every screen appeared. See
[episode-index](episode-index.md#the-registration-state-is-0x548) for the flag,
the owner's name at `[0x542]`/`[0x544]`, and the level threshold that consults
it.

## 0x0c1ad is show_resource, and 0x0b52f is a sixth plane loop

`show_resource` is `show_splash`'s sibling: same job, but the image is loaded by
`(type, index)` rather than handed in as a pointer.

```c
void far show_resource(char type /*0x4d*/, char index, int frames, int x /*0xff*/)
{
    char scratch[0x316];                            /* 790 bytes */
    set_buffer(ss:&scratch);                        /* publish as current buffer */
    clear_vram();
    if (0x05a67(&desc, type, index, 0, 1, x, 1)) {  /* 0 on failure */
        0x0b52f(&desc, frames);
        0x05671(&desc);                             /* release */
    }
    set_buffer(ds:0x13f1);                          /* restore */
}
```

`0x05a67` is a thin forwarder to `0x058b9`. The display is `0x0b52f`, and it is
`show_splash`'s loop again:

```c
fade_direction = 1;  fade_start_colour = 0;
0x0b0c5();                                  /* the palette builder */
do {
    input_poll(320, 200);
    if (si == 0 || last_key || [0x18e5]) fade_direction = 0xff;
    si -= (frames > 0);                     /* counts DOWN; show_splash counts up */
    for (plane = 0; plane < 4; plane++) {   /* <- the sixth plane loop */
        set_plane(plane);
        blit_rows(desc, <viewport at ds:0x1769, 20 bytes by value>);
    }
    page_flip();
    palette_fade_step(0);
} while ([0x1798] != 0);                    /* the identical exit */
```

Its viewport is a global at `ds:0x1769` where `show_splash` builds one on its own
stack, and the frame counter runs down rather than up. Otherwise the two are the
same routine written twice.

## 0x102d7 is show_splash, and it holds another plane loop

**Read out 2026-07-29** by breakpointing its entry on a cold boot, then reading
the body over the socket. `snapshots/snap001.snap` is captured at that entry, so
this state is reachable without playing to it.

```c
void far show_splash(void far *image /* ds:0x28ff */, int frames /* 100 */)
{
    viewport a, b;                                  /* two 20-byte records */
    di = frames; si = 0;
    make_rect(&a, 80, 104, [0x53c], [0x53c] + 320); /* 0x0881d */
    make_rect(&b, 320, 24);                         /* 0x08885 */
    0x0615a(1, 0x53, &loc, 0xff);  0x056f7(0);
    0x0b5cf(image, &loc, 0x12, &b, 0, 0x1c);        /* decodes it */
    clear_vram();
    fade_direction = 1;  fade_start_colour = 0;     /* [0x179a], [0x179b] */
    do {
        input_poll(320, 200);
        if (si == di || last_key || [0x18e5])       /* timeout, key or button */
            fade_direction = 0xff;                  /* = -1, fade out */
        si++;
        for (plane = 0; plane < 4; plane++) {       /* <- another plane loop */
            set_plane(plane);
            blit_rows(&b, <a, 20 bytes by value>, 0);
        }
        page_flip();
        palette_fade_step(0);
    } while ([0x1798] != 0);                        /* until the fade finishes */
    0x05671(&b);  0x088b3(&loc);
}
```

`si`/`di` are a frame counter against the second argument, which is why one
exists: main's `show_splash(ds:0x28ff, 100)` holds the image for 100 frames
unless a key or button cuts it short. That is also why an earlier `finish` over
the socket timed out at 120 s on this call — it was sitting in this loop with
nobody pressing anything, which was misread at the time as the call never
returning.

`fade_direction` and `fade_start_colour` were already named from the fade work,
and land exactly on the two bytes this function sets before the loop, which is an
independent check on the reading.

### The plane loop at 0x10383-0x103b0 is not one of the four

[drawing-port-goal](drawing-port-goal.md) lists four plane loops and says all
four are native. This one is not among them: `set_plane` then `blit_rows` four
times, then `page_flip`. It draws the splash screens and still runs on the
emulated CPU. It would not appear in any profile taken during a level, which is
the likely reason it was missed.

Confirmed from the snapshot rather than from the listing: replaying
`snap001.snap` for five frames records exactly **4 `set_plane`, 4 `blit_rows`,
one `page_flip`** — one iteration of this loop and nothing else drawing.

**It was written up as "the fifth" and that was wrong too.** `0x0b52f` above is
another, found hours later, and a census of calls reaching `set_plane` finds
**26 call sites** across the image — including all four documented loops and both
of these, which is what makes the census trustworthy. So four was never the
count; it was the count *for an in-game frame*. How many of the 26 are
four-iteration loops is **not established** — see
[drawing-port-goal](drawing-port-goal.md).

### The first call draws nothing, and that is correct

**Watched 2026-07-29** on a machine paused inside the first `show_splash`, fully
faded in — `fade_level` 15, `fade_direction` 0, so the palette was at full and
nothing was still moving. The screen was black, and it should have been:

- **All four VGA planes were entirely zero.** Captured and checked offline, 65536
  bytes each, not one non-zero byte. So this is not a palette or fade problem,
  and not a drawing bug: nothing was ever put in video memory.
- The source `blit_rows` is handed, at `[bp-0x32]`, is **allocated but empty**:
  three far pointers into row tables, then `0x0140, 0x0018` — 320 wide, 24 tall.
  The row tables are real, one entry per row, segments `0x15` paragraphs apart.
  Three sampled rows are all zeros.
- The clip at `[bp-0x1c]` is `80, 104, 0, 320`, which is
  `make_rect(&a, 80, 104, [0x53c], [0x53c] + 320)` with `[0x53c]` reading 0.
- `ds:0x28ff`, the first argument, begins with a `00` byte. It sits immediately
  after `"Press a key to begin..."` at `DGROUP+0x2865`-ish, so as a C string it is
  empty.

So `blit_rows` drew exactly what it was given. Whatever should have filled that
320x24 buffer did not, and the call that would have is `0x0b5cf`.

**Pencilled: 320 x 24 has the dimensions of a progress bar**, or of a remnant of
one — a strip that wide and that short, drawn once at a fixed position before any
egg has been loaded, is a poor fit for artwork. Boran's reading, recorded because
the dimensions are the whole argument for it. An earlier guess here that it was a
one-line text banner rests on nothing more, and both are unproven: the measured
facts are the empty source, the empty string and the black planes.

Either way the count changes. `main` makes nine calls that display something, but
the first draws nothing, so there are at most eight things to see.

### Still open here

- `0x0b5cf`, `0x0615a`, `0x056f7`, `0x05671`, `0x088b3` and the two rect builders
  `0x0881d`/`0x08885` are call targets, not identifications. `0x0b5cf` is now the
  interesting one: it is what should have filled the buffer.
- `[0x18e5]`, tested next to `last_key` as a second escape condition.

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
main 0x144d7  at 0x144f1
  └─ 0x13519  set_mode_x
       └─ 0x04d04  set_bios_mode
            └─ int86 0x0293a  ->  INT 10h, AX=0x0013
```

So **main sets the mode directly, at `0x144f1`** — before the loader passes it
calls at `0x14527`.

### The addresses a stack walk gives are return addresses

**Settled 2026-07-29.** This section previously said `0x144f4` and `0x1452a`,
against `0x144f1` in the listing at the top of this note. Disassembling main's
opening from its prologue settles it, and explains both numbers rather than
choosing between them:

```
0x144ec  ff36fe04   push word ptr [0x4fe]
0x144f0  0e         push cs
0x144f1  e825f0     call 0x13519          <- the call
0x144f4  83c402     add sp, 2             <- where it returns to
```

`call rel16` is `E8` plus two bytes, so **a call site read off a backtrace is
three higher than the call instruction**. Both figures here were observed
correctly; they are just answers to a different question. The same +3 accounts
for `0x1452a`, which is where the loader pass called at `0x14527` returns to.

Worth carrying, because everything in this note was established by breakpointing
a live machine: when the socket reports a frame, subtract 3 to name the call
instruction — and the `push cs` of the `push cs; call near` idiom sits one byte
before that again. `set_bios_mode` builds `AH=0, AL=mode` into a register struct
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
