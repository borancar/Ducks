/* stubs.c - everything the reconstruction calls but has not read out yet.
 *
 * This exists so the thing links and runs. Each stub does the least that lets
 * the caller carry on, and says what the real routine is: an image offset if we
 * know it, and what it would have to do.
 *
 * run_level moved to game.c on 2026-08-05 and is a real function: the setup in
 * full, the frame in part. What is left of the gameplay here is the routines it
 * still calls that have not been read, and the ones a demo never reaches, which
 * say so individually below.
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

/* All six endings are in game.c now. */

/* ------------------------------------------------------------ the screens */


/* ------------------------------------------------------------- the eggs */

void far *egg_stream;
void far *current_buffer;

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

/* 0x07a36, 380 bytes: what a tool actually does where it is used. It exists as a
 * byte-compared native in native.py and has not been transcribed, so entity_update
 * and the frame call into this. It complains once rather than silently doing
 * nothing, because a tool that quietly fails is how a demo stops matching. */
/* ------------------------------------------------ unnamed, by image offset */

/* 0x088fa is level_load, in game.c */
void far f_09329(void)                    { }
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
/* Two reasons a routine is missing, and they are not the same claim.
 *
 * `unwritten` means nobody has read it out yet: reaching it is expected and the
 * message says what will not happen. `wrong_about_demos` means it was left out
 * because a demo was believed never to reach it - so reaching one IS the news,
 * and the message says the belief was wrong.
 *
 * These were one function until 2026-08-07, and the bridge footing check
 * announced itself with the demo wording, which was false twice over: it was
 * stubbed for the first reason, and there are demos with bridges. A diagnostic
 * that misstates its own reason is worse than none.
 */
static void unwritten(const char *what, int *said)
{
    if (*said)
        return;
    *said = 1;
    fprintf(stderr, "  [stub] %s is not written yet\n", what);
}

static void wrong_about_demos(const char *what, int *said)
{
    if (*said)
        return;
    *said = 1;
    fprintf(stderr, "  [stub] %s was reached - it was left out on the "
                    "understanding that a demo never gets here, so that "
                    "understanding is wrong and a demo is no longer "
                    "faithful\n", what);
}

/* 0x0ce2e, 217 bytes: what P does when the pause cheat is on. It builds a
 * 320x200 image, draws over the screen and waits on getch. Off the demo path by
 * reading - played_tool_events is its only caller - and behind cheat_state[5]
 * besides, so nothing reaches it without being asked for twice. */
void far pause_screen(void)
{
    static int said;

    unwritten("0x0ce2e, the pause screen", &said);
}

/* 0x149e:0x346: the DSP's rate register, which D toggles between 11000 and
 * 22000. The port opens its device once at the rate sound_init chose, and SDL
 * resamples from there, so there is nothing to reprogram - but the game asking
 * is worth not swallowing. */
void far sound_set_rate(int16_t rate)
{
    static int said;

    (void) rate;
    unwritten("0x149e:0x346, changing the sample rate mid-level", &said);
}


