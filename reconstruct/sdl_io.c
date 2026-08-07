/* sdl_io.c - dos_io.c's interface, on SDL3.
 *
 * Link this instead of dos_io.c and the game draws into a window. Nothing above
 * this file changes, which is what the split was for.
 *
 * What is kept and what is dropped:
 *
 *   kept    The x & 3 plane filter, and set_plane with it. The game still calls
 *           each drawing routine four times and each pass still writes its own
 *           quarter of the columns, so the picture and the amount of work are
 *           what they always were. Collapsing that is a separate change - it
 *           means touching the four plane loops in game.c, not this file - and
 *           doing it here first would quadruple the drawing instead of quartering
 *           it, because each pass would draw everything.
 *
 *   dropped Planar addressing. The framebuffer is linear, `fb[y * width + x]`,
 *           so there is no x >> 2 and no map mask. The two video pages are two
 *           arrays rather than two offsets into one 64 KB aperture.
 *
 *   dropped The DAC. Colours are an SDL palette of 256 RGB triples, and the
 *           6-bit values the game writes are scaled up on the way in.
 *
 * Build:  cc -c sdl_io.c $(pkg-config --cflags sdl3)
 * Link:   $(pkg-config --libs sdl3)
 */

#include <SDL3/SDL.h>
#include <stdlib.h>
#include <string.h>

#include "dos.h"

/* ------------------------------------------------------------------ state
 *
 * The video state is the backend's, so this file defines it exactly as dos_io.c
 * does - link one or the other, never both.
 */

int16_t    video_mode;
int16_t    screen_width;
int16_t    screen_height;
int16_t    screen_x0;
void far (*plot)(int16_t x, int16_t y, uint8_t colour);
uint8_t    current_plane;
uint16_t   page_front, page_back;
int16_t    flip_phase;

/* The compositor's state: the tile's wrap masks, the scroll into it, and how far
 * that scroll moves each frame. load_background sets the masks from the tile's
 * own size; a screen sets the step and palette_fade_step's tail does the moving. */
int16_t    wrap_x, wrap_y;              /* 0x1729, 0x172b */
uint8_t    bg_scroll_x, bg_scroll_y;    /* 0x177e, 0x177f */
/* 0x179f - the background warp's per-row x displacements, and initialised DATA:
 * nothing in the image writes it, the only reference is the read at 0x05e8b, and
 * the bytes are identical in the image and in four live guests. Declared bare it
 * was thirty-two zeros, so the warp ran and displaced nothing - a flat wobble
 * rather than a missing one, which is the harder kind to notice. A 0 -> 16 -> 0
 * hump, indexed `& 0x1f`. */
uint8_t    warp_table[32] = {
    0x00, 0x00, 0x00, 0x01, 0x01, 0x02, 0x03, 0x04,
    0x06, 0x08, 0x0a, 0x0c, 0x0d, 0x0e, 0x0f, 0x0f,
    0x10, 0x10, 0x10, 0x0f, 0x0f, 0x0e, 0x0d, 0x0c,
    0x0a, 0x08, 0x06, 0x04, 0x03, 0x02, 0x01, 0x01,
};
uint8_t    bg_step_x, bg_step_y;        /* 0x1780, 0x1781 - added to the scroll
                                         * once a frame, so a menu's background
                                         * creeps upward at one row a frame */
uint8_t    warp_phase, warp_step;       /* 0x17bf, 0x17c0 - compose_scroll's */

/* There are no ports here. The game still writes the DAC directly in one place -
 * cutscene_photos flashes the screen white - so these exist to let that link, and
 * the flash is a palette operation waiting to be written. */
void outp(uint16_t port, uint8_t v)    { (void) port; (void) v; }
void outpw(uint16_t port, uint16_t v)  { (void) port; (void) v; }
uint8_t inp(uint16_t port)             { (void) port; return 0; }
void far delay(int16_t ms)             { SDL_Delay((Uint32) ms); }

/* 0:0x1e6b, and the last thing main calls. Borland's exit: it runs the atexit
 * chain, flushes the streams and hands control back to DOS, and nothing after it
 * in main is ever reached - main's own `retf` included.
 *
 * It is here rather than in stubs.c because ending the process is the backend's
 * to do: SDL has a window and an audio device open, and leaving through main's
 * return left the exit status as whatever main happened to leave in AX, which is
 * where the 255 a clean QUIT DUCKS was reporting came from. */
void far crt_exit(void)
{
    audio_close();
    SDL_Quit();
    exit(0);
}

/* ------------------------------------------------------------ private state */

#define SCALE_DEFAULT 3          /* the window is this many times the mode */

static SDL_Window  *window;
static void         capture_refresh(void);   /* the mouse capture, below */
static SDL_Surface *surface;     /* the window's own surface; no renderer */
static int          scale = SCALE_DEFAULT;

static uint8_t  page_a[360 * 240];      /* the two video pages, linear */
static uint8_t  page_b[360 * 240];
static uint8_t *fb_back  = page_a;      /* what everything draws into */
static uint8_t *fb_front = page_b;      /* what the last flip presented */

static uint32_t palette[256];           /* already in the surface's format */

/* The original spins on 0x3da until the retrace. Here the pace is a deadline:
 * 70 Hz is what the CRTC gave it in mode 0x13, and the game's own speed control
 * subtracts from that by delaying at the top of the flip. */
#define FRAME_NS (1000000000ull / 70)
static uint64_t next_frame_ns;

/* ------------------------------------------------------------- video: mode */

/* The BIOS mode call has nothing to do here: SDL owns the display. Kept so the
 * caller reads the same, and because set_mode_x asks for 0x13 unconditionally. */
void far set_bios_mode(uint8_t mode)
{
    (void) mode;
}

/* Opens the window, or resizes it when the game changes resolution - which it
 * does from VIDEO SETTINGS, through the same [0x4fe] the original writes here.
 *
 * The geometry and the viewports are set exactly as dos_io.c sets them, because
 * the game reads them: screen_x0 centres a 320-wide play area in a 360-wide
 * screen, and the extra 40 rows at 240 are where the status panel goes.
 */
void far set_mode_x(int16_t wide)
{
    screen_width  = wide ? 360 : 320;
    screen_height = wide ? 240 : 200;
    screen_x0     = wide ? 20  : 0;
    plot          = wide ? plot_pixel_wide : plot_pixel;
    video_mode    = wide;

    if (!window) {
        if (!SDL_Init(SDL_INIT_VIDEO)) {
            SDL_Log("SDL_Init: %s", SDL_GetError());
            return;
        }
        window = SDL_CreateWindow("Ducks!", screen_width * scale,
                                  screen_height * scale, 0);
        if (!window) {
            SDL_Log("SDL_CreateWindow: %s", SDL_GetError());
            return;
        }
        /* Best effort. On a 60 Hz display this cannot also be 70 Hz, and the
         * deadline below is what the game's timing actually depends on, so the
         * clock wins and vsync only removes tearing when the rates agree. */
        SDL_SetWindowSurfaceVSync(window, 1);
        capture_refresh();
    } else {
        SDL_SetWindowSize(window, screen_width * scale, screen_height * scale);
    }
    surface = SDL_GetWindowSurface(window);

    memset(page_a, 0, sizeof page_a);
    memset(page_b, 0, sizeof page_b);
    page_front = page_back = 0;         /* the game reads these; both pages are
                                         * whole arrays here, so the offsets are
                                         * only ever zero */

    make_rect(&viewport_panel,  screen_height - 40, screen_height,
              screen_x0, screen_x0 + 320);
    make_rect(&viewport_screen, screen_height / 2 - 100, screen_height / 2 + 100,
              screen_x0, screen_x0 + 320);
    make_rect(&viewport_full,   0, screen_height, 0, screen_width);

    next_frame_ns = SDL_GetTicksNS();
}

/* ------------------------------------------------------------ video: planes */

/* Still here, and still meaningful: the drawing routines below keep the x & 3
 * filter, so the four passes each write their own columns. When the plane loops
 * in game.c are collapsed this becomes an empty function and then goes away. */
void far set_plane(uint8_t plane)
{
    current_plane = plane & 3;
}

/* Both pages, which is what the original does even though it looks like one
 * screen's worth: 0xfa00 bytes with the map mask opened to all four planes is
 * 64000 * 4 = 256,000 bytes, the whole of VRAM. Clearing only the page being
 * drawn into leaves the other holding the last picture, and page_flip then
 * alternates black and stale - which is exactly what a splash with no image of
 * its own does, because it draws nothing over either. */
void far clear_vram(void)
{
    memset(page_a, 0, sizeof page_a);
    memset(page_b, 0, sizeof page_b);
}

/* --------------------------------------------------------------- video: DAC */

/* The original writes black into `count` DAC entries. Doing the same here blanks
 * whatever is on screen that used those colours, so this also clears the window -
 * which is what every caller is actually after, since it is the last thing before
 * a mode change or a fade. */
void far dac_set_black(uint8_t first, uint8_t count)
{
    int i;

    for (i = first; i < count; i++)
        palette[i] = SDL_MapSurfaceRGB(surface, 0, 0, 0);

    if (surface) {
        SDL_FillSurfaceRect(surface, NULL, SDL_MapSurfaceRGB(surface, 0, 0, 0));
        SDL_UpdateWindowSurface(window);
    }
}

/* The game keeps 6-bit DAC values, so the top two bits are free - scale rather
 * than shift, or everything comes out a quarter too dark. */
static uint32_t dac_to_rgb(uint8_t r, uint8_t g, uint8_t b)
{
    return SDL_MapSurfaceRGB(surface, (Uint8) (r * 255 / 63),
                             (Uint8) (g * 255 / 63), (Uint8) (b * 255 / 63));
}

void far palette_upload(void)
{
    int i;

    for (i = 0; i < 256; i++)
        palette[i] = dac_to_rgb(palette_stored[i * 3 + 0] >> 2,
                                palette_stored[i * 3 + 1] >> 2,
                                palette_stored[i * 3 + 2] >> 2);
}

/* The same state machine as the original, with the 768 port writes replaced by
 * scaling into the palette array. fade_level is 0..15 and the original computes
 * (component * level) >> 6. */
/* 0x0b10f-0x0b22f. Split out because in the original every one of the exits
 * below is a jmp to the tail rather than a ret - see palette_fade_step. */
static void fade_frame(int16_t arg)
{
    int i;

    if (!fade_direction) {
        if (arg)                           /* 0x0b226 - not fading, so hand the
                                            * built palette straight over */
            palette_upload();
        return;
    }
    if (fade_level == 0 && fade_direction == -1) {
        fade_direction = 0;
        return;
    }

    palette_build();
    fade_level += fade_direction;

    if (fade_level >= 15) {
        palette_upload();
        fade_direction = 0;
        return;
    }
    if (fade_level <= 0) {
        fade_level = 0;
        fade_direction = 0;
    }

    for (i = fade_start_colour; i < 256; i++)
        palette[i] = dac_to_rgb((palette_stored[i * 3 + 0] * fade_level) >> 6,
                                (palette_stored[i * 3 + 1] * fade_level) >> 6,
                                (palette_stored[i * 3 + 2] * fade_level) >> 6);
}

void far palette_fade_step(int16_t arg)
{
    fade_frame(arg);

    /* 0x0b230. One function in the original, and this half of it always runs -
     * every exit above is a jmp here. It is the frame tick the screens do not
     * have of their own: the cursor's animation phase, the background scroll,
     * and the warp. A menu never calls anything else once a frame, so without
     * it the background stands still and the cursor's tool never turns.
     *
     * The scroll wraps against the tile's own size, which is why load_background
     * leaves those masks one less than the width and the height. */
    if (++cursor_divider == 2) {
        cursor_phase = (uint8_t) ((cursor_phase + 1) & 3);
        cursor_divider = 0;
    }
    bg_scroll_y = (uint8_t) ((bg_scroll_y + bg_step_y) & wrap_y);
    bg_scroll_x = (uint8_t) ((bg_scroll_x + bg_step_x) & wrap_x);

    warp_phase++;
    warp_phase = (uint8_t) (warp_phase + bg_step_y * warp_step);
}

/* --------------------------------------------------------- video: page flip */

/* Present the back page, swap, then wait.
 *
 * Two waits in the original: display enable, then vertical retrace. Both are gone;
 * what replaces them is a deadline. The game's speed setting still applies, at the
 * top, exactly as page_flip did it - delay(0x1f - game_speed) milliseconds - so
 * VIDEO SETTINGS > GAME SPEED keeps working, which it stopped doing under the
 * native flip.
 */
void far page_flip(void)
{
    uint64_t now;
    int      y, x;

    if (game_speed < 0x1f)                      /* the game's own throttle */
        SDL_DelayNS((uint64_t) (0x1f - game_speed) * 1000000ull);

    if (surface) {
        SDL_LockSurface(surface);
        for (y = 0; y < screen_height * scale; y++) {
            uint32_t      *dst = (uint32_t *) ((uint8_t *) surface->pixels
                                               + (size_t) y * surface->pitch);
            const uint8_t *src = fb_back + (size_t) (y / scale) * screen_width;

            for (x = 0; x < screen_width * scale; x++)
                dst[x] = palette[src[x / scale]];
        }
        SDL_UnlockSurface(surface);
        SDL_UpdateWindowSurface(window);
    }

    {   /* swap the pages, as the CRTC start address swap did */
        uint8_t *t = fb_back;
        fb_back = fb_front;
        fb_front = t;
    }

    /* The deadline, not a sleep of a fixed length: overruns reset the schedule
     * rather than accumulating debt, because the original does not catch up
     * either - it waits out the next retrace and shows fewer frames. */
    next_frame_ns += FRAME_NS;
    now = SDL_GetTicksNS();
    if (now < next_frame_ns)
        SDL_DelayPrecise(next_frame_ns - now);
    else
        next_frame_ns = now;

    flip_phase = (flip_phase + 1) % 10;
}

/* ------------------------------------------------------------ video: drawing
 *
 * Linear addressing: fb[y * screen_width + x]. The x & 3 filter stays, so each of
 * the four passes writes its own columns and the total work is unchanged.
 */

void far plot_pixel(int16_t x, int16_t y, uint8_t colour)
{
    if (current_plane != (x & 3))
        return;
    if (x < 0 || y < 0 || x >= screen_width || y >= screen_height)
        return;
    fb_back[(size_t) y * screen_width + x] = colour;
}

/* The original needed a second routine because the row stride changed. Here the
 * stride is screen_width either way, so this is the same function - kept only
 * because the game swaps `plot` between them and reads the pointer. */
void far plot_pixel_wide(int16_t x, int16_t y, uint8_t colour)
{
    plot_pixel(x, y, colour);
}

void far blit_rows(desc_t far *desc, viewport_t rect, int16_t srcrow)
{
    int16_t row, src_row, x;

    /* Bring-up guard, not something the original needs: until the egg reader
     * exists, resource_load fails and leaves descriptors without a row table, and
     * the screen players blit from them anyway. */
    if (!desc || !desc->rows)
        return;

    /* The original's rectangle counts destination *bytes*, because its destination
     * is one plane at 80 bytes a row. Here both sides are linear pixels, so the
     * same pass is "every fourth column, starting at the plane" - the x >> 2 and
     * the x * 4 both disappear and the stepping does the work.
     *
     * The source row is counted from srcrow, independently of where on the screen
     * the rectangle starts: the original keeps it in cx and increments it beside
     * the destination row rather than deriving it. A splash is 24 rows drawn at
     * screen row 80, so indexing the source by the screen row reads past the end
     * of it and draws nothing at all. */
    for (row = rect.top, src_row = srcrow; row < rect.bottom; row++, src_row++) {
        const uint8_t *src;
        uint8_t       *dst;

        if (src_row < 0 || src_row >= desc->h || row >= screen_height)
            break;
        if (row < 0)
            continue;
        src = desc->rows[src_row];
        dst = fb_back + (size_t) row * screen_width;

        /* The source is read from its own column 0 - rect.left only moves the
         * destination, which is what centres a 320-wide picture in 360. */
        for (x = current_plane; rect.left + x < rect.right; x += 4)
            if (x < desc->w && rect.left + x >= 0 && rect.left + x < screen_width)
                dst[rect.left + x] = src[x];
    }
}

void far blit_rows_masked(desc_t far *desc, viewport_t rect, int16_t srcrow)
{
    int16_t row, src_row, x;

    if (!desc || !desc->rows)
        return;

    for (row = rect.top, src_row = srcrow; row < rect.bottom; row++, src_row++) {
        const uint8_t *src;
        uint8_t       *dst;

        if (src_row < 0 || src_row >= desc->h || row >= screen_height)
            break;
        if (row < 0)
            continue;
        src = desc->rows[src_row];
        dst = fb_back + (size_t) row * screen_width;

        for (x = current_plane; rect.left + x < rect.right; x += 4)
            if (x < desc->w && rect.left + x >= 0 && rect.left + x < screen_width
                && src[x])
                dst[rect.left + x] = src[x];
    }
}

/* dos_io.c's body with the addressing changed: linear instead of planar, and the
 * clip arithmetic left exactly as it was. */
void far draw_sprite(int16_t far *index, int16_t x, int32_t y,
                     table_t far *table, viewport_t far *clip, uint8_t colour)
{
    sprite_t far *desc;
    int16_t  w, h, src = 0, row_extra = 0, row, col;
    int16_t  x_end, y_end, yy = (int16_t) y;

    if (!table || !table->base)
        return;
    desc = &table->base[*index];
    w = desc->w;  h = desc->h;

    x  -= desc->ox;
    yy += clip->top - desc->oy;
    x_end = x + w;
    y_end = yy + h;

    if (x < 0)                        { row_extra -= x;  src -= x;  x = 0; }
    else if (clip->right < x_end)     { row_extra += x_end - clip->right;
                                        x_end = clip->right; }
    if (clip->top > yy)               { src += (clip->top - yy) * w;
                                        yy = clip->top; }
    else if (clip->bottom < y_end)    { y_end = clip->bottom; }

    if (x_end <= x || y_end <= yy)
        return;

    for (row = yy; row < y_end; row++) {
        for (col = x; col < x_end; col++)
            if ((col & 3) == current_plane) {
                uint8_t px = desc->pixels[src + (col - x)];
                if (px)
                    fb_back[(size_t) row * screen_width + col] =
                        (uint8_t) (px + colour);
            }
        src += (x_end - x) + row_extra;
    }
}

/* 0x065f1. A halo: colour 0 above, below, left and right of every non-zero
 * pixel of the sprite, and nothing at the pixel itself - so drawn under the
 * sprite it becomes an outline. The HUD's tool row and draw_entities both use
 * it, which is every outlined thing on screen.
 *
 * Transcribed from native.py's byte-compared version rather than from the
 * listing, so the two quirks come with it and are deliberate:
 *
 *   - the vertical clip insets by a row at EACH end - y becomes top + 1 and
 *     bottom becomes limit - 1 - which is why an outline is a row short of the
 *     sprite it haloes;
 *   - clip->left is added to x AFTER the source offsets are worked out, so it
 *     shifts the sprite and therefore also changes which plane each pixel is
 *     in. The original reads it as a byte, and every viewport's left fits in
 *     one.
 *
 * The neighbours are not clipped, only the source walk is, so a pixel on the
 * first row writes to the row above the clip. That is the original's too, and
 * plot bounds the framebuffer, so here it costs a pixel rather than a fault.
 */
void far outline_sprite(int16_t far *index, int16_t x, int16_t y,
                        table_t far *table, viewport_t far *clip)
{
    sprite_t far *desc;
    int16_t  w, h, src = 0, row_extra = 0;
    int16_t  top, right, bottom, ncols, nrows, stride, row, col, xbase;

    if (!table || !table->base)
        return;
    desc = &table->base[*index];
    w = desc->w;  h = desc->h;
    if (!w || !h)
        return;

    xbase = (uint8_t) clip->left;
    x    -= desc->ox;
    top   = clip->top;
    y    += top - desc->oy;
    right = x + w;
    bottom = y + h;

    if (x < 1)                  { row_extra -= x - 1; src -= x - 1; x = 1; }
    else if (right > 0x13F)     { row_extra += right - 0x13F; right = 0x13F; }
    if (top >= y)               { src -= (y - top - 1) * w; y = top + 1; }
    else if (clip->bottom <= bottom) { bottom = clip->bottom - 1; }
    x     += xbase;
    right += xbase;

    ncols  = right - x;
    nrows  = bottom - y;
    stride = ncols + row_extra;
    if (ncols <= 0 || nrows <= 0 || stride <= 0 || src < 0)
        return;
    if (src + (nrows - 1) * stride + ncols > w * h)   /* the native's guard */
        return;

    for (row = 0; row < nrows; row++)
        for (col = 0; col < ncols; col++)
            if (desc->pixels[src + row * stride + col]) {
                plot(x + col,     y + row - 1, 0);
                plot(x + col,     y + row + 1, 0);
                plot(x + col + 1, y + row,     0);
                plot(x + col - 1, y + row,     0);
            }
}

/* 0x05d3a, on a linear framebuffer. Two layers a pixel: the backdrop, which the
 * menu drew its items into, and where that is zero the background tile, repeated
 * with an AND against the wrap masks. The column steps by four, so one call
 * fills a single plane - which is the only reason a menu frame calls it four
 * times over.
 *
 * The x index into the tile is the column itself: the original starts it at
 * current_plane and masks it, and adds no scroll at all. */
void far compose_layer(void)
{
    int16_t row, x;

    if (!backdrop.rows || !background.rows)
        return;

    for (row = 0; row < screen_height; row++) {
        uint8_t far *fg  = backdrop.rows[row];
        uint8_t far *bg  = background.rows[(row + bg_scroll_y) & wrap_y];
        uint8_t far *dst = fb_back + (size_t) row * screen_width;

        for (x = current_plane; x < screen_width; x += 4)
            dst[x] = fg[x] ? fg[x] : bg[x & wrap_x];
    }
}
/* 0x05dc4, on a linear framebuffer: the in-game compositor, and the reason the
 * ground shows up at all. compose_layer's scrolling sibling, and the differences
 * are all in the indexing:
 *
 *   - the foreground is the level, not the screen. Its row is `sy + r` and its
 *     column `sx + x`, both unmasked - the backdrop is level_w by level_h and
 *     nothing wraps it. Masking it by wrap_x/wrap_y, which are the *tile's*
 *     size less one, would repeat the ground every 64 pixels.
 *   - the background tile does wrap, in both axes, and scrolls at **half** the
 *     foreground's rate: `sx >> 1` and `sy >> 1`, plus its own bg_scroll pair.
 *     That halving is the parallax.
 *   - the rows drawn are the game viewport's, not the screen's.
 *
 * This indexing is native.py's, which is byte-compared against the original on
 * every call - except the warp branch, which had never executed until 2026-08-01
 * and is still unverified. dos_io.c's copy of this routine masks the foreground
 * as well, which does not match native.py and would tile the ground; it has
 * never been run.
 */
void far compose_scroll(int16_t sx, int16_t sy)
{
    int16_t row0   = viewport_game.top;
    int16_t rows   = viewport_game.bottom - viewport_game.top;
    int16_t right  = viewport_game.width;
    int16_t base_x = (sx >> 1) + bg_scroll_x;      /* [0x177e] */
    int16_t phase  = warp_phase + (sy >> 1) * warp_step;
    int16_t r, x;

    if (!backdrop.rows || !background.rows || rows <= 0)
        return;

    for (r = 0; r < rows; r++) {
        int16_t        dx = base_x;
        uint8_t far   *fg = backdrop.rows[sy + r];
        uint8_t far   *bg;
        uint8_t       *dst;

        if (level_flags[2]) {                      /* [0x2022] - the warp */
            phase &= 0x1f;                         /* re-masked every row, so
                                                    * this is not a progression */
            dx     = base_x + warp_table[phase];
            phase  = (phase + warp_step) & 0xff;
        }
        bg  = background.rows[(((sy >> 1) + bg_scroll_y + row0 + r)) & wrap_y];
        dst = fb_back + (size_t) (row0 + r) * screen_width + viewport_game.left;

        for (x = current_plane; x < right; x += 4) {
            uint8_t px = fg[sx + x];

            dst[x] = px ? px : bg[(x + dx) & wrap_x];
        }
    }
}

/* ------------------------------------------------------------------- audio
 *
 * What is left of the card. The original resets the DSP, pulls the IRQ and the
 * DMA channel out of BLASTER, installs a handler and starts an auto-init
 * transfer; every time the DMA reaches half way, the handler mixes the next
 * block. Here the device asks and sound_mix answers, which is the same
 * arrangement with the hardware taken out.
 *
 * The format is the samples' own: signed 8-bit, one channel, 11111 Hz. SDL
 * resamples to whatever the device wants, so the game's rate is kept rather
 * than the machine's.
 */
static SDL_AudioStream *audio;

static void SDLCALL audio_feed(void *userdata, SDL_AudioStream *stream,
                               int additional, int total)
{
    int8_t block[1024];

    (void) userdata; (void) total;
    while (additional > 0) {
        int n = additional > (int) sizeof block ? (int) sizeof block : additional;

        sound_mix(block, (int16_t) n);
        SDL_PutAudioStreamData(stream, block, n);
        additional -= n;
    }
}

int16_t far audio_open(int16_t rate)
{
    SDL_AudioSpec spec;

    if (audio)
        return 1;
    if (!SDL_InitSubSystem(SDL_INIT_AUDIO)) {
        SDL_Log("SDL_InitSubSystem(AUDIO): %s", SDL_GetError());
        return 0;
    }
    spec.format   = SDL_AUDIO_S8;
    spec.channels = 1;
    spec.freq     = rate;
    audio = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec,
                                      audio_feed, NULL);
    if (!audio) {
        SDL_Log("SDL_OpenAudioDeviceStream: %s", SDL_GetError());
        return 0;
    }
    SDL_ResumeAudioStreamDevice(audio);

    /* Only complain if SDL did not take the rate we asked for. It is expected to
     * resample the game's 8-bit samples to whatever the device wants; if it ever
     * does not, everything plays at the device's rate and the whole game sounds
     * fast - which is a hard thing to guess at from outside, and was worth one
     * line while "the sound is wrong" was open. It is not worth a line every
     * launch now that the rate is settled, so this says nothing when it agrees. */
    {
        SDL_AudioSpec src, dst;

        if (SDL_GetAudioStreamFormat(audio, &src, &dst) && src.freq != rate)
            SDL_Log("audio: asked for %d Hz, SDL is feeding %d Hz to a %d Hz "
                    "device - everything will play at the wrong speed",
                    rate, src.freq, dst.freq);
    }
    return 1;
}

void far audio_close(void)
{
    if (!audio)
        return;
    SDL_DestroyAudioStream(audio);
    audio = NULL;
}

/* ------------------------------------------------------------------- mouse
 *
 * The original asks INT 33h for motion since the last call and for per-button
 * press and release counts. SDL gives the same three things: relative motion, and
 * events that can be counted between polls.
 */

static int16_t press_count[3], release_count[3];
static int16_t rel_x, rel_y;

/* INT 33h numbers its buttons LEFT, RIGHT, MIDDLE - 0, 1, 2 - and SDL numbers
 * them LEFT, MIDDLE, RIGHT - 1, 2, 3. The counters are indexed the way the game
 * indexes them, because button_map holds INT 33h numbers and the MOUSE BUTTONS
 * screen writes it, so the translation has to happen here.
 *
 * Getting this wrong is not a dead button, which is what makes it hard to spot:
 * with the default map - walk on RIGHT, cycle tools on MIDDLE - subtracting one
 * hands walking to the middle button and tool cycling to the right one, so both
 * buttons do something, just not what they say. That was the bug behind "right
 * click doesn't move the hero". */
static int int33_button(Uint8 sdl_button)
{
    switch (sdl_button) {
    case SDL_BUTTON_LEFT:   return 0;
    case SDL_BUTTON_RIGHT:  return 1;
    case SDL_BUTTON_MIDDLE: return 2;
    default:                return -1;
    }
}

/* ----------------------------------------------------------- mouse capture
 *
 * The original owned the machine. INT 33h reported motion with nothing for the
 * pointer to run into and nowhere else for it to go, and the game leans on that:
 * it keeps the position itself as a running total of deltas, so a pointer that
 * stops moving because it has hit the edge of a window is a pointer the game
 * thinks stopped moving. Capturing it - SDL's relative mode, which hides it,
 * confines it to the window and reports motion unclipped - is what gives that
 * assumption back.
 *
 * Ctrl+Alt lets go, and pressing it again takes hold. The game reads neither
 * modifier, so the chord costs nothing, and it is the one every DOS emulator has
 * used for this since DOSBox. While the mouse is loose a click inside the window
 * takes hold again, and that click is swallowed rather than handed on - both
 * halves of it, so the game is not left with a release it never saw pressed.
 *
 * What the user asked for and what SDL has been told are kept apart: losing
 * focus drops the capture without changing the answer, and getting focus back
 * restores whatever the answer was.
 */
static int capture_wanted = 1;          /* what Ctrl+Alt last said */
static int capture_now;                 /* what SDL has been told */
static int chord_was;                   /* Ctrl+Alt held on the previous pump */
static int swallow_release[3];          /* the click that took hold again */

static void capture_set(int on)
{
    static int complained;

    if (!window || on == capture_now)
        return;
    if (!SDL_SetWindowRelativeMouseMode(window, on != 0) && !complained) {
        complained = 1;                 /* once: a headless driver has no mouse
                                         * to capture and says so every time */
        SDL_Log("SDL_SetWindowRelativeMouseMode: %s", SDL_GetError());
    }
    capture_now = on;
    SDL_SetWindowTitle(window, on ? "Ducks!  -  Ctrl+Alt frees the mouse"
                                  : "Ducks!  -  click, or Ctrl+Alt, to capture");
}

/* Both conditions, every time: the user's answer, and this window having focus. */
static void capture_refresh(void)
{
    capture_set(capture_wanted
                && window != NULL
                && (SDL_GetWindowFlags(window) & SDL_WINDOW_INPUT_FOCUS) != 0);
}

/* The keyboard, as the runtime's kbhit and getch see it: a queue rather than a
 * single variable, because a key with no ASCII is two reads and the second one
 * has to still be there when input_poll asks for it. */
#define KEY_QUEUE 64
static int16_t key_queue[KEY_QUEUE];
static int     key_head, key_tail;

void key_push(int16_t k)
{
    int next = (key_tail + 1) % KEY_QUEUE;

    if (next != key_head) {                 /* a full queue drops the key, which
                                             * is what the BIOS buffer did */
        key_queue[key_tail] = k;
        key_tail = next;
    }
}

int16_t far key_pending(void)               /* 0:0x29fc */
{
    return key_head != key_tail;
}

int16_t far key_read(void)                  /* 0:0x2814 */
{
    int16_t k;

    if (key_head == key_tail)
        return 0;
    k = key_queue[key_head];
    key_head = (key_head + 1) % KEY_QUEUE;
    return k;
}

/* Pump SDL's queue into the counters the three wrappers below hand back. Call it
 * once a frame; the game polls the wrappers many times per frame and expects each
 * to consume what it reports. */
void sdl_pump_input(void)
{
    SDL_Event e;

    while (SDL_PollEvent(&e)) {
        switch (e.type) {
        case SDL_EVENT_QUIT:
            /* The original has no such thing - it leaves through QUIT DUCKS and
             * then main's teardown. Closing the window is the one input DOS could
             * not give it, so it is handled here rather than pretended away. */
            SDL_Quit();
            exit(0);
            break;
        case SDL_EVENT_KEY_DOWN:
            /* Into the queue, in the shape the BIOS hands them over: an ASCII
             * code, or a zero followed by a scan code for the keys that have no
             * ASCII. input_poll is what turns the second form into 0x1xx. */
            if (e.key.key == SDLK_UP)           { key_push(0); key_push(0x48); }
            else if (e.key.key == SDLK_DOWN)    { key_push(0); key_push(0x50); }
            else if (e.key.key == SDLK_LEFT)    { key_push(0); key_push(0x4b); }
            else if (e.key.key == SDLK_RIGHT)   { key_push(0); key_push(0x4d); }
            else if (e.key.key == SDLK_ESCAPE)  key_push(0x1b);
            else if (e.key.key == SDLK_RETURN)  key_push(0x0d);
            else if (e.key.key < 0x80)          key_push((int16_t) e.key.key);
            break;
        case SDL_EVENT_MOUSE_MOTION:
            if (capture_now) {          /* a loose pointer is not the game's */
                rel_x += (int16_t) e.motion.xrel;
                rel_y += (int16_t) e.motion.yrel;
            }
            break;
        case SDL_EVENT_MOUSE_BUTTON_DOWN: {
            int b = int33_button(e.button.button);

            if (b < 0)
                break;
            if (!capture_now) {
                capture_wanted = 1;     /* the click takes hold instead */
                capture_refresh();
                swallow_release[b] = 1;
                break;
            }
            press_count[b]++;
            break;
        }
        case SDL_EVENT_MOUSE_BUTTON_UP: {
            int b = int33_button(e.button.button);

            if (b < 0)
                break;
            if (swallow_release[b]) {
                swallow_release[b] = 0;
                break;
            }
            if (capture_now)
                release_count[b]++;
            break;
        }
        case SDL_EVENT_WINDOW_FOCUS_GAINED:
        case SDL_EVENT_WINDOW_FOCUS_LOST:
            capture_refresh();
            break;
        default:
            break;
        }
    }

    /* The chord, once the queue is drained: read as a state rather than caught
     * as a key, so it does not matter which of the two went down last. The edge
     * is what toggles - holding it does not flap. */
    {
        SDL_Keymod mods = SDL_GetModState();
        int chord = (mods & SDL_KMOD_CTRL) != 0 && (mods & SDL_KMOD_ALT) != 0;

        if (chord && !chord_was) {
            capture_wanted = !capture_wanted;
            capture_refresh();
        }
        chord_was = chord;
    }
}

void far mouse_motion(int16_t far *dx, int16_t far *dy)
{
    /* input_poll calls this first, every poll, so it is where the queue gets
     * drained. The original had an interrupt doing it; here it has to be pulled. */
    sdl_pump_input();

    *dx = rel_x;                          /* INT 33h 0x0b returns and clears */
    *dy = rel_y;
    rel_x = rel_y = 0;
}

int16_t far mouse_presses(int16_t button)
{
    int16_t n;

    if (button < 0 || button > 2)
        return 0;
    n = press_count[button];              /* the counter is cleared by the read,
                                           * which is what INT 33h 0x05 does and
                                           * what the notes warn about ignoring */
    press_count[button] = 0;
    return n;
}

int16_t far mouse_releases(int16_t button)
{
    int16_t n;

    if (button < 0 || button > 2)
        return 0;
    n = release_count[button];
    release_count[button] = 0;
    return n;
}
