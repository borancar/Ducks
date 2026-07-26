# Ducks! unpacking and emulation tooling

Tools for analysing `Ducks.exe` — a DIET-compressed 16-bit DOS executable
(Ducks! v1.2, Tim Furnish / Hungry Software, 1998-2000) sitting in the parent
directory.

Two things happen here: the packed executable is recovered to a plain EXE, and
the game is run under an emulated DOS with an SDL window so its actual behaviour
can be observed rather than guessed at from disassembly.

**The host filesystem is only ever read.** The DOS shim serves reads from the
real game directory and satisfies the program's writes from an in-memory
overlay, so `settings.dat` and save files are never created or modified.

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
venv/bin/python play.py --scale 3
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

## Files

| file | purpose |
| --- | --- |
| `unpack_ducks.py` | emulates DIET's stub, writes a plain EXE |
| `validate.py` | verifies the unpack, incl. the round-trip check |
| `play.py` | DOS + VGA + SDL; runs the game interactively |
| `trace_dos.py` | headless DOS shim; logs interrupts, files, ports |
| `analyze.py` | static census of interrupts/ports over the unpacked code |
| `modex_probe.py` | re-renders captured planes under candidate layouts |
| `decode_egg.py` | decodes the +1 character shift in `.egg` data files |
| `check_relocs.py` | cross-checks the relocation set two ways |
| `trace_relocs.py` | attributes each relocation write to its instruction |
| `find_extent.py` | derives the true stored-image size |
| `test_seg.py` | probes Unicorn's real-mode segmentation behaviour |
