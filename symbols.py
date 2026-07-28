"""Names for image offsets we have identified, so reports say what a thing is.

Every note in this project refers to code by image offset, and every offset named
here was established by reading the routine, by watching it run, or by replacing
it with a native that was byte-compared against the original. **Not by inferring
from a call site** - two names were nearly recorded on call-site evidence alone
and both turned out wrong: `0x0b9ea` looked like a main routine and sets a
pointer, and `0x0c156` is called from main with no arguments and is a loader
pass. Read the body first.

Names ending in `?` are tentative and print as "(tentative)".

`FUNCTIONS` holds function entry points. `LOOPS` holds inline loop heads, which
are *not* function starts - they sit inside a larger routine and are hooked where
they begin, so `find_function_start` will never return one.

Used by the control socket's `where`, and so by `stack`, `until`, `finish` and
the tail of `step`. Deliberately a plain dict rather than something wired into
`find_function_start`: an unnamed function still reports its bare offset, which
is what the notes are indexed by.
"""

# Image offset -> name. Sorted by offset; it reads as a map of the binary.
FUNCTIONS = {
    0x0014E: "crt_startup",             # no prologue; calls main
    0x01238: "isatty",                  # native
    0x012EB: "dos_lseek",               # native
    0x014A3: "dos_read",                # native
    0x01D8E: "memmove_words",           # word-wise memmove; had the CGA snow waits
    0x02012: "puts?",                   # takes (ds, offset); prints the banners
    0x02067: "bios_video",              # native; inside the INT 10h wrapper below
    0x0202C: "bios_video_int10",        # every INT 10h site in the image
    0x02108: "read_pit",                # the PIT latch-and-read
    0x0223E: "delay_ms",                # Borland delay(), spins on the PIT
    0x0293A: "int86",                   # native; builds an INT on the stack
    0x029D3: "ioctl",                   # native
    0x029FC: "kbhit",                   # native; the single key-poll choke point
    0x02E07: "dos_setblock",            # native
    0x02F2D: "dos_getattr",             # native
    0x02F72: "dos_close",               # native
    0x03791: "egg_getc",                # getc on the egg stream: count/ptr/seg
    0x03AFE: "dos_open",                # native
    0x04B10: "dos_write",               # native
    0x04D04: "set_bios_mode",           # AH=0, AL=mode, then int86(0x10)
    0x04D2A: "clear_vram",              # native
    0x04D4B: "page_flip",               # native
    0x05761: "plot_pixel",              # native
    0x057EE: "set_plane",               # native; map mask + [0x177d]
    0x05AC2: "blit_rows_masked",        # native
    0x05C09: "blit_rows",               # native
    0x05D3A: "compose_layer",           # native
    0x05DC4: "compose_scroll",          # native
    0x056D2: "palette_upload",          # full 768-byte DAC upload, unscaled
    0x063D6: "draw_sprite",             # native
    0x065F1: "outline_sprite",          # native
    0x0675B: "mouse_motion",            # native
    0x0678E: "mouse_presses",           # native
    0x067BA: "mouse_releases",          # native
    0x0876A: "build_washed_ramp",       # v*0.75+64 into [0x0dad]
    0x0AB09: "particles",               # native
    0x0ABA5: "draw_entities",           # native
    0x0B10B: "palette_fade_step",       # the fade state machine
    0x0B9EA: "set_buffer",              # stores a far pointer into [0x1721]
    0x0BB3B: "draw_number",             # native
    0x0C0C2: "egg_load_one?",           # called per egg file with (0, type, i)
    0x0C156: "egg_load_pass_0x48",      # loops every open egg for type 0x48
    0x0D757: "draw_number2",            # native
    0x11657: "build_episode_index",     # builds both indexes; prints the banner
    0x13F2:  "farmalloc?",              # sizes both index arrays
    0x13519: "set_mode_x",              # BIOS 13h, then unchains to Mode X
    0x141FE: "press_any_key_wait",      # the startup wait, before graphics
    0x144D7: "main",                    # the frame the runtime calls
    0x15176: "stop_voice",              # native
    0x151D2: "play_sample",             # native
    0x15232: "egg_find_block",          # (type, ?, index) -> block
    0x15267: "stop_sound_by_id",        # native
    0x15298: "is_sound_playing",        # native
    0x156CC: "mix_voice",               # native
    0x157C1: "sound_gather",            # native
    0x1580C: "sb_irq_handler",          # reads 0x22e, EOIs 0x0a0 and 0x020
    0x159AE: "xms_present",             # native
    0x159C7: "xms_get_entry",           # native
}

# Inline loop heads. These are hooked where the loop body begins, inside a larger
# function, so they are landmarks rather than entry points.
LOOPS = {
    0x056E0: "dac_loop_upload",         # 768 bytes, >> 2, from [0x10e1]
    0x0B15F: "dac_loop_fade",           # the 94%-of-port-IO one
    0x0B1C9: "dac_loop_blink_alt",      # 16 colours from [0x0dad]; unreachable
    0x0B202: "dac_loop_blink_normal",   # the same 16 from [0x10e1]; unreachable
    0x0BC4B: "plane_loop_tally",        # exit 0x0bca9
    0x0CD5F: "plane_loop_layer",        # exit 0x0cd98
    0x0D9A2: "plane_loop_hud",          # exit 0x0db2c
    0x0E4DC: "plane_loop_scroll",       # exit 0x0e673
}


def name(off):
    """The name for an image offset, or None. Functions first, then loop heads."""
    return FUNCTIONS.get(off) or LOOPS.get(off)


def describe(off):
    """`name (tentative)` for a tentative entry, plain name otherwise, or ''."""
    n = name(off)
    if not n:
        return ""
    return f"{n[:-1]} (tentative)" if n.endswith("?") else n
