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

SOURCES = ["reconstruct/game.c", "reconstruct/dos_io.c", "reconstruct/sdl_io.c",
           "reconstruct/sound.c", "reconstruct/egg.c", "reconstruct/stubs.c"]

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
    r"^(?!extern|static|typedef)"             # definitions, not declarations
    r"(?P<type>[A-Za-z_][A-Za-z0-9_]*)\s+"
    r"(?P<stars>\**)\s*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\[(?P<count>[0-9a-fA-Fx]+)\])?\s*;"
    r".*?/\*\s*(?P<off>0x[0-9a-fA-F]{2,4})(?![0-9a-fA-F:])(?P<tail>[^*]*)")

# `0x1894:0000` is a segment, not a DGROUP offset - menu_text and its neighbours
# are far pointers whose *target* lives there - so an offset followed by a colon is
# not one of these at all. That is what the negative lookahead above is for.


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
# these are searched for name-carried offsets, so a *use* of g_21a3 in an
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
            if stripped.startswith(("extern", "static", "typedef", "*", "/")):
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
    bad = []
    for i, (off, size, name, path) in enumerate(decls):
        for off2, size2, name2, path2 in decls[i + 1:]:
            if off2 >= off + size:
                break
            bad.append((off, size, name, path, off2, size2, name2, path2))

    print(f"{len(decls)} declarations carry a DGROUP offset")
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
