# Ducks! unpacking and emulation tooling

Tools for analysing `Ducks.exe` — a DIET-compressed 16-bit DOS executable
(Ducks! v1.2, Tim Furnish / Hungry Software, 1998-2000) sitting in the parent
directory.

Two things happen here: the packed executable is recovered to a plain EXE, and
the game is run under an emulated DOS with an SDL window so its actual behaviour
can be observed rather than guessed at from disassembly.

**`trace_dos.py` only ever reads the host filesystem.** It serves reads from the
real game directory and satisfies the program's writes from an in-memory overlay,
so nothing there is created or modified. That guarantee is what makes it safe to
point at an unfamiliar code path, and it is why behaviour changes go in
`native.py` instead.

**`native.py` does persist saves**, because a save that cannot survive a restart
is not a save. Writes land in the game directory on close, atomically via a temp
file. Anything resolving outside that directory is refused, as is any write to an
`.exe`, `.egg` or `.com` - a write-back to the program's own image is one of the
things this analysis set out to rule out, so an attempt is logged loudly rather
than performed. `--read-only` restores the overlay-only behaviour.

## Setup

```sh
python -m venv venv
venv/bin/pip install capstone unicorn pygame-ce
```

## Unpacking

```sh
venv/bin/python unpack_ducks.py ../Ducks.exe -o Ducks.unpacked.exe
venv/bin/python validate.py
```

`unpack_ducks.py` does not reimplement DIET's LZ77 format. It loads the EXE as
DOS would into a Unicorn x86-16 CPU, lets DIET's own loader stub decompress
itself, and stops at the handoff to the original entry point. Whatever is in
memory then is the original image, by construction.

Two details that took working out:

- **Image extent.** `SS:SP` overshoots, because the stack and BSS are not stored
  in a DOS EXE file, and DIET parks its decompressor stub in that future stack
  area. The overall write watermark overshoots too, because the stub's initial
  `rep movsw` copy-up leaves packed scratch above the real output. The extent is
  taken from the decompression output stores, discriminated by *address span* —
  the stack pushes are equally high-volume but touch only a few bytes.
- **Relocations.** Identified by running the unpack twice at different load
  segments and diffing: bytes differing by exactly the segment delta are the
  relocated words. This avoids trusting any reading of DIET's relocation
  encoding. The result (707) matches the stub's own `mov cx,0x2c3` loop counter.

`validate.py` checks the result, the decisive test being a round trip: applying
the emitted relocation table at a load segment must reproduce DIET's own output
byte-for-byte.

## Running the game

```sh
venv/bin/python emulation.py --scale 3
```

Controls: **F9** pause/resume, **F10** capture, **F11** cycle the Mode X start
address multiplier, **F12** quit. From a shell, `touch capture.request` or
`touch pause.request` does the same without needing window focus.

A capture writes to `debug/`: the window PNG, an unscaled 320x200 PNG, the raw
four VGA planes, the 64 KB aperture, the palette, and a JSON dump of VGA
register state. The raw planes matter — `modex_probe.py` replays them under
alternative layouts offline, so a display bug can be diagnosed without playing
back to the same screen.

Notes on what the emulation has to get right:

- The game runs in **Mode X** (unchained planar VGA), so writes are shadowed
  into four separate planes according to the sequencer map mask. Unicorn's
  memory is flat and would let the planes overwrite each other.
- It page-flips via the CRTC start address, whose **unit** comes from CRTC
  `0x14` bit 6 and `0x17` bit 6. Ducks uses byte addressing; assuming
  doublewords puts page 1 out of range and renders every other frame black.
- Port `0x3da` bit 0 (display enable) runs at the ~31.5 kHz horizontal rate, not
  the frame rate. A snow-avoidance blit waits for a transition of that bit for
  every word it copies, and stalls forever if the bit is driven from wall clock.
- INT 34h-3Eh must be dispatched to the program's own handlers: that is
  Borland's 80x87 floating-point emulation.
- The game refuses to start without an INT 33h mouse driver.
- There is **no `INT 33h` instruction in the image**: Borland's `int86` patches
  the vector number in at runtime, so a static search finds nothing and cannot
  locate the callers. Walking the BP chain at interrupt time does, giving
  `int86 <- {0x0675b motion, 0x0678e presses, 0x067ba releases} <- 0x06869 poll`.
  Confirmed identical in menus and gameplay, so those three wrappers are the
  game's entire mouse input and `--native-mouse` replaces all of it.
- Mouse input arrives entirely through INT 33h `0x0b` (relative motion) and
  `0x05`/`0x06` (per-button press/release counts). It never calls `0x03`, so
  absolute position is irrelevant. `0x05`/`0x06` take **BX as the button being
  queried** (0=left, 1=right, 2=middle) and must return that button's count and
  then clear it; ignoring BX makes every button behave as one, and per-button
  actions never fire. The button mask must be tracked from the SDL events
  themselves — `pygame.mouse.get_pressed()` lags a press behind.

## Sound

```sh
venv/bin/python emulation.py --blaster        # advertise BLASTER=A220 I5 D1
```

`sb.py` models the DSP, the 8237 DMA channel and the IRQ. Ducks drives it in a
completely standard way: reset, get version, time constant `0xd3` (22222 Hz),
speaker on, block size 256, then auto-init 8-bit DMA output over a 512-byte
buffer. Note the two periods are independent — the DMA controller wraps every
`count+1` bytes while the DSP interrupts every `block_size` bytes, which is what
makes the double buffer work; conflating them halves the refill rate.

Captured PCM is written to a WAV on exit, which is the only trustworthy way to
tell a broken card model from a broken host playback path.

**Sound requires XMS.** Ducks keeps its samples in extended memory, and its
startup sound check refuses to enable audio without HIMEM.SYS — it prints
"HIMEM.SYS not installed" and disables the whole audio subsystem, which also
greys out the AUDIO SETTINGS menu. Without `xms.py` the mixer runs but emits
nothing except `0x80` silence, no matter how correct the card emulation is.

Two subtleties that each cost a debugging round:

- The DMA wrap period and the DSP interrupt period are **different**. The
  controller wraps every `count+1` bytes while the DSP interrupts every
  `block_size`; Ducks uses 512 and 256 respectively. Firing the interrupt on
  the wrap instead makes the game refill half as often as it should, so half the
  buffer replays stale audio — audible as choppiness, with the give-away that
  guest writes come to exactly half the bytes consumed.
- XMS `AH=0Fh` takes `BX` = new size and `DX` = handle. Reversed, every
  reallocation fails, the sample block stays at its initial zero size, and every
  sample transfer is rejected on bounds — silently, unless failures are logged.

The rate the game selects is 22222 Hz at startup and 11111 Hz in play. Those are
exact Sound Blaster time constants (`0xd3`, `0xa6`); 22050 is not representable,
so it is not a bug.

Two host-side traps worth remembering: `pygame.init()` initialises the mixer
before `pygame.mixer.pre_init()` can take effect, so the mixer must be explicitly
`quit()` and reopened at the game's rate; and a mixer channel holds only one
queued sound, so overflow must be buffered rather than dropped.

## Native-I/O port

```sh
venv/bin/python native.py --scale 3 --blaster
venv/bin/python native.py --profile        # rank the drawing routines
venv/bin/python native.py --verify         # check a native against the original
```

`native.py` subclasses `emulation.py`. The game logic still runs on the emulated
CPU, but recognised I/O routines are intercepted at their entry: the handler
reads the arguments off the stack, does the work natively, puts the result in AX
and returns to the caller, skipping the original body. Anything not in
`NATIVE_TABLE` falls through to full emulation, so the port converts one routine
at a time and is never in a half-working state.

Ducks is Borland Turbo C++, so its I/O sits behind ordinary C functions with a
regular calling convention, which is what makes this practical.

**Find targets before writing any native.** `--profile` attributes every write to
video memory to the instruction that made it, then walks back to the enclosing
function via its `push bp; mov bp,sp` prologue, giving a ranked list of what
actually does the drawing. Tracing is off by default and toggleable at runtime —
**F5** on/off, **F6** report, or `touch trace.on` / `trace.off` / `trace.report`
— so a specific slow moment can be measured without paying for the whole session
or replaying to reach it.

**Verify before trusting.** `--verify` runs the native into a snapshot, restores
it, lets the original body run, and diffs the planes on return. A hand-translated
blitter can be wrong in ways that still look plausible on screen; this is what
`emulation.py` is kept for.

One structural win worth knowing: the sprite blitter draws only pixels whose
`x & 3` matches the current plane, so the game calls it four times per sprite —
four times the loop iterations for one sprite's pixels. A native does all planes
in one pass.

## The drawing path

Mode X puts column `x` in plane `x & 3` at byte `x >> 2`, so every drawing routine
in the game filters on the selected plane and every caller runs the whole thing
four times. Two four-plane loops drive everything on screen, and both are now on
this side:

| Loop | Where | Inside | Draws |
|---|---|---|---|
| `plane_loop_layer` | `0x0cd5f`-`0x0cd98` | `0x0c716`-`0x0ce2d`, 1816 B | menus and between-level screens: one compositor pass and one scene |
| `plane_loop_scroll` | `0x0e4dc`-`0x0e673` | `0x0d7ee`-`0x0e8ac`, 4287 B | the in-game frame: the scrolling background, particles, seven scenes, three panels, two numbers and a five-pixel run |

Neither loop is a function — both are inline in a much larger one, which is why
they are replaced at the instruction like the interrupt stubs rather than hooked at
an entry, and why the handler reads its locals off the live `BP` frame. The
enclosing extents matter for one specific claim: the loops share a plane counter at
`[bp-0x1f]` with a *third* plane loop at `0x0d9a2`, in the same function as the
scroll one, and the native leaves that counter alone. All eight references to it
sit inside `0x0d7ee`-`0x0e8ac` and all eight belong to those two loops, so nothing
after either reads it. A fourth plane loop lives in the score tally at `0x0bba1`.

`function_extent()` in `native.py` gives those boundaries, cross-checked against an
independent rule (the next prologue that resolves to itself); `test_fn_start.py`
keeps the two agreeing.

The in-game loop is the one that matters — `draw_entities` gets most of its ~34000
calls a session from it. Per plane it does:

```
set_plane(plane)                                  # 0x57ee
compose_scroll([0x1739], [0x173d])                # 0x5dc4
if [0x4f6]: particles()                           # 0xab09
draw_entities(scene, ds:0x172d, 0)                # 0xaba5, five layers
if arg == 0:   draw_entities(ds:0xd93,  ds:0x172d, 0x90)
if [0x178b]:   draw_entities(ds:0x178c, ds:0x1741, 0x90)
for i in 0..2: if [0x2154+i]: blit_rows_masked(...)   # 0x5ac2, flashing panels
if [bp-8]:     draw_number([bp-6],   0x80, 0x22, ..., 6)   # 0xbb3b, the score
if [bp-0xa]:   draw_number([0x2007], 0xe1, 0x22, ..., 2)
for x in [bp-2]..[bp-4]: (*[0x53e])(x, y, 0)      # 0x5761, five pixels
```

**The scene table.** Six 12-byte records live at `DGROUP:0xd63` — count at `+2`, a
far pointer to the entity array at `+8` — and the loop draws them back to front in
the order 1, 0, 2, 3, 5, then 4. Record 4 (`0xd93`) is drawn only when the
function's argument is zero, and it is the only one the menu loop draws. A seventh
scene sits at `0x178c` behind a flag, with a different viewport. Viewports are
20-byte records at `0x172d`, `0x1741` and `0x1755`, passed to the blitter as its
clip rectangle.

**Numbers are sprites.** `draw_number` (`0x0bb3b`) draws glyph `0x71 + digit` from
the same sprite table the entities use, 12 pixels apart, least significant digit
first: a fixed-width right-aligned field with no leading-zero suppression, so a
score of nothing is six noughts.

**Two plotters, and a lesson.** `0x5761` computes its row stride as 80 with no
`[0x4fe]` resolution check — which looks like a bug until you find `0x57a1`, the
same routine with a stride of 90. The game swaps a far pointer at `[0x53e]` when it
changes resolution rather than testing inside the routine. Resolving that pointer
by its offset word alone recognises neither (the segment is not the one image
offsets are measured from), and the failure is silent: the loop just skips its
pixel run. `--verify` reported it as five mismatched bytes a call, and only on the
frames where those pixels were not already zero.

## Removing interrupts

```sh
venv/bin/python native.py --native-xms --native-setup --native-fp   # plus the I/O flags
```

`--run-seconds N` exits cleanly for a measurement run with nobody at the
keyboard. The exit report lists every interrupt still executed, grouped by
service and attributed to the function that raised it. Counts alone cannot drive
removal: the goal is to replace the code raising the interrupt, so the address is
what matters.

A full play session went from **26821 interrupts to 140**, in three steps.

**XMS (194).** All of it was self-inflicted. `xms.py` publishes the driver entry
as a three-byte stub, `INT 60h; RETF`, so every request trapped through a vector
to reach an API that was already pure Python. Hooking the entry address instead,
and doing the far return the `RETF` would have done, removes all of them with no
semantic change. The game caches the entry at `DGROUP:0x2b46` and reaches it by
`lcall`, so one hook catches every call. The only other XMS interrupts were the
two `INT 2Fh` detection sites, now natives - but still answering "installed",
because Ducks disables sound entirely without XMS.

**Two runtime helpers (1486 + 800).** `0x02e07` is Borland's `_dos_setblock`;
our DOS never fails it, so the function reduces to the constant it returns on
success. `0x02067` is Borland's `INT 10h` wrapper, which passed get- and
set-cursor straight through to a shim that ignores them - the game draws its own
text in Mode X, so the BIOS cursor means nothing. Its mode-set and mode-query
paths chain several `INT 10h` calls and touch BIOS variables, so those decline
and are left to run.

**Borland's floating point (24207, i.e. 90% of the total).** Turbo C++ emits each
x87 instruction as `FWAIT` + ESC opcode (`D8`-`DF`) + ModRM + displacement.
Linking against the emulator library overwrites exactly that two-byte
`FWAIT`+ESC pair with a two-byte `INT`, leaving the operand bytes untouched - so
the interrupt number *carries the opcode*: `INT 34h`..`3Bh` for `D8`..`DF`, and
`INT 3Dh` for a lone `FWAIT`. The handler decodes the ModRM at its own return
address. Undoing it is a two-byte write, and Unicorn implements x87 in real mode,
so the instructions the compiler originally wrote can just be put back.

The bytes at `0x00e33` are the proof - substituting `D9`/`DF` back yields
`fnstcw` / `fldcw` / `fistp` around an `or [bp-1],0x0c`, which is `__ftol`
forcing round-toward-zero.

Three things this depends on:

- **Sites patch themselves on first execution**, not in a static sweep. A
  two-byte scan for `CD 34`..`CD 3B` over 114 KB of 16-bit code cannot tell code
  from data - this binary desynchronises constantly under static disassembly,
  which is why the tracer exists - and a false positive corrupts whatever it hit.
  An interrupt that just fired is proof those bytes are an instruction.
- **The two FP representations must never coexist.** Borland's emulator keeps its
  stack in memory, the real FPU keeps its own. Patching before the handler ever
  runs means no operation touches the memory stack. `INT 3Ch`
  (segment-prefixed ESC) and `3Eh` cannot be reversed with confidence, so they
  warn loudly instead of quietly running one operation on the wrong stack. Neither
  has been observed to execute.
- **`FINIT` must run once.** A real FPU powers up with control word `0x037f`;
  Unicorn's starts at zero, selecting single precision and unmasking every
  exception. Without it `__ftol` still works by luck, but at the wrong precision.

Only 42 sites exist, and a menu-only session reaches all of them: the game's
floating point is two functions (`0x0d5c5`, `0x0c716`), `__ftol`, startup, and a
helper block at `0x08f5c`.

**The DOS layer under the native file I/O.** `close`, get-file-attributes,
`isatty` and `ioctl` are thin wrappers - registers, one `INT 21h`, map the
result, and on carry route DOS's error code through the errno helper at
`0x011ed`. Reproducing that helper is what made them worth replacing: without it
a native must decline on failure and let the interrupt happen anyway, and failure
is the common case, since the save-slot scan stats five slots of which four are
usually empty. It stores the DOS code in `_doserrno` at DGROUP:`0x2f9c`,
translates it through the byte table at `0x2f9e` into `errno` at DGROUP:`0x7f`,
and returns `0xFFFF`.

**One-shot startup interrupts.** DOS version, the heap shrink, BIOS ticks and
equipment, date, time, and the 33 vector saves and installs. These are inline in
the C runtime rather than behind a callable function, so they are answered at the
instruction: service in Python, then step IP past the two bytes. Being one-shots
they cannot be discovered adaptively the way the floating-point sites are - the
first execution is the only one - so the addresses are hardcoded, and verified to
hold the expected `CD nn` before being hooked.

This needs the **unpacked** image (`--exe Ducks.unpacked.exe`). Given the packed
original the machine starts on the DIET stub with the game still compressed, so
those addresses hold compressed data when the hooks go in, every site fails
verification, and the count printed at startup says so.

The tick and date/time stubs return real host values rather than constants. The
startup reads ticks to seed the random number generator, so freezing it would
make every session play out identically - a larger behavioural change than any
interrupt removed here.

**int86, and the last two.** x86 has no INT with a register operand, so when the
number is a variable the runtime assembles one: `0x293a` writes
`55 CD nn 5D CB` - `push bp; int nn; pop bp; retf` - into its own stack frame and
calls it. That is why the final interrupts came from an address outside the image
with nothing to disassemble: the instruction is on the stack, written moments
before it runs. Nothing static finds this; the stack does. At the interrupt, the
frame chain leads back through `int86` to its caller, which `find_int86.py`
prints - it found `0x293a` from the mouse reset in one run.

Replacing that worker covers both `int86` and `int86x`. It takes the interrupt
number and far pointers to the input, output and segment register structs
(`ax, bx, cx, dx, si, di, cflag, flags`), so the native loads the registers,
dispatches to the same handler the interrupt would have reached, and writes the
results back. Only a whitelist of interrupts is served - the handlers known to
read just the general registers - and anything else declines and lets the stub
run.

With that, a session executes **no interrupts at all**.

Superseded, for the record: `open` (14) and `write` (9), which maintain
the file-flags table at DGROUP:`0x2f6e` on success and so were left alone rather
than risk the save path; the console read behind `getch` (3), which sits 0xa2
bytes inside a function rather than behind a wrapper; the two `INT 10h AH=0Fh`
mode queries the video wrapper deliberately declines; and the mouse reset and
mode set that Borland's `int86` builds at runtime, patching the interrupt number
into a buffer - self-modifying by construction, and one call each.

## The background warp: never seen, so never verified

The v1.2 changelog mentions a warped background, and `compose_scroll` has the
code for it: when `[0x2022]` is set, each row's x displacement comes from a
32-entry table at `[0x179f]`, starting at phase `[0x17bf]` and stepped by
`[0x17c0]` per row.

**It has never executed.** Every session reports `background-warp path: 0 calls`.
So that branch has only ever been read out of the disassembly - unlike the rest
of `compose_scroll`, which is byte-compared against the original body. If it ever
runs, the game prints a warning saying so and naming what to check.

Two things to know if that warning ever appears:

- Verify it before trusting the screen: `--verify-only compose_scroll` byte-compares
  the native against the original on every call, even though the compositors are
  in `VERIFY_SKIP`.
- If it mismatches, doubt the phase sequence first. The original re-masks the
  phase to `0x1f` **every row**, so it is not an arithmetic progression and cannot
  be flattened to `table[(phase + row * step) & 0x1f]`. That is why the vectorised
  version still builds the per-row displacements in a Python loop before
  compositing in one go.

It is also the one part of the drawing code where an error would be easy to miss:
a wrong displacement gives a plausible-looking background, not a broken screen.

## The game's sound API

```sh
venv/bin/python native.py --blaster --native-sound
```

Recovered by disassembly, Ducks' sound layer is eight voices:

| address | function |
| --- | --- |
| `0x151d2` | `play_sample(desc_far, id, loop)` -> 1, or 0 if all voices busy |
| `0x15176` | `stop_voice(slot)` |
| `0x15267` | `stop_sound_by_id(id)` |
| `0x15298` | `is_sound_playing(id)` |
| `0x156cc` | per-voice mix; `0x155be` streams from XMS; `0x157c1` gathers to DMA |

A 12-byte voice slot at `DGROUP:0x3c78`: `+0/+2` descriptor far pointer, `+4`
caller-supplied sound id (`0xffff` = free), `+6/+8` 32-bit cursor, `+10` loop
flag. Busy flags at `0x3cd8` (one word per slot), active count at `0x3d1c`.
A sample descriptor is `+0` XMS handle, `+2` dword start, `+6` dword length -
confirmed by checking every observed descriptor against the handle it names.

Everything under that API exists only because a real-mode program cannot address
extended memory: samples live in XMS, get copied down into a conventional
staging buffer, are additively mixed into a 16-bit accumulator, passed through a
clip table and fed to the DSP a block at a time. `--native-sound` replaces the
whole chain with pygame channels, slicing PCM straight out of the XMS bytearray.

Notes for anyone touching this:

- **Intercept the whole family or none of it.** The game queries and stops sounds
  by id and reads the active count. If pygame owns playback while the guest voice
  table disagrees, every slot looks busy and sounds stop starting.
- **Neutralise `mix_voice`**, or the sample also reaches the DSP path and plays
  twice, slightly out of step.
- **Samples are signed 8-bit** - the mixer sign-extends with `cbw` before
  accumulating. Feeding them to an unsigned mixer unconverted gives noise.
- **`pygame.init()` opens the mixer first**, so it must be `quit()` and reopened
  at the game's rate. Testing `get_init()` first silently leaves 44100/16/stereo.
- **Volume is not per-voice.** `play_sample` takes only id and loop; gain lived in
  the clip table that `--native-sound` bypasses, so AMBIENCE VOLUME currently has
  no effect on that path. Known gap.

Sound was never a performance problem - 11 KB/s against 4.5 MB/s for graphics -
so this is architectural, not an optimisation.

## Files

| file | purpose |
| --- | --- |
| `unpack_ducks.py` | emulates DIET's stub, writes a plain EXE |
| `validate.py` | verifies the unpack, incl. the round-trip check |
| `emulation.py` | DOS + VGA + SDL; runs the game interactively |
| `sb.py` | Sound Blaster DSP, DMA channel and IRQ model |
| `xms.py` | XMS / HIMEM.SYS driver; without it the game has no sound |
| `nsound.py` | the game's 8-voice sound API implemented on pygame |
| `find_sound_code.py` | finds the code referencing a given string constant |
| `trace_dos.py` | headless DOS shim; logs interrupts, files, ports |
| `analyze.py` | static census of interrupts/ports over the unpacked code |
| `modex_probe.py` | re-renders captured planes under candidate layouts |
| `decode_egg.py` | decodes the +1 character shift in `.egg` data files |
| `check_relocs.py` | cross-checks the relocation set two ways |
| `trace_relocs.py` | attributes each relocation write to its instruction |
| `find_extent.py` | derives the true stored-image size |
| `test_seg.py` | probes Unicorn's real-mode segmentation behaviour |
