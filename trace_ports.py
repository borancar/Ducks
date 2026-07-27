#!/usr/bin/env python3
"""Record every port read and write from a snapshot, attributed to the code
that made it.

    venv/bin/python trace_ports.py snapshots/main-menu-hover.snap --frames 20

Counts alone have never been enough in this project - the address is what lets a
port access be tied to a routine, and from there to a reason. So each access is
attributed to the instruction that made it and to the enclosing function, the
same way `--profile` attributes writes to video memory.

The handlers are **wrapped, not replaced**. `_on_in` returns the value the guest
reads, so a tracer that shadowed it would change what the program sees; here the
real handler runs and its return value is passed through untouched. The wrapping
is done on the class before the machine is built, because the Unicorn hooks are
registered with bound methods at construction and patching the instance
afterwards would not be seen.

Reads are counted in full but only a bounded number are kept for the raw dump:
`0x3da` bit 0 is polled once per word by the snow-avoidance blit, so a busy
frame can produce hundreds of thousands of accesses. What was dropped is
reported rather than silently truncated.
"""

import argparse
import os
import sys
import time
from collections import Counter, defaultdict

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from unicorn.x86_const import UC_X86_REG_CS, UC_X86_REG_IP    # noqa: E402

import emulation                                              # noqa: E402
import native                                                 # noqa: E402
import pygame                                                 # noqa: E402
import snapshot                                               # noqa: E402

# What each port is, so the output reads as hardware rather than as numbers.
PORTS = {
    0x40: "PIT ch0 counter", 0x41: "PIT ch1", 0x42: "PIT ch2",
    0x43: "PIT mode/command",
    0x60: "keyboard data", 0x61: "keyboard control",
    0x201: "joystick",
    0x3C0: "attribute controller", 0x3C2: "misc output",
    0x3C4: "sequencer index", 0x3C5: "sequencer data (map mask at index 2)",
    0x3C6: "DAC pel mask", 0x3C7: "DAC read index",
    0x3C8: "DAC write index", 0x3C9: "DAC data",
    0x3CE: "graphics ctlr index", 0x3CF: "graphics ctlr data",
    0x3D4: "CRTC index", 0x3D5: "CRTC data",
    0x3DA: "input status 1 (bit 0 display enable, bit 3 vertical retrace)",
    0x220: "SB DSP reset", 0x22C: "SB DSP write", 0x22A: "SB DSP read",
    0x22E: "SB DSP read-buffer status", 0x226: "SB reset",
    0x00A: "DMA mask", 0x00B: "DMA mode", 0x00C: "DMA flip-flop clear",
    0x002: "DMA ch1 address", 0x003: "DMA ch1 count", 0x083: "DMA ch1 page",
    0x020: "PIC 1 command (0x20 = end of interrupt)", 0x021: "PIC 1 mask",
    0x0A0: "PIC 2 command", 0x0A1: "PIC 2 mask",
}


def describe(port):
    return PORTS.get(port, "")


def main():
    ap = argparse.ArgumentParser(
        description="Trace port I/O from a snapshot. Unrecognised flags go to "
                    "native.py.")
    ap.add_argument("snapshot")
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--out", default="",
                    help="write the raw sequence here, one access per line")
    ap.add_argument("--max-events", type=int, default=200_000,
                    help="cap on kept raw events; counts are always complete")
    ap.add_argument("--sites", type=int, default=4,
                    help="call sites to name per port")
    args, extra = ap.parse_known_args()
    nargs = native.make_parser().parse_args(extra)

    counts = Counter()                       # (kind, port) -> n
    # Time inside the real handler only. It excludes this tracer's own
    # bookkeeping but still includes one perf_counter pair per access, so on the
    # very cheap ports it reads high.
    secs = Counter()                         # (kind, port) -> seconds
    sites = defaultdict(Counter)             # (kind, port) -> {offset: n}
    values = defaultdict(Counter)            # (kind, port) -> {value: n}
    events = []
    dropped = [0]
    frame = [0]

    real_in = emulation.VgaDos._on_in
    real_out = emulation.VgaDos._on_out

    def where(self):
        return self._reg(UC_X86_REG_CS) * 16 + self._reg(UC_X86_REG_IP)

    def traced_in(self, uc, port, size, user):
        t = time.perf_counter()
        v = real_in(self, uc, port, size, user)
        secs[("in", port)] += time.perf_counter() - t
        lin = where(self)
        counts[("in", port)] += 1
        sites[("in", port)][lin] += 1
        values[("in", port)][v] += 1
        if len(events) < args.max_events:
            events.append((frame[0], "in", port, size, v, lin))
        else:
            dropped[0] += 1
        return v

    def traced_out(self, uc, port, size, value, user):
        lin = where(self)
        counts[("out", port)] += 1
        sites[("out", port)][lin] += 1
        values[("out", port)][value] += 1
        if len(events) < args.max_events:
            events.append((frame[0], "out", port, size, value, lin))
        else:
            dropped[0] += 1
        t = time.perf_counter()
        r = real_out(self, uc, port, size, value, user)
        secs[("out", port)] += time.perf_counter() - t
        return r

    emulation.VgaDos._on_in = traced_in
    emulation.VgaDos._on_out = traced_out

    pygame.init()
    pygame.font.init()
    m, img = native.build_machine(nargs)
    snapshot.restore_file(m, args.snapshot)

    addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
    running = True
    while running and frame[0] < args.frames:
        addr, running = native.step_frame(m, addr, nargs, img)
        frame[0] += 1

    # ------------------------------------------------------------- report
    base = m.image_base

    def name(lin):
        """Attribute an access to a routine, or say it is outside the image."""
        off = lin - base
        if off < 0 or off >= len(img):
            return f"{lin:#07x} (outside the image)"
        start = native.find_function_start(img, off)
        if start is None:
            return f"{off:#07x} (no prologue found)"
        return f"{off:#07x} in {start:#07x}"

    print(f"\n=== port I/O over {frame[0]} frame(s) from "
          f"{os.path.basename(args.snapshot)} ===")
    total = sum(counts.values())
    print(f"  {total} access(es) ({total / max(frame[0], 1):.0f} per frame), "
          f"{len(counts)} port/direction pair(s), "
          f"{sum(secs.values()):.2f}s in the handlers"
          + (f", {dropped[0]} raw event(s) dropped past --max-events"
             if dropped[0] else ""))

    for (kind, port), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        d = describe(port)
        print(f"\n  {kind.upper():3} {port:#05x}  {n:>9}  "
              f"{secs[(kind, port)]:6.2f}s  "
              f"{1e6 * secs[(kind, port)] / n:5.1f} us/access"
              + (f" - {d}" if d else ""))
        vs = values[(kind, port)]
        shown = ", ".join(f"{v:#04x}x{c}" for v, c in vs.most_common(8))
        print(f"      values: {shown}"
              + (f", and {len(vs) - 8} more" if len(vs) > 8 else ""))
        for lin, c in sites[(kind, port)].most_common(args.sites):
            print(f"      {c:>9} from {name(lin)}")
        if len(sites[(kind, port)]) > args.sites:
            print(f"      ... and {len(sites[(kind, port)]) - args.sites} "
                  f"more site(s)")

    if args.out:
        with open(args.out, "w") as f:
            f.write("frame,dir,port,size,value,linear\n")
            for fr, kind, port, size, value, lin in events:
                f.write(f"{fr},{kind},{port:#05x},{size},{value:#04x},"
                        f"{lin:#07x}\n")
        print(f"\n  wrote {len(events)} raw access(es) to {args.out}"
              + (f" ({dropped[0]} dropped)" if dropped[0] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
