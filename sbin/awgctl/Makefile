PROG = awgctl
MAN = awgctl.8
SRCS = awgctl.c
CPPFLAGS += -I${.CURDIR}/../../src
CFLAGS += -Wall -Wextra -Werror
DPADD = ${LIBUTIL} ${LIBC}
LDLIBS = -lutil

.include <bsd.prog.mk>
