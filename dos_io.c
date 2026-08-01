/* dos_io.c - the layer that talks to the hardware and to DOS.
 *
 * Split out of game.c for porting: these are the routines a port has to replace,
 * and nothing above them should know what a VGA sequencer or an INT 33h is. The
 * cut is by *what a port must reimplement*, not by anything the binary proves -
 * every function here lives in the same code segment as the game (0x04ca), so
 * this file boundary is ours. See README.md.
 *
 * The line: hardware and DOS primitives here, game logic there. `set_plane`,
 * `page_flip` and the blitters belong here; `input_poll` does not, even though it
 * is about the mouse - it accumulates a position, clamps it to the play area and
 * applies the user's button mapping, which is all game.
 *
 * C99, aimed at eventually building. Every function carries the image offset it
 * was read from.
 */

#include <stdint.h>

/* ------------------------------------------------------------- video: mode */

/* 0x13519. The argument does NOT choose the BIOS mode - 0x13 is always what is
 * asked for. It chooses whether to reprogram the CRTC for the wider mode, which
 * is what VIDEO SETTINGS > RESOLUTION selects through video_mode.
 *
 * The wide path is a 360-pixel Mode X: 0x3c2 takes 0xe7, selecting the 28 MHz dot
 * clock instead of 25 MHz, and CRTC 0x13 - the offset register - takes 0x2d = 45
 * words, so a row is 90 bytes. That is where plot_pixel's sibling at 0x057a1 gets
 * its stride of 90, and why the game swaps the far pointer at [0x53e] rather than
 * testing a resolution inside the routine.
 */
void far set_mode_x(int16_t wide)
{
    set_bios_mode(0x13);                   /* always 0x13, whatever `wide` is */

    outp(0x3c4, 4);    outp(0x3c5, 6);     /* sequencer memory mode: chain-4 off */
    outp(0x3d4, 0x14); outp(0x3d5, 0);     /* underline location = 0 */
    outp(0x3d4, 0x17); outp(0x3d5, 0xe3);  /* mode control: byte addressing */

    if (!wide)
        return;                            /* 320 wide: the standard Mode X */

    outpw(0x3d4, 0x2c11);                  /* unprotect CRTC 0..7 */
    outp(0x3c2, 0xe7);                     /* misc output: the 28 MHz clock */
    outpw(0x3d4, 0x6b00);                  /* horizontal total */
    outpw(0x3d4, 0x5901);                  /* end horizontal display */
    outpw(0x3d4, 0x5a02);                  /* start blanking */
    outpw(0x3d4, 0x8e03);                  /* end blanking */
    outpw(0x3d4, 0x5e04);                  /* start retrace */
    outpw(0x3d4, 0x8a05);                  /* end retrace */
    outpw(0x3d4, 0x2d13);                  /* offset = 45 words = 90 bytes a row */
    outpw(0x3d4, 0x8e11);  outpw(0x3d4, 0x2c11);
    outpw(0x3d4, 0x0d06);  outpw(0x3d4, 0x3e07);
    outpw(0x3d4, 0xea10);  outpw(0x3d4, 0xac11);
    outpw(0x3d4, 0xdf12);
    /* TODO 0x135c3-: a few more CRTC writes follow this one, not read. */
}

/* ------------------------------------------------------------ video: planes */

/* 0x057ee. Mode X puts column x in plane x & 3, so every drawing routine filters
 * on the selected plane and every caller runs the whole thing four times. This is
 * the call that makes a plane current, and the one a flat-drawing port turns into
 * a no-op.
 *
 * Replaced natively. The ordinary --verify harness cannot check it, because that
 * harness diffs the four planes and what this changes is the sequencer: a native
 * that did nothing at all would pass. It is checked by comparing the sequencer
 * state instead - 460 calls, 0 mismatched.
 */
void far set_plane(uint8_t plane)
{
    current_plane = plane;                 /* 0x177d, for anyone who asks */
    outp(0x3c4, 2);                        /* sequencer index: map mask */
    outp(0x3c5, 1 << plane);
}

/* --------------------------------------------------------------- video: DAC */

/* 0x0572a. Writes `count` entries of black from `first` onwards - the index goes
 * to 0x3c8 once and the data port then takes three zeros per entry, because the
 * DAC auto-increments. The tail call is the same routine every fade ends with.
 */
void far dac_set_black(uint8_t first, uint8_t count)
{
    int16_t i;

    outp(0x3c8, first);
    for (i = first; i < count; i++) {      /* signed compare: `cmp ax, si / jg` */
        outp(0x3c9, 0);                    /* r */
        outp(0x3c9, 0);                    /* g */
        outp(0x3c9, 0);                    /* b */
    }
    f_057db();                             /* 0x057db - unnamed; every caller of
                                            * dac_set_black reaches it */
}

/* 0x056d2. The whole 256-entry palette, unscaled, straight out of palette_stored.
 * 768 writes to 0x3c9 per call, which is why this and the fade at 0x0b15f were
 * 94% of all port I/O before both were replaced natively.
 */
void far palette_upload(void)
{
    int16_t i;

    outp(0x3c8, 0);
    for (i = 0; i < 0x300; i++)            /* 0x056e0 */
        outp(0x3c9, palette_stored[i] >> 2);   /* 8-bit store, 6-bit DAC */
}

/* --------------------------------------------------------- video: page flip */

/* 0x04d4b. Replaced natively, and the reason why: the retrace wait spins ~1836
 * reads of 0x3da per flip - 94-95% of all port I/O in every state measured - and
 * it consumes the guest instruction budget that would otherwise draw.
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

/* ------------------------------------------------------------------- mouse
 *
 * There is no INT 33h instruction anywhere in the image: Borland's int86 patches
 * the vector number into a stub it builds on its own stack, so a static search
 * finds nothing. These three wrappers are the game's entire mouse input, which
 * was established by walking the BP chain at interrupt time and confirmed
 * identical in menus and in play.
 */

/* 0x0675b - INT 33h function 0x0b, relative motion since the last call. Note both
 * out-parameters are read from the same register struct the call filled. */
void far mouse_motion(int16_t far *dx, int16_t far *dy)
{
    union REGS r;

    r.x.ax = 0x0b;
    int86(0x33, &r, &r);                   /* 0x0293a, in and out the same struct */
    *dx = r.x.cx;
    *dy = r.x.dx;
}

/* 0x0678e - INT 33h function 0x05, presses of one button since the last call.
 * BX selects the button (0 left, 1 right, 2 middle) and the call clears that
 * button's counter; ignoring BX makes every button behave as one.
 * TODO: read out, currently inferred from the native and from the notes. */
int16_t far mouse_presses(int16_t button)
{
    union REGS r;

    r.x.ax = 0x05;
    r.x.bx = button;
    int86(0x33, &r, &r);
    return r.x.bx;                         /* the count, then cleared */
}

/* 0x067ba - INT 33h function 0x06, releases. Same shape as presses.
 * TODO: read out, currently inferred from the native and from the notes. */
int16_t far mouse_releases(int16_t button)
{
    union REGS r;

    r.x.ax = 0x06;
    r.x.bx = button;
    int86(0x33, &r, &r);
    return r.x.bx;
}

/* ------------------------------------------------------------- still to move
 *
 * TODO: these belong in this file and have not been read out yet. All are
 * replaced natively, so native.py holds a verified description of each.
 *
 *   0x04d2a  clear_vram
 *   0x05761  plot_pixel          stride 80; 0x057a1 is the same with stride 90
 *   0x05ac2  blit_rows_masked
 *   0x05c09  blit_rows
 *   0x05d3a  compose_layer
 *   0x05dc4  compose_scroll      holds the background warp
 *   0x063d6  draw_sprite
 *   0x065f1  outline_sprite
 *   0x0bb3b  draw_number         and 0x0d757 draw_number2
 *   0x0b10b  palette_fade_step   the fade state machine
 */
