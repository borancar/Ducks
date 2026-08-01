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

#define far

/* ------------------------------------------------------------------- types */

/* The 20-byte viewport every drawing routine clips against; make_rect fills it. */
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

/* The four-word destination rectangle the row blitters take. */
typedef struct {
    int16_t top, bottom, left, right;
} rect_t;

/* An image: a table of row pointers, plus the size resource_load filled in. */
typedef struct {
    uint8_t far **rows;         /* one pointer per row */
    int16_t       w;            /* +0x0c */
    int16_t       h;            /* +0x0e */
} desc_t;

/* A sprite, 14 bytes in the original. */
typedef struct {
    int16_t       w, h;         /* +0x00, +0x02 */
    int16_t       ox, oy;       /* +0x04, +0x06 - origin */
    uint8_t far  *pixels;       /* +0x0a */
} sprite_t;

typedef struct {
    sprite_t far *base;         /* +0x02/+0x04 in the original header */
} table_t;

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
void far blit_rows(desc_t far *desc, rect_t rect, int16_t srcrow);
void far blit_rows_masked(desc_t far *desc, rect_t rect, int16_t srcrow);
void far compose_layer(void);
void far compose_scroll(int16_t scroll_x, int16_t scroll_y);
void far draw_sprite(int16_t far *index, int16_t x, int32_t y,
                     table_t far *table, viewport_t far *clip, uint8_t colour);
void far outline_sprite(int16_t far *index, int16_t x, int16_t y,
                        table_t far *table, viewport_t far *clip);

void far mouse_motion(int16_t far *dx, int16_t far *dy);
int16_t far mouse_presses(int16_t button);
int16_t far mouse_releases(int16_t button);

/* game.c's, called from the video layer */
void far make_rect(viewport_t far *r, int16_t top, int16_t bottom,
                   int16_t left, int16_t right);
void far palette_build(void);

#endif /* DUCKS_DOS_H */
