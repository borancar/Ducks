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
    0x03791: "egg_read_byte",           # one byte off the egg stream, which it
                                        # takes as count/ptr/seg. Paired with
                                        # egg_read_word? at 0x04e88
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
    0x058B9: "resource_load_full",      # egg_find_block, then a header of two
                                        # words and a byte - width, height,
                                        # palette entries - then the palette three
                                        # bytes at a time into current_buffer, an
                                        # allocation, and a row decoder
    0x05A67: "resource_load",           # the form everything calls: the above
                                        # with set_size forced to 1
    0x04E88: "egg_read_word?",          # one word off the egg stream; the header
                                        # reads two
    0x05388: "alloc_image?",            # called with the descriptor before the
                                        # rows are decoded into it
    0x04F4B: "egg_read_string",         # a length word, then that many bytes into
                                        # a fresh allocation
    0x04DE6: "fatal",                   # (msg, arg): text mode back, "DUCKS fatal
                                        # error!", "OH NO: %s (%s)", exit(1)
    0x0615A: "sprite_set_load",         # (index, type, table, egg)
    0x08885: "image_alloc",             # (desc, w, h): size it, then alloc_image
    0x088B3: "sprite_set_free",         # every sprite's pixels, then the records
    0x088FA: "level_load",              # the whole level out of its 'L' block:
                                        # map, tools, entities, ducks, solids,
                                        # the backdrop, and viewport_game
    0x07490: "stamp_solid",             # (object, dest): copy where dest is 0
    0x1480F: "sound_preload",           # (id, scale): load without playing
    0x05AC2: "blit_rows_masked",        # native
    0x05C09: "blit_rows",               # native
    0x05D3A: "compose_layer",           # native
    0x05DC4: "compose_scroll",          # native
    0x051B7: "close_egg_files",         # walks egg_files backwards,
                                        # fclose then free, stride 0x17
    0x05232: "egg_find_block",          # (type, ?, index) -> block; reads [0x20ad]
    0x056D2: "palette_upload",          # full 768-byte DAC upload, unscaled
    0x063D6: "draw_sprite",             # native
    0x065F1: "outline_sprite",          # native
    0x06869: "input_poll",              # accumulates relative motion into a
                                        # position and clamps it to (w, h)
    0x0675B: "mouse_motion",            # native
    0x0678E: "mouse_presses",           # native
    0x067BA: "mouse_releases",          # native
    # The font. One proportional outlined face, 94 glyphs, loaded from the
    # egg's single 'F' block into a 256-entry table at d+0x54d.
    0x06A87: "font_clear",              # zeroes all 256 widths
    0x06AA4: "font_load",               # reads block 'F', mallocs each glyph
    0x06C29: "glyph_to_screen",         # (ch, x, y) through the plot pointer
    0x06CB6: "glyph_to_image",          # (desc, ch, x, y) into a row table
    0x06D52: "text_width",              # native. Sums width-1 over a string,
                                        # from 1
    0x06D84: "draw_string",             # (desc, str, x, y): glyph_to_image
                                        # per character, advancing by its return
    0x0881D: "make_rect",                # (r, top, bottom, left, right): fills
                                        # a viewport_t and derives its width and
                                        # height. Already in game.c; it was only
                                        # unnamed here, which is why the gap
                                        # count had it as missing
    0x0A410: "message_post",             # (fmt, arg): the three-slot on-screen
                                        # ticker, 100 frames a line, newest at
                                        # the bottom. A null fmt posts nothing
    0x0876A: "build_washed_ramp",       # native. v*0.75+64 into [0x0dad]
    0x0537D: "egg_block_end",           # native
    0x0580B: "rle_reset",                # native
    0x05F15: "scroll_axis_snap",         # native
    0x05F7F: "scroll_axis_toward",      # native
    0x0A3A7: "scene_swap_pair",          # native
    0x0600D: "scroll_follow",            # native
    0x0B0C5: "palette_apply_gamma",      # native
    0x0D7EE: "run_level",                # 4287 bytes, and not a frame: two
                                        # thirds of it is one loop, so it is the
                                        # whole of playing a level and returns
                                        # when the level is over. One argument,
                                        # non-zero for a demo
    0x0CF07: "played_tool_events",       # a played level's input: the cycle
                                        # button, the arrows, the digits, and the
                                        # debug keys. tool_events is the demo's
                                        # counterpart and run_level calls one
    0x0CE2E: "pause_screen",             # P, behind cheat_state[5]
    0x0D4C2: "tool_events",              # native
    0x0D55D: "tool_list_has",            # native
    0x0D591: "tool_list_any_flagged",    # native
    0x0D6C3: "bg_scroll_reset",          # native
    0x0993B: "collide_scenes",           # native. Scene 0 against scene 2, every duck
                                        # against every object: |dx| < anim_a
                                        # [type] and |dy| < 3, then a switch on
                                        # the object's type
    0x0979F: "scene_keep_positions",    # native. Copies each entity's
                                        # position to +0x0c/+0x10, so what is
                                        # there is where it was when the frame
                                        # began
    0x0AB09: "particles",               # native
    0x0ABA5: "draw_entities",           # native
    0x0B10B: "palette_fade_step",       # the fade state machine
    0x0B52F: "show_resource_loop",      # (desc, frames): fade in, hold, fade
                                        # out. Holds a plane loop; not native
    0x0B9EA: "set_buffer",              # stores a far pointer into [0x1721]
    0x0BB3B: "draw_number",             # native
    0x0C0C2: "egg_load_one?",           # called per egg file with (0, type, i)
    0x0C156: "egg_load_all",            # loops every open egg for type 0x48
    0x0C1AD: "show_resource",           # (type 0x4d, index, frames, 0xff):
                                        # load, display through 0x0b52f, release
    0x0D757: "draw_number2",            # native
    # The homecoming sequence, in the order game_main calls them at 0x1392f
    # onwards. Each takes no arguments, loads its own resource by id and shows it
    # through its own four-plane loop. Named for what they draw, which was
    # watched. They run only after the LAST episode - finishing level 80 - which
    # episode_end_gate below decides. See docs/notes/homecoming-sequence.md
    0x0F5B1: "cutscene_rocket_space",    # id 0x32: the rocket crossing a
                                         # starfield, then leaving the frame
    0x0F825: "cutscene_welcome_home",    # id 0x36: the flock under a
                                         # "Welcome Home!" banner
    0x0F913: "cutscene_photos",          # ids 0x3a-0x3c: three polaroids, one
                                         # more per screen, each on a DAC flash
    0x0F9FD: "cutscene_doorstep",        # ids 0x37/0x38: the lit doorway,
                                         # silhouette then revealed
    0x0FC8B: "cutscene_rocket_landing",  # ids 0x33/0x34: down on the grass at
                                         # dusk; 12 draw_sprite calls. The only
                                         # one that never reached its own return
                                         # under a driven call
    0x102D7: "show_splash",             # (image far *, frames): fade an image
                                        # in, hold for `frames` or until a key,
                                        # fade out. Holds a fifth plane loop.
    0x100F4: "cutscene_night_monster",  # the sixth ending screen, and the only
                                        # animated one: at night, a monster runs
                                        # towards the house. Called at 0x1396e,
                                        # confirmed from the return address on
                                        # its own stack frame, not the listing
    0x1240F: "load_demo?",              # (index): egg_find_block and friends;
                                        # 0 means the caller shows DEMO MISSING
    0x126DB: "pick_random_demo?",       # rand() % [0x2038], then load_demo
    # game_main's switch table at 0x13a70 dispatches the menu's action code to
    # these. Each was caught at its entry by choosing the item that reaches it.
    0x12951: "load_game_screen",        # code 6: LOAD SAVED GAME, listing slots
    0x12EDF: "check_registration",      # hashes the name over the 27-letter
                                        # alphabet at d+0x21b0; the key is the
                                        # result as six digits
    0x13096: "register_screen",         # code 14: REGISTER DUCKS, ENTER YOUR NAME
    0x13298: "save_game_screen",        # code 5: SAVE THIS GAME, the five slots
    0x11547: "console_rule",            # a newline and eighty dashes: the rule
                                        # between the startup screen's sections
    0x1157A: "read_index",              # (array, start, &total, egg, store):
                                        # the reader both the episode and the
                                        # readme index are filled by
    0x0B9FC: "show_attract_screen",   # the hall of fame, and what the menu
                                        # shows when it is left alone
    0x11D1B: "score_set",              # one row of the board
    0x12DFB: "high_score_name",        # NEW HIGH SCORE!, then name_entry
    0x0F55C: "menus_after_game",       # undoes menus_resume
    0x13DFB: "load_settings",          # the other half of save_settings, and
                                        # what fills the hall of fame
    0x13CCD: "load_eggs_ini",          # the [EGGS] section of EGGS.INI
    0x0B9FC: "show_attract_screen",     # the hall of fame, and what the menu
                                        # shows when it is left alone
    0x11D1B: "score_set",               # one row of the board
    0x12DFB: "high_score_name",         # NEW HIGH SCORE!, then name_entry
    0x0F55C: "menus_after_game",        # undoes menus_resume
    0x13DFB: "load_settings",           # the other half of save_settings, and
                                        # what fills the hall of fame
    0x13CCD: "load_eggs_ini",           # the [EGGS] section of EGGS.INI
    0x11D54: "high_score_screen",       # both halves of it: NEW HIGH SCORE!
                                        # ENTER YOUR NAME, then DUCKS HALL OF
                                        # FAME, each seen on the stack under the
                                        # screen it draws. game_main calls it at
                                        # four sites, one before a level starts,
                                        # so it presumably tests whether the
                                        # score qualifies first - that part has
                                        # not been watched
    0x11C75: "episode_end_gate",        # (level, 0): find the episode whose last
                                        # level is this, show its splash, and
                                        # return that record's terminator flag -
                                        # so it says "the FINAL episode ended",
                                        # which is what gates the homecoming
    0x0C716: "run_screen",              # draws a screen, takes input, returns a
                                        # pointer to the item chosen: +8 action
                                        # code, +0xb a parameter. Holds
                                        # plane_loop_layer
    0x0C20E: "cursor_to_centre",        # (desc) puts the pointer in the middle
    0x0C237: "draw_menu_item",          # (index, style, bounce): draw_banner into
                                        # the backdrop. style 0/1/2 picks one of
                                        # the three palette banks, bounce indexes
                                        # the spacing table at d+0x1904
    0x0C299: "item_label",              # writes ON/OFF or LEFT/RIGHT over the
                                        # tail of an item's own text
    0x0C3DE: "typed_clear",             # 32 spaces and a NUL
    0x0C3FE: "typed_push",              # slides one character in and looks for a
                                        # cheat word; flashes the border
    0x0C4F0: "slider_screen",           # action 0x11: GAME SPEED, AMBIENCE
                                        # VOLUME, GAMMA CORRECT
    0x06DBC: "scene_add",               # (scene, x, y, type, param)
    0x06EE9: "scene_alloc",             # (scene, capacity): capacity * 0x29
    0x077AE: "particles_spawn",          # native. (x, y, n): four rand draws a
                                        # particle, and the order of them is as
                                        # load-bearing as the values
    0x078F7: "duck_dies",                # (e, force, noisy). With force clear it
                                        # does nothing while g_509 is set, so
                                        # g_509 is "ducks do not die"; a demo
                                        # clears it, which is when they do
    0x07646: "bridge_step_end",          # (end, dir): walks one end of a bridge
                                        # and stops it dead on solid backdrop
    0x076E2: "bridge_grow",              # one frame of a bridge building itself
    0x0739C: "stamp_sprite_into",        # blast_terrain's twin: writes the
                                        # sprite's pixels instead of zeroes
    0x0799C: "ground_check",             # the anti-bridge-stacking warning
    0x078A6: "tool_step",                # one line of the frame: grow a bridge
    0x0751B: "blast_terrain",            # (x, y, sprite): writes 0 through a
                                        # sprite into the backdrop - the bomb's
                                        # hole - then stamps every solid back
    0x07A36: "tool_use",                 # native. (x, y, tool): where a tool's
                                        # effect is decided. Publishes [0x1fd6]
                                        # and the busy flag [0x1fd8], then one of
                                        # four arms; only the drag pair leaves
                                        # the flag set
    0x0799C: "ground_check",             # (x*, y): sixteen pixels under 0xc8
                                        # within 28 rows, or it complains
    0x078D4: "entity_set_type",         # native. (e, type): zeroes the frame
                                        # counter
                                        # only when the type actually changes
    0x06F4F: "entity_copy",             # native. (scene, from, to): eleven
                                        # fields, and not +0x08..0x13, so the
                                        # destination keeps its own prev_x/y
    0x06A49: "image_clear",             # native. (desc, value), a row at a time
    0x087BC: "load_background",         # (index, egg): the tile behind a menu,
                                        # palette at entry 64, then the wrap masks
    0x05A95: "resource_load_at",        # resource_load_full with nothing
                                        # allocated and a destination row
    0x0A52A: "animate_scene",           # steps every entity's animation
    0x0E8AD: "menu_reset",              # count = 0, background = 3
    0x0E8C3: "menu_set_text",           # free and replace an item's text
    0x0E8ED: "menu_add",                # the one that adds an item; everything
                                        # below is a forwarder over it
    0x0E9E9: "menu_add_action",
    0x0EA12: "menu_add_title",          # action 0, and menu_never for its flag
    0x0EA36: "menu_add_toggle",         # action 0x10
    0x0EA5E: "menu_add_cycle",          # action 0x13
    0x0EA86: "menu_add_entry",          # action 0x11
    0x0EAAE: "menu_add_submenu",        # action 0x12
    0x0EAD6: "menu_free",
    0x0EB04: "menu_add_list",           # a list cut into pages of three, each
                                        # with a MORE_ to a malloc'd next page
    0x0EC46: "build_menus",             # all fifteen of them, once, from init
    0x12281: "add_save_slots",         # one menu entry per GAMEn.SG
    0x1239E: "find_egg_by_id",         # which open egg a save belongs to
    0x12951: "load_game_screen",
    0x12B6A: "name_entry",             # typing a save's name, drawn as one
                                        # more line of the menu behind it
    0x13298: "save_game_screen",
    0x128A5: "menus_resume",           # relabels two items for a game in
                                        # progress, and changes one action
    0x04EBB: "write_word",             # high byte first
    0x04FBD: "write_string",           # a length, then the +1 shift put back
    0x1271B: "menu_screen_driver",      # game_main's first call, and the whole
                                        # attract cycle: the menu, a demo level
                                        # it plays by itself, and DUCKS HALL OF
                                        # FAME, all three caught above it on the
                                        # stack. It has two internal call sites
                                        # (ret 0x12736 and 0x12766) and the table
                                        # has been caught under each, so they are
                                        # not one screen apiece
    0x11EFB: "show_readme_section",     # (n) = ordinal into the readme index;
                                        # 2 is HOW TO REGISTER. The viewer the
                                        # readme crash happens in
    0x13A98: "load_animations",         # the 'G' block: a sprite script and five
                                        # per-type fields for each entity type,
                                        # into the six arrays at d+0x009a
    0x11657: "build_episode_index",     # builds both indexes; prints the banner
    0x13F2:  "farmalloc?",              # sizes both index arrays
    0x13519: "set_mode_x",              # BIOS 13h, then unchains to Mode X
    0x13676: "game_main",               # the whole game, not just its menu: an
                                        # outer loop around the menu screens and
                                        # an inner one that unpacks an episode
                                        # record, checks the shareware limit,
                                        # calls the in-game frame, shows the
                                        # bonus screen, and runs the ending. The
                                        # only call main makes that does not
                                        # return while play is going on. First
                                        # named game_main, which described the
                                        # screen it opens with rather than the
                                        # function
    0x13FEA: "scan_save_slots",         # GAME1.SG..GAME5.SG; no args, no return;
                                        # its only output is [0x2055]
    0x140B1: "save_settings",           # fopen("settings.dat","wb") and
                                        # writes the word array at [0x4f4]
    0x141FE: "init",                    # the whole startup: banner, objects,
                                        # hardware detection, key wait
    0x144D7: "main",                    # the frame the runtime calls
    0x14628: "sound_load",              # (id, scale, egg): egg block 0x58, a
                                        # length word and that many signed
                                        # bytes, scaled by scale/32 on the way
                                        # in. id -> slot at d+0x298c
    0x146CD: "release_sounds",          # stop_sound_by_id(0..4) if sound_state,
                                        # then pops a stack at [0x290b]/[0x298b]
                                        # through 0x15138. Called four times
                                        # inside game_main, not only at exit.
                                        # Was guessed as game_main from position
                                        # alone; the game is inside game_main
    0x14750: "sound_play",              # (id, voice): gated on sound_state; ids
                                        # below 0x96; loads at unity volume.
                                        # A voice other than 1 stops itself
                                        # first, and 4 loops
    0x147C5: "sound_play_loop",         # (id, scale, egg): the ambience, on
                                        # voice 0, looping, at the caller's
                                        # volume
    0x14F07: "sample_load",             # into XMS, through a 2 KB staging
                                        # buffer; the scaling is here
    0x157C1: "sound_gather",            # 256 accumulator words through the clip
                                        # table into the DMA buffer
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
    0x04FA: "scroll_smooth",            # 1 = ease the view toward the followed
                                        # point, 0 = only move it when the point
                                        # would leave. Starts 1; a level event
                                        # (0x0cf07) toggles it and nothing else
                                        # writes it
    0x0D63: "scenes",                   # six scene_t, twelve bytes each: 0xd63,
                                        # 0xd6f, 0xd7b, 0xd87, 0xd93, 0xd9f
    0x2177: "game_in_progress",         # menus_resume sets it, menus_after_game
                                        # clears it, run_screen and game_main
                                        # test it. --no-demo works by setting it,
                                        # which also disables the PLAYBACKTIME
                                        # cheat, since that is guarded on it too
    0x1798: "level_running",            # run_level loops while this is set
    0x178C: "tool_scene",               # the two-entity scene the tool cursor is
    0x201A: "level_clock",              # frames since the level started; both
                                        # event tables compare against it
    0x210C: "message_image",            # three desc_t far *, rotated on post
    0x2118: "message_rect",             # three viewport_t, 0x14 apart
    0x2154: "message_time",             # three countdowns, 100 frames a line
    0x172D: "view_top",                 # the play area's edges: 0x172d/0x172f
    0x1731: "view_left",                # and 0x1731/0x1733
    0x1FD6: "tool_in_use",              # and 0x1fd8 the busy flag
    0x1FE0: "drag_anchor",              # (x, y-1) twice: anchor at 0x1fe0 and
                                        # current at 0x1fe6, which is the rubber
                                        # band
    0x20FF: "too_deep_count",           # third complaint gives up
    0x2007: "duck_count",               # the HUD's second number, and what the
                                        # "not enough got home" ending compares
    0x3006: "rand_seed",                # a long. srand keeps only the low word
    0x18C1: "particle_array",           # far pointer, farmalloc'd per level
    0x18C5: "particle_colours",         # eight, one picked per particle
    0x18CD: "particle_count",           # live, and 0x18cf the capacity
    0x2039: "level_seed",               # srand'd once at level start, which is
                                        # what makes a demo replay identically
    0x1717: "bg_w",                     # the background tile's size, two words.
                                        # load_background derives wrap_x/wrap_y
                                        # from these by subtracting one
    0x202C: "bg_drift",                 # one byte the level carries, two base-3
                                        # digits: 1 - digit is the drift on each
                                        # axis, so each is +1, 0 or -1
    0x20B6: "egg_block_open",           # non-zero while a block is being read;
                                        # egg_find_block will not open another
    0x20CE: "rle_left",                 # a long: bytes still to come from the
                                        # run the resource reader is in
    0x1701: "level_w",                  # the level's size in pixels, two words
    0x1735: "view_w",                   # the view's, two words at 0x1735/0x1737
    0x1739: "scroll_x",                 # two longs, 0x1739 and 0x173d
    0x18F5: "scroll_shift",             # how much of the way to the target the
                                        # view moves each frame, as a right shift

    # Text. A glyph's pixels are 0 transparent, 1 fill, 2 outline, and both
    # glyph drawers look the value up as [colour_base + value] - so 1 reads
    # 0x54c and 2 reads 0x54d. The screens set these two bytes around a page and
    # put them back; nothing writes 0x54b, which value 0 would have selected.
    0x054C: "text_fill",                # colour for a glyph's value 1
    0x054D: "text_outline",             # colour for its value 2. The same byte
                                        # is font[0]'s width, which is harmless:
                                        # a string ends at character 0, so that
                                        # slot is never measured or drawn
    0x054E: "font",                     # really 0x54d: 256 entries of 8 bytes,
                                        # { uint16 w, uint16 h, uint8 far *px },
                                        # indexed by character. Named one byte
                                        # on so the two colours read back
    0x0DAD: "palette_washed",           # 48 bytes: the lifted terrain ramp
    0x0D61: "flip_phase",               # 0..9, advanced by page_flip
    0x0DDD: "blink_toggle",             # flips between the two blink palettes
    0x0542: "registered_name",          # far pointer to the owner's name, read
    0x0544: "registered_name_seg",      # out of the egg; valid only if 0x548
    0x0548: "registered",               # non-zero = registered. init prints
                                        # "Registered to: <name>" or UNREGISTERED
    # The geometry set_mode_x selects, which everything above it reads instead of
    # testing the resolution itself.
    0x0538: "screen_width",             # 360 wide, 320 narrow
    0x053A: "screen_height",            # 240 wide, 200 narrow
    0x053C: "screen_x0",                # the horizontal centring offset: 20 in
                                        # the 360-wide mode, 0 otherwise. The play
                                        # area stays 320 wide and every viewport
                                        # and splash rect is built x0 .. x0 + 320,
                                        # so the wide mode translates the picture
                                        # right by 20 rather than widening it
    0x053E: "plot",                     # far pointer to the pixel plotter,
    0x0540: "plot_seg",                 # swapped by set_mode_x: 0x05761 at
                                        # stride 80, 0x057a1 at stride 90
    0x054A: "shareware_limit",          # reads 20; the intro screen says "20
                                        # levels classed as shareware"
    0x0094: "episode_egg_index",        # the episode record's +6, and game_main
                                        # indexes egg_files by it (stride 0x17).
                                        # 0 in this build - one egg - which is
                                        # why it reads like a zero high word
    0x2032: "level_attempted",          # the level about to be played. Set from
                                        # the episode's first level at 0x137a7,
                                        # inc'd at 0x139ab after each one, and
                                        # read by episode_end_gate as the level
                                        # just finished. Poking 80 into it plays
                                        # level 80, which is how the ending was
                                        # reached
    0x2022: "background_warp",          # non-zero runs compose_scroll's warp.
                                        # First ever seen set on level 80
    0x2177: "menu_idle_suppress",       # non-zero stops the menu's 500-frame
                                        # idle timeout at 0x0c9d6, and so the
                                        # fade-out it starts and the demo or
                                        # Hall of Fame that follows. What
                                        # hovering a menu item does; --no-demo
    0x21AE: "attract_choice",           # toggled every pass at 0x127ee: 0 plays
                                        # a demo level, non-zero shows the
                                        # attract screen instead
    0x179F: "warp_table",               # 32 x displacement entries, indexed by a
                                        # phase re-masked to 0x1f every row
    0x17BF: "warp_phase",               # where row 0 starts in the table
    0x17C0: "warp_step",                # added per row, before the re-mask
    0x0DDF: "blink_countdown",          # randomised frames until the next flip
    0x10E1: "palette_stored",           # 768 bytes: the level's palette
    0x14B1: "palette_source",           # 48 bytes: the ramp washed_ramp reads
    0x1721: "current_buffer",           # far pointer, set by set_buffer
    0x1723: "current_buffer_seg",       # its segment half
    0x1725: "page_front",               # the visible page; page_flip swaps these
    0x1727: "page_back",
    0x177D: "current_plane",            # written by set_plane
    # The four 20-byte viewport records, three of them built by set_mode_x from
    # the geometry it selects: (top, bottom, left, right). Named for the region
    # each covers at 360x240, where they differ - at 320x200 the middle band and
    # the full screen coincide.
    0x172D: "viewport_game?",           # the in-game scenes' clip; not built by
                                        # set_mode_x, so where it comes from is
                                        # still unread
    0x1741: "viewport_panel",           # the bottom 40 rows: the status strip,
                                        # and the seventh scene draws into it
    0x1755: "viewport_full",            # 0,0 to width,height - everything
    0x1769: "viewport_screen",          # the centred 320x200 window: rows 20-220
                                        # at 360x240, the whole screen at 320x200.
                                        # What the splashes and cutscenes clip to
    0x1798: "fade_level",               # 0..15, scales the palette
    0x179A: "fade_direction",           # signed byte, +1 or -1: fade_level is
                                        # stepped by it through `mov al / cbw /
                                        # add`, so 0xff is -1 and not 255
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
    0x20C6: "egg_stream",               # far pointer egg_read_byte reads through
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
