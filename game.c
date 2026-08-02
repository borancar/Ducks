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
#include <strings.h>       /* strcasecmp, which is Borland's stricmp */

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
uint8_t    game_speed;           /* 0x1fd4 - 0..0x1f, higher is faster; read
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
uint8_t    shareware_limit;      /* 0x054a - per egg, not a constant; read as
                                  * a byte with the high half zeroed */
int16_t    registered;           /* 0x0548 */
int16_t    lives;                /* 0x2034 - decremented on a lost run */
uint16_t   max_save_value;       /* 0x2055 - scan_save_slots' only output;
                                  * compared with `jbe` */

/* ------------------------------------------------------- the animation tables
 *
 * load_animations fills all six of these from the 'G' block, one entry per
 * entity type. They are consecutive in DGROUP and the last of them ends exactly
 * where settings begins, which is what fixes the length at 112.
 */
int16_t far *anim_script[112];   /* 0x009a - a sprite index per step, 999 ends */
uint8_t      anim_a[112];        /* 0x025a - read nowhere yet */
uint8_t      anim_b[112];        /* 0x02c9 */
uint8_t      anim_c[112];        /* 0x0338 */
uint8_t      type_flags[112];    /* 0x03a7 - bit 2 says the type has a mirrored
                                  * script in the next slot */
int16_t      next_type[112];     /* 0x0416 - what a type becomes when its script
                                  * runs out; a type that points at itself loops */

/* the menu and the attract cycle */
int16_t    attract_choice;       /* 0x21ae - 0 demos, non-zero shows a screen */
int16_t    menu_idle_suppress;   /* 0x2177 - non-zero holds the menu still */
uint8_t    g_2038;               /* 0x2038 - how many demos to choose from;
                                  * byte, high half zeroed */

/* strings and buffers */
char       save_name[];          /* 0x21a5 - the template "GAME-.SG" */
char far  *settings_name;        /* 0x21d2 - "settings.dat" */
uint8_t    g_28ff[1];            /* 0x28ff - main's first splash source */
void far  *buf_200f, *buf_203b, *buf_203f, *buf_2043;  /* freed per demo */
/* The string tables - see dos.h. Two of them are far data at 0x1894:0 and
 * 0x1894:4, which is why main loads them with an explicit segment. */
char far **menu_text;            /* 0x1894:0000 */
uint8_t    menu_text_count;      /* 0x0096 */
char far **extra_text;           /* 0x1894:0004 */
uint8_t    extra_text_count;     /* 0x0098 */
char far **cheat_text;           /* 0x0519 */
uint8_t    cheat_text_count;     /* 0x0504 */
/* 0x0505. One flag per cheat word, toggled by typing it. Ten words, twenty
 * bytes, and the array ends exactly where cheat_text begins. What run_screen
 * reads as "[0x515]" is element 8 of this, not a variable of its own. */
int16_t    cheat_state[10];
int16_t    left_handed;          /* 0x0511 - LEFT HANDED, which swaps the side of
                                  * the pen the cursor's tool is drawn on */
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
void far  *init_objects[3];      /* 0x210c - three 22-byte objects, stride 4 */
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
scene_t    cursor_scene;         /* 0x0d93 - one entity: the mouse pointer */
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
int16_t  g_509, g_50b, g_1ffa, g_1ffc, g_1ffe, g_18e1, g_18e3;
uint8_t  g_18f5, g_1fd3;        /* both byte-sized on every access */
int16_t  g_201c;                /* compared with jle, so signed */
uint16_t g_2036;                /* compared with jb, so unsigned */
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
void far resource_load_at(desc_t far *desc, uint8_t type, uint8_t index,
                          int16_t pal_at, int16_t row, int16_t egg)
{
    resource_load_full(desc, 0, type, index, pal_at, 0, row, 0, egg, 1);
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
    s->unread6  = 0;
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

/* 0x08885. Sets a descriptor's size and allocates its rows. */
void far image_alloc(desc_t far *desc, int16_t w, int16_t h)
{
    desc->w = w;
    desc->h = h;
    alloc_image(desc, 0, 0, 0, 1);                 /* 0x05388 */
}

/* 0x088b3. Frees a sprite set: every sprite's pixels, then the records. */
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
    /* TODO 0x094cd: the tool names go through 0x0e09b rather than the loader
     * below, and that one has not been read. */
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
        plot(p->x >> 3, p->y >> 3, p->colour);     /* 1/8-pixel fixed point */
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
 * TODO 0x0a58e-0x0a7ed: before the default there is a switch with arms for
 * types 0x1a-0x24, 0x2f, 0x46, 0x47, 0x4e and 0x54 - the game's own animation
 * behaviour, none of which any menu entity reaches.
 */
void far animate_scene(scene_t far *scene)
{
    int16_t i;

    for (i = 0; i < scene->count; i++) {
        entity_t far *e = &scene->entities[i];

        e->frame++;
        if (anim_script[e->type][e->frame >> 1] != 0x3e7)
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
            if (left_handed)
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
                int16_t mirror = (e->f14 == (left_handed ? -1 : 1));

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

glyph_t font[256];               /* 0x054d */
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
 * Called once per open egg by egg_load_pass_0x48. It is not only a loader - it
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

/* ------------------------------------------------ 0x0c156: egg_load_pass_0x48 */
void far egg_load_pass_0x48(void)
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
    for (j = 0; j < strlen(src); j++)              /* 0x0c338: strlen each time */
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
 * The flash is four OUTs straight at the DAC. The SDL backend cannot see them,
 * so a cheat there toggles silently.
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

/* 0x0c4f0. The slider an item with action 0x11 opens: GAME SPEED, AMBIENCE
 * VOLUME, GAMMA CORRECT.
 *
 * TODO 0x0c4f0-0x0c715: a screen of its own, with its own frame loop. It clears
 * a 32-byte bar, draws 38 tiles of it through 0x0739c, writes the label with
 * 0x06dbc, and then reads the mouse against the item's setting. Left out so the
 * menu itself can be exercised; choosing one of the three does nothing.
 */
void far slider_screen(item_t far *it, int16_t y)
{
    (void) it; (void) y;
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

            if (idle++ > 0x1f4 && !menu_idle_suppress) {
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
                        if (menu == &main_menu && !menu_idle_suppress) {
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

        draw_sprite(&tile,  x + i * 12, y, &sprite_table, &hud_clip, 0); /* behind */
        draw_sprite(&glyph, x + i * 12, y, &sprite_table, &hud_clip, 0); /* over it */
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
                     &menu_idle_suppress);
    menu_add_submenu(&menu_play, menu_text[12], &main_menu,      &menu_always);
    menu_play.background = 12;

    menu_reset(&menu_end_game);
    menu_add_title(&menu_end_game, extra_text[10]);
    menu_add_action(&menu_end_game, extra_text[11], 3, &menu_idle_suppress, 0);
    menu_add_submenu(&menu_end_game, extra_text[12], &menu_play, &menu_always);
    menu_end_game.background = 13;

    menu_reset(&menu_load_save);
    menu_add_title(&menu_load_save, menu_text[11]);
    menu_add_action(&menu_load_save, menu_text[35], 5, &menu_idle_suppress, 0);
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
    f_04dcd(150);                          /* the hold - unnamed */
    resource_release(&desc);
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
            f_0f8bd();
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
int16_t far episode_end_gate(int16_t level, int16_t egg)
{
    int16_t i, flag = 0;

    for (i = 0; i < episode_count; i++) {
        if (episode_index[i].last != level)  continue;
        if (episode_index[i].egg  != egg)    continue;

        sound_play_guarded(0x1a, 1);
        show_splash(menu_text[47], 100);            /* "EPISODE COMPLETED!" */
        f_11bee(episode_index[i].name, egg);        /* draws it - unnamed */
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
                f_088fa();
                free(buf_200f);
                g_18f5 = 5;  g_1ffc = 0;
                saved = g_509;  g_509 = 0;     /* switched off for the demo */
                in_game_frame(1);                  /* 0x1279d - it IS the game */
                g_509 = saved;
                free(buf_2043);  free(buf_203f);  free(buf_203b);
                release_sounds();
            } else {
                show_splash("DEMO MISSING", 100);  /* 0x1287e, DGROUP+0x26bd */
            }
            attract_choice = !attract_choice;      /* 0x127ee: screen, demo, ... */
            break;

        case 0x15:                                 /* play the demo named */
            if (load_demo(r->param)) {             /* 0x1240f */
                /* TODO 0x12811-0x1283a: elided because it is byte for byte the
                 * same as the branch above - the three frees, g_18f5, the
                 * g_509 save and restore. Worth writing out if that ever turns
                 * out not to be exactly true. */
                in_game_frame(1);                  /* 0x1283a */
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
        case 3:   high_score_screen();  f_0f55c();
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
            /* FALL THROUGH into the play loop */

        case 2:                                    /* 0x137f6: play, tally, repeat */
            for (;;) {
                if (g_1ffc) {
                    sound_play_guarded(0x29, 1);
                    show_splash(menu_text[40], 200);   /* "SECRET LEVEL!" */
                }
                if (f_1102a(g_21a3))             /* a screen; non-zero leaves */
                    break;
                g_18f5 = 2;

                if (shareware_limit < level_attempted      /* 0x13841 */
                    && !registered && !g_1ffc) {
                    f_09329();                     /* the refusal - unnamed */
                    egg_load_one(0xfc, 0x48, 0xff);
                    menu = &main_menu;
                    high_score_screen();  f_0f55c();
                    break;
                }

                if (!in_game_frame(0)) {           /* 0x1387e: the run ended badly */
                    g_21a3 = 0;
                    if (!g_50b) {
                        --lives;                   /* [0x2034] */
                        sprintf(buf, "%s: %i", menu_text[53], lives);  /* "LIVES LEFT" */
                        show_splash(buf, 100);
                        release_sounds();
                        if (lives == 0) {          /* GAME OVER */
                            menu = &main_menu;
                            sound_play_guarded(0x16, 1);
                            show_resource(0x4d, 6, 50, 0xff);
                        }
                    }
                    break;
                }

                if (g_1ffc || g_1ffe)          /* 0x1388b, 0x13895 */
                    break;

                g_21a3 = 1;                      /* the level was completed */
                sound_play_guarded(2, 1);
                show_resource(0x4d, 2, 50, 0xff);  /* the BONUS SCREEN */
                f_0becb();
                /* TODO 0x138c4-0x13904: a comparison of [0x2036] against
                 * [0x201c] and whatever it guards, not read. */

                /* The ending. Only DUCKING HELL - level 80 - passes the gate. */
                if (episode_end_gate(level_attempted, episode_egg_index)
                    && episode_egg_index == 0) {                /* 0x1390d */
                    set_buffer(&buf[0]);
                    cutscene_rocket_space();                    /* id 0x32 */
                    f_147c5(0x4a, g_1fd3, 0xff);
                    cutscene_rocket_landing();                  /* ids 0x33/0x34 */
                    cutscene_doorstep();                        /* ids 0x37/0x38 */
                    cutscene_welcome_home();                    /* id 0x36 */
                    release_sounds();
                    cutscene_photos();                          /* ids 0x3a-0x3c */
                    f_147c5(0x4a, g_1fd3, 0xff);
                    cutscene_night_monster();                   /* the animation */
                    release_sounds();
                    dac_set_black(0, 0);
                    input_poll(320, 200);
                    set_buffer(&buf[0]);
                    high_score_screen();  f_0f55c();
                }
                level_attempted++;                 /* 0x139ab */
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
 * eighty dashes in grey. */
void far console_rule(void)
{
    int16_t i;

    fputs("\n", stdout);                           /* d+0x2555, 0:0x3b4d */
    set_text_colour(7);
    for (i = 0; i < 0x50; i++)
        printf("-");                               /* d+0x2557, 0:0x2012 */
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
    printf("Building episode index...");           /* d+0x25b1 */
    console_rule();
    fputs("\n", stdout);

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

/* ------------------------------------------------- 0x13fea: scan_save_slots
 *
 * Takes nothing, returns nothing; its only output is one global. The names are
 * not five constants - save_name holds the template "GAME-.SG" and the loop
 * patches the digit into offset 4.
 */
void far scan_save_slots(void)
{
    FILE   *fp;
    int16_t i, v;

    for (i = 1; i < 6; i++) {
        save_name[4] = '0' + i;                    /* [0x21a9] */
        fp = fopen(save_name, "rb");
        if (fp) {
            v = f_14e88(fp);                       /* a value out of the save */
            if (v > max_save_value)                /* [0x2055], the only output */
                max_save_value = v;
            /* two more values are fetched through 0x14f4b and freed: read a
             * string, free it */
            fclose(fp);
        }
    }
}

/* ---------------------------------------------------- 0x140b1: save_settings
 *
 * Every value goes out through Borland's putw, one word at a time, in four runs.
 */
void far save_settings(void)
{
    FILE   *fp;
    int16_t i;

    fp = fopen(settings_name, "wb");               /* the far pointer at 0x21d2 */
    if (!fp)
        return;

    fputs("!", fp);                                /* DGROUP+0x2806, a marker */
    putw(0, fp);

    for (i = 0; i < 5; i++)  putw(settings[i], fp);        /* 0x04f4 */
    putw(video_mode, fp);                                 /* 0x04fe - the sixth
                                                           * of the six words the
                                                           * original writes as
                                                           * one run */
    for (i = 0; i < 3; i++)  putw(button_map[i], fp); /* 0x20e4, the mapping */
    for (i = 0; i < 3; i++)  putw(((uint8_t *) &g_1fd3)[i], fp);
                                                   /* 0x1fd3, 0x1fd4, 0x1fd5:
                                                    * three bytes - the middle one
                                                    * is game_speed and the last is
                                                    * gamma - widened to words */
    /* TODO 0x1415e-: more writes follow, not read. */
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
    egg_bringup_open();                            /* stands in for the egg
                                                    * opening and indexing the
                                                    * original does here */
    for (i = 0; i < 3; i++) {                      /* three 22-byte objects */
        init_objects[i] = malloc(22);              /* [0x210c], stride 4 */
        ((desc_t far *) init_objects[i])->w = 316;
        ((desc_t far *) init_objects[i])->h = 15;
        f_15388(init_objects[i]);
    }
    buffer_init();                                 /* bring-up: see stubs.c */
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
    egg_load_pass_0x48();                    /* 0x14527 - and it draws the version
                                              * and credits page, which waits for
                                              * a key */
    sound_play_guarded(0x2b, 1);
    show_resource(0x4d, 5, 50, 0xff);        /* 0x1453f - the Hungry Software logo */
    show_splash(menu_text[39], 100);         /* 0x1455c - "PRESENTS" */
    sound_play_guarded(0x28, 1);
    show_resource(0x4d, 8, 100, 0xff);       /* 0x14577 - the title */

    if (!registered) {                       /* 0x1457d - [0x548] */
        show_splash(menu_text[62], 100);     /* 0x1459b - "UNREGISTERED" */
        sound_play_guarded(0x0b, 1);
    }

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
