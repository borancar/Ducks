# The reconstruction, built against SDL3. Every routine in the game's own module
# is transcribed - stubs.c is gone, and its last entry, sound_set_rate, went to
# sound.c on 2026-08-08.
#
#   make          build ./ducks
#   make run      build it, fetch the game data if it is missing, and run
#   make eggs     just fetch the game data
#
# Link dos_io.c instead of sdl_io.c and it would talk to a VGA, which is the point
# of the split: game.c does not know which backend it has.

CC      ?= cc
CFLAGS  ?= -std=c99 -Wall -Wextra -O1 -ggdb $(shell pkg-config --cflags sdl3)
LDLIBS  ?= $(shell pkg-config --libs sdl3)

OBJS = game.o sdl_io.o egg.o sound.o
SRCS = game.c sdl_io.c egg.c sound.c

ducks: $(OBJS)
	$(CC) $(LDFLAGS) $(OBJS) $(LDLIBS) -o $@

%.o: %.c game.h
	$(CC) $(CFLAGS) -c $< -o $@

# ---------------------------------------------------------------- the game data
#
# `./ducks` is a port of the code, not a copy of the game: the pictures, the
# levels, the sounds and the text all live in MAIN.EGG and none of that is in this
# repository. Ducks! belongs to Tim Furnish / Hungry Software and is not
# redistributed here.
#
# So this fetches YOUR copy, from an archive download page, and keeps only the
# egg - the DOS executable in that archive is of no use to a port that has
# replaced it. If you already have the game somewhere, point DUCKS_GAME_DIR at
# it instead and nothing is downloaded.
GAME_URL ?= https://www.kieranmillar.com/ducks/Ducks.zip
GAME_ZIP  = .ducks-download.zip

Eggs:
	@command -v curl >/dev/null || { echo "curl is needed to fetch the game"; exit 1; }
	@command -v 7z   >/dev/null || { echo "7z (p7zip) is needed to unpack it"; exit 1; }
	@echo "Fetching Ducks! from $(GAME_URL) - the game is not part of this repository"
	curl -fL --progress-bar -o $(GAME_ZIP) "$(GAME_URL)"
	@# `e -r` rather than `x`: extract flat and look at every depth, so it does
	@# not matter whether the archive holds Eggs/Main.egg (which it does today) or
	@# buries it a level down. The egg is all we want - the DOS executable beside
	@# it is what this port replaces. Without -r, 7z matches only the archive root
	@# and silently extracts nothing.
	7z e -r -y -o$@ $(GAME_ZIP) "*.egg" >/dev/null
	@rm -f $(GAME_ZIP)
	@ls $@/*.egg >/dev/null 2>&1 || { \
	    echo "no .egg in the archive - has the download moved? see $(GAME_URL)"; \
	    rm -rf $@; exit 1; }
	@ls -1 $@ | sed 's/^/  /'

# Fetch only if there is nothing to use. DUCKS_GAME_DIR means the caller has their
# own copy and this must not touch the network.
eggs:
ifdef DUCKS_GAME_DIR
	@echo "DUCKS_GAME_DIR=$(DUCKS_GAME_DIR) - using your copy, fetching nothing"
else
	@$(MAKE) --no-print-directory Eggs
endif

run: ducks eggs
	./ducks

# The same code as a shared library, so a harness can call one function of it and
# compare against the guest's own bytes under Unicorn - see test_toollist.py.
# Nothing about the port changes for this: the emulator stays outside, and the
# harness marshals each side's arguments, because the two do not and cannot share
# memory (a pointer here is eight bytes and `far` is nothing).
libducks.so: $(SRCS)
	$(CC) $(CFLAGS) -fPIC -shared $(SRCS) $(LDLIBS) -o $@

lib: libducks.so

# The same thing under AddressSanitizer, for when something corrupts the heap.
# The port allocates each image row separately where the original had one block,
# so a write a few pixels past a row was harmless there and is a mangled malloc
# header here - which surfaces as "double free or corruption" at the next free, a
# long way from whatever did it. ASan reports it where it happens.
#
#   make asan && ./ducks-asan
ducks-asan: $(SRCS) game.h
	$(CC) -std=c99 -Wall -Wextra -O1 -ggdb -fsanitize=address,undefined \
	      -fno-omit-frame-pointer $(shell pkg-config --cflags sdl3) \
	      $(SRCS) $(shell pkg-config --libs sdl3) -o $@

asan: ducks-asan

# Eggs/ is deliberately not touched: it is your copy of the game, and deleting
# somebody's data because they asked to delete build output would be rude.
clean:
	rm -f $(OBJS) ducks ducks-asan libducks.so

.PHONY: run clean lib asan eggs
