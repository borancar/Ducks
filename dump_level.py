#!/usr/bin/env python3
"""Load a level in the reconstruction and report what it built.

The port's own level_load runs here, driven through ctypes exactly as
test_leaves.py drives its leaves - `make -C reconstruct lib` first. Everything
reported is read back out of the library's globals, so this is what the C
actually produced and not what it was meant to produce.

    venv/bin/python dump_level.py 4

It is half of a comparison. `compare_level.py` is the other half: it reads the
same fields out of a guest that has loaded the same level and diffs the two.
Neither half asserts what the right answer is.
"""
import ctypes
import os
import sys

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "reconstruct/libducks.so")


class Desc(ctypes.Structure):
    _fields_ = [("rows", ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8))),
                ("w", ctypes.c_int16), ("h", ctypes.c_int16)]


class Entity(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32),
                ("unread", ctypes.c_uint8 * 4),
                ("prev_x", ctypes.c_int32), ("prev_y", ctypes.c_int32),
                ("f14", ctypes.c_int8), ("f15", ctypes.c_uint8),
                ("f16", ctypes.c_uint8), ("param", ctypes.c_int16),
                ("f19", ctypes.c_uint8), ("f1a", ctypes.c_uint8),
                ("lead", ctypes.c_void_p), ("frame", ctypes.c_int16),
                ("f21", ctypes.c_int16), ("f23", ctypes.c_int16),
                ("type", ctypes.c_int16), ("f27", ctypes.c_int16)]


class Scene(ctypes.Structure):
    _fields_ = [("capacity", ctypes.c_int16), ("count", ctypes.c_int16),
                ("flag", ctypes.c_int16), ("unread6", ctypes.c_int16),
                ("entities", ctypes.POINTER(Entity))]


class Viewport(ctypes.Structure):
    _fields_ = [("top", ctypes.c_int16), ("bottom", ctypes.c_int16),
                ("left", ctypes.c_int16), ("right", ctypes.c_int16),
                ("width", ctypes.c_int16), ("height", ctypes.c_int16),
                ("scroll_x", ctypes.c_int32), ("scroll_y", ctypes.c_int32)]


class Solid(ctypes.Structure):
    _fields_ = [("img", Desc), ("unread10", ctypes.c_int16 * 3),
                ("x", ctypes.c_int16), ("y", ctypes.c_int16),
                ("right", ctypes.c_int16), ("bottom", ctypes.c_int16),
                ("id", ctypes.c_uint8)]


def load(level, egg=0):
    """Run the port's level_load for one level; return what it produced.

    The startup that has to happen first is the port's own init() - the egg,
    load_animations for type_flags, the sprite set - and then set_mode_x, which
    is where screen_width and screen_height come from and so where the viewport
    the loader builds gets its numbers.
    """
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    os.environ.setdefault("DUCKS_GAME_DIR",
                          os.path.join(os.path.dirname(LIB), "..", "game"))

    lib = ctypes.CDLL(LIB)
    lib.init()
    lib.set_mode_x(ctypes.c_int16.in_dll(lib, "video_mode").value)

    ctypes.c_int16.in_dll(lib, "level_attempted").value = level
    ctypes.c_int16.in_dll(lib, "episode_egg_index").value = egg
    lib.level_load()

    view = Viewport.in_dll(lib, "viewport_game")
    backdrop = Desc.in_dll(lib, "backdrop")
    tools = ctypes.POINTER(ctypes.c_int16).in_dll(lib, "tool_list")
    n_tools = ctypes.c_uint8.in_dll(lib, "tool_count").value
    solids = ctypes.POINTER(Solid).in_dll(lib, "solids")
    n_solids = ctypes.c_uint8.in_dll(lib, "solid_count").value
    i16 = lambda n: ctypes.c_int16.in_dll(lib, n).value
    u8 = lambda n: ctypes.c_uint8.in_dll(lib, n).value

    out = {
        "level_w": i16("level_w"), "level_h": i16("level_h"),
        "duck_count": i16("duck_count"), "sprite_set_id": u8("sprite_set_id"),
        "scenery_count": u8("scenery_count"), "quota_left": i16("quota_left"),
        "timer_period": i16("timer_period"), "next_level": u8("next_level"),
        "bg_drift": u8("bg_drift"), "ambience_on": u8("ambience_on"),
        "pair_slots": i16("pair_slots"),
        "viewport": [view.top, view.bottom, view.left, view.right],
        "flags": list((ctypes.c_int16 * 7).in_dll(lib, "level_flags")),
        "frac": [round(f, 6) for f in (ctypes.c_float * 4).in_dll(lib, "level_frac")],
        "tools": [tools[i] for i in range(n_tools)],
        "backdrop": [backdrop.w, backdrop.h],
        "solids": [[solids[i].id, solids[i].x, solids[i].y,
                    solids[i].img.w, solids[i].img.h,
                    solids[i].right, solids[i].bottom] for i in range(n_solids)],
        "scenes": [], "entities": [],
    }
    scenes = (Scene * 6).in_dll(lib, "scenes")
    for i, s in enumerate(scenes):
        out["scenes"].append([s.capacity, s.count, s.flag, s.unread6])
        out["entities"].append([[int(s.entities[j].x), int(s.entities[j].y),
                                 s.entities[j].type, s.entities[j].param,
                                 s.entities[j].f14]
                                for j in range(s.count)])
    if backdrop.rows:
        out["backdrop_rows"] = [bytes(backdrop.rows[y][x]
                                      for x in range(backdrop.w))
                                for y in range(backdrop.h)]
    return out


def report(state):
    print(f"  size        {state['level_w']} x {state['level_h']}"
          f"   backdrop {state['backdrop'][0]} x {state['backdrop'][1]}")
    print(f"  ducks       {state['duck_count']}"
          f"   sprite set {state['sprite_set_id']:#04x}"
          f"   next {state['next_level']:#04x}")
    print(f"  viewport    {state['viewport']}")
    print(f"  flags       {state['flags']}   fractions {state['frac']}")
    print(f"  tools       {state['tools']}")
    print(f"  scenery     {state['scenery_count']}   quota {state['quota_left']}"
          f"   timer {state['timer_period']}   drift {state['bg_drift']}")
    for i, (cap, cnt, flag, un6) in enumerate(state["scenes"]):
        print(f"  scene {i}: count {cnt:>4}  capacity {cap:>4}  "
              f"flag {flag:#06x}  +6 {un6}")
    print(f"  solids      {len(state['solids'])}")
    for s in state["solids"][:4]:
        print(f"      id {s[0]:#04x} at ({s[1]},{s[2]}) {s[3]}x{s[4]}"
              f" -> ({s[5]},{s[6]})")
    if "backdrop_rows" in state:
        filled = sum(1 for r in state["backdrop_rows"] for b in r if b)
        print(f"  backdrop non-zero {filled} of "
              f"{state['backdrop'][0] * state['backdrop'][1]}")


if __name__ == "__main__":
    level = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    egg = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    print(f"level {level}, egg {egg}")
    report(load(level, egg))
