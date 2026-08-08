# Ducks! — the game's own code, reconstructed

C reconstructed from the disassembly of **Ducks! v1.2** (Tim Furnish / Hungry
Software, 1998-2000), one file per unit of the original we can argue for. It
builds and plays:

```sh
make run
```

**You need your own copy of the game, and `make run` will fetch it for you.**
Nothing of Ducks! is distributed here — no executable, no `.egg` data, no artwork.
What `make run` does is download the archive from an archive page,
<https://www.kieranmillar.com/ducks/>, and keep the one file the port needs:
`Eggs/Main.egg`, which holds every picture, level, sound and line of text in the
game. That page is not the authors' — it is where a copy can still be found.

It needs `curl` and `7z`, does nothing if `Eggs/` is already there, and does not
touch the network at all if `DUCKS_GAME_DIR` points at a copy you already have.
`make` on its own just builds; `make eggs` just fetches; `clean` deliberately
leaves `Eggs/` alone.

## What you need

A C compiler, `make`, and **SDL3** — not SDL2; the two are not interchangeable and
the build looks for `pkgconfig(sdl3)`. `curl` and `7z` are needed only by
`make eggs`, to fetch the game data.

| | |
| --- | --- |
| Fedora | `sudo dnf install gcc make pkgconf-pkg-config SDL3-devel curl 7zip` |
| Debian / Ubuntu | `sudo apt install build-essential pkg-config libsdl3-dev curl p7zip-full` |
| Arch | `sudo pacman -S base-devel sdl3 curl p7zip` |
| macOS | `brew install sdl3 sevenzip` (compiler from the Xcode command line tools) |

The Fedora line is the one this was built with; the others are the usual names
for the same four things. If a package has been renamed, what you want is the
SDL3 *development* package — the one that installs `SDL3/SDL.h` and an `sdl3.pc`.
Check it with:

```sh
pkg-config --modversion sdl3
```

Debian and Ubuntu only started carrying `libsdl3-dev` recently (trixie, 24.10).
On anything older, build SDL3 from <https://github.com/libsdl-org/SDL>. SDL2 is
not a fallback: the port uses SDL3's audio streams and its
`SDL_GetKeyFromScancode` with a modifier state.

## What this is

The original sources are not available. This is what the code must have looked
like for the compiler to have emitted what it did: the game's own code segment,
`0x04ca0`-`0x14620`, read back out of the disassembly. Every routine in it is
transcribed and nothing stands in for one.

Every function carries the image offset it was read from, so any line can be
checked against the binary, and where a name or a type is a guess it says so.

**Two backends, one interface.** `game.h` declares what the game needs from below
it; `dos_io.c` is the original reconstructed - VGA ports, Mode X planes, INT 33h -
and `sdl_io.c` is the same interface on SDL3. The game cannot tell which one it is
linked against, which is the test of whether the split was drawn in the right
place.

`dos_io.c` is a record rather than a second build: reimplementing the DOS side is
not the aim, so it holds what we know about the original's hardware layer - the
Mode X planes, the DAC, the CRTC, INT 33h - and the Makefile does not compile it.

**Written as C99, not as period C.** The aim is a port that builds and runs, so a
construct is written the clearest modern way that matches the instructions. It is
not an attempt to feed Turbo C++ 3.0 something byte-identical.

## Files

| file | what is in it |
| --- | --- |
| [`game.c`](game.c) | the game: `main` and `init`, the resource and string loaders, the fonts, every menu and screen, the save format, the hall of fame, the level loader, the frame, every entity's behaviour, the tools, the particles, the endings and the cutscenes. In address order, which is the order the compiler emitted them |
| [`game.h`](game.h) | its types, its globals, and the interface both backends implement |
| [`sdl_io.c`](sdl_io.c) | that interface on SDL3: a linear framebuffer, an SDL palette, a 70 Hz deadline in place of the retrace spin, the mouse, the audio device |
| [`dos_io.c`](dos_io.c) | that interface as the original: BIOS mode, Mode X planes, the DAC, the page flip, INT 33h, and every drawing primitive |
| [`sound.c`](sound.c) | the sound module: the id-to-sample map, the eight voices, and the original's additive mixer |
| [`egg.c`](egg.c) | the egg reader: the directory, the chunk decoder, the shifted-string reader |

**It is the game.** The intro, the menus, the levels, the tools, the endings and
the cutscenes: you can start a game, play it, lose your lives, watch the ending
and get your name on the hall of fame. That state is tagged `no-stubs`.

The menus first, since they came first: all fifteen are real - `build_menus`
assembles them out of the string tables at startup and `run_screen` runs whichever
one `game_main` points at - so PLAY DUCKS, OPTIONS, the settings screens, READ ME!
and QUIT DUCKS all navigate, the toggles toggle, and QUIT takes main's teardown
path and exits. That much was tagged `menu-done`.

The cheats work, and they are typed in **capitals** - the compare at `0:0x4c28` is
`repe cmpsb`, with no case folding anywhere in it. `COLOURMAP` then `P` during a
level draws the palette; `THECROWDSAYBO` gives you the level picker;
`PLAYBACKTIME` opens the demo picker. `game.c` lists all ten against the flags
they set, and one of them - `KEYCODE` - is dead in the shipped build: it toggles
a word nothing reads.

The window captures the mouse, because the game keeps the pointer position
itself as a running total of INT 33h deltas and a pointer that stops at the edge
of a window is one the game believes stopped moving. **Ctrl+Alt** lets go, and
takes hold again; so does clicking in the window. Losing focus releases it
without forgetting what you asked for.

READ ME! works end to end: `build_episode_index` (`0x11657`) reads the episode,
readme and demo indexes out of the egg at startup, so SELECT AN EPISODE and
READ ME! list what the file actually holds, and `show_readme_section`
(`0x11efb`) draws a section's pages and turns them.

LOAD / SAVE works too: five slots in GAME1.SG to GAME5.SG, with the name typed
on a screen that is the menu itself with one line being edited. SAVE THIS GAME is
only offered while a game is in progress, which is reachable now that the gameplay
is.

Leave the menu alone for a while and the hall of fame comes up on its own, read
out of `settings.dat` at startup and written back on the way out.

The sliders, REGISTER DUCKS and the hall of fame all work, and every action code
`game_main` switches on reaches something real.

Sound works: 87 samples by id out of the egg's `0x58` blocks, eight voices, and
the original's additive mixer feeding an SDL device at the rate the game asks
for - including `D` mid-level, which doubles that rate exactly as the DSP time
constant did.


## Where the rest of it is

This branch is the port. Everything that produced it is on **`develop`**: the
unpacker, the DOS and VGA emulator the port is compared against, the snapshots,
the verification harnesses, and the working notes that say how each routine was
read and what is still open.

Start with [`docs/notes/reconstruction.md`](https://github.com/borancar/Ducks/blob/develop/docs/notes/reconstruction.md)
for how much is verified rather than merely written, the conventions the code
follows, and where the port knowingly differs from the original.

`master` is generated from `develop` with `git subtree split`, so it is never
committed to directly.

## Licence

The reconstruction is MIT licensed; see [`LICENSE`](LICENSE). **That covers this
code only.** Ducks! itself - the executable, the `.egg` data, the artwork -
belongs to Tim Furnish / Hungry Software and is not distributed here under any
licence.
