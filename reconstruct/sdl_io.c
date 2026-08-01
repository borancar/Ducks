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
#include <string.h>

#include "dos.h"

/* ------------------------------------------------------------------ state */

#define SCALE_DEFAULT 3          /* the window is this many times the mode */

static SDL_Window  *window;
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

void far clear_vram(void)
{
    memset(fb_back, 0, (size_t) screen_width * screen_height);
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
void far palette_fade_step(int16_t arg)
{
    int i;

    (void) arg;
    if (!fade_direction)
        return;
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

void far blit_rows(desc_t far *desc, rect_t rect, int16_t srcrow)
{
    int16_t row, x;

    for (row = rect.top; row <= rect.bottom; row++) {
        const uint8_t *src = desc->rows[srcrow + row] + current_plane;
        uint8_t       *dst = fb_back + (size_t) row * screen_width;

        for (x = rect.left; x <= rect.right; x++)
            dst[x] = src[x * 4];
    }
}

void far blit_rows_masked(desc_t far *desc, rect_t rect, int16_t srcrow)
{
    int16_t row, x;

    for (row = rect.top; row <= rect.bottom; row++) {
        const uint8_t *src = desc->rows[srcrow + row] + current_plane;
        uint8_t       *dst = fb_back + (size_t) row * screen_width;

        for (x = rect.left; x <= rect.right; x++) {
            uint8_t px = src[x * 4];
            if (px)
                dst[x] = px;
        }
    }
}

/* TODO: compose_layer, compose_scroll, draw_sprite and outline_sprite are the
 * four that read the game's own data structures, so porting them is a transcription
 * of dos_io.c with the addressing changed and nothing else. Left until the
 * structures they walk - the row tables and the sprite descriptors - are settled,
 * because writing them twice against a guess is worse than writing them once. */

/* ------------------------------------------------------------------- mouse
 *
 * The original asks INT 33h for motion since the last call and for per-button
 * press and release counts. SDL gives the same three things: relative motion, and
 * events that can be counted between polls.
 */

static int16_t press_count[3], release_count[3];
static int16_t rel_x, rel_y;

/* Pump SDL's queue into the counters the three wrappers below hand back. Call it
 * once a frame; the game polls the wrappers many times per frame and expects each
 * to consume what it reports. */
void sdl_pump_input(void)
{
    SDL_Event e;

    while (SDL_PollEvent(&e)) {
        switch (e.type) {
        case SDL_EVENT_MOUSE_MOTION:
            rel_x += (int16_t) e.motion.xrel;
            rel_y += (int16_t) e.motion.yrel;
            break;
        case SDL_EVENT_MOUSE_BUTTON_DOWN:
            if (e.button.button >= 1 && e.button.button <= 3)
                press_count[e.button.button - 1]++;
            break;
        case SDL_EVENT_MOUSE_BUTTON_UP:
            if (e.button.button >= 1 && e.button.button <= 3)
                release_count[e.button.button - 1]++;
            break;
        default:
            break;
        }
    }
}

void far mouse_motion(int16_t far *dx, int16_t far *dy)
{
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
