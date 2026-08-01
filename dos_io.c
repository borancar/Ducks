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

/* ------------------------------------------------------------ video: memory */

/* 0x04d2a. Clears all four planes in one pass by opening the map mask to 0xff
 * first, so each byte written lands in four. 0xfa00 = 64000 bytes of the aperture.
 * The far pointer is reloaded every iteration, which is what dereferencing a
 * global far pointer in the loop body compiles to.
 */
void far clear_vram(void)
{
    uint16_t i;

    outpw(0x3c4, 0xff02);                  /* sequencer 2 (map mask) = 0x0f..0xff */
    for (i = 0; i < 0xfa00; i++)
        ((uint8_t far *) vram)[i] = 0;     /* vram is the far pointer at 0x16f1 */
}

/* 0x05761. One pixel, into the BACK page, and only if it belongs to the plane
 * currently selected - Mode X puts column x in plane x & 3, so a caller runs the
 * whole thing four times and three of those calls do nothing here.
 *
 * The stride is 80 (`y << 6` plus `y << 4`), with no resolution check. Its twin
 * at 0x057a1 is the same routine with a stride of 90, for the 360-pixel mode, and
 * the game swaps the far pointer at [0x53e] rather than testing inside either.
 */
void far plot_pixel(int16_t x, int16_t y, uint8_t colour)
{
    if (current_plane != (x & 3))
        return;
    ((uint8_t far *) vram)[y * 80 + (x >> 2) + page_back] = colour;
}

/* ---------------------------------------------------------- video: the fade */

/* 0x0b10b. The fade state machine, stepped once per frame by whoever is showing a
 * screen. fade_direction of 0 means "not fading"; +1 in, -1 out.
 */
void far palette_fade_step(int16_t arg)
{
    int16_t i, si;

    if (!fade_direction)                   /* nothing armed */
        return;
    if (fade_level == 0 && fade_direction == -1) {
        fade_direction = 0;                /* faded out: disarm and stop */
        return;
    }

    palette_build();                       /* 0x0b0c5 */
    fade_level += fade_direction;          /* `mov al / cwd / add`: signed */

    if (fade_level >= 15) {                /* fully in: hand over the real palette */
        palette_upload();
        fade_direction = 0;
        return;
    }

    /* Otherwise scale each stored component by the level and push it out. This is
     * the loop that was 94% of all port I/O, 768 writes a call. */
    outp(0x3c8, fade_start_colour);
    si = fade_start_colour * 3;
    for (i = si; i < 0x300; i++)           /* 0x0b15f */
        outp(0x3c9, (palette_stored[i] * fade_level) >> 6);

    /* TODO 0x0b177-0x0b284: the tail, including the two 16-colour blink loops at
     * 0x0b1c9 and 0x0b202 that sit behind a flag nothing in this build sets. */
}

/* ------------------------------------------------------------ video: drawing
 *
 * Everything below draws through the current plane, which is why each is called
 * four times per frame and why replacing planar Mode X with flat drawing is the
 * point of the exercise. All are replaced natively and byte-compared, so
 * native.py holds a verified description of each while these bodies are read out.
 */

/* 0x05c09. Blits a run of rows from a decoded image. The source is a table of far
 * pointers, one per row, indexed by the row counter and offset by the current
 * plane - so the four passes read four interleaved columns of the same source.
 *
 * Note it checks [0x4fe] at the top, unlike plot_pixel: this one does handle both
 * resolutions itself.
 */
void far blit_rows(desc_t far *desc, viewport_t clip, int16_t flags)
{
    int16_t row;

    if (!video_mode) {
        /* TODO 0x05c1b-: the 320-wide path. */
    }
    for (row = clip.top; row < clip.bottom; row++) {
        uint8_t far *src = desc->rows[row][current_plane];   /* +0/+2 far ptr */
        uint8_t far *dst = &((uint8_t far *) vram)[row * 80 + page_back];
        /* TODO 0x05c4a-0x05d39: the inner copy, its clipping against the
         * viewport, and the second resolution's 90-byte stride. */
    }
}

/* 0x063d6. One sprite, again filtered to the current plane. The sprite table is
 * indexed with a stride of 0x0e, and the clip rectangle arrives by value.
 *
 * TODO 0x06417-0x065f0: the row loop, the mask handling and the shadow path -
 * the last of which is only reached when an entity of type 0x0f or 0x10 precedes
 * another, and has never been observed to run.
 */
void far draw_sprite(sprite_t far *table, int16_t index, viewport_t clip,
                     int16_t x, int16_t y, int16_t flags)
{
    /* TODO: read out. */
}

/* 0x0bb3b. A number as sprites: glyph 0x71 + digit from the same table the
 * entities use, 12 pixels apart, least significant digit first. Fixed width with
 * no leading-zero suppression, so a score of nothing is six noughts.
 */
void far draw_number(int16_t value, int16_t x, int16_t y, viewport_t far *clip,
                     int16_t flags, int16_t digits)
{
    int16_t i, glyph;

    for (i = digits - 1; i >= 0; i--) {    /* `dec ax` then count down */
        glyph = 0x71 + (value % 10);       /* idiv by 10, remainder + 0x71 */
        draw_sprite(sprite_table, glyph, *clip, x + i * 12, y, flags);
        value /= 10;
    }
}

/* 0x05ac2. blit_rows with transparency: a source byte of zero leaves the
 * destination alone. Same argument layout as blit_rows, and the destination
 * rectangle arrives as a verbatim copy of a four-word record - first row, last
 * row, first x, last x.
 *
 * This is what draws the three flashing panels in the in-game frame.
 */
void far blit_rows_masked(desc_t far *desc, rect_t rect, int16_t srcrow)
{
    int16_t row, x;

    for (row = rect.top; row <= rect.bottom; row++) {
        uint8_t far *src = desc->rows[srcrow + row][current_plane];
        uint8_t far *dst = &((uint8_t far *) vram)[row * 80 + page_back];
        for (x = rect.left; x <= rect.right; x += 4)
            if (src[x >> 2])                   /* zero is transparent */
                dst[x >> 2] = src[x >> 2];
    }
    /* TODO 0x05ac2-0x05c08: the clipping and the second resolution, not read;
     * the shape above is native.py's, which is byte-compared against this. */
}

/* 0x05d3a. The menu and between-level compositor. Takes no arguments at all -
 * every input is a global:
 *
 *   [0x16f5]  far ptr -> array of far row pointers, foreground
 *   [0x170b]  far ptr -> array of far row pointers, background
 *   [0x16f1]  destination, plus [0x1727] for the row offset
 *   [0x538]   width limit          [0x53a]  height
 *   [0x177d]  x scroll             [0x177f] y scroll
 *   [0x1729]  wrap mask, x         [0x172b] wrap mask, y
 *
 * Per pixel: take the foreground byte, and where it is zero fall through to the
 * background tile. Column steps by 4, so one call fills a single plane.
 */
void far compose_layer(void)
{
    int16_t row, x;

    for (row = 0; row < layer_height; row++) {          /* [0x53a] */
        uint8_t far *fg  = fg_rows[row];
        uint8_t far *bg  = bg_rows[(row + scroll_y) & wrap_y];
        uint8_t far *dst = &((uint8_t far *) vram)[row * 80 + dest_row];
        for (x = current_plane; x < layer_width; x += 4) {
            uint8_t px = fg[x];
            dst[x >> 2] = px ? px : bg[(x + scroll_x) & wrap_x];
        }
    }
}

/* 0x05dc4. compose_layer scrolled, and the one routine in the game with code that
 * had never executed until level 80: the background warp the v1.2 changelog
 * mentions.
 *
 * When background_warp is set, each row's x displacement comes from the 32-entry
 * table at warp_table, starting at warp_phase and stepped by warp_step per row -
 * and the phase is re-masked to 0x1f **every row**, so it is not an arithmetic
 * progression and cannot be flattened to `table[(phase + row * step) & 0x1f]`.
 */
void far compose_scroll(int16_t scroll_x, int16_t scroll_y)
{
    int16_t row, x, phase = warp_phase;

    for (row = 0; row < layer_height; row++) {
        int16_t dx = scroll_x;

        if (background_warp) {                 /* [0x2022] */
            dx += warp_table[phase & 0x1f];
            phase = (phase + warp_step) & 0x1f;   /* re-masked every row */
        }
        {
            uint8_t far *fg  = fg_rows[(row + scroll_y) & wrap_y];
            uint8_t far *bg  = bg_rows[(row + scroll_y) & wrap_y];
            uint8_t far *dst = &((uint8_t far *) vram)[row * 80 + dest_row];
            for (x = current_plane; x < layer_width; x += 4) {
                uint8_t px = fg[(x + dx) & wrap_x];
                dst[x >> 2] = px ? px : bg[(x + dx) & wrap_x];
            }
        }
    }
    /* TODO: the exact source indexing is native.py's, which is byte-compared
     * against this routine on every call - except the warp branch, which had
     * never run until 2026-08-01 and is still unverified. See the root README. */
}

/* 0x065f1. Halo a sprite: a colour-0 pixel above, below, left and right of each
 * of its non-zero pixels. Draws the outline around collected items on the HUD.
 *
 * It plots through the callback at [0x53e] rather than writing spans, so - like
 * the particle loop - only pixels whose x & 3 matches the selected plane land.
 *
 * Two quirks are faithful rather than tidy: the vertical clip insets by a row at
 * each end, and clip[+4] is added to x *after* the source offsets are worked out,
 * which shifts the sprite horizontally and therefore also changes which plane each
 * pixel belongs to.
 */
void far outline_sprite(int16_t far *index, int16_t x, int16_t y,
                        sprite_t far *table, viewport_t far *clip)
{
    int16_t row, col;

    for (row = clip->top + 1; row < clip->bottom - 1; row++)      /* the inset */
        for (col = 0; col < table[*index].width; col++)
            if (sprite_pixel(table, *index, col, row)) {
                plot(x + col + clip->shift_x, y + row - 1, 0);    /* clip[+4] */
                plot(x + col + clip->shift_x, y + row + 1, 0);
                plot(x + col - 1 + clip->shift_x, y + row, 0);
                plot(x + col + 1 + clip->shift_x, y + row, 0);
            }
}

/* 0x0d757. The HUD's number drawer. Same digit layout as draw_number - glyph
 * 0x71 plus the digit, 12 pixels apart, least significant first, no leading-zero
 * suppression - but with the clip, sprite table and colour fixed, and glyph 0x70
 * drawn behind each digit first. That backdrop is the visible difference between
 * the HUD's numbers and the in-game frame's.
 *
 * Never observed to run: it is the one hooked address whose correctness rests
 * only on the disassembly.
 */
void far draw_number2(int16_t value, int16_t digits, int16_t x, int16_t y)
{
    int16_t i;

    for (i = digits - 1; i >= 0; i--) {
        draw_sprite(sprite_table, 0x70, hud_clip, x + i * 12, y, 0);  /* the tile */
        draw_sprite(sprite_table, 0x71 + (value % 10),
                    hud_clip, x + i * 12, y, 0);                      /* over it */
        value /= 10;
    }
}

/* 0x057a1. plot_pixel with a stride of 90 - the 360-pixel mode. The game swaps
 * the far pointer at [0x53e] between this and 0x05761 when the resolution
 * changes, rather than testing inside either. Resolving that pointer by its
 * offset word alone recognises neither, because the segment it is stored with is
 * not the one image offsets are measured from.
 */
void far plot_pixel_wide(int16_t x, int16_t y, uint8_t colour)
{
    if (current_plane != (x & 3))
        return;
    ((uint8_t far *) vram)[y * 90 + (x >> 2) + page_back] = colour;
}

/* -------------------------------------------------- still to read out
 *
 * TODO. Both are replaced natively and byte-compared, so native.py is a verified
 * description while the disassembly is turned into C here. Both are arguably game
 * rather than I/O and may belong in game.c once they are read:
 *
 *   0x0ab09  particles      plots through the [0x53e] callback, so it is
 *                           plane-filtered the same way outline_sprite is
 *   0x0aba5  draw_entities  five layers per call, ~34,000 calls a session, and
 *                           the shadow path inside it has never been seen to run
 */
