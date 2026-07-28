# Accepting a call silently is not the same as answering it

**Found and fixed 2026-07-28.** The startup console under `emulation.py` was
misaligned: the game's 80-column rules started mid-line and ran over the
messages, and one message never appeared at all.

```
Building episode index...-------------------------------------------------------
-------------------------MAIN.EGG: Full Version
```

The cause was one entry in a list.

## The chain

`trace_dos.py` had a branch for calls it accepts and ignores:

```python
if ah in (0x49, 0x4A, 0x33, 0x38, 0x44, 0x0B, 0x62):
```

`0x44` is IOCTL. Accepting it returns with **DX untouched**, so the guest read
whatever DX happened to hold — `0x0004`, `0x0003`, `0x8101`, different every run.

Borland's runtime asks `AH=44h AL=00h` for each standard handle to decide how the
stream is buffered. Bit 7 means character device. Told stdout was a *file*, it
buffered the startup messages fully and never flushed 49 bytes, so:

- nothing was written — `_write` at image `0x04b10` was entered **0 times**;
- so `_tty` never ran, so the **BIOS cursor never moved**;
- and the game positions the glyphs it pokes into `0xb8000` itself by asking
  INT 10h `AH=03h` where the cursor is.

So a stale register in an ignored DOS call came out as text in the wrong columns,
three layers away. The visible symptom had nothing to do with text output.

## The fix

`DosMachine.device_info()` — handles 0, 1 and 2 are the console, everything else
is a file — and an `AH=44h AL=00h` branch that returns it in DX. `0x44` came off
the accept-and-ignore list, without which the new branch was unreachable; that
was worth one wasted measurement, because the answer looked wrong until the
`[ioctl]` log showed the guest still receiving `0x0004`.

`native.py` already served `isatty()`/`ioctl()` at the function level with its own
copy of the rule. It now delegates to the shim's, so the two cannot drift: the
natives remove the interrupt, and with `--no-native-file` the same request falls
through to the shim and must come back the same.

## Proof

Same exe, same dump code, both machines run to the same screen:

| | before | after |
| --- | --- | --- |
| `emulation.py` | 0 chars through `_tty`, 0 writes, stdout empty | 49 chars, 6 writes, handle 1 |
| `native.py` | 49 chars, 6 writes | unchanged |

The two text screens are now identical line for line, where before they differed
on 11 of 18 rows. `--verify-only plane_loop` over a menu snapshot: 837 compared,
0 mismatched, round trip byte-identical.

## The lesson

An `accept silently` list is a decision to return **stale registers**, and a
guest reading them behaves differently on different runs. It is not the same as
"this call does not matter" — for a call that returns a value, the two are
opposites. Worth grepping such lists for anything with an output register: the
fall-through in `_dos()` now logs `UNHANDLED INT 21h AH=..`, which is louder and
better than silence.

See [verification-lessons](verification-lessons.md) — the same shape as
`chain4` in [testing-from-snapshots](testing-from-snapshots.md): what is not
captured, or not answered, does not announce itself.
