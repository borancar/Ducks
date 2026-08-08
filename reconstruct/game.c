/* game.c - code segment 0x04ca, which is the whole game.
 *
 * Reconstructed from Ducks.unpacked.exe. C99, aimed at eventually building and
 * running rather than at matching what Turbo C++ would accept; it does not
 * compile yet, because most of the segment is still missing. Every function
 * carries the image offset it was read from, so any line can be checked against
 * the disassembly. Names ending in a comment saying "unnamed" are ours only in
 * the sense that we have not identified the routine.
 *
 * The game's own logic. The hardware and DOS primitives it calls - set_plane,
 * page_flip, the blitters, the mouse wrappers - are in dos_io.c, split out for
 * porting. Both files are the same code segment (0x04ca): that boundary is ours,
 * chosen for what a port must replace, not something the binary proves. See
 * README.md.
 *
 * Functions are in address order, which within a module is source order.
 *
 * Sources, all under docs/notes/: entry-points.md for main and the screen
 * players, menu-loop.md for the menu, homecoming-sequence.md for the ending,
 * episode-index.md for the index it reads. The root README for page_flip.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <strings.h>       /* strcasecmp, which is Borland's stricmp */
#include <ctype.h>         /* toupper, at 0:0x184f */

#include "dos.h"


/* The fifteen menu descriptors, consecutive in DGROUP from 0x1916 to 0x1fd2 and
 * built once by build_menus. Nothing in the image initialises them; the whole
 * menu system is data assembled at startup out of the string tables. */
menu_t         main_menu;               /* 0x1916 - what main passes in */
menu_t         menu_play;               /* 0x1989 */
menu_t         menu_options;            /* 0x19fc */
menu_t         menu_quit;               /* 0x1a6f */
menu_t         menu_resolution;         /* 0x1ae2 */
menu_t         menu_episodes;           /* 0x1b55 */
menu_t         menu_audio;              /* 0x1bc8 */
menu_t         menu_video;              /* 0x1c3b */
menu_t         menu_mouse;              /* 0x1cae */
menu_t         menu_load_save;          /* 0x1d21 */
menu_t         menu_idle;               /* 0x1d94 */
menu_t         menu_readme;             /* 0x1e07 */
menu_t         menu_buttons;            /* 0x1e7a */
menu_t         menu_end_game;           /* 0x1eed */
menu_t         menu_demos;              /* 0x1f60 */

/* ---------------------------------------------------------------- globals
 *
 * All of these live in DGROUP; the offset in each comment is what the code
 * indexes and what `read d+0x...` over the control socket prints. Names come
 * from symbols.py, which carries the evidence for each; a name of the form
 * `g_xxxx` means the offset is used here but the variable is not identified.
 *
 * Definitions rather than externs, and that is a claim with evidence behind it.
 * Scanning the whole image for writes to each of these and binning the writing
 * instruction by code segment: every variable that is written at all is written
 * only from the game's own segment. Nothing in the runtime, the sound API, the
 * mixer, XMS or the BLASTER parser assigns to any of them, so they belong to this
 * module rather than being imported into it.
 *
 * The scan misses some write forms - byte stores through AL, stores through a
 * pointer, anything indexed - so "no write found" below is a gap in the scan and
 * not evidence of an external owner. game_speed is the clearest example: nothing
 * matched, yet the GAME SPEED slider was watched writing it.
 *
 * Types come from how the code reads each one, never from the literals it stores:
 * `cbw`/`cwd` or a signed jump after a compare means signed, a zeroed high half or
 * an unsigned jump means unsigned, and the width comes from the access size, with
 * `les`/`lds` marking a far pointer and an `adc` marking 32-bit arithmetic. The
 * WIDTH below is evidence-backed throughout. SIGNEDNESS is only established where
 * the comment says so - about a third of them. The rest are int16_t because that
 * is what a Turbo C++ `int` is, and that is a default rather than a finding.
 */

/* video and the flip */
uint8_t    game_speed = 29;      /* 0x1fd4 - 0..0x1f, higher is faster; read
                                  * as `mov al / mov ah, 0`, so unsigned */
uint8_t    gamma_level = 16;     /* 0x1fd5 - what GAMMA CORRECT sets, and 16 is
                                  * what the image initialises it to. Every
                                  * palette component is scaled by (this + 6)/19,
                                  * so 13 would be no correction at all */
int16_t    fade_level;           /* 0x1798 - 0..15, scales the palette. A word,
                                  * but also read a byte at a time in places */
int8_t     fade_direction;       /* 0x179a - +1 or -1; palette_fade_step reads it
                                  * with `mov al / cbw / add [fade_level], ax`, and
                                  * that cbw is what makes it signed rather than
                                  * 0xff being a large positive byte */
int16_t    fade_start_colour;    /* 0x179b - a word: every store to it is
                                  * `mov word [0x179b], imm`, not a byte store */
viewport_t viewport_game;        /* ds:0x172d - the in-game scenes' clip */
viewport_t viewport_panel;       /* ds:0x1741 - the bottom 40 rows */
viewport_t viewport_full;        /* ds:0x1755 - everything */
viewport_t viewport_screen;      /* ds:0x1769 - the centred 320x200 window */
/* ds:0x13f1 - not a pointer but the buffer itself: every set_buffer that
 * "restores" pushes ds and this offset, so it is the palette area everything
 * falls back to when nobody has published a buffer of their own. 768 bytes,
 * which is what palette_build reads out of it. */
uint8_t    default_buffer[768];
/* ds:0x1721, and it starts out pointing at the buffer above. That is not a
 * convenience of the port: the image holds `f1 13 95 18` at d+0x1721 with a
 * relocation entry on the segment half, so the linker emitted a far pointer to
 * DGROUP:0x13f1 and no code ever has to publish the fallback. set_buffer at
 * 0x0b9ea is the only store to it in the whole image; every other reference is
 * `les bx, [0x1721]`.
 *
 * The port used to zero it and call a made-up buffer_init from main, because a
 * bare declaration made show_splash write a palette through a null pointer
 * before anything had published one. There was never a gap to fill - only an
 * initialiser that had been dropped. See open-dgroup-initialisers. */
void far  *current_buffer = default_buffer;
/* The palette the DAC loops actually upload, and the washed copy the menu blink
 * alternates with. palette_build (0x0b0c5) and photo_fade_step fill the first
 * out of current_buffer; build_washed_ramp (0x0876a) fills the second out of
 * default_buffer's entry 64. Both are zero in the image, so bare is right here -
 * unlike current_buffer above. Read from dos_io.c and sdl_io.c, which is why
 * they were left in stubs.c long after they stopped being stubs. */
uint8_t    palette_stored[768];  /* 0x10e1 */
uint8_t    palette_washed[48];   /* 0x0dad - 16 entries, the terrain ramp */
/* Written by set_mode_x, set_plane and page_flip, all of which are in dos_io.c,
 * so that is where these are defined. Declared here because game.c reads them:
 * show_splash centres its rect with screen_x0, particles plots through `plot`. */
extern int16_t    video_mode;    /* 0x04fe */
extern int16_t    screen_width;  /* 0x0538 */
extern int16_t    screen_height; /* 0x053a */
extern int16_t    screen_x0;     /* 0x053c - the centring offset */
extern uint8_t    current_plane; /* 0x177d - written by set_plane */
extern uint16_t   page_front;    /* 0x1725 - swapped by page_flip */
extern uint16_t   page_back;     /* 0x1727 */
extern int16_t    flip_phase;    /* 0x0d61 */

/* input */
int32_t    mouse_x, mouse_y;     /* 0x18d3, 0x18d7 - 32-bit from the add/adc
                                  * pairs, and SIGNED: the clamp compares the
                                  * high word with jg/jl and only then the low
                                  * word with ja/jae, which is how a 32-bit
                                  * signed compare is done on a 16-bit machine.
                                  * An automated pass that sees the `ja` alone
                                  * calls this unsigned, and did */
int16_t    mouse_dx, mouse_dy;   /* 0x18db, 0x18dd - one poll's motion, signed:
                                  * each is `mov ax / cwd` before the add */
/* 0x20e4. Which INT 33h button walks, cycles tools and uses one: three words,
 * and MOUSE BUTTONS indexes them by its items' params, which is what makes them
 * one array rather than three variables. Each holds 0, 1 or 2 - LEFT, RIGHT,
 * MIDDLE - and the cycle steps it modulo three. */
int16_t    button_map[3] = { 1, 2, 0 };   /* 0x20e4, 0x20e6, 0x20e8 - and the
                                  * image initialises them, so out of the box
                                  * WALK is the right button, TOOL CYCLE the
                                  * middle one and USE TOOL the left. All three
                                  * zero is the one state DONE! refuses */
int16_t    button_a_down;        /* 0x18df */
int16_t    button_b_down;        /* 0x18e7 */
int16_t    g_18e5;               /* 0x18e5 - any button; escapes the fades */
int16_t    last_key;             /* 0x18f6 - a word holding the ASCII of the
                                  * last key; often read as just the low byte */

/* the egg files and the indexes built from them */
egg_file_t far *egg_files;       /* 0x20a9 - stride 0x17 */
int16_t         egg_file_count;  /* 0x20ad */
episode_t  far *episode_index;   /* 0x20ba - four 14-byte records */
int16_t         episode_count;   /* 0x20c2 */
/* The readme sections and the demos, indexed the same way: 14-byte records
 * whose first field is a far pointer to the name, which is all menu_add_list
 * reads out of any of the three. */
episode_t  far *readme_index;    /* 0x20be */
int16_t         readme_count;    /* 0x20c4 */
episode_t  far *demo_index;      /* 0x20ca */
uint8_t         current_egg;     /* 0x20b8 - which egg the episodes come from */
uint8_t         g_210b;          /* 0x210b - the chosen egg's format version */

/* progress and the shareware gate */
int16_t    level_attempted;      /* 0x2032 - the level about to be played. A
                                  * word; some sites read only its low byte */
int16_t    episode_egg_index;    /* 0x0094 - which egg the episode is in */
uint8_t    shareware_limit = 0x14;  /* 0x054a - per egg, not a constant; read as
                                  * a byte with the high half zeroed. The 0x14 is
                                  * the image's own initialiser; case 1 replaces
                                  * it from the egg before anything compares it */
int16_t    registered;           /* 0x0548 */
char far  *owner_name;           /* 0x0542 - who the copy is registered to */
int16_t    owner_key;            /* 0x0546 */
int16_t    lives;                /* 0x2034 - decremented on a lost run */
/* 0x2055. The high-water mark of the serial below. load_settings takes the max
 * over the hall of fame's ten rows and scan_save_slots over the five save
 * files, so by the time main is past them it is the largest serial anything on
 * disk holds - and that is why it is read out of settings.dat AND out of every
 * save: the counter is shared between them. Compared with `jbe`, so unsigned. */
uint16_t   serial_high;
/* 0x2057. The hall of fame - ten rows, best first. The names are malloc'd and
 * str_copy'd, and load_settings is what fills them from settings.dat. */
score_t    score_table[10];
/* 0x2053. This game's serial, and what ties a save to the hall of fame.
 *
 * It is minted from serial_high the first time a game is saved and then KEPT
 * across every later save of the same game (0x13433 only mints when it is
 * zero), so it identifies the game rather than the file. That is what makes the
 * check work: score_set writes it into the board row when a game is finished,
 * and save_note looks for it there when a save is loaded. A match means this
 * game has been finished already, and the player gets resource 0xfa -
 *
 *     Attention!
 *     The game you're loading has already been finished, and has resulted
 *     in a score being added to the high score table.
 *
 * so it is a monotonic index across saves, settings.dat and the board together,
 * not a slot number. */
int16_t    save_serial;

/* ------------------------------------------------------- the animation tables
 *
 * load_animations fills all six of these from the 'G' block, one entry per
 * entity type. They are consecutive in DGROUP and the last of them ends exactly
 * where settings begins, which is what fixes the length at 112.
 */
int16_t far *anim_script[112];   /* 0x009a - a sprite index per step, 999 ends */
uint8_t      anim_a[111];        /* 0x025a - an object's collision reach
                                  * in x. 0x0993b tests |dx| against it,
                                  * with |dy| against a constant 3 */
uint8_t      anim_b[111];        /* 0x02c9 */
uint8_t      anim_c[111];        /* 0x0338 */
uint8_t      type_flags[111];    /* 0x03a7 - bit 2 says the type has a mirrored
                                  * script in the next slot */
int16_t      next_type[111];     /* 0x0416 - what a type becomes when its script
                                  * runs out; a type that points at itself loops */

/* the menu and the attract cycle */
int16_t    attract_choice;       /* 0x21ae - 0 demos, non-zero shows a screen */
int16_t    game_in_progress;   /* 0x2177 - a game is under way, so no idle demo */
/* The rest of the flags that block declared. [0x509] and [0x50b] left it when
 * they turned out to be cheat_state[2] and [3]. */
int16_t    g_1ffa, g_1ffc, g_1ffe, g_18e1, g_18e3;
uint8_t    g_2038;               /* 0x2038 - how many demos to choose from;
                                  * byte, high half zeroed */

/* strings and buffers */
/* 0x21a5. Not five constants: one template, with the digit patched into offset
 * 4 before each open. Defined without its contents here, which gave it one byte
 * and made every one of those patches a write past the end of it. */
char       save_name[] = "GAME-.SG";
char far  *settings_name = "settings.dat";       /* 0x21d2 - a pointer in the
                                                  * image, to d+0x27c7 */
uint8_t    g_28ff[1];            /* 0x28ff - main's first splash source */
/* The string tables - see dos.h. Two of them are far data at 0x1894:0 and
 * 0x1894:4, which is why main loads them with an explicit segment. */
char far **menu_text;            /* 0x1894:0000 */
uint8_t    menu_text_count;      /* 0x0096 */
char far **extra_text;           /* 0x1894:0004 */
uint8_t    extra_text_count;     /* 0x0098 */
char far **cheat_text;           /* 0x0519 */
uint8_t    cheat_text_count;     /* 0x0504 */
/* 0x0505. One flag per cheat word, toggled by typing it. Ten words, twenty
 * bytes, and the array ends exactly where cheat_text begins.
 *
 * EVERY [0x505 + 2i] in the listing is an element of this and not a variable of
 * its own, and two of them had been declared as variables anyway: [0x507] as
 * `g_507` and [0x511] as `left_handed`. typed_push writes cheat_state[i], so
 * nothing ever wrote any shadow - typing the word toggled the array and the
 * code read a different word that stayed zero, which is a cheat that silently
 * does nothing.
 *
 * Five more had been declared as variables of their own, and none of them was
 * ever written either. All ten, and what each does:
 *
 *     [0]  0x0505  BUSHKANGAROO      '#' finishes the level outright
 *     [1]  0x0507  THECROWDSAYBO     the level picker, and the level-select key
 *     [2]  0x0509  NOSCHOOLCUSTARD   ducks do not die; cleared for a demo
 *     [3]  0x050b  ONLYFOREVER       a lost attempt costs no life
 *     [4]  0x050d  KEYCODE
 *     [5]  0x050f  COLOURMAP         P pauses
 *     [6]  0x0511  NODNOL            LEFT HANDED: which side the tool is drawn
 *     [7]  0x0513  INGLESHFELDOR
 *     [8]  0x0515  PLAYBACKTIME      the demo picker
 *     [9]  0x0517  YOUINTSEENME
 *
 * The offset is on the declaration so test_dgroup can see the array; without it
 * every one of these overlaps was invisible to the one test that exists to
 * catch exactly this, which is why there were seven of them. */
int16_t    cheat_state[10];      /* 0x0505 */
/* 0x179d, 0x179e. The cursor's animation: a two-frame divider and a phase that
 * runs 0..3, both stepped by palette_fade_step's tail, and the phase is added to
 * the sprite index the two cursor types compute. */
uint8_t    cursor_divider;       /* 0x179d */
uint8_t    cursor_phase;         /* 0x179e */
table_t    sprite_table;         /* 0x18e9 - the set, not a pointer to it: every
                                  * draw_sprite call pushes ds and this offset */
char far **tool_names;           /* 0x2106 */
uint8_t    tool_names_count;     /* 0x210a */

/* startup and settings */
int16_t    sound_available;      /* 0x2104 - detect_hardware's result */
/* 0x04f4. The word array save_settings writes and every menu toggle indexes by
 * its item's param. The image initialises it to these five values, and the
 * sixth word is video_mode - adjacent in DGROUP and reached as [0x4fe], never
 * as settings[5], which is why it is a variable of its own in dos_io.c. */
int16_t    settings[5] = { 1, 1, 0, 1, 1 };
                                 /* [0] SOUNDS        [1] FLYING BLOOD
                                  * [2] unused        [3] SMOOTH SCROLL
                                  * [4] MENU BOUNCE - read by run_screen */

/* ------------------------------------------------------------ the menu state
 *
 * All of it DGROUP, all of it written only by run_screen and its helpers.
 */
menu_t far *current_menu;        /* 0x1900 - the far pointer every helper reads
                                  * rather than taking the menu as an argument */
int16_t    menu_top;             /* 0x18fe - the y the first item is drawn at */
int16_t    colour_cycle;         /* 0x1914 - 0..15, stepped once a frame */
desc_t     backdrop;             /* 0x16f5 - screen-sized, the items are drawn
                                  * into it once and composed every frame */
desc_t     background;           /* 0x170b - the 64x64 tile behind them */
table_t    menu_sprites;         /* 0x18f8 - 'S' block 1, the large font */
int16_t    menu_always = 1;      /* 0x217f */
int16_t    menu_never;           /* 0x2181 */
uint8_t    on_off_width;         /* 0x0097 - max strlen of "N" and "FF" */
uint8_t    cycle_width;          /* 0x0099 - of LEFT / RIGHT / MIDDLE */

/* 0x1904. Sixteen bytes, and they are a letter spacing rather than a colour:
 * draw_banner's last argument. The selected item's spacing walks this table one
 * step a frame, so its letters breathe in and out - which is what the MENU
 * BOUNCE setting turns off, and why turning it off pins the spacing at 17. */
uint8_t    bounce_table[16] = { 16, 16, 17, 19, 23, 27, 29, 30,
                                30, 30, 29, 27, 23, 19, 17, 16 };

/* The compositor's, defined by the video layer and set here: the wrap masks are
 * one less than the background tile's size, and the scroll is what a menu starts
 * at zero and then leaves to palette_fade_step's tail to advance. */
extern int16_t wrap_x, wrap_y;          /* 0x1729, 0x172b */
extern uint8_t bg_scroll_x, bg_scroll_y;/* 0x177e, 0x177f */
extern uint8_t bg_step_x, bg_step_y;    /* 0x1780, 0x1781 */

/* used but not identified */
int16_t  g_2016;                 /* 0x2016 - the attempt is over; see 0x07955 */
/* 0x0da1 is scenes[5].count: whether the level has any mirrored entities,
 * which is what the setup's two ending flags and one of the four endings
 * actually test. */
/* 0x1fd3. The scale sound_load multiplies every sample byte by, out of 32, and
 * the only thing the ambience is ever loaded with - so this is AMBIENCE VOLUME.
 * That is what it is used as and where it is kept, beside game_speed and gamma
 * in the three bytes settings.dat carries, and the slider at 0x0c4f0 writes it,
 * indexed by the item's param - which the AUDIO SETTINGS entry gives as 0. The
 * image starts it at 12. */
uint8_t  ambience_volume = 12;
int16_t  slider_x;              /* 0x176d - where a slider's trough starts; 0 */
int16_t  bar_type_off = 4;      /* 0x2179 - the entity type of a dark block */
int16_t  bar_type_on  = 18;     /* 0x217b - and of a lit one */
int16_t  next_life;             /* 0x201c - the score the next extra
                                 * life is due at. Compared with jle,
                                 * so signed */
int16_t  g_21a3;

/* ---------------------------------------------------- the second, larger font
 *
 * The banners - PRESENTS, UNREGISTERED, EPISODE COMPLETED! - are not drawn with
 * the glyph font. They come from a sprite set, 47 sprites in the egg's 'S' block
 * 1, and a character reaches its sprite through charmap rather than by its own
 * code: charmap is built from a string that lists the characters in sprite order.
 *
 * A sprite's bytes are not colours either. The low nibble is a priority and the
 * drawer keeps whichever pixel has the greater one, which is what lets these
 * letters be kerned so tightly that their boxes overlap by a quarter of their
 * width without one letter's outline eating into the previous letter's face.
 */

uint8_t charmap[256];            /* 0x17c1 - character -> sprite index */

/* d+0x21f8. The order the sprites are in. 27 is '?', which is what an unlisted
 * character maps to, and there is no '0' in it - see below. */
static const char charset[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ/?:-_ !'123,987465`.x";

/* 0x04ca0. Every sound the game asks for goes through here, and the SOUNDS
 * setting is the only thing it does: with it off nothing is even loaded. */
void far sound_play_guarded(int16_t id, int16_t voice)
{
    if (settings[0])                               /* [0x4f4] */
        sound_play(id, voice);                     /* far 0x1462:0x130 */
}

/* 0x04cba */
void far build_charmap(void)
{
    int16_t i;

    for (i = 0; i < 0x100; i++)
        charmap[i] = 0x1b;                       /* everything is '?' */
    for (i = 0; charset[i]; i++)
        charmap[(uint8_t) charset[i]] = (uint8_t) i;
    charmap['0'] = charmap['O'];                 /* 0x04cfc: a zero is an O */
}

/* The five paths EGGS.INI can name.
 *
 * In the original this array is reached through a far pointer at d+0x21cc, and
 * that pointer is NEVER WRITTEN - it is zero in the image and zero in every
 * snapshot, so the five slots live at 0000:0000, in the interrupt vector table.
 * Watched from cold: with no EGGS.INI on disk the fallback stores its string
 * through `str_copy("EGGS\\MAIN.EGG", 0000:0000)` and open_egg reads the same
 * four bytes straight back, so it works by landing on INT 0's vector and taking
 * it away again before anything divides by zero.
 *
 * A null dereference is not something to be faithful to, so here it is an array.
 */
char far  *egg_ini_paths[5];
int16_t    egg_ini_count;        /* 0x21d0 */

/* ==================================================== 0x13ccd: load_eggs_ini
 *
 * EGGS.INI, read a line at a time, and only the lines under `[EGGS]` are taken.
 * A line beginning with `[` is a section header and switches the section on or
 * off; anything else while the section is on is an egg to open, in order.
 *
 * Two details do the work. Forward slashes are turned into backslashes as the
 * line is read (0x13d36), so an INI written on either kind of machine names the
 * same file. And end of line is a newline OR the end of the file, which is what
 * lets the last line count without a trailing newline.
 *
 * There is no EGGS.INI in this copy of the game, so what actually runs is
 * init's fallback below - but the file is the supported way to add episodes and
 * PickEggs.exe writes it, so this is not dead.
 */
void far load_eggs_ini(const char far *path)
{
    char    line[0x104];                       /* [bp-0x10a] */
    FILE   *fp;
    int16_t in_eggs = 0;                       /* [bp-6] */
    int16_t reading, i;                        /* di, si */
    int     c;

    fp = fopen(path, "rt");                    /* 0x13ce5, d+0x2789 */
    if (!fp)
        return;

    while (!feof(fp)) {                        /* 0x13ddb */
        reading = 1;
        i = 0;
        while (reading) {                      /* 0x13d55 */
            if (i == 0xff)                     /* 0x13d08 */
                fatal("Line too long in INI file", 0);
            c = fgetc(fp);
            line[i] = (char) c;
            if (line[i] == '/')                /* 0x13d2f */
                line[i] = '\\';
            if (line[i] == '\n' || feof(fp) || c == EOF) {
                reading = 0;                   /* 0x13d4d */
                line[i] = 0;
            }
            i++;
        }

        if (line[0] == '[') {                  /* 0x13d59 - a section header */
            in_eggs = (strcasecmp("[EGGS]", line) == 0);   /* 0x13d6a */
            continue;
        }
        if (!in_eggs || line[0] == 0)          /* 0x13d7c, 0x13d8f */
            continue;
        if (egg_ini_count >= 5)                /* 0x13d96 */
            fatal("Can't load that many eggs", 0);
        else {
            str_copy(line, &egg_ini_paths[egg_ini_count]);   /* 0x13db3 */
            egg_ini_count++;
        }
    }
    fclose(fp);
}

/* ------------------------------------------------ 0x051b7: close_egg_files */
void far close_egg_files(void)
{
    while (egg_file_count--) {             /* walked backwards, stride 0x17 */
        fclose(egg_files[egg_file_count].fp);
        free(egg_files[egg_file_count].id);         /* the name at +8, which
                                                     * build_episode_index read
                                                     * out of the egg */
    }
}

/* --------------------------------------------- 0x058b9: resource_load_full
 *
 * Pull one resource out of an egg and build a descriptor for it. Everything that
 * draws a screen comes through here.
 *
 *   desc        the descriptor to fill
 *   allocate    when non-zero, write the source's size into the descriptor and
 *               allocate its rows; when zero, draw into one that already exists
 *   type        the resource type - 0x4d for the screens main and game_main show
 *   index       which one
 *   pal_at      where in the palette buffer this resource's colours go, and the
 *               same number is added to every pixel, so a resource loaded at
 *               entry 112 draws in the sixteen colours from 112 up
 *   bias_zero   whether a pixel that ends up zero is biased by pal_at as well
 *   row         the first destination row
 *   opaque      when zero, a zero source pixel leaves the destination alone
 *   egg         which egg file to look in
 *
 * The stream is the open egg at egg_stream ([0x20c6]): egg_find_block seeks it to
 * the resource, then the header is two words and a byte - width, height, and how
 * many palette entries follow - and the palette is read three bytes at a time
 * straight into the current buffer at pal_at * 3.
 *
 * Ten arguments, which is why both of the forwarders below exist and why nothing
 * calls this directly.
 */
int16_t far resource_load_full(desc_t far *desc, int16_t allocate,
                               uint8_t type, uint8_t index, int16_t pal_at,
                               int16_t bias_zero, int16_t row, int16_t opaque,
                               int16_t egg, int16_t arg1a)
{
    int16_t w, h, colours, i, x0, ok = 1;          /* [bp-0xe] starts at 1 */

    if (!egg_find_block(type, index, egg))         /* 0x05232 */
        return 0;

    w       = egg_read_word(egg_stream);           /* 0x04e88 */
    h       = egg_read_word(egg_stream);
    colours = egg_read_byte(egg_stream);           /* 0x03791 */

    if (allocate) {
        desc->w = w;                               /* +0x0c */
        desc->h = h;                               /* +0x0e */
        ok = alloc_image(desc, 0, 0, 0, arg1a);    /* 0x05388 */
    }
    if (!ok)                                       /* 0x05954 - and when the
                                                    * caller supplied the image,
                                                    * this cannot fail */
        return 0;

    /* Where this image sits inside the descriptor's own width, so a source
     * narrower than the destination lands centred rather than at the left. */
    x0 = (desc->w - w) >> 1;

    for (i = 0; i < colours * 3; i++)              /* three bytes an entry */
        ((uint8_t far *) current_buffer)[pal_at * 3 + i] =
            egg_read_byte(egg_stream);

    f_0580b();                                     /* reset the chunk decoder */

    /* The rows. One pixel at a time out of the decoder, placed at x0 so a source
     * narrower than the destination lands centred, and at `row` down, so a logo
     * can be dropped onto a backdrop that is already drawn. */
    for (i = 0; i < h; i++) {
        int16_t x;

        for (x = 0; x < w; x++) {
            uint8_t px = egg_next_pixel();

            if (px == 0 && !opaque)                /* 0x059b6 - transparent */
                continue;
            desc->rows[i + row][x0 + x] = px;      /* 0x059bf */
            if (desc->rows[i + row][x0 + x] == 0 && !bias_zero)  /* 0x059ff */
                continue;
            desc->rows[i + row][x0 + x] =          /* 0x05a22 - a byte add */
                (uint8_t) (desc->rows[i + row][x0 + x] + pal_at);
        }
    }
    egg_block_end();                               /* 0x05a55 - releases the
                                                    * lock the find took */
    return 1;
}

/* --------------------------------------------- 0x05a67: resource_load
 *
 * The form that loads a whole screen: allocate the image, start at row 0, and
 * write every pixel including the zeros. A thin forwarder, twenty bytes of
 * pushes - the caller's fifth argument is the one that decides whether a pixel
 * that came out zero is biased by pal_at with all the others.
 */
int16_t far resource_load(desc_t far *desc, uint8_t type, uint8_t index,
                          int16_t pal_at, int16_t bias_zero,
                          int16_t egg, int16_t arg1a)
{
    return resource_load_full(desc, 1, type, index, pal_at,
                              bias_zero, 0, 1, egg, arg1a);
}

/* 0x05a95. The other forwarder: nothing is allocated, so the resource is drawn
 * into a descriptor that already exists, starting at the row given, and a zero
 * source pixel leaves what is underneath alone.
 *
 * run_screen uses it twice, for the two pieces of furniture around a menu: the
 * DUCKS logo along the bottom, and the big one at the top of a short menu. Each
 * brings sixteen colours of its own, which is what pal_at is for. */
int16_t far resource_load_at(desc_t far *desc, uint8_t type, uint8_t index,
                             int16_t pal_at, int16_t row, int16_t egg)
{
    /* The result was thrown away here and cutscene_doorstep branches on it -
     * every one of its three pictures is optional. */
    return resource_load_full(desc, 0, type, index, pal_at, 0, row, 0, egg, 1);
}

/* 0x056f7. Forces one entry of the current palette buffer to black.
 *
 * show_splash calls it for entry 0 the moment the banner sprite set has loaded,
 * and it has to: that set's own first colour is a purple, and entry 0 is what
 * draw_banner clears its image to, so without this the purple becomes the
 * background the letters sit on and floods the screen through the fade. */
void far palette_set_black(uint8_t index)
{
    uint8_t far *p = current_buffer;
    int16_t      i = index * 3;

    p[i] = 0;
    p[i + 1] = 0;
    p[i + 2] = 0;
}

/* 0x06110. The pixels for one sprite, w*h of them. */
void far sprite_alloc(sprite_t far *s)
{
    s->pixels = malloc((size_t) s->w * s->h);
    if (!s->pixels)
        fatal(out_of_memory, NULL);
}

/* --------------------------------------------- 0x0615a: sprite_set_load
 *
 * A whole set: a big-endian count, then each sprite as width, height, origin x,
 * origin y and its pixels, then a palette slice - a count, a first index, and
 * three bytes each - written into the current buffer.
 *
 * The two nested loops in the original count to width*height while the
 * destination pointer runs straight through the buffer, so their nesting says
 * nothing about the layout; only the drawer does, and it walks rows.
 *
 * It looks in the shared egg first and only then in the one the caller names.
 */
void far sprite_set_load(uint8_t index, uint8_t type, table_t far *table,
                         int16_t egg)
{
    int16_t i, n, first;

    if (!egg_find_block(type, index, 0xff) && !egg_find_block(type, index, egg))
        fatal("Sprite section missing", NULL);    /* ds:0x22d4 */

    table->count = egg_read_word(egg_stream);
    table->base  = malloc((size_t) table->count * sizeof(sprite_t));
    if (!table->base)
        fatal(out_of_memory, NULL);

    for (i = 0; i < table->count; i++) {
        sprite_t far *s = &table->base[i];
        int32_t       k, pixels;

        s->w = egg_read_byte(egg_stream);
        s->h = egg_read_byte(egg_stream);
        sprite_alloc(s);
        s->ox = egg_read_byte(egg_stream);
        s->oy = egg_read_byte(egg_stream);

        pixels = (int32_t) s->w * s->h;
        for (k = 0; k < pixels; k++)
            s->pixels[k] = egg_read_byte(egg_stream);
    }

    n     = egg_read_byte(egg_stream);
    first = egg_read_byte(egg_stream) * 3;
    for (i = 0; i < n * 3; i++)
        ((uint8_t far *) current_buffer)[first + i] = egg_read_byte(egg_stream);
    egg_block_end();
}

/* 0x06ee9. Makes room for a scene's entities: capacity records of 0x29 bytes.
 * Everything else in the header is cleared, except the byte at +4, which comes
 * out 0xff. */
void far scene_alloc(scene_t far *s, int16_t capacity)
{
    s->capacity = capacity;
    s->count    = 0;
    s->keep_order  = 0;
    s->entities = malloc((size_t) capacity * sizeof *s->entities);
                                               /* 0x29 in the original */
    s->flag     = 0xff;
    if (!s->entities)
        fatal(out_of_memory, 0);
}

/* 0x06dbc. Puts one entity into a scene, or refuses if it is full. Every field
 * is zeroed by name rather than by a block clear, which is what says which
 * fields the record actually has. */
int16_t far scene_add(scene_t far *s, int16_t x, int16_t y, int16_t type,
                      int16_t param)
{
    entity_t far *e;

    if (s->capacity <= s->count)
        return 0;

    e = &s->entities[s->count];
    e->x = x;  e->y = y;                       /* both 32-bit, high half zeroed */
    e->f14 = 0;  e->f15 = 0;  e->f16 = 0;
    e->frame = 0;
    e->f21 = 0;  e->f23 = 0;  e->f27 = 0;
    e->type  = type;
    e->param = param;
    s->count++;
    return 1;
}

/* ------------------------------------------------------ 0x06869: input_poll
 *
 * Takes the resolution because the game keeps the cursor position itself: INT 33h
 * is only ever asked for relative motion, so the position is a running total and
 * has to be bounded. Kept as 32-bit so a fast drag cannot wrap it.
 */
void far input_poll(int16_t w, int16_t h)
{
    counts_t p, r;

    mouse_motion(&mouse_dx, &mouse_dy);    /* 0x0675b - the only two arguments */

    /* The button state, as two struct-valued initialisers. Each compiles to
     * three calls storing into a DGROUP temporary - 0x20ea and 0x20f0 - followed
     * by the runtime's block-copy helper at 0x00ff4 moving six bytes to the
     * local: far pointers on the stack, length in CX, `shr cx,1 / rep movsw /
     * adc cx,cx / rep movsb`.
     *
     * Those two temporaries are written once each and read by nothing but that
     * copy, which is what identifies them as compiler scratch rather than
     * globals. An earlier draft of this file declared them as program variables
     * and then wondered why the code kept both them and the locals.
     *
     * The indexes are data: which physical button means what comes out of
     * button_map_a/b/c, which is what the MOUSE BUTTONS screen sets. */
    p = (counts_t) { mouse_presses(0),  mouse_presses(1),  mouse_presses(2)  };
    r = (counts_t) { mouse_releases(0), mouse_releases(1), mouse_releases(2) };

    if (p.n[button_map[0]]) button_a_down = 1;   /* 0x18df */
    if (r.n[button_map[0]]) button_a_down = 0;
    g_18e3 = p.n[button_map[1]];            /* 0x18e3 - unnamed */
    g_18e1 = p.n[button_map[2]];            /* 0x18e1 - unnamed */
    g_18e5 = (p.n[0] || p.n[1] || p.n[2]); /* 0x18e5 - any button at all, which
                                            * is what the fades test to cut a
                                            * splash short */
    if (g_18e5)                              button_b_down = 1;   /* 0x18e7 */
    if (r.n[0] || r.n[1] || r.n[2])          button_b_down = 0;

    /* The position, accumulated from the deltas and clamped. 32-bit, and the
     * comparisons are signed: high word with jg/jl, low word with ja/jae. */
    mouse_x += (int32_t) mouse_dx;         /* mov ax / cwd / add / adc */
    mouse_y += (int32_t) mouse_dy;
    if (mouse_x > (int32_t) w - 1)  mouse_x = w - 1;
    if (mouse_x < 0)                mouse_x = 0;
    if (mouse_y > (int32_t) h - 1)  mouse_y = h - 1;
    if (mouse_y < 0)                mouse_y = 0;

    /* 0x06a0d. The keyboard, and the last thing input_poll does. last_key is
     * assigned on every call, including to zero when nothing is waiting - which
     * is the whole of what run_screen's opening `while (last_key)` spins on, and
     * why nothing anywhere else ever has to clear it.
     *
     * A key with no ASCII arrives as a zero followed by its scan code, and the
     * scan code is what gets the 0x100 - so the up arrow reaches the menu as
     * 0x148 rather than as 0x48, which would be 'H'. */
    if (key_pending()) {                   /* 0:0x29fc - kbhit */
        last_key = key_read();             /* 0:0x2814 - getch */
        if (last_key == 0x3d)              /* 0x06a1e: '=' counts as '+' */
            last_key = 0x2b;
        if (last_key == 0)
            last_key = key_read() + 0x100;
    } else {
        last_key = 0;
    }
}

/* 0x06a49. Fills a descriptor with one value, a row at a time through the
 * runtime's memset. The height is compared unsigned, the width is the count. */
void far image_clear(desc_t far *desc, uint8_t value)
{
    int16_t i;

    for (i = 0; i < desc->h; i++)                  /* +0x0e, `ja` */
        memset(desc->rows[i], value, (size_t) desc->w);
}

/* -------------------------------------------------- 0x0713e: sprite_to_image
 *
 * One sprite into a descriptor, clipped against it. The pixel is kept only if
 * its low nibble is at least the low nibble of what is already there, and the
 * caller's colour is added to what gets stored - a bank in the high nibble,
 * shifted up by the banner code before it calls in.
 *
 * The clipping is done by adjusting where the source starts and how much to
 * skip at the end of each row, not by testing every pixel.
 */
void far sprite_to_image(int16_t x, int16_t y, sprite_t far *s,
                         desc_t far *desc, uint8_t colour)
{
    int32_t at   = 0;                /* di - where we are in the sprite */
    int16_t skip = 0;                /* [bp-4] - dropped at each row's end */
    int16_t x1, y1, row, col;

    x -= s->ox;
    y -= s->oy;
    x1 = x + s->w;
    y1 = y + s->h;

    if (x < 0) {                     /* off the left: start further in */
        skip -= x;
        at   -= x;
        x     = 0;
    } else if (desc->w < x1) {       /* off the right: stop short */
        skip += x1 - desc->w;
        x1    = desc->w;
    }
    if (y < 0) {                     /* off the top: skip whole rows */
        at -= (int32_t) y * s->w;
        y   = 0;
    } else if (desc->h < y1) {
        y1 = desc->h;
    }

    for (row = y; row < y1; row++) {
        for (col = x; col < x1; col++) {
            uint8_t c = s->pixels[at++];

            if (c && (c & 0x0f) >= (desc->rows[row][col] & 0x0f))
                desc->rows[row][col] = c + colour;
        }
        at += skip;
    }
}

/* 0x078d4. Gives an entity a type, and only restarts its animation if the type
 * actually changed - which is why calling it every frame, as run_screen does for
 * the mouse pointer, does not freeze the pointer on its first frame.
 *
 * draw_entities calls it with type 0, which is what retiring an entity is. */
void far entity_set_type(entity_t far *e, int16_t type)
{
    if (e->type != type) {                         /* +0x25, a word */
        e->type  = type;
        e->frame = 0;                              /* +0x1f */
    }
}

/* 0x07259. The same clipping again, and the same walk, but what it writes is a
 * halo: colour 0 above, below, left and right of every non-zero source pixel,
 * and nothing where the pixel itself is. outline_sprite does this through the
 * plot pointer at the screen; this does it into an image.
 *
 * The level intro draws its tools with this and then sprite_to_image_plain over
 * the top, which is the same pairing the HUD uses for the collected items.
 *
 * The neighbours are not clipped - a pixel on the first row of the destination
 * writes to rows[-1]. The original does that too; nothing draws close enough to
 * an edge for it to show, and the tools sit in the middle of the screen.
 */
void far outline_to_image(int16_t x, int16_t y, sprite_t far *s,
                          desc_t far *desc)
{
    int32_t at = 0;
    int16_t skip = 0;
    int16_t x1, y1, row, col;

    x -= s->ox;
    y -= s->oy;
    x1 = x + s->w;
    y1 = y + s->h;

    if (x < 0) {
        skip -= x;
        at   -= x;
        x     = 0;
    } else if (desc->w < x1) {
        skip += x1 - desc->w;
        x1    = desc->w;
    }
    if (y < 0) {
        at -= (int32_t) y * s->w;
        y   = 0;
    } else if (desc->h < y1) {
        y1 = desc->h;
    }

    for (row = y; row < y1; row++) {
        for (col = x; col < x1; col++)
            if (s->pixels[at++]) {
                desc->rows[row - 1][col] = 0;       /* 0x0733b */
                desc->rows[row + 1][col] = 0;
                desc->rows[row][col - 1] = 0;
                desc->rows[row][col + 1] = 0;
            }
        at += skip;
    }
}

/* 0x0739c. sprite_to_image without the colour or the priority nibble: the same
 * clipping, and a pixel that is not zero simply replaces what is there. The
 * slider's trough is drawn with this. */
void far sprite_to_image_plain(int16_t x, int16_t y, sprite_t far *s,
                               desc_t far *desc)
{
    int32_t at = 0;
    int16_t skip = 0;
    int16_t x1, y1, row, col;

    x -= s->ox;
    y -= s->oy;
    x1 = x + s->w;
    y1 = y + s->h;

    if (x < 0) {
        skip -= x;
        at   -= x;
        x     = 0;
    } else if (desc->w < x1) {
        skip += x1 - desc->w;
        x1    = desc->w;
    }
    if (y < 0) {
        at -= (int32_t) y * s->w;
        y   = 0;
    } else if (desc->h < y1) {
        y1 = desc->h;
    }

    for (row = y; row < y1; row++) {
        for (col = x; col < x1; col++) {
            uint8_t c = s->pixels[at++];

            if (c)
                desc->rows[row][col] = c;
        }
        at += skip;
    }
}

/* ------------------------------------------------------- 0x0881d: make_rect
 *
 * Fill a viewport from its four edges. The width and height are derived rather
 * than passed, and the scroll pair is zeroed - so a caller that wants a scrolling
 * viewport builds it here and sets the scroll afterwards.
 *
 * Kept in game.c rather than dos_io.c: it is arithmetic on a struct and touches
 * no hardware. A port recompiles it unchanged.
 */
void far make_rect(viewport_t far *r, int16_t top, int16_t bottom,
                   int16_t left, int16_t right)
{
    r->top      = top;
    r->bottom   = bottom;
    r->left     = left;
    r->right    = right;
    r->scroll_x = 0;
    r->scroll_y = 0;
    r->width    = right - left;      /* 0x0886b: cx - dx */
    r->height   = bottom - top;      /* 0x08876: di - si */
}

/* 0x04dcd. Hold what is on screen for n frames, by flipping and doing nothing
 * else. Every cutscene ends with one of these, so stubbing it makes the whole
 * ending sequence flash past. */
void far hold_frames(int16_t n)
{
    int16_t i;

    for (i = 0; i < n; i++)
        page_flip();
}

/* 0x08885. Sets a descriptor's size and allocates its rows. */
void far image_alloc(desc_t far *desc, int16_t w, int16_t h)
{
    desc->w = w;
    desc->h = h;
    alloc_image(desc, 0, 0, 0, 1);                 /* 0x05388 */
}

/* 0x088b3, and 0x0638f: the SAME routine, emitted twice.

 * Instruction for instruction identical - free each sprite's pixels, then the
 * array - and nothing calls the second copy. Borland put it in two modules
 * because both included the header that defined it, and the linker kept both.
 * Recorded here rather than transcribed again: a second copy of a function is
 * not a second function, and counting it as unwritten was the coverage report
 * asking for something that would be wrong to write.
 */
void far sprite_set_free(table_t far *table)
{
    int16_t i;

    for (i = 0; i < table->count; i++)
        free(table->base[i].pixels);
    free(table->base);
}

/* 0x0876a. The washed palette the blink alternates with: three quarters of the
 * background's own sixteen colours, lifted by 64 so the darkest of them is still
 * visible, and then through the same gamma scaling palette_build uses.
 *
 * It reads d+0x14b1 rather than the current buffer - that is default_buffer at
 * entry 64 * 3, which is exactly where load_background puts the tile's colours.
 */
void far build_washed_ramp(void)
{
    int16_t i;

    for (i = 0; i < 0x30; i++) {
        int32_t v = (default_buffer[0xc0 + i] >> 1)
                  + (default_buffer[0xc0 + i] >> 2) + 0x40;

        v = v * (gamma_level + 6) / 0x13;
        palette_washed[i] = (uint8_t) (v > 255 ? 255 : v);
    }
}

/* 0x087bc. The tile behind a menu: a 'B' resource, its sixteen colours loaded at
 * palette entry 64 and its pixels biased to match. The menu descriptor's last
 * byte says which one.
 *
 * Asked for it in the egg the caller named and then in egg 0, and if neither has
 * it the game stops - this is the one resource nothing can carry on without.
 * The wrap masks are the tile's size less one, so compose_layer can repeat it
 * with an AND; that only works because every one of these is a power of two.
 */
void far load_background(uint8_t index, int16_t egg)
{
    if (!resource_load(&background, 0x42, index, 0x40, 1, egg, 1)
        && !resource_load(&background, 0x42, index, 0x40, 1, 0, 1))
        fatal("Can't load background image", 0);   /* d+0x22fa */

    wrap_x = background.w - 1;                     /* 0x1729 */
    wrap_y = background.h - 1;                     /* 0x172b */
    build_washed_ramp();
}

/* -------------------------------------------------- 0x093fb: load_string_table
 *
 * One table: an 'H' block of strings read into a malloc'd array of far pointers,
 * with the count written back through the caller's byte. Same block type as the
 * readme pages, and the same reader, so the +1 shift comes off on the way in.
 *
 * The strings are the game's whole user-visible vocabulary - every menu item,
 * every banner, every message. Nothing in the executable holds these words.
 */
void far load_string_table(uint8_t index, char far ***table, uint8_t far *count,
                           const char far *missing, uint8_t egg)
{
    int16_t i;

    if (!egg_find_block(0x48, index, egg))
        fatal(missing, NULL);

    *count = egg_read_byte(egg_stream);
    *table = malloc((size_t) *count * sizeof **table);
                                                   /* four bytes an entry in the
                                                    * original, because that is
                                                    * what a far pointer was.
                                                    * Writing 4 here overruns the
                                                    * array on anything with
                                                    * wider pointers and frees
                                                    * the strings allocated right
                                                    * after it - which showed up
                                                    * as the first ten menu items
                                                    * being heap rubble */
    if (!*table)
        fatal(out_of_memory, NULL);

    for (i = 0; i < *count; i++)
        (*table)[i] = egg_read_string(egg_stream);
    egg_block_end();
}

/* 0x094b7. The four tables, then three assertions on their lengths - which is
 * how the program tells an egg built for another version of itself: a block that
 * is present but the wrong length is caught here rather than at the point some
 * screen indexes off the end of it. */
void far load_string_tables(void)
{
    /* 0x094cd. The tool names, and they go through the same loader as the rest -
     * an earlier note here said otherwise. Block 0xff, no length assertion, and
     * message_post indexes it by anim_c[tool_type], so a level with tools reads
     * off a null pointer without it. */
    load_string_table(0xff, &tool_names, &tool_names_count,
                      "Can't find tool names", 0xff);
    load_string_table(0xfd, &menu_text,  &menu_text_count,
                      "No menu text", 0xff);
    load_string_table(0xfb, &extra_text, &extra_text_count,
                      "No v1.2 extended text", 0xff);
    load_string_table(0xfe, &cheat_text, &cheat_text_count,
                      "No cheats section", 0);

    if (menu_text_count != 83)
        fatal("Incorrect number of lines in menu text slice", NULL);
    if (extra_text_count != 15)
        fatal("Incorrect number of lines in extended text slice", NULL);
    if (cheat_text_count != 10)
        fatal("Incorrect number of cheats", NULL);
}

/* ------------------------------------------------------ 0x0ab09: particles
 *
 * Walks an array of 16-byte records and plots each through the pointer at
 * [0x53e] - plot_pixel, or its 360-wide twin. Called once per plane, so it offers
 * every particle four times and the plotter keeps the quarter whose x & 3 matches.
 *
 * That is why plot_pixel saw 627,260 calls a session for ~157,000 written bytes,
 * and why replacing plot_pixel was the wrong level to work at: there is no work to
 * batch inside it. Replacing this loop turns ~205 emulated iterations per plane
 * into one pass.
 */
void far particles(void)
{
    int16_t i;

    for (i = 0; i < particle_count; i++) {         /* [0x18cd] */
        particle_t far *p = &particle_array[i];    /* [0x18c1], 16-byte records */
        /* 0x0ab2f, 0x0ab5f. Level coordinates to screen ones, and the whole
         * point of the routine: the pool is in level space and the view is not,
         * so a particle is drawn scroll away from where it lives. Plotting the
         * raw position put every burst where the ducks would have been had the
         * view been at the origin - which on a level whose flock is off the top
         * of the screen is a long way from the ducks.
         *
         * All 16-bit and all unsigned, both here and in the native this is
         * transcribed from: the shift helper at 0:0x1148 is SHR, not SAR, so the
         * fixed point is unsigned, and the wrap has to happen before the compare
         * because that is what makes one bound reject both sides - an x left of
         * the view wraps to a huge value and fails the upper test. */
        uint16_t x = (uint16_t) (((uint32_t) p->x >> 3)
                                 - (uint16_t) viewport_game.scroll_x
                                 + (uint16_t) viewport_game.left);
        uint16_t y = (uint16_t) (((uint32_t) p->y >> 3)
                                 - (uint16_t) viewport_game.scroll_y
                                 + (uint16_t) viewport_game.top);

        if (x < (uint16_t) viewport_game.left           /* 0x0ab39, 0x0ab3f */
            || x >= (uint16_t) viewport_game.right)
            continue;
        if (y < (uint16_t) viewport_game.top            /* 0x0ab6d, 0x0ab76 */
            || y >= (uint16_t) viewport_game.bottom)
            continue;
        plot((int16_t) x, (int16_t) y, p->colour);      /* 0x0ab90 */
    }
}

/* 0x0e70a's twin, defined with the collision pass below. */
static uint8_t terrain_at(int32_t y, int32_t x);

/* 0x0a85f. Retire particle `i`: the last one is copied over it and the count
 * comes down, so the pool has no holes and the draw can be a flat walk. Field
 * by field in the original - 0, 2, 6, 8, 0xa, 0xc, 0xe - which is a struct copy
 * either way. */
static void far particle_retire(int16_t i)
{
    particle_array[i] = particle_array[--particle_count];   /* 0x0a867 */
}

/* ---------------------------------------------------- 0x0a956: the particles
 *
 * One frame of every particle, and what makes a death an explosion rather than
 * a cluster of dots: the spawner gives each one an upward velocity and this
 * adds gravity, carries it, and retires it when it leaves or has bounced enough.
 *
 * Both coordinates are 32-bit and both bounds tests are UNSIGNED (`jb`/`ja` at
 * 0x0a9ec and 0x0aa16), which is how a particle that has gone off the left or
 * the top is caught: a negative coordinate is a huge unsigned one, so a single
 * compare against the far edge covers both sides. Written as uint32_t here for
 * that reason and not as a tidier pair of tests.
 *
 * Terrain is a hit, not a wall. The particle is stopped only in the sense that
 * vy is forced to 8 - straight down and fast - and its life comes down by one;
 * f0d is 1 or 2 from the spawner, so it survives one or two landings and then
 * goes. What it leaves behind is the stain at 0x0aab3: the particle's own
 * colour written into the terrain, which is FLYING BLOOD, and is why that
 * setting gates it.
 *
 * The retire at 0x0aaf3 - the f0e == 0 arm - does NOT step `i` back, where the
 * other two do. So the particle swapped into the hole is skipped for one frame.
 * That is the original's, kept.
 */
void far particles_step(void)
{
    int16_t i, px, py;                             /* si, di, [bp-2] */

    for (i = 0; i < particle_count; i++) {         /* 0x0aafc */
        particle_t far *p = &particle_array[i];

        p->x += p->vx;                             /* 0x0a988 */
        p->y += p->vy;                             /* 0x0a9b4 */
        p->vy++;                                   /* 0x0a9c7 - gravity */

        /* 0x0a9cb, 0x0a9f5. Off the level in any direction. */
        if ((uint32_t) p->x > (uint32_t) (int32_t) (int16_t) (level_w << 3)
            || (uint32_t) p->y > (uint32_t) (int32_t) (int16_t) (level_h << 3)) {
            particle_retire(i);                    /* 0x0aa22 */
            i--;
            continue;
        }

        px = (int16_t) ((uint32_t) p->x >> 3);     /* 0x0aa40, 0:0x1148 is SHR */
        py = (int16_t) ((uint32_t) p->y >> 3);     /* 0x0aa5c */
        if (!terrain_at(py, px))                   /* 0x0aa73 - still in the air */
            continue;

        if (!p->f0e) {                             /* 0x0aa87 */
            particle_retire(i);                    /* 0x0aaf5, and no i-- */
            continue;
        }

        /* 0x0aa8e. The stain. Guarded on the level's own bounds as well as the
         * setting, which the original does not need to be: a coordinate exactly
         * on the far edge indexes one past the row, and that was a farmalloc
         * header there and is a separate allocation's malloc header here. */
        if (settings[1] && px >= 0 && px < backdrop.w
            && py >= 0 && py < backdrop.h)
            backdrop.rows[py][px] = p->colour;     /* 0x0aab3 */

        p->vy = 8;                                 /* 0x0aac1 */
        if (--p->f0d == 0) {                       /* 0x0aad2, 0x0aae1 */
            particle_retire(i);                    /* 0x0aaea */
            i--;
        }
    }
}

/* 0x0a52a. Steps every entity in a scene one frame.
 *
 * Each entity's counter goes up by one, and the script for its type is indexed
 * by that counter halved - so every sprite in a script is held for two frames.
 * A script entry of 999 means the script has run out, and only then does
 * anything else happen.
 *
 * When it has, the default is to become whatever next_type says and start again
 * from zero. A type whose next_type is itself simply loops, which is what the
 * mouse pointer does: its script is 0,0,0,0,1,1,1,1 and next_type[0x14] is 0x14,
 * so it blinks between two sprites forever.
 *
 * 0x0a58e-0x0a7ed is a switch on the type before that default, and it is where
 * a good deal of the game's behaviour lives - the rocket filling up and taking
 * off, the teleporter arriving, a duck drowning. Nothing in a menu reaches any
 * of it, which is why it went unwritten while the menus were the whole port.
 *
 * `chain` is the original's `di`: set before the switch and cleared only by the
 * drowning arm, it decides whether the default runs at all. An arm that has set
 * the type itself must not have it overwritten.
 */
void far animate_scene(scene_t far *scene)
{
    int16_t i;

    for (i = 0; i < scene->count; i++) {
        entity_t far *e = &scene->entities[i];
        int16_t       chain = 1;               /* di, 0x0a58e */

        e->frame++;
        if (anim_script[e->type][e->frame >> 1] != 0x3e7)
            continue;

        switch (e->type) {
        case 0x54:                             /* 0x0a5e0 - face either way */
            e->f14 = (int8_t) ((game_rand() & 2) - 1);
            break;
        case 0x47:                             /* 0x0a602 */
            e->f14 = 1;
            break;
        case 0x46:                             /* 0x0a61a */
            e->f14 = -1;
            break;
        case 0x2f:                             /* 0x0a632 */
            scene_swap_pair();
            break;

        /* 0x0a639. A duck has just gone into the rocket: give it a nudge
         * upward, and the default then walks it on to 0x1b. */
        case 0x1a:
            e->f15 = 0xfc;                     /* -4 */
            break;

        /* 0x0a651. The teleporter's far end, which the 0x37 arm of
         * collide_scenes stashed when the duck went in. */
        case 0x20:
            e->x   = (int16_t) g_1ff2;
            e->y   = (int16_t) g_1ff4;
            e->f15 = 0;
            e->f21 = 0;
            e->f16 = 0;
            break;

        case 0x23:                             /* 0x0a6d3 */
            e->y -= 10;
            break;

        /* 0x0a6f0. Drowned. duck_dies has already set the type to 3, so the
         * default must not run - unless cheat_state[2] says ducks do not die, in which
         * case duck_dies did nothing and the type still has to move on. */
        case 0x24:
            duck_dies(e, 0, 1);
            chain = cheat_state[2];
            break;

        /* 0x0a71d. The other way a level is won. */
        case 0x4e:
            duck_count--;
            level_outcome = 1;
            g_1ffc        = 1;
            g_1ffa        = level_attempted;
            break;

        /* 0x0a736: THE ROCKET, and the whole of filling one up.
         *
         * `param` is how many ducks it still wants, and the type it wears is
         * param + 5 - so types 6 to 0x0a are "five more" down to "one more", and
         * that is what collide_scenes' 6..0x0a arm collides with. Each duck that
         * arrives there sets this entity to 0x1a and takes one off param; 0x1a
         * bounces it and next_type walks it to 0x1b; and 0x1b is here, which puts
         * the right "N more" sprite back on.
         *
         * When param reaches zero the rocket goes instead, and if it was the last
         * one on the level - scenery_count, which the loader counted - the level
         * is won: outcome 1, which the frame turns into 2 and returns.
         *
         * The type set here survives the default because next_type is the
         * identity for 5 and for 6..0x0a. */
        case 0x1b:
            entity_set_type(e, (int16_t) (e->param + 5));
            if (e->param > 0)                  /* 0x0a780 - still wants ducks */
                break;
            if (--scenery_count == 0)          /* 0x0a787 */
                level_outcome = 1;
            entity_set_type(e, 5);             /* 0x0a799 - launched */
            message_post(menu_text[44], NULL); /* "Rocket launched!" */
            score += 0x19;
            sound_play_guarded(0x0c, 1);
            break;

        default:                               /* 0x0a7ee - 0x1c..0x22 and all */
            break;
        }

        if (!chain)                            /* 0x0a7ee */
            continue;
        e->type  = next_type[e->type];
        e->frame = 0;
    }
}

/* -------------------------------------------------- 0x0aba5: draw_entities
 *
 * One level above draw_sprite: walk a scene's entity array, work out which sprite
 * each entity shows, and blit it. Called once per plane like everything else in
 * the frame loop, which is where its ~34,000 calls a session come from.
 *
 *   scene   +2 entity count, +8 far pointer to the array
 *   view    the 20-byte viewport by value; its address is what draw_sprite gets
 *           as its clip rectangle
 *   colour  offset added to every pixel drawn
 *
 * Most types take the sprite straight out of their script. The two cursors
 * compute theirs, type 4 has two fixed sprites for a non-zero facing, and two
 * types move themselves before drawing.
 */
void far draw_entities(scene_t far *scene, viewport_t view, uint8_t colour)
{
    int16_t i;
    int16_t haloed = 0;                            /* di, and it starts at 0 */

    for (i = 0; i < scene->count; i++) {
        entity_t far *e = &scene->entities[i];     /* 0x29-byte records */
        int16_t  index;                            /* [bp-2] */
        int32_t  y = e->y;                         /* [bp-8]/[bp-6] */
        /* Worked out before the switch, and only taken up after this entity has
         * been drawn: a scene usually holds one highlighted entity, and it is
         * the entity *after* it that gets the halo. */
        int16_t  halo_next = (e->type == 0x10 || e->type == 0x0f);

        switch (e->type) {
        case 0x36:                                 /* 0x0ac53 */
            index = anim_script[e->type][e->frame >> 1];
            if (e->param != 1)                     /* +0x17 */
                y += 8;
            break;

        case 1:                                    /* 0x0acb7 - the two cursors */
        case 2:
            /* Left-handed swaps which side of the pen the tool sits on, which is
             * the whole difference between the two arms. */
            if (cheat_state[6])
                index = (2 - e->type) * 12 + e->f14 * 4 + cursor_phase + 6;
            else
                index = (2 - e->type) * 12 + 6 - e->f14 * 4 + cursor_phase;
            break;

        case 4:                                    /* 0x0ad4c */
            if (e->f14)
                index = 0x7b + (e->f14 < 0);       /* two fixed sprites */
            else
                index = anim_script[4][e->frame >> 1];
            break;

        case 0x26:                                 /* 0x0adb0 */
            y -= e->f23;
            index = anim_script[0x26][e->frame >> 1];
            break;

        case 5:                                    /* 0x0adf2 */
            /* A 32-bit signed compare against zero, and only the high word
             * decides it: y == 0 is not retired. */
            if (e->y < 0)
                entity_set_type(e, 0);             /* type 0 is retired */
            /* FALL THROUGH */

        default:                                   /* 0x0ae36 */
            if (type_flags[e->type] & 4) {
                /* The mirrored script lives in the next slot, and which of the
                 * two is used depends on the facing matching the handedness. */
                int16_t mirror = (e->f14 == (cheat_state[6] ? -1 : 1));

                index = anim_script[e->type + mirror][e->frame >> 1];
            } else {
                index = anim_script[e->type][e->frame >> 1];
            }
            break;
        }

        if (haloed)
            outline_sprite(&index, (int16_t) (e->x - view.scroll_x),
                           (int16_t) (y - view.scroll_y), &sprite_table, &view);

        draw_sprite(&index, (int16_t) (e->x - view.scroll_x),
                    y - view.scroll_y, &sprite_table, &view, colour);

        haloed = halo_next;
    }
}

/* ------------------------------------------------------ 0x0b0c5: palette_build
 *
 * The palette the DAC loops upload, built from the current buffer a component at
 * a time: each is scaled by (gamma + 6) / 19 and clamped, so gamma 13 is no
 * correction, below it darkens and above it brightens. The division is the
 * runtime's 32-bit signed one, which is why a component can be multiplied up
 * past 255 before the clamp catches it.
 */
void far palette_build(void)
{
    int16_t i;

    for (i = 0; i < 0x300; i++) {
        int32_t v = (int32_t) ((uint8_t far *) current_buffer)[i]
                  * (gamma_level + 6) / 0x13;

        palette_stored[i] = (uint8_t) (v > 255 ? 255 : v);
    }
}

/* ------------------------------------------------------- 0x0b9ea: set_buffer
 *
 * Eighteen bytes, and the whole of it: store the far argument at [0x1721] and
 * return. Real code rather than an accessor invented here - resource_load and
 * sprite_set_load write the palettes they read straight through it, and the
 * screens publish a stack buffer of their own so that those writes land there
 * instead of in default_buffer.
 */
void far set_buffer(void far *p)
{
    current_buffer = p;                            /* 0x0b9f3, 0x0b9f6 */
}

/* ------------------------------------------- 0x0b9fc: show_attract_screen
 *
 * The hall of fame, and what the menu shows when it has been left alone: the
 * blueprint page with the ten rows on it, held for `frames` and then faded out
 * by show_resource_loop like any other screen.
 *
 * The first row is drawn in colour 1 and given four extra rows of space below
 * it; the rest are colour 2. Scores are right-aligned against x = 0x113.
 */
void far show_attract_screen(int16_t frames)
{
    uint8_t buffer[768];                           /* [bp-0x32a] */
    char    number[0x10];                          /* [bp-0x2a] */
    desc_t  page;                                  /* [bp-0x1a] */
    int16_t y = 0x32;                              /* di */
    int16_t i;

    set_buffer(buffer);
    clear_vram();
    if (!resource_load(&page, 0x4d, 7, 0, 1, 0xff, 1))
        goto out;

    text_colour[0] = 3;
    text_colour[1] = 0;
    draw_string(&page, menu_text[1],               /* "- DUCKS HALL OF FAME -" */
                (0x140 - text_width(menu_text[1])) / 2, 0x1e);

    text_colour[0] = 1;
    for (i = 0; i < 10; i++) {
        draw_string(&page, score_table[i].name, 0x2d, y);
        sprintf(number, "%u", score_table[i].score);
        draw_string(&page, number, 0x113 - text_width(number), y);
        y += (i == 0 ? 1 : 0) * 4 + 0x0c;
        text_colour[0] = 2;
    }
    show_resource_loop(&page, frames);
    resource_release(&page);
out:
    set_buffer(default_buffer);
}

/* ---------------------------------------------- 0x0b52f: show_resource_loop
 *
 * show_splash's sibling: the same fade in, hold, fade out, but from a global
 * viewport and counting down rather than up. Holds a four-plane loop; not native.
 */
void far show_resource_loop(desc_t far *desc, int16_t frames)
{
    /* 0x0b536: the count is taken, a step of 1 or 0 derived from whether it is
     * non-zero, and only then is the count incremented. So a caller asking for
     * zero frames gets si = 1 and a step of 0: the countdown never reaches zero
     * and the page holds until a key. That +1 is the whole of the "no frame
     * count means hold" behaviour, and without it the page lasts one frame. */
    uint8_t step = (frames != 0);                      /* [bp-2] */
    int16_t si   = frames + 1;                         /* 0x0b547 */
    int16_t plane;

    fade_direction = 1;  fade_start_colour = 0;
    palette_build();                                   /* 0x0b0c5 */
    do {
        input_poll(320, 200);
        if (si == 0 || last_key || g_18e5)
            fade_direction = -1;                       /* fade out */
        si -= step;
        for (plane = 0; plane < 4; plane++) {
            set_plane(plane);
            blit_rows(desc, viewport_screen, 0);
        }
        page_flip();
        palette_fade_step(0);
    } while (fade_level != 0);                         /* [0x1798] */
}

/* 0x0bb3b. A number as sprites: glyph 0x71 + digit from the same table the
 * entities use, 12 pixels apart, least significant digit first. Fixed width with
 * no leading-zero suppression, so a score of nothing is six noughts.
 */
void far draw_number(int16_t value, int16_t x, int16_t y, viewport_t far *clip,
                     int16_t flags, int16_t digits)
{
    int16_t i, glyph;

    for (i = digits - 1; i >= 0; i--) {    /* `dec ax` then count down */
        glyph = 0x71 + (value % 10);       /* idiv by 10, remainder + 0x71 */
        draw_sprite(&glyph, x + i * 12, y, &sprite_table, clip, (uint8_t) flags);
        value /= 10;
    }
}

/* ---------------------------------------------------------------- the font
 *
 * One face for the whole game. font_load reads the egg's single 'F' block at
 * startup - 94 glyphs, 5,952 bytes - into a table indexed by the character
 * itself, so an unmapped character has width 0 and draws nothing.
 *
 * A glyph's bytes are stored column-major and are not colours: 0 is
 * transparent, 1 and 2 pick text_colour[0] and text_colour[1]. That is why the
 * screens set two bytes around a page rather than passing a colour - and why a
 * page can print one line in one colour and the next in another without the
 * drawing code knowing anything about it.
 *
 * A glyph advances by width - 1, so neighbouring letters share a column and
 * their outlines join into one continuous border.
 */

glyph_t font[256];               /* 0x054d alias - glyph 0's first byte
                                  * is text_colour[1], and nothing draws
                                  * character 0: charmap sends everything
                                  * unknown to 0x1b */
uint8_t text_colour[2];          /* 0x054c - see dos.h on the shared byte */
uint8_t far *font_codes;         /* 0x20f6 - the first code of each glyph */
uint8_t      font_glyph_count;   /* 0x20fa */
char far    *out_of_memory;      /* 0x0500 - "Out of memory" */

/* 0x06a87 */
void far font_clear(void)
{
    int16_t i;

    for (i = 0; i < 0x100; i++)
        font[i].w = 0;
}

/* 0x06aa4. The block is a count, then per glyph: width, height, a
 * null-terminated list of the character codes that share it, then width*height
 * raw bytes. Only one glyph is shared in this egg - 'O' and '0'. */
void far font_load(void)
{
    int16_t i;
    uint8_t count, w, h, code;

    font_clear();
    if (!egg_find_block(0x46, 0, 0xff))
        fatal("Can't find font", NULL);            /* ds:0x22ea */

    count = egg_read_byte(egg_stream);
    font_codes = malloc(count);
    if (!font_codes)
        fatal(out_of_memory, NULL);
    font_glyph_count = count;

    for (i = 0; i < count; i++) {
        uint8_t far *pixels;

        w = egg_read_byte(egg_stream);
        h = egg_read_byte(egg_stream);
        pixels = malloc((size_t) w * h);
        if (!pixels)
            fatal(out_of_memory, NULL);

        code = egg_read_byte(egg_stream);
        font_codes[i] = code;                      /* only the first is kept */
        while (code) {
            font[code].w      = w;
            font[code].h      = h;
            font[code].pixels = pixels;
            code = egg_read_byte(egg_stream);
        }
        egg_fread(pixels, w, h);                   /* fread, not the decoder */
    }
    egg_block_end();
}

/* 0x06c29. One glyph through the plot pointer, straight at the screen. Nothing
 * calls it: every caller in the program uses the image form below and blits the
 * result. Kept because it is there. */
int16_t far glyph_to_screen(uint8_t ch, int16_t x, int16_t y)
{
    glyph_t far *g = &font[ch];
    int16_t      col, row, i = 0;

    for (col = 0; col < g->w; col++)
        for (row = 0; row < g->h; row++) {
            uint8_t v = g->pixels[i++];

            if (v)
                plot(x + col, y + row, text_colour[v - 1]);
        }
    return g->w ? g->w - 1 : 1;
}

/* 0x06cb6. The same loop into a descriptor's row table. */
int16_t far glyph_to_image(desc_t far *desc, uint8_t ch, int16_t x, int16_t y)
{
    glyph_t far *g = &font[ch];
    int16_t      col, row, i = 0;

    for (col = 0; col < g->w; col++)
        for (row = 0; row < g->h; row++) {
            uint8_t v = g->pixels[i++];

            if (v)
                desc->rows[y + row][x + col] = text_colour[v - 1];
        }
    return g->w ? g->w - 1 : 1;
}

/* 0x06d52. What a string will measure, starting at 1 for the column the last
 * glyph does not share. */
int16_t far text_width(const char far *s)
{
    int16_t i = 0, w = 1;

    while (s[i])
        w += font[(uint8_t) s[i++]].w - 1;
    return w;
}

/* 0x06d84 */
void far draw_string(desc_t far *desc, const char far *s, int16_t x, int16_t y)
{
    int16_t i = 0;

    while (s[i]) {
        x += glyph_to_image(desc, (uint8_t) s[i], x, y);
        i++;
    }
}

/* -------------------------------------------------------- 0x0b5cf: draw_banner
 *
 * A line of the large font into a descriptor, centred. The layout is done in the
 * planar unit rather than in pixels - a byte spans four pixels across the four
 * planes, so a width becomes (width - origin) * 4, the spacing is subtracted in
 * those same units, and the running position is divided by four on the way into
 * the drawing call.
 *
 * The spacing arrives one greater than it is used, and the colour arrives as a
 * bank number that is shifted into the high nibble here, so the drawing itself
 * needs neither adjustment.
 */
void far draw_banner(const char far *s, table_t far *set, int16_t y,
                     desc_t far *desc, uint8_t colour, uint8_t spacing)
{
    int16_t width = 0;               /* [bp-2], in the planar unit */
    int16_t at, i;

    spacing = (uint8_t) (spacing - 1);             /* 0x0b5dc */
    colour  = (uint8_t) (colour << 4);             /* 0x0b5e4 */

    for (i = y - 0x12; i < y + 6; i++)             /* the 24 rows it owns */
        memset(desc->rows[i], 0, (size_t) desc->w);

    for (i = 0; s[i]; i++) {
        sprite_t far *g = &set->base[charmap[(uint8_t) s[i]]];

        width += (g->w - g->ox) * 4 - spacing;
    }

    /* 0x0b684. Centred, still in planar units, less half the spacing so the
     * kerning that follows the last letter does not push the line off-centre. */
    at = ((desc->w + 4 - (width >> 2)) << 1) - (spacing >> 1);

    for (i = 0; s[i]; i++) {
        sprite_t far *g = &set->base[charmap[(uint8_t) s[i]]];

        sprite_to_image(at >> 2, y, g, desc, colour);
        at += (g->w - g->ox) * 4 - spacing;
    }
}

/* 0x204f. Four colours for the cell below, indexed `(selected << 1) + hard`.
 * Initialised data - nothing writes it - so it is carried, like
 * particle_colours. */
uint8_t picker_colour[4] = { 0x50, 0x59, 0x5c, 0x5d };

/* ============================================== 0x10abc: one level-picker cell
 *
 * The cheat's level picker draws its levels as a grid of squares, ten across,
 * and this is one of them: a 0x1a square in one of four colours with a two-pixel
 * drop shadow, and the level's number over it.
 *
 * `slot` is the level's place within its episode - the caller passes
 * `number - episode_index[i].first` - so the grid is per episode and starts at
 * the top left whatever the levels are numbered. Forty fit; past that it draws
 * nothing at all rather than wrapping, which is the `slot >= 0x28` at 0x10ae1.
 *
 * The shadow is the first memset and it is drawn FIRST, two rows down and two
 * columns right, in colour 0; the cell goes over it. Both are `memset` in the
 * original too - 0:0x4c09 - so the row is one call, not a loop.
 *
 * The bounds checks are the port's. The original writes 0x1a bytes into a row it
 * does not measure, which is one allocation there and one per row here.
 *
 * Its only caller is level_picker (0x10c06), which draws the whole grid with it
 * once and then repaints one cell a frame to follow the pointer.
 */
void far picker_cell(int16_t slot, int16_t number, desc_t far *page,
                     int16_t current, int16_t hard)
{
    char    buf[8];                                /* [bp-8] */
    uint8_t colour;
    int16_t x, y, row;

    colour = picker_colour[((current == number) << 1) + hard];   /* 0x10ada */
    if (slot >= 0x28)                              /* 0x10ae1 */
        return;

    x = (int16_t) ((slot % 10) * 32 + 2);          /* 0x10af3 */
    y = (int16_t) ((slot / 10) * 32 + 0x16);       /* 0x10b04 */

    for (row = y; row < y + 0x1a; row++) {         /* 0x10b67 */
        if (row + 2 < page->h && x + 2 + 0x1a <= page->w)
            memset(page->rows[row + 2] + x + 2, 0, 0x1a);        /* the shadow */
        if (row < page->h && x + 0x1a <= page->w)
            memset(page->rows[row] + x, colour, 0x1a);           /* 0x10b57 */
    }

    sprintf(buf, "%i", number);                    /* 0x10b7d, d+0x24d0 */
    draw_string(page, buf, x + 1, y + 0x10);       /* 0x10b9a */
}

/* ================================================== 0x10c06: the level picker
 *
 * What THECROWDSAYBO turns on. level_screens calls this INSTEAD of the episode
 * intro (0x1104b), so with the cheat set every level starts by asking which one
 * you meant: picture 0x4d:1 with four centred captions, then the episode's
 * levels as a grid of numbered squares that light up under the pointer.
 *
 * The grid is drawn once into the page and then edited in place. Each frame the
 * cell under the pointer is repainted highlighted, and the frame after, the one
 * that WAS under it is repainted plain - which is what the two picker_cell
 * calls at 0x10e3c and 0x10ef1 are, one un-highlighting last frame's and one
 * highlighting this frame's. `level_attempted` is where the pointer's cell is
 * kept between them, which is why it is set to -1 twice a frame.
 *
 * `-` and `+` change episode. They set `again`, which ends the inner loop and
 * sends the outer one round to rebuild the page for the new episode - the one
 * place the outer loop is for.
 *
 * The mouse maps to a cell by plain division: 32 pixels a square, ten across, y
 * offset by 0x14 for the captions. All of it is done in longs through the
 * runtime's __ldiv, because mouse_x and mouse_y are 32-bit.
 */
void far level_picker(void)
{
    desc_t     page;                           /* [bp-0x3a] */
    char far  *heading = menu_text[55];        /* 0x1894:0 + 0xdc */
    char far  *hint1   = menu_text[63];        /* + 0xfc */
    char far  *hint2   = menu_text[64];        /* + 0x100 */
    int16_t    was;                            /* [bp-2] - the level we came from */
    int16_t    chosen, again = 1;              /* [bp-4], [bp-6] */
    int16_t    ep, level;                      /* si, di */
    uint8_t    plane;                          /* [bp-7] */

    while (again) {                            /* 0x11006 */
        was   = level_attempted;               /* 0x10c64 */
        again = 0;
        ep    = episode_for_level();           /* 0x10c70 */
        if (ep == 0xff)                        /* 0x10c75 - no episode owns it */
            return;

        if (!resource_load(&page, 0x4d, 1, 0x80, 1, 0xff, 1))
            fatal("Can't load image", 0);      /* d+0x24d3 */

        /* 0x10cc0. Four captions, each centred by its own width. */
        draw_string(&page, heading,
                    (int16_t) ((0x140 - text_width(heading)) >> 1), 0x0a);
        draw_string(&page, hint1,
                    (int16_t) ((0x140 - text_width(hint1)) >> 1), 0xa8);
        draw_string(&page, episode_index[ep].name,
                    (int16_t) ((0x140 - text_width(episode_index[ep].name)) >> 1),
                    0xb2);
        draw_string(&page, hint2,
                    (int16_t) ((0x140 - text_width(hint2)) >> 1), 0xbc);

        for (level = episode_index[ep].first;          /* 0x10dd8 */
             level <= episode_index[ep].last; level++)
            picker_cell((int16_t) (level - episode_index[ep].first), level,
                        &page, was, 0);

        level_attempted = -1;                  /* 0x10deb */
        chosen = 0;
        clear_vram();
        palette_apply_gamma();
        palette_upload();

        do {                                   /* 0x10e02 */
            input_poll(viewport_screen.width, viewport_screen.height);

            /* 0x10e11. Last frame's cell, put back the way it was. */
            if (level_attempted != -1)
                picker_cell((int16_t) (level_attempted
                                       - episode_index[ep].first),
                            level_attempted, &page, was, 0);
            level_attempted = -1;              /* 0x10e42 */

            /* 0x10e48. Which square the pointer is in. Everything is 32-bit
             * because the mouse is; the original divides through __ldiv. */
            if (mouse_y > 0x14) {
                level_attempted = (int16_t)
                    (episode_index[ep].first
                     + ((mouse_y - 0x14) / 32) * 10 + mouse_x / 32);
                if (episode_index[ep].last < level_attempted)   /* 0x10eb6 */
                    level_attempted = -1;
            }

            /* 0x10ec6. This frame's, highlighted. */
            if (level_attempted != -1)
                picker_cell((int16_t) (level_attempted
                                       - episode_index[ep].first),
                            level_attempted, &page, was, 1);

            if (g_18e5 && level_attempted != -1)   /* 0x10ef7 - clicked on one */
                chosen = 1;

            cursor_scene.entities[0].x = mouse_x;  /* 0x10f0a */
            cursor_scene.entities[0].y = mouse_y;
            animate_scene(&cursor_scene);

            for (plane = 0; plane < 4; plane++) {
                set_plane(plane);
                blit_rows(&page, viewport_screen, 0);
                draw_entities(&cursor_scene, viewport_screen, 0);
            }
            page_flip();

            if (last_key == '-' && ep) {           /* 0x10f8d */
                ep--;
                again = 1;
            }
            if (last_key == '+' && ep < episode_count - 1) {    /* 0x10f9e */
                ep++;
                again = 1;
            }
            if (again) {                           /* 0x10fb3 */
                level_attempted   = episode_index[ep].first;
                episode_egg_index = episode_index[ep].egg;      /* [0x94] */
                chosen = 1;
            }
        } while (!chosen);                         /* 0x10fe6 */

        dac_set_black(0, 0);                       /* 0x10ff4 */
        resource_release(&page);
    }
}

/* ================================================= 0x11bee: the episode page
 *
 * One page of text over picture 0x4d:7, held until a key. The caller hands it
 * `episode_index[i].ordinal` and an egg, and the ordinal selects the string
 * block - so this is the episode's own page, one per episode, and the same
 * routine draws any of them.
 *
 * The palette buffer is a LOCAL, 0x300 bytes of stack, published with
 * set_buffer for as long as the page is up and swapped back for the default at
 * the end. That is the only reason this needs its own function: the page has a
 * palette of its own and nothing else on screen may keep it.
 *
 * The cursor entity is set to type 0 - the retire convention - so the pointer is
 * not drawn over the page.
 */
void far episode_page(int16_t ordinal, int16_t egg)
{
    uint8_t buffer[0x300];                         /* [bp-0x316] */
    desc_t  page;                                  /* [bp-0x16] */

    set_buffer(buffer);                            /* 0x11bfc */
    if (resource_load(&page, 0x4d, 7, 0, 1, 0xff, 1)) {        /* 0x11c15 */
        text_colour[0] = 1;                        /* 0x11c1f */
        load_text_page(&page, 0x45, (uint8_t) ordinal, 1, 0x10e, egg);
        entity_set_type(cursor_scene.entities, 0); /* 0x11c48 */
        show_resource_loop(&page, 0);              /* 0x11c56 */
        resource_release(&page);
    }
    set_buffer(default_buffer);                    /* 0x11c6d */
}

/* ---------------------------------------------------- 0x0b7c3: load_text_page
 *
 * A whole page of text drawn into a descriptor: the readme sections, the
 * credits, and the version page. The block is a count and then that many
 * strings, each of which may begin with a digit - that digit, plus the caller's
 * base, is the line's colour, and a line without one keeps the previous line's.
 * So 'H' 0 opens "5DUCKS v1.2" then "3Programmed by Tim Furnish".
 *
 * Lines wider than max_width are wrapped at the last space that still fits, and
 * a page stops at 31 lines however many the block holds. The result is centred
 * both ways: 9 pixels a line about the middle of a 200-line screen, and each
 * line about x = 160.
 */
void far load_text_page(desc_t far *desc, uint8_t type, uint8_t index,
                        uint8_t colour_base, int16_t max_width, int16_t egg)
{
    char      buf[0x100];        /* [bp-0x10c] - only for the error */
    char far *line[31];          /* [bp-0x8a] */
    uint8_t   colour[31];        /* [bp-0xa8] */
    uint8_t   digit = 0;         /* [bp-5] - carried between lines */
    int16_t   n = 0;             /* di */
    int16_t   count, i, y;

    text_colour[1] = 0;                                    /* 0x0b7d2 */
    if (!egg_find_block(type, index, egg)) {
        sprintf(buf, "%c %i", type, index);                /* ds:0x244d */
        fatal("Can't find text section", buf);             /* ds:0x2453 */
    }

    count = egg_read_byte(egg_stream);
    for (i = 0; i < count; i++) {
        char far *s = egg_read_string(egg_stream);
        int16_t   start = 0;

        if (s[0] >= '0' && s[0] <= '9') {
            digit = (uint8_t) (s[0] - '0');
            start = 1;
        }

        /* Wrap, while what is left is too wide. The scan below has no test for
         * the end of the string - it walks until a prefix does not fit - which
         * is safe only because this loop has already established that one will
         * not. */
        while (text_width(s + start) > max_width) {
            int16_t j = start, last = start, fits = 1;

            while (fits) {
                if (s[j] == ' ') {
                    s[j] = 0;
                    if (text_width(s + start) > max_width)
                        fits = 0;
                    else
                        last = j;
                    s[j] = ' ';
                }
                j++;
            }
            s[last] = 0;
            str_copy(s + start, &line[n]);
            colour[n] = digit + colour_base;
            if (n < 30)
                n++;
            start = last + 1;
        }
        str_copy(s + start, &line[n]);
        colour[n] = digit + colour_base;
        if (n < 30)
            n++;
        free(s);
    }
    egg_block_end();

    y = (200 - n * 9) / 2;
    for (i = 0; i < n; i++) {
        text_colour[0] = colour[i];
        draw_string(desc, line[i], 160 - text_width(line[i]) / 2, y);
        y += 9;
        free(line[i]);
    }
}

/* 0x04eed. strlen, malloc, strcpy - all three are the runtime's, and the result
 * goes back through the pointer rather than the return. */
void far str_copy(const char far *s, char far **dest)
{
    *dest = malloc(strlen(s) + 1);
    if (!*dest)
        fatal(out_of_memory, NULL);
    strcpy(*dest, s);
}

/* ------------------------------------------------------ 0x0c0c2: egg_load_one
 *
 * Called once per open egg by egg_load_all. It is not only a loader - it
 * draws, which is what makes the version and credits page appear before the logo,
 * and it holds until a key.
 *
 * One resource, not two: the blueprint frame at 0x4d:7 is loaded, the text is
 * drawn *into that same descriptor* on top of it, and the result is shown once
 * and released. Everything after the load hangs off it succeeding.
 *
 * The two colours are set around it - outline 4 for the whole page, fill 1 as
 * the default a line's own digit then overrides.
 */
void far egg_load_one(int16_t index, int16_t type, int16_t egg)
{
    uint8_t scratch[0x302];
    desc_t  desc;
    uint8_t saved;

    clear_vram();                                  /* 0x0c0ca */
    set_buffer(&scratch[0]);
    saved = text_colour[1];                        /* [0x54d] */
    text_colour[1] = 4;

    if (resource_load(&desc, 0x4d, 7, 0, 1, 0xff, 1)) {   /* the blueprint frame */
        text_colour[0] = 1;                        /* [0x54c] */
        load_text_page(&desc, (uint8_t) type, (uint8_t) index, 0, 0x10e, egg);
        show_resource_loop(&desc, 0);              /* no frame count, so it holds
                                                    * until a key */
        resource_release(&desc);
    }

    text_colour[1] = saved;
    set_buffer(default_buffer);
}

/* ------------------------------------------------ 0x0c156: egg_load_all */
void far egg_load_all(void)
{
    uint8_t scratch[0x302];
    int16_t i;
    uint8_t saved;

    set_buffer(&scratch[0]);               /* publish our own stack buffer */
    saved = text_colour[1];  text_colour[1] = 4;
    for (i = 0; i < egg_file_count; i++)   /* [0x20ad] */
        egg_load_one(0, 0x48, i);          /* 0x0c0c2 */
    text_colour[1] = saved;
    set_buffer(default_buffer);

    /* It also *draws* the version and credits page, through show_resource_loop,
     * and holds it until a key - the wait that read as a hang in one session. */
}

/* --------------------------------------------------- 0x0c1ad: show_resource */
void far show_resource(uint8_t type /* 0x4d */, uint8_t index,
                       int16_t frames, int16_t x /* 0xff */)
{
    uint8_t scratch[0x316];
    desc_t  desc;

    set_buffer(&scratch[0]);
    clear_vram();
    if (resource_load(&desc, type, index, 0, 1, x, 1)) {   /* 0 on failure */
        show_resource_loop(&desc, frames);
        resource_release(&desc);
    }
    set_buffer(default_buffer);
}

/* ==========================================================================
 * The menu screen: 0x0c20e to 0x0ce2d.
 *
 * Every screen in the game is run_screen on a different menu_t. It owns the
 * whole frame - the tiled background, the items, the mouse pointer, the fade -
 * and it returns the item the user chose, which is what game_main switches on.
 *
 * The items are drawn once, into `backdrop`, with the large sprite font; after
 * that a frame is compose_layer over four planes and nothing else. Only the item
 * under the cursor is redrawn, and only because its letter spacing walks a table
 * to make it breathe.
 * ========================================================================== */

/* 0x0c20e. Puts the pointer in the middle of an image. The stores are 32-bit,
 * with the high half explicitly zeroed, and the shift is unsigned. */
void far cursor_to_centre(desc_t far *desc)
{
    mouse_x = (uint16_t) desc->w >> 1;             /* 0x18d3 */
    mouse_y = (uint16_t) desc->h >> 1;             /* 0x18d7 */
}

/* 0x0c237. One item into the backdrop.
 *
 *   style   draw_banner's colour: 0 selected, 1 an ordinary item, 2 a title.
 *           It becomes the high nibble of every pixel, which picks one of the
 *           three sixteen-colour banks run_screen builds.
 *   bounce  an index into bounce_table, and what comes out is a letter spacing.
 *
 * The rows an item owns are 24 apart, which is written as i*8 + i*16 because
 * that is what the compiler emitted and there is no multiply here.
 */
void far draw_menu_item(uint8_t index, uint8_t style, int16_t bounce)
{
    if (index >= current_menu->count)              /* +0x70 */
        return;

    draw_banner(current_menu->item[index].text, &menu_sprites,
                menu_top + index * 8 + index * 16 + 0x2a,
                &backdrop, style, bounce_table[bounce]);
}

/* 0x0c299. Writes an item's current value over the tail of its own label.
 *
 * There is no separate value field and nothing is reformatted: menu_add worked
 * out, once, the column at which the widest value would end flush with the end
 * of the text, and this drops the value in at that column. "SOUNDS: O--" with
 * "N" over column 9 reads SOUNDS: ON-, and with "FF" it reads SOUNDS: OFF.
 *
 * Anything that is not a toggle or a cycle has no value and falls straight out.
 */
void far item_label(item_t far *it)
{
    const char far *src;
    uint8_t         j;

    switch (it->action) {
    case 0x10:                                     /* a toggle */
        src = settings[it->param] ? menu_text[81]  /* "N"  */
                                  : menu_text[82]; /* "FF" */
        break;
    case 0x13:                                     /* a three-way cycle */
        src = extra_text[6 + button_map[it->param]];  /* LEFT/RIGHT/MIDDLE */
        break;
    default:
        return;
    }
    /* One past the length, because the compare is `jbe`: the terminator is
     * copied with the value, and that is what shortens the label when a shorter
     * value replaces a longer one. Without it "SOUNDS: OFF" turned back on
     * reads "SOUNDS: ONF", and the mouse buttons keep their dashes. */
    for (j = 0; j <= strlen(src); j++)             /* 0x0c338: strlen each time */
        it->text[it->value_at + j] = src[j];
}

/* 0x0c3de. The typed-character window: 32 spaces and a terminator. */
void far typed_clear(char far *buf)
{
    int16_t i;

    for (i = 0; i < 0x20; i++)
        buf[i] = ' ';
    buf[0x20] = 0;
}

/* 0x0c3fe. Pushes one typed character into that window and looks for a cheat.
 *
 * The window slides - every character moves down one and the new one goes on the
 * end - so a cheat word is recognised by comparing the last strlen(word)
 * characters, whatever was typed before them. Each of the ten words toggles its
 * own flag, and the border flashes green if the flag just came on and red if it
 * just went off. Returns non-zero when a word matched, which is how run_screen
 * knows to hold that flash for five frames.
 *
 * The flash is four OUTs straight at the DAC - green when the flag just came on,
 * red when it just went off. That used to go nowhere, because `outp` was a no-op
 * in sdl_io.c; it decodes the two DAC ports now, so the flash is the only
 * feedback a cheat gives and it works.
 */
int16_t far typed_push(char far *buf, uint8_t ch)
{
    int16_t i, matched = 0;

    for (i = 0; i < 0x1f; i++)
        buf[i] = buf[i + 1];
    buf[0x1f] = (char) ch;

    for (i = 0; i < cheat_text_count; i++) {
        int16_t n = (int16_t) strlen(cheat_text[i]);

        if (strcasecmp(cheat_text[i], buf + 0x20 - n) == 0) {   /* 0:0x4c28 */
            cheat_state[i] = !cheat_state[i];      /* 0x0505 + i*2 */
            sound_play_guarded(0xd, 1);

            outp(0x3c8, 0);
            outp(0x3c9, (uint8_t) (!cheat_state[i] << 5));      /* red: off */
            outp(0x3c9, (uint8_t) (cheat_state[i] << 5));       /* green: on */
            outp(0x3c9, 0);

            matched = 1;
            typed_clear(buf);
        }
    }
    return matched;
}

/* --------------------------------------------- 0x0c4f0: the slider
 *
 * What an item with action 0x11 opens: GAME SPEED, AMBIENCE VOLUME, GAMMA
 * CORRECT. It is not a screen of its own - the menu stays where it is, a trough
 * is drawn into the backdrop under the item, and 32 blocks are added to a scene
 * of their own and lit up to the value.
 *
 * The value is held in the mouse: it goes in as x * 4 and comes back out as
 * x / 4, and input_poll(0x80, 1) is what clamps it to 0..31. So dragging the
 * mouse moves the slider, and left and right nudge it by one.
 *
 * Which of the three it is comes from the item's param, and the original
 * indexes d+0x1fd3 with it because the three are adjacent there. They are three
 * variables here, so this picks between them by name.
 *
 * How it ends depends on how it began. Opened with a button already down - a
 * click on the item - it runs until that button comes up. Opened from the
 * keyboard it runs until SPACE, ENTER or any button.
 */
static uint8_t *slider_value(uint8_t param)
{
    switch (param) {                               /* d+0x1fd3 + param */
    case 0:  return &ambience_volume;
    case 1:  return &game_speed;
    default: return &gamma_level;
    }
}

void far slider_screen(item_t far *it, int16_t y)
{
    scene_t  scene;                                /* [bp-0x1a] */
    uint8_t *value = slider_value(it->param);
    int16_t  held    = button_b_down;              /* [bp-4] */
    int16_t  running = 1;                          /* [bp-6] */
    int16_t  type[2];                              /* [bp-0xa], [bp-8] */
    uint8_t  i;
    int16_t  plane;

    type[0] = bar_type_off;
    type[1] = bar_type_on;
    scene_alloc(&scene, 0x20);

    /* The trough: 38 sprites, and the one at each end is a different piece. */
    for (i = 0; i < 0x26; i++)
        sprite_to_image_plain(i * 8 + slider_x + 9, y,
                              &sprite_table.base[0x103 - (i == 0)
                                                       + (i == 0x25)],
                              &backdrop);

    /* The 32 blocks, as entities so they animate. Their type is set every frame
     * below; type 0 here is what scene_add leaves them as. */
    for (i = 0; i < 0x20; i++)
        scene_add(&scene, i * 9 + slider_x + 0x14, y + 0x12, 0, 5);

    mouse_x = *value * 4;

    while (running) {
        if (last_key == 0x14d)                     /* right */
            mouse_x += 4;
        if (last_key == 0x14b)                     /* left */
            mouse_x -= 4;
        input_poll(0x80, 1);                       /* and the clamp is the point */
        *value = (uint8_t) (mouse_x >> 2);

        for (i = 0; i < 0x20; i++)
            entity_set_type(&scene.entities[i], type[*value > i]);

        animate_scene(&scene);
        for (plane = 0; plane < 4; plane++) {
            set_plane((uint8_t) plane);
            compose_layer();
            draw_entities(&scene, viewport_full, 0);
        }
        page_flip();

        if (held)
            running = button_b_down;
        else
            running = !(last_key == 0x20 || last_key == 0x0d || g_18e5);

        /* GAMMA CORRECT is the one that shows while it is being moved: the
         * palette is rebuilt and then handed straight over, which is what
         * palette_fade_step's argument is for. */
        if (it->param == 2)
            palette_build();
        palette_fade_step(1);
    }
}

/* ------------------------------------------------------- 0x0c716: run_screen
 *
 *   menu    the descriptor to run
 *   chosen  a far int16_t the index of the chosen item is written back through
 *   owns    non-zero when this screen owns its images and its fade. game_main
 *           passes 1, so choosing an item fades out before returning; the same
 *           variable is then reused to mean "leave".
 *
 * Returns a pointer into the menu, at the item chosen - except when the screen
 * gave up waiting, and then it returns one of menu_idle's two items, which is
 * how the attract cycle and the demo picker are reached without either of them
 * being anything the user can point at.
 */
item_t far *far run_screen(menu_t far *menu, void far *chosen, int16_t owns)
{
    char         typed[0x21];      /* [bp-0x42] */
    uint8_t far *pal = current_buffer;
    int16_t      running   = 1;    /* [bp-6]    */
    int16_t      timed_out = 0;    /* [bp-8]    */
    int16_t      i;                /* [bp-0xe]  */
    uint8_t      sel       = 0;    /* [bp-0xf]  - the item this frame acts on */
    uint8_t      drawn     = 0;    /* [bp-0x10] - the one currently highlighted */
    uint8_t      hover     = 0;    /* [bp-0x11] - where the keys or mouse point */
    uint8_t      flash     = 0;    /* [bp-0x12] - a cheat's border flash */
    uint8_t      escaped   = 0;    /* [bp-0x13] */
    int16_t      idle      = 0;    /* si */
    int16_t      leave     = owns; /* di */

    colour_cycle = 6;
    do {                                           /* 0x0c73f: drain the key */
        input_poll(0x140, 0xc8);
    } while (last_key);
    typed_clear(typed);

    current_menu = menu;                           /* 0x1900 */
    sprite_set_load(1, 0x53, &menu_sprites, 0xff); /* the large font */

    /* 0x0c785. Three banks out of the sprite set's own sixteen colours, because
     * draw_banner's `colour` is a bank number shifted into the high nibble:
     * entries 0-15 as loaded for the selected item, 16-31 at half brightness for
     * an ordinary one, and 32-47 red only and a third brighter for a title. The
     * red is the i % 3 test: component 0 of each entry survives, and in the
     * original the other two are multiplied by zero rather than skipped.
     *
     * The multiply is a double, and a double here is a double - the original
     * reaches it through Borland's 8087 emulator, which is only interesting when
     * reading the disassembly: the emulator patches each ESC opcode into an
     * INT 34h..3Bh, so this loop decodes as `int 0x3b` and garbage unless those
     * are turned back into D8..DF first.
     *
     * The long cast is not decoration. 255 * 1.3 is 331, and the original
     * converts to a long and stores the low byte, so a bright component wraps. */
    for (i = 0; i < 0x30; i++) {
        pal[i + 0x30] = (uint8_t) (pal[i] >> 1);
        pal[i + 0x60] = (i % 3 == 0) ? (uint8_t) (int32_t) (pal[i] * 1.3) : 0;
    }
    palette_set_black(0);                          /* 0x0c800 */
    load_background(menu->background, 0xff);       /* 0x0c815 */

    /* 0x0c81b. Not "hold it still": the y step is 1, so the tile creeps upward
     * a row a frame for as long as the menu is up, and only the starting offset
     * is zeroed. Every menu snapshot has a different scroll in it. */
    bg_step_y = 1;  bg_scroll_y = 0;  bg_step_x = 0;  bg_scroll_x = 0;
    image_alloc(&backdrop, screen_width, screen_height);
    image_clear(&backdrop, 0);
    clear_vram();

    /* 0x0c853. The block of items is centred, 24 rows each, and then shifted for
     * whichever piece of furniture the screen has room for: the strip along the
     * bottom, and the big logo at the top when the menu is short enough. */
    menu_top = screen_height / 2 - menu->count * 0x18 / 2;
    if (menu->count < 8 || video_mode)
        resource_load_at(&backdrop, 0x4d, 0x2c, 0x70, screen_height - 15, 0xff);
    else
        menu_top += 8;
    if (menu->count < video_mode * 2 + 5)
        resource_load_at(&backdrop, 0x4d, 0, 0x80, 2, 0xff);
    else
        menu_top -= 0x21;

    for (i = 0; i < menu->count; i++)              /* 0x0c8da */
        draw_menu_item((uint8_t) i,
                       (uint8_t) ((menu->item[i].action == 0) + 1), 8);

    viewport_game.scroll_x = 0;                    /* 0x0c913 */
    viewport_game.scroll_y = 0;
    entity_set_type(&cursor_scene.entities[0], 0x14);   /* the mouse pointer */
    cursor_to_centre(&backdrop);
    mouse_y = drawn * 0x18 + menu_top + 0x23;
    if (*menu->item[drawn].visible == 0)           /* a title cannot be chosen */
        drawn = 0xff;

    fade_direction = 1;                            /* 0x0c97c: fade in */
    fade_level = 0;

    do {
        colour_cycle = (colour_cycle + 1) & 0xf;
        input_poll(screen_width, screen_height);

        if (flash && --flash == 0) {               /* 0x0c99f */
            outp(0x3c8, 0);                        /* the cheat flash, off */
            outp(0x3c9, 0);
            outp(0x3c9, 0);
            outp(0x3c9, 0);
        }

        /* 0x0c9c9. Once the fade out has started nothing is read any more: the
         * frame below still runs, but every branch that could change the choice
         * is skipped. */
        if (fade_direction != -1) {

            if (idle++ > 0x1f4 && !game_in_progress) {
                timed_out = 1;                     /* 0x0c9e2 */
                fade_direction = -1;
            }

            switch (last_key) {
            case 0x1b:                             /* ESC: the last item, which
                                                    * is always the way out */
                hover = (uint8_t) (menu->count - 1);
                /* FALL THROUGH */
            case 0x0d:                             /* ENTER */
            case 0x20:                             /* SPACE */
                g_18e5 = 1;                        /* stand in for a click */
                idle = 0;
                break;

            case 0x148:                            /* up */
                hover--;
                if (hover >= menu->count)          /* wrapped past zero */
                    hover = (uint8_t) (menu->count - 1);
                mouse_y = hover * 0x18 + menu_top + 0x23;
                idle = 0;
                break;

            case 0x150:                            /* down */
                hover++;
                if (hover >= menu->count)
                    hover = 0;
                mouse_y = hover * 0x18 + menu_top + 0x23;
                idle = 0;
                break;

            default:
                /* 0x0ca98. Which item the pointer is over: a 32-bit divide, and
                 * the -1 is for the strip of screen above the first item. */
                hover = (uint8_t) ((mouse_y - menu_top) / 0x18 - 1);

                if (last_key > 0 && last_key < 0x100) {
                    if (typed_push(typed, (uint8_t) last_key))
                        flash = 5;
                    /* 0x0cadf. Cheat 8 is the one that opens the demo picker,
                     * and only from the main menu with no game in progress. */
                    if (cheat_state[8]) {
                        cheat_state[8] = 0;
                        if (menu == &main_menu && !game_in_progress) {
                            timed_out = 1;
                            fade_direction = -1;
                            escaped = 1;
                        }
                    }
                    idle = 0;
                }
                break;
            }

            sel = hover;                           /* 0x0cb11 */
            if (sel < menu->count) {
                if (*menu->item[sel].visible == 0) {
                    sel = 0xff;                    /* a title: nothing to choose */
                } else if (g_18e5) {               /* something was pressed */
                    item_t far *it = &menu->item[sel];

                    switch (it->action) {
                    case 0x10:                     /* 0x0cb79: a toggle */
                        settings[it->param] = !settings[it->param];
                        item_label(it);
                        sound_play_guarded(0xd, 1);
                        idle = 0;
                        break;

                    case 0x13:                     /* 0x0cbd3: a cycle of three */
                        button_map[it->param] =
                            (button_map[it->param] + 1) % 3;
                        item_label(it);
                        sound_play_guarded(0xd, 1);
                        idle = 0;
                        break;

                    case 0x11:                     /* 0x0cc34: a slider */
                        slider_screen(it, menu_top + sel * 8 + sel * 16 + 0x18);
                        cursor_to_centre(&backdrop);
                        mouse_y = drawn * 0x18 + menu_top + 0x23;
                        idle = 0;
                        break;

                    default:                       /* 0x0cc92: hand it back */
                        if (it->action == 0xf)
                            leave = 1;
                        if (leave)
                            fade_direction = -1;   /* fade, then return it */
                        else
                            running = 0;           /* return it now */
                        sound_play_guarded(3, 1);
                        break;
                    }
                }
            }
        }

        /* 0x0ccc5. Only two items are ever redrawn: the one the cursor left, in
         * the ordinary bank, and the one it is on, whose spacing walks the
         * bounce table. With MENU BOUNCE off the spacing is pinned. */
        if (sel != drawn) {
            draw_menu_item(drawn, 1, 8);
            colour_cycle = 8;
            drawn = sel;
            idle = 0;
            if (sel < menu->count)
                sound_play_guarded(0, 1);
        } else if (settings[4]) {
            draw_menu_item(sel, 0, colour_cycle);
        } else {
            draw_menu_item(sel, 0, 2);
        }

        cursor_scene.entities[0].x = mouse_x;      /* 0x0cd2f */
        cursor_scene.entities[0].y = mouse_y;
        animate_scene(&cursor_scene);

        for (i = 0; i < 4; i++) {
            set_plane((uint8_t) i);
            compose_layer();                       /* tile, then the backdrop */
            draw_entities(&cursor_scene, viewport_full, 0);
        }
        page_flip();
        palette_fade_step(0);

        if (leave)                                 /* 0x0cda5 */
            running = (fade_level != 0);
    } while (running);

    if (leave) {                                   /* 0x0cdc3 */
        resource_release(&background);
        resource_release(&backdrop);
        sprite_set_free(&menu_sprites);
    }
    set_buffer(default_buffer);

    *(int16_t far *) chosen = sel;                 /* 0x0cdf3 */
    if (timed_out)                                 /* 0x0cdfe */
        return &menu_idle.item[escaped];
    return &menu->item[sel];                       /* 0x0ce15 */
}

/* ==================================================== 0x09329: level_free
 *
 * Everything level_load allocated, given back. Three callers and they are the
 * three ways a level stops mattering: run_level's teardown at 0x0e7ec,
 * level_screens at 0x11484 when the level-select key means the level it just
 * loaded will not be played, and game_main's shareware refusal at 0x13856.
 *
 * It was a stub, so every one of those leaked a whole level - the backdrop, the
 * background tile, every scenery image, all five entity arrays and the tool
 * list.
 *
 * Two things in it are not memory at all and are easy to miss:
 *
 *   level_flags[0] and [3] are cleared, which is what stops the blink and the
 *   0x0e673 branch carrying into whatever comes next;
 *   the sample rate goes back to 11000, undoing the D key, which doubles it for
 *   the level it is pressed on and would otherwise leave every later sound fast.
 *
 * scenes[4] is not freed. That is the cursor scene, and run_level owns it.
 * The order the arrays go in - 3, 5, 0, 1, 2 - is the original's.
 */
void far level_free(void)
{
    int16_t i;

    level_flags[0] = 0;                            /* 0x0932d, [0x201e] */
    level_flags[3] = 0;                            /* [0x2024] */
    if (sound_available)                           /* 0x09339 */
        sound_set_rate(0x2af8);                    /* 11000 */

    resource_release(&background);                 /* 0x09352, d+0x170b */
    resource_release(&backdrop);                   /* 0x0935d, d+0x16f5 */

    for (i = 0; i < solid_count; i++)              /* 0x0937f */
        resource_release(&solids[i]);              /* each is 0x20 bytes */
    free(solids);                                  /* 0x09390 */

    free(scenes[3].entities);                      /* 0x093a0 */
    free(scenes[5].entities);                      /* 0x093b0 */
    free(scenes[0].entities);                      /* 0x093c0 */
    free(scenes[1].entities);                      /* 0x093d0 */
    free(scenes[2].entities);                      /* 0x093e0 */
    free(tool_list);                               /* 0x093f0, d+0x1782 */
}

/* ============================================ the tool list, d+0x1782
 *
 * What the player can place, and which of them is selected. The array holds
 * entity types - 0x0d591 tests its entries against the same per-type flag table
 * load_animations fills, which is what identifies them.
 * ======================================================================== */

int16_t far *tool_list;          /* 0x1782 */
int16_t      tool_type;          /* 0x1786 - the selected entry, copied out */
uint8_t      tool_at;            /* 0x1788 - which one */
uint8_t      tool_count;         /* 0x178b */

/* 0x0d55d. Is `type` one of them? The loop does not stop when it finds one - it
 * walks the whole list and remembers. */
int16_t far tool_list_has(int16_t type)
{
    int16_t i, found = 0;

    for (i = 0; tool_count > i; i++)
        if (tool_list[i] == type)
            found = 1;
    return found;
}

/* 0x0d591. Does any of them have bit 1 set in its type flags? Same shape. */
int16_t far tool_list_any_flagged(void)
{
    int16_t i, found = 0;

    for (i = 0; tool_count > i; i++)
        if (type_flags[tool_list[i]] & 2)
            found = 1;
    return found;
}

/* 0x0d757. The HUD's number drawer. Same digit layout as draw_number - glyph
 * 0x71 plus the digit, 12 pixels apart, least significant first, no leading-zero
 * suppression - but with the clip, sprite table and colour fixed, and glyph 0x70
 * drawn behind each digit first. That backdrop is the visible difference between
 * the HUD's numbers and the in-game frame's.
 *
 * Never observed to run: it is the one hooked address whose correctness rests
 * only on the disassembly.
 */
void far draw_number2(int16_t value, int16_t digits, int16_t x, int16_t y)
{
    int16_t i;

    for (i = digits - 1; i >= 0; i--) {
        int16_t tile = 0x70, glyph = 0x71 + (value % 10);

        /* viewport_panel and colour 0x90, which is what native.py passes and
         * what --verify compares against the original on every call. The
         * `hud_clip` this used to clip against was an invented global that
         * nothing ever filled, so every digit was clipped away entirely. */
        draw_sprite(&tile,  x + i * 12, y, &sprite_table, &viewport_panel, 0x90);
        draw_sprite(&glyph, x + i * 12, y, &sprite_table, &viewport_panel, 0x90);
        value /= 10;
    }
}

/* ==========================================================================
 * Building the menus: 0x0e8ad to 0x0f55b.
 *
 * Fifteen descriptors, assembled once at startup out of the string tables and
 * then only read. Nothing in the executable holds a menu: the words come from
 * the eggs, the structure from build_menus below, and a submenu is a pointer to
 * another descriptor rather than any kind of code.
 * ========================================================================== */

/* 0x0e8ad. Empties a menu. Background 3 is the default; the screens that want
 * another one overwrite it after they have added their items. */
void far menu_reset(menu_t far *m)
{
    m->count = 0;
    m->background = 3;
}

/* 0x0e8c3. Replaces an item's text, freeing what was there. */
void far menu_set_text(item_t far *it, const char far *text)
{
    free(it->text);
    str_copy(text, &it->text);
}

/* 0x0e8ed. The one that adds an item; everything below is a forwarder that
 * fixes some of these arguments.
 *
 * The interesting line is the last one. value_at is where item_label will write
 * the item's value, and it is the length of the label less the length of the
 * widest value the item could take - so the value ends flush with the end of
 * the text, and "SOUNDS: O--" reserves exactly the room that "OFF" needs.
 */
void far menu_add(menu_t far *m, const char far *text, menu_t far *link,
                  int16_t action, int16_t far *visible, uint8_t param)
{
    item_t far *it;
    int16_t     n;

    if (m->count >= 7)                             /* seven slots, no more */
        return;

    it = &m->item[m->count];
    str_copy(text, &it->text);                     /* 0x04eed: its own copy */
    it->link    = link;
    it->action  = action;
    it->visible = visible;
    it->param   = param;

    for (n = 0; text[n]; n++)
        ;
    it->value_at = (uint8_t) (n - (action == 0x10 ? on_off_width
                                                  : cycle_width));
    item_label(it);
    m->count++;
}

/* 0x0e9e9 */
void far menu_add_action(menu_t far *m, const char far *text, int16_t action,
                         int16_t far *visible, uint8_t param)
{
    menu_add(m, text, 0, action, visible, param);
}

/* 0x0ea12. A heading: action 0, and menu_never for its flag, which is what
 * makes run_screen refuse to select it. */
void far menu_add_title(menu_t far *m, const char far *text)
{
    menu_add(m, text, 0, 0, &menu_never, 0);
}

/* 0x0ea36 */
void far menu_add_toggle(menu_t far *m, const char far *text, uint8_t param,
                         int16_t far *visible)
{
    menu_add(m, text, 0, 0x10, visible, param);
}

/* 0x0ea5e */
void far menu_add_cycle(menu_t far *m, const char far *text, uint8_t param,
                        int16_t far *visible)
{
    menu_add(m, text, 0, 0x13, visible, param);
}

/* 0x0ea86 */
void far menu_add_entry(menu_t far *m, const char far *text, uint8_t param,
                        int16_t far *visible)
{
    menu_add(m, text, 0, 0x11, visible, param);
}

/* 0x0eaae */
void far menu_add_submenu(menu_t far *m, const char far *text,
                          menu_t far *link, int16_t far *visible)
{
    menu_add(m, text, link, 0x12, visible, 0);
}

/* 0x0ead6. Frees every item's text. */
void far menu_free(menu_t far *m)
{
    int16_t i;

    for (i = 0; i < m->count; i++)
        free(m->item[i].text);
}

/* 0x0eb04. A list too long for one screen, cut into pages.
 *
 * Each page is the title, up to three entries, a MORE_ that points at the next
 * page, and a CANCEL that points back where the caller came from. The pages
 * after the first are malloc'd, which is why the episode list, the readme
 * sections and the demo picker are the only menus not in DGROUP.
 *
 * The loop runs one past the end: that last turn adds no entry and exists only
 * to put CANCEL on the final page.
 */
void far menu_add_list(menu_t far *m, int16_t count, episode_t far *records,
                       int16_t action, const char far *title, menu_t far *back)
{
    menu_t far *next = 0;
    int16_t     i, open = 1, last;

    for (i = 0; i <= count; i++) {
        if (open) {                                /* start a page */
            menu_reset(m);
            menu_add_title(m, title);
        }
        open = 0;
        last = (i == count);

        /* 0x0eb4f. Four items on the page and more than one entry still to
         * come: this page is full, so it gets both a MORE_ and a CANCEL. */
        if (count - 2 > i && m->count == 4) {
            last = 1;
            open = 1;
        }
        if (i != count)
            menu_add_action(m, records[i].name, action, &menu_always,
                            (uint8_t) i);
        if (open) {
            next = malloc(sizeof *next);           /* 0x73 bytes */
            if (!next)
                fatal(out_of_memory, 0);
            menu_add_submenu(m, menu_text[32], next, &menu_always);  /* MORE_ */
        }
        if (last)
            menu_add_submenu(m, menu_text[33], back, &menu_always);  /* CANCEL */
        if (open)
            m = next;
    }
}

/* ------------------------------------------------------ 0x0ec46: build_menus
 *
 * Called once, from init. The two widths at the top are what menu_add needs to
 * right-align a value against the end of its label: the wider of "N" and "FF"
 * for a toggle, and the widest of LEFT, RIGHT and MIDDLE for a cycle.
 *
 * Nine of the menus set a background of their own after their last item, as a
 * plain byte store rather than through any of the calls above; the other six
 * keep the 3 that menu_reset left.
 */
void far build_menus(void)
{
    on_off_width = (uint8_t) strlen(menu_text[81]);
    if (on_off_width < strlen(menu_text[82]))
        on_off_width = (uint8_t) strlen(menu_text[82]);

    cycle_width = (uint8_t) strlen(extra_text[6]);
    if (cycle_width < strlen(extra_text[7]))
        cycle_width = (uint8_t) strlen(extra_text[7]);
    if (cycle_width < strlen(extra_text[8]))
        cycle_width = (uint8_t) strlen(extra_text[8]);

    /* the three paged lists */
    menu_add_list(&menu_episodes, episode_count, episode_index, 1,
                  menu_text[31], &menu_play);         /* SELECT AN EPISODE: */
    menu_add_list(&menu_readme, readme_count, readme_index, 7,
                  menu_text[9], &main_menu);          /* READ ME! */
    menu_add_list(&menu_demos, g_2038, demo_index, 0x15,
                  extra_text[13], &main_menu);        /* PICK A DEMO */

    /* 0x0edde. Never drawn. run_screen returns item 0 when it gives up waiting
     * and item 1 when the demo-picker cheat is typed, so these two exist only to
     * carry an action code back to game_main. Both labels are empty strings. */
    menu_reset(&menu_idle);
    menu_add_action(&menu_idle, "", 0x0a, &menu_always, 0);   /* d+0x24b8 */
    menu_add_submenu(&menu_idle, "", &menu_demos, &menu_always);

    menu_reset(&main_menu);
    menu_add_submenu(&main_menu, menu_text[2],  &menu_play,    &menu_always);
    menu_add_submenu(&main_menu, menu_text[8],  &menu_options, &menu_always);
    menu_add_submenu(&main_menu, menu_text[9],  &menu_readme,  &menu_always);
    menu_add_submenu(&main_menu, menu_text[10], &menu_quit,    &menu_always);
    main_menu.background = 0;                      /* 0x0eeb6 - the brick wall */

    menu_reset(&menu_play);
    menu_add_submenu(&menu_play, menu_text[4],  &menu_episodes,  &menu_always);
    menu_add_submenu(&menu_play, menu_text[11], &menu_load_save, &menu_always);
    /* END CURRENT GAME is there only while a game is in progress, and that is
     * the whole of what an item's flag pointer is for. */
    menu_add_submenu(&menu_play, menu_text[17], &menu_end_game,
                     &game_in_progress);
    menu_add_submenu(&menu_play, menu_text[12], &main_menu,      &menu_always);
    menu_play.background = 12;

    menu_reset(&menu_end_game);
    menu_add_title(&menu_end_game, extra_text[10]);
    menu_add_action(&menu_end_game, extra_text[11], 3, &game_in_progress, 0);
    menu_add_submenu(&menu_end_game, extra_text[12], &menu_play, &menu_always);
    menu_end_game.background = 13;

    menu_reset(&menu_load_save);
    menu_add_title(&menu_load_save, menu_text[11]);
    menu_add_action(&menu_load_save, menu_text[35], 5, &game_in_progress, 0);
    menu_add_action(&menu_load_save, menu_text[34], 6, &menu_always, 0);
    menu_add_submenu(&menu_load_save, menu_text[33], &menu_play, &menu_always);
    menu_load_save.background = 15;

    menu_reset(&menu_options);
    menu_add_title(&menu_options, menu_text[8]);
    menu_add_submenu(&menu_options, menu_text[13], &menu_audio, &menu_always);
    menu_add_submenu(&menu_options, menu_text[14], &menu_video, &menu_always);
    menu_add_submenu(&menu_options, menu_text[15], &menu_mouse, &menu_always);
    menu_add_entry(&menu_options, menu_text[23], 1, &menu_always);
    menu_add_action(&menu_options, menu_text[56], 0x0e, &menu_always, 0);
    menu_add_submenu(&menu_options, extra_text[1], &main_menu, &menu_always);

    /* Both audio items are there only if detect_hardware found a card. */
    menu_reset(&menu_audio);
    menu_add_title(&menu_audio, menu_text[13]);
    menu_add_toggle(&menu_audio, menu_text[18], 0, &sound_available);
    menu_add_entry(&menu_audio, menu_text[19], 0, &sound_available);
    menu_add_submenu(&menu_audio, extra_text[1], &menu_options, &menu_always);
    menu_audio.background = 22;

    menu_reset(&menu_video);
    menu_add_title(&menu_video, menu_text[14]);
    menu_add_submenu(&menu_video, menu_text[25], &menu_resolution, &menu_always);
    menu_add_toggle(&menu_video, menu_text[20], 1, &menu_always);
    menu_add_toggle(&menu_video, menu_text[22], 4, &menu_always);
    menu_add_entry(&menu_video, extra_text[0], 2, &menu_always);
    menu_add_submenu(&menu_video, extra_text[1], &menu_options, &menu_always);
    menu_video.background = 22;

    menu_reset(&menu_resolution);
    menu_add_title(&menu_resolution, menu_text[25]);
    menu_add_action(&menu_resolution, menu_text[26], 0x0c, &menu_always, 0);
    menu_add_action(&menu_resolution, menu_text[27], 0x0d, &menu_always, 0);
    menu_add_submenu(&menu_resolution, menu_text[33], &menu_video, &menu_always);
    menu_resolution.background = 15;

    menu_reset(&menu_mouse);
    menu_add_title(&menu_mouse, menu_text[15]);
    menu_add_toggle(&menu_mouse, menu_text[21], 3, &menu_always);
    menu_add_submenu(&menu_mouse, extra_text[2], &menu_buttons, &menu_always);
    menu_add_submenu(&menu_mouse, extra_text[1], &menu_options, &menu_always);
    menu_mouse.background = 22;

    /* 0x0f432. The three cycles are added out of order - USE TOOL first - and
     * their params are 2, 0, 1, so each still owns the right button. */
    menu_reset(&menu_buttons);
    menu_add_title(&menu_buttons, extra_text[2]);
    menu_add_cycle(&menu_buttons, extra_text[5], 2, &menu_always);
    menu_add_cycle(&menu_buttons, extra_text[3], 0, &menu_always);
    menu_add_cycle(&menu_buttons, extra_text[4], 1, &menu_always);
    menu_add_action(&menu_buttons, extra_text[1], 0x14, &menu_always, 0);
    menu_buttons.background = 10;

    menu_reset(&menu_quit);
    menu_add_title(&menu_quit, menu_text[28]);
    menu_add_action(&menu_quit, menu_text[29], 4, &menu_always, 0);
    menu_add_submenu(&menu_quit, menu_text[30], &main_menu, &menu_always);
}

/* 0x0f55c. The inverse of menus_resume: the two items it relabelled go back to
 * what they were, PLAY DUCKS' first item points at the episode list again, and
 * the game is no longer in progress. Called after every high_score_screen. */
void far menus_after_game(void)
{
    menu_set_text(&main_menu.item[0], menu_text[2]);       /* "PLAY DUCKS" */
    menu_set_text(&menu_play.item[0], menu_text[4]);       /* "START A NEW GAME" */
    menu_play.item[0].action = 0x12;
    menu_play.item[0].link   = &menu_episodes;
    game_in_progress = 0;
}

/* -------------------------------------------- 0x0f5b1: cutscene_rocket_space
 *
 * The first of the six endings, and the only one that animates by arithmetic
 * rather than by holding a picture: the rocket crosses a starfield from the
 * bottom right, and everything about its path is in seven words of state.
 *
 * Position is 1/8th of a pixel, so `si`/`di` are eight times the screen
 * coordinates and the draw shifts them down by three. It starts at (0xb7c,
 * 0x802) travelling up and left, and both steps are SUBTRACTED each frame -
 * `dx` 16 and `dy` 8 - so the rocket flies up-left until something changes them.
 *
 * Crossing x = 0x320 is what changes them. That arms two things at once: the
 * flame animation, which walks the sprite index up and adds 2 to `dy` a frame,
 * and the braking, which takes `dx` down to zero and `dy` with it. So the rocket
 * decelerates, the flame lights, and it arcs over and falls back down the screen
 * until `di` passes 0xaf0 and the scene ends.
 *
 * `frame = counter` is a `sar ax, 0` in the original - a shift by zero, which is
 * what Turbo C emitted for whatever the expression was. Written as the
 * assignment it is.
 */
void far cutscene_rocket_space(void)
{
    desc_t  desc;
    table_t set;
    int16_t frame   = 0;                           /* [bp-0xa] */
    int16_t counter = 0;                           /* [bp-0xc] */
    int16_t dy      = 8;                           /* [bp-0xe] */
    int16_t dx      = 0x10;                        /* [bp-0x10] */
    int16_t braking = 0;                           /* [bp-0x12] */
    int16_t flaming = 0;                           /* [bp-0x14] */
    int16_t armed   = 1;                           /* [bp-0x16] */
    int16_t x = 0xb7c, y = 0x802;                  /* si, di - eighths */
    int16_t plane;

    clear_vram();
    if (!resource_load(&desc, 0x4d, 0x32, 0, 1, 0xff, 1))
        return;
    sprite_set_load(0x32, 0x53, &set, 0xff);
    clear_vram();
    palette_apply_gamma();
    palette_upload();
    sound_play_guarded(0x0c, 0x2710);

    do {
        for (plane = 0; plane < 4; plane++) {      /* 0x0f637 */
            set_plane((uint8_t) plane);
            blit_rows(&desc, viewport_screen, 0);
            draw_sprite(&frame, (int16_t) (x >> 3), (int32_t) ((y >> 3) - 50),
                        &set, &viewport_screen, 0);
        }

        if (flaming) {                             /* 0x0f68e */
            counter++;
            frame = counter;
            dy   += 2;
            if (frame == 6)
                flaming = 0;
        }
        if (braking) {                             /* 0x0f6af */
            if (dx)
                dx--;
            dy--;
        }
        x -= dx;                                   /* 0x0f6c1 */
        y -= dy;

        if (armed && x < 0x320) {                  /* 0x0f6c7 - it arrives */
            sound_play_guarded(0x64, 0x2710);
            flaming = 1;
            braking = 1;
            armed   = 0;
        }
        page_flip();
    } while (y < 0xaf0);                           /* 0x0f6f2 */

    sprite_set_free(&set);
    resource_release(&desc);
}

/* ------------------------------------------- 0x0f825: cutscene_welcome_home
 *
 * One of the six ending screens; the others have the same shape with different
 * resource ids. Each draws into both video pages, which is the two-iteration
 * outer loop, and each holds its own four-plane loop.
 */
void far cutscene_welcome_home(void)
{
    desc_t  desc;
    int16_t page, plane;

    if (!resource_load(&desc, 0x4d, 0x36, 0, 0, 0xff, 1))
        return;
    clear_vram();
    palette_build();
    for (page = 0; page < 2; page++) {
        for (plane = 0; plane < 4; plane++) {
            set_plane(plane);
            blit_rows(&desc, viewport_screen, 0);
        }
        page_flip();
        if (page == 0)
            palette_upload();
    }
    hold_frames(150);                          /* the hold - unnamed */
    resource_release(&desc);
}

/* --------------------------------------------------- 0x100a7: image_fill_rect
 *
 * Flood a rectangle of an image with one byte. Only the night scene uses it, to
 * black out the ground and the two steps of the doorway in the picture it
 * inherits, so the monster has something solid to walk in front of. Half-open
 * on both axes, and it walks the row pointers rather than assuming the image is
 * one block - which is why the port and the original agree here.
 */
static void far image_fill_rect(int16_t x0, int16_t y0, int16_t x1, int16_t y1,
                                desc_t far *desc, uint8_t colour)
{
    int16_t x, y;

    for (y = y0; y < y1; y++) {                    /* 0x100eb */
        uint8_t far *p = desc->rows[y] + x0;       /* 0x100cb */

        for (x = x0; x < x1; x++)                  /* 0x100e6 */
            *p++ = colour;
    }
}

/* ============================================== 0x100f4: cutscene_night_monster
 *
 * The last ending. The picture is loaded with its palette at 0xf0 and the whole
 * of 0 to 0xef is set to black first, so only the sixteen colours the picture
 * brought are lit - that is the night, and it costs one loop rather than a
 * second image.
 *
 * The monster is a one-entity scene walking left from x = 0xfa, one pixel a
 * frame, and the two things that happen to it are a switch on its position:
 *
 *   x == 0xdc   it is heard   (sound 0x18)
 *   x == 0x78   fade_direction goes to -1 and the scene starts to fade
 *
 * The original compiles that to a two-entry table at cs:0xb62b comparing both
 * halves of the 32-bit x. The loop ends when fade_level reaches 0, so nothing
 * counts frames - the fade is the clock.
 */
void far cutscene_night_monster(void)
{
    desc_t   pic;
    scene_t  scene;
    int16_t  i;
    uint8_t  plane;

    clear_vram();
    fade_level     = 0xf;                          /* 0x100fe */
    fade_direction = 0;
    for (i = 0; i < 0xf0; i++)                     /* 0x1010f */
        palette_set_black((uint8_t) i);

    if (!resource_load(&pic, 0x4d, 0x32, 0xf0, 1, 0xff, 1))   /* 0x10137 */
        return;
    palette_apply_gamma();
    palette_upload();

    scene_alloc(&scene, 1);                        /* 0x10154 */
    scene_add(&scene, 0xfa, 0x80, 0x39, 5);        /* 0x1016a */
    scene.entities[0].f14 = (int8_t) 0xff;         /* 0x10173 - walking left */

    /* 0x1019b. The doorway is stamped into the picture rather than drawn every
     * frame: it never moves, and this way the monster can be clipped by it. */
    stamp_sprite_into(0xfa, 0x82,
                      &sprite_table.base[anim_script[5][0]], &pic);

    image_fill_rect(0, 0x80, 0x140, 0xc8, &pic, 0);   /* 0x101b4 - the ground */
    image_fill_rect(0, 0x50, 0x32,  0x80, &pic, 0);   /* 0x101cb */
    image_fill_rect(0, 0x4b, 0x35,  0x50, &pic, 0);   /* 0x101e1 */

    while (fade_level) {                           /* 0x102a5 */
        for (plane = 0; plane < 4; plane++) {
            set_plane(plane);
            blit_rows(&pic, viewport_screen, 0);
            draw_entities(&scene, viewport_screen, 0);
        }
        page_flip();
        animate_scene(&scene);
        scene.entities[0].x -= 1;                  /* 0x1024d, a 32-bit sub */

        switch (scene.entities[0].x) {             /* 0x10266, cs:0xb62b */
        case 0x78:  fade_direction = -1;  break;   /* 0x10288 */
        case 0xdc:  sound_play_guarded(0x18, 1);  break;   /* 0x1028f */
        default:    break;
        }
        palette_fade_step(0);                      /* 0x1029f */
    }

    free(scene.entities);                          /* 0x102b5, lcall 0:0xedb */
    resource_release(&pic);
}

/* ================================================== 0x0f9fd: cutscene_doorstep
 *
 * The third ending, and three pictures in a row: the doorstep (0x37), the door
 * opening behind a wipe (0x38), and what is behind it (0x39). Every one of them
 * is optional - each load is tested and its whole section skipped if the egg
 * does not have it - which is why this reads as three independent blocks rather
 * than one sequence.
 *
 * One 320x200 image is allocated up front and all three are loaded into it in
 * turn, so `desc` is reused and released once at the end.
 *
 * The middle one is the only animation: a two-row strip of picture 0x38 is
 * blitted at row `curtain` while `curtain` walks from 0xc6 down to 0xb, which
 * uncovers it from the bottom up. `blit_rows`' third argument is the source
 * row, and it is the same counter, so the strip always shows the part of the
 * picture that belongs at that height.
 */
void far cutscene_doorstep(void)
{
    desc_t     desc;
    viewport_t strip;
    int16_t    o = viewport_screen.top;            /* si */
    uint8_t    curtain = 0xc6;                     /* [bp-2] */
    uint8_t    page, plane;

    desc.w = 0x140;                                /* 0x0fa08 */
    desc.h = 0xc8;
    alloc_image(&desc, 0, 0, 0, 1);                /* 0x0fa20 */
    clear_vram();

    /* the doorstep */
    if (resource_load_at(&desc, 0x4d, 0x37, 0, 0xa, 0xff)) {   /* 0x0fa3f */
        palette_apply_gamma();
        palette_upload();
        for (page = 0; page < 2; page++) {
            for (plane = 0; plane < 4; plane++) {
                set_plane(plane);
                blit_rows(&desc, viewport_screen, 0);
            }
            page_flip();
        }
        hold_frames(0x1e);                         /* 0x0fa9c */
    }

    /* the door, uncovered from the bottom */
    if (resource_load_at(&desc, 0x4d, 0x38, 0x18, 0xd, 0xff)) {  /* 0x0fab3 */
        palette_apply_gamma();
        palette_upload();
        sound_play_guarded(0x67, 4);               /* 0x0facd */
        while (curtain > 0xa) {                    /* 0x0fb3f */
            make_rect(&strip, o + curtain, o + curtain + 2, o, o + 0x140);
            for (plane = 0; plane < 4; plane++) {
                set_plane(plane);
                blit_rows(&desc, strip, curtain);  /* source row == height */
            }
            curtain--;
            page_flip();
        }
        stop_sound_by_id(4);                       /* 0x0fb45, via 0x1462:0x196 */
        sound_play_guarded(0x66, 1);
        hold_frames(0x32);
    }

    /* and what was behind it */
    if (resource_load_at(&desc, 0x4d, 0x39, 0x18, 0xd, 0xff)) {  /* 0x0fb6f */
        sound_play_guarded(0x0d, 1);
        for (page = 0; page < 2; page++) {
            for (plane = 0; plane < 4; plane++) {
                set_plane(plane);
                blit_rows(&desc, viewport_screen, 0);
            }
            page_flip();
            /* After the first page, not before it: the picture is already on
             * screen when the palette arrives, so it appears rather than
             * fading. 0x0fbc8. */
            if (page == 0) {
                palette_apply_gamma();
                palette_upload();
            }
        }
        hold_frames(0x96);
    }

    resource_release(&desc);                       /* 0x0fbed */
    dac_set_black(0, 0);
}

/* ------------------------------------------------ 0x0fc01: the bird and its
 * reflection
 *
 * Two calls, both from the landing, and it is one gull drawn twice: sprite 2 in
 * the sky band at `y`, and sprite 3 in the sea band at `y - 0x95` - which is the
 * band's own height, so the second is the first mirrored into the water below
 * the horizon. Each is clipped to its own band, which is what stops the
 * reflection appearing in the sky and the bird in the sea.
 *
 * Past the waterline at 0x9a the bird is gone and only sprite 3 is drawn, at the
 * waterline itself and clipped to the whole screen instead of to a band.
 */
static void far landing_bird(int16_t x, int16_t y, table_t far *sprites,
                             viewport_t far *sky, viewport_t far *sea)
{
    int16_t index;

    if (y < 0x9a) {                                /* 0x0fc0f */
        index = 2;
        draw_sprite(&index, x, y, sprites, sky, 0);            /* 0x0fc34 */
        index = 3;
        draw_sprite(&index, x, y - 0x95, sprites, sea, 0);     /* 0x0fc5c */
    } else {
        index = 3;
        draw_sprite(&index, x, 0x9a, sprites, &viewport_screen, 0);  /* 0x0fc81 */
    }
}

/* ============================================ 0x0fc8b: cutscene_rocket_landing
 *
 * The second ending: the rocket comes down over the sea while clouds drift past
 * and two gulls cross. 255 frames, four planes each, and every position is an
 * expression in the frame counter - there is no state but the counter.
 *
 * `o` is `viewport_screen.top`, and the original uses that one word for BOTH
 * axes: the centred 320x200 window inside 360x240 has top and left equal at 20,
 * and both are 0 in 320-wide mode, so one read does for both.
 *
 * The two bands are the picture: rows 0..0x95 are the sky, drawn from a
 * four-pixel-wide tile, and 0x95..0xab the sea, drawn from a 32-wide one with a
 * ripple that grows with the frame. See blit_warped in sdl_io.c.
 *
 * The two little index cycles are locals with initialisers - Borland compiles
 * those to a memcpy from a template in DGROUP, which is what 0x0fca7 and 0x0fcb9
 * are, and not something the source said.
 */
void far cutscene_rocket_landing(void)
{
    uint8_t     smoke[8]   = { 5, 5, 6, 6, 7, 7, 6, 6 };            /* d+0x2183 */
    uint8_t     splash[16] = { 5, 5, 5, 5, 5, 6, 6, 6,
                               7, 7, 7, 7, 7, 6, 6, 6 };            /* d+0x218b */
    viewport_t  sky_band, sea_band;
    desc_t      sky, sea;
    table_t     sprites;
    int16_t     index;
    int16_t     o     = viewport_screen.top;       /* si, 0x0fc97 */
    uint8_t     frame = 0;                         /* [bp-8] */
    uint8_t     plane;                             /* [bp-7] */

    make_rect(&sky_band, o, o + 0x95, o, o + 0x140);       /* 0x0fcd2 */
    make_rect(&sea_band, o + 0x95, o + 0xab, o, o + 0x140);/* 0x0fcf1 */

    if (!resource_load(&sky, 0x4d, 0x33, 0, 1, 0xff, 1))   /* 0x0fd0a */
        return;
    if (!resource_load(&sea, 0x4d, 0x34, 0, 1, 0xff, 1)) { /* 0x0fd2a */
        resource_release(&sky);                            /* 0x0fd34 -> 0x10098 */
        return;
    }
    sprite_set_load(0x34, 0x53, &sprites, 0xff);           /* 0x0fd44 */
    clear_vram();
    palette_apply_gamma();
    palette_upload();

    do {
        for (plane = 0; plane < 4; plane++) {              /* 0x0fd56 */
            set_plane(plane);

            blit_warped(&sky, sky_band, 0, 3);             /* 0x0fd7f */
            blit_warped(&sea, sea_band, frame, 0x1f);      /* 0x0fd9e */

            /* 0x0fdcd. Five clouds, each drifting left at half a pixel a frame
             * from its own start, all sitting on the horizon at y = 0x94. */
            index = 0;
            draw_sprite(&index, (int16_t) ((0x100 - frame) >> 1), 0x94,
                        &sprites, &sky_band, 0);
            index = 4;
            draw_sprite(&index, (int16_t) ((0x190 - frame) >> 1), 0x94,
                        &sprites, &sky_band, 0);
            index = 1;
            draw_sprite(&index, (int16_t) ((0x258 - frame) >> 1), 0x94,
                        &sprites, &sky_band, 0);
            index = 4;
            draw_sprite(&index, (int16_t) ((0x2f8 - frame) >> 1), 0x94,
                        &sprites, &sky_band, 0);
            index = 0;
            draw_sprite(&index, (int16_t) ((0x2c2 - frame) >> 1), 0x94,
                        &sprites, &sky_band, 0);

            /* 0x0fe8f. The sixth falls until frame 0x30 and then sits on the
             * horizon with the rest. */
            index = 4;
            if (frame < 0x30)
                draw_sprite(&index, (int16_t) ((0x212 - frame) >> 1),
                            frame + 0x64, &sprites, &sky_band, 0);
            else
                draw_sprite(&index, (int16_t) ((0x212 - frame) >> 1), 0x94,
                            &sprites, &sky_band, 0);

            /* 0x0ff29. The rocket: two pixels down and one left a frame,
             * starting well above the screen, and clipped to the whole window
             * rather than to a band so it can cross the horizon. */
            index = 2;
            draw_sprite(&index, (int16_t) (0x226 - frame),
                        (int32_t) (frame * 2 - 0x190),
                        &sprites, &viewport_screen, 0);

            landing_bird((int16_t) (0xdc - frame), (int16_t) (frame * 2),
                         &sprites, &sky_band, &sea_band);          /* 0x0ff52 */
            landing_bird((int16_t) (0x190 - frame), (int16_t) (frame * 2 - 0x28),
                         &sprites, &sky_band, &sea_band);          /* 0x0ff7e */

            /* 0x0ffaa. Two boats on the waterline, and then the smoke and the
             * splash, each stepping through its own cycle - eight frames long
             * and sixteen, which is why they never quite line up. */
            index = 3;
            draw_sprite(&index, (int16_t) (0x14a - frame), 0x9a,
                        &sprites, &viewport_screen, 0);
            index = 3;
            draw_sprite(&index, (int16_t) (0x50 - frame), 0x9a,
                        &sprites, &viewport_screen, 0);
            index = smoke[frame & 7];                      /* 0x10006 */
            draw_sprite(&index, (int16_t) (0x1d6 - frame), 0x9a,
                        &sprites, &viewport_screen, 0);
            index = splash[frame & 0xf];                   /* 0x10045 */
            draw_sprite(&index, (int16_t) (0x1ea - frame), 0x9a,
                        &sprites, &viewport_screen, 0);
        }
        frame++;                                           /* 0x10065 */
        page_flip();
    } while (frame < 0xff);                                /* 0x1006c */

    dac_set_black(0, 0);                                   /* 0x1007a */
    sprite_set_free(&sprites);
    resource_release(&sea);
    resource_release(&sky);
}

/* ------------------------------------------- 0x0f8bd: the photograph's fade
 *
 * cutscene_photos slams the whole DAC to white and then calls this once a frame
 * for 150 frames. It is not palette_fade_step: that one fades toward black and
 * this fades *from white*, which is what a photograph developing looks like and
 * why the flash is part of the effect rather than a glitch.
 *
 * Per channel, `(c * fade_level) >> 6` plus `(0xf - fade_level) * 4`: the
 * colour scaled up as the white washes out. At fade_level 0 every channel is
 * 0x3c, near the DAC's maximum of 0x3f. The loop only runs below 0xf - the
 * moment fade_level reaches it the real palette is uploaded and fade_direction
 * is cleared, so that upload is both the last step of the fade and its end.
 *
 * palette_apply_gamma runs first, so what gets scaled is the gamma-corrected
 * palette at d+0x10e1 rather than the buffer it came from.
 *
 * All of the arithmetic is 8-bit except the multiply, which is a signed 16-bit
 * imul of the byte by fade_level before the shift.
 */
void far photo_fade_step(void)
{
    int16_t i;

    fade_level += fade_direction;                  /* 0x0f8c5 */
    palette_apply_gamma();

    if (fade_level >= 0xf) {                       /* 0x0f8cd */
        palette_upload();
        fade_direction = 0;                        /* 0x0f8d8 */
        return;
    }

    outp(0x3c8, 0);                                /* 0x0f8df */
    for (i = 0; i < 768; i++) {                    /* 0x0f90a */
        uint8_t white = (uint8_t) ((uint8_t) (0xf - fade_level) << 2);
        int16_t lit   = (int16_t) (palette_stored[i] * fade_level) >> 6;

        outp(0x3c9, (uint8_t) (white + (uint8_t) lit));   /* 0x0f903 */
    }
}

/* ----------------------------------------------- 0x0f913: cutscene_photos */
void far cutscene_photos(void)
{
    desc_t  desc;
    int16_t id, page, plane, i;

    for (id = 0x3a; id <= 0x3c; id++) {    /* three polaroids, one per screen */
        if (!resource_load(&desc, 0x4d, id, 0, 0, 0xff, 1))
            continue;
        clear_vram();
        outp(0x3c8, 0);                    /* the whole DAC to white: the flash */
        for (i = 0; i < 255; i++) {
            outp(0x3c9, 0x3f);  outp(0x3c9, 0x3f);  outp(0x3c9, 0x3f);
        }
        sound_play_guarded(0x68, 1);
        fade_level = 0;  fade_direction = 1;
        for (page = 0; page < 2; page++) {
            for (plane = 0; plane < 4; plane++) {
                set_plane(plane);
                blit_rows(&desc, viewport_screen, 0);
            }
            page_flip();
        }
        for (i = 0; i < 150; i++) {        /* hold, fading in */
            page_flip();
            photo_fade_step();             /* 0x0f8bd */
        }
        resource_release(&desc);
    }
}

/* ------------------------------------------------------- 0x102d7: show_splash
 *
 * (text, frames): a line of the large font faded in, held for `frames` or until
 * a key, faded out. Holds a four-plane loop of its own.
 *
 * It takes a *string*, not a picture: it loads the banner sprite set, lays the
 * line out into a 320x24 image of its own and shows that. main's first call
 * draws nothing because the string it passes, at d+0x28ff, is empty - which is
 * what the four blank planes seen in that frame were.
 */
void far show_splash(const char far *text, int16_t frames)
{
    viewport_t  a;
    desc_t      b;
    table_t     set;
    int16_t    si = 0, di = frames, plane;

    make_rect(&a, 80, 104, screen_x0, screen_x0 + 320);   /* centred */
    image_alloc(&b, 320, 24);                      /* 0x08885 */
    sprite_set_load(1, 0x53, &set, 0xff);          /* 0x0615a - the large font */
    palette_set_black(0);                       /* 0x056f7 */
    draw_banner(text, &set, 0x12, &b, 0, 0x1c);    /* 0x0b5cf */
    clear_vram();
    fade_direction = 1;  fade_start_colour = 0;
    do {
        input_poll(320, 200);
        if (si == di || last_key || g_18e5)      /* timeout, key or button */
            fade_direction = -1;
        si++;
        for (plane = 0; plane < 4; plane++) {
            set_plane(plane);
            blit_rows(&b, a, 0);
        }
        page_flip();
        palette_fade_step(0);
    } while (fade_level != 0);
    resource_release(&b);  sprite_set_free(&set);
}

/* ------------------------------------------------ 0x11c75: episode_end_gate
 *
 * Finds the episode whose last level is the one just finished, shows that
 * episode's own splash - "That's enough training", "EPISODE COMPLETED!" - and
 * returns that record's terminator flag. So it answers "was that the FINAL
 * episode", which is what gates the homecoming.
 */
int16_t far episode_end_gate(int16_t number, int16_t egg)
{
    int16_t i, flag = 0;

    for (i = 0; i < episode_count; i++) {
        if (episode_index[i].last != number)  continue;
        if (episode_index[i].egg  != egg)    continue;

        sound_play_guarded(0x1a, 1);
        show_splash(menu_text[47], 100);            /* "EPISODE COMPLETED!" */
        /* 0x11ce9 pushes the record's +0xa, which is `ordinal` and not `name`.
         * The stub took a char far * and the call site had been written to
         * match it, so the page was selected by a pointer. */
        episode_page(episode_index[i].ordinal, egg);       /* 0x11bee */
        flag = episode_index[i].terminator;         /* +0xc: the answer */
    }
    return flag;
}

/* --------------------------------------------------- 0x1271b: the menu loop
 *
 * Draws a screen, and handles the two action codes that mean "keep the menu up":
 * the idle timeout, and a request to play a demo. Everything else it hands back
 * to game_main. The two branches below are the same code twice in the original.
 */
item_t far *menu_screen_driver(menu_t far *menu, void far *a, int16_t b)
{
    item_t far   *r;
    int16_t       leave;                           /* di */
    int16_t       saved;                           /* si, across the demo call */

    do {                                           /* 0x12723 */
        r = run_screen(menu, a, b);                /* 0x12733 -> 0x0c716 */
        leave = 0;

        switch (r->action) {
        case 0x0a:                                 /* idle: 500 frames untouched */
            if (attract_choice) {                  /* [0x21ae] */
                show_attract_screen(400);          /* 0x0b9fc */
            } else if (pick_random_demo()) {       /* 0x126db: rand() % [0x2038] */
                level_load();                  /* 0x088fa */
                free(level_text);
                scroll_shift = 5;  g_1ffc = 0;
                saved = cheat_state[2];  cheat_state[2] = 0;     /* switched off for the demo */
                run_level(1);                  /* 0x1279d - it IS the game */
                cheat_state[2] = saved;
                free(script_table);  free(event_table);  free(tool_event_table);
                /* Not the original's, which leaves the counts standing and
                 * the pointers dangling. Nothing outside a demo reads them
                 * and every demo reloads them, so this changes nothing -
                 * except that the frame above cannot walk freed memory. */
                script_table = NULL;  script_count = 0;
                release_sounds();
            } else {
                show_splash("DEMO MISSING", 100);  /* 0x1287e, DGROUP+0x26bd */
            }
            attract_choice = !attract_choice;      /* 0x127ee: screen, demo, ... */
            break;

        case 0x15:                                 /* play the demo named */
            if (load_demo(r->param)) {             /* 0x1240f */
                /* 0x12811-0x1283a is the idle branch above again, and that is
                 * now checked rather than assumed: the 0x38 bytes from 0x12774
                 * and from 0x12811 are identical except for the two relative
                 * call displacements, which have to differ because the calls are
                 * made from different addresses. So the same lines, written out
                 * rather than elided - a demo picked by name has to free what a
                 * demo picked at random frees. */
                level_load();                      /* 0x12817 */
                free(level_text);
                scroll_shift = 5;  g_1ffc = 0;
                saved = cheat_state[2];  cheat_state[2] = 0;
                run_level(1);                  /* 0x1283a */
                cheat_state[2] = saved;
                free(script_table);  free(event_table);  free(tool_event_table);
                /* Not the original's, which leaves the counts standing and
                 * the pointers dangling. Nothing outside a demo reads them
                 * and every demo reloads them, so this changes nothing -
                 * except that the frame above cannot walk freed memory. */
                script_table = NULL;  script_count = 0;
                release_sounds();
            } else {
                show_splash("DEMO MISSING", 100);
            }
            break;

        default:
            leave = 1;                             /* 0x1288d */
            break;
        }
    } while (!leave);                              /* 0x12892 */

    return r;                                      /* dx:ax */
}

/* ------------------------------------------------- 0x13676: the game itself
 *
 * A menu interpreter with the game as one of its cases. `menu` is the current
 * menu descriptor and most action codes only change it. The switch compiles to
 * the jump table at 0x13a70, twenty words, codes 1..20.
 */
void far game_main(menu_t far *menu)               /* main passes &main_menu */
{
    char          buf[0x326];      /* sprintf's target, so char and not uint8_t */
    item_t far   *r;
    int16_t       running = 1;                     /* si, set at 0x1367e */
    int16_t       i;

    do {
        r = menu_screen_driver(menu, &buf[0], 1);  /* 0x1368f, five words */

        switch (r->action) {                       /* 0x1369e, table at 0x13a70 */

        case 18:  menu = r->link;         break;   /* a submenu is data, not code */
        case 4:   running = 0;            break;   /* QUIT */
        case 14:  register_screen();      break;   /* 0x13096 */
        case 7:   show_readme_section(r->param);   break;
        case 5:   save_game_screen();  menu = &menu_play;  break;   /* 0x13298 */
        case 6:   load_game_screen();  menu = &menu_play;  break;   /* 0x12951 */
        case 3:   high_score_screen();  menus_after_game();
                  menu = &main_menu;                       break;

        case 20:                                   /* 0x136fe: MOUSE BUTTONS */
            /* DONE! on the MOUSE BUTTONS screen. It is the only way off that
             * screen - the menu has no submenu item at all - so refusing here
             * is what keeps the user on it until the three are distinct. */
            if (button_map[0] == button_map[1] || button_map[0] == button_map[2]
                || button_map[1] == button_map[2])
                show_splash(extra_text[9], 200);   /* "INVALID SETTINGS!" */
            else
                menu = &menu_mouse;                /* 0x13737 */
            break;

        case 12:                                   /* 0x136cb: RESOLUTION */
        case 13:
            clear_vram();
            set_mode_x(r->action == 13);
            dac_set_black(0, 0);
            menu = &menu_video;
            break;

        case 1:                                    /* START: unpack the episode */
            g_1ffc = 0;  g_1ffa = 0;           /* 0x1377b */
            menu = &menu_play;
            i = r->param;                          /* the episode ordinal */
            level_attempted   = episode_index[i].first;
            episode_egg_index = episode_index[i].egg;
            shareware_limit   = egg_files[episode_egg_index].limit;   /* +0x10 */
            /* 0x137da. What starting a game means: five lives, no score, and
             * the two menu items menus_resume relabels. These five stores were
             * missing, which is why the HUD's lives counter read 00 - a byte
             * scan of the whole image for a store to [0x2034] is what found
             * them, after a scan of the recognised function extents did not. */
            g_21a3 = 1;
            lives  = 5;                            /* 0x137e0 */
            score  = 0;                            /* 0x137e6 */
            next_life = 0x1388;                    /* 0x137ec - 5000 */
            menus_resume();                        /* 0x128a5 */
            /* FALL THROUGH into the play loop */

        case 2:                                    /* 0x137f6: play, tally, repeat */
            for (;;) {
                if (g_1ffc) {
                    sound_play_guarded(0x29, 1);
                    show_splash(menu_text[40], 200);   /* "SECRET LEVEL!" */
                }
                /* 0x13828. Non-zero does not leave the loop - it re-runs the
                 * screens, because the only thing that returns non-zero is the
                 * level-select key, and the screens have to be built again for
                 * whatever level was just picked. */
                while (level_screens(g_21a3))    /* 0x1102a, 0x13835 jne back */
                    ;
                scroll_shift = 2;

                if (shareware_limit < level_attempted      /* 0x13841 */
                    && !registered && !g_1ffc) {
                    level_free();                  /* 0x13856 */
                    egg_load_one(0xfc, 0x48, 0xff);
                    menu = &main_menu;
                    high_score_screen();  menus_after_game();
                    goto play_tail;                        /* 0x139a9 */
                }

                if (!run_level(0)) {           /* 0x1387e: the run ended badly */
                    g_21a3 = 0;                        /* 0x139b2 */
                    /* 0x139b8. Only the decrement is guarded - the splash, and
                     * the game-over that follows it, are outside. [0x50b] is
                     * therefore "this attempt was free", not "say nothing". */
                    if (!cheat_state[3])
                        --lives;                       /* 0x139bf, [0x2034] */
                    sprintf(buf, "%s: %i", menu_text[53], lives);  /* "LIVES LEFT" */
                    show_splash(buf, 100);             /* 0x139f6 */
                    release_sounds();                  /* 0x139fc */
                    if (lives == 0) {                  /* 0x13a01: GAME OVER */
                        menu = &main_menu;             /* 0x13a08 */
                        sound_play_guarded(0x16, 1);
                        show_resource(0x4d, 6, 50, 0xff);
                        /* The same two calls the ending uses. Without them the
                         * game-over screen was the last thing the run did, and
                         * the hall of fame never came up. */
                        high_score_screen();           /* 0x13a2c */
                        menus_after_game();            /* 0x13a30 */
                    }
                    goto play_tail;                    /* 0x139b0 */
                }

                if (g_1ffc || g_1ffe)          /* 0x1388b, 0x13895 */
                    goto play_tail;            /* 0x13892, 0x1389c */

                g_21a3 = 1;                      /* the level was completed */
                sound_play_guarded(2, 1);
                show_resource(0x4d, 2, 50, 0xff);  /* the BONUS SCREEN */
                bonus_screen();      /* 0x0becb - the bonus tally */

                /* 0x138c4. The extra life, and the only place lives go up.
                 * next_life starts at 5000 and moves up by 5000 each time, so
                 * it is one life per 5000 points however the score arrives -
                 * and the check is here, after the bonus screen, so the
                 * bonuses count toward it.
                 *
                 * `>` and not `>=`, which looks like a slip and is not: 0x138cb
                 * is `jle` over the whole block, so a score of exactly 5000
                 * does not earn the life and 5001 does. Signed, too - `jle`
                 * rather than `jbe` - which is what both declarations say.
                 * Leave it alone. */
                if (score > next_life) {
                    lives++;
                    sound_play_guarded(0x20, 1);
                    show_splash(menu_text[46], 100);   /* "EXTRA LIFE!" */
                    next_life += 0x1388;
                }
                release_sounds();                      /* 0x138ff */

                /* The ending. Only DUCKING HELL - level 80 - passes the gate,
                 * and only episode 0 has cutscenes behind it; the other
                 * episodes end on the hall of fame alone. */
                if (episode_end_gate(level_attempted, episode_egg_index)) {
                    if (episode_egg_index == 0) {               /* 0x1391a */
                        set_buffer(&buf[0]);
                        cutscene_rocket_space();                /* id 0x32 */
                        sound_play_loop(0x4a, ambience_volume, 0xff);
                        cutscene_rocket_landing();              /* ids 0x33/0x34 */
                        cutscene_doorstep();                    /* ids 0x37/0x38 */
                        cutscene_welcome_home();                /* id 0x36 */
                        release_sounds();
                        cutscene_photos();                      /* ids 0x3a-0x3c */
                        sound_play_loop(0x4a, ambience_volume, 0xff);
                        cutscene_night_monster();               /* the animation */
                        release_sounds();
                        dac_set_black(0, 0);
                        input_poll(320, 200);
                        set_buffer(&buf[0]);                    /* 0x13993 */
                    }
                    menu = &main_menu;                          /* 0x13999 */
                    high_score_screen();  menus_after_game();
                    goto play_tail;                             /* 0x139a9 */
                }
                level_attempted++;                 /* 0x139ab */

              play_tail:                           /* 0x13a33 */
                /* Every way out of a level lands here, including the game-over
                 * one. The original plays one level per pass through the
                 * switch and then lets the menu back in, which is what
                 * menus_resume is for: it relabels PLAY DUCKS' first item to
                 * PLAY NEXT LEVEL before the driver draws it again. */
                if (game_in_progress)
                    menus_resume();                /* 0x13a3a */
                if (!g_1ffc && !g_1ffe)            /* 0x13a3e, 0x13a48 */
                    break;                         /* 0x13a52 */
            }
            break;

        default:                                   /* 8-11, 15-17, 19 */
            break;
        }
    } while (running);                             /* 0x13a66 */
}

/* ---------------------------------------------- 0x13a98: load_animations
 *
 * The 'G' block, read once at startup: for each entity type a script of sprite
 * indices, then four bytes and a word of per-type state. Six arrays, all indexed
 * by type, and 111 is the most the block is allowed to hold - which is why they
 * are 112 long and end exactly where settings begins.
 *
 * The script is stored with its length; the 999 that ends it is put on here
 * rather than being in the file, so the reader and animate_scene agree without
 * either of them carrying a count.
 *
 * The second half has nothing to do with animation: it asks each open egg what
 * kind it is, and settles which of them the episodes come from. That has to
 * happen before build_episode_index, and it does - init calls them in that
 * order - because `contributes` is what decides whether an egg's records go
 * into the arrays or are read past and dropped.
 */
void far load_animations(void)
{
    int16_t types, n, i, j;

    if (!egg_find_block(0x47, 0, 0xff))
        fatal("No animation data", 0);             /* d+0x2763 */

    types = egg_read_word(egg_stream);
    if (types > 0x6f)
        fatal("Too many animations", 0);           /* d+0x2775 */

    for (i = 0; i < types; i++) {
        n = egg_read_word(egg_stream);
        anim_script[i] = malloc(((size_t) n + 1) * 2);
        for (j = 0; j < n; j++)
            anim_script[i][j] = egg_read_word(egg_stream);
        anim_script[i][n] = 0x3e7;                 /* 999 ends a script */

        anim_a[i]     = egg_read_byte(egg_stream);
        anim_b[i]     = egg_read_byte(egg_stream);
        anim_c[i]     = egg_read_byte(egg_stream);
        type_flags[i] = egg_read_byte(egg_stream);
        next_type[i]  = egg_read_word(egg_stream);
    }
    egg_block_end();

    /* 0x13bcf. The chosen egg's format version, kept where the register screen
     * can find it. */
    g_210b = egg_files[current_egg].version;

    /* Each egg's kind, out of its 'Z' block. An egg without one is kind 1. */
    for (i = 0; i < egg_file_count; i++) {
        if (egg_find_block(0x5a, 0xff, i)) {
            egg_files[i].kind = (uint8_t) (egg_read_byte(egg_stream) + 0xd0);
            egg_block_end();
        } else {
            egg_files[i].kind = 1;
        }
    }

    /* And which of them contributes episodes. Kind 2 - a complete set - counts
     * only if it is the chosen one; anything else counts if it is kind 0, which
     * is an add-on, or if it is the chosen one. */
    for (i = 0; i < egg_file_count; i++) {
        if (egg_files[i].kind == 2)
            egg_files[i].contributes = (current_egg == i);
        else
            egg_files[i].contributes = (egg_files[i].kind == 0
                                        || current_egg == i);
    }
}

/* ================================================= the indexes, 0x11547 on
 *
 * Three of them, all built once at startup out of every open egg's information
 * block: the episodes, the readme sections, and the rolling demos. The first two
 * share a record layout and a reader; the third is assembled here.
 * ======================================================================== */

/* 0x11547. The separator between the startup screen's sections: a newline, then
 * eighty dashes in grey.
 *
 * There is no newline after the dashes in the original, and there does not need
 * to be: eighty of them fill a DOS console row exactly, so the wrap is the line
 * break. docs/notes/accepting-is-not-answering.md is that assumption failing -
 * when the cursor was left in the wrong column the rules "started mid-line and
 * ran over the messages". A terminal that is not eighty columns wide has no such
 * wrap, so the newline below stands in for it.
 *
 * The two printers are not the same one. 0:0x3b4d is printf and goes to the
 * stream - "Using file %s - %i slices" comes out of it, and that is the one line
 * a run's captured stdout holds. 0:0x2012 is cprintf, which writes straight into
 * 0xb8000, which is why none of the rest of the startup screen appears there.
 */
void far console_rule(void)
{
    int16_t i;

    printf("\n");                                  /* d+0x2555, 0:0x3b4d */
    set_text_colour(7);
    for (i = 0; i < 0x50; i++)
        printf("-");                               /* d+0x2557, 0:0x2012 */
    printf("\n");                                  /* not in the original: see
                                                    * above, it is the wrap */
}

/* 0x1157a. Reads records out of the open information block until the running
 * total says to stop, and hands back where it got to.
 *
 * `store` is what makes an egg that contributes nothing still cost its records:
 * with it clear every record is read into one scratch struct and dropped, so the
 * stream still advances past the names, and the total is wound back by however
 * many were skipped. With it set the records land in the array.
 */
int16_t far read_index(episode_t far *array, int16_t start, int16_t far *total,
                       int16_t egg, int16_t store)
{
    episode_t      scratch;                        /* [bp-0x14] */
    episode_t far *rec = &scratch;
    int16_t        i = start, ordinal = 0;

    while (i < *total) {
        if (store)
            rec = &array[i];

        rec->name       = egg_read_string(egg_stream);
        rec->first      = egg_read_byte(egg_stream);
        rec->last       = egg_read_byte(egg_stream);
        rec->egg        = egg;
        rec->ordinal    = ordinal++;
        rec->terminator = (*total - 1 == i);       /* set on the last one */
        i++;
    }
    if (!store)
        *total -= i - start;                       /* 0x11639 */
    return store ? i : start;
}

/* ------------------------------------------- 0x11657: build_episode_index
 *
 * Two passes over every open egg's information block. The first counts, so the
 * two arrays can be sized; the second reads the records in and prints the egg's
 * banner - "MAIN.EGG: Full Version" and its credit - which is what made this
 * routine findable in the first place.
 *
 * The strings come out through egg_read_string, so the +1 shift the directory
 * uses is already off by the time they land in a record.
 */
void far build_episode_index(void)
{
    int16_t   episodes_seen = 0;                   /* [bp-6]  */
    int16_t   readmes_seen  = 0;                   /* [bp-8]  */
    int16_t   episode_at, readme_at;               /* [bp-2], [bp-4] */
    int16_t   i, j, n;
    char far *label;
    char far *credit;

    episode_count = 0;
    console_rule();
    set_text_colour(15);
    printf("Building episode index...");           /* d+0x25b1, cprintf */
    console_rule();
    printf("\n");                                  /* d+0x25cb, printf */

    /* Pass one: the header of each egg's information block - version, shareware
     * limit, how many episodes and how many readme sections. */
    for (i = 0; i < egg_file_count; i++) {
        if (!egg_find_block(0x5a, 1, i))
            fatal("No information block in loaded EGG file", egg_files[i].name);

        egg_files[i].version = egg_read_byte(egg_stream);
        if (egg_files[i].version < 4 || egg_files[i].version > 6)
            fatal("Not a data file for this version of Ducks",
                  egg_files[i].name);

        egg_files[i].limit = egg_read_byte(egg_stream);
        n = egg_read_byte(egg_stream);
        if (egg_files[i].contributes)
            episode_count += n;
        readme_count += egg_read_byte(egg_stream);
        egg_block_end();
    }

    episode_index = malloc((size_t) episode_count * sizeof *episode_index);
    if (!episode_index)
        fatal(out_of_memory, 0);
    readme_index = malloc((size_t) readme_count * sizeof *readme_index);
    if (!readme_index)
        fatal(out_of_memory, 0);

    /* Pass two: the records themselves, and the banner. */
    episode_at = 0;
    readme_at  = 0;
    for (i = 0; i < egg_file_count; i++) {
        egg_find_block(0x5a, 1, i);
        egg_read_byte(egg_stream);                 /* the version again */
        egg_read_byte(egg_stream);                 /* and the limit */
        episodes_seen += egg_read_byte(egg_stream);
        readmes_seen  += egg_read_byte(egg_stream);

        episode_at = read_index(episode_index, episode_at, &episodes_seen, i,
                                egg_files[i].contributes);

        label  = egg_read_string(egg_stream);
        credit = egg_read_string(egg_stream);
        set_text_colour(14);  printf("%s: ", egg_files[i].name);
        set_text_colour(15);  printf("%s", label);
        set_text_colour(7);   printf("\r\n%s\r\n", credit);
        free(label);
        free(credit);

        /* One byte says whether this egg is a complete set. The first egg has
         * to be one and the rest must not be, so the test is against whether
         * this is the first - and the two refusals are a two-entry table of
         * messages at d+0x219b indexed the same way. */
        n = egg_read_byte(egg_stream);
        if (n == (i != 0))
            fatal(i != 0 ? "Attempting to load more than one primary EGG"
                         : "Primary EGG must be a complete set of data",
                  egg_files[i].name);

        egg_files[i].id = egg_read_string(egg_stream);
        for (j = 0; j < i; j++)
            if (strcmp(egg_files[i].id, egg_files[j].id) == 0)   /* 0:0x4215 */
                fatal("Same EGG loaded twice", 0);

        readme_at = read_index(readme_index, readme_at, &readmes_seen, i, 1);

        egg_files[i].demos = egg_read_byte(egg_stream);
        if (egg_files[i].contributes)
            g_2038 = (uint8_t) (g_2038 + egg_files[i].demos);
        egg_files[i].demo_base = g_2038;
        egg_block_end();
    }

    /* And the demo index, which is not read out of the eggs at all: one record
     * per demo per contributing egg, its name made up from the egg's. */
    if (g_2038 == 0)
        fatal("No valid rolling demos found", 0);

    demo_index = malloc((size_t) g_2038 * sizeof *demo_index);
    if (!demo_index)
        fatal(out_of_memory, 0);

    n = 0;
    for (i = 0; i < egg_file_count; i++) {
        if (!egg_files[i].contributes)
            continue;
        for (j = 0; j < egg_files[i].demos; j++) {
            demo_index[n].egg   = i;
            demo_index[n].first = j;
            demo_index[n].name  = malloc(0x14);
            if (!demo_index[n].name)
                fatal(out_of_memory, 0);
            sprintf(demo_index[n].name, "%s %i", egg_files[i].name, j);
            n++;
        }
    }
}

/* --------------------------------------------- 0x11efb: show_readme_section
 *
 * The readme viewer: one page of an 'H' block on the same blueprint background
 * the version page uses, with the section's name in the top left, "Page n of m"
 * in the top right, and what the keys do along the bottom.
 *
 *   n   which record of the readme index - game_main passes the item's param
 *
 * A section's pages are numbered by where they sit in the egg, not by position
 * in a list: DUCKS OVERVIEW is pages 1 to 3, THE OBJECTS is 11 to 19, and there
 * is nothing at 4 to 10. So "next page" is first + 1, and the ends of the range
 * are what the arrows are tested against.
 *
 * Two loops. The outer one is a page: load the blueprint, draw everything into
 * it, run the inner one, free it. The inner one is a frame, and it reads no
 * input at all while a fade is running.
 */
void far show_readme_section(uint8_t n)
{
    char     line[0x100];                          /* [bp-0x420] */
    desc_t   page;                                 /* [bp-0x320] */
    uint8_t  buffer[768];                          /* [bp-0x30a] */
    int16_t  at = readme_index[n].first;           /* si - the page number */
    int16_t  running = 1;                          /* [bp-2] */
    int16_t  has_next, has_prev;                   /* [bp-4], [bp-6] */
    int16_t  x, plane, alive;                      /* [bp-8], [bp-0xa], di */

    clear_vram();
    set_buffer(buffer);
    fade_level = 0;
    fade_direction = 1;                            /* only the first page fades */

    do {
        if (!resource_load(&page, 0x4d, 7, 0, 1, 0xff, 1))
            break;                                 /* 0x11f55 - and the original
                                                    * spins here rather than
                                                    * leaving, since running is
                                                    * still set */
        alive = 1;
        has_next = (readme_index[n].last  != at);
        has_prev = (readme_index[n].first != at);

        text_colour[0] = 1;
        load_text_page(&page, 0x48, (uint8_t) at, 0, 0x10e,
                       (int16_t) readme_index[n].egg);

        sprintf(line, "Page %i of %i",             /* d+0x266e */
                at - readme_index[n].first + 1,
                readme_index[n].last - readme_index[n].first + 1);

        text_colour[0] = 3;
        draw_string(&page, readme_index[n].name, 5, 3);
        draw_string(&page, line, 0x13b - text_width(line), 3);

        /* The three key hints, each replaced by an empty string when that key
         * would do nothing here. */
        sprintf(line, "%s%s%s",                    /* d+0x267c */
                has_prev ? menu_text[71] : "",     /* "UP: Last Page" */
                menu_text[72],                     /* "ESC: Done" */
                has_next ? menu_text[73] : "");    /* "DOWN: Next Page" */
        x = 0xa0 - text_width(line) / 2;
        text_colour[0] = 5;
        draw_string(&page, line, x, 0xbb);

        do {
            if (fade_direction == 0) {             /* 0x12107 - no input mid-fade */
                input_poll(0x140, 0xc8);

                /* A mouse button, or SPACE, means the obvious thing: turn the
                 * page if there is one, and otherwise leave. */
                if (g_18e5 || last_key == 0x20)
                    last_key = has_next ? 0x151 : 0x1b;

                switch (last_key) {
                case 0x2b: case 0x3d:              /* '+' and '=' */
                case 0x14d:                        /* right */
                case 0x150:                        /* down */
                case 0x151:                        /* page down */
                    if (has_next) {
                        at++;
                        alive = 0;
                        sound_play_guarded(3, 1);
                    } else {
                        sound_play_guarded(0x17, 1);   /* the refusal */
                    }
                    break;

                case 0x2d:                         /* '-' */
                case 0x148:                        /* up */
                case 0x149:                        /* page up */
                case 0x14b:                        /* left */
                    if (has_prev) {
                        at--;
                        alive = 0;
                        sound_play_guarded(3, 1);
                    } else {
                        sound_play_guarded(0x17, 1);
                    }
                    break;

                case 0x1b:                         /* ESC */
                    fade_direction = -1;
                    running = 0;
                    sound_play_guarded(5, 1);
                    break;

                case 0:                            /* nothing pressed */
                    break;

                default:
                    sound_play_guarded(0x17, 1);
                    break;
                }
            }

            for (plane = 0; plane < 4; plane++) {
                set_plane((uint8_t) plane);
                blit_rows(&page, viewport_screen, 0);
            }
            page_flip();
            palette_fade_step(0);

            if (fade_level == 0)                   /* 0x1222d - the fade out has
                                                    * finished, so let go */
                alive = 0;
        } while (alive);

        resource_release(&page);
    } while (running);

    set_buffer(default_buffer);
    input_poll(0x140, 0xc8);
}

/* ============================================ save and load, 0x12281 on
 *
 * Five slots, GAME1.SG to GAME5.SG, and both screens are the ordinary menu with
 * one entry per slot. The name in a slot is the name the player typed, read
 * straight back out of the file - so the list is built by opening all five.
 *
 * The file's strings carry the same +1 shift the eggs use, through the same
 * reader and a writer that puts it back on.
 * ======================================================================== */

/* 0x04ebb. A word, high byte first. */
void far write_word(int16_t v, FILE far *fp)
{
    fputc((v >> 8) & 0xff, fp);
    fputc(v & 0xff, fp);
}

/* 0x04fbd. A length and then the characters, each shifted up by one - the same
 * form egg_read_string takes apart. */
void far write_string(FILE far *fp, const char far *s)
{
    int16_t n = (int16_t) strlen(s);
    int16_t i;

    write_word(n, fp);
    for (i = 0; i < n; i++)
        fputc((s[i] + 1) & 0xff, fp);
}

/* 0x12281. One menu entry per save slot.
 *
 *   for_saving  the save screen offers an empty slot as "EMPTY SLOT n"; the load
 *               screen simply leaves it out, which is how a machine with nothing
 *               saved gets a LOAD SAVED GAME screen with only CANCEL on it.
 */
void far add_save_slots(menu_t far *m, int16_t for_saving)
{
    int16_t   action = for_saving ? 8 : 9;
    int16_t   i;
    FILE far *fp;
    char far *label;

    for (i = 1; i < 6; i++) {
        save_name[4] = (char) ('0' + i);
        fp = fopen(save_name, "rb");
        if (fp) {
            while (fgetc(fp) > 0)                  /* past "Ducks Saved Game..",
                                                    * and see load_settings on
                                                    * why the end of the file
                                                    * counts as well as the NUL */
                ;
            label = egg_read_string(fp);
            menu_add_action(m, label, action, &menu_always, (uint8_t) i);
            fclose(fp);
            free(label);
        } else if (for_saving) {
            label = malloc(0x14);
            sprintf(label, "%s %i", menu_text[36], i);      /* "EMPTY SLOT" */
            menu_add_action(m, label, action, &menu_always, (uint8_t) i);
            free(label);
        }
    }
}

/* 0x1239e. Which open egg a save belongs to, by the egg's own name.
 *
 *   id    what the save recorded
 *   name  the egg's file name, for the refusal
 *
 * Nothing here compares whole strings: it walks until the bytes differ, and a
 * stop on the terminator is what counts as a match. */
void far find_egg_by_id(const char far *id, const char far *name)
{
    int16_t i, j;

    episode_egg_index = 0xff;
    for (i = 0; i < egg_file_count; i++) {
        for (j = 0; id[j]; j++)
            if (id[j] != egg_files[i].id[j])
                break;
        if (id[j] == 0)
            episode_egg_index = i;
    }
    if (episode_egg_index == 0xff)
        fatal("A required data file isn't loaded", name);   /* d+0x268e */
}

/* 0x128a5. Relabels two menu items for a game that is now in progress: the main
 * menu's first item becomes BACK TO IT_, and PLAY DUCKS' first becomes RETRY
 * LEVEL or PLAY NEXT LEVEL depending on whether the last one was finished. Its
 * action changes with it, from "start a new game" to "carry on". */
void far menus_resume(void)
{
    game_in_progress = 1;                        /* a game is in progress */
    menu_set_text(&main_menu.item[0], menu_text[3]);       /* "BACK TO IT_" */
    menu_play.item[0].action = 2;
    menu_set_text(&menu_play.item[0],
                  g_21a3 ? menu_text[6]            /* "PLAY NEXT LEVEL" */
                         : menu_text[5]);          /* "RETRY LEVEL" */
}

/* 0x12916. d+0x205d is not an array of its own: it is the serial field of the
 * hall of fame, so this asks whether the game being loaded is already on the
 * board - and if it is, says so. See save_serial: the serial follows the game
 * rather than the file, so a save that was finished once carries the serial
 * score_set put on the board, and this finds it. Resource 0xfa is the page.
 *
 * Note it does not stop at the first match, and does not need to: one serial
 * can only be on the board once, so the loop shows the page at most once. */
void far save_note(int16_t serial)
{
    uint8_t i;

    for (i = 0; i < 10; i++)
        if (score_table[i].serial == serial)
            egg_load_one(0xfa, 0x48, 0xff);
}

/* --------------------------------------------- 0x12b6a: the name entry
 *
 * Typing a save's name, drawn as one more line of the menu that is still on
 * screen behind it - which is why the save screen hands its menu back without
 * letting go of the backdrop, and why the freeing at the end of this is the
 * freeing run_screen would otherwise have done.
 *
 *   buf     the text, always ending in a '`' that stands in for the cursor
 *   row     which line of the menu to draw it on
 *   escape  whether ESC abandons it. Returns zero only when it did.
 */
int16_t far name_entry(char far *buf, int16_t row, int16_t escape)
{
    int16_t ok = 1;
    int16_t plane;

    for (;;) {
        outp(0x3c8, 0);                            /* entry 0 black, every frame */
        outp(0x3c9, 0);
        outp(0x3c9, 0);
        outp(0x3c9, 0);
        colour_cycle = (colour_cycle + 1) & 0xf;
        input_poll(0x140, 0xc8);

        if (fade_direction != -1 && last_key > 0 && last_key < 0x100) {
            uint8_t n = (uint8_t) strlen(buf);

            last_key = toupper(last_key);          /* 0:0x184f */

            if (last_key == 0x0d) {                /* accept */
                if (n > 1) {
                    fade_direction = -1;
                    buf[n - 1] = 0;                /* drop the cursor */
                    sound_play_guarded(3, 1);
                } else {
                    sound_play_guarded(0x17, 1);   /* nothing typed yet */
                }
            } else if (last_key == 8) {            /* backspace */
                if (n > 1) {
                    buf[n - 2] = '`';
                    buf[n - 1] = 0;
                    sound_play_guarded(8, 1);
                } else {
                    sound_play_guarded(0x17, 1);
                }
            } else if (last_key == 0x1b && escape) {
                fade_direction = -1;
                ok = 0;
                sound_play_guarded(0x0f, 1);
            } else {
                /* Only what the large font has a sprite for. */
                static const char allowed[] =
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ,.!'-?/:";   /* d+0x26cd */
                const char far *p;
                int16_t         found = 0;

                for (p = allowed; *p; p++)
                    if (*p == last_key) {
                        found = 1;
                        break;
                    }
                if (!found) {
                    sound_play_guarded(0x17, 1);
                } else if (n < 0x14) {             /* twenty characters, no more */
                    buf[n - 1] = (char) last_key;
                    buf[n]     = '`';
                    buf[n + 1] = 0;
                    sound_play_guarded(0x12, 1);
                }
            }
        }

        draw_banner(buf, &menu_sprites,
                    menu_top + row * 8 + row * 16 + 0x2a, &backdrop, 0,
                    settings[4] ? bounce_table[colour_cycle] : bounce_table[2]);

        for (plane = 0; plane < 4; plane++) {
            set_plane((uint8_t) plane);
            compose_layer();
        }
        page_flip();
        palette_fade_step(0);

        if (fade_level == 0)
            break;
    }

    resource_release(&background);
    resource_release(&backdrop);
    sprite_set_free(&menu_sprites);
    set_buffer(default_buffer);
    return ok;
}

/* 0x11d1b. One row of the board: the score, a copy of the name, and which saved
 * game earned it. */
void far score_set(int16_t score, char far *name, int16_t slot)
{
    score_table[slot].score = score;
    str_copy(name, &score_table[slot].name);
    score_table[slot].serial = save_serial;
}

/* --------------------------------------------- 0x11d54: high_score_screen
 *
 * The end of a game: GAME OVER, the score, and then the board - with the name
 * entry in between if the score earned a place on it.
 *
 * `at` is the row the score belongs in; `di` is the row it may displace. The
 * two are different because a game that was saved and finished before is
 * already on the board: it is found by its serial and improved in place rather
 * than added again, and a game with no serial can only ever take the last row.
 */
void far high_score_screen(void)
{
    char    line[0x18];
    int16_t here = 9;                              /* di */
    int16_t at;                                    /* [bp-2] */
    int16_t i;

    show_splash(menu_text[48], 100);               /* "GAME OVER" */
    release_sounds();
    g_1ffc = 0;
    g_1ffe = 0;
    sprintf(line, "%s: %i", menu_text[50], score);        /* "SCORE" */
    show_splash(line, 100);

    if (save_serial) {                             /* already on the board? */
        for (here = 0; here < 10; here++)
            if (score_table[here].serial == save_serial)
                break;
        if (here == 10)
            here--;
    }
    /* On it already with a score this good: nothing to add. */
    if (save_serial && score_table[here].serial == save_serial
        && (uint16_t) score_table[here].score >= score)
        goto show;

    /* The lowest row this score does not beat, and then the one below it. */
    for (at = 9; at >= 0; at--)
        if ((uint16_t) score_table[at].score > score)
            break;
    at++;
    if (at > here)                                 /* not good enough */
        goto show;

    free(score_table[here].name);
    for (i = here; i > at; i--)
        score_table[i] = score_table[i - 1];

    sound_play_loop(0x27, ambience_volume, 0xff);
    high_score_name(line);
    score_set((int16_t) score, line, at);
    release_sounds();

show:
    show_attract_screen(400);
}

/* --------------------------------------------- 0x12dfb: high_score_name
 *
 * NEW HIGH SCORE!, the score, and then the name typed on the line the menu was
 * just showing. Same shape as the save screen: a menu run with `owns` clear so
 * the backdrop survives, and then name_entry over the top of it. ESC does not
 * abandon this one - a place on the board is not refusable.
 */
void far high_score_name(char far *buf)
{
    menu_t      m;
    int16_t     chosen;

    menu_reset(&m);
    menu_add_title(&m, menu_text[0]);              /* "NEW HIGH SCORE!" */
    menu_add_action(&m, menu_text[60], 0x0b, &menu_always, 0);  /* "ENTER YOUR NAME" */
    sprintf(buf, "%s: %u", menu_text[50], score);              /* "SCORE" */
    menu_add_action(&m, buf, 0x0b, &menu_never, 0);
    m.background = 7;

    menu_screen_driver(&m, &chosen, 0);

    strcpy(buf, "`");                              /* start empty, cursor only */
    name_entry(buf, chosen, 0);
    menu_free(&m);
}

/* --------------------------------------- 0x12edf: check_registration
 *
 * The name is hashed and the result, as six digits, is the key. Letters and the
 * space count and nothing else does - anything outside the alphabet at d+0x21b0
 * is passed over - and each one contributes its position times a multiplier that
 * changes as it goes. Both running values are kept inside their own modulus,
 * which is what stops the arithmetic from mattering beyond sixteen bits.
 *
 *   announce  whether to say so on screen. load_settings passes 0, because it is
 *             only re-checking what it has just read out of the file; the
 *             registration screen passes 1.
 *
 * One name is refused however good the key: MR. BLACK. Nothing here says why.
 *
 * Whatever the answer, the copy is unregistered first - so a wrong key entered
 * on a registered copy loses the registration.
 */
void far check_registration(char far *name, char far *key, int16_t announce)
{
    static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ ";  /* d+0x21b0 */
    char      want[8];                             /* [bp-8] */
    char far *fresh = 0;
    int32_t   acc = 0x8f12;                        /* [bp-0xc] */
    int32_t   mul = 0x30a8;                        /* [bp-0x10] */
    int16_t   i, j;

    for (i = 0; name[i]; i++) {
        int16_t at = -1;

        for (j = 0; alphabet[j]; j++)
            if (alphabet[j] == name[i])
                at = j;
        if (at < 0)                                /* not one of the 27 */
            continue;
        acc += (int32_t) (at + 1) * mul;
        acc %= 65530;
        mul += 0x6b0b;
        mul %= 0x406c;
    }
    acc++;
    sprintf(want, "%06u", (uint16_t) acc);         /* d+0x2704 */

    /* The copy is taken before the old name is let go, and that is a departure.
     * The original frees first and then copies from the caller's pointer - which
     * load_settings has just handed it as owner_name itself, the very thing being
     * freed. DOS's allocator leaves the bytes alone and hands the same block
     * back, so it works there; here the name came back as "F6". */
    if (strcmp(key, want) == 0 && strcmp(name, "MR. BLACK") != 0)
        str_copy(name, &fresh);

    if (registered) {                              /* 0x12fb7 */
        registered = 0;
        free(owner_name);
        owner_name = 0;
    }

    if (fresh) {
        if (announce)
            show_splash(menu_text[57], 100);       /* "GAME REGISTERED!" */
        registered = 1;
        owner_name = fresh;
        owner_key = (int16_t) (acc + 1);
    } else if (announce) {
        show_splash(menu_text[58], 100);           /* "INCORRECT KEY" */
        show_splash(menu_text[59], 100);           /* "REGISTRATION FAILED" */
    } else {
        printf("Invalid registration details...\r\n");          /* d+0x2713 */
    }
}

/* ----------------------------------------- 0x13096: register_screen
 *
 * Two menus in a row, each one item and a CANCEL, with the name entry over the
 * top - the same shape as saving a game. The name may be abandoned with ESC and
 * the key may not, which is the third argument to name_entry.
 *
 * The idle timeout is held off for the whole of it and put back afterwards,
 * because typing slowly is not being idle.
 */
void far register_screen(void)
{
    menu_t  m;                                     /* [bp-0x78] */
    char    name[0x16];                            /* [bp-0x8e] */
    char    key[0x16];                             /* [bp-0xa4] */
    int16_t chosen;
    int16_t saved;
    int16_t ok;                                    /* si */

    menu_reset(&m);
    menu_add_title(&m, menu_text[56]);                          /* REGISTER DUCKS */
    menu_add_action(&m, menu_text[60], 0x0b, &menu_always, 0);  /* ENTER YOUR NAME */
    menu_add_action(&m, menu_text[33], 0x0f, &menu_always, 0);  /* CANCEL */
    m.background = 7;

    saved = game_in_progress;
    game_in_progress = 1;

    menu_screen_driver(&m, &chosen, 0);
    if (m.item[chosen].action == 0x0b) {
        strcpy(name, "`");                         /* d+0x2735 - the cursor */
        ok = name_entry(name, chosen, 1);
    } else {
        ok = 0;
    }
    menu_free(&m);

    if (ok) {
        menu_reset(&m);
        menu_add_title(&m, menu_text[56]);
        menu_add_action(&m, menu_text[61], 0x0b, &menu_always, 0);  /* ENTER YOUR KEY */
        menu_add_action(&m, menu_text[33], 0x0f, &menu_always, 0);
        m.background = 7;

        menu_screen_driver(&m, &chosen, 0);
        if (m.item[chosen].action == 0x0b) {
            strcpy(key, "`");                      /* d+0x2737 */
            ok = name_entry(key, chosen, 0);       /* and this one cannot be
                                                    * abandoned */
        } else {
            ok = 0;
        }
        menu_free(&m);

        if (ok)
            check_registration(name, key, 1);
    }
    if (!ok)
        show_splash("ABORTED", 100);               /* d+0x2739 */

    game_in_progress = saved;
}

/* ------------------------------------------------- 0x12951: load_game_screen
 *
 * Returns non-zero when a game was actually loaded, and everything after the
 * menu is the file: the name, the egg it belongs to, three words, four bytes.
 */
int16_t far load_game_screen(void)
{
    menu_t      m;
    item_t far *r;
    int16_t     chosen;
    FILE far   *fp;
    char far   *id;
    char far   *name;
    int16_t     loaded = 0, v12 = 0;
    int         c;

    menu_reset(&m);
    menu_add_title(&m, menu_text[34]);             /* "LOAD SAVED GAME" */
    add_save_slots(&m, 0);
    menu_add_action(&m, menu_text[33], 0x0f, &menu_always, 0);   /* "CANCEL" */
    m.background = 0x11;

    r = menu_screen_driver(&m, &chosen, 1);
    if (r->action == 0x0f)
        goto done;

    save_name[4] = (char) ('0' + r->param);
    fp = fopen(save_name, "rb");
    if (!fp)
        goto done;

    /* The header doubles as a version stamp: a '2' anywhere in it means the file
     * has the extra field v1.2 added at the end. */
    while ((c = fgetc(fp)) > 0)
        if (c == '2')
            v12 = 1;

    free(egg_read_string(fp));                     /* the slot's name, shown in
                                                    * the menu and not needed
                                                    * again */
    id   = egg_read_string(fp);
    name = egg_read_string(fp);
    find_egg_by_id(id, name);
    shareware_limit = egg_files[episode_egg_index].limit;
    free(id);
    free(name);

    save_serial     = egg_read_word(fp);
    score          = egg_read_word(fp);
    next_life       = egg_read_word(fp);
    level_attempted = fgetc(fp);
    lives           = fgetc(fp);
    g_21a3          = fgetc(fp);
    if (v12)
        g_1ffa      = fgetc(fp);

    menus_resume();
    loaded = 1;
    save_note(save_serial);
    fclose(fp);

done:
    menu_free(&m);
    return loaded;
}

/* ------------------------------------------------- 0x13298: save_game_screen
 *
 * The mirror of it, with the name entry in the middle. The menu is run with
 * `owns` clear so the backdrop survives it, the chosen item's text becomes the
 * starting point for what is typed, and a '`' is stuck on the end as the cursor.
 */
void far save_game_screen(void)
{
    menu_t      m;
    char        typed[0x16];
    item_t far *r;
    int16_t     chosen;
    FILE far   *fp;

    menu_reset(&m);
    menu_add_title(&m, menu_text[35]);             /* "SAVE THIS GAME" */
    add_save_slots(&m, 1);
    menu_add_action(&m, menu_text[33], 0x0f, &menu_always, 0);   /* "CANCEL" */
    m.background = 7;

    r = menu_screen_driver(&m, &chosen, 0);
    if (r->action == 0x0f)
        goto done;

    strcpy(typed, m.item[chosen].text);
    strcpy(typed + strlen(typed), "`");            /* d+0x2741 - the cursor */

    if (!name_entry(typed, chosen, 1)) {
        show_splash(menu_text[38], 100);           /* "SAVE ABORTED" */
        goto done;
    }

    save_name[4] = (char) ('0' + r->param);
    fp = fopen(save_name, "wb");
    if (!fp) {
        show_splash(menu_text[37], 100);           /* "ERROR SAVING" */
        goto done;
    }

    fputs("Ducks Saved Game v1.2", fp);            /* d+0x2746 */
    fputc(0, fp);
    write_string(fp, typed);
    write_string(fp, egg_files[episode_egg_index].id);
    write_string(fp, egg_files[episode_egg_index].name);

    if (save_serial == 0)                          /* a slot that had none */
        save_serial = ++serial_high;

    write_word(save_serial, fp);
    write_word(score, fp);
    write_word(next_life, fp);
    fputc(level_attempted, fp);
    fputc(lives, fp);
    fputc(g_21a3, fp);
    fputc(g_1ffa, fp);
    fclose(fp);

done:
    menu_free(&m);
}

/* ------------------------------------------------- 0x13fea: scan_save_slots
 *
 * Takes nothing, returns nothing; its only output is one global. The names are
 * not five constants - save_name holds the template "GAME-.SG" and the loop
 * patches the digit into offset 4.
 *
 * The read order matters and was wrong here: a leading string is skipped byte
 * by byte, then THREE strings are read and thrown away, and only the word after
 * all of that is the one compared. Reading the word first, as this did, reads
 * the front of a string.
 *
 * That word was going through a stub called `f_14e88`, which is 0x04e88 with
 * the segment wrapped into the name - and 0x04e88 is two fgetc calls, `hi << 8`
 * then `+ lo`. It is egg_read_word on a FILE, which egg.c has had all along.
 */
void far scan_save_slots(void)
{
    FILE     *fp;
    int16_t   i, v;
    char far *s;

    for (i = 1; i < 6; i++) {
        save_name[4] = (char) ('0' + i);           /* [0x21a9] */
        fp = fopen(save_name, "rb");               /* 0x14009 */
        if (!fp)
            continue;                              /* 0x1401f */

        /* 0x14024. Bytes until a zero. The original would spin here on a save
         * that ended first, since fgetc keeps handing back EOF; the second test
         * is the port's and nothing else. */
        {
            int c;

            while ((c = fgetc(fp)) != 0 && c != EOF)
                ;
        }

        /* Three of them, and the file says what they are: "BORAN", ten bytes
         * of something, and "MAIN.EGG" - the owner, a key and the egg the save
         * belongs to. Reading two left the word eleven bytes early. */
        s = egg_read_string(fp);  free(s);         /* 0x1403d */
        s = egg_read_string(fp);  free(s);         /* 0x14054 */
        s = egg_read_string(fp);  free(s);         /* 0x1406b */

        v = egg_read_word(fp);                     /* 0x14082 -> 0x04e88 */
        if ((uint16_t) v > serial_high)            /* 0x1408e is jbe, unsigned */
            serial_high = (uint16_t) v;            /* [0x2055], the only output */
        fclose(fp);                                /* 0x1409a */
    }
}

/* --------------------------------------------------- 0x13dfb: load_settings
 *
 * The other half of save_settings, and what fills the hall of fame. Returns
 * non-zero when there was a file to read.
 *
 * The leading string doubles as a version stamp: a '!' anywhere in it means the
 * current format. Without one the file is an older build's, and two things
 * differ - the button map is derived from a single setting rather than stored,
 * and one fewer byte follows it.
 */
int16_t far load_settings(void)
{
    char      line[0x1a];
    FILE     *fp;
    int16_t   old = 1;                             /* di */
    int16_t   i;
    int       n;

    serial_high = 0;
    fp = fopen(settings_name, "rb");
    if (!fp)
        return 0;

    /* Read to the end of the leading string. fgetc gives -1 at the end of the
     * file and the original keeps it in a byte, where it is 0xff and not zero -
     * so on a file that ends there the original spins here forever, and on one
     * that ends later it reads -1 into every field after it.
     *
     * A file with no header is treated as no file. That is not in the original,
     * and the reason it has to be here is worth stating: -1 in button_map sends
     * item_label at extra_text[5] instead of one of [6] to [8], and that string
     * is longer than the room menu_add reserved for it, so the menus corrupt the
     * heap before anything gets as far as a window. */
    while ((n = fgetc(fp)) > 0)
        if (n == '!')
            old = 0;
    if (n < 0) {
        fclose(fp);
        return 0;
    }
    if (old)
        printf("Old %s format! Converting file...\n\n", settings_name);

    for (i = 0; i < 5; i++)
        settings[i] = fgetc(fp);                   /* 0x04f4 */
    video_mode = fgetc(fp);                        /* 0x04fe, the sixth of the
                                                    * six the original reads as
                                                    * one run */

    if (old) {
        button_map[0] = !settings[2];
        button_map[1] = 2;
        button_map[2] = settings[2];
    } else {
        for (i = 0; i < 3; i++)
            button_map[i] = fgetc(fp);
    }
    /* Three bytes, or two on the old format. The original reads them as one run
     * from d+0x1fd3 because they are adjacent there; here they are three
     * variables and taking the address of the first does not reach the others -
     * it writes two bytes past a one-byte object, which is what a zero-length
     * settings.dat turned into a heap assertion. */
    ambience_volume = (uint8_t) fgetc(fp);         /* 0x1fd3 */
    game_speed = (uint8_t) fgetc(fp);              /* 0x1fd4 */
    if (!old)
        gamma_level = (uint8_t) fgetc(fp);         /* 0x1fd5 */

    registered = fgetc(fp);                        /* 0x0548 */
    if (registered) {
        owner_name = egg_read_string(fp);          /* 0x0542 */
        owner_key  = egg_read_word(fp);            /* 0x0546 */
        sprintf(line, "%06u", owner_key - 1);      /* d+0x27fb */
        check_registration(owner_name, line, 0);
    }

    for (i = 0; i < 10; i++) {
        free(score_table[i].name);
        score_table[i].score  = egg_read_word(fp);
        score_table[i].name   = egg_read_string(fp);
        score_table[i].serial = egg_read_word(fp);
        if ((uint16_t) score_table[i].serial > serial_high)
            serial_high = score_table[i].serial;
    }
    fclose(fp);
    return 1;
}

/* ---------------------------------------------------- 0x140b1: save_settings
 *
 * The same values back out, in the same order. Every one of them goes through
 * fputc, so a setting is a byte on the way out however wide it is here; only the
 * registration key and the board's scores and serials are words, and those go
 * through write_word, high byte first.
 */
void far save_settings(void)
{
    FILE   *fp;
    int16_t i;

    fp = fopen(settings_name, "wb");               /* the far pointer at 0x21d2 */
    if (!fp)
        return;

    fputs("!", fp);                                /* DGROUP+0x2806, the marker */
    fputc(0, fp);

    for (i = 0; i < 5; i++)  fputc(settings[i], fp);       /* 0x04f4 */
    fputc(video_mode, fp);                                 /* 0x04fe - the sixth
                                                            * of the six the
                                                            * original writes as
                                                            * one run */
    for (i = 0; i < 3; i++)  fputc(button_map[i], fp);     /* 0x20e4 */
    fputc(ambience_volume, fp);                    /* 0x1fd3, 0x1fd4, 0x1fd5 -
                                                    * one run in the original,
                                                    * three variables here */
    fputc(game_speed, fp);
    fputc(gamma_level, fp);
    fputc(registered, fp);                         /* 0x1415e */
    if (registered) {
        write_string(fp, owner_name);
        write_word(owner_key, fp);
    }
    for (i = 0; i < 10; i++) {                     /* the hall of fame */
        write_word(score_table[i].score, fp);
        write_string(fp, score_table[i].name);
        write_word(score_table[i].serial, fp);
    }
    fclose(fp);
}

/* ---------------------------------------------------------------- 0x141fe: init
 *
 * The whole startup screen, not merely the key wait it was first named for. Its
 * first act prints "DUCKS v1.21", the first line the program shows at all.
 */
void far init(void)
{
    int16_t i;

    puts("DUCKS v1.21");                           /* DGROUP+0x2808 */

    /* 0x14291. The only hardware gate that can stop the game starting. */
    i = mouse_init();
    if (!i)
        fatal("No mouse driver", 0);               /* 0x142a6 */
    printf("%i button mouse found\r\n\r\n", i);  /* d+0x2828 */
    for (i = 0; i < 3; i++) {                      /* three 22-byte objects */
        message_image[i] = malloc(sizeof(desc_t));  /* [0x210c], stride 4 */
        message_image[i]->w = 316;
        message_image[i]->h = 15;
        /* 0x14284 calls 0x15388, which is alloc_image at 0x05388 - a near call
         * wraps inside its 64 KB segment, so the offset read off a listing is a
         * segment offset and not an image one. This had been a do-nothing stub,
         * which left all three of these images without rows: message_post writes
         * through them, so the first level with a tool crashed. */
        alloc_image(message_image[i], 0, 0, 0, 1);
    }
    /* 0x142bb. The board starts full: ten rows of TIM FURNISH, 10000 down to
     * 1000. This runs before the file is read, so a settings.dat replaces it and
     * a machine without one still has a hall of fame to show. */
    for (i = 0; i < 10; i++)
        score_set((int16_t) ((10 - i) * 1000), "TIM FURNISH", i);  /* d+0x2842 */

    /* 0x142e1. Which eggs to open, and where from. */
    load_eggs_ini("EGGS.INI");
    if (egg_ini_count == 0 && egg_ini_count < 5)   /* 0x142e7, 0x14302 */
        str_copy("EGGS\\MAIN.EGG",                 /* d+0x2857, the fallback */
                 &egg_ini_paths[egg_ini_count++]);

    load_settings();                               /* 0x1432c - the settings and
                                                    * the hall of fame */

    /* 0x14380. One record per named egg, then open each in turn. Only the FIRST
     * one failing is fatal - the rest are episode packs and the game runs
     * without them, which is what the `si == 0` at 0x143ac says. The path is
     * freed as soon as it has been opened. */
    egg_table_alloc(egg_ini_count);
    for (i = 0; i < egg_ini_count; i++) {
        if (!open_egg(egg_ini_paths[i]) && i == 0)
            fatal("Couldn't load primary data file", 0);   /* d+0x2889 */
        free(egg_ini_paths[i]);                    /* 0x143d1 */
    }

    build_charmap();                               /* 0x143e5 */
    font_load();                                   /* 0x143e9 - the one 'F'
                                                    * block, before anything
                                                    * has any words to draw */
    load_string_tables();                          /* 0x094b7 - and the words
                                                    * themselves */
    /* 0x143f8. The game's whole sprite set - 273 of them - and then the scripts
     * that say which one an entity of a given type shows. Everything drawn as an
     * entity, the mouse pointer included, needs both. */
    sprite_set_load(0, 0x53, &sprite_table, 0xff);
    load_animations();                             /* 0x143ff */
    build_episode_index();                         /* 0x14403 */
    build_menus();                                 /* 0x14407 - and the menus,
                                                    * which are made of them */
    scene_alloc(&cursor_scene, 1);                 /* 0x14411 - the one entity
                                                    * every menu draws: the
                                                    * mouse pointer */
    scene_add(&cursor_scene, 0, 0, 0x14, 5);       /* 0x14424 */
    /* the remaining banners */

    sound_available = detect_hardware();           /* 0x14974: the sound check,
                                                    * then XMS, then "Free XMS
                                                    * memory: %uk" */
    if (sound_available)
        sound_init(11000);                         /* 0x2af8 = 11000 decimal */

    console_rule();                                /* 0x1442f */
    set_text_colour(15);
    // NOT NEEDED
    //puts("Press a key to begin...");               /* DGROUP+0x28e7 */
    //print_newline();
    //do {
    //    input_poll(320, 200);
    //} while (!last_key);                           /* [0x18f6] */
}

/* ------------------------------------------------------------ 0x144d7: main
 *
 * Mapped in docs/notes/entry-points.md by breakpointing a live machine - every
 * frame below was observed rather than read off the listing.
 *
 * What it indexes at 0x1894:0 is menu_text, and the offsets are byte offsets
 * into an array of far pointers, so +0x9c is menu_text[39] - "PRESENTS".
 */
void far main(void)
{
    install_int23(&ctrl_break_handler);      /* 0x144e0, far 04ca:f82d */

    init();                                  /* 0x144e9 */
    set_mode_x(video_mode);                  /* 0x144f1 - [0x4fe], not a literal */
    dac_set_black(0, 0);                     /* 0x14502 - black from here on */
    input_poll(320, 200);                    /* 0x1450f */
    scan_save_slots();                       /* 0x14516 - GAME1.SG..GAME5.SG */

    /* The intro: two screen players, interleaved with sounds. Screen (1) draws
     * nothing - the 320x24 source is allocated but empty, checked in the planes. */
    // NOT NEEDED
    //show_splash(g_28ff, 100);               /* 0x14520 - (1) blank */
    egg_load_all();                    /* 0x14527 - and it draws the version
                                              * and credits page, which waits for
                                              * a key */
    sound_play_guarded(0x2b, 1);
    show_resource(0x4d, 5, 50, 0xff);        /* 0x1453f - the Hungry Software logo */
    show_splash(menu_text[39], 100);         /* 0x1455c - "PRESENTS" */
    sound_play_guarded(0x28, 1);
    show_resource(0x4d, 8, 100, 0xff);       /* 0x14577 - the title */

    if (!registered)                         /* 0x1457d - [0x548] */
        show_splash(menu_text[62], 100);     /* 0x1459b - "UNREGISTERED" */

    sound_play_guarded(0x0b, 1);             /* 0x145a1 */
    game_main(&main_menu);                   /* 0x145b1 - does not return until
                                              * QUIT clears its loop flag */

    /* On the way out. Nobody had seen any of this before it was walked, because
     * it runs only after the menu is quit. */
    if (!registered) {                       /* 0x145b7 */
        show_resource(0x4d, 0x64, 250, 0xff);   /* the gameplay collage */
        show_resource(0x4d, 0x65, 250, 0xff);   /* "World Wide Webbed" */
        show_resource(0x4d, 0x66, 250, 0xff);   /* nothing - not in this egg */
        show_readme_section(2);                 /* HOW TO REGISTER, waits for ESC */
    }
    show_resource(0x4d, 0x67, 250, 0xff);    /* 0x14605 - visit us on the web */
    release_sounds();                        /* 0x1460b - 0x146cd */

    set_bios_mode(3);                        /* 0x14613 - back to text */
    save_settings();                         /* 0x1461a - settings.dat */
    close_egg_files();                       /* 0x1461e - fclose and free, each */
    crt_exit();                              /* 0x14621 - lcall 0, 0x1e6b; runs
                                              * with segment base 0, not this
                                              * segment's */
}

/* ==========================================================================
 * run_level's leaves, 0x0580b to 0x0d6c3
 *
 * The innermost routines of the gameplay, transcribed from the Python natives
 * in native.py that stand in for them. Those were byte-compared against the
 * guest before this was written - on the level 80 and demo snapshots under
 * --verify, and on made-up inputs in test_gameplay.py, which is what covers the
 * branches no captured state reaches. Every one of them is a leaf: nothing they
 * call is itself replaced, so the comparison was of their own arithmetic and
 * not partly of itself. See docs/notes/verification-lessons.md on why that
 * distinction matters.
 *
 * Nothing calls these yet. run_level is still the stub in stubs.c, because 24
 * of the 91 routines it reaches are unwritten and its loop exits on a flag that
 * only the unread part clears - so a skeleton of it would not play a level, it
 * would hang. These are the bottom of that list, done first.
 *
 * That count was 51, then 33, and both were too high: they were taken by
 * matching symbols.py names rather than image offsets, so anything written
 * under a name symbols.py did not have - image_alloc, sprite_set_free,
 * sprite_set_load, egg_read_string - read as missing. Count by offset.
 * ======================================================================== */

int16_t level_w, level_h;        /* 0x1701, 0x1703 */
uint8_t scroll_shift;            /* 0x18f5 - how much of the way to the target
                                  * the view moves each frame, as a right shift.
                                  * The menu sets it to 5 before a demo */
/* 0x4fa, and initialised DATA: the image has 1 and four live guests - the main
 * menu, snap003, snap012, level80-late - all read 1, so nothing writes it but
 * the 'c' key and MOUSE SETTINGS. Declared bare, the port ran with SMOOTH
 * SCROLL off and every camera move was the hard edge push below rather than the
 * ease, which is what "the demo is missing mouse scrolling" was. */
int16_t scroll_smooth = 1;       /* 0x4fa - 1 to ease, 0 to only move the view
                                  * when the followed point would leave it.
                                  * Starts 1; a level event toggles it */
uint8_t bg_drift;                /* 0x202c - two base-3 digits the level carries */
scene_t scenes[6];               /* 0x0d63, twelve bytes each */
int16_t level_clock;             /* 0x201a - frames since the level started */
uint8_t far *tool_event_table;   /* 0x203b - three bytes a record */
uint16_t    tool_event_count;    /* 0x2047 */
uint8_t far *event_table;        /* 0x203f - six bytes a record, and what
                                  * 0x0d471 walks on the demo path */
uint16_t    event_count;         /* 0x2049 */
uint8_t far *script_table;       /* 0x2043 - three bytes a record, the
                                  * level script the frame advances */
uint16_t    script_count;        /* 0x204b */
int16_t     script_at;           /* 0x204d */

/* ------------------------------------------------------- 0x05f7f: one axis
 *
 * Ease `pos` toward centring `focus`, and clamp it into [0, span] so the view
 * never leaves the level:
 *
 *     *pos += (target - *pos) >> scroll_shift;
 *
 * The mask is what makes it arrive. A right shift of a small difference is
 * zero, so plain easing stops short and stays there; adding (1 << shift) - 1 to
 * both the target and the clamp keeps the difference non-zero until the view is
 * really at the edge, and the same mask on the limit means the clamp admits
 * that overshoot rather than fighting it.
 *
 * The mask is NOT (1 << scroll_shift) - 1, and this is the one thing here that
 * a careful reading gets wrong. The guest computes `2 << (shift - 1)` in 16
 * bits with the count in cl, so a shift of 0 - the value the variable is
 * initialised to - asks for `2 << 255`, the hardware masks the count to 31, the
 * result is 0, and the decrement leaves 0xffff, i.e. -1. Reproduced here with
 * the same masking, which is why the expression is written the long way.
 */
static void scroll_axis(int32_t focus, int32_t half, int32_t far *pos,
                        int16_t span)
{
    int16_t mask;
    int32_t target, limit;

    /* Above 15 the runtime's signed long shift at 0:0x1128 takes its other path
     * and the mask stops being expressible this way. That cannot happen: three
     * instructions in the whole image write [0x18f5] - 5 at 0x12788 and 0x12825
     * for a demo, 2 at 0x13837 for a played level - and the image initialises it
     * to 0. So the value is 0, 2 or 5 and nothing else, and this guard is for a
     * state the game cannot reach rather than one left unwritten. */
    if (scroll_shift >= 16)
        return;

    mask   = (int16_t) ((((uint32_t) 2u << ((scroll_shift - 1) & 0x1f)) - 1u)
                        & 0xffffu);
    target = focus - (half - mask);
    limit  = (int16_t) (span + mask);

    if (limit < target)
        target = limit;
    else if (target < 0)
        target = 0;

    /* Arithmetic, matching the guest's sar. */
    *pos += (target - *pos) >> scroll_shift;
}

/* 0x05f15. The same without the easing, and so without a mask: there is nothing
 * to converge to. run_level calls it twice right after cursor_to_centre, which
 * is how a level starts with the view already around the cursor instead of
 * sliding to it. The halving is a 32-bit sar/rcr of the caller's whole long, so
 * a negative extent rounds toward minus infinity rather than toward zero. */
void far scroll_axis_snap(int32_t focus, int32_t extent, int32_t far *pos,
                          int16_t span)
{
    int32_t v = focus - (extent >> 1);

    if (span < v)
        v = span;
    if (v < 0)
        v = 0;
    *pos = v;
}

/* 0x0600d. Put the view where it should be for the point being followed. */
void far scroll_follow(int32_t x, int32_t y)
{
    if (scroll_smooth) {
        scroll_axis(x, viewport_game.width >> 1, &viewport_game.scroll_x, (int16_t) (level_w - viewport_game.width));
        scroll_axis(y, viewport_game.height >> 1, &viewport_game.scroll_y, (int16_t) (level_h - viewport_game.height));
        return;
    }

    /* The view does not move while the point is inside it, and is pushed by
     * exactly as much as the point leaves by when it is not. The +1 is what
     * makes the two bounds consistent: it leaves the point on the last column
     * of the view rather than the first one past it. */
    if (x < viewport_game.scroll_x)
        viewport_game.scroll_x = x;
    if (y < viewport_game.scroll_y)
        viewport_game.scroll_y = y;
    if (x - viewport_game.scroll_x >= viewport_game.width)
        viewport_game.scroll_x = x - viewport_game.width + 1;
    if (y - viewport_game.scroll_y >= viewport_game.height)
        viewport_game.scroll_y = y - viewport_game.height + 1;
}

/* ================================================== 0x09565: flock_chain
 *
 * **This is what the walk button is for.** Holding it gives the hero a facing
 * (entity_update's type 1 arm), and this is what turns that one duck walking
 * into a line of them: starting from the head, repeatedly take the nearest duck
 * that has not been taken yet, give it the head's facing, point it at the one in
 * front, and make *it* the head. Rank is the position in the line.
 *
 * The three fields it writes are exactly the three that type 2 reads back in
 * entity_update: `f19` is whether it walks at all (and doubles as "already in
 * the line"), `f1a` is how far behind to hold, and `lead` is who to hold behind.
 * So without this the ducks have f19 == 0 for ever and the flock never moves, no
 * matter what the hero does.
 *
 * The distance is `|dx| + |dy| / 2` on the **low words** of x and y - the guest
 * subtracts with word operations and never looks at the high halves - and it is
 * compared against a reach of 0x32, passed in as a long from both call sites.
 */
void far flock_chain(entity_t far *head, int32_t reach)
{
    int16_t rank;

    if (head->f14 != 0)                            /* 0x0956d - a facing sticks */
        head->f16 = (uint8_t) head->f14;
    if (head->f16 == 0)                            /* 0x09585 */
        return;

    for (rank = 1; rank <= scenes[0].count; rank++) {
        int32_t best = reach;                      /* 0x0959a - reset per rank */
        int16_t pick = -1, i;

        for (i = 0; i < scenes[0].count; i++) {
            entity_t far *o = &scenes[0].entities[i];
            int16_t dx, dy;
            int32_t d;

            if (i == scenes[0].flag)               /* 0x095ae - never the hero */
                continue;
            dx = (int16_t) o->x - (int16_t) head->x;
            dy = (int16_t) o->y - (int16_t) head->y;
            if (dx < 0) dx = (int16_t) -dx;        /* 0x095d3 - cdq/xor/sub */
            if (dy < 0) dy = (int16_t) -dy;
            d  = (int16_t) (dx + (dy >> 1));       /* 0x09603 - then cdq */
            if (d >= best)                         /* 0x09610 - strictly nearer */
                continue;
            if (o->f19)                            /* 0x09629 - already in line */
                continue;
            pick = i;
            best = d;
        }

        if (pick < 0)                              /* 0x0964d - nobody in reach */
            break;

        {
            entity_t far *e = &scenes[0].entities[pick];

            if (e->type == 4)                      /* 0x0965d - a loose duck joins */
                entity_set_type(e, 2);
            e->f16  = head->f16;
            e->f1a  = 8;
            e->f19  = (uint8_t) rank;
            e->lead = head;
            head    = e;                           /* 0x096eb - and it leads next */
        }
    }
}

/* 0x0970c. Rebuild that line, from scratch, every frame: clear every duck's
 * rank first and demote the followers back to loose ducks, then chain from the
 * mirrored scene's first entity if the level has one, and from the hero.
 *
 * Rebuilding rather than maintaining is what lets a duck be picked up by simply
 * walking near it and dropped by walking away, with no bookkeeping anywhere
 * else - and it is why the demotion at the top is not a bug: flock_chain
 * promotes each one straight back on the same frame.
 */
void far flock_link(void)
{
    int16_t i;

    for (i = 0; i < scenes[0].count; i++) {
        entity_t far *e = &scenes[0].entities[i];

        e->f19 = 0;
        if (e->type == 2)
            entity_set_type(e, 4);
    }

    /* 0x0975c and 0x09776: [0xda1] is scenes[5].count and [0xda7] its entity
     * array, which is the DGROUP-duplicate trap again - two names for one
     * scene header field. */
    if (scenes[5].count)
        flock_chain(scenes[5].entities, 0x32);
    if (scenes[0].flag != 0xff)
        flock_chain(&scenes[0].entities[scenes[0].flag], 0x32);
}

/* 0x0979f. Remember where every entity in a scene was. run_level calls it on
 * five of the six scenes before anything moves, so prev_x/prev_y is where the
 * entity was when the frame began. */
void far scene_keep_positions(scene_t far *s)
{
    int16_t i;

    for (i = 0; i < s->count; i++) {
        s->entities[i].prev_x = s->entities[i].x;
        s->entities[i].prev_y = s->entities[i].y;
    }
}

/* 0x0a3a7. Swap two entity types throughout the third scene, and leave
 * everything else alone. It goes through entity_set_type, so each one that
 * changes has its frame reset - and since the two types always differ, every
 * one of them does. */
void far scene_swap_pair(void)
{
    int16_t i;

    for (i = 0; i < scenes[2].count; i++) {
        entity_t far *e = &scenes[2].entities[i];

        if (e->type == 0x2c)
            entity_set_type(e, 0x2d);
        else if (e->type == 0x2d)
            entity_set_type(e, 0x2c);
    }
}

/* 0x0d6c3. Start the background where it belongs and say which way it drifts.
 *
 * The level carries one byte and it is two base-3 digits - the remainder gives
 * the horizontal drift and the quotient the vertical - each turned into
 * 1 - digit, so each axis is one of +1, 0 or -1. The step is kept as an
 * unsigned byte, which is why -1 is written as the tile's size minus one, and
 * the remainder comes from an unsigned 16-bit divide of a value that has
 * already wrapped, so it is written that way and not as a mathematical modulus.
 *
 * A zero tile size is a divide fault here exactly as it is in the original.
 */
void far bg_scroll_reset(void)
{
    int16_t d = (int8_t) bg_drift;

    bg_scroll_y = 0;
    bg_step_y   = (uint8_t) ((uint16_t) (background.h + 1 - d / 3) % (uint16_t) background.h);
    bg_scroll_x = 0;
    bg_step_x   = (uint8_t) ((uint16_t) (background.w + 1 - d % 3) % (uint16_t) background.w);
}

/* 0x0b0c5. Build the palette that goes to the DAC from the one on file: 768
 * entries scaled by (gamma + 6) / 19 and clipped at 255, so gamma 13 is the
 * identity, below it dims and above it brightens until everything saturates. A
 * level event steps the byte up to 0x1f and back down, which is how a level
 * flashes. The multiply is 16-bit and its low half is what is kept. */
void far palette_apply_gamma(void)
{
    const uint8_t far *src = current_buffer;
    int16_t scale = (int16_t) (gamma_level + 6);
    int16_t i;

    for (i = 0; i < 768; i++) {
        int16_t v = (int16_t) ((int16_t) (src[i] * scale) / 19);

        palette_stored[i] = (uint8_t) (v > 0xff ? 0xff : v);
    }
}

/* 0x0d4c2. The level's scheduled tool changes: a table of three-byte records,
 * each a time and a tool index, applied when the time matches the level clock.
 *
 * The loop does not stop at the first match, so two records on the same tick
 * leave the last one selected. That is the guest's behaviour and not obviously
 * deliberate, which is a reason to keep it rather than tidy it.
 */
void far tool_events(void)
{
    uint16_t i;

    for (i = 0; i < tool_event_count; i++) {
        const uint8_t far *rec = tool_event_table + i * 3;

        if ((int16_t) (rec[0] | (rec[1] << 8)) == level_clock)
            tool_at = rec[2];
    }
}

/* 0x06f4f. Copy one entity of a scene over another - eleven fields, in the
 * guest's own order, and the gaps are the point. +0x08 to +0x13 is not copied,
 * which is prev_x and prev_y, so an entity moved this way keeps the
 * destination's idea of where it was last frame rather than the source's.
 * +0x19 to +0x1e is not copied either; nothing has read those yet. */
void far entity_copy(scene_t far *s, int16_t from, int16_t to)
{
    entity_t far *a = &s->entities[from];
    entity_t far *b = &s->entities[to];

    b->x     = a->x;
    b->y     = a->y;
    b->f14   = a->f14;
    b->f21   = a->f21;
    b->f16   = a->f16;
    b->f15   = a->f15;
    b->frame = a->frame;
    b->f23   = a->f23;
    b->f27   = a->f27;
    b->type  = a->type;
    b->param = a->param;
}

/* ------------------------------------------------ 0:0x146c and 0:0x147d
 *
 * The runtime's own generator, written out rather than handed to the host's.
 * That is not tidiness: run_level draws from it eleven times a frame, and a
 * level is srand'd from a seed it carries, which is the whole reason a recorded
 * demo replays identically. A demo run against libc's rand() would diverge on
 * the first duck.
 *
 * Borland's is the usual LCG with the seed as a long at d+0x3006, initialised
 * to 1, returning bits 16..30. srand keeps only the low word - it zeroes the
 * high one rather than sign-extending.
 */
uint32_t rand_seed = 1;          /* 0x3006 */

int16_t far game_rand(void)
{
    rand_seed = rand_seed * 0x015a4e35u + 1u;
    return (int16_t) ((rand_seed >> 16) & 0x7fff);
}

void far game_srand(uint16_t seed)
{
    rand_seed = seed;
}

/* --------------------------------------------------- 0x077ae: the particles
 *
 * The pool is the far allocation run_level makes in its setup, sized
 * (scene0.count + [0x18d1]) * 0x28 records - and 0x28 is exactly what one dying
 * duck asks for, so it is "forty per duck". A full pool drops the rest silently
 * rather than growing.
 *
 * Four draws per particle and the ORDER is as load-bearing as the values: the
 * generator is shared with everything else in the frame, so a draw too few or
 * in the wrong place moves every later duck and quack.
 *
 * The shift is 16-bit and only then widened, so a coordinate past 4095 wraps
 * rather than scaling. Reproduced with the cast.
 */
int16_t          particle_cap;       /* 0x18cf */
int16_t          particle_count;     /* 0x18cd - run_level clears it, the
                                      * spawner fills it, particles draws it */
particle_t far  *particle_array;     /* 0x18c1 - run_level's own allocation */
int16_t          duck_count;         /* 0x2007 - what the HUD's second number
                                      * shows, and what the "not enough got
                                      * home" ending compares against */
/* 0x18c5, and initialised DATA - nothing in the image ever writes it, the only
 * reference at all is the read at 0x07854. Declared bare here, so every particle
 * came out colour 0, and colour 0 is empty terrain: each hit ERASED the pixel it
 * landed on instead of staining it, the particle fell into the hole it had just
 * made, and the flock drilled straight down through the ground. Read out of the
 * image at d+0x18c5 and checked against a live guest. */
uint8_t          particle_colours[8] = { 0x5c, 0x5d, 0x5e, 0x5c,
                                         0x5d, 0x52, 0x64, 0x58 };

void far particles_spawn(int16_t x, int16_t y, int16_t n)
{
    int16_t i;

    for (i = 0; i < n; i++) {
        particle_t far *p;

        if (particle_count >= particle_cap)
            continue;
        p = &particle_array[particle_count];

        p->x      = (int16_t) (x * 8);
        p->y      = (int16_t) (y * 8);
        p->vx     = (int16_t) ((game_rand() & 15) - 7);
        p->vy     = (int16_t) (-7 - (game_rand() & 15));
        p->colour = particle_colours[game_rand() & 7];
        p->f0d    = (uint8_t) ((game_rand() & 1) + 1);
        p->f0e    = 1;
        particle_count++;
    }
}

/* -------------------------------------------------------- 0x078f7: a duck dies
 *
 * The first test is the interesting one, and it is easy to read inside out:
 * `jne` on [0x509] jumps to the RETURN, so this does nothing when force is clear
 * and cheat_state[2] is SET. cheat_state[2] is therefore "ducks do not die", and
 * menu_screen_driver clears it for the duration of a demo - which is exactly
 * when they do. Getting it backwards made both sides of the comparison silent
 * for object types 0x0b and 0x58.
 *
 * Type 3 is dead. Note that this is not how the monster kills - that sets the
 * type to 0 and lets the retire pass drop the record; see docs/notes/the-monster.md.
 */
void far duck_dies(entity_t far *e, int16_t force, int16_t noisy)
{
    if (!force && cheat_state[2])
        return;
    if (e->type == 3)
        return;

    duck_count--;
    if (noisy)
        sound_play_guarded(5, 1);
    entity_set_type(e, 3);
    e->f14 = 0;
    particles_spawn((int16_t) e->x, (int16_t) e->y, 0x28);
}

/* ------------------------------------------------- 0x07955: abandon the attempt
 *
 * Every duck in scene 0 through duck_dies with force set, which is 40 particles
 * each and the type set to 3 - so the flock blows up where it stands. Nothing
 * here ends the level directly. What ends it is [0x2016]: run_level's frame
 * counts 0x20 of those and only then sets outcome 3, which is the delay the
 * explosion plays out in.
 *
 * The sound is guarded on [0x2016] rather than played every time, so aborting an
 * attempt that has already ended is silent. `force` is 1 because [0x509] holds
 * duck deaths off during a demo and this has to work anyway - though the only
 * three callers are all played-level ones:
 *
 *   0x0cffe  ESC, Q or q                          - the player gives up
 *   0x07a22  the third bridge-stacking warning     - the game gives up for them
 *   0x0de2e  run_level's frame, when [0x1ffe] is set and [0x2003] has run out
 *
 * The third is a secret level's clock running out - [0x2003] is level_timer and
 * [0x1ffe] the secret-level flag - and it is the reason those levels are timed.
 */
void far kill_all_ducks(void)
{
    int16_t i;

    for (i = 0; i < scenes[0].count; i++)          /* 0x0797b, [0xd65] */
        duck_dies(&scenes[0].entities[i], 1, 0);   /* 0x07974 */

    if (!g_2016)                                   /* 0x07981 */
        sound_play_guarded(5, 1);
    g_2016 = 1;                                    /* 0x07993 */
}

/* ======================================================================
 * 0x0993b: the collision pass
 *
 * Scene 0 against scene 2 - every duck against every object - and the switch
 * below is where most of the game's rules live. 2,668 bytes in the original,
 * two nested loops covering all but 96 of them.
 *
 * The gate is two absolute-value tests: |dx| against anim_a[object type], the
 * per-type table load_animations fills, and |dy| against a constant 3. So the
 * boxes are wide and flat, which is what a duck walking into something on the
 * same row needs. Only six duck types take part, and a duck whose type is
 * already 0 is skipped - that is the retire convention, the same one the
 * monster kill relies on.
 *
 * The dispatch is two jump tables in the code segment (cs:0x56bb for 0x39..0x59
 * and cs:0x56fd for 6..0x0a) plus a chain of compares. Twenty-one of the
 * thirty-three table slots go straight to the next iteration, so the switch
 * below lists only the arms that do something.
 *
 * The Python twin in native.py IS verified - every object type against every
 * duck type, against the guest, plus 300 random multi-entity scenes. This C is a
 * transcription of it and has NOT itself been compared: doing that needs three
 * scenes and ten globals marshalled through ctypes, the way test_leaves.py does
 * for entity_copy, and that is not written yet. Nothing calls this, because
 * run_level is still a stub.
 * ====================================================================== */

int16_t score;                   /* 0x2036 */
int16_t quota_left;              /* 0x2013 - the "not enough got home" counter */
int16_t combo_hi, combo_lo;      /* 0x1ff6, 0x1ff8 - the score bonus decays out
                                  * of these two; both are also ticked down once
                                  * a frame by run_level */
int16_t g_1ff2, g_1ff4;          /* what type 0x37 stashes */
int16_t eaten_countdown;         /* 0x2005 */
/* 0x0d7f is scenes[2].flag - which object of scene 2 did it. scene_alloc
 * clears it to 0xff, which is how a level starts with nothing being eaten. */

static int16_t iabs(int32_t v)   { return (int16_t) (v < 0 ? -v : v); }

/* The bonus both "carried home" arms add. */
static int16_t carry_bonus(void)
{
    return (int16_t) ((combo_lo >> 4) + (combo_hi >> 8) + 5);
}

void far collide_scenes(void)
{
    int16_t si, di;

    for (si = 0; si < scenes[0].count; si++) {
        entity_t far *d = &scenes[0].entities[si];

        if (d->type != 1 && d->type != 2 && d->type != 4 &&
            d->type != 0x40 && d->type != 0x41 && d->type != 0x53)
            continue;

        for (di = 0; di < scenes[2].count; di++) {
            entity_t far *o = &scenes[2].entities[di];

            if (iabs(d->x - o->x) >= anim_a[o->type])
                continue;
            if (iabs(d->y - o->y) >= 3)
                continue;
            if (d->type == 0)          /* already retired this frame */
                continue;

            switch (o->type) {
            case 0x51:                                  /* 0x09a98 */
                entity_set_type(d, 0x52);
                /* face away from it, one step per eight pixels */
                if (d->x > o->x)
                    d->f14 = (int8_t) (((d->x - o->x) >> 3) + 1);
                else
                    d->f14 = (int8_t) (-(((o->x - d->x) >> 3) + 1));
                sound_play_guarded(4, 1);
                break;

            case 0x2e:                                  /* 0x09b87 */
                if (d->type == 1) {
                    sound_play_guarded(0x0d, 1);
                    entity_set_type(o, 0x2f);
                    score += 0x14;
                }
                break;

            case 6: case 7: case 8: case 9: case 0x0a:  /* 0x09bc9 - got home */
                if (d->type == 1)
                    break;
                duck_count--;
                quota_left--;
                entity_set_type(d, 0);
                d->y = -40;
                entity_set_type(o, 0x1a);
                o->param--;
                sound_play_guarded(4, 1);
                score += carry_bonus();
                combo_lo = 0xa0;
                break;

            case 0x48:                                  /* 0x09c72 */
                if (d->type == 1)
                    break;
                sound_play_guarded(0x1f, 1);
                entity_set_type(o, 0x49);
                entity_set_type(d, 0x4a);
                d->param = 0;
                d->f15   = 0xfc;
                d->y     = o->y;
                d->x     = o->x;
                score   += 0x1e;
                break;

            case 0x1f:                                  /* 0x09d51 */
                if (d->type == 1 || d->f14 == 0)
                    break;
                entity_set_type(d, 0);
                entity_set_type(o, 0x1e);
                o->f14 = (int8_t) (d->f14 > 0 ? 1 : -1);
                o->f15 = 0xff;
                sound_play_guarded(0x14, 1);
                score += 0x14;
                break;

            case 0x0b: case 0x58:                       /* 0x09e07 */
                duck_dies(d, 0, 1);
                break;

            case 0x4d:                                  /* 0x09e27 */
                if (d->type == 1) {
                    entity_set_type(d, 0x4e);
                    sound_play_guarded(0x2a, 1);
                }
                break;

            case 0x39: case 0x4b:                       /* 0x09e64 - eaten */
                if (cheat_state[2])
                    break;
                entity_set_type(d, 0);
                duck_count--;
                /* 0x46 and 0x47 are the same eleven-frame swallow, mirrored;
                 * which one is the object's facing. See the-monster.md. */
                entity_set_type(o, (int16_t) (0x46 + (o->f14 == 1)));
                sound_play_guarded(0x1d, 1);
                eaten_countdown = 0x32;
                scenes[2].flag     = di;
                break;

            case 0x2d:                                  /* 0x09eda */
                if (cheat_state[2])
                    break;
                sound_play_guarded(0x0f, 1);
                entity_set_type(d, 0x35);
                d->f15 = 0xfb;
                duck_count--;
                break;

            case 0x22:                                  /* 0x09f20 */
                if (d->type == 1)
                    break;
                entity_set_type(d, 0x24);
                entity_set_type(o, 0x23);
                o->f23 = 0;
                break;

            case 0x37:                                  /* 0x09fae */
                if (d->type != 1)
                    break;
                d->x    = o->x;
                /* the NEXT record's x and y, read straight past this one */
                g_1ff2  = (int16_t) o[1].x;
                g_1ff4  = (int16_t) o[1].y;
                entity_set_type(d, 0x20);
                sound_play_guarded(0x0a, 1);
                break;

            case 0x38:                                  /* 0x0a04a */
                if (d->type == 1)
                    entity_set_type(o, 0x3b);
                break;

            case 0x42: {                                /* 0x0a07c */
                int16_t at;

                if (cheat_state[2])
                    break;
                entity_set_type(d, 0);
                duck_count--;
                at = scenes[1].count;
                if (scene_add(&scenes[1], (int16_t) d->x,
                              (int16_t) (d->y - 10), 0x43, 0)) {
                    scenes[1].entities[at].f15 =
                        (uint8_t) (0xfb - (game_rand() & 3));
                    scenes[1].entities[at].f14 =
                        (int8_t) (((game_rand() & 1) << 1) - 1);
                }
                sound_play_guarded(0x1e, 1);
                break;
            }

            case 0x3c:                                  /* 0x0a12f */
                if (d->type == 0x40 || d->type == 0x41)
                    break;
                d->y = o->y;
                d->x = o->x;
                entity_set_type(o, 0x3e);
                entity_set_type(d, (int16_t) (d->type == 1 ? 0x33 : 0x41));
                sound_play_guarded(0x1b, 1);
                d->f15 = 0xf9;
                d->f21 = 0;
                break;

            case 0x3d:                                  /* 0x0a243 */
                entity_set_type(o, 0x3f);
                entity_set_type(d, (int16_t) (d->type == 1 ? 0x1c : 0x40));
                sound_play_guarded(0x1b, 1);
                d->f15 = 0xf9;
                d->f21 = 0;
                break;

            case 0x4f:                                  /* 0x0a2dc */
                duck_count--;
                quota_left--;
                entity_set_type(d, 0);
                d->y = -40;
                sound_play_guarded(0x14, 1);
                score += carry_bonus();
                combo_lo = 0xa0;
                break;

            default:
                break;
            }
        }
    }
}

/* ------------------------------------------------- 0x0a410: the message ticker
 *
 * Three slots of one line each, newest at the bottom, a hundred frames apiece.
 * run_level's plane loop blits them through blit_rows_masked while their
 * countdowns last, which is what the three overlay images built in its setup are
 * for. Every one of the four ways a level can end posts one of these.
 *
 * Posting shuffles slots 1 and 2 down into 0 and 1 and reuses slot 0's image for
 * the new line - so the images rotate rather than being reallocated. Only the
 * `right` edge of each viewport travels with the shuffle, because that is the
 * only part that depends on the text: it is set from text_width below.
 *
 * A null format does nothing at all, which is how a caller says "no message".
 */

desc_t far  *message_image[3];   /* 0x210c */
viewport_t   message_rect[3];    /* 0x2118 */
uint8_t      message_time[3];    /* 0x2154 */

void far message_post(const char far *fmt, const char far *arg)
{
    char        line[0x6a];
    desc_t far *reused;
    int16_t     i;

    if (!fmt)
        return;
    if (arg)
        sprintf(line, "%s %s", fmt, arg);
    else
        sprintf(line, "%s", fmt);

    reused = message_image[0];
    for (i = 0; i < 2; i++) {
        message_image[i]      = message_image[i + 1];
        message_rect[i].right = message_rect[i + 1].right;
        message_time[i]       = message_time[i + 1];
    }
    message_time[2]  = 0x64;
    message_image[2] = reused;

    image_clear(reused, 0);
    draw_string(reused, line, 0, 0);
    message_rect[2].right = (int16_t) (text_width(line) + viewport_game.left + 4);
}

/* ==========================================================================
 * Loading a level, 0x088fa
 *
 * Read out of the disassembly on 2026-08-05, not transcribed from a native, so
 * unlike the leaves above **none of this has been compared against anything**.
 * The check it is waiting for is a dump of the guest's own loaded state against
 * this one's - see docs/notes/run-level.md, which also has the level format
 * this reads, field by field.
 *
 * It is not reached from run_level: game_main calls f_1102a just before
 * run_level(0), and that calls this.
 * ======================================================================== */

uint8_t      sprite_set_id;      /* 0x2103 - the level's own 'C' sprite set */
table_t      level_sprites;      /* 0x1fec - block type 0x43, chosen by
                                  * sprite_set_id. It carries a palette
                                  * slice of its own, which is why a level
                                  * that never loads it draws in the
                                  * menu's colours */
uint8_t      solid_count;        /* 0x2031 */
solid_t far *solids;             /* 0x202d */
int16_t      level_flags[7];     /* 0x201e - one per word */
uint8_t      scenery_count;      /* 0x2000 - a byte: the loader
                                  * clears and increments it as one */
int16_t      timer_period;       /* 0x2001 - frames per tick of [0x2003] */
uint8_t      next_level;         /* 0x2102 */
char far    *level_text;         /* 0x200f */
uint8_t      ambience_on;        /* 0x2015 */
int16_t      pair_slots;         /* 0x18d1 - two per mirrored entity, and what
                                  * run_level's far allocation is sized from */
float        level_frac[4];      /* 0x13e1, 0x13e5, 0x13e9, 0x13ed */
int16_t      level_seed;         /* 0x2039 - what run_level srand()s */
int16_t      picked_index;       /* 0x18f3 - which entity the pointer is over */
entity_t far *picked;            /* 0x18ef - and a pointer to it */
int16_t      g_217d;             /* 0x217d - a once-only gate on tool 0x15 */
int16_t      g_2100;             /* 0x2100 - what the level script last set */
int16_t      g_dab;              /* 0x0dab - is the flock right of the cursor */
uint8_t      too_deep_count;     /* 0x20ff - tool_use's complaint counter */
uint8_t      tool_prev;          /* 0x1789 - the selection last frame */
uint8_t      tool_announce;      /* 0x178a - frames of "you have got X" */
int16_t      blink_enable;       /* 0x2157 - from the level's first flag */
scene_t      tool_scene;         /* 0x178c - two entities: the tool cursor */
int16_t      blink_toggle;       /* 0x0ddd */
int16_t      blink_countdown;    /* 0x0ddf */
int16_t      level_timer;        /* 0x2003 - counts down at one per [0x2001] */
int16_t      can_finish;         /* 0x2009 - decides one of the four endings */
int16_t      can_finish_alt;     /* 0x200b - and another */
int16_t      level_outcome;      /* 0x200d - what run_level returns == 2 */
int16_t      g_2018, g_1fd8, g_1fda;
int16_t      play_log;           /* 0x51d - the fprintf gate, cleared here and
                                  * nothing has been seen to set it */

/* ------------------------------------------------------- 0x11013: clock_seed
 *
 * The seed a level is played from when nothing else supplies one. The original
 * reads the runtime's clock at 0:0x17df into a four-byte local and returns what
 * that call left in AX; any value works as long as it varies, since run_level
 * srand()s it. A demo overrides it with its own recorded seed, which is what
 * makes a recording replay.
 */
int16_t far clock_seed(void)
{
    return (int16_t) time(NULL);
}

/* --------------------------------------------------------- 0x1240f: load_demo
 *
 * A demo is not a recording of the mouse. It is a seed, a level, and **three
 * tables of events** - which is why replaying one needs nothing but the same
 * seed: the level plays itself out of those tables, and rand() lands in the same
 * places both times.
 *
 *   [0x203f]  six bytes a record, count [0x2049]  - what 0x0d471 fires
 *   [0x203b]  three bytes a record, count [0x2047] - the tool changes, 0x0d4c2
 *   [0x2043]  three bytes a record, count [0x204b] - the level script
 *
 * Each record's fields are read out of order - the two words that come first in
 * the file go to +2 and +4, and the one that comes last to +0 - so the file's
 * order is not the record's.
 *
 * The block is 0x52 in whichever egg the demo names. It begins with two strings:
 * when the first is empty the demo belongs to the egg it was found in, and when it
 * is not, it names an egg by id and find_egg_by_id goes looking.
 */
int16_t far load_demo(uint8_t index)
{
    int16_t   egg   = demo_index[index].egg;        /* +6 */
    int16_t   block = demo_index[index].first;      /* +4 */
    char far *want, *id;
    uint16_t  i;

    if (!egg_find_block(0x52, (uint8_t) block, egg))
        return 0;

    want = egg_read_string(egg_stream);             /* 0x12462 */
    id   = egg_read_string(egg_stream);
    if (*want)
        find_egg_by_id(want, id);                   /* 0x12499 */
    else
        episode_egg_index = egg;                    /* 0x124a1 */
    free(want);
    free(id);

    level_seed      = egg_read_byte(egg_stream);    /* 0x124cb */
    level_attempted = egg_read_byte(egg_stream);

    event_count = egg_read_byte(egg_stream);        /* 0x124e9 */
    event_table = malloc((size_t) event_count * 6);
    if (!event_table)
        fatal(out_of_memory, 0);
    for (i = 0; i < event_count; i++) {             /* 0x12534 */
        uint8_t far *r = event_table + i * 6;

        *(uint16_t far *) (r + 2) = (uint16_t) egg_read_word(egg_stream);
        *(uint16_t far *) (r + 4) = (uint16_t) egg_read_word(egg_stream);
        *(uint16_t far *) (r + 0) = (uint16_t) egg_read_word(egg_stream);
    }

    tool_event_count = egg_read_byte(egg_stream);   /* 0x125a0 */
    tool_event_table = malloc((size_t) tool_event_count * 3);
    if (!tool_event_table)
        fatal(out_of_memory, 0);
    for (i = 0; i < tool_event_count; i++) {        /* 0x125ec */
        uint8_t far *r = tool_event_table + i * 3;

        r[2] = egg_read_byte(egg_stream);
        *(uint16_t far *) r = (uint16_t) egg_read_word(egg_stream);
    }

    script_count = egg_read_byte(egg_stream);       /* 0x12631 */
    script_table = malloc((size_t) script_count * 3);
    if (!script_table)
        fatal(out_of_memory, 0);
    script_at = 0;                                  /* 0x12679 */
    for (i = 0; i < script_count; i++) {            /* 0x12683 */
        uint8_t far *r = script_table + i * 3;

        r[2] = egg_read_byte(egg_stream);
        *(uint16_t far *) r = (uint16_t) egg_read_word(egg_stream);
    }

    egg_block_end();
    return 1;
}

/* -------------------------------------------------- 0x126db: pick_random_demo
 *
 * Which demo the idle menu plays: seeded from the clock so it is not the same one
 * every time, and the score and lives set as a game would - a demo is played as
 * though someone had just started, five lives and nothing scored.
 */
int16_t far pick_random_demo(void)
{
    game_srand((unsigned) clock_seed());                /* 0x126e2 */
    score = 0;
    lives = 5;
    return load_demo((uint8_t) (game_rand() % g_2038));  /* 0x12711 */
}

/* ---------------------------------------------------- 0x0e088: tool_selected
 *
 * The selection and the tool are two different things, and this is the only place
 * one becomes the other. tool_events and the played input move `tool_at`; nothing
 * looks at that except here, which turns it into `tool_type` - and `tool_type` is
 * what level_event dispatches on. Without this a demo's recorded tool changes are
 * invisible: the selection moves and every click still uses the first tool.
 *
 * A selection past the end of the list is refused and put back, which is how the
 * played input can walk off the end harmlessly.
 *
 * The announcement is `[0x178a]`, a countdown of 2n+3 frames set from whichever
 * slot was chosen; run_level's arrival check reads it, and while it runs the tool
 * scene's second entity is a type 0xf.
 */
void far tool_selected(int16_t slot)
{
    if (tool_at != tool_prev) {
        /* 0x0e091 logs "%05u TOOL %i" to the play log, which nothing enables. */
        if (tool_at >= tool_count) {               /* 0x0e0b9 - out of range */
            tool_at = tool_prev;
        } else {
            tool_type = tool_list[tool_at];        /* 0x0e0d7 */
            scenes[3].count = 0;                   /* drop the highlight */
            tool_announce = (uint8_t) (slot * 2 + 3);
            /* 0x0e0ea sets entity ZERO to 0x0f, and which of the two matters
             * more than it looks. Entity 1 is the tool's icon and entity 0 the
             * box around it, so this hides the box for the length of the
             * countdown and leaves the icon alone - and the icon is still
             * sitting on the OLD slot, drawing the same picture the HUD drew
             * there once at level start, so nothing is damaged.
             *
             * Setting entity 1 instead paints 0x0f - which is sprite 42, the
             * empty slot - over the old tool's icon for three frames, and then
             * the pair moves away and leaves the hole. That is exactly how "the
             * HUD clears the other tools" was reported. */
            entity_set_type(&tool_scene.entities[0], 0x0f);
            sound_play_guarded(3, 1);
        }
    }
    if (tool_announce) {                           /* 0x0e106 */
        tool_announce--;
        /* 0x0e111 clears the word at +0x48 of the tool scene's entities, which is
         * entity 1's +0x1f - its animation frame. */
        tool_scene.entities[1].frame = 0;
    }
}

/* ------------------------------------------------------ 0x0d471: demo_events
 *
 * The demo's own input, and it is three words a record: when the first matches the
 * level clock, the other two are a point and level_event is called on it. So a
 * recorded game is a list of "at frame N, click here" - which is why a demo needs
 * the level's seed and nothing else.
 *
 * Every record is looked at every frame and the clock is compared for equality, so
 * a record whose frame is skipped is a record that never fires.
 */
void far demo_events(void)
{
    uint16_t i;

    for (i = 0; i < event_count; i++) {
        uint8_t far *r = event_table + i * 6;

        if (*(uint16_t far *) r == (uint16_t) level_clock)
            level_event(*(int16_t far *) (r + 2), *(int16_t far *) (r + 4));
    }
}

/* One probe of the level's terrain, bounded.
 *
 * The original probes off the edges of its own backdrop and carries on. The step
 * loop at 0x081f2 is the clear case: it tries every d from the entity's speed
 * down to -4, so an entity standing on row 0 - a duck that has just been carried
 * to the top of the level by the rocket - reads `rows[-1]` through `rows[-4]`.
 * In real mode that is the four bytes in front of the row table and whatever
 * they happen to point at; there is no fault and the game never notices.
 *
 * Here it is a segfault, which is what the backtrace from the rocket launch
 * shows. The two axes are not the same question, and it took a bug report to
 * see that:
 *
 * **Off the left or right of a row, the guest reads its own allocator.** Each row
 * is a separate farmalloc, so x = -1 is the last byte of that block's header and
 * x = w the next block's. Measured across three snapshots and 880 rows, both are
 * `0x01` every single time, and x = -2 is varied but never zero. So the original
 * behaves as though the level were walled, and this returns solid to match -
 * which is measurement, not choice.
 *
 * It matters because of the drift at 0x07f2d: a duck compares the ground two
 * pixels either side and leans toward whichever is open. Reading the outside as
 * empty makes every duck near the left edge lean off it, which on a level whose
 * column 0 is open - 11 is one, 13 solid pixels in 160 rows - walks the flock
 * into the void. That is the "ducks fall off the left edge" report.
 *
 * **Off the top or bottom there is nothing to match.** That indexes the row
 * TABLE out of range and dereferences whatever pointer comes back, so the
 * original reads somewhere unknowable. Empty is the choice, and it is the useful
 * one: it leaves an entity moving, and 0x08565 is a whole section for an entity
 * that has left the level, so leaving is a state the game expects to reach.
 */
static uint8_t terrain_at(int32_t y, int32_t x)
{
    if (y < 0 || y >= backdrop.h)
        return 0;                    /* the row table - unknowable, so empty */
    if (x < 0 || x >= backdrop.w)
        return 1;                    /* the row's own header - always non-zero */
    return backdrop.rows[y][x];
}

/* ================================================== 0x0ce2e: the colour chart
 *
 * P during a played level, and only with cheat_state[5] - the word for which is
 * COLOURMAP, which is what this draws and not a pause at all. It had been
 * stubbed here as "the pause screen" on the strength of the key alone; the cheat
 * table in the egg's 0xfe block names it.
 *
 * A full-screen image with the 256 palette entries as a 16x16 grid of 10x10
 * swatches - colour i at row (i / 16) * 10, column (i % 16) * 10, so the chart
 * is 160x160 in the top-left corner and the rest is whatever alloc_image left,
 * which is colour 1 and not 0. Checked against the guest: stopping the original
 * one instruction before its flip and reading the page back, every pixel inside
 * the chart matches and all 38400 outside it are 1.
 *
 * Then the four planes, a flip, and a blocking getch: the game stands still
 * until a key is pressed, which is the only sense in which it pauses. The image
 * is freed on the way out, so the chart is gone when play resumes and the caller
 * does not have to redraw anything.
 */
void far pause_screen(void)
{
    desc_t  page;                                  /* [bp-0x1a] */
    int16_t i, row, plane;                         /* [bp-2], si, di */

    page.w = 0x140;                                /* 0x0ce36 */
    page.h = 0xc8;                                 /* 0x0ce3b */
    alloc_image(&page, 0, 0, 0, 1);                /* 0x0ce4e */

    for (i = 0; i < 0x100; i++) {                  /* 0x0ceb1 */
        int16_t top = (int16_t) (i / 16 * 10);     /* 0x0ce5b - di */
        int16_t x   = (int16_t) (i % 16 * 10);     /* 0x0ce6b - [bp-4] */

        /* si runs from di while di + 10 > si, so ten rows. */
        for (row = top; row < top + 10; row++)     /* 0x0cea5 */
            memset(page.rows[row] + x, i, 10);     /* 0x0ce9c - 0:0x4c09 */
    }

    for (plane = 0; plane < 4; plane++) {          /* 0x0cee8 */
        set_plane((uint8_t) plane);                /* 0x0cec4 */
        blit_rows(&page, viewport_screen, 0);      /* 0x0cedf */
    }
    page_flip();                                   /* 0x0ceee - 0x14d4b, which
                                                    * wraps to 0x04d4b */
    key_read();                                    /* 0x0cef2 - 0:0x2814, getch,
                                                    * and it blocks */
    resource_release(&page);                       /* 0x0cefd */
}

/* ============================================== 0x0cf07: played_tool_events
 *
 * A played level's input, and the counterpart of tool_events: that one moves the
 * selection from the demo's table, this one from the keyboard and the mouse.
 * run_level calls exactly one of the two.
 *
 * **The tool changes three ways** - the cycle button, the arrow keys and the
 * digits - and none of them bounds-checks. They do not have to: tool_selected
 * refuses a selection past the end of the list and puts the old one back, so
 * walking off either end is harmless by design.
 *
 * Extended keys arrive as `0x100 | scan code`, which input_poll builds from the
 * BIOS's zero-then-scancode pair - so left is 0x14b and right 0x14d.
 *
 * The rest are the debug and display keys, two of them behind cheats. `fast` is
 * the caller's own flag and not a tool slot: D toggles it, and it both doubles
 * the sound rate and lengthens the tool announcement, which is why
 * tool_selected is given it.
 */
void far played_tool_events(int16_t far *fast)
{
    /* 0x0cf0d. The cycle button - button_map[1], whatever it is bound to -
     * steps to the next tool and wraps. */
    if (g_18e3 && tool_count) {
        tool_at++;
        if (tool_at == tool_count)
            tool_at = 0;
    }

    switch (last_key) {
    case 0:                                        /* 0x0cfec - nothing typed */
        break;

    case 0x14b:  tool_at--;  break;                /* 0x0d004 - left arrow */
    case 0x14d:  tool_at++;  break;                /* 0x0d00b - right arrow */

    case 0x1b: case 0x51: case 0x71:               /* ESC, Q, q */
        kill_all_ducks();                          /* 0x0cffd */
        break;

    case 0x50: case 0x70:                          /* P, p - pause, if allowed */
        if (cheat_state[5])                        /* 0x0cfef - [0x50f] */
            pause_screen();                        /* 0x0ce2e */
        break;

    case 0x23:                                     /* 0x0d012 - '#' */
        if (cheat_state[0])                        /* [0x505] */
            level_outcome = 1;                     /* finish the level outright */
        break;

    case 0x44: case 0x64: {                        /* 0x0d022 - D, d */
        int16_t rate;

        *fast = !*fast;
        rate  = (int16_t) (11000 << *fast);        /* 0x2af8, doubled */
        if (sound_available)
            sound_set_rate(rate);                  /* 0x149e:0x346 */
        break;
    }

    case 0x63:                                     /* 0x0d057 - c */
        scroll_smooth = !scroll_smooth;
        break;

    case 0x2e: case 0x3e:                          /* 0x0d064 - . and > */
        if (gamma_level < 0x1f) {
            gamma_level++;
            palette_apply_gamma();
            build_washed_ramp();
            palette_upload();
        }
        break;

    case 0x2c: case 0x3c:                          /* 0x0d07d - , and < */
        if (gamma_level) {
            gamma_level--;
            palette_apply_gamma();
            build_washed_ramp();
            palette_upload();
        }
        break;

    case 0x5d: case 0x7d:                          /* 0x0d096 - ] and } */
        if (game_speed < 0x1f)
            game_speed++;
        break;

    case 0x5b: case 0x7b:                          /* 0x0d0a3 - [ and { */
        if (game_speed)
            game_speed--;
        break;

    default:                                       /* 0x0d0b0 */
        if (last_key >= 0x31 && last_key <= 0x39)  /* 1..9 pick a tool */
            tool_at = (uint8_t) (last_key - 0x31);
        break;
    }
}

/* --------------------------------------------------------- 0x0d0c8: level_event
 *
 * What a tool does at a point - the click handler, and on the demo path what the
 * six-byte event table fires. Six tools have their own behaviour and everything
 * else falls to a default that puts the tool's own entity down.
 *
 * The first thing it does is ask whether the target is empty: a zero byte in the
 * backdrop. Most of the arms refuse on solid ground, with sound 0x17 for "no".
 * Then y is incremented, so what is placed sits one row below what was tested.
 *
 * `y == 0` is turned into 1 before any of that, which is why an event at the very
 * top of a level behaves as though it were one row down.
 */
void far level_event(int16_t x, int16_t y)
{
    int16_t clear;

    if (y == 0)                                    /* 0x0d0d8 */
        y = 1;
    /* 0x0d0db writes the clock and the point to the play log here. [0x51d] gates
     * it and nothing sets it, and the FILE * at [0x51f] is not kept on this side. */
    clear = terrain_at(y, x) == 0;                 /* 0x0d0fc */
    y++;                                           /* 0x0d11a */

    switch (tool_type) {
    case 0x0d:                                     /* 0x0d14b - send the leader */
        if (!clear)
            break;
        if (scenes[0].flag != 0xff
            && scenes[0].entities[scenes[0].flag].type == 1) {
            sound_play_guarded(0x0a, 1);
            g_1ff2 = x;                            /* where it is being sent */
            g_1ff4 = y;
            entity_set_type(&scenes[0].entities[scenes[0].flag], 0x20);
        } else {
            message_post(menu_text[74], 0);        /* 0x0d19f - no leader */
            sound_play_guarded(0x17, 1);
        }
        break;

    case 0x15:                                     /* 0x0d1cc - act on what is under */
        if (picked) {
            switch (picked->type) {
            case 0x13:                             /* 0x0d1ec */
                tool_use((int16_t) picked->x, (int16_t) picked->y, 0x18);
                entity_set_type(picked, 0);
                break;
            case 0x28:                             /* 0x0d217 */
                entity_set_type(picked, 0x31);
                particles_spawn((int16_t) picked->x, (int16_t) picked->y, 0x28);
                sound_play_guarded(6, 3);
                break;
            default:
                sound_play_guarded(0x17, 1);
                break;
            }
        }
        scenes[3].count = 0;                       /* 0x0d258 - drop the highlight */
        g_217d = 1;
        break;

    case 0x12:                                     /* 0x0d267 - choose a leader */
        if (scenes[0].flag == picked_index)
            break;                                 /* already this one */
        if (scenes[0].flag != 0xff
            && scenes[0].entities[scenes[0].flag].type != 1)
            break;                                 /* the old one is not a leader */
        if (scenes[0].flag != 0xff)
            entity_set_type(&scenes[0].entities[scenes[0].flag], 2);
        if (picked && (picked->type == 2 || picked->type == 4)) {
            scenes[0].flag = picked_index;         /* 0x0d2d6 */
            entity_set_type(&scenes[0].entities[scenes[0].flag], 1);
            scenes[0].entities[scenes[0].flag].f16 = 0;
            sound_play_guarded(0x13, 1);
        } else {
            scenes[0].flag = 0xff;                 /* 0x0d318 - nobody */
            sound_play_guarded(0x14, 1);
        }
        break;

    case 0x50:                                     /* 0x0d32c - cash something in */
        if (picked && picked->type != 0x17 && picked->type != 0) {
            eaten_countdown = 0;
            entity_set_type(picked, 0x17);
            score += scenes[0].count * 8;          /* eight a duck still out */
            sound_play_guarded(9, 1);
        }
        break;

    case 0x52:                                     /* 0x0d37a - another duck */
        if (!g_2016) {
            if (scene_add(&scenes[0], (int16_t) scenes[2].entities[0].x,
                          0x64, 0x54, 0))
                duck_count++;
            else
                sound_play_guarded(0x17, 1);
        }
        break;

    default:                                       /* 0x0d3b1 */
        if (type_flags[tool_type] & 2) {
            /* A mirrored tool goes into scene 5, and the sound says which. */
            if (!clear) {
                sound_play_guarded(0x17, 1);
            } else if (scene_add(&scenes[5], x, y, tool_type, 5)) {
                sound_play_guarded((int16_t) ((tool_type == 0x28) + 0x23), 1);
            } else {
                message_post(menu_text[75], 0);    /* 0x0d3f6 - no room */
                sound_play_guarded(0x17, 1);
            }
        } else if (clear && tool_type != 0) {      /* 0x0d43c - an ordinary one */
            sound_play_guarded(8, 1);
            scene_add(&scenes[3], x, y, tool_type, 5);
            g_1fda = 1;
        } else {
            sound_play_guarded(0x17, 1);
        }
        break;
    }
}

/* ------------------------------------------------- 0x0d715: scene_update_all
 *
 * Every entity of one scene through entity_update. run_level calls it on three of
 * the six at 0x0e11b, which is where a frame's walking and falling happens.
 */
void far scene_update_all(scene_t far *s)
{
    int16_t i;

    for (i = 0; i < s->count; i++)
        entity_update(&s->entities[i], 0, 0);
}

/* tool_use's published state, and the bridge it leaves growing.
 *
 * A bridge is two ends walking away from where it was placed, and 0x07646 takes
 * a pointer to one of them - so the two are records rather than loose words. */
int16_t tool_in_use;             /* 0x1fd6 - which tool, for the frame to read */
bridge_end bridge_left;          /* 0x1fe0 - x, y, alive */
bridge_end bridge_right;         /* 0x1fe6 */
int16_t bridge_span;             /* 0x1fdc - pixels an end walks a frame */
int16_t bridge_drop;             /* 0x1fde - rows it falls per pixel: 1 or 0 */
int16_t bridge_left_live;        /* 0x20fb - was it alive at the frame's start */
int16_t bridge_right_live;       /* 0x20fd */

/* ------------------------------------------------------ 0x0751b: blast_terrain
 *
 * The bomb's hole. It walks the bomb's own sprite over the backdrop and writes 0
 * wherever the sprite has a pixel - so the shape of the blast IS the shape of the
 * sprite, and "terrain" here is just the backdrop image the compositor draws and
 * the physics probes. Erasing it is the whole of the damage.
 *
 * Clipping is the usual four cases, and the two that matter carry a row skip:
 * off the left adds to both the skip and the source cursor, off the right adds
 * only to the skip, and off the top winds the source on by whole rows. The
 * bounds compares are unsigned in the original, so a coordinate past the level
 * wraps rather than reading as negative.
 *
 * Then every solid is stamped back in. A bomb is not allowed to destroy the
 * level's scenery, and rather than test for it while erasing, the original just
 * puts all of it back afterwards.
 */
void far blast_terrain(int16_t x, int16_t y, int16_t index)
{
    sprite_t far *sp   = &sprite_table.base[index];
    int16_t       skip = 0;                        /* [bp-4] */
    int16_t       src  = 0;                        /* di */
    int16_t       right, bottom, row, col, i;

    x -= sp->ox;                                   /* 0x07545 */
    y -= sp->oy;
    right  = (int16_t) (x + sp->w);
    bottom = (int16_t) (y + sp->h);

    if (x < 0) {                                   /* 0x0756f */
        skip -= x;
        src  -= x;
        x     = 0;
    } else if ((uint16_t) right > (uint16_t) level_w) {
        skip += (int16_t) (right - level_w);
        right = level_w;
    }

    if (y < 0) {                                   /* 0x075a3 */
        src -= (int16_t) (y * sp->w);
        y    = 0;
    } else if ((uint16_t) bottom > (uint16_t) level_h) {
        bottom = level_h;
    }

    for (row = y; row < bottom; row++) {           /* 0x075ca */
        for (col = x; col < right; col++)
            if (sp->pixels[src++])
                backdrop.rows[row][col] = 0;
        src += skip;                               /* 0x07604 */
    }

    for (i = 0; i < solid_count; i++)              /* 0x07612 */
        stamp_solid(&solids[i], &backdrop);
}

/* ------------------------------------------------- 0x0739c: stamp_sprite_into
 *
 * blast_terrain's twin, and the two differ in exactly three places: this writes
 * the sprite's own pixel where that one writes 0, it clips against the
 * DESTINATION's width and height rather than the level's, and it takes the
 * sprite as a pointer rather than an index. So one carves terrain away and this
 * builds it, which is how a bridge or a brick becomes ground the ducks can walk
 * on - there is no separate notion of a bridge, only backdrop.
 */
void far stamp_sprite_into(int16_t x, int16_t y, sprite_t far *sp,
                           desc_t far *dest)
{
    int16_t skip = 0;                              /* [bp-4] */
    int16_t src  = 0;                              /* di */
    int16_t right, bottom, row, col;

    x -= sp->ox;                                   /* 0x073ae */
    y -= sp->oy;
    right  = (int16_t) (x + sp->w);
    bottom = (int16_t) (y + sp->h);

    if (x < 0) {                                   /* 0x073d8 */
        skip -= x;
        src  -= x;
        x     = 0;
    } else if ((uint16_t) dest->w < (uint16_t) right) {
        skip += (int16_t) (right - dest->w);
        right = dest->w;
    }

    if (y < 0) {                                   /* 0x07416 */
        src -= (int16_t) (y * sp->w);
        y    = 0;
    } else if ((uint16_t) dest->h < (uint16_t) bottom) {
        bottom = dest->h;
    }

    for (row = y; row < bottom; row++) {           /* 0x07444 */
        for (col = x; col < right; col++) {
            uint8_t px = sp->pixels[src++];

            if (px)
                dest->rows[row][col] = px;
        }
        src += skip;
    }
}

/* ----------------------------------------------------- 0x0799c: ground_check
 *
 * Whether there is enough water under a bridge, and the anti-stacking warning.
 * From the row below the point it scans down the same column of the backdrop
 * counting pixels below 0xc8 - anything that is not solid - and stops at sixteen
 * of them or at the bottom of the level. If that took more than 28 rows it
 * complains, and the three messages escalate:
 *
 *     "Careful... don't stack bridges..."
 *     "THIS IS YOUR LAST WARNING! NO BRIDGE STACKING!"
 *     "Oops, how did that happen?"
 *
 * The third is the last: too_deep_count reaching 3 calls 0x07955 instead of
 * making a noise, and that routine is not written.
 *
 * It takes x by pointer and never writes through it - the pointer buys nothing,
 * and an earlier note here guessed that it nudged the point, which it does not.
 */
void far ground_check(int16_t far *x, int16_t y)
{
    int16_t found = 0;                             /* [bp-2] */
    int16_t rows  = 0;                             /* di */
    int16_t row   = (int16_t) (y + 1);             /* si */

    while (row < level_h && found < 0x10) {        /* 0x079d4 */
        if (backdrop.rows[row][*x] < 0xc8)
            found++;
        row++;
        rows++;
    }
    if (rows <= 0x1c)                              /* 0x079e0 */
        return;

    message_post(menu_text[41 + too_deep_count], 0);
    too_deep_count++;
    if (too_deep_count == 3)
        kill_all_ducks();                          /* 0x07a22 */
    else
        sound_play_guarded(0x2d, 1);
}

/* --------------------------------------------------- 0x07646: one bridge end
 *
 * Walks one end of a bridge `bridge_span` pixels in `dir`, dropping `bridge_drop`
 * rows for each - so 4 pixels level for a horizontal one, 3 pixels down a slope
 * for a diagonal. It stops for good the moment it is over solid backdrop, or
 * once it leaves the level, and returns whether it is still going.
 *
 * The bump when it stops is sound 0x11, played once because `alive` is cleared
 * with it.
 */
static int16_t bridge_step_end(bridge_end far *end, int16_t dir)
{
    int16_t i;

    if (!end->alive)                               /* 0x07651 */
        return 0;
    if ((uint16_t) end->y >= (uint16_t) level_h || end->x < 0) {
        end->alive = 0;                            /* 0x0766e - off the level */
        return 0;
    }

    /* The bounds test above happens once, and then the loop steps up to
     * bridge_span pixels - so the last few probes of a bridge running off the
     * edge are past the row, or past the row table. terrain_at is what the
     * physics already uses for exactly this, and reads outside the level as
     * empty; what the original reads there is a property of its heap and can
     * only be chosen, not matched. */
    for (i = 0; i < bridge_span; i++) {            /* 0x0767b */
        if (terrain_at(end->y, end->x) && end->alive) {
            sound_play_guarded(0x11, 1);           /* 0x076aa - it hit something */
            end->alive = 0;
        }
        end->x += dir;                             /* 0x076c1 */
        end->y += bridge_drop;
    }
    return end->alive;
}

/* ------------------------------------------------------ 0x076e2: bridge_grow
 *
 * One frame of a bridge building itself. Both ends step, and each that was alive
 * when the frame began stamps a sprite where it was - so the bridge is laid down
 * behind the ends as they travel, and stops growing on the side that hit
 * something while the other carries on.
 *
 * **The sprite is picked at random from the level's own set**, one per segment,
 * which is what stops a bridge being a row of identical tiles.
 *
 * The right end does not stamp while it is still on top of the left one, which
 * is only true on the first frame, when both are at the click.
 */
static void bridge_grow(void)
{
    int16_t lx = bridge_left.x,  ly = bridge_left.y;    /* si, [bp-2] */
    int16_t rx = bridge_right.x, ry = bridge_right.y;   /* di, [bp-4] */
    int16_t still_left  = bridge_step_end(&bridge_left, -1);
    int16_t still_right = bridge_step_end(&bridge_right, 1);

    g_1fd8 = (bridge_left_live || bridge_right_live) ? 1 : 0;   /* 0x0771e */

    if (bridge_left_live)                          /* 0x07736 */
        stamp_sprite_into(lx, ly,
                          &level_sprites.base[game_rand() % level_sprites.count],
                          &backdrop);
    if (bridge_right_live && lx != rx)             /* 0x07768 */
        stamp_sprite_into(rx, ry,
                          &level_sprites.base[game_rand() % level_sprites.count],
                          &backdrop);

    bridge_left_live  = still_left;                /* 0x0779e */
    bridge_right_live = still_right;
}

/* -------------------------------------------------------- 0x078a6: tool_step
 *
 * One line of run_level's frame, right after input_poll: if a tool is in progress
 * and is not being applied, either grow it - only the two bridges grow - or
 * declare it finished. Everything else's effect happened when it was used.
 */
void far tool_step(void)
{
    if (!g_1fd8 || g_1fda)                         /* 0x078a9 */
        return;
    if (tool_in_use == 0x0c || tool_in_use == 0x19)
        bridge_grow();                             /* 0x078c7 */
    else
        g_1fd8 = 0;                                /* 0x078cc */
}

/* ========================================================= 0x07a36: tool_use
 *
 * What a tool does where it is used, and the only place a tool has an effect.
 * entity_update calls it when an entity carrying a tool lands - the `applying`
 * argument, which the frame passes as 1 for exactly one scene.
 *
 * It first says what is happening: the tool at [0x1fd6] and a busy flag at
 * [0x1fd8], which the frame reads to draw the cursor as 0x16 while one is in
 * progress. Then one of four arms.
 */
void far tool_use(int16_t x, int16_t y, int16_t tool)
{
    tool_in_use = tool;                            /* 0x07a44 */
    g_1fd8      = 1;

    switch (tool) {
    /* 0x07a74. The bomb and the balloon: leave the thing itself in scene 1 as a
     * type 0x17, and blow the hole. The sprite the hole is cut with is
     * anim_script[0x17][0] - a fixed address in the original, so it is the
     * bomb's own first frame whichever of the two placed it. */
    case 0x18:
    case 0x36:
        sound_play_guarded(9, 1);
        scene_add(&scenes[1], x, y, 0x17, 0);
        blast_terrain(x, y, anim_script[0x17][0]);
        g_1fd8 = 0;
        break;

    /* 0x07aad. The two bridges, and the only arm that stays busy across frames:
     * it puts an anchor and a moving end at [0x1fe0] and [0x1fe6], both (x, y-1),
     * which is what makes it a rubber band. */
    case 0x0c:
    case 0x19: {
        int16_t diagonal = (tool == 0x0c);

        ground_check(&x, y);                       /* 0x07ab4 */
        bridge_left.x  = x;                        /* 0x1fe0 */
        bridge_left.y  = (int16_t) (y - 1);
        bridge_right.x = x;                        /* 0x1fe6 */
        bridge_right.y = (int16_t) (y - 1);
        bridge_left.alive  = 1;
        bridge_right.alive = 1;
        bridge_span    = (int16_t) (4 - diagonal); /* 0x1fdc */
        bridge_drop    = diagonal;                 /* 0x1fde */
        bridge_left_live  = 1;
        bridge_right_live = 1;
        break;
    }

    /* 0x07b12 and 0x07b61. Both stamp the tool's own first sprite into the
     * backdrop through 0x0739c - the brick, and everything with no arm of its
     * own - and differ only in which sound they make. 0x0739c is not written. */
    case 0x0e:
        sound_play_guarded(0x22, 1);
        g_1fd8 = 0;
        stamp_sprite_into(x, y, &sprite_table.base[anim_script[tool][0]],
                          &backdrop);
        break;

    default:
        sound_play_guarded(0x0e, 1);
        g_1fd8 = 0;
        stamp_sprite_into(x, y, &sprite_table.base[anim_script[tool][0]],
                          &backdrop);
        break;
    }
}

/* ================================================== 0x07bb2: entity_update
 *
 * One entity, one frame: what it decides to do, then the walking and falling that
 * every type shares, then what happens if that moved it out of the level. 3,082
 * bytes in the original and four parts:
 *
 *   the fall step, from anim_b[type]        0x07bba, table at image 0x08762
 *   what this type wants                    0x07c10, table at image 0x0873a
 *   the walk-and-fall core                  0x080bb
 *   having moved, or been blocked           0x0832f, table at image 0x08714
 *   having left the level                   0x08565, tables at 0x086d4 and 0x086ba
 *
 * **Gravity is not a velocity.** The core counts d down from the movement amount
 * to -5, probing backdrop.rows[y + d][x + facing] and requiring every row between
 * that one and the entity to be clear too. y grows downward, so it tries the
 * largest **fall** first, then smaller ones, then level, and finally **climbs** of
 * up to five pixels - one loop for falling off a ledge, walking along, and
 * stepping up onto something, in that order of preference.
 *
 * Read out and transcribed 2026-08-05, and **not compared against anything**. The
 * check it wants is a demo: run_level(1) needs no input and reseeds from the
 * level, so the guest and this can be stepped from one snapshot and their entity
 * positions diffed frame by frame.
 */
/* The two words after the entity are separate things, and mixing them up is easy:
 * [bp+0xa] is "a tool is being applied through this entity" - the branch at
 * 0x08345 and the clear of [0x1fda] at 0x086aa - while [bp+0xc] is "this one is
 * being driven by a script", which only type 1 reads, at 0x07dc6. */
void far entity_update(entity_t far *e, int16_t applying, int16_t scripted)
{
    int16_t depth   = -5;                          /* [bp-6] - how far it may fall */
    int16_t step    = 4;                           /* [bp-0xe] - the type's speed cap */
    int16_t moved   = 0;                           /* [bp-0x14] */
    int16_t stepped = 0;                           /* [bp-0x16] */
    int16_t active  = 1;                           /* [bp-0x18] - does it walk at all */
    int16_t blocked = 0;                           /* [bp-0xc] */
    int16_t facing, want, speed, d, i;

    switch (anim_b[e->type] - 1) {                 /* 0x07bd3 */
    case 0: step = 2;  break;                      /* 0x07c09 */
    case 1: return;                                /* 0x07c06 */
    case 2: e->y -= 1;  return;                    /* 0x07bf6 */
    case 3: step = 0;  break;                      /* 0x07bef */
    default: break;
    }

    switch (e->type) {                             /* 0x07c10 */
    case 0x36:                                     /* 0x07d89 */
        e->f15 = (uint8_t) 0xfe;
        break;
    case 0x1e:                                     /* 0x07d64 */
        if (g_2016)
            duck_dies(e, 1, 0);
        step   = -1;
        active = 1;
        break;
    case 0x25:                                     /* 0x07d0c */
        if (e->f23++ == 0x20) {
            entity_set_type(e, 0x26);
            e->f23 = 0;
        }
        break;
    case 0x26:                                     /* 0x07d37 */
        e->f23 += 2;
        if ((uint16_t) e->f23 >= 0xa) {
            entity_set_type(e, 0x22);
            e->f23 = 0;
        }
        break;
    case 1:                                        /* 0x07dc6 - the hero */
        if (scripted) {                            /* [bp+0xc] */
            e->f14 = (int8_t) g_2100;
            active = e->f14 != 0;
        } else {
            /* 0x07dec. One 32-bit signed compare of x against the cursor, twice
             * - `jg/jl` on the high words and `jae/jbe` on the low ones. An
             * earlier reading took that pair of halves for y and then x and
             * turned it into a two-axis comparison, which faces the hero the
             * wrong way whenever the cursor is above or below it. The button
             * being up in every snapshot hid it: `active` is 0 there, and the
             * clear at 0x080ed puts f14 back to 0 before anything can tell. */
            e->f14 = (int8_t) ((e->x < (int32_t) mouse_x)
                             - (e->x > (int32_t) mouse_x));
            active = button_a_down;
        }
        e->f23 = 0;
        break;
    case 2:                                        /* 0x07e83 - follows its leader */
    {
        /* imul then cwd, so the product is 16 bits sign-extended, not 32. */
        int32_t ahead = (int16_t) (e->f1a * (int8_t) e->f16);
        entity_t far *lead = e->lead;
        /* 0x07ea2 reads the lead's +0x0c and +0x0e - **prev_x**, where it was
         * when the frame began, not where it is now. That is what makes a line
         * of ducks trail rather than pile up: each one walks to where the one
         * in front was, a frame behind. Reading the live x instead put every
         * follower a step or two out and flipped the facing of any that had
         * caught up, which is what test_entity.py's walk sweep found on
         * snap002, snap003 and snap006.
         *
         * The guest dereferences this without checking, because only
         * flock_chain makes a type 2 and it always sets lead first. */
        int32_t tx = lead ? lead->prev_x - ahead : e->x;

        e->f14 = (int8_t) ((e->x < tx) - (e->x > tx));
        active = e->f19;
        e->f23 = 0;
        break;
    }
    case 4:                                        /* 0x07f11 - an ordinary duck */
        if (!level_flags[1]) {
            active = 0;
            break;
        }
        {
            /* 0x07f2d reads rows[y + 1], not rows[y]: a duck looks at the
             * ground under its feet two pixels either side, and drifts toward
             * whichever side is open. Probing its own row instead put the slope
             * one row too high, which test_entity.py caught on level 11 as a
             * duck a pixel out with the wrong facing. */
            int16_t slope = (terrain_at(e->y + 1, e->x + 2) == 0)
                          - (terrain_at(e->y + 1, e->x - 2) == 0);

            if ((int8_t) e->f15 < 4) {             /* 0x07f94 */
                if ((game_rand() & 3) == 0) {
                    if (e->param > 0) e->param--;
                    if (e->param < 0) e->param++;
                }
            } else {
                e->f23 = 5;
            }
            e->param = (int16_t) (slope * 2 + e->param);
            if (e->param < -0xc) e->param = -0xc;
            if (e->param >  0xc) e->param =  0xc;
            facing = (int8_t) e->f14;
            e->f14 = (int8_t) ((e->param > 0) - (e->param < 0));
            if ((int8_t) e->f14 != facing) {       /* it turned */
                if (e->f23) {
                    e->f14    = 0;
                    e->param  = 0;
                }
                e->f23 = 0;
            } else if (e->f23) {
                e->f23--;
            }
            active = 1;
        }
        break;
    /* 0x40 and 0x41 share these two, and that is the whole of the spring's
     * horizontal kick. The dispatch for types 0x40..0x53 is a JUMP TABLE at
     * cs:0x3a9a, not the compare chain above it - `sub bx, 0x40` at 0x07c6e -
     * and reading only the chain is how they came to be missing here. An
     * ordinary duck then had no case in this switch at all, so it fell to the
     * default and kept whatever facing it was walking with, while the hero flew
     * off sideways: exactly what a spring looked like.
     *
     * They also appear in the settled switch below, at 0x08427, which is where
     * a sprung duck lands. Both are real; this one runs while it is in the air. */
    case 0x1c: case 0x40:                          /* 0x07e47 */
        e->f14 = (int8_t) ((e->f21 < 0xf) + 1);
        break;
    case 0x33: case 0x41:                          /* 0x07e64 */
        e->f14 = (int8_t) (-(e->f21 < 0xf) - 1);
        break;
    case 0x4f: case 0x51:                          /* 0x07d94 - toward the cursor */
        step   = 0;
        e->f14 = (int8_t) (((int32_t) mouse_x + 4 - e->x) >> 3);
        active = 1;
        break;
    case 0x4a:                                     /* 0x07c80 - sinks and settles */
        e->y -= 1;
        if (e->y == 0) {
            entity_set_type(e, 2);
        } else if (terrain_at(e->y, e->x) != 0) {
            e->param = 1;
        } else if (e->param) {
            entity_set_type(e, 2);
        }
        return;
    case 0x52:                                     /* 0x07cf4 */
        e->f15 = (uint8_t) 0xfc;
        active = 1;
        break;
    case 0x53:                                     /* 0x07d04 */
        active = 1;
        break;
    case 0x43:                                     /* 0x080a3 */
        particles_spawn(e->x, e->y, 1);
        break;
    default:                                       /* 0x080bb */
        if (type_flags[e->type] & 1) {
            if (e->f14 == 0)
                e->f14 = (int8_t) ((g_dab << 1) - 1);
            active = 1;
        } else {
            active = 0;
        }
        break;
    }

    /* 0x080ed. Without a facing there is nothing to walk. */
    if (!active)
        e->f14 = 0;
    if ((int8_t) e->f15 < step)                    /* 0x080fb - one a frame */
        e->f15++;
    /* 0x08117 is `sar ax, 1`, which floors - and C's / 2 truncates toward zero,
     * so the two disagree on every ODD NEGATIVE speed. -1 is the case that
     * matters: the balloon's arm sets f15 to -2 every frame, the increment just
     * above takes it to -1, and the original then rises a pixel a frame where
     * `/ 2` gives 0 and it hangs in the air. Even values agree, which is why
     * everything that falls looked right. */
    speed  = (int16_t) ((int8_t) e->f15 >> 1);     /* 0x0810f - sar, not / 2 */
    facing = (int8_t) e->f14;

    /* 0x0812a. Once per pixel of intended movement. */
    do {
        want    = (int8_t) e->f15;
        blocked = 0;

        if (facing == 0) {                         /* 0x08143 - standing still */
            moved = 1;
            switch (e->type) {
            case 0x1e:                             /* 0x08160 - it lands and hatches */
                sound_play_guarded(1, 1);
                entity_set_type(e, 0);
                scene_add(&scenes[0], (int16_t) e->x, (int16_t) e->y, 2, 0);
                break;
            case 0x52:                             /* 0x08198 */
                entity_set_type(e, 0x53);
                /* fall through */
            case 0x53:                             /* 0x081a7 */
                tool_use((int16_t) e->x, (int16_t) e->y, 0x18);
                score += 5;
                e->f14 = (int8_t) -(int8_t) e->f14;
                break;
            default:                               /* 0x081d4 */
                if (type_flags[e->type] & 1)
                    e->f14 = (int8_t) -(int8_t) e->f14;
                break;
            }
        }

        /* 0x081f2. Both paths come here - an entity with no facing still falls,
         * it just probes its own column. Largest fall first, then level, then a
         * climb of up to five: the first d whose whole column is clear wins. */
        {
            for (d = speed; d > depth; d--) {
                if (stepped)
                    want = d * 2;
                stepped = 1;

                if (terrain_at(e->y + d, e->x + facing) != 0) {
                    blocked = 1;                   /* 0x08308 */
                    continue;
                }
                {
                    int16_t clear = 1;

                    for (i = d - 1; i >= 0; i--)   /* 0x0824a */
                        clear &= terrain_at(e->y + i, e->x + facing) == 0;
                    if (!clear)
                        continue;
                }
                e->x += facing;                    /* 0x0828c - it moves */
                if (e->x < 0)
                    e->x = 0;
                if (e->x > level_w)
                    e->x = level_w - 1;
                e->y += d;
                e->f15 = (uint8_t) want;
                e->f21++;
                d     = -5;
                moved = 1;
            }
        }
        depth  = 0;                                /* 0x08316 */
        facing -= (int8_t) e->f14;
    } while (!moved);

    /* 0x0832f. What it makes of having moved, or of being blocked. */
    if (e->type != 0 && blocked) {
        if (applying) {                            /* 0x0834b - [bp+0xa] */
            tool_use((int16_t) e->x, (int16_t) e->y, e->type);
            entity_set_type(e, 0);
            g_1fda = 0;
        } else {
            switch (e->type) {
            case 1: case 2: case 4:                /* 0x083d2 */
                if (e->f21 > 0x32)
                    duck_dies(e, 0, 1);
                break;
            case 0x1c: case 0x33:                  /* 0x083f0 */
                if (e->f21 > 0x32)
                    duck_dies(e, 0, 1);
                else {
                    entity_set_type(e, 1);
                    e->f14 = 0;
                }
                break;
            case 0x40:                             /* 0x08427 */
                if (e->f21 > 0x32)
                    duck_dies(e, 0, 1);
                else {
                    entity_set_type(e, 2);
                    e->f14 = 0;
                }
                break;
            case 0x39:                             /* 0x0845e */
                if (e->f21 > 8) {
                    sound_play_guarded(0x18, 1);
                    entity_set_type(e, 0x4b);
                    eaten_countdown = 0;
                }
                break;
            case 0x31:                             /* 0x0848b */
                entity_set_type(e, 0x32);
                sound_play_guarded(0x10, 3);
                particles_spawn((int16_t) e->x, (int16_t) e->y, 0x28);
                break;
            case 0x41:                             /* 0x08427 via the table */
                if (e->f21 > 0x32)
                    duck_dies(e, 0, 1);
                else {
                    entity_set_type(e, 2);
                    e->f14 = 0;
                }
                break;
            case 0x4b:                             /* 0x0845e via the table */
                if (e->f21 > 8) {
                    sound_play_guarded(0x18, 1);
                    entity_set_type(e, 0x4b);
                    eaten_countdown = 0;
                }
                break;
            case 0x53:                             /* 0x084be */
                entity_set_type(e, 0x52);
                tool_use((int16_t) e->x, (int16_t) e->y, 0x18);
                score += 5;
                break;
            case 0x43: case 0x44: case 0x45:       /* 0x084ea - it tumbles */
                if (e->f21 > 8) {
                    int16_t v = (int16_t) (2 - (e->f21 >> 2) - (game_rand() & 3));

                    e->f15 = (uint8_t) v;
                    if ((int8_t) e->f15 < (int8_t) 0xf8)
                        e->f15 = (uint8_t) 0xf8;
                    entity_set_type(e, 0x43);
                } else if (e->type == 0x43) {
                    entity_set_type(e, (int16_t) ((game_rand() & 1) + 0x44));
                }
                break;
            default:
                break;
            }
        }
        e->f21 = 0;                                /* 0x0855c */
    }

    /* 0x08565. Below the level's floor, or above its ceiling, and it is gone -
     * with whatever that means for its type on the way out. */
    if ((int32_t) e->y >= level_h + 8 || e->y <= 1) {
        switch (e->type) {
        case 6: case 7: case 8: case 9: case 0x0a:
            g_2018 = 1;                            /* 0x085f8 */
            break;
        case 1: case 2: case 4: case 0x1c: case 0x1e: case 0x20:
        case 0x21: case 0x33: case 0x40: case 0x41: case 0x4a:
            sound_play_guarded(7, 1);              /* 0x08601 */
            duck_count--;
            break;
        case 0x13:
            sound_play_guarded(0x25, 1);           /* 0x08613 */
            break;
        case 0x28:
            sound_play_guarded(0x24, 1);           /* 0x08620 */
            break;
        case 0x52:                                 /* 0x0862d */
            entity_set_type(e, 0x53);
            if (!g_2016
                && !scene_add(&scenes[0], (int16_t) scenes[2].entities[0].x,
                              0x64, 0x54, 0))
                duck_count--;
            break;
        case 0x47: case 0x4b: case 0x39: case 0x46:  /* 0x08665 */
            sound_play_guarded(0x19, 1);
            score += 0x19;
            message_post(menu_text[45], 0);
            eaten_countdown = 0;
            break;
        default:
            break;
        }
        entity_set_type(e, 0);                     /* 0x0869b */
        if (applying)
            g_1fda = 0;
    }
}

/* ------------------------------------------------------ 0x0981b: scene_retire
 *
 * The one place a scene's entity array ever shrinks. An entity is dead when its
 * type is 0, and the compaction takes one of two shapes depending on the scene's
 * `flag6`: with it set the survivors shuffle down one by one and keep their order,
 * with it clear the last entity is swapped into the hole, which is cheaper and
 * scrambles the order. Scene 2 is the one the loader sets it on.
 *
 * The `flag` field follows whatever it was pointing at: cleared to 0xff when that
 * entity is the one dying, and moved to the hole when the swap brings the last
 * entity - which is the hero index for scene 0 and what is being eaten for scene 2.
 *
 * An entity that lives gets one thing done to it: if its type has changed since
 * last frame, its animation restarts. That is why the type and its copy at +0x27
 * both exist.
 */
void far scene_retire(scene_t far *s)
{
    int16_t i, j;

    for (i = 0; i < s->count; i++) {
        entity_t far *e = &s->entities[i];

        if (e->type != 0) {                        /* 0x098be - it lives */
            if (e->f27 != e->type)
                e->frame = 0;                      /* restart its script */
            e->f27 = e->type;
            continue;
        }

        if (s->flag == i)                          /* 0x0983f */
            s->flag = 0xff;

        if (s->keep_order) {                          /* 0x09851 - keep the order */
            s->count--;
            for (j = i; j < s->count; j++)
                entity_copy(s, j + 1, j);
        } else {                                   /* 0x09885 - swap the last in */
            if (s->flag == s->count - 1)
                s->flag = i;
            s->count--;
            entity_copy(s, s->count, i);
        }
        i--;                                       /* look at this slot again */
    }
}

/* --------------------------------------------------- 0x0af95: scene_pick_nearest
 *
 * Which entity of a scene the pointer is over, by nearest in the taxicab sense
 * with the pointer taken six pixels lower than it is, and only within twelve.
 * The answer goes to three globals: the index at [0x18f3] or -1, and a pointer to
 * the entity at [0x18ef].
 *
 * With `mark` set it also puts the highlight on it - scene 3's single entity
 * becomes type 0x11 and moves to the picked entity's position - and [0x0d89] ends
 * up saying whether anything is highlighted at all. That is scene 3's whole job:
 * one entity that follows whatever the pointer is closest to.
 */
void far scene_pick_nearest(scene_t far *s, int16_t mark)
{
    int32_t best = 0xc;                            /* twelve, and no further */
    int16_t found = -1;
    int16_t i;

    for (i = 0; i < s->count; i++) {
        entity_t far *e = &s->entities[i];
        int32_t d = iabs(e->x - (int32_t) mouse_x)
                  + iabs(e->y - (int32_t) mouse_y - 6);

        if (d < best) {                            /* 0x0b009 */
            found = i;
            best  = d;
        }
    }

    picked_index = found;                          /* 0x18f3 */
    if (found != -1) {
        picked = &s->entities[found];              /* 0x18ef */
        if (mark) {                                /* 0x0b055 */
            entity_t far *high = scenes[3].entities;

            entity_set_type(high, 0x11);
            high->x = picked->x;
            high->y = picked->y;
        }
    } else {
        picked = NULL;                             /* 0x0b09c */
    }
    /* 0x0b0be. [0x0d89] is scenes[3].count: the highlight scene holds one
     * entity and its count says whether it is there, so draw_entities skips
     * it by drawing nothing rather than by testing a flag. */
    scenes[3].count = (mark && picked) ? 1 : 0;
}

/* ---------------------------------------------------- 0x0d4fc: level_update
 *
 * One call a frame, and all it does is decide which scene the pointer is picking
 * from, which depends on the tool in hand: the ducks for 0x12, the objects for
 * 0x50, and for 0x15 the mirrored-entity scene, but only while no tool is in
 * progress and only until [0x217d] says it has been done once.
 */
void far level_update(void)
{
    switch (tool_type) {                           /* 0x0d4ff */
    case 0x12:
        scene_pick_nearest(&scenes[0], 1);
        break;
    case 0x50:
        scene_pick_nearest(&scenes[2], 1);
        break;
    case 0x15:
        if (!g_1fd8 && !g_1fda) {
            if (!g_217d)
                scene_pick_nearest(&scenes[5], 1);
            g_217d = 0;
        }
        break;
    default:
        break;
    }
}

/* ==================================================== 0x0bba1: the bonus screen
 *
 * What comes between "level complete" and the next level: five rows of numbers
 * that count themselves up and then pour into each other. game_main calls
 * bonus_screen (0x0becb) straight after showing resource 0x4d:2.
 *
 * The five rows are a table of counters at d+0x2159, one per label - Time bonus,
 * Survivors bonus, Lives bonus, Total, Score - and row 4 doubles as [0x2161],
 * which is where the score is parked while the screen runs and read back out of
 * afterwards. So the last row IS the score, counting up in front of you.
 * ========================================================================= */

int16_t bonus_row[5];            /* 0x2159, and [0] is Time bonus */

/* --------------------------------------------------------- 0x0bba1: one row
 *
 * Counts `dst` from where it is to where it should be, a frame at a time,
 * drawing as it goes. Two shapes, chosen by `moving`:
 *
 *   moving == 0   `src` is a number, and it is added to row `dst`
 *   moving == 1   `src` is another ROW, which empties into `dst` as it fills -
 *                 both are drawn each frame, which is what makes the bonuses
 *                 visibly pour into the total
 *
 * The step is a sixteenth of what is left plus one, so it starts fast and eases
 * in. It only terminates because the amounts are counts and so never negative:
 * a negative one would step by 0 within sixteen of the target and never arrive.
 * The original is the same, and level_timer stops at 0 rather than going below
 * it, so nothing here can reach that - and a keypress escapes it regardless.
 * `gap` is re-armed on every frame that moves - which means it is not
 * really a speed but the pause AFTER the row lands. The ticking is sound 0x12,
 * spaced by how big the step is: a big step ticks every three frames, a small
 * one waits longer, so the sound thins out as the row slows.
 *
 * A key or a button sets the caller's `skipped`, and every later row sees it and
 * finishes instantly - which is how one press skips the whole screen rather than
 * one row of it.
 */
static void tally_row(int16_t moving, int16_t dst, int16_t src,
                      int16_t gap, int16_t far *skipped)
{
    int16_t amount = moving ? bonus_row[src] : src;   /* [bp-2] */
    int16_t cur    = bonus_row[dst];                  /* si */
    int16_t target = cur + amount;                    /* [bp-6] */
    int16_t frames = 1;                               /* [bp-8] */
    int16_t quiet  = 0;                               /* [bp-0xa] */
    int16_t remain = moving ? bonus_row[src] : 0;     /* [bp-0xe] */

    while (!*skipped) {                               /* 0x0bbec */
        int16_t plane;

        if (cur != target) {                          /* 0x0bbf8 */
            int16_t step = ((target - cur) >> 4) + 1;

            frames = gap;
            cur   += step;
            if (moving)
                remain -= step;
            if (quiet) {
                quiet--;
            } else {
                quiet = (step > 0xc) ? 3 : (int16_t) (0xc - step + 3);
                sound_play_guarded(0x12, 1);
            }
        }

        for (plane = 0; plane < 4; plane++) {         /* 0x0bc4b */
            set_plane((uint8_t) plane);
            draw_number(cur, 0x96, (int16_t) (dst * 0x14 + 0x46),
                        &viewport_screen, 0, 6);
            if (moving)
                draw_number(remain, 0x96, (int16_t) (src * 0x14 + 0x46),
                            &viewport_screen, 0, 6);
        }
        page_flip();
        frames--;
        input_poll(0x140, 0xc8);
        if (g_18e5 || last_key) {                     /* 0x0bcbd */
            *skipped = 1;
            frames   = 0;
        }
        if (!frames)                                  /* 0x0bcd8 */
            break;
    }

    bonus_row[dst] += amount;                         /* 0x0bce1 */
    if (moving)
        bonus_row[src] = 0;
}

/* ------------------------------------------------- 0x0bd00: the five numbers
 *
 * Both pages, four planes, five rows, six digits - and each digit is drawn
 * twice: sprite 0x70 to wipe the cell, then 0x71 + the digit over it. Least
 * significant on the right, no leading-zero suppression, the same shape
 * draw_number has.
 */
static void bonus_numbers(void)
{
    int16_t page, plane, row, digit;

    for (page = 0; page < 2; page++) {
        for (plane = 0; plane < 4; plane++) {
            set_plane((uint8_t) plane);
            for (row = 0; row < 5; row++) {
                int16_t v = bonus_row[row];

                for (digit = 5; digit >= 0; digit--) {
                    int16_t x     = (int16_t) (digit * 12 + 0x96);
                    int16_t y     = (int16_t) (row * 0x14 + 0x46);
                    int16_t blank = 0x70;
                    int16_t glyph = (int16_t) (v % 10 + 0x71);

                    draw_sprite(&blank, x, y, &sprite_table,
                                &viewport_screen, 0);
                    draw_sprite(&glyph, x, y, &sprite_table,
                                &viewport_screen, 0);
                    v /= 10;
                }
            }
        }
        page_flip();
    }
}

/* --------------------------------------------------------- 0x0bdee: the tally
 *
 * Seven passes in the order they are watched: the three bonuses count up out of
 * nothing, then each pours into Total, then Total pours into Score.
 *
 *   time bonus      level_timer * 5      what is left on the clock
 *   survivors       duck_count * 10      what got home
 *   lives           lives * 10
 *
 * Then a hundred and fifty frames of holding it, or until something is pressed.
 */
static void bonus_tally(void)
{
    int16_t skipped = 0;
    int16_t hold    = 0x96;

    tally_row(0, 0, (int16_t) (level_timer * 5),  0x0a, &skipped);
    tally_row(0, 1, (int16_t) (duck_count * 10),  0x0a, &skipped);
    tally_row(0, 2, (int16_t) (lives * 10),       0x32, &skipped);
    tally_row(1, 3, 0, 0x0a, &skipped);           /* time      -> total */
    tally_row(1, 3, 1, 0x0a, &skipped);           /* survivors -> total */
    tally_row(1, 3, 2, 0x32, &skipped);           /* lives     -> total */
    tally_row(1, 4, 3, 0x02, &skipped);           /* total     -> score */

    bonus_numbers();
    do {                                          /* 0x0bea2 */
        input_poll(0x140, 0xc8);
        page_flip();
        if (g_18e5 || last_key)
            break;
    } while (hold--);
}

/* -------------------------------------------------- 0x0becb: the whole screen */
void far bonus_screen(void)
{
    char far   *label[5];                          /* 0x2163 */
    desc_t      page;
    int16_t     i, pass, plane;

    label[0] = menu_text[66];                      /* "Time bonus:" */
    label[1] = menu_text[67];                      /* "Survivors bonus:" */
    label[2] = menu_text[68];                      /* "Lives bonus:" */
    label[3] = menu_text[69];                      /* "Total:" */
    label[4] = menu_text[70];                      /* "Score:" */

    if (!resource_load(&page, 0x4d, 1, 0x80, 1, 0xff, 1))
        fatal("Can't find bonus screen", NULL);    /* d+0x246e */

    draw_string(&page, menu_text[52], 0x0a, 0x0a);
    for (i = 0; i < 5; i++) {                      /* 0x0bfbb - right-aligned */
        draw_string(&page, label[i],
                    (int16_t) (0x8c - text_width(label[i])),
                    (int16_t) (i * 0x14 + 0x3e));
        bonus_row[i] = 0;
    }
    bonus_row[4] = score;                          /* 0x0c015 - [0x2161] */

    for (pass = 0; pass < 2; pass++) {             /* 0x0c01b - into both pages */
        for (plane = 0; plane < 4; plane++) {
            set_plane((uint8_t) plane);
            blit_rows(&page, viewport_screen, 0);
        }
        page_flip();
    }
    resource_release(&page);

    bonus_numbers();                               /* 0x0c06f */
    fade_level       = 0;
    fade_direction   = 1;
    fade_start_colour = 0;
    while (fade_direction) {                       /* 0x0c093 - fade in */
        palette_fade_step(0);
        page_flip();
    }

    bonus_tally();                                 /* 0x0c09a */
    score = bonus_row[4];                          /* 0x0c09e */

    fade_direction = -1;
    while (fade_level) {                           /* 0x0c0b8 - fade out */
        palette_fade_step(0);
        page_flip();
    }
}

/* ============================================================ 0x0d7ee: run_level
 *
 * Playing a level. 4,287 bytes, and two thirds of it is the frame - which is why
 * this is written in two halves and only the first is finished.
 *
 * **The setup below is the original's, read out end to end.** Everything it calls
 * exists: the level's own sprite set, the panel, the palette, the tool cursor's
 * two-entity scene, the three message rectangles, the HUD drawn once into each
 * video page, the particle pool, the camera snapped to the mouse, the ambience.
 *
 * **The frame is partly written**, and the parts say so individually. Since this
 * comment was first written 0x07bb2, 0x0d0c8, 0x0d471, 0x09565 and 0x0970c have
 * all landed, so the spawns, the physics, the click handler, the demo's event
 * table and the flock are real. Six routines a demo reaches are not - 0x0a956,
 * 0x0751b, 0x09329, 0x076e2, 0x07646, 0x078a6, 1,350 bytes between them - and
 * the four endings are not wired, so a played level still leaves on ESC while a
 * demo ends on a touch or when its ducks run out.
 * ========================================================================= */
int16_t far run_level(int16_t demo)
{
    static const int16_t layers[5] = { 1, 0, 2, 3, 5 };
    desc_t  panel;
    int16_t hud_x       = video_mode * 0x14 + 0x135;   /* [bp-2], and +5 */
    int16_t shown_score = score;                       /* [bp-6] */
    int16_t shown_ducks = duck_count;                  /* [bp-0xe] */
    int16_t sparkle_x;                                 /* [bp-0x16] */
    int16_t page, plane, i, edge;
    int16_t score_redraw = 2, ducks_redraw = 2;    /* [bp-8], [bp-0xa] */
    uint8_t half = 0;                              /* [bp-0x20] */
    /* [bp-0x12]. An out-parameter of the played tool handler at 0x0cf07, which
     * is not written - so in a demo it stays 0, the half-rate mode never fires,
     * and the tool announcement is always three frames. */
    int16_t tool_slot = 0;
    int16_t tick;                                  /* [bp-0xc], 0x0d820 */
    int16_t ending_said = 0;                       /* [bp-0x22], 0x0d846 */
    int16_t running = 1;                           /* [bp-0x10], 0x0d829 */
    int16_t hold = 0;                              /* [bp-0x28] */
    int16_t over = 0;                              /* [bp-0x13] */
    /* The flock's average, and what the camera followed last. Both are the
     * frame's locals in the original and both outlive one pass of the loop:
     * [bp-0x24]/[bp-0x26] keep their value when no branch picks a target, which
     * is how the view stays put rather than snapping to the origin. */
    int32_t avg_x = 0, avg_y = 0;                  /* [bp-0x18]..[bp-0x1e] */
    int16_t follow_x = 0, follow_y = 0;            /* [bp-0x24], [bp-0x26] */


    sparkle_x     = game_rand() & 0x3ff;                /* 0x0d837 */
    blink_enable  = level_flags[0];                /* 0x0d850 - [0x201e] */
    button_a_down = 0;

    sprite_set_load(sprite_set_id, 0x43, &level_sprites, episode_egg_index);
    too_deep_count = 0;
    g_1ffe = g_1ffc;                               /* 0x0d876 */
    g_1ffc = 0;
    game_srand((unsigned) level_seed);                  /* 0x0d886 - the level's own */
    clear_vram();
    resource_load(&panel, 0x4d, 0x21, 0, 1, 0xff, 1);
    level_palette_build();                         /* 0x0d5c5 */

    /* 0x0d8af. The tool cursor: two entities in a scene of its own, the second
     * carrying the selected tool's type, and its param says which is which. */
    tool_at   = 0;
    tool_type = tool_list[0];
    scene_alloc(&tool_scene, 2);
    scene_add(&tool_scene, 0x82, 0x10, 0x10, 5);
    scene_add(&tool_scene, 0x82, 0x10, tool_type, 5);
    tool_scene.entities[1].param = 1;              /* 0x0d902 */

    /* 0x0d908. Three message slots stacked ten rows apart, the lowest 34 rows up
     * from the bottom of the play area. Zero width until message_post measures a
     * line into one. */
    edge = viewport_game.bottom - 0x22;
    for (i = 0; i < 3; i++) {
        make_rect(&message_rect[i], edge + i * 10, edge + i * 10 + 0xf,
                  viewport_game.left + 4, viewport_game.left + 4);
        message_time[i] = 0;
    }
    if (tool_count)                                /* 0x0d95b - "you have got X" */
        message_post(menu_text[7], tool_names[anim_c[tool_type]]);

    eaten_countdown = 0;

    /* 0x0d9a2. The HUD, once into each of the two pages with a flip between, and
     * never again - so its two comparisons a level are the whole population. */
    for (page = 0; page < 2; page++) {
        for (plane = 0; plane < 4; plane++) {
            static int16_t slot = 0x2a, s_score = 7, s_ducks = 0x4f, s_lives = 0xae;

            set_plane((uint8_t) plane);
            blit_rows(&panel, viewport_panel, 0);
            for (i = 0; i < tool_count; i++) {
                int16_t x   = 0x82 + (i << 4);
                int16_t idx = anim_script[tool_list[i]][0];

                draw_sprite(&slot, x, 0x10, &sprite_table, &viewport_panel, 0x90);
                outline_sprite(&idx, x, 0x10, &sprite_table, &viewport_panel);
                draw_sprite(&idx, x, 0x10, &sprite_table, &viewport_panel, 0x90);
            }
            draw_sprite(&s_score, 0x0d4, 0x23, &sprite_table, &viewport_panel, 0x90);
            draw_sprite(&s_ducks, 0x105, 0x23, &sprite_table, &viewport_panel, 0x90);
            draw_number2(score,      6, 0x080, 0x22);
            draw_number2(duck_count, 2, 0x0e1, 0x22);
            draw_number2(lives,      2, 0x113, 0x22);
            draw_sprite(&s_lives, 0x135, 0x07, &sprite_table, &viewport_panel, 0x90);
        }
        page_flip();
    }
    resource_release(&panel);                      /* 0x0db42 */

    bg_scroll_reset();
    warp_step       = level_flags[6] ? 7 : 1;      /* 0x0db4c - [0x202a] */
    g_2016          = 0;
    blink_toggle    = 0;
    blink_countdown = 0x28;
    level_clock     = 0;
    combo_hi        = 0xa00;
    combo_lo        = 0;
    level_timer     = 0x1b;
    tick            = timer_period;                /* 0x0d81d */

    /* 0x0db86. Two flags the endings test, and they differ only in what they say
     * about [0xda1]: whether the level can be finished at all without the tool
     * that would be needed. */
    can_finish     = (!tool_list_any_flagged() && !tool_list_has(0x12)
                      && !level_flags[1] && !scenes[5].count);
    can_finish_alt = (scenes[5].count && !tool_list_any_flagged() && !tool_list_has(0x12)
                      && !level_flags[1]);
    if (!tool_list_has(0x12) && scenes[0].flag != 0xff)
        quota_left++;                              /* 0x0dbf5 */

    /* 0x0dbf9. The particle pool, sized from what scene 0 was allocated plus two
     * per mirrored entity, forty records each. */
    particle_cap   = (scenes[0].capacity + pair_slots) * 0x28;
    particle_array = malloc((size_t) particle_cap * sizeof *particle_array);
    if (!particle_array)
        particle_cap = 0;
    particle_count = 0;
    g_1fda = g_1fd8 = level_outcome = g_2018 = 0;

    fade_level     = 0;
    fade_direction = 1;
    cursor_to_centre(&backdrop);                   /* 0x0dc5b */
    scroll_axis_snap(mouse_x, screen_width, &viewport_game.scroll_x,
                     (int16_t) (level_w - screen_width));
    scroll_axis_snap(mouse_y, viewport_game.bottom, &viewport_game.scroll_y,
                     (int16_t) (level_h - viewport_game.bottom));
    if (ambience_volume && ambience_on)            /* 0x0dca3 */
        sound_play_loop(ambience_on, ambience_volume, episode_egg_index);

    /* ------------------------------------------------------------ the frame
     *
     * Not the original's. 0x0dcc9 is the real one and it is 2,830 bytes; this
     * composes what the setup built and waits for ESC, so that everything above
     * can be seen. See docs/notes/run-level.md for the map of what belongs here.
     */
    do {
        /* 0x0dcc9. The frame opens with a draw and a tick, and the tick is the
         * one the demo runs on: demo_events and tool_events both fire a record
         * when its frame number EQUALS level_clock, so a clock that never moves
         * is a demo where nothing is ever clicked. It was set to 0 in the setup
         * and incremented nowhere, which is why the attract mode showed a level
         * with gravity and monsters in it and no player. */
        if ((game_rand() & 0x7f) == 0)             /* 0x0dcce */
            ambience_random();                     /* 0x0dcd3 */
        level_clock++;                             /* 0x0dcd8, [0x201a] */

        /* 0x0ddbc. The demo's OTHER table, and the one that walks the hero.
         * Three bytes a record - a frame and a heading - with script_at as the
         * cursor: when the record under it names this frame the cursor moves
         * on, and the record it lands on gives the heading. entity_update's
         * type 1 arm reads that out of [0x2100] as the hero's f14, but only
         * when `scripted` is set, so this is demo input and nothing else.
         *
         * event_table is the clicks - the tool - and this is the walking. Only
         * the first was written, which is why the attract mode placed items and
         * never moved. The heading byte is 4, 5 or 6 in every demo in the egg,
         * stored plus five, so -1, 0 or +1.
         *
         * The second lookup is not bounds-checked in the original - 0x0ddc3
         * jumps straight to it - so once the cursor reaches the end it reads one
         * record past a farmalloc'd block. Measured across six snapshots the
         * byte it lands on is 02, 00, 00, 00, 03, 00: the next allocation's
         * contents, not a header, so unlike terrain_at there is nothing to
         * match. menu-halloffame-2.snap has script_at == script_count, so the
         * original does get there. Holding the last record is the choice, and
         * every demo in the egg ends on heading 0, so the hero stops. */
        if (script_count && script_table) {
            uint16_t at = (uint16_t) script_at;

            if (at < script_count                  /* 0x0ddbf, unsigned */
                && *(uint16_t far *) (script_table + at * 3)
                   == (uint16_t) level_clock)      /* 0x0ddd7 */
                at = (uint16_t) ++script_at;       /* 0x0dddd */
            if (at >= script_count)
                at = script_count - 1;             /* see above - not the
                                                    * original's, which reads on */
            g_2100 = (int8_t) (script_table[at * 3 + 2] - 5);   /* 0x0ddf4 */
        }

        /* 0x0ddfe. The clock. `tick` is the frames left in the current second
         * and timer_period is how many that is, so level_timer comes down one
         * per period and the warning at 0x1c plays once as it lands on zero.
         *
         * The else is 0x0de2e, and it is what a secret level's clock is for:
         * [0x1ffe] is the secret-level flag moved off [0x1ffc] at 0x0d876, so
         * on an ordinary level the clock simply stops and is worth five points
         * a tick on the bonus screen, and on a secret level running out of time
         * blows the flock up. */
        if (level_timer) {
            if (tick)
                tick--;                            /* 0x0de0b */
            else {
                tick = timer_period;               /* 0x0de10 */
                if (--level_timer == 0)            /* 0x0de16 */
                    sound_play_guarded(0x1c, 1);
            }
        } else if (!g_2016 && g_1ffe) {            /* 0x0de2e */
            kill_all_ducks();                      /* 0x0de3d */
        }

        input_poll(level_w, level_h);
        tool_step();                               /* 0x0de4f */

        /* 0x0de53. Everything from here to the outcome check is behind
         * `running`. Once an outcome is settled the level stops reacting - no
         * more spawns, no more input, no more tool - and only the fade and the
         * drawing are left to play out. The original expresses it as a jump
         * straight to 0x0df27. */
        if (running) {
            /* 0x0de5c. Once the ducks are gone - or [0x2016] says the level is
             * over - a counter runs for 0x20 frames and then sets the outcome to
             * 3, which is what fades out a level that has run out of ducks. */
            if (duck_count == 0 || g_2016)
                over++;
            if (over >= 0x20)
                level_outcome = 3;

            /* 0x0de79. The tool. The selection is remembered first, because
             * tool_selected compares against it, and then the demo's table moves
             * it - a played level reads 0x0cf07 instead, which is where ESC
             * goes: it does not fade the level out, it blows the flock up and
             * lets [0x2016] and the 0x20-frame count above end it. */
            tool_prev = tool_at;                   /* 0x0de7c */

            /* 0x0de7f. A demo ends the moment anything is touched - that is
             * attract mode's whole exit. */
            if (demo) {
                if (g_18e5 || last_key)
                    fade_direction = -1;
                tool_events();                     /* 0x0d4c2 */
            } else {
                played_tool_events(&tool_slot);    /* 0x0dea4 */
            }

            /* The events only fire while no tool is in progress, which is the
             * guard at 0x0deaa, and the cursor entity's type follows the tool:
             * 0x2a plus which side of the cursor the flock is on for a mirrored
             * tool, 0x14 otherwise, and 0x16 while one is being used. */
            if (!g_1fd8 && !g_1fda) {              /* 0x0deaa */
                entity_set_type(cursor_scene.entities,
                                (type_flags[tool_type] & 2) ? g_dab + 0x2a : 0x14);
                if (demo)
                    demo_events();                 /* 0x0def3 */
                else if (g_18e1)
                    level_event((int16_t) mouse_x, (int16_t) mouse_y);
            } else {
                entity_set_type(cursor_scene.entities, 0x16);
                tool_at = tool_prev;               /* 0x0df24 - put it back */
            }
        }

        /* 0x0df27. What an outcome does, and the only place a level ends of its
         * own accord. Outcome 1 is a win - the rocket's last duck, or a 0x4e -
         * and it alone moves the outcome on to the 2 that run_level returns;
         * outcome 3, having run out of ducks, leaves it at 3 and so returns 0.
         * Both stop the level reacting and start the fade.
         *
         * Without this a rocket could launch, set outcome 1, and nothing would
         * ever look at it - which is exactly what it did. */
        if (level_outcome == 1) {                  /* 0x0df27 */
            running        = 0;
            fade_direction = -1;
            can_finish     = 0;
            level_outcome  = 2;
        } else if (level_outcome == 3) {           /* 0x0df46 */
            can_finish     = 0;
            running        = 0;
            fade_direction = -1;
        }

        /* 0x0df5d. The four ways an attempt becomes unwinnable. Each posts one
         * line plus "Press ESCAPE to abort this attempt", and `ending_said` - a
         * frame local cleared once in the setup - makes that at most one message
         * per level however long it runs.
         *
         * They only say so. Nothing here clears level_running or touches the
         * outcome, which is why the panel keeps counting and the player is the
         * one who decides to give up.
         *
         * Skipped entirely for a demo (0x0df70), and while g_2016 or g_1ffe say
         * the level is already over. */
        if (!ending_said && !g_2016 && !demo && !g_1ffe) {
            if (scenes[0].flag == 0xff && can_finish) {         /* 0x0df83 */
                message_post(menu_text[76], menu_text[80]);
                ending_said = 1;
            }
            /* [0xda1] is scenes[5].count, and can_finish_alt was only set when
             * that count was non-zero at setup - so this is "the level had
             * mirrored entities and now has none". */
            if (scenes[0].flag == 0xff && !scenes[5].count && can_finish_alt) {
                message_post(menu_text[77], menu_text[80]);     /* 0x0dfc6 */
                ending_said = 1;
            }
            /* An unsigned compare in the original, and it is the one the panel
             * makes visible: quota_left is how many still have to get home, and
             * duck_count how many are left to do it with. */
            if ((uint16_t) quota_left > (uint16_t) duck_count) {  /* 0x0e010 */
                message_post(menu_text[78], menu_text[80]);
                ending_said = 1;
            }
            if (g_2018) {                                       /* 0x0e04d */
                message_post(menu_text[79], menu_text[80]);
                ending_said = 1;
            }
        }

        /* 0x0e088. And now the selection becomes the tool. */
        tool_selected(tool_slot);                  /* 0x0e0e0 takes [bp-0x12] */

        /* 0x0e11b. Where everything was when the frame began, on five of the six
         * scenes - scene 4 is the cursor's and does not move by itself - and
         * then the flock is chained up again from nothing. */
        scene_keep_positions(&scenes[0]);
        scene_keep_positions(&scenes[2]);
        scene_keep_positions(&scenes[3]);
        scene_keep_positions(&scenes[5]);
        scene_keep_positions(&scenes[1]);
        flock_link();                              /* 0x0e152 */

        /* 0x0e156. Scene 0 - the ducks - walks and falls, and the same loop
         * averages where the flock is. This is the one update pass that does not
         * go through scene_update_all, because of that average and because its
         * third argument is the demo flag: in a demo the hero's facing comes
         * from the script table at [0x2100], and in a played level from the
         * mouse and the walk button.
         *
         * The average leaves out the hero, unless it is the only duck there,
         * and [0xdab] - which chose the cursor's sprite above - is whether it
         * comes out right of the cursor. Both halves of it are 32-bit sums
         * divided by the count through the runtime's __ldiv. */
        {
            int32_t sum_x = 0, sum_y = 0;
            int16_t n = 0;

            for (i = 0; i < scenes[0].count; i++) {
                entity_t far *e = &scenes[0].entities[i];

                entity_update(e, 0, demo);         /* 0x0e192 */
                if (i == scenes[0].flag && scenes[0].count != 1)
                    continue;                      /* 0x0e198 */
                sum_x += e->x;
                sum_y += e->y;
                n++;
            }
            if (n) {                               /* 0x0e1e2 - and 0/0 is not asked */
                avg_x = sum_x / n;
                avg_y = sum_y / n;
            } else {
                avg_x = sum_x;
                avg_y = sum_y;
            }
            g_dab = avg_x > mouse_x;               /* 0x0e21c */
        }

        /* 0x0e2c9. The other three scenes, and then the entity a tool is being
         * applied through - which is the one call anywhere that passes
         * entity_update's `applying` as 1. */
        scene_update_all(&scenes[2]);
        scene_update_all(&scenes[1]);
        scene_update_all(&scenes[5]);
        if (g_1fda)                                /* 0x0e2ea */
            entity_update(scenes[3].entities, 1, 0);

        /* 0x0e304. The retire pass, over all six scenes - the only place an
         * entity array shrinks, so this is what makes a duck that has been
         * eaten actually leave. */
        for (i = 0; i < 6; i++)
            scene_retire(&scenes[i]);

        /* 0x0e346. Which entity the pointer is over, per the tool in hand, and
         * then every duck against every object. */
        level_update();
        collide_scenes();                          /* 0x0e34b */

        /* 0x0e34e. The cursor entity takes the mouse, and then the camera
         * follows it - which is what makes a level wider than the screen
         * scroll. The demo path instead follows the entity at scenes[3] when
         * [0x1fda] says so, or the hero duck while it is facing somewhere, each
         * with a twenty-frame hold, and falls back to the flock's average once
         * that hold runs out. */
        cursor_scene.entities[0].x = mouse_x;
        cursor_scene.entities[0].y = mouse_y;
        if (!demo) {
            scroll_follow(mouse_x, mouse_y);       /* 0x0e427 */
        } else {
            /* Word stores in the original: the low half of each position, and
             * then sign-extended back to a long for scroll_follow. */
            if (g_1fda) {
                hold = 0x14;
                follow_x = (int16_t) scenes[3].entities[0].x;
                follow_y = (int16_t) scenes[3].entities[0].y;
            } else if (scenes[0].flag != 0xff
                       && scenes[0].entities[scenes[0].flag].f14 != 0) {
                hold = 0x14;
                follow_x = (int16_t) scenes[0].entities[scenes[0].flag].x;
                follow_y = (int16_t) scenes[0].entities[scenes[0].flag].y;
            }
            if (!hold) {                           /* 0x0e3ea - the hold expired */
                follow_x = (int16_t) avg_x;
                follow_y = (int16_t) avg_y;
            } else {
                hold--;
            }
            scroll_follow(follow_x, follow_y);     /* 0x0e40e */
        }

        animate_scene(&cursor_scene);
        for (i = 0; i < 6; i++)
            animate_scene(&scenes[i]);
        animate_scene(&tool_scene);
        particles_step();                          /* 0x0e482 */

        /* 0x0e485. The two panel numbers, and they are not kept the same way.
         * The score CHASES its target - a quarter of the gap plus one a frame -
         * so it rolls up rather than jumping, and what is drawn is that rolling
         * copy. The duck count does not: shown_ducks exists only to notice a
         * change, and what is drawn is the live counter.
         *
         * Their redraw flags differ too, and the asymmetry is the original's.
         * Both start at 2 in the setup; only the score's is decremented (at
         * 0x0e717), so it stops redrawing two frames after the score settles.
         * Nothing anywhere decrements the ducks' flag, so from the first frame
         * of the level that number is redrawn every frame for ever. */
        if (shown_score != score) {                /* 0x0e485 */
            score_redraw = 2;
            shown_score = (int16_t) (shown_score + ((score - shown_score) >> 2) + 1);
        }
        if (shown_ducks != duck_count) {           /* 0x0e4a0 */
            ducks_redraw = 2;
            shown_ducks  = duck_count;
        }

        /* 0x0e4b4. Every other frame draws nothing at all when tool_slot is
         * set - the half-rate mode. tool_slot is an out-parameter of the played
         * tool handler, so in a demo it stays 0 and no frame is ever skipped. */
        half ^= 1;
        if (half || !tool_slot) {
            for (i = 0; i < 3; i++)                /* 0x0e4c7 */
                if (message_time[i])
                    message_time[i]--;

            for (plane = 0; plane < 4; plane++) {
                set_plane((uint8_t) plane);
                compose_scroll((int16_t) viewport_game.scroll_x,
                               (int16_t) viewport_game.scroll_y);
                if (settings[1])                   /* 0x0e4fd - [0x4f6] */
                    particles();
                for (i = 0; i < 5; i++)
                    draw_entities(&scenes[layers[i]], viewport_game, 0);
                if (!demo)
                    draw_entities(&cursor_scene, viewport_game, 0x90);
                if (tool_count)
                    draw_entities(&tool_scene, viewport_panel, 0x90);

                /* 0x0e5cd. The three message slots, while their timers last. */
                for (i = 0; i < 3; i++)
                    if (message_time[i])
                        blit_rows_masked(message_image[i], message_rect[i], 0);

                if (score_redraw)                  /* 0x0e608 */
                    draw_number(shown_score, 0x080, 0x22, &viewport_panel, 0x90, 6);
                if (ducks_redraw)                  /* 0x0e626 */
                    draw_number(duck_count, 0x0e1, 0x22, &viewport_panel, 0x90, 2);

                /* 0x0e645. The timer bar: a run of pixels from the HUD's left
                 * edge, as long as the time left is short. */
                edge = (int16_t) (screen_height + 0xfff9 - level_timer);
                for (i = hud_x; i < hud_x + 5; i++)
                    plot(i, edge, 0);
            }

            /* 0x0e673. One sparkle pixel down a column, which then walks by one
             * either way - or is thrown somewhere new every 32nd frame. */
            /* level_flags[3], and the sparkle's column starts at screen row 0
             * rather than at the top of the play area: the address is the page
             * base plus (x >> 2) with no row term, and only the COUNT comes from
             * the viewport. Reproduced rather than tidied. */
            if (level_flags[3]) {                   /* 0x0e673 - [0x2024] */
                if (sparkle_x >= viewport_game.left
                        && sparkle_x < viewport_game.right) {
                    set_plane((uint8_t) (sparkle_x & 3));
                    for (i = 0; i < viewport_game.bottom - viewport_game.top; i++)
                        plot(sparkle_x, i, 0x6f);
                }
                /* Sequenced deliberately. C does not order the operands of a
                 * subtraction, and these two draws are observable: they move the
                 * shared seed, so the wrong order changes every later rand() in
                 * the frame. The guest pushes the first and subtracts the
                 * second. */
                if (game_rand() & 0x1f) {
                    int16_t lo = (int16_t) (game_rand() & 1);
                    int16_t hi = (int16_t) (game_rand() & 1);

                    sparkle_x = (int16_t) (sparkle_x + (lo - hi));
                } else {
                    sparkle_x = game_rand() & 0x3ff;
                }
            }

            if (score_redraw)                      /* 0x0e711 */
                score_redraw--;
            page_flip();
        }

        /* 0x0e71e. The tool announcement landing, and the only thing that moves
         * the highlight. The countdown reaching 1 - not 0 - says the tool has
         * arrived: it names the tool in a message and parks the tool scene's two
         * entities over the selected slot, at tool_at * 16 + 0x82, which is the
         * same spacing the HUD drew the icons at.
         *
         * The two are the highlight and the icon: entity 0 becomes type 0x10, the
         * box, and entity 1 the tool's own type. tool_selected has had entity 1
         * as 0x0f for the length of the countdown, which is what makes the new
         * tool flash before it settles.
         *
         * This sits OUTSIDE the frame skip: 0x0e4c4 jumps here, so a skipped
         * frame still moves the highlight.
         *
         * Without it both entities stay where the setup put them - over slot 0,
         * drawn every frame - so the first tool is covered and the highlight
         * never follows the selection. */
        if (tool_announce == 1) {                  /* 0x0e71e */
            int16_t x = (int16_t) (tool_at * 16 + 0x82);

            message_post(menu_text[7], tool_names[anim_c[tool_type]]);
            entity_set_type(&tool_scene.entities[0], 0x10);
            entity_set_type(&tool_scene.entities[1], tool_type);
            tool_scene.entities[0].x = x;
            tool_scene.entities[1].x = x;
        }

        palette_fade_step(0);
    } while (fade_level != 0);

    /* 0x0e7da's teardown. level_free was a stub and out of this list entirely,
     * so every attempt at a level leaked the whole of it. */
    blink_enable = 0;                              /* 0x0e7da, [0x2157] */
    sprite_set_free(&level_sprites);               /* 0x0e7e5 */
    level_free();                                  /* 0x0e7ec */
    free(particle_array);                          /* 0x0e7f7 */
    particle_array = NULL;
    free(tool_scene.entities);                     /* 0x0e807 */
    set_buffer(default_buffer);                    /* 0x0e814 */

    /* 0x0e81a. The other half of level_palette_build's `+= 0x90`: together they
     * are 0x100, so text_colour comes back to what it was. A level's text draws
     * through the copy of entries 0x50-0x6f that the palette grade puts at
     * 0xe0-0xff, and the screens after a level do not - the bonus screen sets no
     * text colour at all, it inherits this one. Without this line its labels are
     * drawn 0x90 up the palette and are not there. */
    text_colour[0] += 0x70;

    return level_outcome == 2;
}

/* --------------------------------------------------- 0x1102a: level_screens
 *
 * Everything between choosing a level and playing it, and it is two screens with
 * the level loaded between them:
 *
 *   the level's name bounces in as a banner       0x110a4
 *   the level loads                               0x1108b
 *   a picture, with the level's map drawn into it 0x110d7, 0x11131
 *   the name over it, the tools, the status line  0x11190, 0x1123d
 *   that fades in, waits for a key, fades out     0x11280
 *   then the instructions page, if the level has one, with the same name banner
 *   over a fresh picture and one animated entity  0x1131f
 *
 * Returns non-zero only on the level-select key in the [0x507] branch, and
 * game_main's `while` at 0x13835 sends that straight back in here - the screens
 * have to be built again for whatever level was just picked. It is a retry, not
 * a way out of the play loop.
 *
 * The tools that belong on the first screen at y=0xb4 need 0x7259, which is not
 * written; the branch that draws the level's name over the picture when [0x507]
 * is set is here, because it is two calls.
 */
int16_t far level_screens(int16_t fresh)
{
    desc_t  name, pic;
    scene_t scene;
    int16_t leave = 0, has_page, i, ordinal;

    text_colour[1] = 0;                            /* 0x11041 */
    text_colour[0] = 0x6f;

    /* 0x1104b. Three ways past here and only the middle one is ordinary play:
     * cheat_state[1] set takes the cheat's level picker at 0x10c06 instead;
     * otherwise the episode intro runs, but only when `fresh` is
     * non-zero. That argument is game_main's [0x21a3] - 1 for a new game or
     * after a level was completed, 0 after an attempt was abandoned - so
     * retrying a level plays its own intro and not the episode's. It was called
     * unconditionally here, which replayed the episode intro on every retry. */
    if (cheat_state[1])                            /* 0x1104b - THECROWDSAYBO */
        level_picker();                            /* 0x10c06 */
    else if (fresh)                                /* 0x11058 */
        episode_intro();                           /* 0x1105f -> 0x1089b */

    i       = episode_for_level();                 /* 0x11063 */
    ordinal = (i == 0xff) ? 0xff : episode_index[i].ordinal;

    level_load();                                  /* 0x1108b */
    clear_vram();

    /* The name, bouncing in over whatever is on screen, before the picture it
     * will be stamped onto even exists. */
    banner_build(&name, level_text, 0, 0);         /* 0x110a4 */
    fade_start_colour = 0x10;
    free(level_text);                              /* 0x110b8 */

    if (!resource_load(&pic, 0x4a, (uint8_t) ordinal, 0x80, 1,
                       episode_egg_index, 0)
        && !resource_load(&pic, 0x4d, 1, 0x80, 1, 0xff, 0)) {
        dac_set_black(0, 0);                       /* 0x11109 - no picture */
        clear_vram();
        resource_release(&name);
        fade_start_colour = 0;
        return leave;
    }

    level_map_draw(&pic);                          /* 0x11131 */
    if (cheat_state[1]) {                                   /* 0x1113e */
        const char far *s = menu_text[65];

        draw_string(&pic, s, 0xa0 - text_width(s) / 2, 0x5f);
    }
    image_overlay(&name, &pic, 0);                 /* 0x11190 */

    /* 0x11196. The tools this level gives you, in a row centred on the middle of
     * the screen: sixteen pixels apart, each haloed and then drawn. The sprite
     * is the first frame of the tool type's own script, which is how the HUD
     * picks its icons too. */
    {
        int16_t at = 0xa0 - ((tool_count - 1) << 3);

        for (i = 0; i < tool_count; i++) {
            sprite_t far *icon =
                &sprite_table.base[anim_script[tool_list[i]][0]];

            outline_to_image(at + (i << 4), 0xb4, icon, &pic);      /* 0x111e5 */
            sprite_to_image_plain(at + (i << 4), 0xb4, icon, &pic); /* 0x11224 */
        }
    }

    draw_level_status(&pic);                       /* 0x1123d */

    /* Whether there is an instructions page at all is settled here, before the
     * first screen is shown, by looking for the block and closing it again. */
    has_page = egg_find_block(0x44, (uint8_t) level_attempted,
                              episode_egg_index) != 0;
    if (has_page)
        egg_block_end();

    fade_direction = 1;                            /* 0x1126a */
    fade_level     = 0;
    dac_set_black(0x10, 0);
    do {                                           /* 0x11280 */
        int16_t plane;

        input_poll(0x140, 0xc8);
        if (fade_direction == 0) {
            if (cheat_state[1] && last_key == 0x20) {       /* 0x1129b - level select */
                leave    = 1;
                has_page = 0;
                fresh    = 0;                      /* 0x112a9, and dead: the
                                                    * intro decision is already
                                                    * made. The original's. */
            }
            if (last_key || g_18e5) {
                fade_direction = -1;
                if (!has_page)
                    fade_start_colour = 0;
            }
        }
        for (plane = 0; plane < 4; plane++) {
            set_plane((uint8_t) plane);
            blit_rows(&pic, viewport_screen, 0);
        }
        page_flip();
        palette_fade_step(0);
    } while (fade_level != 0);

    if (has_page) {                                /* 0x1131f - the second screen */
        scene_alloc(&scene, 1);
        scene_add(&scene, 0xa0, 0x3c, 0x34, 5);
        resource_release(&pic);
        if (!resource_load(&pic, 0x4d, 1, 0x80, 1, 0xff, 1))
            fatal("Can't load level information screen", 0);   /* d+0x24e4 */

        image_overlay(&name, &pic, 0);             /* 0x11388 - the same banner */
        load_text_page(&pic, 0x44, (uint8_t) level_attempted,
                       text_colour[0], 0x136, episode_egg_index);

        fade_direction = 1;                        /* 0x113ab */
        fade_level     = 0;
        do {                                       /* 0x113b6 */
            int16_t plane;

            input_poll(0x140, 0xc8);
            if ((last_key || g_18e5) && fade_direction == 0) {
                fade_direction    = -1;
                fade_start_colour = 0;
            }
            animate_scene(&scene);                 /* 0x113e9 */
            for (plane = 0; plane < 4; plane++) {
                set_plane((uint8_t) plane);
                blit_rows(&pic, viewport_screen, 0);
                draw_entities(&scene, viewport_screen, 0);
            }
            page_flip();
            palette_fade_step(0);
        } while (fade_level != 0);

        free(scene.entities);                      /* 0x1145d */
    }

    resource_release(&name);                       /* 0x1146b */
    resource_release(&pic);

    /* 0x1147d. The level-select key is the one way out of here, and the level
     * loaded for the map screen will not be played, so it goes back. */
    if (leave)
        level_free();                              /* 0x11484 */

    /* 0x11488. A fresh seed for the level, which run_level srand()s - so this is
     * what makes two goes at the same level differ. The original reads it out of
     * the runtime's clock at 0:0x17df. The play log below it is gated on [0x513],
     * which nothing has been seen to set, and is not written. */
    level_seed = (int16_t) time(NULL);
    play_log   = 0;

    return leave;
}

/* -------------------------------------------------- 0x1089b: episode_intro
 *
 * The screen before the first level of an episode: a picture of its own with
 * the episode number bouncing in at the top and the episode's name at the
 * bottom. It runs only when the level about to be played is an episode's first
 * and the egg matches, which is the whole of its gate.
 *
 * It keeps its palette in a buffer on its own stack - 768 bytes of local - so
 * the colours it loads at 0x20 do not disturb whatever the menus left in
 * default_buffer. The picture is a 0x57 resource, one per episode ordinal, and
 * if the egg has not got one it says the same two things with plain splashes
 * instead.
 *
 * Verified against snapshots/snap005.snap: the picture with both banners over it
 * is the captured screen, 64000 of 64000 pixels.
 */
void far episode_intro(void)
{
    uint8_t buffer[768];                           /* [bp-0x364] */
    char    title[0x4c];                           /* [bp-0x64] */
    desc_t  pic, top, bottom;
    int16_t i;

    set_buffer(buffer);                            /* 0x108aa */

    for (i = 0; i < episode_count; i++) {
        if (episode_index[i].first != level_attempted    /* 0x108c6 */
            || episode_index[i].egg != episode_egg_index)
            continue;

        sprintf(title, "%s %i", menu_text[54],     /* d+0x24ca */
                episode_index[i].ordinal + 1);
        sound_play_guarded(0x21, 1);               /* 0x10926 */

        if (!resource_load(&pic, 0x57, (uint8_t) episode_index[i].ordinal,
                           0x20, 1, episode_egg_index, 1)) {
            show_splash(title, 0x64);              /* 0x10962 - no picture */
            show_splash(episode_index[i].name, 0x64);
            continue;
        }

        clear_vram();
        banner_build(&top, title, 0, 0);           /* 0x1099b */
        banner_build(&bottom, episode_index[i].name, 0x10, 0x9b);
        fade_start_colour = 0x20;
        image_overlay(&top, &pic, 0);              /* 0x109d9 */
        image_overlay(&bottom, &pic, 0x9b);

        dac_set_black(0x30, 0);                    /* 0x109f8 */
        fade_level     = 0;
        fade_direction = 1;
        do {                                       /* 0x10a09 */
            int16_t plane;

            input_poll(0x140, 0xc8);
            if (fade_direction == 0 && (last_key || g_18e5)) {
                fade_direction    = -1;
                fade_start_colour = 0;
            }
            for (plane = 0; plane < 4; plane++) {
                set_plane((uint8_t) plane);
                blit_rows(&pic, viewport_screen, 0);
            }
            page_flip();
            palette_fade_step(0);
        } while (fade_level != 0);

        resource_release(&pic);                    /* 0x10a86 */
        resource_release(&top);
        resource_release(&bottom);
    }

    /* 0x10ab3, and the whole reason this is safe: the buffer above is a local,
     * so the shared one has to be published again before the frame goes away.
     * Without this every later palette write - the level's tiles, its
     * background, its solids, the status panel - lands in dead stack. */
    set_buffer(default_buffer);
}

/* -------------------------------------------------- 0x103e2: banner_build
 *
 * The wavy title on the episode and level screens, and it is not a picture -
 * it is an animation that runs to a standstill and leaves its last frame behind.
 *
 * Each character gets eight bytes of state: where it is, how fast it is moving,
 * what amplitude it is bouncing at, and which side of y=0x1e it was on last
 * time. They start off-screen - above, at -8 per character, or below at
 * 0x5a + 8 per character when a colour is asked for - and fall in. Every time a
 * character crosses the line the amplitude flips sign and loses one, so the
 * bounce dies away; between crossings the speed walks toward the amplitude one
 * step a frame. A character with both at zero has settled, and when every
 * character has settled for four frames in a row the routine returns.
 *
 * So the letters drop in, bounce, and come to rest one after another, because
 * each starts eight further out than the last. That is the animation.
 *
 * It draws into `dest`, a 320x45 image of its own, and blits that to the screen
 * itself every frame through the clip rectangle it built - which is why the
 * caller gets a finished image back and nothing else has to animate anything.
 *
 * `colour` doubles as the palette: non-zero and it first derives a second ramp
 * at entries 0x10-0x1f from the first sixteen, with blue scaled by 0.8 and the
 * other two by 1.4 - the two constants at d+0x24ba and d+0x24c2 are 0.6 and 0.8.
 * That is why the two banners on the episode screen are different colours.
 */
void far banner_build(desc_t far *dest, const char far *text, uint8_t colour,
                      int16_t top)
{
    struct { int16_t y, speed, amp, above; } far *state;
    viewport_t rect;
    table_t    set;
    int16_t    i, n, width = 0, x, settled, still = 0;

    if (top && video_mode)                         /* 0x103fa */
        top += 0x28;
    make_rect(&rect, top, top + 0x2d, screen_x0, screen_x0 + 0x140);
    image_alloc(dest, 0x140, 0x2d);                /* 0x10432 */
    sprite_set_load(1, 0x53, &set, 0xff);          /* the large font */

    if (colour) {                                  /* 0x1044b */
        uint8_t far *buf = current_buffer;

        for (i = 0; i < 0x30; i++)
            buf[0x30 + i] = (uint8_t) (buf[i] * ((i % 3 == 2) * 0.6 + 0.8));
    }
    /* 0x104bc, and it runs either way - the `je` above lands on this call, not
     * past it. The large font's own first colour is a purple, and entry 0 is
     * what image_clear fills the banner with, so without this the purple becomes
     * the background the letters sit on and floods the screen. The root README
     * records show_splash needing exactly the same thing. */
    palette_set_black(0);

    /* 0x104c6. The width, in quarter-pixels: each glyph advances by four times
     * what it occupies past its own origin, less 0x1a. */
    for (n = 0; text[n]; n++) {
        sprite_t far *s = &set.base[charmap[(uint8_t) text[n]]];

        width += ((s->w - s->ox) << 2) - 0x1a;
    }
    width >>= 2;
    x = ((0x144 - width) << 1) - 0xd;              /* 0x10517 */

    state = malloc((size_t) n * sizeof *state);    /* 0x1052d - eight bytes each */
    for (i = 0; i < n; i++) {
        state[i].y     = colour ? (i * 8 + 0x5a) : -(i * 8);
        state[i].speed = 4;
        state[i].amp   = 4;
        state[i].above = 1;
    }

    palette_apply_gamma();                         /* 0x105b3 */
    palette_upload();

    do {
        int16_t at = x, plane;

        image_clear(dest, 0);                      /* 0x105c3 */
        settled = 0;
        for (i = 0; i < n; i++) {
            sprite_t far *s = &set.base[charmap[(uint8_t) text[i]]];
            int16_t       was = state[i].above;

            sprite_to_image(at >> 2, state[i].y, s, dest, colour);
            at += ((s->w - s->ox) << 2) - 0x1a;

            state[i].y    += state[i].speed;       /* 0x1064f */
            state[i].above = state[i].y < 0x1e;
            if (state[i].above != was) {           /* 0x106b3 - it crossed */
                if (state[i].amp < 0)
                    state[i].amp = (int16_t) (-state[i].amp - 1);
                else if (state[i].amp > 0)
                    state[i].amp = (int16_t) (-(state[i].amp - 1));
            } else if (state[i].speed < state[i].amp) {
                state[i].speed++;                  /* 0x1073f */
            } else if (state[i].speed > state[i].amp) {
                state[i].speed--;                  /* 0x1076d */
            }
            if (state[i].speed == 0 && state[i].amp == 0)
                settled++;
        }

        for (plane = 0; plane < 4; plane++) {      /* 0x107aa */
            set_plane((uint8_t) plane);
            blit_rows(dest, rect, 0);
        }
        page_flip();
        palette_fade_step(0);

        if (settled == n)                          /* 0x107e7 - four in a row */
            still++;
    } while (still < 4);

    free(state);
    sprite_set_free(&set);
    fade_level = 0;                                /* 0x10812 */
}

/* ------------------------------------------------------ 0x1081c: image_overlay
 *
 * Stamp one image onto another, transparent where the source is zero, starting
 * at `row` down the destination. In the 360x240 mode it slides by the twenty
 * rows that mode has spare: a source going to row 0 is read from its own row
 * 0x14 instead, and anywhere else is written twenty rows lower. That is how the
 * same 320x200 artwork lands correctly in either resolution.
 */
void far image_overlay(desc_t far *src, desc_t far *dst, int16_t row)
{
    int16_t sy = 0, end = src->h, x;

    if (video_mode) {                              /* 0x10836 */
        if (row) { end -= 0x14; row += 0x14; }
        else       sy += 0x14;
    }
    for (; sy < end; sy++, row++)
        for (x = 0; x < 0x140; x++) {
            uint8_t px = src->rows[sy][x];

            if (px)
                dst->rows[row][x] = px;
        }
}

/* -------------------------------------------------- 0x0b739: draw_level_status
 *
 * The line along the bottom of the level's intro screen: which level, the score
 * and the lives, from one format string and three of the game's own words, then
 * centred by its own measured width.
 */
void far draw_level_status(desc_t far *dest)
{
    char line[0x50];

    sprintf(line, "%s: %i  -  %s: %i  -  %s: %i",   /* d+0x2430 */
            menu_text[49], level_attempted,
            menu_text[50], score,
            menu_text[51], lives);
    draw_string(dest, line, 0xa0 - text_width(line) / 2, 0xb9);
}

/* ----------------------------------------------- 0x10ba4: episode_for_level
 *
 * Which episode the level about to be played belongs to, or 0xff. The egg has
 * to match as well as the range, because two eggs can number levels the same.
 * The loop does not stop at the first hit - the last match wins, exactly as the
 * tool-list scans do.
 */
int16_t far episode_for_level(void)
{
    int16_t i, found = 0xff;

    for (i = 0; i < episode_count; i++)
        if (episode_index[i].first <= level_attempted
            && episode_index[i].last >= level_attempted
            && episode_index[i].egg == episode_egg_index)
            found = i;
    return found;
}

/* --------------------------------------------------- 0x0b284: level_map_draw
 *
 * The episode intro is a **map of the level**, drawn into the picture that
 * screen loaded: every fourth pixel of the backdrop, so a quarter of the size
 * in each direction, centred in 320x200. Where the backdrop is transparent the
 * background tile shows through, wrapped, exactly as the compositor does it.
 *
 * Then a marker per duck - sprite 80, or 81 for the hero, which is the entity
 * whose type is 1 - and one per scenery item of types 6 to 0x0a, all five of
 * which share sprite 82 (the jump table at image 0x0b525 has five entries and
 * they are the same address). Positions are the entity's own, quartered.
 *
 * Last, a one-pixel frame in colour 0 with a one-pixel shadow down and right.
 */
void far level_map_draw(desc_t far *dest)
{
    int16_t x0 = (0x140 - level_w / 4) / 2;        /* 0x0b28c */
    int16_t y0 = (0x0c8 - level_h / 4) / 2;
    int16_t dx = x0, dy = y0;
    int16_t sx, sy, i;

    for (sy = 2; sy < level_h; sy += 4) {          /* 0x0b2b7 */
        dx = x0;
        for (sx = 2; sx < level_w; sx += 4) {
            uint8_t px = backdrop.rows[sy][sx];

            if (px == 0)                           /* 0x0b2de */
                px = background.rows[sy & wrap_y][sx & wrap_x];
            dest->rows[dy][dx] = px;
            dx++;
        }
        dy++;
    }

    for (i = 0; i < scenes[0].count; i++) {        /* 0x0b336 - the ducks */
        entity_t far *e = &scenes[0].entities[i];

        sprite_to_image_plain((int16_t) (e->x / 4) + x0,
                              (int16_t) (e->y / 4) + y0,
                              &sprite_table.base[80 + (e->type == 1)], dest);
    }

    for (i = 0; i < scenes[2].count; i++) {        /* 0x0b3c0 - the scenery */
        entity_t far *e = &scenes[2].entities[i];

        if (e->type >= 6 && e->type <= 0x0a)
            sprite_to_image_plain((int16_t) (e->x / 4) + x0,
                                  (int16_t) (e->y / 4) + y0,
                                  &sprite_table.base[82], dest);
    }

    for (i = x0; i < dx; i++) {                    /* 0x0b44c - top and bottom */
        dest->rows[y0 - 1][i] = 0;
        dest->rows[dy][i]     = 0;
        dest->rows[dy + 1][i + 1] = 0;             /* the shadow */
    }
    for (i = y0; i < dy; i++) {                    /* 0x0b4a5 - the sides */
        dest->rows[i][x0 - 1] = 0;
        dest->rows[i][dx]     = 0;
        dest->rows[i + 1][dx + 1] = 0;
    }
    dest->rows[dy][dx] = 0;                        /* 0x0b509 - the corner */
}

uint8_t      level_palette[768]; /* 0x0de1 - the palette a level is played
                                  * through, blended from default_buffer here
                                  * and published with set_buffer */

/* -------------------------------------------------- 0x0d5c5: level_palette_build
 *
 * The level's colours, and the reason a level drawn without it comes out black
 * where the numbers and the cursor should be.
 *
 * Every entry of the palette the level's resources loaded is tinted toward the
 * level's own three fractions, in proportion to the fourth - so the four bytes
 * the level block carries, which nothing had been seen to read, are a colour
 * grade. The weight per entry is the average of its own red and green.
 *
 * Then the part that matters for every sprite on the screen: entries 0x50-0x6f
 * are **duplicated at 0xe0-0xff**. Sprite pixels are already palette indices in
 * the sprite set's own 0x50-0x6f slice, and the HUD and the cursor are drawn
 * with a colour bias of 0x90 - which lands exactly on the copy. Without it they
 * draw through whatever is at 0xe2-0xf5, which on a fresh palette is nothing.
 *
 * Two level flags override slices of the result outright: [0x2026] restores
 * entries 0x40-0x4f, and [0x2028] restores 0x00-0x0f and takes the top copy
 * from the source rather than from the tinted result.
 */
void far level_palette_build(void)
{
    int16_t i, c, di = 0;

    text_colour[0] += 0x90;                        /* 0x0d5cf */
    text_colour[1]  = 0x5b;
    set_buffer(level_palette);                     /* 0x0d5e1 - d+0x0de1 */

    for (i = 0; i < 0xe0; i++) {                   /* 224 entries, not 256 */
        /* 0x0d5eb: (r + g) / 2.0, the constant at d+0x2498 */
        float scale = (float) (default_buffer[di] + default_buffer[di + 1]) / 2.0f;

        for (c = 0; c < 3; c++) {
            float t = (float) default_buffer[di];

            level_palette[di] = (uint8_t) (t * (1.0f - level_frac[3])
                                           + level_frac[c] * scale * level_frac[3]);
            di++;
        }
    }

    if (level_flags[4])                            /* 0x0d661 - [0x2026] */
        for (i = 0xc0; i < 0xf0; i++)
            level_palette[i] = default_buffer[i];

    if (level_flags[5]) {                          /* 0x0d67c - [0x2028] */
        for (i = 0; i < 0x30; i++)
            level_palette[i] = default_buffer[i];
        for (i = 0xf0; i < 0x150; i++)
            level_palette[i + 0x1b0] = default_buffer[i];
    } else {
        for (i = 0xf0; i < 0x150; i++)
            level_palette[i + 0x1b0] = level_palette[i];
    }
}

/* ------------------------------------------------------- 0x07490: stamp_solid
 *
 * One solid object into the backdrop, and the test is on the **destination**:
 * a pixel is written only where what is already there is zero. So the tiles
 * win every argument and an object fills in around them, which is the opposite
 * of the usual transparency rule and is worth not getting backwards.
 */
void far stamp_solid(solid_t far *o, desc_t far *dest)
{
    int16_t sy = 0, dy;

    for (dy = o->y; dy < o->bottom; dy++, sy++) {
        uint8_t far *src = o->img.rows[sy];
        uint8_t far *dst = dest->rows[dy] + o->x;
        int16_t      x;

        for (x = o->x; x < o->right; x++, src++, dst++)
            if (*dst == 0)
                *dst = *src;
    }
}

/* --------------------------------------------------------- 0x088fa: the level
 *
 * One 'L' block, read straight through: the map, the tools, the entities, the
 * ducks, the flags, the solid objects. Then it builds what gets drawn - the
 * backdrop out of 20x20 tiles, the objects stamped into it, the leftover ducks
 * scattered where the backdrop is still empty - and finally the viewport the
 * in-game scenes clip to.
 */
void far level_load(void)
{
    desc_t   map;                            /* [bp-0x70] */
    desc_t   tiles;                          /* [bp-0x5a] */
    char     detail[0x22];                   /* [bp-0x22] - the "(%i)" */
    uint8_t  ambience[0x22];                 /* [bp-0x44] */
    uint8_t  tile_set_id, records;           /* [bp-0x15], [bp-0xd] */
    int16_t  spare_ducks = 0;                /* [bp-0xb] - 0x10 once a 0x4f
                                              * has been seen, and half of that
                                              * is how many ducks are scattered */
    int16_t  tool_slots  = 0;                /* [bp-0xc] - 0x10 for tool 0x50 */
    int16_t  wide_scene1 = 0;                /* [bp-0x14] - set by a 0x42 */
    int16_t  background_id;                  /* [bp-6] */
    int16_t  ambience_n;                     /* [bp-0xa] */
    int16_t  i, j, x, y, type, layer;

    pair_slots = 0;
    play_log   = 0;
    release_sounds();

    scene_alloc(&scenes[5], 1);                       /* 0x08922 - d+0xd9f */
    scene_alloc(&scenes[3], 1);                       /* 0x0892f - d+0xd87 */

    if (!egg_find_block(0x4c, (uint8_t) level_attempted, episode_egg_index)) {
        sprintf(detail, "%i", level_attempted);
        fatal("Can't find level", detail);            /* 0x08969 */
    }

    tile_set_id   = egg_read_byte(egg_stream);
    sprite_set_id = egg_read_byte(egg_stream);
    map.w         = egg_read_byte(egg_stream);        /* in tiles, not pixels */
    map.h         = egg_read_byte(egg_stream);
    image_alloc(&map, map.w, map.h);                  /* 0x089c2 */

    for (i = 0; i < map.h; i++)                       /* 0x089cc */
        for (j = 0; j < map.w; j++)
            map.rows[i][j] = egg_read_byte(egg_stream);

    background_id = egg_read_byte(egg_stream);
    tool_count    = egg_read_byte(egg_stream);

    if (tool_count) {                                 /* 0x08a34 */
        tool_list = malloc((size_t) tool_count * 2);
        if (!tool_list)
            fatal(out_of_memory, 0);                  /* d+0x500 */
        for (i = 0; i < tool_count; i++) {
            tool_list[i] = egg_read_byte(egg_stream);
            if (tool_list[i] == 0x50)                 /* 0x08a97 */
                tool_slots = 0x10;
        }
    } else {
        tool_list    = malloc(2);
        tool_list[0] = 0;
    }

    records = egg_read_byte(egg_stream);
    scene_alloc(&scenes[2], records + tool_slots);     /* 0x08aef - d+0xd7b */
    scenes[2].keep_order = 1;                            /* 0x08af5 */
    scenery_count     = 0;
    quota_left        = 0;

    for (; records; records--) {                      /* 0x08b0e */
        x     = egg_read_word(egg_stream);
        y     = egg_read_word(egg_stream);
        type  = egg_read_byte(egg_stream);
        layer = 5;

        switch (type) {
        case 0x51:                                    /* 0x08b85 */
            scene_add(&scenes[2], x, y, type, layer);
            continue;
        case 0x4f:                                    /* 0x08b9f */
            scene_add(&scenes[2], x, y, type, layer);
            spare_ducks = 0x10;
            continue;
        case 0x42:                                    /* 0x08bbd */
            scene_add(&scenes[2], x, y, type, layer);
            wide_scene1 = 1;
            continue;
        case 0x4d:                                    /* 0x08bdb */
            if (cheat_state[9] || level_attempted != g_1ffa)
                scene_add(&scenes[2], x, y, type, layer);
            continue;
        case 6: case 7: case 8: case 9: case 0x0a:    /* 0x08c04 - all one arm,
                                                       * the jump table at image
                                                       * 0x9321 has four entries
                                                       * and they are the same */
            scenery_count++;
            layer       = type - 5;
            quota_left += layer;
            break;
        default:
            break;
        }

        if (type_flags[type] & 2) {                   /* 0x08c1b */
            pair_slots += 2;
            scene_add(&scenes[5], x, y, type, 5);
        } else {
            scene_add(&scenes[2], x, y, type, layer);
        }
    }

    duck_count = egg_read_byte(egg_stream);           /* 0x08c63 */
    scene_alloc(&scenes[0], duck_count + spare_ducks
                            + (scenes[2].entities[0].type == 0x51 ? 8 : 0));
    scene_alloc(&scenes[1], wide_scene1 ? duck_count + 5 : 5);

    for (i = 0; i < duck_count; i++) {                /* 0x08cd4 */
        x = egg_read_word(egg_stream);
        y = egg_read_word(egg_stream);
        scene_add(&scenes[0], x, y, 4, 0);
    }

    if (scenes[2].entities[0].type == 0x51) {         /* 0x08d19 */
        scene_add(&scenes[0], 0xa0, 0x64, 0x53, 0);
        scenes[0].entities[0].f14 = (int8_t) ((game_rand() & 2) - 1);
        duck_count++;
    }
    duck_count += spare_ducks / 2;

    i = egg_read_byte(egg_stream);                    /* which duck is the hero */
    if (spare_ducks == 0 && i < scenes[0].count) {    /* 0x08d7d */
        scenes[0].flag = i;                           /* d+0xd67 */
        entity_set_type(&scenes[0].entities[i], 1);
    }

    level_text = egg_read_string(egg_stream);         /* 0x04f4b */

    for (i = 0; i < 7; i++)                           /* 0x08dcb */
        level_flags[i] = egg_read_byte(egg_stream);
    bg_drift     = egg_read_byte(egg_stream);
    timer_period = egg_read_word(egg_stream);

    j = egg_read_byte(egg_stream);                    /* the hero's facing */
    /* 0x08e25 tests the index against 0 and nothing else, so a level with no
     * hero - flag is 0xff, which scene_alloc set and nothing replaced - passes
     * it and the original writes entities[255].f14, well past a 16-entry array.
     * In DOS that lands in whatever follows on the heap and nobody notices.
     * Level 4 is such a level, and ASan stops on it here.
     *
     * The 0xff is excluded, which is a deviation: the original's write goes
     * somewhere and this one does not go anywhere. Nothing can read what it
     * wrote, so nothing observable is lost - but it is a choice, not a match. */
    if (scenes[0].flag && scenes[0].flag != 0xff)     /* 0x08e25 */
        scenes[0].entities[scenes[0].flag].f14 = (int8_t) (j - 1);

    ambience_on = egg_read_byte(egg_stream);
    ambience_n  = egg_read_byte(egg_stream);
    for (i = 0; i < ambience_n; i++)
        ambience[i] = egg_read_byte(egg_stream);

    solid_count = egg_read_byte(egg_stream);          /* 0x08e96 */
    solids      = malloc((size_t) solid_count * sizeof *solids);
                                                      /* 0x20 in the original */
    if (!solids)
        fatal(out_of_memory, 0);
    for (i = 0; i < solid_count; i++) {               /* 0x08ed1 */
        solids[i].id = egg_read_byte(egg_stream);
        solids[i].x  = egg_read_word(egg_stream);
        solids[i].y  = egg_read_word(egg_stream);
    }

    next_level = egg_read_byte(egg_stream);
    /* Four bytes as 1/256ths - the only floating point in the level format, and
     * the stream order is not the memory order. */
    level_frac[3] = egg_read_byte(egg_stream) / 256.0f;   /* 0x13ed */
    level_frac[0] = egg_read_byte(egg_stream) / 256.0f;   /* 0x13e1 */
    level_frac[1] = egg_read_byte(egg_stream) / 256.0f;   /* 0x13e5 */
    level_frac[2] = egg_read_byte(egg_stream) / 256.0f;   /* 0x13e9 */
    egg_block_end();                                  /* 0x08fd6 */

    for (i = 0; i < solid_count; i++) {               /* 0x08fde */
        if (!resource_load(&solids[i].img, 0x51, solids[i].id, 0x50, 0,
                           episode_egg_index, 1)
            && !resource_load(&solids[i].img, 0x51, solids[i].id, 0x50, 0,
                              0, 1)) {
            sprintf(detail, "%i", solids[i].id);
            fatal("Can't load solid object image", detail);
        }
        solids[i].right  = solids[i].x + solids[i].img.w;
        solids[i].bottom = solids[i].y + solids[i].img.h;
    }

    level_w = map.w * 0x14;                           /* 0x090d4 - 20x20 tiles */
    level_h = map.h * 0x14;
    /* 0x090f7: alloc_image(&backdrop, 1, 1, 0xa, 1). What those four arguments
     * mean is not read out - 0x05388 is 745 bytes and manages a buffer of its
     * own - but the size is not in doubt: the tile paint below writes every one
     * of level_h rows and level_w columns, so that is what it must produce. */
    image_alloc(&backdrop, level_w, level_h);

    if (!resource_load(&tiles, 0x54, tile_set_id, 0x10, 0, episode_egg_index, 1)
        && !resource_load(&tiles, 0x54, tile_set_id, 0x10, 0, 0, 1)) {
        sprintf(detail, "%i", tile_set_id);
        fatal("Can't load tiles", detail);
    }

    for (i = 0; i < map.h; i++)                       /* 0x09167 */
        for (j = 0; j < map.w; j++) {
            int16_t tile = map.rows[i][j];
            int16_t row;

            for (row = 0; row < 0x14; row++)
                memcpy(backdrop.rows[i * 0x14 + row] + j * 0x14,
                       tiles.rows[tile * 0x14 + row], 0x14);
        }

    resource_release(&tiles);                         /* 0x091fb */
    resource_release(&map);

    for (i = 0; i < solid_count; i++)                 /* 0x09211 */
        stamp_solid(&solids[i], &backdrop);

    for (i = 0; i < spare_ducks / 2; i++) {           /* 0x0923d */
        do {
            x = (game_rand() & 0xff) + 0x20;
            y = (game_rand() & 0x3f) + 2;
        } while (backdrop.rows[y][x] != 0);           /* open space only */
        scene_add(&scenes[0], x, y, 4, 0);
    }

    load_background((uint8_t) background_id, episode_egg_index);
    text_colour[0] = 0x6f;                            /* 0x092a3 */

    /* 0x092a8. The level centred in what is left above the 40-row panel, or
     * hard against the edge when it is bigger than the screen. */
    {
        int16_t left = level_w > screen_width
                     ? 0 : (screen_width - level_w) / 2;
        int16_t high = screen_height - 0x28;
        int16_t top  = level_h > high ? 0 : (high - level_h) / 2;

        make_rect(&viewport_game, top, high - top, left, screen_width - left);
    }

    if (ambience_volume)                              /* 0x092f8 */
        for (i = 0; i < ambience_n; i++)
            sound_preload(ambience[i], ambience_volume);
}
