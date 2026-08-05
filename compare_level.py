#!/usr/bin/env python3
"""Diff what the port's level loader built against what the guest's built.

`level_load` in game.c was read out of the disassembly rather than transcribed
from a byte-compared native, so it is the first thing in the reconstruction that
no comparison had ever seen. This is that comparison: restore a snapshot taken
during a level, read the loader's output out of the guest's DGROUP, run the
port's loader on the same level, and diff.

    make -C reconstruct lib
    venv/bin/python compare_level.py snapshots/level-start.snap

**Only the fields a level cannot change are compared.** A snapshot is taken
mid-play, so entity positions have moved, ducks have died and counts have
dropped - none of that is the loader's output any more. What survives play is
the level's size, its flags, its tools, what its scenes were sized for, the
solid objects and the backdrop the tiles were painted into. Comparing a count
would report a difference that means nothing, which is worse than not comparing
it at all.

Exits non-zero on a difference, so it can be a test.
"""
import os
import struct
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import dump_level

# What a level cannot change while it is played. `count` is deliberately absent
# from the scene comparison: capacity is what the loader chose, count is what
# play has left. Scene 4 is absent too - it is the cursor, which init allocates
# and the loader never touches, and the port keeps it as its own object.
INVARIANT = ["level_w", "level_h", "sprite_set_id", "scenery_count",
             "timer_period", "next_level", "bg_drift",
             "ambience_on", "viewport", "flags", "frac", "tools", "backdrop"]

# quota_left is deliberately absent. The loader sets it from the scenery layers,
# and then two other things move it: run_level's setup adds one at 0x0dbf5 when
# the level has a hero duck (scene 0's flag is not 0xff), and collide_scenes
# decrements it as ducks get home. Level 1 has a hero and reads 6 against the
# loader's 5; level 4 has none and reads 10 against 10. Comparing it would report
# the setup's increment as a loader difference every time.


def guest(path):
    import pygame

    import native
    import snapshot

    pygame.init()
    nargs = native.make_parser().parse_args(["--flip-hz", "0"])
    man, blobs = snapshot.load(path)
    m, _ = native.build_machine(nargs)
    snapshot.restore(m, man, blobs, verbose=False)

    g = m.dgroup_base
    u8 = lambda a: m.read(g + a, 1)[0]
    s16 = lambda a: struct.unpack("<h", m.read(g + a, 2))[0]
    f32 = lambda a: struct.unpack("<f", m.read(g + a, 4))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(g + a, 4))
        return seg * 16 + off

    n_tools = u8(0x178B)
    tool_base = far(0x1782)
    n_solids = u8(0x2031)
    solid_base = far(0x202D)
    vp = struct.unpack("<4h", m.read(g + 0x172D, 8))

    state = {
        "level": s16(0x2032), "egg": s16(0x0094),
        "level_w": s16(0x1701), "level_h": s16(0x1703),
        "duck_count": s16(0x2007), "sprite_set_id": u8(0x2103),
        "scenery_count": u8(0x2000), "quota_left": s16(0x2013),
        "timer_period": s16(0x2001), "next_level": u8(0x2102),
        "bg_drift": u8(0x202C), "ambience_on": u8(0x2015),
        "pair_slots": s16(0x18D1),
        "viewport": list(vp),
        "flags": [s16(0x201E + i * 2) for i in range(7)],
        "frac": [round(f32(a), 6) for a in (0x13E1, 0x13E5, 0x13E9, 0x13ED)],
        "tools": [struct.unpack("<H", m.read(tool_base + i * 2, 2))[0]
                  for i in range(n_tools)],
        "scenes": [], "solids": [],
    }
    for i in range(6):
        cap, cnt, flag, un6 = struct.unpack("<4h", m.read(g + 0x0D63 + i * 12, 8))
        state["scenes"].append([cap, cnt, flag, un6])
    for i in range(n_solids):
        rec = m.read(solid_base + i * 0x20, 0x20)
        w, h = struct.unpack_from("<hh", rec, 0x0C)
        x, y, right, bottom = struct.unpack_from("<4h", rec, 0x16)
        state["solids"].append([rec[0x1E], x, y, w, h, right, bottom])

    bd = far(0x16F5)
    bw, bh = state["level_w"], state["level_h"]
    state["backdrop"] = [bw, bh]
    rows = []
    for y in range(bh):
        off, seg = struct.unpack("<HH", m.read(bd + y * 4, 4))
        rows.append(bytes(m.read(seg * 16 + off, bw)))
    state["backdrop_rows"] = rows
    return state


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: compare_level.py <snapshot>")

    g = guest(sys.argv[1])
    print(f"guest: {os.path.basename(sys.argv[1])} is level {g['level']}, "
          f"egg {g['egg']}")
    p = dump_level.load(g["level"], g["egg"])

    bad = []
    for k in INVARIANT:
        if g[k] != p[k]:
            bad.append((k, g[k], p[k]))
    for i, (gs, ps) in enumerate(zip(g["scenes"], p["scenes"])):
        if i == 4:
            continue                       # the cursor scene, not the loader's
        if gs[0] != ps[0] or gs[3] != ps[3]:
            bad.append((f"scene {i} capacity/+6", [gs[0], gs[3]], [ps[0], ps[3]]))
    if g["solids"] != p["solids"]:
        bad.append(("solids", g["solids"], p["solids"]))

    grows, prows = g.get("backdrop_rows"), p.get("backdrop_rows")
    if grows and prows and len(grows) == len(prows):
        wrong = [y for y in range(len(grows)) if grows[y] != prows[y]]
        if wrong:
            y = wrong[0]
            x = next(i for i in range(len(grows[y])) if grows[y][i] != prows[y][i])
            bad.append((f"backdrop: {len(wrong)} of {len(grows)} rows differ, "
                        f"first at ({x},{y})", grows[y][x], prows[y][x]))
        else:
            print(f"backdrop: {len(grows)} rows identical, "
                  f"{sum(1 for r in grows for b in r if b)} non-zero pixels")
    elif grows and prows:
        bad.append(("backdrop rows", len(grows), len(prows)))

    for k, gv, pv in bad:
        print(f"  DIFFER {k}:\n    guest {gv}\n    port  {pv}")
    print(f"\n{len(INVARIANT) + 7} fields compared, {len(bad)} differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
