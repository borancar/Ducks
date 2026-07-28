"""Names for image offsets we have identified, so reports say what a thing is.

Every note in this project refers to code by image offset, and every offset that
earns a name here was established by reading the routine or by watching it run -
not by inferring from a call site. Two names were nearly recorded on call-site
evidence alone and both turned out wrong (`0x0b9ea` looked like a main routine
and sets a pointer; `0x0c156` was called from main with no arguments and is a
loader pass), so the rule is: read the body first.

Names ending in `?` are tentative and say so wherever they are printed.

Used by the control socket's `where`, `stack`, `step` and `until`. Keeping it a
plain dict rather than wiring it into find_function_start means an unnamed
function still reports its offset, which is what the notes are indexed by.
"""

# Image offset -> name. Keep sorted by offset; it is read as a map of the binary.
FUNCTIONS = {
    0x0014E: "crt_startup",             # no prologue; calls main
    0x01D8E: "memmove_words",           # word-wise memmove; had the CGA snow waits
    0x0202C: "bios_video_int10",        # every INT 10h site in the image
    0x02108: "read_pit",                # the PIT latch-and-read
    0x0223E: "delay_ms",                # Borland delay(), spins on the PIT
    0x03791: "egg_getc",                # getc on the egg stream: count/ptr/seg
    0x04D4B: "page_flip",               # replaced by native_page_flip
    0x057EE: "set_plane",               # map mask + [0x177d]; replaced natively
    0x0876A: "build_washed_ramp",       # v*0.75+64 into [0x0dad]
    0x0B10B: "palette_fade_step",       # the fade state machine
    0x0B9EA: "set_buffer",              # stores a far pointer into [0x1721]
    0x0C0C2: "egg_load_one?",           # called per egg file with (0, type, i)
    0x0C156: "egg_load_pass_0x48",      # loops every open egg for type 0x48
    0x11657: "build_episode_index",     # the two indexes; prints the banner
    0x141FE: "press_any_key_wait",      # the startup wait, before graphics
    0x144D7: "main",                    # the frame the runtime calls
    0x15232: "egg_find_block",          # (type, ?, index) -> block
    0x1580C: "sb_irq_handler",          # reads 0x22e, EOIs 0x0a0 and 0x020
}


def name(off):
    """The name for an image offset, or None. Exact matches only."""
    return FUNCTIONS.get(off)


def describe(off):
    """`name (tentative)` for a tentative entry, plain name otherwise, or ''."""
    n = FUNCTIONS.get(off)
    if not n:
        return ""
    return f"{n[:-1]} (tentative)" if n.endswith("?") else n
