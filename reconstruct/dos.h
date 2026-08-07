/* dos.h - the types and the I/O interface both backends implement.
 *
 * dos_io.c is the original, reconstructed: VGA ports, Mode X planes, INT 33h.
 * sdl_io.c is the same interface on SDL3. game.c does not know which it is
 * linked against, which is the point of having split them.
 *
 * `far` is what the 16-bit original said; on anything modern it is nothing.
 */
#ifndef DUCKS_DOS_H
#define DUCKS_DOS_H

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
    int8_t  f14;                /* +0x14 - which way the entity faces: every
                                 *         read of it is a byte load and a cbw,
                                 *         and draw_entities tests it for < 0 */
    uint8_t f15, f16;           /* +0x15 - scene_add zeroes these with it */
    int16_t param;              /* +0x17 - scene_add's last argument */
    uint8_t f19, f1a;           /* +0x19 - type 2 reads both: whether it
                                 * walks, and how far behind to follow */
    struct entity_s far *lead;  /* +0x1b - which entity type 2 follows */
    int16_t frame;              /* +0x1f - animate_scene's step, zeroed when
                                 *         the type changes */
    int16_t f21, f23;           /* +0x21 */
    int16_t type;               /* +0x25 - a word, not a byte */
    int16_t f27;                /* +0x27 */
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
    uint8_t f0d;                /* +0x0d - (rand & 1) + 1 */
    int16_t f0e;                /* +0x0e - 1 when spawned */
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
    int16_t   unread_e;         /* +0x0e */
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
extern uint16_t   page_front, page_back;
extern int16_t    flip_phase;

/* game.c's, which the video reads */
extern uint8_t    game_speed;
extern int16_t    last_key;      /* 0x18f6 - what init spins on */
extern int16_t    g_18e5;        /* 0x18e5 - any button; escapes the fades */
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
void far cutscene_night_monster(void);
void far cutscene_welcome_home(void);
void far cutscene_photos(void);
void far draw_number(int16_t value, int16_t x, int16_t y, viewport_t far *clip,
                     int16_t flags, int16_t digits);
void far draw_number2(int16_t value, int16_t digits, int16_t x, int16_t y);
void far particles(void);
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
extern int16_t cheat_flag;      /* 0x0515 - set when a cheat word is typed */
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
void far resource_load_at(desc_t far *desc, uint8_t type, uint8_t index,
                          int16_t pal_at, int16_t row, int16_t egg);
void far entity_set_type(entity_t far *e, int16_t type);
void far animate_scene(scene_t far *scene);
void far scene_alloc(scene_t far *s, int16_t capacity);
void far scene_retire(scene_t far *s);              /* 0x0981b */
void far scene_pick_nearest(scene_t far *s, int16_t mark);  /* 0x0af95 */
void far level_update(void);                        /* 0x0d4fc */
extern int16_t       picked_index;                  /* 0x18f3 */
extern entity_t far *picked;                        /* 0x18ef */
extern int16_t       g_217d;                        /* 0x217d */
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
uint8_t far egg_read_byte(void far *s);
int16_t far alloc_image(void far *d, int16_t a, int16_t b, int16_t c, int16_t e);
int16_t far load_demo(uint8_t index);
int16_t far pick_random_demo(void);
int16_t far detect_hardware(void);
int16_t far f_14e88(void far *fp);
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
void far load_text_page(desc_t far *desc, uint8_t type, uint8_t index,
                        uint8_t colour_base, int16_t max_width, int16_t egg);
void far str_copy(const char far *s, char far **dest);
void far fatal(const char far *msg, const char far *arg);
extern char far *out_of_memory;
void far resource_release(void far *d);
void far set_buffer(void far *p);
void far buffer_init(void);
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
void far release_sounds(void);
void far sound_init(int16_t rate);
void far sound_mix(int8_t *dst, int16_t frames);
extern uint8_t sound_state;

/* the backend's, like set_plane and page_flip: what is left of the DSP */
int16_t far audio_open(int16_t rate);
void far audio_close(void);
void far install_int23(void far *h);
void far ctrl_break_handler(void);
void far crt_exit(void);       /* 0:0x1e6b - Borland's exit; the backend's */
void far set_text_colour(int16_t c);
void far retire_entity(void far *e);
void far f_0580b(void);
uint8_t egg_next_pixel(void);
char far *far egg_read_string(void far *s);
void far egg_fread(void far *buf, int16_t size, int16_t n);
void far egg_block_end(void);
int egg_open(const char *path);
void egg_bringup_open(void);
void far f_04dcd(int16_t n);
void far f_0615a(int16_t a, int16_t b, void far *c, int16_t d);
void far f_09329(void);
void far bonus_screen(void);   /* 0x0becb */
void far f_0f8bd(void);
void far f_11bee(void far *name, int16_t egg);
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
extern int16_t      left_handed;
extern uint8_t      cursor_divider, cursor_phase;
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
void far level_event(int16_t x, int16_t y);          /* 0x0d0c8 */
void far demo_events(void);                          /* 0x0d471 */
void far tool_selected(int16_t slot);                /* 0x0e088 */
extern uint8_t tool_prev, tool_announce;             /* 0x1789, 0x178a */
extern int16_t g_2100, g_dab;

/* stubs.c - stubbed until run_level(1) needs them; see the note there */
void far played_tool_events(uint8_t far *flash);
void far tool_apply(scene_t far *s, int16_t n);
void far f_07955(void);

/* 0x0993b, game.c - the collision pass */
extern int16_t score, quota_left, combo_hi, combo_lo;
extern int16_t lives;                    /* 0x2034 */
extern int32_t mouse_x, mouse_y;         /* 0x18d3, 0x18d7 */
extern int16_t g_1ff2, g_1ff4, eaten_countdown;
extern uint8_t anim_a[111];
void far collide_scenes(void);

/* 0x088fa, game.c - the level loader, and what it fills in */
extern uint8_t     sprite_set_id;        /* 0x2103 */
extern table_t     level_sprites;        /* 0x1fec */
extern uint8_t     solid_count;          /* 0x2031 */
extern solid_t far *solids;              /* 0x202d */
extern int16_t     level_flags[7];       /* 0x201e - one per word. The third,
                                          * [0x2022], is the background warp */
extern uint8_t     scenery_count;        /* 0x2000 - a byte */
extern int16_t     level_outcome;        /* 0x200d - 1 = won, and run_level
                                          * turns that into the 2 it returns */
extern int16_t     g_1ffa, g_1ffc;       /* what a 0x4e win hands to the menu */
extern int16_t     g_1ff2, g_1ff4;       /* the teleporter's far end */
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
int16_t far level_screens(int16_t demo);   /* 0x1102a */
void far banner_build(desc_t far *dest, const char far *text, uint8_t colour,
                      int16_t top);   /* 0x103e2 */
void far image_overlay(desc_t far *src, desc_t far *dst, int16_t row);  /* 0x1081c */
void far draw_level_status(desc_t far *dest);  /* 0x0b739 */
extern uint8_t level_palette[768];   /* 0x0de1 */
void far stamp_solid(solid_t far *o, desc_t far *dest);

/* 0x0a410, game.c - the three-slot message ticker */
extern desc_t far  *message_image[3];    /* 0x210c */
extern viewport_t   message_rect[3];     /* 0x2118 */
extern uint8_t      message_time[3];     /* 0x2154 */
void far message_post(const char far *fmt, const char far *arg);

#endif /* DUCKS_DOS_H */
