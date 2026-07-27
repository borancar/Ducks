# Open bug: navigating the in-game readme jumps into DGROUP data

**Open as of 2026-07-27.** Navigating the in-game readme crashes:

```
[cpu] Invalid instruction (UC_ERR_INSN_INVALID) at 19a5:210a
```

Reproducible within a few seconds of play, every time.

## What is established

`0x19a5` is the DGROUP segment, so that address is data, not code. Control
arrives at **DGROUP+0x1d82** with **TF and DF set**, crawls ~900 bytes of BSS
(`00 00` decodes as a harmless `add [bx+si], al`), raising 440 `INT 01h`
single-step traps, and dies on an invalid opcode at DGROUP+0x210a. The reported
fault address is the end of the wreck, not the cause.

## The lead to follow

`--trace-blocks` names the transferring block as `0x00f34`, a Borland DOS
wrapper whose tail is:

```
0x00f3e  mov al, byte ptr [bp + 6]
0x00f41  push ds
0x00f42  lds dx, ptr [bp + 8]
0x00f45  int 0x21
0x00f47  pop ds
0x00f48  pop bp
```

The blocks just before it run "outside the image" (`0x000ca`, `0x000fa`,
`0x00645`…), which is our own INT 21h shim territory, and `0x00f2a` is reached
twice right before. So suspect the **INT 21h path taken by that wrapper** —
including whether `_dispatch_to_guest` should be handing this vector to a guest
handler at all. Note that `0x00f2a`/`0x00f39` are the runtime's setvect/getvect
helpers (the INT 23h installer at `0x00eb5` calls `lcall 0,0xf39`).

## Ruled out

Both `iret`s in the image (`0x00eb4`, the INT 23h handler; `0x15916`, the Sound
Blaster IRQ). The iret guard never fires.

An earlier conclusion that an iret was responsible came from popping `19a5:1d82`
from below SP and finding it landed exactly on the arrival address. But those two
words are SS and DS, the values this program pushes most often, so the match was
coincidence. Refuted by instrument, not by argument — see
[verification-lessons](verification-lessons.md).

## Instruments available

Committed in `255e9f8`: `crash_report()` on any fault; the wild-jump trap, which
fires on the first instruction executed outside the code region and is silent
until then; the iret guard, which checks SP at each iret against the value the
handler was entered with; and `--trace-blocks` for the last 24 basic blocks
(~30% slower, 7 fps rather than 10).

## Not yet done

The obvious bisect. `--no-native-file`, `--no-native-setup` and friends all exist,
and one reproduction each would say whether a native is responsible at all. Do
that before reading more disassembly.
