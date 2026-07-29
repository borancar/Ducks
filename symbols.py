"""Names for image offsets we have identified, so reports say what a thing is.

Every note in this project refers to code by image offset, and every offset named
here was established by reading the routine, by watching it run, or by replacing
it with a native that was byte-compared against the original. **Not by inferring
from a call site** - two names were nearly recorded on call-site evidence alone
and both turned out wrong: `0x0b9ea` looked like a main routine and sets a
pointer, and `0x0c156` is called from main with no arguments and is a loader
pass. Read the body first.

Names ending in `?` are tentative and print as "(tentative)".

**A near-call target read off a disassembly is not an image offset.** `call rel16`
wraps within its 16-bit segment, and image offsets are not segment offsets, so a
target computed in image space can be a whole 64 KB out. `egg_find_block` was
recorded at `0x15232` - which is mid-instruction inside `play_sample` - when it is
at `0x05232`. test_symbols.py now requires every entry to start with a prologue,
which is what catches this.

`FUNCTIONS` holds function entry points. `VARIABLES` holds DGROUP offsets, which
live in a different space again - relative to the data segment, not the image.
`LOOPS` holds inline loop heads, which are *not* function starts - they sit inside a larger routine and are hooked where
they begin, so `find_function_start` will never return one.

Used by the control socket's `where`, and so by `stack`, `until`, `finish` and
the tail of `step`. Deliberately a plain dict rather than something wired into
`find_function_start`: an unnamed function still reports its bare offset, which
is what the notes are indexed by.
"""

# Image offset -> name. Sorted by offset; it reads as a map of the binary.
FUNCTIONS = {
    0x0014E: "crt_startup",             # no prologue; calls main
    0x00EB5: "install_int23",           # the Ctrl-C handler installer
    0x01238: "isatty",                  # native
    0x012EB: "dos_lseek",               # native
    0x014A3: "dos_read",                # native
    0x01D8E: "memmove_words",           # word-wise memmove; had the CGA snow waits
    0x01E94: "set_text_colour?",        # called with 15 before a banner
    0x02012: "puts",                    # (ds, offset); prints the banners
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
    0x04CA0: "sound_play_guarded",      # if [0x4f4]: forward (id, mode) to
                                        # 0x14750; 26 bytes and nothing else
    0x04D2A: "clear_vram",              # native
    0x04D4B: "page_flip",               # native
    0x0572A: "dac_set_black?",          # writes an index, then three zeros
    0x05671: "resource_release?",       # ends show_splash and show_resource
    0x05761: "plot_pixel",              # native
    0x057EE: "set_plane",               # native; map mask + [0x177d]
    0x05A67: "resource_load?",          # (&desc, type, index, ...) -> 0 on
                                        # failure; forwards to 0x058b9
    0x05AC2: "blit_rows_masked",        # native
    0x05C09: "blit_rows",               # native
    0x05D3A: "compose_layer",           # native
    0x05DC4: "compose_scroll",          # native
    0x05232: "egg_find_block",          # (type, ?, index) -> block; reads [0x20ad]
    0x056D2: "palette_upload",          # full 768-byte DAC upload, unscaled
    0x063D6: "draw_sprite",             # native
    0x065F1: "outline_sprite",          # native
    0x06869: "input_poll",              # accumulates relative motion into a
                                        # position and clamps it to (w, h)
    0x0675B: "mouse_motion",            # native
    0x0678E: "mouse_presses",           # native
    0x067BA: "mouse_releases",          # native
    0x0876A: "build_washed_ramp",       # v*0.75+64 into [0x0dad]
    0x0AB09: "particles",               # native
    0x0ABA5: "draw_entities",           # native
    0x0B10B: "palette_fade_step",       # the fade state machine
    0x0B52F: "show_resource_loop",      # (desc, frames): fade in, hold, fade
                                        # out. Holds a plane loop; not native
    0x0B9EA: "set_buffer",              # stores a far pointer into [0x1721]
    0x0BB3B: "draw_number",             # native
    0x0C0C2: "egg_load_one?",           # called per egg file with (0, type, i)
    0x0C156: "egg_load_pass_0x48",      # loops every open egg for type 0x48
    0x0C1AD: "show_resource",           # (type 0x4d, index, frames, 0xff):
                                        # load, display through 0x0b52f, release
    0x0D757: "draw_number2",            # native
    0x102D7: "show_splash",             # (image far *, frames): fade an image
                                        # in, hold for `frames` or until a key,
                                        # fade out. Holds a fifth plane loop.
    0x11547: "print_newline?",          # no args, bracketing the banners
    0x11657: "build_episode_index",     # builds both indexes; prints the banner
    0x13F2:  "farmalloc?",              # sizes both index arrays
    0x13519: "set_mode_x",              # BIOS 13h, then unchains to Mode X
    0x13676: "main_menu",               # PLAY/OPTIONS/READ ME/QUIT; drawn via
                                        # the layer compositor. Does not return
                                        # while the menu is up, so the game is
                                        # reached from inside it
    0x13FEA: "scan_save_slots",         # GAME1.SG..GAME5.SG; no args, no return;
                                        # its only output is [0x2055]
    0x141FE: "init",                    # the whole startup: banner, objects,
                                        # hardware detection, key wait
    0x144D7: "main",                    # the frame the runtime calls
    0x146CD: "after_menu?",             # runs once main_menu has returned, just
                                        # before the mode is restored. Was
                                        # guessed as game_main from position
                                        # alone; the game is inside main_menu
    0x14750: "sound_play?",             # gated on sound_state; ids below 0x96;
                                        # calls 0x14628(id, 0x20, 0xff)
    0x148A2: "detect_soundblaster",     # the sound check; probes the DSP
    0x14974: "detect_hardware",         # sound, then XMS, then prints
    0x149EA: "dsp_write",               # polls 0x22c, writes the byte
    0x15A49: "blaster_env_field",       # pulls one letter out of BLASTER
    0x15B37: "parse_blaster_env",       # getenv("BLASTER"), fields A/I/D
    0x15176: "stop_voice",              # native
    0x151D2: "play_sample",             # native
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

# DGROUP offsets we have identified. Read with `read d+0x…` over the control
# socket, which prints the name. These are *not* image offsets - a DGROUP offset
# is relative to the data segment, and `dgroup_base` is where that lands in
# memory. Mixing the two is the mistake documented in
# docs/notes/address-spaces.md.
VARIABLES = {
    0x054D: "draw_flag?",               # set to 4 around a loader pass
    0x0DAD: "palette_washed",           # 48 bytes: the lifted terrain ramp
    0x0D61: "flip_phase",               # 0..9, advanced by page_flip
    0x0DDD: "blink_toggle",             # flips between the two blink palettes
    0x0542: "registered_name",          # far pointer to the owner's name, read
    0x0544: "registered_name_seg",      # out of the egg; valid only if 0x548
    0x0548: "registered",               # non-zero = registered. init prints
                                        # "Registered to: <name>" or UNREGISTERED
    0x054A: "shareware_limit",          # reads 20; the intro screen says "20
                                        # levels classed as shareware"
    0x2032: "level_attempted?",         # compared against shareware_limit at
                                        # 0x13841; 0 before a level is loaded
    0x0DDF: "blink_countdown",          # randomised frames until the next flip
    0x10E1: "palette_stored",           # 768 bytes: the level's palette
    0x14B1: "palette_source",           # 48 bytes: the ramp washed_ramp reads
    0x1721: "current_buffer",           # far pointer, set by set_buffer
    0x1723: "current_buffer_seg",       # its segment half
    0x1725: "page_front",               # the visible page; page_flip swaps these
    0x1727: "page_back",
    0x177D: "current_plane",            # written by set_plane
    0x1798: "fade_level",               # 0..15, scales the palette
    0x179A: "fade_direction",           # 0xff seen when a fade is armed
    0x179B: "fade_start_colour",        # where the fade upload begins
    0x18D3: "mouse_x",                  # 32-bit, accumulated from deltas
    0x18D7: "mouse_y",                  # 32-bit
    0x18DB: "mouse_dx",                 # one poll's relative motion
    0x18DD: "mouse_dy",
    0x18DF: "button_a_down",            # via the mapping in [0x20e4]
    0x18E7: "button_b_down",
    0x20E4: "button_map_a",             # which INT 33h button is which
    0x20E6: "button_map_b",
    0x20E8: "button_map_c",
    0x18F6: "last_key",                 # ASCII; init spins until non-zero
    0x04FE: "video_mode",               # main passes this to set_mode_x
    0x1FD4: "game_speed",               # page_flip delays (0x1f - this) ms
    0x1FD5: "gamma",                    # (gamma + 6) / 19 scales every palette
    0x201E: "blink_enable_src",         # only ever written zero
    0x21A5: "save_name",                # "GAME-.SG"; +4 is patched per slot
    0x2055: "max_save_value?",          # scan_save_slots keeps the max over slots
    0x2104: "sound_available",          # detect_hardware's return
    0x210C: "init_objects",             # 3 far pointers to 22-byte objects
    0x2157: "blink_enable",             # copied from blink_enable_src; dead
    0x20A9: "egg_files",                # far pointer to the 23-byte descriptors
    0x20AD: "egg_file_count",           # how many are open
    0x20BA: "episode_index",            # far pointer to 14-byte records
    0x20BE: "readme_index",             # far pointer to 14-byte records
    0x20C2: "episode_count",            # sizes episode_index
    0x20C4: "readme_count",             # sizes readme_index
    0x20C6: "egg_stream",               # far pointer egg_getc reads through
    0x2908: "sound_state?",             # cleared at the top of detect_hardware
    0x3C78: "voice_table",              # 12-byte slots
    0x3CD8: "voice_busy",
    0x3D1C: "voice_active_count",
}


def variable(off):
    """The name for a DGROUP offset, or None."""
    return VARIABLES.get(off)


def describe_variable(off):
    """`name (tentative)` for a tentative entry, plain name otherwise, or ''."""
    n = VARIABLES.get(off)
    if not n:
        return ""
    return f"{n[:-1]} (tentative)" if n.endswith("?") else n
