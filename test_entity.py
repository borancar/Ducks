#!/usr/bin/env python3
"""entity_update in game.c against the guest's own 0x07bb2, on a real level.

Everything else in the reconstruction is checked one of three ways: transcribed
from a byte-compared native, diffed field-by-field against a snapshot, or compared
pixel-for-pixel with a capture. The physics had none of those - it was read once
and eyeballed - and this is the check it was missing.

Both sides are given the same level, so the terrain they probe is the same by
construction: compare_level.py already proves the backdrop agrees byte for byte.
Then, entity by entity, the guest's body runs from a hand-built far-call frame with
the natives unhooked, the port's C runs on the same input, and every field of the
entity is compared.

    make -C reconstruct lib && venv/bin/python test_entity.py snapshots/snap004.snap

Nothing is asserted about what the right answer is - only that the two agree, which
is all that can be checked without a second reading that might be wrong the same
way.
"""
import ctypes
import os
import struct
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("DUCKS_GAME_DIR", os.path.abspath("game"))

ENTITY = 0x29
OFF = 0x07BB2
ARGS_SEG = 0x3800

# The fields the original names, in order, so a difference can be reported as the
# field rather than as a byte offset.
FIELDS = [("x", 0, 4), ("y", 4, 4), ("prev_x", 0x0C, 4), ("prev_y", 0x10, 4),
          ("f14", 0x14, 1), ("f15", 0x15, 1), ("f16", 0x16, 1),
          ("param", 0x17, 2), ("f19", 0x19, 1), ("f1a", 0x1A, 1),
          ("frame", 0x1F, 2), ("f21", 0x21, 2), ("f23", 0x23, 2),
          ("type", 0x25, 2), ("f27", 0x27, 2)]


def field_bytes(raw):
    return {name: raw[at:at + n] for name, at, n in FIELDS}


def main():
    snap = sys.argv[1] if len(sys.argv) > 1 else "snapshots/snap004.snap"

    import pygame

    import native
    import snapshot
    import test_gameplay
    import dump_level as D

    pygame.init()
    m, _ = native.build_machine(native.make_parser().parse_args(["--flip-hz", "0"]))
    man, blobs = snapshot.load(snap)
    snapshot.restore(m, man, blobs, verbose=False)
    g = m.dgroup_base
    level = struct.unpack("<h", m.read(g + 0x2032, 2))[0]
    print(f"{os.path.basename(snap)} is level {level}")

    # the port, on the same level
    lib = ctypes.CDLL(D.LIB)
    lib.init()
    lib.set_mode_x(ctypes.c_int16.in_dll(lib, "video_mode").value)
    ctypes.c_int16.in_dll(lib, "level_attempted").value = level
    ctypes.c_int16.in_dll(lib, "episode_egg_index").value = \
        struct.unpack("<h", m.read(g + 0x0094, 2))[0]
    lib.level_load()

    # the scalars entity_update reads that a level does not settle by itself
    for off, name, kind in ((0x18D3, "mouse_x", ctypes.c_int32),
                            (0x18D7, "mouse_y", ctypes.c_int32),
                            (0x18DF, "button_a_down", ctypes.c_int16),
                            (0x2100, "g_2100", ctypes.c_int16),
                            (0x0DAB, "g_dab", ctypes.c_int16),
                            (0x2016, "g_2016", ctypes.c_int16)):
        n = 4 if kind is ctypes.c_int32 else 2
        kind.in_dll(lib, name).value = int.from_bytes(m.read(g + off, n),
                                                     "little", signed=True)

    scenes = (D.Scene * 6).in_dll(lib, "scenes")
    checked = differ = moved = 0
    for si in (0, 1, 2):
        base_off, base_seg = struct.unpack("<HH", m.read(g + 0x0D63 + si * 12 + 8, 4))
        base = base_seg * 16 + base_off
        count = struct.unpack("<h", m.read(g + 0x0D63 + si * 12 + 2, 2))[0]
        port = scenes[si]
        if count != port.count:
            print(f"  scene {si}: guest has {count} entities, port has {port.count}"
                  f" - not comparable, skipped")
            continue

        for i in range(count):
            at = base + i * ENTITY
            # As found, then lifted clear of the ground so the fall is a long one:
            # on a level whose ducks are already resting, the step loop never
            # reaches its -5 limit and a comparison cannot see that limit at all.
            # Sabotaging the limit went unnoticed until these were added.
            for lift in (0, 8, 20, 40):
              raw = bytearray(m.read(at, ENTITY))
              if lift:
                  y = int.from_bytes(raw[4:8], "little", signed=True) - lift
                  if y < 2:
                      continue
                  raw[4:8] = y.to_bytes(4, "little", signed=True)
                  m.uc.mem_write(at, bytes(raw))
              before = bytes(m.read(at, ENTITY))

              # the port: the same entity in, its own call, the result out
              ctypes.memmove(ctypes.addressof(port.entities[i]), before, 0)  # layout differs
              for name, off, n in FIELDS:
                  v = int.from_bytes(before[off:off + n], "little",
                                     signed=name not in ("f15", "f16", "f19", "f1a"))
                  setattr(port.entities[i], name, v)
              lib.entity_update(ctypes.byref(port.entities[i]),
                                ctypes.c_int16(0), ctypes.c_int16(0))
              ours = {name: getattr(port.entities[i], name) for name, _, _ in FIELDS}

              # the guest: its own body, natives out, from a made-up frame
              frame = struct.pack("<HHhh", at & 0xF, at >> 4, 0, 0)
              test_gameplay.guest_call(m, OFF, frame)
              theirs_raw = bytes(m.read(at, ENTITY))
              m.uc.mem_write(at, before)                    # put it back

              for name, off, n in FIELDS:
                  want = ours[name]
                  got = int.from_bytes(theirs_raw[off:off + n], "little",
                                       signed=name not in ("f15", "f16", "f19", "f1a"))
                  checked += 1
                  was = int.from_bytes(before[off:off + n], "little",
                                       signed=name not in ("f15", "f16", "f19", "f1a"))
                  if got != was:
                      moved += 1
                  if (want & ((1 << (8 * n)) - 1)) != (got & ((1 << (8 * n)) - 1)):
                      differ += 1
                      if differ <= 12:
                          print(f"  scene {si} entity {i} type {ours['type']:#04x}"
                                f" field {name}: port {want} guest {got}")

    # ---------------------------------------------------------------- events
    #
    # level_event is the click handler, and what a demo's table fires. Its effects
    # are all over DGROUP - a scene gains an entity, a type changes, the score
    # moves - so the comparison watches the three scenes it touches and the
    # scalars, rather than one entity.
    WATCH = [(0x0D63, 0x48), (0x2036, 2), (0x2007, 2), (0x2005, 2),
             (0x1FF2, 4), (0x1FDA, 2), (0x217D, 2)]
    ev_checked = ev_differ = ev_changed = 0
    tools = ctypes.POINTER(ctypes.c_int16).in_dll(lib, "tool_list")
    n_tools = ctypes.c_uint8.in_dll(lib, "tool_count").value
    for t in range(max(1, n_tools)):
        tool = tools[t] if n_tools else 0
        for (px, py) in ((40, 60), (80, 100), (160, 120), (200, 40)):
            for at, n in WATCH:
                pass
            m.uc.mem_write(g + 0x1786, struct.pack("<h", tool))
            ctypes.c_int16.in_dll(lib, "tool_type").value = tool
            # The port has a freshly loaded level and the guest a played one, so
            # anything level_event only *adds to* has to start equal - comparing a
            # score of 0 against one with 2368 banked says nothing about the code.
            for off, nm in ((0x2036, "score"), (0x2007, "duck_count"),
                            (0x2005, "eaten_countdown"), (0x1FDA, "g_1fda"),
                            (0x217D, "g_217d"), (0x18F3, "picked_index")):
                ctypes.c_int16.in_dll(lib, nm).value = \
                    struct.unpack("<h", m.read(g + off, 2))[0]
            # both sides start from the guest's current DGROUP for what is watched
            before = [bytes(m.read(g + a, n)) for a, n in WATCH]
            for (a, n), b in zip(WATCH, before):
                if a == 0x0D63:
                    for si in range(6):
                        cap, cnt, flag, k6 = struct.unpack("<4h", b[si*12:si*12+8])
                        scenes[si].capacity, scenes[si].count = cap, cnt
                        scenes[si].flag, scenes[si].keep_order = flag, k6
            lib.level_event(ctypes.c_int16(px), ctypes.c_int16(py))
            ours = [(scenes[si].count, scenes[si].flag) for si in range(6)]
            ours_score = ctypes.c_int16.in_dll(lib, "score").value

            test_gameplay.guest_call(m, 0x0D0C8, struct.pack("<hh", px, py))
            after = [bytes(m.read(g + a, n)) for a, n in WATCH]
            theirs = [struct.unpack("<4h", after[0][si*12:si*12+8]) for si in range(6)]
            theirs_score = struct.unpack("<h", after[1])[0]
            for (a, n), b in zip(WATCH, before):
                m.uc.mem_write(g + a, b)

            for si in range(6):
                ev_checked += 2
                if theirs[si][:2] != (0,)*0 and (ours[si][0] != theirs[si][1]
                                                 or ours[si][1] != theirs[si][2]):
                    ev_differ += 1
                    if ev_differ <= 8:
                        print(f"  tool {tool:#04x} at ({px},{py}) scene {si}: "
                              f"port count/flag {ours[si]} "
                              f"guest {(theirs[si][1], theirs[si][2])}")
                if (theirs[si][1], theirs[si][2]) != tuple(struct.unpack("<4h", before[0][si*12:si*12+8])[1:3]):
                    ev_changed += 1
            ev_checked += 1
            if ours_score != theirs_score:
                ev_differ += 1
                if ev_differ <= 8:
                    print(f"  tool {tool:#04x} at ({px},{py}) score: "
                          f"port {ours_score} guest {theirs_score}")

    print(f"level_event: {ev_checked} checks over {max(1, n_tools) * 4} cases, "
          f"{ev_differ} differ, {ev_changed} changed something")

    print(f"\n{checked} fields compared, {differ} differ, "
          f"{moved} of them changed by the call - a comparison over "
          f"fields nothing touches proves nothing")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
