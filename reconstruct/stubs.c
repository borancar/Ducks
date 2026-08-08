/* stubs.c - everything the reconstruction calls but has not read out yet.
 *
 * This exists so the thing links and runs. Each stub does the least that lets
 * the caller carry on, and says what the real routine is: an image offset if we
 * know it, and what it would have to do. When one is read out it moves to
 * game.c, dos_io.c or sdl_io.c and comes off this list, so the list is also the
 * to-do list.
 *
 * Every routine in the game's own module is now written, so what is left here is
 * three things and no more: two routines behind a cheat or a settings toggle
 * that a demo cannot reach, and one runtime call the port has nothing to do
 * with. Nothing below is on the gameplay path.
 *
 * The file also used to hold real data - the two palette arrays, current_buffer,
 * egg_stream - left over from bring-up. Data that belongs to a module belongs in
 * that module's file, and while it sits here it carries no DGROUP offset and is
 * therefore invisible to test_dgroup.py. See open-dgroup-initialisers.
 */

#include <stdio.h>

#include "dos.h"

/* 0:0x1e94, and outside the game's own segment: the runtime's text attribute,
 * set before the startup banners and the episode index. The port prints those to
 * a host terminal that colours itself, so there is no attribute to set - this is
 * a stub in the sense that it will stay one, not one waiting to be read. */
void far set_text_colour(int16_t c)       { (void) c; }


/* ------------------------------------------ stubbed because nothing reaches them
 *
 * Both of the two below are off the path *by reading* - a named caller, behind a
 * cheat or a settings toggle - and that is the only kind of evidence worth
 * writing down here. "Never observed" is not evidence, and it misled us once:
 * every capture we had was mid-level, so run_level's setup and teardown never
 * appeared in a trace taken from one, and 0x0881d, 0x0d5c5, 0x0615a, 0x04f4b and
 * 0x0a85f all looked dead. Timing the menu out into a demo shows 0x0a85f alone
 * running 520 times. Five of twelve wrong.
 *
 * So each complains the first time it is called, because a stub that silently
 * does nothing is how the port quietly stops matching the original.
 */
static void unwritten(const char *what, int *said)
{
    if (*said)
        return;
    *said = 1;
    fprintf(stderr, "  [stub] %s is not written yet\n", what);
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


