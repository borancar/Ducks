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

/* A menu descriptor: game_main's argument, and what action 18 swaps. run_screen
 * is what walks it and run_screen has not been read, so the contents are unknown.
 * Given a size only so the three descriptors can be defined; nothing indexes it
 * yet, and when run_screen is read this becomes the real layout. */
typedef struct { uint8_t opaque[64]; } menu_t;

/* What run_screen returns. Offsets are the ones the code indexes; the record is
 * longer than this and the rest has not been read. */
typedef struct {
    uint8_t       pad0[4];
    menu_t far   *submenu;      /* +4/+6: where action 18 points the menu */
    int16_t       action;       /* +8:    the action code, 1..20 */
    uint8_t       pad1;         /* +0xa */
    uint8_t       param;        /* +0xb:  episode ordinal, readme section, demo */
} record_t;

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
typedef struct {
    int16_t x, y;
    uint8_t type;
} entity_t;

typedef struct {
    int16_t     count;          /* +2 */
    entity_t far *entities;     /* +8 */
} scene_t;

typedef struct {
    int32_t x, y;               /* 1/8-pixel fixed point */
    uint8_t colour;
} particle_t;

/* An egg file entry, stride 0x17. */
typedef struct {
    void far *fp;
    void far *block;            /* +8 - freed by close_egg_files */
    uint8_t   limit;            /* +0x10 - this egg's shareware limit */
} egg_file_t;

/* ------------------------------------------------- the state the video owns */

extern int16_t    video_mode;        /* non-zero is the 360-wide mode */
extern int16_t    screen_width;      /* 360 or 320 */
extern int16_t    screen_height;     /* 240 or 200 */
extern int16_t    screen_x0;         /* centring offset, 20 or 0 */
extern void far (*plot)(int16_t x, int16_t y, uint8_t colour);
extern uint8_t    current_plane;
extern uint16_t   page_front, page_back;
extern int16_t    flip_phase;

/* game.c's, which the video reads */
extern uint8_t    game_speed;
extern int16_t    last_key;      /* 0x18f6 - what init spins on */
extern int16_t    fade_level;
extern int8_t     fade_direction;
extern int16_t    fade_start_colour;
extern uint8_t    palette_stored[768];
extern uint8_t    palette_washed[48];
extern int16_t    blink_enable, blink_countdown, blink_toggle;
extern viewport_t viewport_panel, viewport_screen, viewport_full;

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
void far compose_scroll(int16_t scroll_x, int16_t scroll_y);
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
void far close_egg_files(void);
void far input_poll(int16_t w, int16_t h);
void far scan_save_slots(void);
void far save_settings(void);
void far init(void);
void far game_main(menu_t far *menu);
int16_t far resource_load(desc_t far *desc, uint8_t type, uint8_t index,
                          int16_t pal_at, int16_t set_size,
                          int16_t arg18, int16_t arg1a);
int16_t far episode_end_gate(int16_t level, int16_t egg);
record_t far *menu_screen_driver(menu_t far *menu, void far *a, int16_t b);

/* the port I/O the original used; a port supplies its own or drops them */
void outp(uint16_t port, uint8_t v);
void outpw(uint16_t port, uint16_t v);
uint8_t inp(uint16_t port);
void far delay(int16_t ms);
int putw(int w, FILE *f);   /* Borland's; one word out */

/* stubs.c, until each is read out - see that file for what they are */
int16_t far in_game_frame(int16_t arg);
record_t far *far run_screen(menu_t far *menu, void far *a, int16_t b);
int16_t far egg_find_block(uint8_t type, uint8_t index, int16_t arg);
int16_t far egg_read_word(void far *s);
uint8_t far egg_read_byte(void far *s);
int16_t far alloc_image(void far *d, int16_t a, int16_t b, int16_t c, int16_t e);
int16_t far load_demo(uint8_t index);
int16_t far pick_random_demo(void);
int16_t far sprite_index_for(void far *e);
int16_t far detect_hardware(void);
int16_t far f_1102a(int16_t a);
int16_t far f_14e88(void far *fp);
void far show_readme_section(uint8_t n);
void far save_game_screen(void);
void far load_game_screen(void);
void far register_screen(void);
void far high_score_screen(void);
void far show_attract_screen(int16_t frames);
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
void far sound_play_guarded(int16_t id, int16_t mode);
void far release_sounds(void);
void far sound_init(int16_t rate);
void far install_int23(void far *h);
void far ctrl_break_handler(void);
void far crt_exit(void);
void far print_newline(void);
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
void far f_088fa(void);
void far f_09329(void);
void far f_0becb(void);
void far f_0f55c(void);
void far f_0f8bd(void);
void far f_11bee(void far *name, int16_t egg);
void far f_147c5(int16_t a, int16_t b, int16_t c);
void far f_15388(void far *o);

extern int16_t previous_type, particle_count;
extern particle_t far *particle_array;
extern table_t far *sprite_table;
extern viewport_t hud_clip;
extern void far *egg_stream, *current_buffer;

/* game.c's, called from the video layer */
void far make_rect(viewport_t far *r, int16_t top, int16_t bottom,
                   int16_t left, int16_t right);
void far palette_build(void);
void far palette_set_black(uint8_t index);
extern uint8_t gamma_level;

#endif /* DUCKS_DOS_H */
