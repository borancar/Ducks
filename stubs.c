/* stubs.c - everything the reconstruction calls but has not read out yet.
 *
 * This exists so the thing links and runs. Each stub does the least that lets
 * the caller carry on, and says what the real routine is: an image offset if we
 * know it, and what it would have to do.
 *
 * The gameplay is deliberately absent. in_game_frame is a no-op that reports
 * "the run ended", so game_main's inner loop falls straight through to the
 * screens either side of it - which is what makes the menus testable on their
 * own.
 *
 * When a routine here is read out, it moves to game.c or dos_io.c and comes off
 * this list. The list is therefore also the to-do list, in dependency order:
 * nothing above it can be trusted until the things it calls are real.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dos.h"

/* --------------------------------------------------------------- gameplay */

/* 0x0d7ee. The in-game frame, four plane loops and the whole of the drawing
 * pipeline. Stubbed: returning 0 tells game_main the run is over, which sends it
 * down the lives-decrement path and back to a menu.
 *
 * This is the largest single unread function in the segment - 4,287 bytes - and
 * two of the four native plane loops live inside it. */
int16_t far in_game_frame(int16_t arg)
{
    (void) arg;
    return 0;
}

int16_t particle_count;
particle_t far *particle_array;
viewport_t hud_clip;

/* The four ending screens that have not been read out. cutscene_welcome_home and
 * cutscene_photos are real, in game.c. */
void far cutscene_rocket_space(void)      { }
void far cutscene_rocket_landing(void)    { }
void far cutscene_doorstep(void)          { }
void far cutscene_night_monster(void)     { }

/* ------------------------------------------------------------ the screens */

void far save_game_screen(void)           { }
void far load_game_screen(void)           { }
void far register_screen(void)            { }
void far high_score_screen(void)          { }
void far show_attract_screen(int16_t f)   { (void) f; }

/* ------------------------------------------------------------- the eggs */

void far *egg_stream;
void far *current_buffer;

int16_t far load_demo(uint8_t index)      { (void) index; return 0; }
int16_t far pick_random_demo(void)        { return 0; }
/* 0x0b9ea. Eighteen bytes in the original: it stores the far pointer at [0x1721]
 * and returns. Real, because resource_load writes the palette through it. */
void far set_buffer(void far *p)          { current_buffer = p; }

/* main runs before anything publishes a buffer, and show_splash writes its
 * sprite set's palette through whatever is current, so start on the fallback. */
void far buffer_init(void)                { current_buffer = default_buffer; }

/* ------------------------------------------------------------- the sound */

void far sound_play_guarded(int16_t id, int16_t mode) { (void) id; (void) mode; }
void far release_sounds(void)             { }
void far sound_init(int16_t rate)         { (void) rate; }

/* --------------------------------------------------------------- startup */

void far install_int23(void far *h)       { (void) h; }
void far ctrl_break_handler(void)         { }
int16_t far detect_hardware(void)         { return 0; }

/* The original opens its eggs during startup and builds an index over them -
 * 0x11657, which prints "Using file EGGS\\MAIN.EGG - 303 slices". Until that is
 * read out, open the one egg here so the resource loader has a file to seek in.
 * DUCKS_GAME_DIR matches the tooling's own environment variable. */
void egg_bringup_open(void)
{
    const char *dir = getenv("DUCKS_GAME_DIR");
    char        path[512];
    int         n;

    snprintf(path, sizeof path, "%s/Eggs/Main.egg", dir ? dir : "../game");
    n = egg_open(path);
    printf("Using file %s - %d slices\n", path, n);

    /* The count the original keeps at [0x20ad], which egg_load_pass_0x48 loops
     * over. One egg here. Without this the pass has nothing to iterate and the
     * version page never draws - which is exactly what it did.
     *
     * The array itself has to exist as well as the count: close_egg_files is
     * real, and it walks one record per count on the way out. */
    egg_file_count = (n > 0) ? 1 : 0;
    egg_files      = calloc(1, sizeof *egg_files);
    egg_files[0].fp = fopen(path, "rb");
    /* The name is what build_episode_index prints and what the demo records are
     * built from, and the original has it from opening the file. Everything else
     * in the record - the kind, whether it contributes, the version, the limit -
     * is filled in for real, by load_animations and build_episode_index. */
    egg_files[0].name = malloc(sizeof "MAIN.EGG");
    strcpy(egg_files[0].name, "MAIN.EGG");
}
void far set_text_colour(int16_t c)       { (void) c; }

/* ------------------------------------------------ unnamed, by image offset */

void far f_04dcd(int16_t n)               { (void) n; }
void far f_0615a(int16_t a, int16_t b, void far *c, int16_t d)
{
    (void) a; (void) b; (void) c; (void) d;
}
void far f_088fa(void)                    { }
void far f_09329(void)                    { }
void far f_0becb(void)                    { }
void far f_0f55c(void)                    { }
void far f_0f8bd(void)                    { }

/* 0x04de6. The fatal error reporter: it puts the screen back into text mode,
 * prints the message and the argument, and exits(1). Only the printing and the
 * exit are read out; the mode restoration in the middle is not. */
void far fatal(const char far *msg, const char far *arg)
{
    if (arg)
        fprintf(stderr, "%s: %s\n", msg, arg);
    else
        fprintf(stderr, "%s\n", msg);
    exit(1);
}

int16_t far f_1102a(int16_t a)            { (void) a; return 0; }
void far f_11bee(void far *name, int16_t egg) { (void) name; (void) egg; }
void far f_147c5(int16_t a, int16_t b, int16_t c)
{
    (void) a; (void) b; (void) c;
}
int16_t far f_14e88(void far *fp)         { (void) fp; return 0; }
void far f_15388(void far *o)             { (void) o; }

/* The palette the DAC loops upload, and the washed copy the blink alternates
 * with: 0x10e1 and 0x0dad. palette_build fills the first; nothing fills the
 * second yet. */
uint8_t palette_stored[768];
uint8_t palette_washed[48];

