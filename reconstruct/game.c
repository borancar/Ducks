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

#include "dos.h"


menu_t         main_menu;               /* ds:0x1916, what main passes in */
menu_t         menu_1989;               /* after starting, saving or loading */
menu_t         menu_1c3b;               /* after a resolution change */

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
int16_t    button_map_a;         /* 0x20e4 - which INT 33h button is which */
int16_t    button_map_b;         /* 0x20e6 */
int16_t    button_map_c;         /* 0x20e8 */
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
char far **tool_names;           /* 0x2106 */
uint8_t    tool_names_count;     /* 0x210a */

/* startup and settings */
int16_t    sound_available;      /* 0x2104 - detect_hardware's result */
void far  *init_objects[3];      /* 0x210c - three 22-byte objects, stride 4 */
int16_t    settings[];           /* 0x04f4 - the word array save_settings
                                         * writes; settings[0] gates sound */

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
        free(egg_files[egg_file_count].block);      /* the pointer at +8 */
    }
}

/* --------------------------------------------- 0x058b9: resource_load_full
 *
 * Pull one resource out of an egg and build a descriptor for it. Everything that
 * draws a screen comes through here.
 *
 *   desc      the descriptor to fill
 *   set_size  when non-zero, write the source's width and height into it
 *   type      the resource type - 0x4d for the screens main and game_main show
 *   index     which one
 *   pal_at    where in the palette buffer this resource's colours go
 *
 * The stream is the open egg at egg_stream ([0x20c6]): egg_find_block seeks it to
 * the resource, then the header is two words and a byte - width, height, and how
 * many palette entries follow - and the palette is read three bytes at a time
 * straight into the current buffer at pal_at * 3.
 */
int16_t far resource_load_full(desc_t far *desc, int16_t set_size,
                               uint8_t type, uint8_t index, int16_t pal_at,
                               int16_t arg18, int16_t arg1a)
{
    int16_t w, h, colours, i, x0;

    if (!egg_find_block(type, index, arg18))       /* 0x05232 */
        return 0;

    w       = egg_read_word(egg_stream);           /* 0x04e88 */
    h       = egg_read_word(egg_stream);
    colours = egg_read_byte(egg_stream);           /* 0x03791 */

    if (set_size) {
        desc->w = w;                               /* +0x0c */
        desc->h = h;                               /* +0x0e */
    }

    if (!alloc_image(desc, 0, 0, 0, arg1a))        /* 0x05388 */
        return 0;

    /* Where this image sits inside the descriptor's own width, so a source
     * narrower than the destination lands centred rather than at the left. */
    x0 = (desc->w - w) >> 1;

    for (i = 0; i < colours * 3; i++)              /* three bytes an entry */
        ((uint8_t far *) current_buffer)[pal_at * 3 + i] =
            egg_read_byte(egg_stream);

    f_0580b();                                     /* reset the chunk decoder */

    /* The rows. One pixel at a time out of the decoder, placed at x0 so a source
     * narrower than the destination lands centred. Zero is transparent unless the
     * caller asked for it to be kept - which is the arg18 test at 0x059b6. */
    for (i = 0; i < h; i++) {
        int16_t x;

        for (x = 0; x < w; x++) {
            uint8_t px = egg_next_pixel();

            if (px || arg18)
                desc->rows[i][x0 + x] = px;
        }
    }
    egg_block_end();                               /* 0x05a55 - releases the
                                                    * lock the find took */
    return 1;
}

/* --------------------------------------------- 0x05a67: resource_load
 *
 * The form everything actually calls: the same thing with `set_size` forced to 1
 * and two of the arguments fixed. A thin forwarder, twenty bytes of pushes.
 */
int16_t far resource_load(desc_t far *desc, uint8_t type, uint8_t index,
                          int16_t pal_at, int16_t set_size,
                          int16_t arg18, int16_t arg1a)
{
    return resource_load_full(desc, 1, type, index, pal_at, arg18, arg1a);
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

    if (p.n[button_map_a])  button_a_down = 1;   /* 0x18df */
    if (r.n[button_map_a])  button_a_down = 0;
    g_18e3 = p.n[button_map_b];            /* 0x18e3 - unnamed */
    g_18e1 = p.n[button_map_c];            /* 0x18e1 - unnamed */
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
 * Most entity types read their sprite index out of the record; types 1, 2 and 4
 * compute it arithmetically, and 0x26 and 0x36 adjust y first.
 */
void far draw_entities(scene_t far *scene, viewport_t view, uint8_t colour)
{
    int16_t i;

    for (i = 0; i < scene->count; i++) {
        entity_t far *e = &scene->entities[i];     /* 0x29-byte records */
        int16_t index = sprite_index_for(e);       /* type-dependent */
        int16_t x = e->x - view.scroll_x;
        int16_t y = e->y - view.scroll_y;

        /* An entity following one of type 0x0f or 0x10 is haloed as well as
         * drawn. This is the path the native declines on rather than
         * reimplementing, and it is what most declines are: a scene usually holds
         * one highlighted entity, so the entity after it takes it. */
        if (previous_type == 0x0f || previous_type == 0x10)
            outline_sprite(&index, x, y, sprite_table, &view);

        draw_sprite(&index, x, (int32_t) y, sprite_table, &view, colour);

        /* Type 5 with y <= 0 retires the entity through 0x78d4, which mutates
         * game state rather than drawing - the reason the native declines here
         * too. Verified by driving the guest's own code on synthetic input, since
         * a balloon floating off the top is not a state you can ask for. */
        if (e->type == 5 && y <= 0)
            retire_entity(e);                      /* 0x078d4 */

        previous_type = e->type;
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
        draw_sprite(&glyph, x + i * 12, y, sprite_table, clip, (uint8_t) flags);
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

        draw_sprite(&tile,  x + i * 12, y, sprite_table, &hud_clip, 0); /* behind */
        draw_sprite(&glyph, x + i * 12, y, sprite_table, &hud_clip, 0); /* over it */
        value /= 10;
    }
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
record_t far *menu_screen_driver(menu_t far *menu, void far *a, int16_t b)
{
    record_t far *r;
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
    record_t far *r;
    int16_t       running = 1;                     /* si, set at 0x1367e */
    int16_t       i;

    do {
        r = menu_screen_driver(menu, &buf[0], 1);  /* 0x1368f, five words */

        switch (r->action) {                       /* 0x1369e, table at 0x13a70 */

        case 18:  menu = r->submenu;      break;   /* a submenu is data, not code */
        case 4:   running = 0;            break;   /* QUIT */
        case 14:  register_screen();      break;   /* 0x13096 */
        case 7:   show_readme_section(r->param);   break;
        case 5:   save_game_screen();  menu = &menu_1989;  break;   /* 0x13298 */
        case 6:   load_game_screen();  menu = &menu_1989;  break;   /* 0x12951 */
        case 3:   high_score_screen();  f_0f55c();
                  menu = &main_menu;                       break;

        case 20:                                   /* 0x136fe: MOUSE BUTTONS */
            if (button_map_a == button_map_b || button_map_a == button_map_c
                || button_map_b == button_map_c) {
                /* the duplicate-assignment case; body not read */
            }
            break;

        case 12:                                   /* 0x136cb: RESOLUTION */
        case 13:
            clear_vram();
            set_mode_x(r->action == 13);
            dac_set_black(0, 0);
            menu = &menu_1c3b;
            break;

        case 1:                                    /* START: unpack the episode */
            g_1ffc = 0;  g_1ffa = 0;           /* 0x1377b */
            menu = &menu_1989;
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

    for (i = 0; i < 6; i++)  putw(settings[i], fp);        /* 0x04f4, six words */
    for (i = 0; i < 3; i++)  putw((&button_map_a)[i], fp); /* 0x20e4, the mapping */
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
    /* the remaining banners */

    sound_available = detect_hardware();           /* 0x14974: the sound check,
                                                    * then XMS, then "Free XMS
                                                    * memory: %uk" */
    if (sound_available)
        sound_init(11000);                         /* 0x2af8 = 11000 decimal */

    print_newline();
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
    show_splash(g_28ff, 100);               /* 0x14520 - (1) blank */
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
