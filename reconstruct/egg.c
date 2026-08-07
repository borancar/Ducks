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

#include <dirent.h>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>

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
int             block_open;     /* 0x20b6 - egg_find_block's lock, and
                                 * what egg_table_alloc clears */

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

/* The original has one kind of stream: every one of these is the runtime's fgetc
 * on a FILE *, and the egg is just another open file. The port maps the egg and
 * walks it with a cursor instead, so there are two - and the saved games are read
 * with the same three readers, off a real FILE *.
 *
 * NULL is the egg. That is not a convention invented here: egg_stream is what
 * every caller passes for it, and nothing ever assigns to it. */
uint8_t far egg_read_byte(void far *s)
{
    if (s)
        return (uint8_t) fgetc((FILE *) s);
    return cursor < egg_len ? egg[cursor++] : 0;
}

/* Big endian, which is what the header and the chunk counts are. */
int16_t far egg_read_word(void far *s)
{
    int16_t hi = egg_read_byte(s);
    int16_t lo = egg_read_byte(s);

    return (int16_t) ((hi << 8) | lo);
}

/* 0x04f4b. One string: a big-endian word of length, then that many bytes, each
 * one *more* than the character it stands for - so 'Vtjoh' is 'Using'. The
 * caller frees it. This is where the cipher is undone, and the reason the same
 * shift shows up in the episode index: it is the only string reader there is.
 *
 * The original reads through the runtime's fgetc on the egg's FILE *, so the
 * length word and the bytes come off the same stream position this cursor is. */
char far *far egg_read_string(void far *s)
{
    int16_t  len = egg_read_word(s);
    char far *buf;
    int16_t  i;

    /* A stream that has ended gives 0xff twice, which is -1 as a word, and the
     * original then mallocs nothing and writes the terminator one byte in front
     * of it. That is a heap corruption whether it faults or not, and glibc says
     * so; a length that cannot be right is no string at all. */
    if (len < 0)
        len = 0;
    buf = malloc((size_t) len + 1);

    if (!buf)
        fatal("No room for string", NULL);       /* ds:0x2269 */
    for (i = 0; i < len; i++)
        buf[i] = (char) (egg_read_byte(s) - 1);
    buf[len] = 0;
    return buf;
}

/* The glyph pixels are stored raw, so the original reads them with the runtime's
 * fread(buf, size, n, egg) rather than through the chunk decoder.
 *
 * Both counts are UNSIGNED, as Borland's size_t is. They were int16_t here, and
 * a sound is the one caller that can exceed 32767 - the biggest sample in this
 * egg is 45,818 bytes - so the length arrived negative, the product became
 * astronomical, `cursor + want` wrapped past the guard, and the memcpy either
 * ran wild or clamped to "the rest of the egg" and poured 1.7 MB into a 45 KB
 * buffer. That smashed every allocation after it: the sounds loaded next were
 * overwritten with file bytes, which is why the audio was noise, and the mangled
 * malloc headers surfaced later as "double free or corruption (out)" on the
 * first free after restarting a level.
 *
 * The clamp is written so it cannot overflow either: cursor <= egg_len always,
 * so the subtraction is the safe side to do first. */
void far egg_fread(void far *buf, uint16_t size, uint16_t n)
{
    size_t want = (size_t) size * (size_t) n;

    if (want > egg_len - cursor)
        want = egg_len - cursor;
    memcpy(buf, egg + cursor, want);
    cursor += want;
}

/* 0x0537d. Four instructions: it clears [0x20b6], which egg_find_block sets and
 * then refuses to run against - "File slice already in use" - so it is the lock
 * saying a block is open, not decoder state. Both readers of a whole block, the
 * font and the text pages, end with it. */
void far egg_block_end(void)
{
    block_open = 0;
}

/* 0x05232. Seeks the stream to a resource. The original walks the open egg files
 * and sets egg_stream to whichever holds it; there is one egg here, so this is
 * the directory search that was inside that. */
int16_t far egg_find_block(uint8_t type, uint8_t index, int16_t arg)
{
    int i;

    (void) arg;
    if (block_open)                              /* 0x0524a, and it only warns */
        printf("File slice already in use\n");   /* ds:0x22ae */
    for (i = 0; i < dir_count; i++)
        if (dir[i].type == type && dir[i].index == index) {
            cursor = data_base + dir[i].offset;
            block_open = 1;                      /* 0x0536d */
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

/* 0x05671 */
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

/* =========================================================== opening an egg
 *
 * The original streams every read straight off the FILE it opens here. This
 * port reads the file once into `egg` above and serves from that, so open_egg
 * has to do both: keep the FILE the original keeps, because egg_read_byte takes
 * one and close_egg_files closes it, and hand the path to the buffered reader.
 * That second call is the port's and is marked where it happens.
 */

/* DOS is case-blind and has backslashes; this is neither. The game asks for
 * `EGGS\MAIN.EGG` - open_egg upper-cases the path itself, at 0x05085 - and what
 * is on disk is `Eggs/Main.egg`. So the separators are swapped and each
 * component is matched without regard to case, one directory at a time.
 *
 * Entirely the port's: there is nothing to be faithful to here, because the
 * original's fopen was already looking at a filesystem that did this for it. */
static void join(char *out, size_t max, const char *part)
{
    size_t n = strlen(out);

    if (n == 0 || out[n - 1] == '/')
        snprintf(out + n, max - n, "%s", part);
    else
        snprintf(out + n, max - n, "/%s", part);
}

static int host_path_from(const char *want_in, char *out, size_t max)
{
    char        want[512];
    char       *p, *slash;
    struct stat st;

    snprintf(want, sizeof want, "%s", want_in);
    if (stat(want, &st) == 0) {                /* already right */
        snprintf(out, max, "%s", want);
        return 1;
    }

    p = want;
    out[0] = 0;
    if (*p == '/') {                           /* absolute: keep the root */
        snprintf(out, max, "/");
        p++;
    }

    for (; p && *p; p = slash) {
        DIR           *d;
        struct dirent *e;
        char           here[512];
        int            found = 0;

        slash = strchr(p, '/');
        if (slash)
            *slash++ = 0;
        if (!*p)                               /* "//" or a trailing slash */
            continue;

        snprintf(here, sizeof here, "%s", out);
        join(here, sizeof here, p);
        if (stat(here, &st) == 0) {
            snprintf(out, max, "%s", here);
            continue;
        }

        d = opendir(out[0] ? out : ".");
        if (!d)
            return 0;
        while ((e = readdir(d)) != NULL)
            if (strcasecmp(e->d_name, p) == 0) {
                join(out, max, e->d_name);
                found = 1;
                break;
            }
        closedir(d);
        if (!found)
            return 0;
    }
    return out[0] != 0 && stat(out, &st) == 0;
}

/* The path as the game gives it, then the same path under DUCKS_GAME_DIR. The
 * first is how a shipped copy finds its own Eggs directory beside the binary;
 * the second is how the harnesses do, since they run from the repo root. */
static int host_path(const char far *dos, char *out, size_t max)
{
    char        want[512];
    const char *dir;
    size_t      i;

    for (i = 0; dos[i] && i + 1 < sizeof want; i++)
        want[i] = (dos[i] == '\\') ? '/' : dos[i];
    want[i] = 0;

    if (host_path_from(want, out, max))
        return 1;

    dir = getenv("DUCKS_GAME_DIR");
    if (dir) {
        char rooted[512];

        snprintf(rooted, sizeof rooted, "%s/%s", dir, want);
        return host_path_from(rooted, out, max);
    }
    return 0;
}

/* 0x05005. One record per egg the INI named, and the lock cleared with them.
 * 0x17 is sizeof(egg_file_t) and the original writes it as a literal. */
void far egg_table_alloc(int16_t n)
{
    egg_files = malloc((size_t) n * sizeof *egg_files);
    if (!egg_files)
        fatal(out_of_memory, 0);               /* 0x05036 */
    block_open = 0;                            /* 0x0503c, [0x20b6] */
}

/* 0x05044. Open one egg and fill in its record, or say why not.
 *
 * The path is upper-cased IN PLACE as it is walked, and the walk is also what
 * finds the basename: every backslash moves `name` past it, so what ends up in
 * the record at +4 is `MAIN.EGG` and not `EGGS\MAIN.EGG`. The two jobs share one
 * loop, which is why the upper-casing skips the separator.
 *
 * `slices` is the count the banner prints, and +0x0e is where the data starts:
 * seven bytes a directory entry after the two-byte count.
 */
int16_t far open_egg(char far *path)
{
    char far       *name = path;               /* [bp-4] */
    char far       *p    = path;               /* [bp-8] */
    egg_file_t far *e;
    char            host[512];

    while (*p) {                               /* 0x05096 */
        if (*p == '\\')
            name = p + 1;                      /* 0x0506d - past the separator */
        else
            *p = (char) toupper((unsigned char) *p);   /* 0x05085 */
        p++;
    }

    e = &egg_files[egg_file_count];
    e->fp = host_path(path, host, sizeof host) ? fopen(host, "rb") : NULL;
    if (!e->fp) {                              /* 0x050d8 */
        printf("Can't open file %s\n", path);  /* d+0x229a */
        return 0;
    }

    str_copy(name, &e->name);                  /* 0x0510c, the record's +4 */
    e->slices  = egg_read_word(e->fp);         /* 0x05128 */
    e->data_at = (int16_t) (e->slices * 7 + 2);/* 0x05159 */

    /* The port's, not the original's: its reader is buffered where this one
     * streams, so the same file is handed to egg_open as well. */
    egg_open(host);

    printf("Using file %s - %i slices\n", path, e->slices);   /* d+0x227f */
    egg_file_count++;                          /* 0x05194 */
    return 1;
}
