# Driving a running machine over a socket

`--control-socket PATH` makes a running machine answer commands on a Unix socket.
One line in, one line back, connection closes:

```sh
venv/bin/python native.py --control-socket /tmp/ducks.sock
printf 'status\n'          | nc -U /tmp/ducks.sock
printf 'key down\n'        | nc -U /tmp/ducks.sock
printf 'text hello\n'      | nc -U /tmp/ducks.sock
printf 'snap page 3\n'     | nc -U /tmp/ducks.sock
printf 'quit\n'            | nc -U /tmp/ducks.sock
```

| command | effect |
| --- | --- |
| `key <name> [frames]` | press a key, held for `frames` display frames (default 2) |
| `text <string>` | press each character in turn |
| `snap [note]` | capture at the next page flip, exactly as F2 does |
| `status` | frame, mode, flips, pending keys, CS:IP |
| `quit` | stop the run, as F12 does |

Key names are pygame's - `down`, `escape`, `return`, `a` - which avoids inventing
a second name-to-scancode table: the name resolves to a pygame key and
`emulation.KEYMAP`, the same table the window's event loop uses, turns it into the
scancode and ASCII pair the guest reads.

It is built in `build_machine()`, so **`replay.py` gets it too**: a captured state
can be restored headlessly and then driven.

## Why a socket rather than a `--keys` flag

A key list on the command line has to be right before the run starts. The inputs
worth sending are the ones chosen after looking at what is on screen, and the
useful loop is *look, press, look again* - which needs the process to stay alive
and answer questions. Add `snap` to that and a state found by poking around can be
kept.

This is what turned the readme crash from "reproducible within a few seconds of
play" into a two-second headless test: capture the state just before the input
that breaks it, and send the input. See
[open-readme-crash](open-readme-crash.md), where it made the long-deferred flag
bisect cost four minutes.

## Two traps

**`disasm` prints linear addresses, and so do capstone's branch operands.** Every
note in this project uses image offsets, which are `image_base` lower. The
instruction column is tagged `i+0x…`, and each branch now also carries
`-> i+0x…` with the symbol if it has one — resolved through the segment, so the
64 KB wrap of `call rel16` is handled rather than left to whoever reads it. Both
wrong addresses recorded on 2026-07-28 came from doing that conversion by hand;
see [address-spaces](address-spaces.md).

**A command is serviced at a frame boundary, so two commands are a chunk apart.**
`key return` followed by `step` does not step from the keypress: the machine has
already run up to 400,000 instructions in between. Arm a `break` first, then send
the key. Breakpoints stop inside the slice loop, not just at the top of a frame,
which is what makes them land on the exact instruction.

## The one rule

Nothing the listener receives touches the machine on the listener's thread.
Commands are queued and applied by `_service_control()` from the emulator thread -
once per display frame in `step_frame()`, and again from `native_page_flip()`, so a
key sent from a shell lands within a game frame rather than within a chunk.
Anything else would be writing guest state underneath a running `emu_start`.

A press is held rather than being instantaneous. `key_buf` is a queue the guest
drains at its own pace, but `last_scancode` is the port 0x60 view, where a key
that is never released stays down forever.

See [testing-from-snapshots](testing-from-snapshots.md) for where the states come
from.
