/* sound.c - the sound module, code segments 0x1462 and 0x149e.
 *
 * Two segments in the original, one file here, because the split between them
 * is the game's own: 0x1462 is what the game calls - load this sound, play it,
 * let them all go - and 0x149e is the mixer and the card underneath. Only the
 * card is gone.
 *
 * A sound is identified by an id, 0 to 0x95, and lives in the egg as a block of
 * type 0x58: a length word and then that many signed 8-bit samples at 11111 Hz.
 * The original stages them into extended memory, because a real-mode program
 * could not address them otherwise, and a sample descriptor is therefore an XMS
 * handle, an offset into it and a length. None of that survives the port - the
 * descriptor here is a pointer and a length - but the thing above it does, and
 * has to: the game asks whether a sound is still playing and tells it to stop by
 * id, so the eight-voice table is part of the interface and not an artefact.
 *
 * The mixer is the original's, additively and in signed 8-bit, because that is
 * what the samples are. What replaces the DSP and the DMA is one call the
 * backend makes when it wants more audio - see sound_mix at the bottom.
 *
 * Read from Ducks.unpacked.exe, and from nsound.py, which had already recovered
 * the voice table and the descriptor by watching the guest run.
 */

#include <stdlib.h>
#include <string.h>

#include "dos.h"

/* ------------------------------------------------------------------ state */

/* d+0x2908. Zero until sound_init succeeds, and every entry point below is
 * gated on it - which is what makes a machine with no card silently quiet
 * rather than broken. */
uint8_t   sound_state;

uint8_t   sound_count;              /* 0x298b - samples loaded, at most 31 */
uint8_t   sound_slot[0x96];         /* 0x298c - id -> slot, 0xff for none */
sample_t *sample_table[32];         /* 0x290b - one per slot */

voice_t   voices[SOUND_VOICES];     /* 0x3c78 - twelve bytes each */
int16_t   voice_busy[SOUND_VOICES]; /* 0x3cd8 - one word each */
int16_t   active_voices;            /* 0x3d1c */

/* ------------------------------------------------------- 0x14628: sound_load
 *
 * Make sure a sound is in memory, and say whether it is. Already loaded is a
 * success; a full table is not, and neither is a sound the eggs do not have.
 *
 *   scale  the volume, out of 32. Every sample byte is multiplied by it on the
 *          way in, so a sound loaded quietly stays quiet - which is how AMBIENCE
 *          VOLUME works, and why it only takes effect on the next load.
 *
 * The block is looked for in the egg the caller named and then in egg 0, the
 * same two-step every other resource loader does.
 */
int16_t far sound_load(uint8_t id, int16_t scale, int16_t egg)
{
    sample_t *s;
    int32_t   len;
    int32_t   i;

    if (sound_slot[id] != 0xff)                    /* 0x14635 */
        return 1;
    if (sound_count == 0x1f)                       /* 31 is the last slot */
        return 0;

    if (!egg_find_block(0x58, id, egg) && !egg_find_block(0x58, id, 0))
        return 0;

    len = (uint16_t) egg_read_word(egg_stream);    /* 0x14680 */

    /* 0x14f07. The original allocates a ten-byte descriptor, asks XMS for
     * (len + 1023) / 1024 KB or a slice of the block it already holds, and
     * feeds it through a 2 KB staging buffer. What is left of that here is the
     * read and the scaling. */
    s = malloc(sizeof *s);
    if (!s)
        fatal(out_of_memory, 0);
    s->pcm    = malloc((size_t) len);
    s->length = len;
    if (!s->pcm)
        fatal(out_of_memory, 0);

    egg_fread(s->pcm, 1, (int16_t) len);
    if (scale != 0x20)                             /* 0x150a5 */
        for (i = 0; i < len; i++)
            s->pcm[i] = (uint8_t) (((int8_t) s->pcm[i] * scale) >> 5);

    sample_table[sound_count] = s;
    sound_slot[id] = sound_count;
    sound_count++;
    egg_block_end();
    return 1;
}

/* ------------------------------------------------------- 0x151d2: play_sample
 *
 * The first free voice, or nothing. `id` is the caller's own label for the
 * sound, kept so it can ask about it later; it is not the sound's id.
 */
int16_t far play_sample(sample_t *desc, int16_t id, int16_t loop)
{
    int16_t i, slot = -1;

    for (i = 0; i < SOUND_VOICES && slot < 0; i++)
        if (!voice_busy[i])
            slot = i;
    if (slot < 0)
        return 0;

    voices[slot].desc   = desc;
    voices[slot].id     = id;
    voices[slot].cursor = 0;
    voices[slot].loop   = loop;
    active_voices++;
    voice_busy[slot] = 1;
    return 1;
}

/* 0x15176 */
void far stop_voice(int16_t slot)
{
    if (slot < 0 || slot >= SOUND_VOICES || !voice_busy[slot])
        return;
    voice_busy[slot] = 0;
    voices[slot].id  = 0xffff;
    voices[slot].desc = 0;
    if (active_voices)
        active_voices--;
}

/* 0x15267 */
void far stop_sound_by_id(int16_t id)
{
    int16_t i;

    for (i = 0; i < SOUND_VOICES; i++)
        if (voice_busy[i] && voices[i].id == id)
            stop_voice(i);
}

/* 0x15298 */
int16_t far is_sound_playing(int16_t id)
{
    int16_t i;

    for (i = 0; i < SOUND_VOICES; i++)
        if (voice_busy[i] && voices[i].id == id)
            return 1;
    return 0;
}

/* ------------------------------------------------------- 0x14750: sound_play
 *
 *   id     which sound
 *   voice  the label it plays under. Anything but 1 stops whatever was already
 *          playing under that label first, and 4 means loop - which is how a
 *          sound that must not overlap itself is asked for.
 */
void far sound_play(int16_t id, int16_t voice)
{
    if (!sound_state)
        return;
    if (!sound_load((uint8_t) id, 0x20, 0xff))     /* unity volume */
        return;
    if (id >= 0x96)
        return;
    if (voice != 1)
        stop_sound_by_id(voice);

    play_sample(sample_table[sound_slot[id]], voice, voice == 4);
}

/* ------------------------------------------------- 0x147c5: sound_play_loop
 *
 * The ambience: voice 0, looping, and loaded at whatever volume the caller
 * asks for. game_main passes ambience_volume, which is the byte the AMBIENCE
 * VOLUME slider sets and settings.dat keeps.
 */
void far sound_play_loop(int16_t id, int16_t scale, int16_t egg)
{
    if (!sound_state)
        return;
    if (!sound_load((uint8_t) id, scale, egg))
        return;
    if (id >= 0x96)
        return;

    play_sample(sample_table[sound_slot[id]], 0, 1);
}

/* --------------------------------------------------- 0x146cd: release_sounds
 *
 * Everything goes: the five labelled voices are stopped by name, then every
 * loaded sample is freed from the top down and every id forgets its slot. The
 * original then hands the extended memory back, which is the part with nothing
 * left to do here.
 */
void far release_sounds(void)
{
    int16_t i;

    if (!sound_state)
        return;

    for (i = 0; i < 5; i++)                        /* 0x146d8 */
        stop_sound_by_id(i);

    while (sound_count) {                          /* 0x1470c */
        sound_count--;
        if (sample_table[sound_count]) {
            free(sample_table[sound_count]->pcm);
            free(sample_table[sound_count]);
            sample_table[sound_count] = 0;
        }
    }
    for (i = 0; i < 0x96; i++)
        sound_slot[i] = 0xff;
}

/* --------------------------------------------------------------- sound_init
 *
 * The original's is the card: reset the DSP, find the IRQ and the DMA channel
 * out of BLASTER, install a handler and start the transfer. All that is left of
 * it is opening a device at the same rate and saying whether it opened, because
 * sound_state is what every entry point above is gated on.
 */
void far sound_init(int16_t rate)
{
    int16_t i;

    for (i = 0; i < 0x96; i++)
        sound_slot[i] = 0xff;
    for (i = 0; i < SOUND_VOICES; i++) {
        voice_busy[i] = 0;
        voices[i].id  = 0xffff;
    }
    active_voices = 0;

    sound_state = audio_open(rate) ? 1 : 0;
}

/* ------------------------------------------------------------ 0x156cc: the mixer
 *
 * One buffer's worth, and the whole of what the DMA used to carry. The original
 * mixes each voice into a 16-bit accumulator per sample and then, in
 * sound_gather (0x157c1), walks 256 of them through a lookup table into the DMA
 * buffer. Reading that table out of a live machine says what the lookup does:
 * 0 gives 128, 1 gives 129, 64 gives 192, and 127 and everything above it gives
 * 255. So it is one to one and saturating, with the bias the DSP wants - not an
 * attenuation, which is the other thing a "clip table" could have been.
 *
 * The samples are signed, and that is the thing that has to be got right:
 * playing them unsigned is loud noise rather than a quiet mistake. The bias is
 * the backend's here, because SDL is told the format.
 *
 * A voice that reaches the end of its sample either starts again or lets go of
 * its slot, and letting go here is what makes is_sound_playing eventually
 * answer no.
 */
void far sound_mix(int8_t *dst, int16_t frames)
{
    int16_t i, v;

    memset(dst, 0, (size_t) frames);

    for (v = 0; v < SOUND_VOICES; v++) {
        sample_t *s;

        if (!voice_busy[v])
            continue;
        s = voices[v].desc;
        if (!s) {
            stop_voice(v);
            continue;
        }
        for (i = 0; i < frames; i++) {
            int16_t mixed;

            if (voices[v].cursor >= s->length) {
                if (!voices[v].loop) {
                    stop_voice(v);
                    break;
                }
                voices[v].cursor = 0;
            }
            mixed = dst[i] + (int8_t) s->pcm[voices[v].cursor++];
            dst[i] = (int8_t) (mixed > 127 ? 127 : mixed < -128 ? -128 : mixed);
        }
    }
}
