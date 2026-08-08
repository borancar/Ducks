#!/usr/bin/env python3
"""No two of the port's globals may claim the same DGROUP bytes.

Six bugs in one day came from one mistake: a field of a record declared a second
time as a scalar. `bg_w` was `background.w`; `view_w` was `viewport_game.width`;
`init_objects` was `message_image`. Each was silent until something read the copy
nobody wrote - a divide by zero, a null descriptor, a level that would not start.

So this reads the offsets out of `reconstruct/`'s own declaration comments, gives
each declaration the size it has **in the guest** rather than on this machine, and
fails if one lands inside another. It is the same idea as test_fn_start.py: pin a
class of mistake to a test rather than to whoever remembers it.

    venv/bin/python test_dgroup.py

A declaration opts out with `/* 0xNNNN alias */`, which is for the cases where two
names over one span are deliberate.
"""
import re
import sys

SOURCES = ["reconstruct/game.h",
           "reconstruct/game.c", "reconstruct/dos_io.c", "reconstruct/sdl_io.c",
           "reconstruct/sound.c", "reconstruct/egg.c"]

# What a type occupies in the original, which is the only size that matters here:
# a far pointer is four bytes there and eight here, and the structures are packed.
SIZES = {
    "int8_t": 1, "uint8_t": 1, "char": 1,
    "int16_t": 2, "uint16_t": 2, "int": 2, "short": 2,
    "int32_t": 4, "uint32_t": 4, "long": 4, "float": 4,
    "viewport_t": 0x14, "desc_t": 0x10, "scene_t": 0x0c, "entity_t": 0x29,
    "table_t": 6, "sprite_t": 14, "glyph_t": 8, "item_t": 0x10,
    "menu_t": 0x73, "score_t": 8, "episode_t": 14, "particle_t": 16,
    "solid_t": 0x20, "voice_t": 12, "counts_t": 6, "egg_file_t": 0x17,
}
POINTER = 4                                   # any `*` in the original

DECL = re.compile(
    r"^(?!static|typedef)"                    # definitions AND extern declarations
    r"(?:extern\s+)?"                          # - see the note below on why both
    r"(?P<type>[A-Za-z_][A-Za-z0-9_]*)\s+"
    r"(?P<stars>\**)\s*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\[(?P<count>[0-9a-fA-Fx]+)\])?\s*;"
    r".*?/\*\s*(?P<off>0x[0-9a-fA-F]{2,4})(?![0-9a-fA-F:])(?P<tail>[^*]*)")

# `0x1894:0000` is a segment, not a DGROUP offset - menu_text and its neighbours
# are far pointers whose *target* lives there - so an offset followed by a colon is
# not one of these at all. That is what the negative lookahead above is for.
#
# **`extern` counts here, and did not until 2026-08-08.** A declaration claims the
# same DGROUP bytes as the definition it names, so for the overlap check it is
# evidence like any other - and skipping it left a blind spot with a real bug in
# it: `extern int16_t cheat_flag; /* 0x0515 */` sat in game.h claiming
# cheat_state[8]'s two bytes, defined nowhere, used nowhere, one of the shadow
# variables the cheat array replaced. Invisible to this test purely because of the
# word it starts with.
#
# Two declarations of the SAME name are one object, however many files declare it,
# and that is not an overlap. Different names over the same bytes is the bug.


def size_of(type_name, stars, count):
    unit = POINTER if stars else SIZES.get(type_name)
    if unit is None:
        return None
    n = int(count, 0) if count else 1
    return unit * n


# a name like g_203b or buf_203b carries its own offset, and those turn up on
# lines declaring several at once - which the pattern above skips. `buf_203b` was
# a second name for tool_event_table for exactly that reason.
NAMED = re.compile(r"\b(?:g|buf|f)_([0-9a-f]{3,4})\b")


# A whole definition line and nothing else: a type, some names, a semicolon. Only
# these are searched for name-carried offsets, so a *use* of level_completed in an
# expression is not mistaken for a declaration of it.
MULTI = re.compile(r"^(?P<type>[A-Za-z_][A-Za-z0-9_]*)\s+(?P<rest>[^;{}()=]*);")


def main():
    decls = []
    for path in SOURCES:
        try:
            text = open(path).read()
        except OSError:
            continue
        for line in text.split("\n"):
            stripped = line.replace("far ", "").strip()
            if stripped.startswith(("static", "typedef", "*", "/")):
                continue
            m = DECL.match(stripped)
            if not m:
                multi = MULTI.match(stripped)
                if multi and multi.group("type") in SIZES:
                    for part in multi.group("rest").split(","):
                        part = part.strip()
                        found = NAMED.search(part)
                        if not found:
                            continue
                        decls.append((int(found.group(1), 16),
                                      POINTER if part.startswith("*")
                                      else SIZES[multi.group("type")],
                                      part.lstrip("*"), path))
                continue
            if "alias" in m.group("tail"):
                continue
            size = size_of(m.group("type"), m.group("stars"), m.group("count"))
            if size is None:
                continue
            decls.append((int(m.group("off"), 16), size, m.group("name"), path))

    decls.sort()
    # The two backends are alternatives - the Makefile links one or the other,
    # never both - so they define the same video state at the same offsets on
    # purpose. That is not an overlap, but it is worth checking rather than just
    # excusing: the same name must mean the same offset and the same size in
    # both, or one of them is describing a different object.
    BACKENDS = {"reconstruct/dos_io.c", "reconstruct/sdl_io.c"}

    bad, agreed = [], 0
    for i, (off, size, name, path) in enumerate(decls):
        for off2, size2, name2, path2 in decls[i + 1:]:
            if off2 >= off + size:
                break
            if name == name2:
                continue                     # one object, declared more than once
            if {path, path2} == BACKENDS:
                if name == name2 and off == off2 and size == size2:
                    agreed += 1          # one object, declared by both backends
                    continue
                bad.append((off, size, name, path, off2, size2, name2, path2))
                continue
            bad.append((off, size, name, path, off2, size2, name2, path2))

    print(f"{len(decls)} declarations carry a DGROUP offset")
    if agreed:
        print(f"{agreed} of them are the video state the two backends both "
              f"define, and they agree on offset and size")
    for off, size, name, path, off2, size2, name2, path2 in bad:
        print(f"  OVERLAP {name} at {off:#06x}+{size:#x} ({path}) covers "
              f"{name2} at {off2:#06x}+{size2:#x} ({path2})"
              f"  - {name2} is {name}+{off2 - off:#x}")
    if bad:
        print(f"\n{len(bad)} overlapping pair(s). Each is one object in the "
              f"original and has to be one object here.")
        return 1
    print("no two declarations claim the same DGROUP bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
