PROG = awgctl
MAN = awgctl.8
# E2-HYGIENE: the release publishes awgctl to /usr/local/sbin with group wheel
# (docs/SHIP-LIST.md row 5, install.sh publish_awgctl). bsd.prog.mk defaults
# BINDIR to /usr/sbin and BINGRP to bin, so a standalone `make install` shipped
# a different path/owner than the release. Pin them so the standalone install
# places the same pair (awgctl + awgctl.8, modes 555/444) at the release
# destinations; MANDIR keeps the bsd.prog.mk default /usr/share/man, matching
# SHIP-LIST.md row 6.
BINDIR = /usr/local/sbin
BINGRP = wheel
SRCS = awgctl.c
CPPFLAGS += -I${.CURDIR}/../../src
CFLAGS += -Wall -Wextra -Werror
DPADD = ${LIBUTIL} ${LIBC}
LDLIBS = -lutil

.include <bsd.prog.mk>