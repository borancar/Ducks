#!/usr/bin/env python3
"""
Native voice player: implement Ducks' sound API on top of pygame.

The game's real interface, recovered by disassembly, is eight voices:

    0x151d2  play_sample(desc_far, id, loop) -> 1, or 0 if all voices busy
    0x15176  stop_voice(slot)
    0x15267  stop_sound_by_id(id)
    0x15298  is_sound_playing(id)
    0x156cc  mix_voice(voice_far)          - per-voice accumulate

with a 12-byte voice slot at DGROUP:0x3c78:

    +0x00/+0x02  far pointer to a sample descriptor
    +0x04        caller-supplied sound id (0xffff = free)
    +0x06/+0x08  32-bit playback cursor
    +0x0a        loop flag

and a sample descriptor of:

    +0x00 word   XMS handle
    +0x02 dword  start offset in that handle
    +0x06 dword  length in bytes

Everything below that - XMS staging into a conventional buffer, additive mixing
into a 16-bit accumulator, a clip table, DMA and the DSP - exists only because a
real-mode program could not address extended memory and had to feed a card one
block at a time. We have the samples as Python bytes already.

Three things this has to get right to be faithful:

  * The whole family is intercepted together. The game asks "is this still
    playing" and "stop this id", and reads an active-voice count. If pygame owns
    playback while the guest's voice table disagrees, sounds stop starting
    because every slot looks busy.
  * mix_voice is neutralised, otherwise the game would also mix the same sample
    through the DSP path and you would hear it twice.
  * Samples are SIGNED 8-bit: the mixer sign-extends each byte with cbw before
    accumulating. Playing them as unsigned without conversion is loud noise.
"""
import struct

import numpy as np
import pygame

VOICES = 8
VOICE_BASE = 0x3C78          # DGROUP offset of voice[0]
VOICE_STRIDE = 12
BUSY_BASE = 0x3CD8           # one word per slot
ACTIVE_COUNT = 0x3D1C
FREE_ID = 0xFFFF


class NativeVoices:
    def __init__(self, m, rate=11111, log=print):
        self.m = m
        self.rate = rate
        self.log = log
        self.chan = {}           # slot -> pygame Channel
        self.snd = {}            # slot -> pygame Sound (kept alive)
        self.loops = {}          # slot -> bool
        self.started = 0
        self.refused = 0
        self.stopped = 0
        self.reaped = 0
        self.ok = False
        try:
            # pygame.init() has already opened the mixer at its own defaults,
            # so it must be closed and reopened - checking get_init() first
            # silently leaves 44100/16-bit/stereo in place and the samples come
            # out as noise.
            if pygame.mixer.get_init():
                pygame.mixer.quit()
            pygame.mixer.init(frequency=rate, size=8, channels=1, buffer=1024)
            pygame.mixer.set_num_channels(VOICES + 2)
            self.ok = True
            self.log(f"  [nsnd] pygame voices ready: {pygame.mixer.get_init()}")
        except Exception as e:
            self.log(f"  [nsnd] mixer unavailable ({e}); native sound disabled")

    # ------------------------------------------------------------ guest state
    def _voice(self, slot):
        return self.m.dgroup_base + VOICE_BASE + slot * VOICE_STRIDE

    def _get_id(self, slot):
        return struct.unpack("<H", self.m.read(self._voice(slot) + 4, 2))[0]

    def _set_slot(self, slot, desc_off, desc_seg, sid, loop):
        a = self._voice(slot)
        self.m.write(a, struct.pack("<HH", desc_off, desc_seg))
        self.m.write(a + 4, struct.pack("<H", sid))
        self.m.write(a + 6, struct.pack("<I", 0))
        self.m.write(a + 10, struct.pack("<H", loop))
        self.m.write(self.m.dgroup_base + BUSY_BASE + slot * 2,
                     struct.pack("<H", 1))

    def _clear_slot(self, slot):
        a = self._voice(slot)
        self.m.write(a, struct.pack("<HH", 0, 0))
        self.m.write(a + 4, struct.pack("<H", FREE_ID))
        self.m.write(a + 6, struct.pack("<I", 0))
        self.m.write(a + 10, struct.pack("<H", 0))
        self.m.write(self.m.dgroup_base + BUSY_BASE + slot * 2,
                     struct.pack("<H", 0))

    def _busy(self, slot):
        return struct.unpack("<H", self.m.read(
            self.m.dgroup_base + BUSY_BASE + slot * 2, 2))[0] != 0

    def _bump_active(self, delta):
        a = self.m.dgroup_base + ACTIVE_COUNT
        n = struct.unpack("<H", self.m.read(a, 2))[0]
        n = max(0, min(0xFFFF, n + delta))
        self.m.write(a, struct.pack("<H", n))

    # ---------------------------------------------------------------- samples
    def _pcm(self, desc_seg, desc_off):
        """Fetch a sample straight out of the XMS block, as unsigned 8-bit."""
        raw = self.m.read(desc_seg * 16 + desc_off, 10)
        handle = struct.unpack_from("<H", raw, 0)[0]
        start = struct.unpack_from("<I", raw, 2)[0]
        length = struct.unpack_from("<I", raw, 6)[0]
        blk = self.m.xms.handles.get(handle)
        if blk is None or length == 0 or start + length > len(blk):
            return None, (handle, start, length)
        # Signed 8-bit in the game, unsigned in the mixer: offset by 128.
        pcm = np.frombuffer(bytes(blk[start:start + length]),
                            dtype=np.uint8) ^ 0x80
        return pcm.tobytes(), (handle, start, length)

    # -------------------------------------------------------------------- API
    def play_sample(self, desc_off, desc_seg, sid, loop):
        if not self.ok:
            return 0
        slot = next((s for s in range(VOICES) if not self._busy(s)), None)
        if slot is None:
            self.refused += 1
            return 0
        pcm, info = self._pcm(desc_seg, desc_off)
        if pcm is None:
            self.log(f"  [nsnd] bad descriptor {desc_seg:04x}:{desc_off:04x} "
                     f"handle/start/len={info}")
            return 0
        try:
            snd = pygame.mixer.Sound(buffer=pcm)
            ch = pygame.mixer.Channel(slot)
            ch.play(snd, loops=-1 if loop else 0)
        except Exception as e:
            self.log(f"  [nsnd] play failed: {e}")
            return 0
        self.snd[slot], self.chan[slot] = snd, ch
        self.loops[slot] = bool(loop)
        self._set_slot(slot, desc_off, desc_seg, sid, 1 if loop else 0)
        self._bump_active(+1)
        self.started += 1
        return 1

    def stop_voice(self, slot):
        if 0 <= slot < VOICES:
            ch = self.chan.pop(slot, None)
            if ch is not None:
                try:
                    ch.stop()
                except Exception:
                    pass
            self.snd.pop(slot, None)
            self.loops.pop(slot, None)
            self._clear_slot(slot)
            self.stopped += 1
        return None

    def stop_by_id(self, sid):
        for slot in range(VOICES):
            if self._get_id(slot) == sid:
                self.stop_voice(slot)
                self._bump_active(-1)
        return None

    def is_playing(self, sid):
        for slot in range(VOICES):
            if self._get_id(slot) == sid:
                return 1
        return 0

    def reap(self):
        """Free slots whose one-shot sample has finished.

        The original frees a voice when its cursor reaches the sample length.
        Here the channel going quiet is the equivalent signal; without this,
        slots leak and the ninth sound of the session never plays.
        """
        if not self.ok:
            return
        for slot in list(self.chan):
            if self.loops.get(slot):
                continue
            ch = self.chan.get(slot)
            if ch is not None and not ch.get_busy():
                self.stop_voice(slot)
                self._bump_active(-1)
                self.reaped += 1

    def summary(self):
        return {"started": self.started, "refused_no_free_voice": self.refused,
                "stopped": self.stopped, "finished": self.reaped,
                "slots_live": sorted(self.chan)}
