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

/* 0x0d7ee, and this is NOT it. run_level is 4,287 bytes and two thirds of it is
 * the frame loop - the spawns, the input, the tools, the collisions, the camera,
 * the four endings - none of which is written yet.
 *
 * What this does instead is draw the level the loader just built and hold it
 * until a key, so that level_load can be looked at rather than only diffed. The
 * plane loop below is the shape of the real one at 0x0e4e3 - the same five
 * scenes in the same back-to-front order, the same viewport, the same
 * compositor - but everything that would make the picture move is absent, and
 * so is the setup at 0x0d7ee that positions the camera and draws the HUD.
 *
 * It is bring-up, like egg_bringup_open below it, and it goes when the real one
 * lands. It returns 0, which tells game_main the run ended. */
int16_t far run_level(int16_t arg)
{
    /* 0x0e4e3 draws them 1, 0, 2, 3, 5 - back to front - and then scene 4, the
     * cursor, only when a person is playing. */
    static const int16_t order[5] = { 1, 0, 2, 3, 5 };
    /* The three fixed sprites on the panel, and the empty slot behind each
     * collected item. draw_sprite takes the index by address. */
    static int16_t slot = 0x2a, s_score = 0x07, s_ducks = 0x4f, s_lives = 0xae;
    desc_t  panel;
    int16_t plane, i;

    /* 0x0d86b, and the first thing the real setup does. The set carries a
     * palette slice of its own, so without it every sprite on the level - the
     * cursor included - draws through whatever colours the menu left behind. */
    sprite_set_load(sprite_set_id, 0x43, &level_sprites, episode_egg_index);

    /* 0x0d868 in the real setup, and leaving it out left the menu behind the
     * level: both pages still hold whatever the last screen drew, and the
     * compositor only writes the rows of the game viewport. */
    clear_vram();

    /* 0x0d87f. The status panel, and the one resource the HUD is made of. */
    panel.rows = NULL;
    resource_load(&panel, 0x4d, 0x21, 0, 1, 0xff, 1);

    /* 0x0d5c5, the last thing the real setup does before the frame: tint the
     * level's palette and publish it. Everything drawn with a colour bias -
     * the HUD numbers, the cursor - depends on it. */
    level_palette_build();

    fade_direction    = 1;
    fade_start_colour = 0;
    palette_build();

    do {
        input_poll(level_w, level_h);
        if (last_key)
            fade_direction = -1;

        /* The cursor is an entity like any other: run_screen puts it where the
         * mouse is and steps its script, and without that it neither moves nor
         * animates. The real frame does this at 0x0e34e and animates all six
         * scenes at 0x0e42d; here it is the cursor and the scenes, which is
         * what makes the ducks flap. */
        cursor_scene.entities[0].x = mouse_x;
        cursor_scene.entities[0].y = mouse_y;
        animate_scene(&cursor_scene);
        for (i = 0; i < 6; i++)
            animate_scene(&scenes[i]);

        for (plane = 0; plane < 4; plane++) {
            set_plane((uint8_t) plane);
            compose_scroll((int16_t) viewport_game.scroll_x,
                           (int16_t) viewport_game.scroll_y);
            for (i = 0; i < 5; i++)
                draw_entities(&scenes[order[i]], viewport_game, 0);
            if (arg == 0)
                draw_entities(&cursor_scene, viewport_game, 0x90);

            /* The HUD, from the plane loop at 0x0d9a2 as native.py has it - the
             * panel, the collected items each with an outline over an empty
             * slot, three labels and three numbers. The real one draws this
             * once into each page at level start and never again; drawing it
             * every frame is this bring-up being simple, not the original. */
            if (panel.rows)
                blit_rows(&panel, viewport_panel, 0);
            for (i = 0; i < tool_count; i++) {
                int16_t x   = 0x82 + i * 16;
                int16_t idx = anim_script[tool_list[i]][0];

                draw_sprite(&slot, x, 0x10, &sprite_table, &viewport_panel, 0x90);
                outline_sprite(&idx, x, 0x10, &sprite_table, &viewport_panel);
                draw_sprite(&idx, x, 0x10, &sprite_table, &viewport_panel, 0x90);
            }
            draw_sprite(&s_score, 0x0d4, 0x23, &sprite_table, &viewport_panel, 0x90);
            draw_sprite(&s_ducks, 0x105, 0x23, &sprite_table, &viewport_panel, 0x90);
            draw_sprite(&s_lives, 0x135, 0x07, &sprite_table, &viewport_panel, 0x90);
            draw_number2(score,      6, 0x080, 0x22);
            draw_number2(duck_count, 2, 0x0e1, 0x22);
            draw_number2(lives,      2, 0x113, 0x22);
        }
        page_flip();
        palette_fade_step(0);
    } while (fade_level != 0);

    resource_release(&panel);
    sprite_set_free(&level_sprites);               /* 0x088b3, as teardown does */
    set_buffer(default_buffer);                    /* 0x0e814, likewise: the
                                                    * level played through
                                                    * level_palette, and the
                                                    * shared buffer goes back */
    return 0;
}

int16_t particle_count;
particle_t far *particle_array;

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

/* 0x1102a, 1,309 bytes: what happens between choosing a level and playing it,
 * and the only caller of level_load. It is **two screens** - the episode intro
 * and an information screen - and neither is written here, so the port goes
 * straight from the menu into the level. That is a missing pair of screens, not
 * a design decision.
 *
 * What is here is the one thing about the routine that is established: it loads
 * the level. Returning 0 means "carry on into the level"; the real one returns
 * non-zero to leave. */
int16_t far f_1102a(int16_t a)
{
    desc_t  intro;
    int16_t i, ordinal = 0;

    (void) a;

    /* 0x1102a builds **two** screens. This is the first: the level's map over a
     * picture, with the status line under it, faded in and held until a key.
     * The second - the instructions, with the wavy title - only exists when the
     * level carries a 0x44 block, and is not written: it needs 0x103e2 (1,082
     * bytes) to lay the text out. Neither is the EPISODE screen that comes
     * before both, which is 0x10c06 (1,037 bytes), also not written.
     *
     * The order here is the original's: load the level (0x1108b), load the
     * picture (0x110d7), draw the map into it (0x11131), the status line
     * (0x1123d), then the fade loop (0x11280). The picture is resource 0x4a at
     * the episode's ordinal with its own colours at 0x80, and 0x4d:1 when the
     * episode has none - which in this egg is always, since type 0x4a has no
     * blocks in it at all.
     *
     * Not here: the level's name over the picture when [0x507] is set, and the
     * collected tools at y=0xb4, which need 0x7259 (323 bytes, unwritten). */
    /* 0x1105f. The episode intro, when the level about to be played is an
     * episode's first - which is the gate inside it, not here. The original
     * picks between this and 0x10c06's level picker on [0x507]; the picker is
     * not written, and normal play does not reach it. */
    episode_intro();

    level_load();                                  /* 0x1108b */

    i = episode_for_level();                       /* 0x10ba4 */
    ordinal = (i == 0xff) ? 0xff : episode_index[i].ordinal;

    intro.rows = NULL;
    if (resource_load(&intro, 0x4a, (uint8_t) ordinal, 0x80, 1,
                      episode_egg_index, 0)
        || resource_load(&intro, 0x4d, 1, 0x80, 1, 0xff, 0)) {
        level_map_draw(&intro);                    /* 0x0b284 */
        draw_level_status(&intro);                 /* 0x0b739 */

        fade_start_colour = 0x10;                  /* 0x110aa */
        fade_direction    = 1;
        fade_level        = 0;
        dac_set_black(0x10, 0);                    /* 0x1127a */
        do {                                       /* 0x11280 */
            int16_t plane;

            input_poll(0x140, 0xc8);
            if (fade_direction == 0 && (last_key || g_18e5)) {
                fade_direction    = -1;
                fade_start_colour = 0;
            }
            for (plane = 0; plane < 4; plane++) {
                set_plane((uint8_t) plane);
                blit_rows(&intro, viewport_screen, 0);
            }
            page_flip();
            palette_fade_step(0);
        } while (fade_level != 0);

        resource_release(&intro);
    }
    return 0;
}
void far f_11bee(void far *name, int16_t egg) { (void) name; (void) egg; }
int16_t far f_14e88(void far *fp)         { (void) fp; return 0; }
void far f_15388(void far *o)             { (void) o; }

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
