# The reconstruction, built against SDL3. Not the game yet - most of the segment
# is still unread and stubs.c says which parts - but it compiles, links and runs,
# which is the difference between a description and a port.
#
#   make          build ./ducks
#   make run      build and run it
#
# Link dos_io.c instead of sdl_io.c and it would talk to a VGA, which is the point
# of the split: game.c does not know which backend it has.

CC      ?= cc
CFLAGS  ?= -std=c99 -Wall -Wextra -O1 -ggdb $(shell pkg-config --cflags sdl3)
LDLIBS  ?= $(shell pkg-config --libs sdl3)

OBJS = game.o sdl_io.o stubs.o egg.o sound.o
SRCS = game.c sdl_io.c stubs.c egg.c sound.c

ducks: $(OBJS)
	$(CC) $(LDFLAGS) $(OBJS) $(LDLIBS) -o $@

%.o: %.c dos.h
	$(CC) $(CFLAGS) -c $< -o $@

run: ducks
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
ducks-asan: $(SRCS) dos.h
	$(CC) -std=c99 -Wall -Wextra -O1 -ggdb -fsanitize=address,undefined \
	      -fno-omit-frame-pointer $(shell pkg-config --cflags sdl3) \
	      $(SRCS) $(shell pkg-config --libs sdl3) -o $@

asan: ducks-asan

clean:
	rm -f $(OBJS) ducks ducks-asan libducks.so

.PHONY: run clean lib asan
