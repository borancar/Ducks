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
CFLAGS  ?= -std=c99 -Wall -Wextra -O1 $(shell pkg-config --cflags sdl3)
LDLIBS  ?= $(shell pkg-config --libs sdl3)

OBJS = game.o sdl_io.o stubs.o egg.o

ducks: $(OBJS)
	$(CC) $(OBJS) $(LDLIBS) -o $@

%.o: %.c dos.h
	$(CC) $(CFLAGS) -c $< -o $@

run: ducks
	./ducks

clean:
	rm -f $(OBJS) ducks

.PHONY: run clean
