/* game.h - the port's own header: its types, its globals and the I/O interface
 * both backends implement.
 *
 * It was called dos.h until 2026-08-08, which described the smaller half of it -
 * dos_io.c is the original reconstructed (VGA ports, Mode X planes, INT 33h) and
 * sdl_io.c is that same interface on SDL3, and game.c does not know which it is
 * linked against. But most of what is here is the game's, not DOS's: the entity,
 * the scene, the viewport, the sprite table, and the prototypes for every routine
 * in game.c, egg.c and sound.c.
 *
 * `far` is what the 16-bit original said; on anything modern it is nothing.
 */
#ifndef DUCKS_GAME_H
#define DUCKS_GAME_H

#include <stdint.h>
#include <stdio.h>

#define far

/* ------------------------------------------------------------------- types */

/* The 20-byte viewport every drawing routine clips against; make_rect fills it. */
/* The 20-byte record everything clips against. The row blitters take the same
 * record and read only its first four words, which is why the original passes a
 * verbatim copy rather than converting anything. */
typedef struct {
    int16_t top;                /* +0x00 */
    int16_t bottom;             /* +0x02 */
    int16_t left;               /* +0x04 - centring offset, added to sprite x */
    int16_t right;              /* +0x06 */
    int16_t width;              /* +0x08 - derived: right - left */
    int16_t height;             /* +0x0a - derived: bottom - top */
    int32_t scroll_x;           /* +0x0c */
    int32_t scroll_y;           /* +0x10 */
} viewport_t;

/* An image: a table of row pointers, plus the size resource_load filled in. */
typedef struct {
    uint8_t far **rows;         /* one pointer per row */
    int16_t       w;            /* +0x0c */
    int16_t       h;            /* +0x0e */
} desc_t;

/* A solid object: one of the level's scenery images, with where it goes. The
 * original's record is 0x20 bytes and starts with a descriptor, so the loader
 * hands the same pointer to resource_load and to stamp_solid. */
typedef struct {
    desc_t  img;                /* +0x00 - rows, and w/h at +0x0c/+0x0e */
    int16_t unread10[3];        /* +0x10 - nothing written or read here */
    int16_t x, y;               /* +0x16, +0x18 - out of the level block */
    int16_t right, bottom;      /* +0x1a, +0x1c - x + w and y + h, after loading */
    uint8_t id;                 /* +0x1e - which 0x51 resource it is */
} solid_t;                      /* 0x20 in the original */

/* A glyph, the 8 bytes at d+0x54d + character*8. The pixels are one byte each,
 * stored column-major, and the byte is not a colour: 0 is transparent, 1 and 2
 * select text_colour[0] and text_colour[1]. */
typedef struct {
    int16_t      w;             /* +0 - the advance is this minus one */
    int16_t      h;             /* +2 */
    uint8_t far *pixels;        /* +4 */
} glyph_t;

/* A sprite, 14 bytes in the original. The origin is subtracted from where it is
 * asked to draw, so a letter's box can start left of its pen position. */
typedef struct {
    int16_t       w, h;         /* +0x00, +0x02 */
    int16_t       ox, oy;       /* +0x04, +0x06 - origin */
    int16_t       unused;       /* +0x08 - nothing read or written here yet */
    uint8_t far  *pixels;       /* +0x0a - row-major, one byte a pixel */
} sprite_t;

/* A set of them: the header sprite_set_load fills and sprite_set_free empties. */
typedef struct {
    int16_t       count;        /* +0x00 */
    sprite_t far *base;         /* +0x02 */
} table_t;

/* A menu item, 0x10 bytes, and a menu is seven of them behind a count. Every
 * screen in the game is this structure: the main menu, the submenus, the
 * confirmations, the episode list, the demo picker.
 *
 * There is no separate "record" type. run_screen returns a pointer to the item
 * the user chose, which is why game_main can switch on r->action and follow
 * r->link without anything converting between the two.
 */
typedef struct menu_s menu_t;

typedef struct {
    char far    *text;          /* +0x00 - str_copy's malloc'd copy, and what
                                 *         item_label overwrites part of */
    menu_t far  *link;          /* +0x04 - action 0x12's submenu */
    int16_t      action;        /* +0x08 - the code game_main switches on */
    uint8_t      value_at;      /* +0x0a - the column item_label writes the
                                 *         ON/OFF or LEFT/RIGHT into: the
                                 *         label's length less the widest
                                 *         value, so the value is right-aligned
                                 *         against the end of the text */
    uint8_t      param;         /* +0x0b - episode ordinal, readme section,
                                 *         demo, or which setting a toggle or a
                                 *         cycle owns */
    int16_t far *visible;       /* +0x0c - the item can be chosen only while
                                 *         this is non-zero. A title's points at
                                 *         menu_never, which is always 0 */
} item_t;

struct menu_s {
    item_t   item[7];           /* +0x00 - menu_add refuses an eighth */
    int16_t  count;             /* +0x70 */
    uint8_t  background;        /* +0x72 - which 'B' resource tiles behind it */
};                              /* 0x73 bytes; the fifteen of them are
                                 * consecutive in DGROUP, 0x1916 to 0x1fd2 */

/* A row of the hall of fame: ten of them at d+0x2057, eight bytes each. The
 * serial is which saved game earned it, so a game already on the board can be
 * recognised when it is loaded and finished again. */
typedef struct {
    char far *name;             /* +0x00 */
    int16_t   score;            /* +0x04 */
    int16_t   serial;           /* +0x06 - save_serial at the time */
} score_t;

/* Three per-button counters, copied about as a unit. */
typedef struct {
    int16_t n[3];
} counts_t;

/* The episode index built at startup from MAIN.EGG; four 14-byte records. */
typedef struct {
    char far *name;             /* +0 - decoded, "TRAINING LEVELS" plainly */
    int16_t   first;            /* +4 */
    int16_t   egg;              /* +6 - which egg file the levels are in */
    int16_t   last;             /* +8 */
    int16_t   ordinal;          /* +0xa */
    int16_t   terminator;       /* +0xc - set only on the last record */
} episode_t;

/* An entity and the scene that holds them. Both layouts are from native.py's
 * reading; the fields nothing has needed yet are absent rather than guessed. */
/* 0x29 bytes. x and y are 32-bit: run_screen assigns the mouse position to the
 * cursor entity with two word stores each, and entity_set_type reaches past
 * both of them for the frame counter and the type. */
typedef struct entity_s {
    int32_t x, y;               /* +0x00, +0x04 */
    uint8_t unread[4];          /* +0x08 - never touched by anything read yet */
    int32_t prev_x, prev_y;     /* +0x0c, +0x10 - where it was when the frame
                                 *         began. scene_keep_positions copies
                                 *         x and y here before anything moves */
    int8_t  vx;                 /* +0x14 - horizontal speed, pixels a frame,
                                 *         and NOT merely a facing. The movement
                                 *         loop at 0x0812a starts `facing` at it
                                 *         and subtracts it each pass, adding
                                 *         what is left to x, so a blocked
                                 *         entity retries with less. Magnitudes
                                 *         above 1 are real: a somersault is
                                 *         +-2, ENTITY_PADDLE's bounce is one
                                 *         per eight pixels off centre, and
                                 *         ENTITY_CATCHER_ROCKET's is the
                                 *         distance to the cursor over eight.
                                 *
                                 *         Its SIGN is the facing, which is all
                                 *         the drawing wants - and for a duck,
                                 *         where it is only ever -1, 0 or 1, the
                                 *         two readings coincide. That is why
                                 *         this said "which way it faces" until
                                 *         the entities were named. */
    int8_t  vy;                 /* +0x15 - vertical speed, and NEGATIVE IS UP.
                                 *         SIGNED: every read in the image is a
                                 *         byte load and a cbw, which is what
                                 *         makes it int8_t and not the uint8_t
                                 *         this said until the casts were
                                 *         counted. Gravity is `if (vy < step)
                                 *         vy++` at 0x080fb, one a frame to a
                                 *         terminal `step`, and every launch
                                 *         writes a negative: the bubble -1,
                                 *         TOOL_BALLOON -2, the rocket -4, a
                                 *         spark -5, a spring -7, and a bouncing
                                 *         head is clamped at -8 */
    int8_t  flock_facing;       /* +0x16 - the direction the FLOCK is going, not
                                 *         this entity's, and signed for the same
                                 *         reason. flock_chain latches the
                                 *         leader's vx here and every follower
                                 *         copies it, so `follow_gap *
                                 *         flock_facing` is where the next one
                                 *         stands; a leader that has stopped
                                 *         keeps the last value that was not 0 */
    int16_t param;              /* +0x17 - scene_add's last argument, and every
                                 *         type means its own thing by it: the
                                 *         rocket's ducks still wanted, a walking
                                 *         duck's momentum, whether the balloon's
                                 *         passenger has been inside solid */
    uint8_t rank;               /* +0x19 - place in the flock, 1 upward, set by
                                 *         flock_chain. Zero is not in it, and
                                 *         entity_update reads it as `active` -
                                 *         an unranked duck does not move */
    uint8_t follow_gap;         /* +0x1a - 8, the pixels between one rank and
                                 *         the next */
    struct entity_s far *lead;  /* +0x1b - which entity type 2 follows */
    int16_t frame;              /* +0x1f - animate_scene's step, zeroed when
                                 *         the type changes */
    int16_t fall;               /* +0x21 - frames spent falling, ++ once a frame
                                 *         while airborne. Over 0x32 on landing
                                 *         kills; over 8 starts the alien's
                                 *         one-shot and bounces a head, whose new
                                 *         vy is scaled by `fall >> 2`. A spring
                                 *         and a teleport both zero it, so the
                                 *         drop is measured from there */
    int16_t counter;            /* +0x23 - scratch, and deliberately not named
                                 *         for a use: the stomper counts to 0x20
                                 *         with it and then rises in 2s, while a
                                 *         walking duck uses it as a turn
                                 *         debounce - 5 when falling fast, and
                                 *         turning while it is set stops the duck
                                 *         dead. draw_entities also subtracts it
                                 *         from y for ENTITY_STOMPER_RISING */
    int16_t type;               /* +0x25 - a word, not a byte */
    int16_t last_type;          /* +0x27 - the type last frame. scene_retire
                                 *         restarts the script when they differ,
                                 *         a second path to the restart
                                 *         entity_set_type does - for changes
                                 *         that did not go through it */
} entity_t;                     /* 0x29 */

typedef struct {
    int16_t       capacity;     /* +0 - how many scene_alloc made room for */
    int16_t       count;        /* +2 */
    int16_t       flag;         /* +4 - scene_alloc sets it to 0xff */
    int16_t       keep_order;   /* +6 - scene_retire shuffles the survivors
                                 * down when this is set and swaps the last
                                 * one into the hole when it is not. The
                                 * level loader sets it on scene 2 */
    entity_t far *entities;     /* +8 */
} scene_t;

typedef struct {
    int32_t x, y;               /* 1/8-pixel fixed point */
    int16_t vx, vy;             /* +0x08, +0x0a - vy is always upward */
    uint8_t colour;             /* +0x0c */
    uint8_t landings_left;      /* +0x0d - how many landings it survives, 1 or
                                 *         2 from the spawner. Each one stains
                                 *         the terrain and takes one off this;
                                 *         at zero the particle is retired */
    int16_t stains;             /* +0x0e - whether touching the ground marks it
                                 *         or merely ends the particle. ALWAYS
                                 *         1: the only writes in the whole image
                                 *         are the 1 at 0x0788f and the copy
                                 *         particle_retire makes at 0x0a94e,
                                 *         which propagates it, so the `!stains`
                                 *         arm at 0x0aa87 is unreachable. The
                                 *         other kind of particle exists in the
                                 *         code and is never made */
} particle_t;                   /* 16 bytes, as in the original */

/* ------------------------------------------------------------------ sound
 *
 * A sample is an XMS handle, an offset into it and a length in the original -
 * ten bytes, because extended memory was the only place 87 sounds would fit.
 * Here it is the bytes themselves. Signed 8-bit at 11111 Hz.
 */
typedef struct {
    uint8_t *pcm;               /* the original's handle and offset */
    int32_t  length;            /* +0x06 */
} sample_t;

/* A voice, twelve bytes at d+0x3c78. `id` is the caller's label, not the
 * sound's - it is what stop_sound_by_id and is_sound_playing match on. */
typedef struct {
    sample_t *desc;             /* +0x00 */
    int16_t   id;               /* +0x04 - 0xffff when free */
    int32_t   cursor;           /* +0x06 */
    int16_t   loop;             /* +0x0a */
} voice_t;

#define SOUND_VOICES 8

/* An egg file entry, stride 0x17. Everything from +0x10 on is filled in by
 * load_animations' tail and build_episode_index, out of the egg's own 'Z' and
 * information blocks. */
typedef struct {
    void far *fp;               /* +0x00 - per-file state */
    char far *name;             /* +0x04 - the file name, "MAIN.EGG" */
    char far *id;               /* +0x08 - the egg's own name, out of its
                                 *         information block. Two eggs with the
                                 *         same one is fatal; freed on the way
                                 *         out by close_egg_files */
    int16_t   slices;           /* +0x0c - what the open banner prints */
    int16_t   data_at;          /* +0x0e - slices * 7 + 2, where the
                                 *         directory ends */
    uint8_t   limit;            /* +0x10 - this egg's shareware limit */
    uint8_t   demo_base;        /* +0x11 - the running demo total after it */
    uint8_t   kind;             /* +0x12 - from its 'Z' block, or 1 */
    uint8_t   version;          /* +0x13 - the format version, 4 to 6 */
    int16_t   contributes;      /* +0x14 - whether its episodes count */
    uint8_t   demos;            /* +0x16 - how many rolling demos it holds */
} egg_file_t;

/* ------------------------------------------------- the state the video owns */

extern int16_t    video_mode;        /* non-zero is the 360-wide mode */
extern int16_t    episode_egg_index; /* 0x0094 - which egg the episode is in */
extern int16_t    screen_width;      /* 360 or 320 */
extern int16_t    screen_height;     /* 240 or 200 */
extern int16_t    screen_x0;         /* centring offset, 20 or 0 */
extern void far (*plot)(int16_t x, int16_t y, uint8_t colour);
extern uint8_t    current_plane;
extern int16_t    wrap_x, wrap_y;               /* 0x1729, 0x172b */
extern uint8_t    bg_scroll_x, bg_scroll_y;     /* 0x177e, 0x177f */
extern uint8_t    bg_step_x, bg_step_y;         /* 0x1780, 0x1781 */
extern uint8_t    warp_phase, warp_step;        /* 0x17bf, 0x17c0 */
extern int16_t    flip_phase;

/* game.c's, which the video reads */
extern uint8_t    game_speed;
extern int16_t    last_key;      /* 0x18f6 - what init spins on */
extern int16_t    any_click;        /* 0x18e5 - any button; escapes the fades */
extern int16_t    button_a_down; /* 0x18df */
extern int16_t    button_b_down; /* 0x18e7 */
extern int16_t    tool_apply_button;  /* 0x18e1 */
extern int16_t    tool_cycle_button;  /* 0x18e3 */
extern int16_t    fade_level;
extern int8_t     fade_direction;
extern int16_t    fade_start_colour;
extern uint8_t    palette_stored[768];
extern uint8_t    palette_washed[48];
extern int16_t    blink_enable, blink_countdown, blink_toggle;
extern viewport_t viewport_panel, viewport_screen, viewport_full;
extern viewport_t viewport_game;     /* 0x172d - built by the level loader */

/* --------------------------------------------------------- the interface */

void far set_bios_mode(uint8_t mode);
void far set_mode_x(int16_t wide);
void far set_plane(uint8_t plane);
void far clear_vram(void);
void far page_flip(void);

void far dac_set_black(uint8_t first, uint8_t count);
void far palette_upload(void);
void far palette_fade_step(int16_t arg);

void far plot_pixel(int16_t x, int16_t y, uint8_t colour);
void far plot_pixel_wide(int16_t x, int16_t y, uint8_t colour);
void far blit_rows(desc_t far *desc, viewport_t rect, int16_t srcrow);
void far blit_rows_masked(desc_t far *desc, viewport_t rect, int16_t srcrow);
void far blit_warped(desc_t far *desc, viewport_t rect, uint8_t step,
                     uint8_t mask);
void far compose_layer(void);
void far compose_scroll(int16_t sx, int16_t sy);
void far draw_sprite(int16_t far *index, int16_t x, int32_t y,
                     table_t far *table, viewport_t far *clip, uint8_t colour);
void far outline_sprite(int16_t far *index, int16_t x, int16_t y,
                        table_t far *table, viewport_t far *clip);

/* The runtime's kbhit and getch, at 0:0x29fc and 0:0x2814. A port replaces them
 * along with everything else the DOS runtime supplied. */
int16_t far key_pending(void);
int16_t far key_read(void);

int16_t far mouse_init(void);       /* 0x067e6 */
void far mouse_motion(int16_t far *dx, int16_t far *dy);
int16_t far mouse_presses(int16_t button);
int16_t far mouse_releases(int16_t button);

/* The four string tables, each an array of far pointers built from an 'H' block
 * at startup. What main indexes as "the table at 0x1894:0" is menu_text, and the
 * offsets it uses are byte offsets into an array of far pointers - +0x9c is
 * menu_text[39], which is "PRESENTS". They are the game's entire user-visible
 * vocabulary; nothing in the executable holds these words.
 *
 * menu_text and extra_text live in their own segment at 0x1894:0 and 0x1894:4
 * rather than in DGROUP; the other two are ordinary DGROUP variables. */
extern char far **menu_text;         /* 0x1894:0000 - 'H' 253, 83 strings */
extern uint8_t    menu_text_count;   /* 0x0096 - checked against 83 */
extern char far **extra_text;        /* 0x1894:0004 - 'H' 251, 15 */
extern uint8_t    extra_text_count;  /* 0x0098 - checked against 15 */
extern char far **cheat_text;        /* 0x0519 - 'H' 254, 10 */
extern uint8_t    cheat_text_count;  /* 0x0504 - checked against 10 */
extern char far **tool_names;        /* 0x2106 */
extern uint8_t    tool_names_count;  /* 0x210a */

void far load_string_table(uint8_t index, char far ***table, uint8_t far *count,
                           const char far *missing, uint8_t egg);

/* The large font: a sprite set, reached through charmap rather than by
 * character code. See game.c for what the pixel values mean. */
extern uint8_t charmap[256];        /* 0x17c1 */

void far build_charmap(void);
void far sprite_alloc(sprite_t far *s);
void far sprite_set_load(uint8_t index, uint8_t type, table_t far *table,
                         int16_t egg);
void far sprite_set_free(table_t far *table);
void far sprite_to_image(int16_t x, int16_t y, sprite_t far *s,
                         desc_t far *desc, uint8_t colour);
void far outline_to_image(int16_t x, int16_t y, sprite_t far *s,
                          desc_t far *desc);   /* 0x07259 */
void far sprite_to_image_plain(int16_t x, int16_t y, sprite_t far *s,
                               desc_t far *desc);
void far image_alloc(desc_t far *desc, int16_t w, int16_t h);
void far draw_banner(const char far *s, table_t far *set, int16_t y,
                     desc_t far *desc, uint8_t colour, uint8_t spacing);
void far load_string_tables(void);

/* the four remaining cutscene screens, in game.c */
void far cutscene_rocket_space(void);
void far cutscene_rocket_landing(void);
void far cutscene_doorstep(void);
void far cutscene_night_alien(void);
void far cutscene_welcome_home(void);
void far cutscene_photos(void);
void far draw_number(int16_t value, int16_t x, int16_t y, viewport_t far *clip,
                     int16_t flags, int16_t digits);
void far draw_number2(int16_t value, int16_t digits, int16_t x, int16_t y);
void far particles(void);
void far particles_step(void);
void far draw_entities(scene_t far *scene, viewport_t view, uint8_t colour);
void far show_splash(const char far *text, int16_t frames);
void far show_resource(uint8_t type, uint8_t index, int16_t frames, int16_t x);
void far show_resource_loop(desc_t far *desc, int16_t frames);
extern episode_t far *episode_index;   /* 0x20ba */
extern int16_t        level_attempted; /* 0x2032 */
void far close_egg_files(void);
void far input_poll(int16_t w, int16_t h);
void far scan_save_slots(void);
void far save_settings(void);
void far init(void);
void far game_main(menu_t far *menu);
int16_t far resource_load(desc_t far *desc, uint8_t type, uint8_t index,
                          int16_t pal_at, int16_t bias_zero,
                          int16_t egg, int16_t arg1a);
int16_t far resource_load_full(desc_t far *desc, int16_t allocate,
                               uint8_t type, uint8_t index, int16_t pal_at,
                               int16_t bias_zero, int16_t row, int16_t opaque,
                               int16_t egg, int16_t arg1a);
int16_t far episode_end_gate(int16_t number, int16_t egg);
item_t far *menu_screen_driver(menu_t far *menu, void far *a, int16_t b);

/* ------------------------------------------------------------- the menus
 *
 * Fifteen descriptors in DGROUP, built once by build_menus and then only read.
 * The names are ours; the addresses are what identifies them.
 */
extern menu_t main_menu;        /* 0x1916 */
extern menu_t menu_play;        /* 0x1989 - PLAY DUCKS */
extern menu_t menu_options;     /* 0x19fc */
extern menu_t menu_quit;        /* 0x1a6f - QUIT? REALLY? */
extern menu_t menu_resolution;  /* 0x1ae2 */
extern menu_t menu_episodes;    /* 0x1b55 - the paged episode list */
extern menu_t menu_audio;       /* 0x1bc8 */
extern menu_t menu_video;       /* 0x1c3b */
extern menu_t menu_mouse;       /* 0x1cae */
extern menu_t menu_load_save;   /* 0x1d21 */
extern menu_t menu_idle;        /* 0x1d94 - never drawn; run_screen returns one
                                 * of its two items when it gives up waiting */
extern menu_t menu_readme;      /* 0x1e07 - the paged section list */
extern menu_t menu_buttons;     /* 0x1e7a - MOUSE BUTTONS */
extern menu_t menu_end_game;    /* 0x1eed - REALLY END THE GAME? */
extern menu_t menu_demos;       /* 0x1f60 - the paged demo list */

extern int16_t menu_always;     /* 0x217f - 1; what most items point at */
extern int16_t menu_never;      /* 0x2181 - 0; what a title points at */
extern desc_t  backdrop;        /* 0x16f5 - the screen the items are drawn into */
extern desc_t  background;      /* 0x170b - the tile compose_layer repeats */

void far menu_reset(menu_t far *m);
void far menu_set_text(item_t far *it, const char far *text);
void far menu_add(menu_t far *m, const char far *text, menu_t far *link,
                  int16_t action, int16_t far *visible, uint8_t param);
void far menu_add_action(menu_t far *m, const char far *text, int16_t action,
                         int16_t far *visible, uint8_t param);
void far menu_add_title(menu_t far *m, const char far *text);
void far menu_add_toggle(menu_t far *m, const char far *text, uint8_t param,
                         int16_t far *visible);
void far menu_add_cycle(menu_t far *m, const char far *text, uint8_t param,
                        int16_t far *visible);
void far menu_add_entry(menu_t far *m, const char far *text, uint8_t param,
                        int16_t far *visible);
void far menu_add_submenu(menu_t far *m, const char far *text,
                          menu_t far *link, int16_t far *visible);
void far menu_free(menu_t far *m);
void far menu_add_list(menu_t far *m, int16_t count, episode_t far *records,
                       int16_t action, const char far *title, menu_t far *back);
void far build_menus(void);

void far item_label(item_t far *it);
void far draw_menu_item(uint8_t index, uint8_t style, int16_t bounce);
void far cursor_to_centre(desc_t far *desc);
void far typed_clear(char far *buf);
int16_t far typed_push(char far *buf, uint8_t ch);
void far slider_screen(item_t far *it, int16_t y);
void far image_clear(desc_t far *desc, uint8_t value);
void far load_background(uint8_t index, int16_t egg);
int16_t far resource_load_at(desc_t far *desc, uint8_t type, uint8_t index,
                             int16_t pal_at, int16_t row, int16_t egg);
void far entity_set_type(entity_t far *e, int16_t type);
void far animate_scene(scene_t far *scene);
void far scene_alloc(scene_t far *s, int16_t capacity);
void far scene_retire(scene_t far *s);              /* 0x0981b */
void far highlight_nearest(scene_t far *s, int16_t mark);  /* 0x0af95 */
void far highlight_for_tool(void);                        /* 0x0d4fc */
extern int16_t       picked_index;                  /* 0x18f3 */
extern entity_t far *picked;                        /* 0x18ef */
extern int16_t       skip_pick_once;                        /* 0x217d */
int16_t far scene_add(scene_t far *s, int16_t x, int16_t y, int16_t type,
                      int16_t param);
#define cursor_scene scenes[4]   /* 0x0d93 is scenes[4] */
void far build_washed_ramp(void);

/* the port I/O the original used; a port supplies its own or drops them */
void outp(uint16_t port, uint8_t v);
void outpw(uint16_t port, uint16_t v);
uint8_t inp(uint16_t port);
void far delay(int16_t ms);

/* stubs.c, until each is read out - see that file for what they are */
int16_t far run_level(int16_t arg);
item_t far *far run_screen(menu_t far *menu, void far *a, int16_t b);
int16_t far egg_find_block(uint8_t type, uint8_t index, int16_t arg);
int16_t far egg_read_word(void far *s);
void far egg_table_alloc(int16_t n);        /* 0x05005 */
void far load_eggs_ini(const char far *path);  /* 0x13ccd */
extern char far *egg_ini_paths[5];
extern int16_t   egg_ini_count;             /* 0x21d0 */
int16_t far open_egg(char far *path);       /* 0x05044 */
extern int block_open;                      /* 0x20b6 */
uint8_t far egg_read_byte(void far *s);
int16_t far alloc_image(void far *d, int16_t a, int16_t b, int16_t c, int16_t e);
int16_t far load_demo(uint8_t index);
int16_t far pick_random_demo(void);
int16_t far detect_hardware(void);
void far show_readme_section(uint8_t n);
void far console_rule(void);
void far build_episode_index(void);
int16_t far read_index(episode_t far *array, int16_t start, int16_t far *total,
                       int16_t egg, int16_t store);
void far save_game_screen(void);
int16_t far load_game_screen(void);
void far write_word(int16_t v, FILE far *fp);
void far write_string(FILE far *fp, const char far *s);
void far add_save_slots(menu_t far *m, int16_t for_saving);
void far find_egg_by_id(const char far *id, const char far *name);
void far menus_resume(void);
void far save_note(int16_t serial);
int16_t far name_entry(char far *buf, int16_t row, int16_t escape);
void far register_screen(void);
void far check_registration(char far *name, char far *key, int16_t announce);
void far high_score_screen(void);
void far show_attract_screen(int16_t frames);
void far high_score_screen(void);
void far high_score_name(char far *buf);
void far score_set(int16_t score, char far *name, int16_t slot);
void far menus_after_game(void);
int16_t far load_settings(void);
extern score_t score_table[10];
void far egg_load_one(int16_t index, int16_t type, int16_t egg);
extern int16_t egg_file_count;
extern egg_file_t far *egg_files;

/* The font, and the two colours a glyph's pixel values select. In the original
 * both drawers reach them as text_colour[value - 1], which is why the byte the
 * screens set for the outline is d+0x54d - the same byte as font[0]'s width.
 * That costs nothing there, because a string ends at character 0 and font[0] is
 * never measured or drawn; here they are simply separate. */
extern glyph_t font[256];
extern uint8_t text_colour[2];

void far font_clear(void);
void far font_load(void);
int16_t far glyph_to_screen(uint8_t ch, int16_t x, int16_t y);
int16_t far glyph_to_image(desc_t far *desc, uint8_t ch, int16_t x, int16_t y);
int16_t far text_width(const char far *s);
void far draw_string(desc_t far *desc, const char far *s, int16_t x, int16_t y);
void far picker_cell(int16_t slot, int16_t number, desc_t far *page,
                     int16_t current, int16_t hard);   /* 0x10abc */
void far load_text_page(desc_t far *desc, uint8_t type, uint8_t index,
                        uint8_t colour_base, int16_t max_width, int16_t egg);
void far str_copy(const char far *s, char far **dest);
void far fatal(const char far *msg, const char far *arg);
extern char far *out_of_memory;
void far resource_release(void far *d);
void far set_buffer(void far *p);              /* 0x0b9ea, in game.c */
extern uint8_t default_buffer[768];
/* sound.c - the game's own sound module */
void far sound_play_guarded(int16_t id, int16_t voice);
void far sound_play(int16_t id, int16_t voice);
void far sound_play_loop(int16_t id, int16_t scale, int16_t egg);
int16_t far sound_load(uint8_t id, int16_t scale, int16_t egg);
void far sound_preload(uint8_t id, int16_t scale);   /* 0x1480f */
extern int16_t sound_keep_mark;                      /* 0x2909 */
int16_t far play_sample(sample_t *desc, int16_t id, int16_t loop);
void far stop_voice(int16_t slot);
void far stop_sound_by_id(int16_t id);
int16_t far is_sound_playing(int16_t id);
void far ambience_random(void);
void far release_sounds(void);
void far sound_init(int16_t rate);
void far sound_mix(int8_t *dst, int16_t frames);
extern uint8_t sound_state;

/* the backend's, like set_plane and page_flip: what is left of the DSP */
int16_t far audio_open(int16_t rate);
void far audio_close(void);
/* What the DSP time constant did: change the rate the samples are consumed at,
 * without touching the samples. See sound_set_rate. */
void far audio_set_rate(int16_t rate);
void far install_int23(void far *h);
int16_t far ctrl_break_handler(void);      /* 0x144cd - returns 1, "carry on" */
void far crt_exit(void);       /* 0:0x1e6b - Borland's exit; the backend's */
void far set_text_colour(int16_t c);           /* 0:0x1e94, in the backend */
void far retire_entity(void far *e);
void far f_0580b(void);
uint8_t egg_next_pixel(void);
char far *far egg_read_string(void far *s);
void far egg_fread(void far *buf, uint16_t size, uint16_t n);
void far egg_block_end(void);
int egg_open(const char *path);
void far hold_frames(int16_t n);   /* 0x04dcd */
void far level_free(void);
void far bonus_screen(void);   /* 0x0becb */
void far photo_fade_step(void);
void far episode_page(int16_t ordinal, int16_t egg);   /* 0x11bee */
extern char far *owner_name;
extern int16_t   owner_key;

extern int16_t particle_count;
extern particle_t far *particle_array;
extern table_t sprite_table;
extern int16_t far *anim_script[112];
extern uint8_t      type_flags[111];
extern int16_t far *tool_list;      /* 0x1782 */
extern int16_t      tool_type;      /* 0x1786 */
extern uint8_t      tool_at;        /* 0x1788 */
extern uint8_t      tool_count;     /* 0x178b */
int16_t far tool_list_has(int16_t type);
int16_t far tool_list_any_flagged(void);
extern int16_t      next_type[111];
extern uint8_t      walk_divider, walk_phase;
void far load_animations(void);
extern void far *egg_stream, *current_buffer;

/* game.c's, called from the video layer */
void far make_rect(viewport_t far *r, int16_t top, int16_t bottom,
                   int16_t left, int16_t right);
void far palette_build(void);
void far palette_set_black(uint8_t index);
extern uint8_t gamma_level;

/* run_level's leaves - game.c, at the bottom. Nothing calls them yet. */
extern int16_t level_w, level_h;         /* 0x1701, 0x1703 */
extern uint8_t scroll_shift;             /* 0x18f5 */
extern int16_t scroll_smooth;            /* 0x4fa */
extern uint8_t bg_drift;                 /* 0x202c */
extern scene_t scenes[6];                /* 0x0d63 */
extern int16_t level_clock;              /* 0x201a */
extern uint8_t far *tool_event_table;    /* 0x203b */
extern uint16_t    tool_event_count;     /* 0x2047 */
extern uint8_t far *event_table;         /* 0x203f */
extern uint16_t    event_count;          /* 0x2049 */
extern uint8_t far *script_table;        /* 0x2043 */
extern uint16_t    script_count;         /* 0x204b */
extern int16_t     script_at;            /* 0x204d */
int16_t far clock_seed(void);            /* 0x11013 */

void far scroll_axis_snap(int32_t focus, int32_t extent, int32_t far *pos,
                          int16_t span);
void far scroll_follow(int32_t x, int32_t y);
void far scene_keep_positions(scene_t far *s);
void far scene_swap_pair(void);
void far bg_scroll_reset(void);
void far palette_apply_gamma(void);
void far tool_events(void);
void far entity_copy(scene_t far *s, int16_t from, int16_t to);
int16_t far game_rand(void);
void far game_srand(uint16_t seed);
extern uint32_t rand_seed;               /* 0x3006 */
extern int16_t  particle_cap;            /* 0x18cf */
extern int16_t  duck_count;              /* 0x2007 */
extern uint8_t  particle_colours[8];     /* 0x18c5 */
void far particles_spawn(int16_t x, int16_t y, int16_t n);
void far duck_dies(entity_t far *e, int16_t force, int16_t noisy);
void far tool_use(int16_t x, int16_t y, int16_t type);   /* 0x07a36 */
void far entity_update(entity_t far *e, int16_t applying, int16_t scripted);
void far scene_update_all(scene_t far *s);           /* 0x0d715 */
void far tool_click_at(int16_t x, int16_t y);           /* 0x0d0c8 */
void far demo_events(void);                          /* 0x0d471 */
void far tool_selected(int16_t slot);                /* 0x0e088 */
extern uint8_t tool_prev, tool_announce;             /* 0x1789, 0x178a */
extern int16_t script_heading, flock_is_right;

/* stubs.c - stubbed until run_level(1) needs them; see the note there */
void far played_tool_events(int16_t far *fast);   /* 0x0cf07 */
void far pause_screen(void);                     /* 0x0ce2e */
void far sound_set_rate(int16_t rate);           /* 0x149e:0x346 */
void far kill_all_ducks(void);

/* 0x0993b, game.c - the collision pass */
extern int16_t score, quota_left, combo_hi, combo_lo;
extern int16_t lives;                    /* 0x2034 */
extern int32_t mouse_x, mouse_y;         /* 0x18d3, 0x18d7 */
extern int16_t teleport_to_x, teleport_to_y, eaten_countdown;
extern uint8_t anim_a[111];
void far collide_scenes(void);

/* 0x088fa, game.c - the level loader, and what it fills in */
extern uint8_t     sprite_set_id;        /* 0x2103 */
extern table_t     level_sprites;        /* 0x1fec */
extern uint8_t     solid_count;          /* 0x2031 */
extern solid_t far *solids;              /* 0x202d */
/* 0x201e - seven words, one per flag, read from the level's own block. They are
 * indices into one array and not seven variables: naming DGROUP bytes twice is
 * what left seven cheats reading words nothing ever wrote.
 *
 * What each one does, and how many of the eighty levels set it - counted by
 * loading every level and reading the array, not by guessing:
 *
 *   [0] LIGHTNING          3   blink_enable: the terrain ramp flashes to a washed
 *                              copy of itself and claps (sound 0x15). 16, 41, 55
 *   [1] SLIPPERY          15   an ordinary duck samples the ground two pixels
 *                              either side of its feet and gains momentum
 *                              downhill - 0x07f11, and `param` is the momentum
 *   [2] WARP               1   the background warp at 0x05df4. Level 80 only
 *   [3] SPARKLE            1   the sparkle column at 0x0e673. Level 71 only
 *   [4] RAMP_FROM_BG       1   copies palette entries 64-79 - the terrain ramp -
 *                              from default_buffer into the level palette. 55
 *   [5] PALETTE_FROM_BG   77   the same for entries 0-15 and 240-335. The normal
 *                              arrangement; 1, 55 and 80 are the exceptions
 *   [6] FAST_WARP          0   warp_step 7 instead of 1. No level in this egg
 *                              sets it, so that branch has never run
 *
 * [0] to [3] are named from the code that reads them. [4] and [5] are named for
 * what they copy rather than for what the author meant by them, and [6] for the
 * one line it changes. */
/* Tiles are 20x20 pixels, which is what turns the map's width and height into the
 * backdrop's: level_load reads the map in tiles and paints it a tile at a time.
 *
 * Only the loader's uses are this. The value 20 also appears in the image as a
 * sound id, an entity type, a score, several stack slots, screen_x0 and the twenty
 * spare rows the 360x240 mode has - all 0x14 and none of them a tile, so they stay
 * as they are. */
#define TILE_SIZE 20

/* The logical screen the game draws for, and it does NOT change with the
 * resolution: at 360x240 the play area is still 320x200, centred by screen_x0
 * with the spare rows given to the status panel. So these are the picture's size,
 * where screen_width and screen_height are the mode's.
 *
 * input_poll takes them as the clamp for the mouse position, which is why nearly
 * every call passes this pair - the exceptions pass a viewport's own size, or
 * 0x80 x 1 for a slider, and mean something else by it. */
/* One extra life per 5000 points, however the score arrives. next_life starts
 * here and steps up by the same amount each time it is passed, so the two uses at
 * 0x137ec and 0x138f5 are the same number for the same reason. */
#define NEXT_LIFE_AT_INC 5000

#define SCREEN_SIZE_X 320
#define SCREEN_SIZE_Y 200

/* Entity types identified by looking at the artwork in block 0x53 next to what
 * the code does with them. The pictures are the game's, the names are ours, and
 * each one below has a code site that agrees with the picture.
 *
 * The rocket is one object in six states: flying, and then showing how many
 * ducks are still to come. A duck that reaches any of the five is home - the
 * arm at 0x09bc9 takes all five together, as does the level loader at 0x08c04,
 * whose jump table has four identical entries. */
#define ENTITY_DUCK_CRASHING 0x03  /* the 7-frame fall, next_type 0 - it retires
                                    * itself. duck_dies sets it and returns
                                    * early if it is already set, so a duck
                                    * cannot die twice */
#define ENTITY_DUCK_IDLE     0x04  /* stood still, script loops on itself */
#define ENTITY_ROCKET_FLYING 0x05  /* sprites 27 and 55, the exhaust alternating */
#define ENTITY_ROCKET_1      0x06  /* one duck still to come */
#define ENTITY_ROCKET_2      0x07
#define ENTITY_ROCKET_3      0x08
#define ENTITY_ROCKET_4      0x09
#define ENTITY_ROCKET_5      0x0a

/* The menu pointer: script 0,0,0,0,1,1,1,1 with next_type itself, so it blinks
 * between sprites 0 and 1 forever. Set at 0x0cbe4 on cursor_scene's one entity.
 * Not to be confused with types 1 and 2, which draw_entities calls "the two
 * cursors" - those are the in-level pen, and their sprite is computed rather
 * than scripted. */
#define ENTITY_CURSOR        0x14

/* The pointer while a tool is in progress: one frame, sprite 63, and the branch
 * that sets it is also the branch that refuses input and puts tool_at back. So
 * it is not decoration - it is the picture of the game not listening. */
#define ENTITY_CURSOR_INACTIVE 0x16

/* The cursor while a MIRRORED tool is held (type_flags bit 1) - the ones that
 * go into scene 5 as a pair. It shows an arrow, and the arrow points at the
 * flock: the type is flock_is_right + ENTITY_ARROW_LEFT, and flock_is_right is `avg_x > mouse_x`,
 * so the flock being left of the cursor gives the left arrow. Kept as an
 * addition rather than a ternary because that is what the original computes. */
#define ENTITY_ARROW_LEFT      0x2a
#define ENTITY_ARROW_RIGHT     0x2b

/* The two states of one hazard, alternating. scene_swap_pair walks scene 2 and
 * turns every gap into a spark and every spark into a gap, so the whole level's
 * set flips together rather than each keeping its own timer - and the flip is
 * driven by type 0x2f reaching the end of its script (0x0a632), not by a clock.
 *
 * Touching a live one is fatal: the arm at 0x09eda plays sound 0x0f, turns the
 * duck into 0x35 with vy = -5, and takes one off duck_count - unless
 * cheat_state[2] is set, which is the invulnerability cheat. The gap is
 * harmless; it has no collision arm at all. */
#define ENTITY_SPARK_GAP       0x2c
#define ENTITY_SPARK           0x2d

/* The switch being thrown: two frames, and reaching the end of them is what
 * calls scene_swap_pair and flips every spark on the level at once.
 *
 * It is the middle of a one-way chain: ON -> FLIPPING -> OFF. The arm at
 * 0x09b87 only takes the ON one if `d->type == 1` - the LEADER alone can throw
 * it, for sound 0x0d and 20 points - and next_type then carries it to OFF,
 * whose next_type is itself, so the switch is spent and cannot be thrown twice.
 *
 * The two rest states look their parts: ON animates (140, 245, 246, 140, 140),
 * OFF is the single frame 143.
 *
 * OFF has no code site anywhere in the image: nothing sets it, nothing tests
 * it, and it is only ever arrived at through next_type. That absence is not a
 * gap in the reading - a spent switch is INERT, and having no arm is exactly
 * how the game says so. Its name therefore rests on the egg's chain and the
 * picture rather than on a line of C, which is a weaker footing than ON has and
 * is recorded as such. */
#define ENTITY_SPARK_SWITCH_ON       0x2e
#define ENTITY_SPARK_SWITCH_FLIPPING 0x2f
#define ENTITY_SPARK_SWITCH_OFF      0x30

/* What a duck becomes when a live spark gets it, set at 0x09eda with vy = -5
 * so it is thrown upward. The script is the electrocution: sprite 103 - the
 * ordinary duck - alternating with 161 three times, then 162..167 and 208, with
 * next_type 0, so it retires itself when the flicker is over. */
#define ENTITY_DUCK_ZAPPED   0x35

/* The "i" sign on the level information screen - the SECOND pre-level screen,
 * which only appears when the level has a text page. run_level's other screens
 * never place it: the one scene_add is inside `if (has_page)` at 0x1131f. */
#define ENTITY_INFO_SIGN     0x34

/* The alien prowling: sprites 181..184, next_type itself, so it loops until
 *
 * ALIEN, not monster, and that is the game's word rather than ours:
 * menu_text[45] is "Alien killed! 25 bonus points!", posted at 0x08665 next to
 * the `score += 0x19` that is those 25 points. Everything here was named for a
 * guess until 2026-08-13; docs/notes/the-alien.md keeps its filename, since
 * that is where the frame trace lives and links point at it.
 *
 * something else moves it on. It is the state everything else about the alien
 * returns to - 0x46 and 0x47, the two facings of the eleven-frame eat, both
 * have next_type 0x39, as does 0x4b.
 *
 * type_flags bit 2 is set, so its RIGHT-facing artwork is the mirror slot 0x3a
 * (sprites 226..229) and no entity is ever set to that; 181..184, this type's
 * own, are the alien facing left. cutscene_night_alien
 * places one directly - scene_add(..., 0x39, 5) at 0x1016a - which is the one
 * place the alien appears outside a level. See docs/notes/the-alien.md,
 * where the frame trace showed it entering and leaving this state. */
#define ENTITY_ALIEN_WALKING 0x39

/* The two facings of the eleven-frame eat, both with next_type back to
 * ENTITY_ALIEN_WALKING. Which one is chosen is the object's own facing:
 * 0x46 + (o->vx == 1) at 0x09e64, so vx == 1 - travelling right - gives the
 * RIGHT one. The duck is set to type 0 outright; there is no dying animation,
 * which is what makes this different from every other way a duck is lost. */
#define ENTITY_ALIEN_EATING_LEFT  0x46
#define ENTITY_ALIEN_EATING_RIGHT 0x47

/* The springs, two of each because a spring throws one way only. The resting
 * one is a single frame; touching it swaps it for the four-frame launch, whose
 * next_type drops it back to rest. The throw itself is one ternary per side:
 *     0x0a12f  LEFT  -> becomes 0x3e, duck to LEADER/DUCK_SOMERSAULT_LEFT
 *     0x0a243  RIGHT -> becomes 0x3f, duck to LEADER/DUCK_SOMERSAULT_RIGHT
 * and both set vy = -7 and clear fall, which is the fall counter - so the drop
 * is measured from the spring and not from wherever the duck came in. */
#define ENTITY_SPRING_LEFT            0x3c
#define ENTITY_SPRING_RIGHT           0x3d
#define ENTITY_SPRING_LEFT_LAUNCHING  0x3e
#define ENTITY_SPRING_RIGHT_LAUNCHING 0x3f

/* The fan, and it does not merely kill: the arm at 0x0a07c retires the duck and
 * puts a 0x43 into scene 1 ten pixels above it, with a random upward vy and a
 * random facing, so what comes out is thrown debris. The level loader gives a
 * fan its own arm at 0x08bbd, which is the only thing that sets wide_scene1 -
 * scene 1 has to be bigger on a level that has one, because of the bits. */
#define ENTITY_FAN                    0x42

/* What the fan throws: the duck's head, spinning. Four frames on a loop, and
 * type_flags bit 0 - the only one of the three head types to have it.
 *
 * It spawns particles every frame it exists (0x080a3), so it trails as it
 * flies, and it bounces: the arm at 0x084ea gives it a fresh upward vy on any
 * landing harder than 8, clamped to -8, and puts it back to spinning. Only a
 * soft landing lets it settle, and which way it ends up facing is a coin toss:
 * `(game_rand() & 1) + ENTITY_DUCK_HEAD_LEFT`, both single frames. Sprite 199
 * is both this one's first frame and the whole of the left one, which is how
 * the same head stays recognisable across all three.
 *
 * A settled head is not finished: it is in the tumble arm too, so kicking it
 * hard enough sends it back to spinning. */
#define ENTITY_DUCK_HEAD_SPINNING     0x43
#define ENTITY_DUCK_HEAD_LEFT         0x44
#define ENTITY_DUCK_HEAD_RIGHT        0x45

/* The hot air balloon, which is NOT TOOL_BALLOON. The tool's 0x36 is the small
 * red one that goes off on impact; this is the big one with a basket, and it
 * carries a duck away.
 *
 * Idle is the single frame 223, and it waits. The arm at 0x09c72 fires when an
 * ordinary duck reaches it - `if (d->type == 1) break`, so the LEADER cannot
 * board, the exact opposite of the spark switch, which only the leader can
 * throw. On boarding: sound 0x1f, the balloon takes the six-frame flying
 * script, and the duck becomes 0x4a with param 0, vy = -4 and its y snapped to
 * the balloon's, which is what puts passenger and basket in the same place. */
#define ENTITY_HOT_AIR_BALLOON_IDLE   0x48
#define ENTITY_HOT_AIR_BALLOON_FLYING 0x49

/* The passenger. No level places one - the only way in is boarding, at 0x09c72
 * - and the arm at 0x07c80 is the whole ride:
 *     e->y -= 1                    a pixel a frame, always upward
 *     y == 0                       out of the top, and it becomes an ordinary
 *                                  duck (type 2) wherever it got to
 *     terrain_at(y, x) != 0        passing through something solid: param = 1
 *     param already set, now clear  it has come out the far side, so let go
 * That middle pair is what lets a ceiling be passed through rather than
 * blocking: param remembers having been inside solid ground, and leaving it is
 * what ends the ride. `return` rather than `break`, so none of the ordinary
 * movement below applies to it. */
#define ENTITY_DUCK_IN_HOT_AIR_BALLOON 0x4a

/* A stomping machine, and it is a four-state CYCLE that re-arms itself:
 *     0x22  armed, one frame, looping - what levels place (16 across 13)
 *     0x23  sprung: y -= 10, one jump upward, then next_type to 0x25
 *     0x25  counts counter to 0x20 - about half a second of staying down
 *     0x26  rises, counter += 2 a frame, and draw_entities draws it y - counter so the
 *           climb is in the drawing rather than the position; at counter >= 0xa it
 *           becomes 0x22 again and is ready for the next duck
 * so it is not spent like the spark switch - it resets.
 *
 * The duck it catches becomes ENTITY_DUCK_CRUSHED, whose four frames end in
 * duck_dies at
 * 0x0a6f0. The port labelled that arm "Drowned" for a long time, which was a
 * guess made before anything here had a name and is simply wrong - the duck is
 * crushed, not drowned. Corrected at the site. */
#define ENTITY_STOMPER         0x22
#define ENTITY_STOMPER_SPRUNG  0x23   /* fires: y -= 10, once */
#define ENTITY_STOMPER_DOWN    0x25   /* holds while counter counts to 0x20 */
#define ENTITY_STOMPER_RISING  0x26   /* counter += 2 until 0xa, then armed again */

/* The duck the stomper catches. Four frames and then duck_dies, and next_type
 * is 2 - which only matters under cheat_state[2], where duck_dies does nothing
 * and the duck has to be given somewhere to go; that is what `chain` is doing
 * in the arm at 0x0a6f0. Without the cheat the type is already
 * ENTITY_DUCK_CRASHING by then and the default must not run. */
#define ENTITY_DUCK_CRUSHED    0x24

/* Nothing. entity_set_type(e, ENTITY_NONE) is how anything is removed, and the
 * retire pass then drops the record. Its script is sprite 37, which is 0x0 with
 * no pixels at all - so "retired" is drawn, not skipped, and costs one
 * zero-sized blit. */
#define ENTITY_NONE            0x00

/* THE DUCKS. Neither is ever a level record - the loader places
 * ENTITY_DUCK_IDLE and promotes one to ENTITY_LEADER (0x08d7d, "which duck is
 * the hero") - but between them they are what the whole game is about, and they
 * appear in every level there is.
 *
 * Both have an EMPTY script in block 0x47, because their sprite is computed
 * rather than looked up. draw_entities at 0x0acb7:
 *
 *     index = (2 - e->type) * 12 + 6 - e->vx * 4 + walk_phase
 *
 * which is twelve sprites per type - three facings of four frames - with
 * `(2 - type) * 12` choosing the set: 2..13 for the duck, 14..25 for the
 * leader. Rendered, the first set is an orange duck in a green cap and the
 * second the same walk in green with a red one, which is what makes the leader
 * the green one and TOOL_PICK_LEADER's icon a green duck.
 *
 * The cheat_state[6] branch is LEFT HANDED flipping `- e->vx * 4` to `+`, the
 * same mirroring the type_flags bit 2 types get from their extra slot. */
#define ENTITY_LEADER          0x01
#define ENTITY_DUCK            0x02

/* The explosion. The BDG9000 turns whatever it cashes in into this, and its
 * first sprite is also blast_terrain's stencil - anim_script[ENTITY_EXPLOSION][0]
 * is the shape of the hole knocked out of the terrain, so the flash and the
 * damage are the same picture. next_type 0, so it clears itself. */
#define ENTITY_EXPLOSION       0x17

/* The two touch-and-die hazards. The arm at 0x09e07 is one line, duck_dies,
 * and both share it - so all that separates them is the picture and the reach
 * anim_a gives each: 3 for the spike, 7 for the flame, so the flame catches a
 * duck from more than twice as far away.
 *
 * Levels place 77 of these across 34 of them, which makes it the commonest
 * object in the game - as a plain hazard should be. (An earlier guess here had
 * it as the duck source on the strength of that count alone. Ducks come from
 * 0x4f, which is what sets spare_ducks.) */
#define ENTITY_SPIKE           0x0b
#define ENTITY_FLAME           0x58

/* A rotating alarm light. Four frames on a loop with the glow travelling round
 * the dome, which is the rotation; sitting next to ENTITY_FLAME in the numbering
 * had it read as a brazier here until 2026-08-13.
 *
 * It has no arm anywhere in the image - no collision, no movement, no entry in
 * either jump table - and needs none: it is scenery that turns. Five of them,
 * in levels 45 and 73, which makes it the only one of the last few types that
 * appears in the game proper rather than in a secret level. */
#define ENTITY_ALARM_LIGHT     0x57

/* A bottle a duck picks up, 3x7 pixels, and the floating "10" it turns into.
 * The arm at 0x09f80 is score += 10, entity_set_type(o, the popup), sound 0x2c
 * - so the popup's artwork, which is the characters "10", is literally the ten
 * points just scored.
 *
 * Both were missing from the port until 2026-08-13, and each hid the other: the
 * collision arm was skipped in transcription, and because nothing then set the
 * popup, the popup looked like unreachable artwork. The jump table at cs:0x56bb
 * is what found it - it has an entry per type for 0x39..0x59, and 0x59's was
 * the one target the port had no arm for.
 *
 * Both belong to a SECRET LEVEL and are seen nowhere else. Only level 203
 * places bottles - 23 of them, and none of 1..80 has a single one - and 203 is
 * reached from level 52, whose next_level names it. So the route to seeing
 * either is: find the invisible ENTITY_SECRET in level 52 with the leader.
 *
 * That is also why both went missing for so long. Neither can appear in normal
 * play, so no amount of playing the eighty levels would have shown up the
 * absent arm. */
/* The rocket the PLAYER flies, in a secret level, to catch falling ducks. It
 * steers itself at the cursor - `vx = (mouse_x + 4 - e->x) >> 3` at 0x07d94,
 * one step per eight pixels of error - and 0x51 shares that arm, so the girder
 * in level 202 is driven the same way.
 *
 * Catching a duck is scored as getting one home: the arm at 0x0a2dc is the
 * rocket's own arm at 0x09bc9 with the counting taken out - duck_count--,
 * quota_left--, the duck retired to y = -40, carry_bonus() and the same
 * combo_lo. It has no param to decrement and no leader test, and the sound is
 * 0x14 rather than 4.
 *
 * The level loader gives it its own arm, and it is the only thing that sets
 * spare_ducks - 0x10, half of which is how many ducks get scattered. So this
 * type is where a catching level's falling ducks come from as well as what
 * catches them. Only level 200 has one. */
#define ENTITY_CATCHER_ROCKET  0x4f

/* ARKANOID, in a secret level. The paddle is flown with the cursor - it shares
 * ENTITY_CATCHER_ROCKET's steering arm at 0x07d94 - and the ball is a duck.
 *
 * The loader builds the game when scene 2's first entity is a paddle (0x08d19):
 * it serves one ball at x 0xa0, y 0x64 with a random direction, adds a duck to
 * the count, and gives scene 0 eight extra slots.
 *
 * The bounce is at 0x09a98 and is the real thing - the angle comes from where
 * on the paddle it lands: `vx = ((d->x - o->x) >> 3) + 1`, one step per eight
 * pixels off centre, signed by which side. A duck that touches the paddle also
 * becomes the ball, so the served one is not special.
 *
 * The bricks are the TERRAIN. Every time the ball comes to rest it calls
 * tool_use(x, y, TOOL_BOMB) and scores 5 - so it blasts a hole where it lands
 * and the level is eaten away. The two ball types are one object in two spins,
 * each landing turning it into the other (0x081a7 and 0x084be), and 0x52 is
 * also TOOL_EXTRA_DUCK, which is the same duck the tool places in an ordinary
 * level. */
#define ENTITY_PADDLE          0x51
#define ENTITY_DUCK_BALL       0x53

#define ENTITY_BOTTLE          0x59
#define ENTITY_SCORE_POPUP     0x5a

/* A seagull knocked out of the air by the detonator, and what it becomes on
 * landing: the arm at 0x0848b plays sound 0x10 on voice 3, spawns particles and
 * moves it on. The falling one loops; the landed one runs five frames and
 * retires. */
#define ENTITY_SEAGULL_FALLING 0x31
#define ENTITY_SEAGULL_SPLAT   0x32

/* The alien's one-shot after a landing harder than 8 (sound 0x18 at 0x0845e).
 * Mouth wide open, three frames, and next_type is ENTITY_ALIEN_WALKING, so it
 * always returns to prowling. Its right-facing artwork is the mirror slot
 * 0x4c. */
#define ENTITY_ALIEN_LANDING 0x4b

/* The secret, and it is INVISIBLE: its only frame is sprite 37, the same 0x0
 * empty one ENTITY_NONE uses. Only the LEADER can find it - `d->type == 1` at
 * 0x09e27 - for sound 0x2a, and the duck then becomes the second type here,
 * whose script ending wins the level outright (level_outcome = 1) by a
 * different route from filling the rocket.
 *
 * secret_from_level remembers which level it was found in, and the loader will
 * not place another there - `cheat_state[9] || level_attempted !=
 * secret_from_level` - which is what the YOUINTSEENME cheat overrides. An
 * invisible object named "you ain't seen me" is the joke.
 *
 * Where it leads is run_level's tail: the level's own next_level byte, so
 * 19, 46, 71 and 52 open 200..203, while 29 and 62 show a page instead. */
#define ENTITY_SECRET             0x4d
#define ENTITY_DUCK_FOUND_SECRET  0x4e

/* The extra spinning duck arriving, placed by TOOL_EXTRA_DUCK at a fixed y of
 * 0x64. It shares its whole script with ENTITY_TELEPORT_IN - the same
 * materialise - and next_type carries it to 0x53. */
#define ENTITY_EXTRA_DUCK_ARRIVING 0x54

/* An alien DANCING, and its hi-fi. The pair is the joke: level 203 places the
 * hi-fi at (375,199) and the alien at (386,199), eleven pixels apart on the
 * same ground line, and it is the only place either appears.
 *
 * The dance is the walk with the walking taken away. 0x55's script is
 * byte-identical to ENTITY_ALIEN_WALKING's - the same sprites 181..184 - and
 * all that differs is the flags: no bit 0, so entity_update's default movement
 * never applies and it stays where it is putting one foot in front of the
 * other; and no bit 2, so it never reaches the right-facing mirror slot and
 * always faces left, towards the hi-fi.
 *
 * Neither has an arm anywhere in the image, and neither needs one. */
#define ENTITY_ALIEN_DANCING 0x55
#define ENTITY_HIFI            0x56

/* The teleporter, which is a PAIR OF RECORDS and not one entity. The arm at
 * 0x09fae reads o[1].x and o[1].y - the entity record *after* the entry - as
 * where the duck comes out, so the exit is found by position in the level file
 * rather than by any link between them.
 *
 * That is a contract the levels keep: all 23 entries in the egg are immediately
 * followed by an exit, with no exceptions across the 84 level blocks, and the
 * two are placed 23 times each in the same 17 levels. Reordering those records
 * would silently send ducks to whatever happened to be next.
 *
 * Touching the exit does nothing unless it is the LEADER (`d->type == 1`), and
 * then it plays its own arrival animation. */
#define ENTITY_TELEPORTER_ENTRY       0x37
#define ENTITY_TELEPORTER_EXIT        0x38
#define ENTITY_TELEPORTER_EXIT_ACTIVE 0x3b

/* A duck riding inside a bubble - NOT a balloon. Three different things in this
 * game carry or resemble one, and the names have to keep them apart:
 *   TOOL_BALLOON 0x36            the small red one, goes off on impact
 *   ENTITY_HOT_AIR_BALLOON_IDLE  the big one with a basket, carries a duck away
 *   this pair                    a bubble a duck gets into, which drifts and
 *                                then pops open where it lands
 *
 * The code has its whole life:
 *   0x09d51  a moving duck that is not the leader touches an empty one (0x1f):
 *            the duck is retired and the balloon becomes this, inheriting the
 *            duck's facing and drifting
 *   0x07d64  it moves each frame, and attempt_over set kills the passenger
 *   0x08160  when it comes to a stop it "lands and hatches" - sound 1, the
 *            balloon retires, and a fresh duck of type 2 is added where it sat
 * It is in the list at 0x085f8 that costs a duck for leaving the level, which
 * is right: there is a duck inside it. */
#define ENTITY_DUCK_IN_BUBBLE 0x1e

/* The empty one, waiting to be got into: the arm at 0x09d51 fires when a moving
 * duck that is not the leader touches it. Levels place it 18 times across 10 of
 * them; the occupied one is never placed, because it is a state. */
#define ENTITY_BUBBLE_EMPTY   0x1f

/* A somersaulting leader, thrown rightward. Two arms describe it and they agree:
 * at 0x07e47 it sets its own facing positive while in the air, and at 0x083f0 it
 * lands - dying if it fell more than 0x32, and otherwise becoming type 1, the
 * leader, which is what makes this the LEADER's somersault and not a duck's.
 *
 * One of four, and the spring that throws them proves the grid rather than
 * leaving it to be inferred from the landing:
 *     0x0a243  the right-hand spring: d->type == 1 ? 0x1c : 0x40
 *     0x0a12f  the left-hand spring:  d->type == 1 ? 0x33 : 0x41
 * so the leader/duck split and the left/right split are each written once, in
 * one ternary. Landing agrees: 0x1c and 0x33 come back as type 1, 0x40 and 0x41
 * as type 2, and any of them dies on a fall of more than 0x32.
 *
 * The dispatch for 0x40..0x53 is a jump table at cs:0x3a9a - `sub bx, 0x40` -
 * not the compare chain, which is why 0x41's landing arm sits apart from the
 * other three. */
#define ENTITY_LEADER_SOMERSAULT_RIGHT 0x1c
#define ENTITY_LEADER_SOMERSAULT_LEFT  0x33
#define ENTITY_DUCK_SOMERSAULT_RIGHT   0x40
#define ENTITY_DUCK_SOMERSAULT_LEFT    0x41

/* A duck going into the rocket - but the type belongs to the ROCKET, not to the
 * duck. At 0x09bc9 the duck is retired outright (type 0, y = -40) and it is the
 * rocket that becomes 0x1a, with param-- for the one it just took.
 *
 * 0x1a bobs the rocket upward (vy = -4) for two frames and next_type walks it
 * to 0x1b, which decides what the rocket is now:
 *     param > 0   back to ENTITY_ROCKET_FLYING + param, so the picture shows
 *                 the new number - which is what makes ENTITY_ROCKET_N mean
 *                 "param == N" rather than merely looking like it
 *     param == 0  ENTITY_ROCKET_FLYING, "Rocket launched!", and if it was the
 *                 last on the level, outcome 1 - the win */
#define ENTITY_ROCKET_DUCK_ENTERING 0x1a
#define ENTITY_ROCKET_DUCK_ENTERED  0x1b

/* The selection brackets. highlight_nearest with `mark` set puts scene 3's one
 * entity at whatever the pointer is nearest and makes it this, so it marks the
 * hovered entity for whichever tool is in hand - not for the leader picker
 * alone. scenes[3].count is how draw_entities knows whether to draw it. */
#define ENTITY_HIGHLIGHT     0x11

/* The box behind the tool in the pen, which is tool_scene entity 0 - entity 1
 * is the tool's own icon sitting on top of it. Selected is the one with the lit
 * border; the plain one is sprite 42, the empty slot.
 *
 * draw_entities keys its halo off them: an entity of either type sets
 * halo_next, so the entity drawn AFTER the box gets outlined. That is how the
 * icon is outlined without the icon's own type having to know about it. */
#define ENTITY_TOOL_SLOT          0x0f
#define ENTITY_TOOL_SLOT_SELECTED 0x10

/* Two entity types that are a duck's state rather than an object, so they carry
 * no tool name and no level ever lists them. They are the two halves of one
 * move, and the animation records say so: 0x20 is sprites 103,104,105,106 with
 * next_type 0x21, and 0x21 is 111,110,109,108,103 - the same sequence run
 * backwards onto the ordinary duck sprite - with next_type 1, the leader.
 *
 * Only when the script runs out does entity_update's 0x20 arm move the duck to
 * teleport_to_x/y and zero its momentum, which is why the jump lands on the
 * frame the duck has finished vanishing and not on the frame it was clicked.
 *
 * Both are still ducks while in flight: they are in the list at 0x085f8 that
 * costs a duck for leaving the level. 0x21 has no arm of its own - it falls to
 * the default and becomes the leader again. */
#define ENTITY_TELEPORT_OUT 0x20
#define ENTITY_TELEPORT_IN  0x21

/* The tools, and these names are not ours: they are the game's own strings, out
 * of string table 0xff - block 0x48 index 0xff - which message_post prints as
 * "You have selected the %s". The table is indexed by anim_c[type] rather than
 * by the type, so the mapping below is what load_animations read out of block
 * 0x47 paired with what load_string_tables read out of 0x48.
 *
 * These are ENTITY types, not a namespace of their own. A tool is an entity type
 * that a level put in tool_list, and the same value means the same object
 * wherever it appears - the saucer the detonator destroys is a 0x13 whether it
 * arrived from the tool or from the level. So the TOOL_ prefix says how the
 * value is usually read here, not that it is a different kind of number, and
 * entity types with no tool are still bare: there are 91 animation types and
 * only these fourteen have a name.
 *
 * The order is the type's, which is the order they are listed in above. */
#define TOOL_DIAGONAL_BRIDGE   0x0c   /* "Diagonal bridge" */
#define TOOL_TELEPORT          0x0d   /* "Teleport current leader" */
#define TOOL_BRICK             0x0e   /* "Brick" */
#define TOOL_PICK_LEADER       0x12   /* "Pick new leader" */
#define TOOL_SAUCER            0x13   /* "Flying saucer" */
#define TOOL_DETONATOR         0x15   /* "Detonator" */
#define TOOL_BOMB              0x18   /* "Bomb" */
#define TOOL_HORIZONTAL_BRIDGE 0x19   /* "Horizontal bridge" */
#define TOOL_STOP_SIGN         0x1d   /* "Stop sign" */
#define TOOL_TREE              0x27   /* "Tree" */
#define TOOL_SEAGULL           0x28   /* "Seagull"; 0x29 is its mirror, below */
#define TOOL_BALLOON           0x36   /* "Balloon" */
#define TOOL_BDG9000           0x50   /* "BDG9000" */
#define TOOL_EXTRA_DUCK        0x52   /* "Extra spinning duck" */

/* THREE TYPE NUMBERS THAT ARE NOT TYPES. Where type_flags has bit 2, the
 * facing-RIGHT artwork lives in the NEXT slot, and draw_entities reaches it as
 * anim_script[type + mirror] - so the slot holds a script but no entity is ever
 * set to it. The direction is worth getting right: mirror is `vx == 1`, and
 * vx == 1 is travelling right, so the type's OWN script is the left-facing one
 * and the extra slot is the right. cutscene_night_alien settles it - it sets
 * vx = -1 with the comment "walking left" and the alien draws from 0x39's
 * own sprites. Exactly three types do this, and nothing in the image assigns any
 * of the three companions:
 *     0x28 TOOL_SEAGULL  -> 0x29
 *     0x39               -> 0x3a
 *     0x4b               -> 0x4c
 * They are worth knowing precisely because the sheet shows them as types with
 * their own picture, which invites a name they must not be given. The saucer is
 * NOT one of them: type_flags[0x13] is 0x03, bit 2 clear, and 0x14 is the
 * cursor - a single script, drawn the same way whichever way it travels. */

enum {
    LEVEL_LIGHTNING = 0, LEVEL_SLIPPERY = 1, LEVEL_WARP = 2, LEVEL_SPARKLE = 3,
    LEVEL_RAMP_FROM_BG = 4, LEVEL_PALETTE_FROM_BG = 5, LEVEL_FAST_WARP = 6
};
extern int16_t     level_flags[7];       /* 0x201e */
extern uint8_t     scenery_count;        /* 0x2000 - a byte */
extern int16_t     level_outcome;        /* 0x200d - 1 = won, and run_level
                                          * turns that into the 2 it returns */
extern int16_t     secret_from_level, secret_found;
extern int16_t     in_secret_level;
extern int16_t     teleport_to_x, teleport_to_y;  /* 0x1ff2, 0x1ff4 */
extern int16_t     timer_period;         /* 0x2001 */
extern uint8_t     next_level;           /* 0x2102 */
extern char far   *level_text;           /* 0x200f */
extern uint8_t     ambience_on;          /* 0x2015 */
extern int16_t     pair_slots;           /* 0x18d1 */
extern float       level_frac[4];        /* 0x13e1, 0x13e5, 0x13e9, 0x13ed - the
                                          * level's four 1/256ths. Written here,
                                          * read nowhere yet. The stream order is
                                          * [3], [0], [1], [2] */
void far level_load(void);
void far level_palette_build(void);   /* 0x0d5c5 */
int16_t far episode_for_level(void);  /* 0x10ba4 */
void far level_map_draw(desc_t far *dest);  /* 0x0b284 */
void far episode_intro(void);   /* 0x1089b */
int16_t far level_screens(int16_t level_completed);  /* 0x1102a */
void far banner_build(desc_t far *dest, const char far *text, uint8_t colour,
                      int16_t top);   /* 0x103e2 */
void far image_overlay(desc_t far *src, desc_t far *dst, int16_t row);  /* 0x1081c */
void far draw_level_status(desc_t far *dest);  /* 0x0b739 */
extern uint8_t level_palette[768];   /* 0x0de1 */
void far stamp_solid(solid_t far *o, desc_t far *dest);
void far blast_terrain(int16_t x, int16_t y, int16_t index);   /* 0x0751b */
void far ground_check(int16_t far *x, int16_t y);              /* 0x0799c */
void far stamp_sprite_into(int16_t x, int16_t y, sprite_t far *sp,
                           desc_t far *dest);                  /* 0x0739c */
/* One end of a growing bridge: 0x07646 takes a pointer to one of these. */
typedef struct {
    int16_t x, y;               /* +0x00, +0x02 */
    int16_t alive;              /* +0x04 - cleared when it hits something */
} bridge_end;

extern int16_t    tool_in_use;           /* 0x1fd6 */
extern bridge_end bridge_left;           /* 0x1fe0 */
extern bridge_end bridge_right;          /* 0x1fe6 */
extern int16_t    bridge_span;           /* 0x1fdc */
extern int16_t    bridge_drop;           /* 0x1fde */
extern int16_t    bridge_left_live;      /* 0x20fb */
extern int16_t    bridge_right_live;     /* 0x20fd */
void far tool_step(void);                /* 0x078a6 */

/* 0x0a410, game.c - the three-slot message ticker */
extern desc_t far  *message_image[3];    /* 0x210c */
extern viewport_t   message_rect[3];     /* 0x2118 */
extern uint8_t      message_time[3];     /* 0x2154 */
void far message_post(const char far *fmt, const char far *arg);

#endif /* DUCKS_GAME_H */
