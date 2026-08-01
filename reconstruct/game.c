/* game.c - code segment 0x04ca, which is the whole game.
 *
 * Reconstructed from Ducks.unpacked.exe. Not compiled and not run: every
 * function carries the image offset it was read from, so any line can be checked
 * against the disassembly. Names ending in a comment saying "unnamed" are ours
 * only in the sense that we have not identified the routine.
 *
 * One file per code segment, because that is the division the binary actually
 * proves - 0x04ca0-0x14620 is a full 64 KB and holds main, the menus, the
 * cutscenes and the drawing. Whether the original author split it across several
 * .c files cannot be recovered from far calls; see README.md.
 *
 * Functions are in address order, which within a module is source order.
 *
 * Sources, all under docs/notes/: entry-points.md for main and the screen
 * players, menu-loop.md for the menu, homecoming-sequence.md for the ending,
 * episode-index.md for the index it reads. The root README for page_flip.
 */

/* ------------------------------------------------------------------ types */

/* What run_screen (0x0c716) returns. Offsets are the ones the code indexes; the
 * record is longer than this and the rest has not been read. */
typedef struct {
    char          pad0[4];
    menu_t far   *submenu;      /* +4/+6: where action 18 points the menu */
    int           action;       /* +8:    the action code, 1..20 */
    char          pad1;         /* +0xa */
    unsigned char param;        /* +0xb:  episode ordinal, readme section, demo */
} record_t;

/* The episode index built at startup from MAIN.EGG; four 14-byte records. */
typedef struct {
    char far *name;             /* +0:    decoded, "TRAINING LEVELS" plainly */
    int       first;            /* +4:    first level */
    int       egg;              /* +6:    which egg file the levels are in */
    int       last;             /* +8:    last level */
    int       ordinal;          /* +0xa */
    int       terminator;       /* +0xc:  set only on the last record */
} episode_t;

extern episode_t far *episode_index;    /* [0x20ba] */
extern int            episode_count;    /* [0x20c2] */
extern menu_t         main_menu;        /* ds:0x1916, what main passes in */
extern menu_t         menu_1989;        /* after starting, saving or loading */
extern menu_t         menu_1c3b;        /* after a resolution change */

/* ------------------------------------------------------- 0x04d4b: page_flip
 *
 * Replaced natively: the retrace wait below was 94% of all port I/O, ~1836 reads
 * of 0x3da per flip, each one a Python callback.
 */
void far page_flip(void)
{
    delay(0x1f - game_speed);              /* [0x1fd4]; 31 is no delay at all */
    while (inp(0x3da) & 1)                 /* wait for display enable to fall */
        ;
    swap(&page_front, &page_back);         /* [0x1725], [0x1727] */
    outpw(0x3d4, (hi << 8) | 0x0c);        /* CRTC start address high */
    outpw(0x3d4, (lo << 8) | 0x0d);        /* ... and low */
    while (!(inp(0x3da) & 8))              /* wait for vertical retrace */
        ;
    flip_phase = (flip_phase + 1) % 10;    /* [0xd61] */
}

/* ------------------------------------------------ 0x051b7: close_egg_files */
void far close_egg_files(void)
{
    while (egg_file_count--) {             /* walked backwards, stride 0x17 */
        fclose(egg_files[egg_file_count].fp);
        free(egg_files[egg_file_count].block);      /* the pointer at +8 */
    }
}

/* ------------------------------------------------------ 0x06869: input_poll
 *
 * Takes the resolution because the game keeps the cursor position itself: INT 33h
 * is only ever asked for relative motion, so the position is a running total and
 * has to be bounded. Kept as 32-bit so a fast drag cannot wrap it.
 */
void far input_poll(int w, int h)
{
    mouse_motion(&mouse_dx, &mouse_dy);    /* 0x0675b - the only two arguments */

    mouse_x += (long) mouse_dx;
    mouse_y += (long) mouse_dy;
    if (mouse_x > w - 1) mouse_x = w - 1;  /* and clamped at 0 below */
    if (mouse_y > h - 1) mouse_y = h - 1;

    /* Buttons go through [0x20e4]/[0x20e6]/[0x20e8], which say which INT 33h
     * button index means what - the mapping is data, not code. */
    ...
}

/* ---------------------------------------------- 0x0b52f: show_resource_loop
 *
 * show_splash's sibling: the same fade in, hold, fade out, but from a global
 * viewport and counting down rather than up. Holds a four-plane loop; not native.
 */
void far show_resource_loop(desc_t far *desc, int frames)
{
    int si = frames, plane;

    fade_direction = 1;  fade_start_colour = 0;
    palette_build();                                   /* 0x0b0c5 */
    do {
        input_poll(320, 200);
        if (si == 0 || last_key || [0x18e5])
            fade_direction = 0xff;                     /* = -1, fade out */
        si -= (frames > 0);
        for (plane = 0; plane < 4; plane++) {
            set_plane(plane);
            blit_rows(desc, viewport_1769);            /* 20 bytes by value */
        }
        page_flip();
        palette_fade_step(0);
    } while (fade_level != 0);                         /* [0x1798] */
}

/* ------------------------------------------------ 0x0c156: egg_load_pass_0x48 */
void far egg_load_pass_0x48(void)
{
    char scratch[0x302];
    int  i, saved;

    set_buffer(&scratch[0]);               /* publish our own stack buffer */
    saved = [0x54d];  [0x54d] = 4;
    for (i = 0; i < egg_file_count; i++)   /* [0x20ad] */
        egg_load_one(0, 0x48, i);          /* 0x0c0c2 */
    [0x54d] = saved;
    set_buffer(ds_13f1);

    /* It also *draws* the version and credits page, through show_resource_loop,
     * and holds it until a key - the wait that read as a hang in one session. */
}

/* --------------------------------------------------- 0x0c1ad: show_resource */
void far show_resource(char type /* 0x4d */, char index, int frames, int x /* 0xff */)
{
    char   scratch[0x316];
    desc_t desc;

    set_buffer(&scratch[0]);
    clear_vram();
    if (resource_load(&desc, type, index, 0, 1, x, 1)) {   /* 0 on failure */
        show_resource_loop(&desc, frames);
        resource_release(&desc);
    }
    set_buffer(ds_13f1);
}

/* ------------------------------------------- 0x0f825: cutscene_welcome_home
 *
 * One of the six ending screens; the others have the same shape with different
 * resource ids. Each draws into both video pages, which is the two-iteration
 * outer loop, and each holds its own four-plane loop.
 */
void far cutscene_welcome_home(void)
{
    desc_t desc;
    int    page, plane;

    if (!resource_load(&desc, 0x4d, 0x36, 0, 0, 0xff, 1))   /* the banner */
        return;
    clear_vram();
    palette_build();
    for (page = 0; page < 2; page++) {
        for (plane = 0; plane < 4; plane++) {
            set_plane(plane);
            blit_rows(&desc, viewport_1769);
        }
        page_flip();
        if (page == 0)
            palette_upload();
    }
    f_04dcd(150);                          /* the hold - unnamed */
    resource_release(&desc);
}

/* ----------------------------------------------- 0x0f913: cutscene_photos */
void far cutscene_photos(void)
{
    desc_t desc;
    int    id, page, plane, i;

    for (id = 0x3a; id <= 0x3c; id++) {    /* three polaroids, one per screen */
        if (!resource_load(&desc, 0x4d, id, 0, 0, 0xff, 1))
            continue;
        clear_vram();
        outp(0x3c8, 0);                    /* the whole DAC to white: the flash */
        for (i = 0; i < 255; i++) {
            outp(0x3c9, 0x3f);  outp(0x3c9, 0x3f);  outp(0x3c9, 0x3f);
        }
        sound_play_guarded(0x68, 1);
        fade_level = 0;  fade_direction = 1;
        for (page = 0; page < 2; page++) {
            for (plane = 0; plane < 4; plane++) {
                set_plane(plane);
                blit_rows(&desc, viewport_1769);
            }
            page_flip();
        }
        for (i = 0; i < 150; i++) {        /* hold, fading in */
            page_flip();
            f_0f8bd();
        }
        resource_release(&desc);
    }
}

/* ------------------------------------------------------- 0x102d7: show_splash
 *
 * (image, frames): fade an image in, hold for `frames` or until a key, fade out.
 * Holds a four-plane loop of its own. main's first call draws nothing, and that
 * is correct - the source is an allocated but empty 320x24 bitmap.
 */
void far show_splash(void far *image, int frames)
{
    viewport_t a, b;
    int        si = 0, di = frames, plane;

    make_rect(&a, 80, 104, [0x53c], [0x53c] + 320);
    make_rect(&b, 320, 24);
    f_0615a(1, 0x53, &loc, 0xff);  f_056f7(0);
    f_0b5cf(image, &loc, 0x12, &b, 0, 0x1c);       /* decodes it into b */
    clear_vram();
    fade_direction = 1;  fade_start_colour = 0;
    do {
        input_poll(320, 200);
        if (si == di || last_key || [0x18e5])      /* timeout, key or button */
            fade_direction = 0xff;
        si++;
        for (plane = 0; plane < 4; plane++) {
            set_plane(plane);
            blit_rows(&b, a, 0);
        }
        page_flip();
        palette_fade_step(0);
    } while (fade_level != 0);
    resource_release(&b);  f_088b3(&loc);
}

/* ------------------------------------------------ 0x11c75: episode_end_gate
 *
 * Finds the episode whose last level is the one just finished, shows that
 * episode's own splash - "That's enough training", "EPISODE COMPLETED!" - and
 * returns that record's terminator flag. So it answers "was that the FINAL
 * episode", which is what gates the homecoming.
 */
int far episode_end_gate(int level, int egg)
{
    int i, flag = 0;

    for (i = 0; i < episode_count; i++) {
        if (episode_index[i].last != level)  continue;
        if (episode_index[i].egg  != egg)    continue;

        sound_play_guarded(0x1a, 1);
        show_splash(res->splash_bc, 100);           /* the episode's own screen */
        f_11bee(episode_index[i].name, egg);        /* draws it - unnamed */
        flag = episode_index[i].terminator;         /* +0xc: the answer */
    }
    return flag;
}

/* --------------------------------------------------- 0x1271b: the menu loop
 *
 * Draws a screen, and handles the two action codes that mean "keep the menu up":
 * the idle timeout, and a request to play a demo. Everything else it hands back
 * to game_main. The two branches below are the same code twice in the original.
 */
record_t far *menu_screen_driver(menu_t far *menu, void far *a, int b)
{
    int         leave;                             /* di */
    record_t far *r;

    do {                                           /* 0x12723 */
        r = run_screen(menu, a, b);                /* 0x12733 -> 0x0c716 */
        leave = 0;

        switch (r->action) {
        case 0x0a:                                 /* idle: 500 frames untouched */
            if (attract_choice) {                  /* [0x21ae] */
                show_attract_screen(400);          /* 0x0b9fc */
            } else if (pick_random_demo()) {       /* 0x126db: rand() % [0x2038] */
                f_088fa();
                free(buf_200f);
                [0x18f5] = 5;  [0x1ffc] = 0;
                saved = [0x509];  [0x509] = 0;     /* switched off for the demo */
                in_game_frame(1);                  /* 0x1279d - it IS the game */
                [0x509] = saved;
                free(buf_2043);  free(buf_203f);  free(buf_203b);
                release_sounds();
            } else {
                show_splash("DEMO MISSING", 100);  /* 0x1287e, DGROUP+0x26bd */
            }
            attract_choice = !attract_choice;      /* 0x127ee: screen, demo, ... */
            break;

        case 0x15:                                 /* play the demo named */
            if (load_demo(r->param)) {             /* 0x1240f */
                /* ... byte for byte the same as the branch above ... */
                in_game_frame(1);                  /* 0x1283a */
            } else {
                show_splash("DEMO MISSING", 100);
            }
            break;

        default:
            leave = 1;                             /* 0x1288d */
            break;
        }
    } while (!leave);                              /* 0x12892 */

    return r;                                      /* dx:ax */
}

/* --------------------------------------------------------- 0x13519: set_mode_x
 *
 * BIOS 13h through int86, then unchain it in place. The mode number comes from
 * [0x4fe], which is what makes VIDEO SETTINGS > RESOLUTION work.
 */
void far set_mode_x(int mode)
{
    set_bios_mode(mode);                   /* 0x04d04 -> int86(0x10) */
    outp(0x3c4, 4);  outp(0x3c5, 6);       /* sequencer memory mode: chain-4 off */
    outp(0x3d4, 0x14);  outp(0x3d5, 0);    /* CRTC underline = 0 */
    outp(0x3d4, 0x17);  ...                /* CRTC mode control */
}

/* ------------------------------------------------- 0x13676: the game itself
 *
 * A menu interpreter with the game as one of its cases. `menu` is the current
 * menu descriptor and most action codes only change it. The switch compiles to
 * the jump table at 0x13a70, twenty words, codes 1..20.
 */
void far game_main(menu_t far *menu)               /* main passes &main_menu */
{
    char          buf[0x326];
    int           running = 1;                     /* si, set at 0x1367e */
    int           i;
    record_t far *r;

    do {
        r = menu_screen_driver(menu, &buf[0], 1);  /* 0x1368f, five words */

        switch (r->action) {                       /* 0x1369e, table at 0x13a70 */

        case 18:  menu = r->submenu;      break;   /* a submenu is data, not code */
        case 4:   running = 0;            break;   /* QUIT */
        case 14:  register_screen();      break;   /* 0x13096 */
        case 7:   show_readme_section(r->param);   break;
        case 5:   save_game_screen();  menu = &menu_1989;  break;   /* 0x13298 */
        case 6:   load_game_screen();  menu = &menu_1989;  break;   /* 0x12951 */
        case 3:   high_score_screen();  f_0f55c();
                  menu = &main_menu;                       break;

        case 20:                                   /* 0x136fe: MOUSE BUTTONS */
            if (button_map_a == button_map_b || button_map_a == button_map_c
                || button_map_b == button_map_c) {
                /* the duplicate-assignment case; body not read */
            }
            break;

        case 12:                                   /* 0x136cb: RESOLUTION */
        case 13:
            clear_vram();
            set_mode_x(r->action == 13);
            dac_set_black(0, 0);
            menu = &menu_1c3b;
            break;

        case 1:                                    /* START: unpack the episode */
            [0x1ffc] = 0;  [0x1ffa] = 0;           /* 0x1377b */
            menu = &menu_1989;
            i = r->param;                          /* the episode ordinal */
            level_attempted   = episode_index[i].first;
            episode_egg_index = episode_index[i].egg;
            shareware_limit   = egg_files[episode_egg_index].limit;   /* +0x10 */
            /* FALL THROUGH into the play loop */

        case 2:                                    /* 0x137f6: play, tally, repeat */
            for (;;) {
                if ([0x1ffc]) {
                    sound_play_guarded(0x29, 1);
                    show_splash(res->splash_a0, 200);
                }
                if (f_1102a([0x21a3]))             /* a screen; non-zero leaves */
                    break;
                [0x18f5] = 2;

                if (shareware_limit < level_attempted      /* 0x13841 */
                    && !registered && ![0x1ffc]) {
                    f_09329();                     /* the refusal - unnamed */
                    egg_load_one(0xfc, 0x48, 0xff);
                    menu = &main_menu;
                    high_score_screen();  f_0f55c();
                    break;
                }

                if (!in_game_frame(0)) {           /* 0x1387e: the run ended badly */
                    [0x21a3] = 0;
                    if (![0x50b]) {
                        --lives;                   /* [0x2034] */
                        sprintf(buf, "%s: %i", res->label_d4, lives);
                        show_splash(buf, 100);
                        release_sounds();
                        if (lives == 0) {          /* GAME OVER */
                            menu = &main_menu;
                            sound_play_guarded(0x16, 1);
                            show_resource(0x4d, 6, 50, 0xff);
                        }
                    }
                    break;
                }

                if ([0x1ffc] || [0x1ffe])          /* 0x1388b, 0x13895 */
                    break;

                [0x21a3] = 1;                      /* the level was completed */
                sound_play_guarded(2, 1);
                show_resource(0x4d, 2, 50, 0xff);  /* the BONUS SCREEN */
                f_0becb();
                /* ... a comparison of [0x2036] against [0x201c], not read ... */

                /* The ending. Only DUCKING HELL - level 80 - passes the gate. */
                if (episode_end_gate(level_attempted, episode_egg_index)
                    && episode_egg_index == 0) {                /* 0x1390d */
                    set_buffer(&buf[0]);
                    cutscene_rocket_space();                    /* id 0x32 */
                    f_147c5(0x4a, [0x1fd3], 0xff);
                    cutscene_rocket_landing();                  /* ids 0x33/0x34 */
                    cutscene_doorstep();                        /* ids 0x37/0x38 */
                    cutscene_welcome_home();                    /* id 0x36 */
                    release_sounds();
                    cutscene_photos();                          /* ids 0x3a-0x3c */
                    f_147c5(0x4a, [0x1fd3], 0xff);
                    cutscene_night_monster();                   /* the animation */
                    release_sounds();
                    dac_set_black(0, 0);
                    input_poll(320, 200);
                    set_buffer(&buf[0]);
                    high_score_screen();  f_0f55c();
                }
                level_attempted++;                 /* 0x139ab */
            }
            break;

        default:                                   /* 8-11, 15-17, 19 */
            break;
        }
    } while (running);                             /* 0x13a66 */
}

/* ------------------------------------------------- 0x13fea: scan_save_slots
 *
 * Takes nothing, returns nothing; its only output is one global. The names are
 * not five constants - save_name holds the template "GAME-.SG" and the loop
 * patches the digit into offset 4.
 */
void far scan_save_slots(void)
{
    int i, v;

    for (i = 1; i < 6; i++) {
        save_name[4] = '0' + i;                    /* [0x21a9] */
        fp = fopen(save_name, "rb");
        if (fp) {
            v = f_14e88(fp);                       /* a value out of the save */
            if (v > max_save_value)                /* [0x2055], the only output */
                max_save_value = v;
            /* two more values are fetched through 0x14f4b and freed: read a
             * string, free it */
            fclose(fp);
        }
    }
}

/* ---------------------------------------------------- 0x140b1: save_settings */
void far save_settings(void)
{
    fp = fopen(settings_name, "wb");               /* ds:[0x21d2] -> settings.dat */
    fwrite(&settings[0], ...);                     /* the word array at [0x4f4],
                                                    * whose first word gates
                                                    * sound_play_guarded */
    fclose(fp);
}

/* ---------------------------------------------------------------- 0x141fe: init
 *
 * The whole startup screen, not merely the key wait it was first named for. Its
 * first act prints "DUCKS v1.21", the first line the program shows at all.
 */
void far init(void)
{
    int i;

    puts("DUCKS v1.21");                           /* DGROUP+0x2808 */
    for (i = 0; i < 3; i++) {                      /* three 22-byte objects */
        init_objects[i] = malloc(22);              /* [0x210c], stride 4 */
        init_objects[i]->w = 316;  init_objects[i]->h = 15;
        f_15388(init_objects[i]);
    }
    /* the remaining banners */

    sound_available = detect_hardware();           /* 0x14974: the sound check,
                                                    * then XMS, then "Free XMS
                                                    * memory: %uk" */
    if (sound_available)
        sound_init(11000);                         /* 0x2af8 = 11000 decimal */

    print_newline();
    set_text_colour(15);
    puts("Press a key to begin...");               /* DGROUP+0x28e7 */
    print_newline();
    do {
        input_poll(320, 200);
    } while (!last_key);                           /* [0x18f6] */
}

/* ------------------------------------------------------------ 0x144d7: main
 *
 * Mapped in docs/notes/entry-points.md by breakpointing a live machine - every
 * frame below was observed rather than read off the listing.
 *
 * `res` is the far pointer at 0x1894:0 that the intro indexes for its splashes;
 * the field names here are the offsets it uses.
 */
void far main(void)
{
    install_int23(&ctrl_break_handler);      /* 0x144e0, far 04ca:f82d */

    init();                                  /* 0x144e9 */
    set_mode_x(video_mode);                  /* 0x144f1 - [0x4fe], not a literal */
    dac_set_black(0, 0);                     /* 0x14502 - black from here on */
    input_poll(320, 200);                    /* 0x1450f */
    scan_save_slots();                       /* 0x14516 - GAME1.SG..GAME5.SG */

    /* The intro: two screen players, interleaved with sounds. Screen (1) draws
     * nothing - the 320x24 source is allocated but empty, checked in the planes. */
    show_splash(ds_28ff, 100);               /* 0x14520 - (1) blank */
    egg_load_pass_0x48();                    /* 0x14527 - and it draws the version
                                              * and credits page, which waits for
                                              * a key */
    sound_play_guarded(0x2b, 1);
    show_resource(0x4d, 5, 50, 0xff);        /* 0x1453f - the Hungry Software logo */
    show_splash(res->splash_9c, 100);        /* 0x1455c - "PRESENTS" */
    sound_play_guarded(0x28, 1);
    show_resource(0x4d, 8, 100, 0xff);       /* 0x14577 - the title */

    if (!registered) {                       /* 0x1457d - [0x548] */
        show_splash(res->splash_f8, 100);    /* 0x1459b - "UNREGISTERED" */
        sound_play_guarded(0x0b, 1);
    }

    game_main(&main_menu);                   /* 0x145b1 - does not return until
                                              * QUIT clears its loop flag */

    /* On the way out. Nobody had seen any of this before it was walked, because
     * it runs only after the menu is quit. */
    if (!registered) {                       /* 0x145b7 */
        show_resource(0x4d, 0x64, 250, 0xff);   /* the gameplay collage */
        show_resource(0x4d, 0x65, 250, 0xff);   /* "World Wide Webbed" */
        show_resource(0x4d, 0x66, 250, 0xff);   /* nothing - not in this egg */
        show_readme_section(2);                 /* HOW TO REGISTER, waits for ESC */
    }
    show_resource(0x4d, 0x67, 250, 0xff);    /* 0x14605 - visit us on the web */
    release_sounds();                        /* 0x1460b - 0x146cd */

    set_bios_mode(3);                        /* 0x14613 - back to text */
    save_settings();                         /* 0x1461a - settings.dat */
    close_egg_files();                       /* 0x1461e - fclose and free, each */
    crt_exit();                              /* 0x14621 - lcall 0, 0x1e6b; runs
                                              * with segment base 0, not this
                                              * segment's */
}
