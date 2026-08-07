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

    # The particle array is run_level's, so in a harness that only calls
    # level_load the port has nowhere to put particles and quietly draws no
    # random numbers for them - while the guest, captured mid-level, has an array
    # and draws four per particle. That is not a difference in the code; it is
    # the harness missing a buffer, and the seed comparison below is what made it
    # visible. Given the guest's own capacity, both sides draw the same.
    p_cap = struct.unpack("<h", m.read(g + 0x18CF, 2))[0]
    particles = (ctypes.c_uint8 * max(1, p_cap * 16))()
    ctypes.c_void_p.in_dll(lib, "particle_array").value = ctypes.addressof(particles)
    ctypes.c_int16.in_dll(lib, "particle_cap").value = p_cap
    ctypes.c_int16.in_dll(lib, "particle_count").value = \
        struct.unpack("<h", m.read(g + 0x18CD, 2))[0]

    def put_entity(dst, raw):
        for nm, off, n in FIELDS:
            setattr(dst, nm, int.from_bytes(
                raw[off:off + n], "little",
                signed=nm not in ("f15", "f16", "f19", "f1a")))

    # A type 2 entity reads its leader through the far pointer at +0x1b, and the
    # port's copy of one has no leader on this side: mirroring the guest's fields
    # into a port entity leaves that pointer at whatever malloc left there. It
    # went unnoticed for as long as every mirrored follower had f19 == 0, because
    # a follower that does not walk never reaches the dereference - and then
    # level1-bonus, whose ducks are chained, dumped core.
    #
    # One scratch entity is enough: entity_update reads the leader and never
    # writes it, so the leader's fields can be mirrored one call at a time.
    scratch = D.Entity()
    backdrop = D.Desc.in_dll(lib, "backdrop")

    def live(raw):
        """Does this look like an entity of a level that is actually running?

        A snapshot taken between levels keeps its scene pointers and what they
        point at is freed DOS memory: level1-bonus reads back 0x81818181 for
        every field of every entity. The guest merely reads rubbish from that,
        because indexing off the end of a table in real mode lands somewhere; the
        port segfaults. So the state is checked for being a live level rather
        than compared, and the skip is printed - a comparison that quietly does
        nothing is worse than one that says it did nothing.
        """
        x, y = struct.unpack("<ii", raw[0:8])
        t = struct.unpack("<h", raw[0x25:0x27])[0]
        return 0 <= t < 111 and 0 <= x < backdrop.w and 0 <= y < backdrop.h

    # Both sides draw from the same LCG - the guest's seed is a long at
    # d+0x3006, the port's is rand_seed - and a type 4 duck draws one number a
    # frame. Without syncing them the comparison is a coin toss for every loose
    # duck on the level, and it looked like agreement only because the draws
    # happened to line up: click-castle differed on twelve fields one run and
    # none the next. Comparing the seed afterwards checks the *count* of draws
    # as well, which is how a missing or extra rand() shows up.
    # And the guest's seed itself is not part of the snapshot: the machine boots
    # first, and the boot srand()s from the clock, so two runs of this harness
    # would compare two different games. Fixed here so a run is reproducible.
    m.uc.mem_write(g + 0x3006, struct.pack("<I", 0x1234ABCD))

    def seed_sync():
        ctypes.c_uint32.in_dll(lib, "rand_seed").value = \
            struct.unpack("<I", m.read(g + 0x3006, 4))[0]

    def seed_same():
        return (ctypes.c_uint32.in_dll(lib, "rand_seed").value
                == struct.unpack("<I", m.read(g + 0x3006, 4))[0])

    def put_lead(dst, raw):
        off, seg = struct.unpack("<HH", raw[0x1B:0x1F])
        try:
            put_entity(scratch, bytes(m.read(seg * 16 + off, ENTITY)))
        except Exception:                       # not a mapped address
            dst.lead = None
            return
        dst.lead = ctypes.addressof(scratch)

    def scene_live(si):
        off, seg = struct.unpack("<HH", m.read(g + 0x0D63 + si * 12 + 8, 4))
        cnt = struct.unpack("<h", m.read(g + 0x0D63 + si * 12 + 2, 2))[0]
        return all(live(bytes(m.read(seg * 16 + off + i * ENTITY, ENTITY)))
                   for i in range(cnt))

    state_live = all(scene_live(si) for si in (0, 1, 2))

    # Everything below probes the terrain, so the two sides have to be standing
    # on the same ground for any of it to mean anything. compare_level.py checks
    # this properly; what is needed here is a warning, because a level whose
    # backdrops disagree produces entity differences that are the loader's, not
    # entity_update's - and on click-castle it produces different ones each run,
    # because the rows the port's loader leaves unwritten hold whatever the heap
    # had. Chasing that as a walking bug is a wasted afternoon.
    bd_at = struct.unpack("<HH", m.read(g + 0x16F5, 4))
    bd_at = bd_at[1] * 16 + bd_at[0]
    bad_rows = 0
    for y in range(min(backdrop.h, struct.unpack("<h", m.read(g + 0x1703, 2))[0])):
        off, seg = struct.unpack("<HH", m.read(bd_at + y * 4, 4))
        theirs_row = bytes(m.read(seg * 16 + off, backdrop.w))
        if theirs_row != bytes(backdrop.rows[y][:backdrop.w]):
            bad_rows += 1
    if bad_rows:
        print(f"  the two backdrops differ in {bad_rows} of {backdrop.h} rows -"
              f" differences below may be the loader's, not the entity's")

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
        if not scene_live(si):
            print(f"  scene {si}: the guest's entities are not a running level"
                  f" - skipped")
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
              put_entity(port.entities[i], before)   # the layouts differ, so by field
              put_lead(port.entities[i], before)
              seed_sync()
              lib.entity_update(ctypes.byref(port.entities[i]),
                                ctypes.c_int16(0), ctypes.c_int16(0))
              ours = {name: getattr(port.entities[i], name) for name, _, _ in FIELDS}

              # the guest: its own body, natives out, from a made-up frame
              frame = struct.pack("<HHhh", at & 0xF, at >> 4, 0, 0)
              test_gameplay.guest_call(m, OFF, frame)
              theirs_raw = bytes(m.read(at, ENTITY))
              m.uc.mem_write(at, before)                    # put it back

              checked += 1
              if not seed_same():
                  differ += 1
                  if differ <= 12:
                      print(f"  scene {si} entity {i} drew a different number of"
                            f" random values than the guest")
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
    # Same guard as above, for the same reason: on a between-levels snapshot the
    # scenes hold freed memory, and level_event walking it reported three
    # differences on level2-bonus that were the state's, not the port's.
    if not state_live:
        print("  the guest is not in a level - level_event skipped")
    for t in range(max(1, n_tools) if state_live else 0):
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

    # ----------------------------------------------------- the chain and the walk
    #
    # Two things the comparison above cannot reach, both of them what the walk
    # button does.
    #
    # flock_link (0x0970c) works on a whole scene rather than on one entity, so
    # the guest's scene 0 has to be mirrored into the port wholesale - counts,
    # flags and the +0x1b lead pointers, translated to this side's addresses -
    # before either can be called. And once it has run, both sides have a flock
    # of type 2 followers with matching leads, which is the state the per-entity
    # sweep never starts from.
    #
    # Then the walk itself: every snapshot was taken with the button up, so the
    # hero's `active = button_a_down` branch was never once executed above. Here
    # the input is set on both sides deliberately, including positions either
    # side of the flock so the facing comes out both ways.
    def guest_scene(si):
        cap, cnt, flag, keep = struct.unpack("<4h", m.read(g + 0x0D63 + si * 12, 8))
        off, seg = struct.unpack("<HH", m.read(g + 0x0D63 + si * 12 + 8, 4))
        return cap, cnt, flag, keep, seg * 16 + off

    def guest_lead(raw, bases):
        """The guest's +0x1b far pointer as (scene, index), or None."""
        off, seg = struct.unpack("<HH", raw[0x1B:0x1F])
        lin = seg * 16 + off
        for si, (base, cnt) in bases.items():
            if base <= lin < base + cnt * ENTITY and (lin - base) % ENTITY == 0:
                return si, (lin - base) // ENTITY
        return None

    def port_lead(e, bases):
        p = e.lead
        if not p:
            return None
        for si in bases:
            b = ctypes.addressof(scenes[si].entities[0])
            n = scenes[si].count
            if b <= p < b + n * ctypes.sizeof(D.Entity) \
                    and (p - b) % ctypes.sizeof(D.Entity) == 0:
                return si, (p - b) // ctypes.sizeof(D.Entity)
        return "elsewhere"

    bases, mirrored = {}, True
    for si in (0, 5):
        cap, cnt, flag, keep, base = guest_scene(si)
        if cnt > scenes[si].capacity:
            print(f"  scene {si}: {cnt} entities will not fit the port's "
                  f"{scenes[si].capacity} - chain and walk skipped")
            mirrored = False
            break
        if any(not live(bytes(m.read(base + i * ENTITY, ENTITY)))
               for i in range(cnt)):
            print(f"  scene {si}: not a running level - chain and walk skipped")
            mirrored = False
            break
        scenes[si].count, scenes[si].flag, scenes[si].keep_order = cnt, flag, keep
        bases[si] = (base, cnt)

    ch_checked = ch_differ = ch_promoted = walk_checked = walk_differ = 0
    if mirrored:
        saved = {si: bytes(m.read(base, cnt * ENTITY))
                 for si, (base, cnt) in bases.items()}

        def mirror():
            for si, (base, cnt) in bases.items():
                for i in range(cnt):
                    put_entity(scenes[si].entities[i],
                               bytes(m.read(base + i * ENTITY, ENTITY)))
            for si, (base, cnt) in bases.items():    # leads, once all exist
                for i in range(cnt):
                    t = guest_lead(bytes(m.read(base + i * ENTITY, ENTITY)), bases)
                    scenes[si].entities[i].lead = None if t is None else \
                        ctypes.addressof(scenes[t[0]].entities[t[1]])

        # A hero with no facing chains nobody - flock_chain returns at its second
        # line - so a snapshot taken with the button up would compare an empty
        # line against an empty line and call it agreement. The facing is forced
        # here for that reason, and ch_promoted is printed so a run that chained
        # nothing cannot be read as a run that agreed.
        hero = struct.unpack("<h", m.read(g + 0x0D67, 2))[0]
        for facing in (0, 1, -1):
            for si, (base, _) in bases.items():
                m.uc.mem_write(base, saved[si])
            if facing and hero != 0xFF:
                m.uc.mem_write(bases[0][0] + hero * ENTITY + 0x14,
                               facing.to_bytes(1, "little", signed=True))
            mirror()

            seed_sync()
            lib.flock_link()
            test_gameplay.guest_call(m, 0x0970C, b"")

            for si, (base, cnt) in bases.items():
                for i in range(cnt):
                    raw = bytes(m.read(base + i * ENTITY, ENTITY))
                    for nm in ("f16", "f19", "f1a", "type", "frame"):
                        off, n = next((o, k) for x, o, k in FIELDS if x == nm)
                        got = int.from_bytes(raw[off:off + n], "little",
                                             signed=nm not in ("f15", "f16", "f19", "f1a"))
                        want = getattr(scenes[si].entities[i], nm) \
                            & ((1 << (8 * n)) - 1)
                        ch_checked += 1
                        if want != (got & ((1 << (8 * n)) - 1)):
                            ch_differ += 1
                            if ch_differ <= 8:
                                print(f"  chain: facing {facing} scene {si} "
                                      f"entity {i} {nm}: port {want} guest {got}")
                    ch_checked += 1
                    if port_lead(scenes[si].entities[i], bases) != guest_lead(raw, bases):
                        ch_differ += 1
                        if ch_differ <= 8:
                            print(f"  chain: facing {facing} scene {si} entity {i}"
                                  f" lead: port "
                                  f"{port_lead(scenes[si].entities[i], bases)} "
                                  f"guest {guest_lead(raw, bases)}")
                    if int.from_bytes(raw[0x19:0x1A], "little"):
                        ch_promoted += 1

        # the walk, from that state
        base, cnt = bases[0]
        for (down, mx, my) in ((0, 40, 40), (1, 0, 0), (1, 0x140, 0x60),
                               (1, 0x20, 0x10), (1, 0x80, 0xa0)):
            for off, nm, kind in ((0x18D3, "mouse_x", ctypes.c_int32),
                                  (0x18D7, "mouse_y", ctypes.c_int32),
                                  (0x18DF, "button_a_down", ctypes.c_int16)):
                v = {"mouse_x": mx, "mouse_y": my, "button_a_down": down}[nm]
                kind.in_dll(lib, nm).value = v
                m.uc.mem_write(g + off, v.to_bytes(4 if kind is ctypes.c_int32
                                                   else 2, "little", signed=True))
            for i in range(cnt):
                at = base + i * ENTITY
                before = bytes(m.read(at, ENTITY))
                put_entity(scenes[0].entities[i], before)
                seed_sync()
                lib.entity_update(ctypes.byref(scenes[0].entities[i]),
                                  ctypes.c_int16(0), ctypes.c_int16(0))
                ours = {nm: getattr(scenes[0].entities[i], nm)
                        for nm, _, _ in FIELDS}
                test_gameplay.guest_call(m, OFF,
                                         struct.pack("<HHhh", at & 0xF, at >> 4, 0, 0))
                after = bytes(m.read(at, ENTITY))
                walk_checked += 1
                if not seed_same():
                    walk_differ += 1
                m.uc.mem_write(at, before)
                put_entity(scenes[0].entities[i], before)
                for nm, off, n in FIELDS:
                    got = int.from_bytes(after[off:off + n], "little",
                                         signed=nm not in ("f15", "f16", "f19", "f1a"))
                    walk_checked += 1
                    if (ours[nm] & ((1 << (8 * n)) - 1)) != (got & ((1 << (8 * n)) - 1)):
                        walk_differ += 1
                        if walk_differ <= 8:
                            print(f"  walk: button {down} at ({mx},{my}) entity {i}"
                                  f" type {ours['type']:#04x} {nm}: "
                                  f"port {ours[nm]} guest {got}")

        for si, (base, cnt) in bases.items():        # the guest, as it was
            m.uc.mem_write(base, saved[si])

    # ------------------------------------------------------------- collisions
    #
    # 0x0993b, every duck of scene 0 against every object of scene 2, and where
    # most of the game's rules live. The Python twin of it in native.py is
    # verified against the guest; the C was transcribed from that and had never
    # itself been compared, because doing it needs two scenes and a handful of
    # scalars marshalled - which the mirroring above now has.
    #
    # It is worth having now rather than later: the frame calls it, so a duck
    # walking into anything at all goes through this code.
    SCALARS = [(0x2036, "score"), (0x2007, "duck_count"), (0x2013, "quota_left"),
               (0x1FF6, "combo_hi"), (0x1FF8, "combo_lo"), (0x1FF2, "g_1ff2"),
               (0x1FF4, "g_1ff4"), (0x2005, "eaten_countdown"), (0x0509, "g_509")]
    co_checked = co_differ = co_changed = 0
    if mirrored and scene_live(2) and scenes[2].count == \
            struct.unpack("<h", m.read(g + 0x0D63 + 2 * 12 + 2, 2))[0]:
        _, cnt2, _, _, base2 = guest_scene(2)
        all_bases = dict(bases)
        all_bases[2] = (base2, cnt2)
        saved = {si: bytes(m.read(b, c * ENTITY))
                 for si, (b, c) in all_bases.items()}
        saved_scalars = {off: bytes(m.read(g + off, 2)) for off, _ in SCALARS}

        def collide_case(place):
            """One comparison, with the first duck put where `place` says."""
            nonlocal co_checked, co_differ, co_changed

            for si, (b, _) in all_bases.items():     # both sides, as found
                m.uc.mem_write(b, saved[si])
            for off, _ in SCALARS:
                m.uc.mem_write(g + off, saved_scalars[off])
            if place is not None:
                ox, oy = struct.unpack("<ii", m.read(base2 + place * ENTITY, 8))
                m.uc.mem_write(bases[0][0], struct.pack("<ii", ox, oy))

            for si, (b, c) in all_bases.items():
                _, cnt, flag, keep, _ = guest_scene(si)
                scenes[si].count = cnt
                scenes[si].flag = flag
                scenes[si].keep_order = keep
                for i in range(c):
                    put_entity(scenes[si].entities[i],
                               bytes(m.read(b + i * ENTITY, ENTITY)))
            for si, (b, c) in all_bases.items():     # leads, once all exist
                for i in range(c):
                    t = guest_lead(bytes(m.read(b + i * ENTITY, ENTITY)), all_bases)
                    scenes[si].entities[i].lead = None if t is None else \
                        ctypes.addressof(scenes[t[0]].entities[t[1]])
            for off, nm in SCALARS:
                ctypes.c_int16.in_dll(lib, nm).value = \
                    struct.unpack("<h", m.read(g + off, 2))[0]

            seed_sync()
            lib.collide_scenes()
            mine = {nm: ctypes.c_int16.in_dll(lib, nm).value for _, nm in SCALARS}
            my_scenes = {si: (scenes[si].count, scenes[si].flag) for si in all_bases}
            my_types = {si: [scenes[si].entities[i].type for i in range(c)]
                        for si, (_, c) in all_bases.items()}

            test_gameplay.guest_call(m, 0x0993B, b"")

            where = "as found" if place is None else f"duck 0 on object {place}"
            co_checked += 1
            if not seed_same():
                co_differ += 1
                if co_differ <= 8:
                    print(f"  collide ({where}): a different number of random"
                          f" values was drawn")
            for off, nm in SCALARS:
                got = struct.unpack("<h", m.read(g + off, 2))[0]
                co_checked += 1
                if got != struct.unpack("<h", saved_scalars[off])[0]:
                    co_changed += 1
                if mine[nm] != got:
                    co_differ += 1
                    if co_differ <= 8:
                        print(f"  collide ({where}): {nm}: "
                              f"port {mine[nm]} guest {got}")
            for si in all_bases:
                _, cnt, flag, _, b = guest_scene(si)
                co_checked += 1
                if my_scenes[si] != (cnt, flag):
                    co_differ += 1
                    if co_differ <= 8:
                        print(f"  collide ({where}): scene {si} count/flag: "
                              f"port {my_scenes[si]} guest {(cnt, flag)}")
                for i in range(min(cnt, len(my_types[si]))):
                    got = struct.unpack("<h", m.read(b + i * ENTITY + 0x25, 2))[0]
                    co_checked += 1
                    if my_types[si][i] != got:
                        co_differ += 1
                        if co_differ <= 8:
                            print(f"  collide ({where}): scene {si} entity {i}"
                                  f" type: port {my_types[si][i]} guest {got}")

        # A level's ducks are not standing on its objects, so this runs as found
        # and then once per object with the first duck moved onto it. Without
        # that, both sides agree about a frame in which nothing happened, which
        # says only that neither of them did anything - and the switch, which is
        # where the game's rules are, goes unread. The count of scalars that
        # moved is printed so a run that collided with nothing cannot be read as
        # a run that agreed.
        for case in [None] + (list(range(min(cnt2, 8))) if bases[0][1] else []):
            collide_case(case)

        for si, (b, _) in all_bases.items():         # the guest, as it was
            m.uc.mem_write(b, saved[si])
        for off, _ in SCALARS:
            m.uc.mem_write(g + off, saved_scalars[off])
    else:
        print("  scene 2 does not match or is not live - collisions skipped")

    print(f"collide_scenes: {co_checked} compared, {co_differ} differ, "
          f"{co_changed} scalars moved")
    differ += co_differ

    print(f"flock_link: {ch_checked} fields compared, {ch_differ} differ, "
          f"{ch_promoted} entities came out ranked - a chain of none proves nothing")
    print(f"the walk: {walk_checked} fields compared, {walk_differ} differ")
    differ += ch_differ + walk_differ

    print(f"\n{checked} fields compared, {differ} differ, "
          f"{moved} of them changed by the call - a comparison over "
          f"fields nothing touches proves nothing")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
