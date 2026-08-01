/* egg.c - reading EGGS/MAIN.EGG, which is where every picture in the game lives.
 *
 * The format, worked out from the directory bytes and from the routines that walk
 * it - egg_find_block at 0x05232 and the byte reader at 0x0581c - then checked by
 * decoding resources and looking at them:
 *
 *   file    count      2 bytes, BIG endian. 303 in this egg, which is the number
 *                      the startup banner prints as "303 slices"
 *           directory  `count` entries of 7 bytes:
 *                        +0 type, one ASCII letter - 'M' is a full screen
 *                        +1 unused so far, always 0 here
 *                        +2 index, what show_resource's second argument selects
 *                        +3 offset, 4 bytes LITTLE endian, from the end of the
 *                           directory rather than from the start of the file
 *
 *   resource  width    2 bytes big endian
 *             height   2 bytes big endian
 *             colours  1 byte - how many palette entries follow
 *             palette  3 bytes each, 6-bit DAC values as the game stores them
 *             pixels   chunked, see below
 *
 *   chunk   table      16 bytes: a translation table from nibble to colour index
 *           count      2 bytes big endian - how many PIXELS this chunk covers
 *           data       4 bits a pixel, LOW nibble first, each indexing the table
 *
 * So it is not a general compressor: each chunk says "the next N pixels use only
 * these 16 colours" and spends half a byte on each. A run of sky costs a table and
 * then nothing much; a busy row starts a new chunk. The counter is per pixel, not
 * per byte, and a chunk boundary drops any half-used byte - which is what the
 * reader's [0x20d3] flag is for, reset when a new table is read.
 *
 * Two endiannesses in one file, and they are not a mistake: the counts and sizes
 * are big endian, the directory offsets little. Whoever wrote the packer wrote the
 * sizes by hand and let the compiler write the offsets.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dos.h"

#define MAX_ENTRIES 1024

typedef struct {
    uint8_t  type;
    uint8_t  index;
    uint32_t offset;            /* from the end of the directory */
} entry_t;

static uint8_t *egg;            /* the whole file; 2.4 MB is nothing now */
static size_t   egg_len;
static entry_t  dir[MAX_ENTRIES];
static int      dir_count;
static size_t   data_base;      /* where the directory ends */

static size_t   cursor;         /* the open "stream" every read advances */

/* the decoder's state, which is [0x20ce], [0x20d2], [0x20d3] and [0x20d4] */
static uint8_t  table[16];
static uint32_t chunk_left;
static uint8_t  pending;
static int      have_pending;

/* ---------------------------------------------------------------- opening */

int egg_open(const char *path)
{
    FILE  *f = fopen(path, "rb");
    long   n;
    int    i;

    if (!f)
        return 0;
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    egg = malloc((size_t) n);
    if (!egg || fread(egg, 1, (size_t) n, f) != (size_t) n) {
        fclose(f);
        return 0;
    }
    fclose(f);
    egg_len = (size_t) n;

    dir_count = (egg[0] << 8) | egg[1];          /* big endian, and it is 303 */
    if (dir_count > MAX_ENTRIES)
        dir_count = MAX_ENTRIES;
    for (i = 0; i < dir_count; i++) {
        const uint8_t *e = egg + 2 + i * 7;
        dir[i].type   = e[0];
        dir[i].index  = e[2];
        dir[i].offset = (uint32_t) e[3] | ((uint32_t) e[4] << 8)
                      | ((uint32_t) e[5] << 16) | ((uint32_t) e[6] << 24);
    }
    data_base = 2 + (size_t) dir_count * 7;
    return dir_count;
}

/* ------------------------------------------------------------ the stream */

uint8_t far egg_read_byte(void far *s)
{
    (void) s;
    return cursor < egg_len ? egg[cursor++] : 0;
}

/* Big endian, which is what the header and the chunk counts are. */
int16_t far egg_read_word(void far *s)
{
    int16_t hi = egg_read_byte(s);
    int16_t lo = egg_read_byte(s);

    return (int16_t) ((hi << 8) | lo);
}

/* 0x05232. Seeks the stream to a resource. The original walks the open egg files
 * and sets egg_stream to whichever holds it; there is one egg here, so this is
 * the directory search that was inside that. */
int16_t far egg_find_block(uint8_t type, uint8_t index, int16_t arg)
{
    int i;

    (void) arg;
    for (i = 0; i < dir_count; i++)
        if (dir[i].type == type && dir[i].index == index) {
            cursor = data_base + dir[i].offset;
            return 1;
        }
    return 0;
}

/* --------------------------------------------------------- the decoder */

/* 0x0580b - clears the chunk counter, so the next pixel starts a new chunk. */
void far f_0580b(void)
{
    chunk_left   = 0;
    have_pending = 0;
}

/* 0x0581c - one pixel. */
uint8_t egg_next_pixel(void)
{
    uint8_t b;

    if (chunk_left == 0) {
        int i;

        for (i = 0; i < 16; i++)
            table[i] = egg_read_byte(NULL);
        have_pending = 0;
        chunk_left   = (uint32_t) (uint16_t) egg_read_word(NULL);
    }
    chunk_left--;

    if (have_pending) {
        have_pending = 0;
        return table[pending];
    }
    b = egg_read_byte(NULL);
    pending      = (uint8_t) (b >> 4);         /* the high nibble waits */
    have_pending = 1;
    return table[b & 0x0f];                    /* the low one is used now */
}

/* ------------------------------------------------------------ allocation */

/* 0x05388. The original hands back a row table into a buffer it manages; this
 * allocates one image's worth and hangs it off the descriptor. */
int16_t far alloc_image(void far *d, int16_t a, int16_t b, int16_t c, int16_t e)
{
    desc_t  *desc = (desc_t *) d;
    int16_t  y;

    (void) a; (void) b; (void) c; (void) e;
    if (!desc || desc->w <= 0 || desc->h <= 0)
        return 0;

    desc->rows = calloc((size_t) desc->h, sizeof *desc->rows);
    if (!desc->rows)
        return 0;
    for (y = 0; y < desc->h; y++) {
        desc->rows[y] = calloc((size_t) desc->w, 1);
        if (!desc->rows[y])
            return 0;
    }
    return 1;
}

void far resource_release(void far *d)
{
    desc_t  *desc = (desc_t *) d;
    int16_t  y;

    if (!desc || !desc->rows)
        return;
    for (y = 0; y < desc->h; y++)
        free(desc->rows[y]);
    free(desc->rows);
    desc->rows = NULL;
}
