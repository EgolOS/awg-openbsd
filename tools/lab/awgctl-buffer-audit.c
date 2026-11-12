/*
 * Host-side buffer audit for sbin/awgctl/awgctl.c (E2 remediation D4/D5).
 *
 * This harness links the PRODUCTION source (it #includes awgctl.c; it is
 * never a copy) and instruments the allocator and explicit_bzero(3) with
 * recording wrappers, so a test can observe the three buffer sinks the E2
 * review asked about and which the ioctl fixture cannot reach:
 *
 *   serialise-*  D5/G8: the base64 checks inside serialise_config(). The
 *                fixture's F15 case never reaches them — load_key() rejects
 *                the same input during parsing — so this harness calls
 *                serialise_config() directly with a cfg whose decode fails.
 *   show-wipe    D4/G3: the show size-probe buffer must be wiped (with its
 *                live capacity) before realloc(3) frees the old, key-bearing
 *                copy, and before the final free.
 *   parse-wipe   D4:    parse_config()'s raw line buffer carries the key
 *                text of the line being parsed and must be wiped, not just
 *                freed.
 *
 * Every allocation/wipe prints one "EV" line on stdout; the caller asserts
 * on the sequence. Exit codes: 0 ok, 1 harness failure, 2/3 as awgctl.
 *
 * Build (see tools/tests/test_awgctl_buffer_audit.py):
 *   cc -DAWGCTL_LINUX -Isrc -Isbin/awgctl -o awgctl-buffer-audit \
 *      tools/lab/awgctl-buffer-audit.c tools/lab/fixture-stubs.c -lutil
 */
#include <sys/types.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>

/* ------------------------------------------------------------------ */
/* Recording allocator / wipe wrappers.  The real functions are captured
 * before the macros below rename the call sites inside awgctl.c. */
struct ev {
	int	kind;		/* 0 calloc, 1 realloc, 2 free, 3 bzero */
	void	*a;
	void	*b;
	size_t	 n;
};
enum { EV_CALLOC, EV_REALLOC, EV_FREE, EV_BZERO };
#define NEV	512
static struct ev g_ev[NEV];
static int g_nev;

static void (*const real_free)(void *) = free;
static void *(*const real_realloc)(void *, size_t) = realloc;
static void *(*const real_calloc)(size_t, size_t) = calloc;

static void
record(int kind, void *a, void *b, size_t n)
{
	if (g_nev < NEV) {
		g_ev[g_nev].kind = kind;
		g_ev[g_nev].a = a;
		g_ev[g_nev].b = b;
		g_ev[g_nev].n = n;
	}
	g_nev++;
}

void	test_bzero(void *, size_t);
void	*test_realloc(void *, size_t);
void	*test_calloc(size_t, size_t);
void	test_free(void *);

#define	explicit_bzero	test_bzero
#define	realloc		test_realloc
#define	calloc		test_calloc
#define	free		test_free
#define	main		awgctl_program_main
#ifndef AWGCTL_SOURCE
#define	AWGCTL_SOURCE	"../../sbin/awgctl/awgctl.c"
#endif
#include AWGCTL_SOURCE
#undef	main
#undef	free
#undef	calloc
#undef	realloc
#undef	explicit_bzero

void
test_bzero(void *p, size_t n)
{
	record(EV_BZERO, p, NULL, n);
	if (p != NULL)
		memset(p, 0, n);
}

void *
test_calloc(size_t n, size_t sz)
{
	void *p = real_calloc(n, sz);

	record(EV_CALLOC, p, NULL, n * sz);
	return (p);
}

void *
test_realloc(void *p, size_t n)
{
	void *q = real_realloc(p, n);

	record(EV_REALLOC, p, q, n);
	return (q);
}

void
test_free(void *p)
{
	record(EV_FREE, p, NULL, 0);
	real_free(p);
}

/* ------------------------------------------------------------------ */
/* Test inputs.  Keys are generated at run time (b64_ntop), so no key
 * literal lives in this file. */

static void
key_of(unsigned char fill, char *out, size_t n)
{
	unsigned char raw[AWG_KEY_LEN];

	memset(raw, fill, sizeof(raw));
	if (b64_ntop(raw, sizeof(raw), out, n) != AWG_BASE64_KEY_LEN) {
		fprintf(stderr, "harness: b64_ntop failed\n");
		exit(1);
	}
}

/*
 * A 44-character key whose base64 decode is NOT AWG_KEY_LEN bytes: the last
 * quantum is rebuilt as "B==", so b64_pton(3) returns a short count / error
 * instead of 32. load_key() would reject it as well; this harness bypasses
 * the parser on purpose to reach serialise_config().
 */
static void
short_key_of(const char *good, char *out, size_t n)
{
	if (snprintf(out, n, "%.41sB==", good) < 0) {
		fprintf(stderr, "harness: snprintf failed\n");
		exit(1);
	}
		if (strlen(out) != AWG_BASE64_KEY_LEN) {
		fprintf(stderr, "harness: short key is %zu chars, want %d\n",
		    strlen(out), AWG_BASE64_KEY_LEN);
		exit(1);
	}
}

static void
dump_events(void)
{
	int i;

	printf("INFO sizeof_iface=%zu sizeof_peer=%zu line_max=%d probe_max=%d\n",
	    sizeof(struct awg_interface_io), sizeof(struct awg_peer_io),
	    AWG_CONFIG_LINE_MAX, AWG_GET_PROBE_MAX);
	for (i = 0; i < g_nev && i < NEV; i++) {
		switch (g_ev[i].kind) {
		case EV_CALLOC:
			printf("EV calloc %p %zu\n", g_ev[i].a, g_ev[i].n);
			break;
		case EV_REALLOC:
			printf("EV realloc %p %p %zu\n", g_ev[i].a, g_ev[i].b,
			    g_ev[i].n);
			break;
		case EV_FREE:
			printf("EV free %p\n", g_ev[i].a);
			break;
		case EV_BZERO:
			printf("EV bzero %p %zu\n", g_ev[i].a, g_ev[i].n);
			break;
		}
	}
}

static int g_dumped;

/* Runs AFTER awgctl's cleanup_active_cfg (atexit is LIFO and this handler
 * is registered first), so an errx()/err() refusal path is observable: the
 * wipe performed by the atexit cleanup is already in the event list. */
static void
dump_at_exit(void)
{
	if (g_dumped)
		return;
	g_dumped = 1;
	dump_events();
}

static void
fill_cfg(struct cfg *c, int bad)
{
	static char good[AWG_BASE64_KEY_LEN + 1];
	static char shortk[AWG_BASE64_KEY_LEN + 1];
	static char psk[AWG_BASE64_KEY_LEN + 1];
	static char peer_psk[AWG_BASE64_KEY_LEN + 1];
	static struct peer_cfg pc;
	static struct peer_cfg *peers[1];

	key_of(0x11, good, sizeof(good));	/* private */
	key_of(0x22, psk, sizeof(psk));		/* hpk */
	key_of(0x33, peer_psk, sizeof(peer_psk));	/* peer key */
	short_key_of(good, shortk, sizeof(shortk));

	memset(c, 0, sizeof(*c));
	c->c_private = bad == 'p' ? shortk : good;
	c->c_update_private = 1;
	c->c_hpk = bad == 'h' ? shortk : psk;
	c->c_update_hpk = 1;
	c->c_has_hpk = 1;
	c->c_has_version = 1;
	c->c_version = AWG_VERSION_3_1;

	memset(&pc, 0, sizeof(pc));
	pc.pc_pubkey = bad == 'b' ? shortk : peer_psk;
	pc.pc_psk = bad == 'k' ? shortk : peer_psk;
	peers[0] = &pc;
	c->c_peers = 1;
	c->c_peer = peers;
	c->c_replace_peers = 1;
}

static int
serialise_case(int bad)
{
	struct awg_data_io d;
	struct cfg c;
	struct awg_interface_io *ip;

	memset(&d, 0, sizeof(d));
	fill_cfg(&c, bad);
	serialise_config(&d, &c);
	ip = d.awgd_interface;
	if (ip == NULL) {
		fprintf(stderr, "harness: serialise_config left no buffer\n");
		return (1);
	}
	/* Only the "accepted" path gets here; the bad cases exit via errx(2). */
	printf("RESULT accepted private=%02x wipes=%d\n", ip->i_private[0],
	    g_nev);
	return (0);
}

static int
show_case(void)
{
	int r;

	r = show_interface(0, "awg9");
	printf("RESULT show rc=%d\n", r);
	return (r);
}

static int
parse_case(const char *path)
{
	struct cfg c;
	int fd;

	if ((fd = open(path, O_RDONLY)) == -1) {
		fprintf(stderr, "harness: open %s\n", path);
		return (1);
	}
	memset(&c, 0, sizeof(c));
	parse_config(fd, path, &c);
	printf("RESULT parse private_set=%d line_len=%zu\n",
	    c.c_private != NULL ? (int)strlen(c.c_private) : 0,
	    (size_t)AWG_CONFIG_LINE_MAX);
	return (0);
}

static int
secret_case(void)
{
	char *s;

	s = read_secret("-");
	printf("RESULT secret len=%zu\n", strlen(s));
	return (0);
}

int
main(int argc, char *argv[])
{
	setvbuf(stdout, NULL, _IOLBF, 0);
	/* Registered BEFORE awgctl's cleanup so it runs after it (atexit is
	 * LIFO): the errx()/err() paths then print the wipe that the cleanup
	 * performed, which is what makes the refusal path observable. */
	if (atexit(dump_at_exit) != 0) {
		fprintf(stderr, "harness: atexit\n");
		return (1);
	}
	/* Same registration as awgctl's main(): err()/errx() exit through it. */
	if (atexit(cleanup_active_cfg) != 0) {
		fprintf(stderr, "harness: atexit\n");
		return (1);
	}
	if (argc < 2) {
		fprintf(stderr, "usage: %s serialise-ok|serialise-bad-"
		    "private|serialise-bad-hpk|serialise-bad-pubkey|"
		    "serialise-bad-psk|show-wipe|parse-wipe <conf>|"
		    "secret-stdin\n", argv[0]);
		return (1);
	}
	if (strcmp(argv[1], "serialise-ok") == 0)
		return (serialise_case(0));
	if (strcmp(argv[1], "serialise-bad-private") == 0)
		return (serialise_case('p'));
	if (strcmp(argv[1], "serialise-bad-hpk") == 0)
		return (serialise_case('h'));
	if (strcmp(argv[1], "serialise-bad-pubkey") == 0)
		return (serialise_case('b'));
	if (strcmp(argv[1], "serialise-bad-psk") == 0)
		return (serialise_case('k'));
	if (strcmp(argv[1], "show-wipe") == 0)
		return (show_case());
	if (strcmp(argv[1], "parse-wipe") == 0 && argc == 3)
		return (parse_case(argv[2]));
	if (strcmp(argv[1], "secret-stdin") == 0)
		return (secret_case());
	fprintf(stderr, "harness: unknown case %s\n", argv[1]);
	return (1);
}
