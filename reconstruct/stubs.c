/* stubs.c - everything the reconstruction calls but has not read out yet.
 *
 * This exists so the thing links and runs. Each stub does the least that lets
 * the caller carry on, and says what the real routine is: an image offset if we
 * know it, and what it would have to do.
 *
 * The gameplay is deliberately absent. run_level() is a no-op that reports
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

/* The four ending screens that have not been read out. cutscene_welcome_home and
 * cutscene_photos are real, in game.c. */
void far cutscene_rocket_space(void)      { }
void far cutscene_rocket_landing(void)    { }
void far cutscene_doorstep(void)          { }
void far cutscene_night_monster(void)     { }

/* ------------------------------------------------------------ the screens */


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


/* --------------------------------------------------------------- startup */

void far install_int23(void far *h)       { (void) h; }
void far ctrl_break_handler(void)         { }
/* 0x14974. The original's is the Sound Blaster probe - reset the DSP at the
 * base address BLASTER names, and then the XMS check that prints "Free XMS
 * memory: %uk". Its answer gates AUDIO SETTINGS and whether sound_init runs.
 *
 * Here the only question is whether a device opens, and sound_init answers that
 * by leaving sound_state clear when it does not. So this says yes and lets it. */
int16_t far detect_hardware(void)         { return 1; }

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

    /* The count the original keeps at [0x20ad], which egg_load_all loops
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
/* 0x088fa is level_load, in game.c */
void far f_09329(void)                    { }
void far f_0becb(void)                    { }
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

void far f_11bee(void far *name, int16_t egg) { (void) name; (void) egg; }
int16_t far f_14e88(void far *fp)         { (void) fp; return 0; }

/* The palette the DAC loops upload, and the washed copy the blink alternates
 * with: 0x10e1 and 0x0dad. palette_build fills the first; nothing fills the
 * second yet. */
uint8_t palette_stored[768];
uint8_t palette_washed[48];


/* ------------------------------------- stubbed because a demo does not run them
 *
 * The goal for now is run_level(1) - a demo, which needs no input. These are the
 * routines it does not reach, so they can wait; each says how we know, because
 * the two kinds of evidence are not equally good.
 *
 * "By reading" is proof. "Not observed" is not, and it misled us once already:
 * every capture we had was mid-level, so run_level's setup and teardown never
 * appeared in a trace taken from one, and 0x0881d, 0x0d5c5, 0x0615a, 0x04f4b and
 * 0x0a85f all looked dead. Letting the menu time out into a demo and tracing
 * that shows 0x0a85f alone runs 520 times. Five of twelve wrong - so the ones
 * below say so instead of pretending.
 *
 * Each complains the first time it is called. A stub that silently does nothing
 * is how a demo quietly stops matching the original.
 */
static void demo_stub(const char *what, int *said)
{
    if (*said)
        return;
    *said = 1;
    fprintf(stderr, "  [stub] %s was reached - it was stubbed on the "
                    "understanding that a demo never gets here, so that is "
                    "wrong and the demo is no longer faithful\n", what);
}

/* 0x0cf07, 449 bytes. run_level's PLAYED branch: with its argument zero it is
 * this that reads the input and drives the tool, where a demo takes 0x0d4c2 and
 * the level's own table instead. Off the demo path by reading, not by absence -
 * see docs/notes/run-level.md on the [bp+6] fork. 0x0ce2e (217 bytes) is called
 * only from here, so it goes with it. */
void far played_tool_events(uint8_t far *flash)
{
    static int said;
    (void) flash;
    demo_stub("0x0cf07, the played tool handler", &said);
}

/* 0x0af95, 304 bytes, from 0x0d4fc - which does run, four times a frame, but
 * only reaches this when the selected tool is 0x12, 0x15 or 0x50. The four
 * captures hold tools 0x36, 0x19, 0x0c and 0x0d, and a demo from the menu did
 * not reach it either. Not observed, so not proven: a demo whose level offers
 * one of those three tools will land here. */
void far tool_apply(scene_t far *s, int16_t n)
{
    static int said;
    (void) s; (void) n;
    demo_stub("0x0af95, the tool applicator", &said);
}

/* 0x07955, 71 bytes, called from run_level's frame loop when the level is not
 * paused, and from 0x0799c and 0x0cf07. Not observed in any demo, from a
 * capture or from the start. Not proven. */
void far f_07955(void)
{
    static int said;
    demo_stub("0x07955", &said);
}
