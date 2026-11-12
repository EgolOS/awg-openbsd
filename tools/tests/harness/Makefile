# Builds the E2 conformance harness on a userspace host (no kernel, no VM).
#
#   make            -> regenerate gen/ from src/ and link e2_harness
#   make clean
#
# gen/ is a build product: extract.py copies the message structs, the
# junk/counter/KDF function bodies and the constants verbatim out of
# ../../../src/.  Building therefore fails when src/ is absent or when a
# symbol the vectors depend on disappears.

CC ?= cc
CFLAGS ?= -std=c11 -O1 -g -Wall -Wextra -Werror \
	  -Wno-unused-variable -Wno-unused-parameter -Wno-unused-function
PYTHON ?= python3
ROOT := ../../..
GEN := gen

all: e2_harness

$(GEN)/manifest.json: extract.py $(ROOT)/src/if_awg.c $(ROOT)/src/awg_noise.c $(ROOT)/src/awg_noise.h
	@rm -rf $(GEN)
	@$(PYTHON) extract.py --root $(ROOT) --out $(GEN)

e2_harness: e2_harness.c blake2s.c blake2s.h stubs.h $(GEN)/manifest.json
	$(CC) $(CFLAGS) -o $@ e2_harness.c blake2s.c

clean:
	rm -rf $(GEN) e2_harness

.PHONY: all clean
