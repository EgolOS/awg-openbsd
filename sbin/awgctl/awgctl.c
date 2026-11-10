/*	$OpenBSD: awgctl.c,v 1.0 2026/11/08 00:00:00 Exp $ */

/*
 * Copyright (C) 2026 Andrey Orekhov <pieceofcakecupofcoffee@gmail.com>
 *
 * Permission to use, copy, modify, and distribute this software for any
 * purpose with or without fee is hereby granted, provided that the above
 * copyright notice and this permission notice appear in all copies.
 *
 * THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
 * WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
 * MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
 * ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
 * WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
 * ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
 * OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
 */

/*
 * awgctl — standalone management tool for awg(4) (docs/ADMIN-INTERFACE.md,
 * Option B). One SIOCSAWG/SIOCGAWG path on struct awg_data_io; interface
 * create/destroy shell out to stock ifconfig(8) (SIOCIFCREATE2). The field
 * semantics follow sbin/ifconfig/ifconfig_awg.patch, the config grammar the
 * wg-quick shape parsed by sbin/awg-quick.
 *
 * Exit codes: 0 ok, 1 usage/IO, 2 config parse, 3 ioctl.
 */

#include <sys/types.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/socketvar.h>	/* MIN() on OpenBSD */
#include <sys/stat.h>
#include <sys/wait.h>
#ifndef __OpenBSD__
#define	IFDESCRSIZE	32	/* net/if.h on non-OpenBSD hosts */
/* The host build (and the fixture stub) only needs the MTU out of the
 * AF_LINK ifa_data; glibc's net/if.h does not always carry if_data. */
struct if_data {
	unsigned int	ifi_mtu;
};
#endif
#ifndef erx
#define	erx		errx	/* erx(3) is an OpenBSD err.h macro; Linux has errx */
#endif
#ifndef __dead
#define	__dead		__attribute__((__noreturn__))
#endif
#ifndef RT_TABLEID_MAX
#define	RT_TABLEID_MAX	255	/* net/route.h on non-OpenBSD hosts */
#endif
#include <net/if.h>
#include <net/if_awg.h>
#include <netinet/in.h>
#include <netdb.h>
#include <arpa/inet.h>

#include <ctype.h>
#include <err.h>
#include <errno.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <limits.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#ifdef AWGCTL_LINUX
#include <sys/param.h>	/* b64_pton/b64_ntop on glibc */
#else
#include <resolv.h>	/* b64_pton/b64_ntop on OpenBSD (libc) */
#endif

extern char **environ;

#ifdef AWGCTL_LINUX
/* OpenBSD APIs missing from glibc; the host build is a smoke build only. */
#ifndef AF_LINK
#define	AF_LINK		17
#endif
#include <arpa/inet.h>	/* inet_net_pton, inet_ntop */
int	b64_pton(const char *, uint8_t *, size_t);
int	b64_ntop(const uint8_t *, size_t, char *, size_t);
long long	strtonum(const char *, long long, long long, const char **);
int	pledge(const char *, const char **);
int	unveil(const char *, const char *);
int	inet_net_pton(int af, const char *, void *, size_t);

/* Minimal CIDR parser so the host smoke build links. */
int
inet_net_pton(int af, const char *src, void *dst, size_t size)
{
	char *slash, *copy;
	int bits = -1;

	(void)size;
	if ((copy = strdup(src)) == NULL)
		return (-1);
	if ((slash = strchr(copy, '/')) != NULL) {
		const char *errmsg = NULL;
		*slash++ = '\0';
		bits = (int)strtonum(slash, 0,
		    af == AF_INET ? 32 : 128, &errmsg);
		if (errmsg) {
			free(copy);
			return (-1);
		}
	}
	if (inet_pton(af, copy, dst) != 1) {
		free(copy);
		return (-1);
	}
	free(copy);
	return (bits);
}

/* Minimal implementations so the host smoke build links. */
int
b64_pton(const char *src, uint8_t *dst, size_t dstlen)
{
	static const char *t =
	    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
	size_t o = 0;
	int q[256], i, v = 0, bits = 0;

	for (i = 0; i < 256; i++)
		q[i] = -1;
	for (i = 0; t[i]; i++)
		q[(unsigned char)t[i]] = i;
	for (; *src; src++) {
		if (*src == '=')
			break;
		if (q[(unsigned char)*src] == -1)
			return (-1);
		v = (v << 6) | q[(unsigned char)*src];
		bits += 6;
		if (bits >= 8) {
			if (o >= dstlen)
				return (-1);
			dst[o++] = (v >> (bits - 8)) & 0xff;
			bits -= 8;
		}
	}
	return ((int)o);
}

int
b64_ntop(const uint8_t *src, size_t srclen, char *dst, size_t dstlen)
{
	static const char *t =
	    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
	size_t o = 0, i;

	for (i = 0; i + 2 < srclen + 2; i += 3) {
		unsigned v = src[i] << 16;
		int k;

		if (i + 1 < srclen)
			v |= src[i + 1] << 8;
		if (i + 2 < srclen)
			v |= src[i + 2];
		for (k = 0; k < 4 && o + 1 < dstlen; k++) {
			if ((size_t)k * 6 >= (srclen - i) * 8 &&
			    (i + k) >= srclen) {
				dst[o++] = '=';
				continue;
			}
			dst[o++] = t[(v >> (18 - 6 * k)) & 0x3f];
		}
	}
	if (o < dstlen)
		dst[o] = '\0';
	return ((int)o);
}

long long
strtonum(const char *nptr, long long lo, long long hi, const char **errstr)
{
	char *end;
	long long v;

	errno = 0;
	v = strtoll(nptr, &end, 10);
	if (*end != '\0' && *end != '\n')
		goto range;
	if (errno == ERANGE)
		goto range;
	if (v < lo || v > hi)
		goto range;
	if (errstr != NULL)
		*errstr = NULL;
	return (v);
range:
	if (errstr != NULL)
		*errstr = "out of range";
	return (0);
}

int
pledge(const char *promises, const char *paths[])
{
	(void)promises;
	(void)paths;
	return (0);
}

int
unveil(const char *path, const char *permissions)
{
	(void)path;
	(void)permissions;
	return (0);
}
#endif

#ifndef IFCONFIG_PATH
#define	IFCONFIG_PATH	"/sbin/ifconfig"
#endif
#ifndef CONF_DIR
#define	CONF_DIR	"/etc/amnezia/amneziawg"
#endif
/* Owner of the config tree. 0 (root) on OpenBSD; the host fixture accepts
 * any owner so the same checks run for an unprivileged lab user. */
#ifndef CONF_OWNER_UID
#define	CONF_OWNER_UID	0
#endif
#ifndef CONF_OWNER_OK
#define	CONF_OWNER_OK(u)	((u) == CONF_OWNER_UID)
#endif
#define	AWG_BASE64_KEY_LEN	(4 * ((AWG_KEY_LEN + 2) / 3))

#define	EX_OK		0
#define	EX_USAGE	1
#define	EX_CONFIG	2
#define	EX_IOCTL	3

/* Kernel bounds, mirroring awg_ioctl_check() in src/if_awg.c. */
#define	AWG_MAX_JUNK		64
#define	AWG_MAX_JUNK_SIZE	1500
#define	AWG_MAX_PADDING		1280
#define	AWG_H_MAX		4294967295LL
#define	AWG_RANGE_MAX		65535
#define	AWG_PKA_MAX		43200
#ifndef MIN
#define	MIN(a, b)		((a) < (b) ? (a) : (b))
#endif
/* glibc's struct sockaddr has no sa_len; the host build is a smoke build. */
#ifdef AWGCTL_LINUX
#define	AWG_SA_LEN(s)		(s).sa_family
#else
#define	AWG_SA_LEN(s)		(s).sa_len
#endif
/* noise.h defaults, used when merging timing for the kernel's check. */
#define	DEF_REKEY_AFTER_TIME	120
#define	DEF_REKEY_TIMEOUT	5
#define	DEF_REJECT_AFTER_TIME	180
#define	DEF_KEEPALIVE_TIMEOUT	10

struct peer_cfg {
	char		*pc_pubkey;
	char		*pc_psk;
	char		*pc_endpoint;	/* host:port or [v6]:port */
	char		*pc_pka;	/* "N" or "N-M" */
	char		**pc_aips;
	int		 pc_n_aips;
	int		 pc_remove;
};

struct cfg {
	char		*c_private;	/* base64, required */
	char		*c_hpk;		/* base64, 3.1 only */
	char		*c_ispec[AWG_ISPEC_COUNT];
	char		*c_mtu;
	char		**c_addrs;
	char		**c_dns;	/* parsed, not applied by the kernel */
	int		 c_n_addrs;
	int		 c_n_dns;
	int		 c_peers;
	struct peer_cfg **c_peer;

	/* presence flags, mirroring AWG_INTERFACE_HAS_* */
	int		 c_has_port, c_has_rtable, c_has_version;
	/* R4-06: only secrets the caller explicitly named are written back;
	 * read-back siblings (private key, HPK) stay in the cfg for grouping
	 * but must not force HAS_PRIVATE/HAS_HPK on an unrelated set.
	 * R5/N2: the grouped read-modify-write is last-writer-wins — the
	 * kernel has no generation counter, so the R4 fingerprint pre-check
	 * (which counted the GET's I-spec pointer echo and the live peer
	 * counters as changes) is gone; see docs/ADMIN-INTERFACE.md. */
	int		 c_update_private, c_update_hpk;
	int		 c_has_jc, c_has_s12, c_has_s3, c_has_s4, c_has_h;
	int		 c_has_hpk, c_has_cpa, c_has_trailers, c_has_cookies;
	int		 c_has_rekey_after, c_has_rekey_timeout;
	int		 c_has_reject_after, c_has_keepalive_timeout;
	int		 c_has_max_attempts;
	int		 c_has_mtu;
	int		 c_replace_peers;	/* full peer-list replacement */
	long		 c_mtu_num;		/* validated MTU */
	int		 c_has_i[AWG_ISPEC_COUNT];

	long		 c_port, c_rtable, c_jc, c_jmin, c_jmax;
	long		 c_s1, c_s2, c_s3, c_s4;
	int		 c_version;	/* AWG_VERSION_* */
	int		 c_random_trailers, c_disable_cookies;
	struct awg_range c_h1, c_h2, c_h3, c_h4;
	struct awg_range c_cpa, c_rekey_after, c_rekey_timeout;
	struct awg_range c_reject_after, c_keepalive_timeout;
	struct awg_range c_max_attempts;
};

/* Command arguments: `up` needs the interface name, the config path and the
 * verified descriptor (R4-07: the trust walk runs before unveil(2)). */
struct request {
	const char	*r_ifname;
	const char	*r_path;
	int		 r_fd;
};

struct addr_ent {
	char		*ae_addr;		/* presentation text, prefix included */
	struct addr_ent	*ae_next;
};

/* R7/R4-08: the pre-up state of the interface, captured before any mutation
 * so an operational failure restores it.  is_mtu is NULL when the kernel
 * reports no MTU (leave the default alone); is_created marks an interface
 * this run created, which a restore destroys wholesale.  R9/R4-08: is_up
 * carries the AF_LINK IFF_UP flag so the restore puts the up/down state
 * back, not only the MTU and the address list. */
struct if_state {
	const char	*is_ifname;
	int		 is_exists;
	int		 is_created;
	int		 is_up;		/* AF_LINK IFF_UP, R9 */
	char		*is_mtu;		/* decimal, NULL = unknown */
	struct addr_ent	*is_addrs;		/* "addr/prefix" list, in order */
};

static void	 usage(void) __dead;
static void	 cfg_free(struct cfg *);
static void	 parse_config(int, const char *, struct cfg *);
static void	 validate_config(struct cfg *);
static void	 serialise_config(struct awg_data_io *, struct cfg *);
static void	 apply_config(int, const char *, struct cfg *,
		    struct if_state *);
static void	 state_snapshot(int, const char *, struct if_state *);
static void	 restore_state(int, struct if_state *);
static void	 state_free(struct if_state *);
static int	 show_interface(int, const char *);

/*
 * R4-10/R5/R6: one cleanup path for every exit. The command's parsed config
 * is published here so err()/errx() — which go through exit(3) — wipe the
 * key, HPK and PSK buffers too, not only the success path. R6/R4-10 also
 * registers the transient ioctl buffers (apply_config's serialised SET,
 * read_current's size-probe GET): a parse failure or a SET error exits
 * before the in-line wipe, so the registered buffer is wiped here instead.
 * What cannot be wiped from userland (the kernel's copyin of the ioctl
 * buffer, allocator reuse) is documented in awgctl(8).
 */
static struct cfg *g_active_cfg;

/*
 * R6/R4-10: the live ioctl buffer that carries key material (apply_config's
 * serialised SET, read_current's size-probe GET). Only one is alive at a
 * time, so a single slot is enough; secret_replace() swaps the pointer on a
 * grow so the previous allocation is wiped, not just freed.
 */
struct secret_buf {
	void		*sb_buf;
	size_t		 sb_len;
};
static struct secret_buf g_secret;

static void
secret_register(void *buf, size_t len)
{
	g_secret.sb_buf = buf;
	g_secret.sb_len = len;
}

static void
secret_free(void *buf, size_t len)
{
	explicit_bzero(buf, len);
	free(buf);
	if (g_secret.sb_buf == buf) {
		g_secret.sb_buf = NULL;
		g_secret.sb_len = 0;
	}
}

static void
cleanup_active_cfg(void)
{
	if (g_active_cfg != NULL) {
		cfg_free(g_active_cfg);
		g_active_cfg = NULL;
	}
	if (g_secret.sb_buf != NULL) {
		explicit_bzero(g_secret.sb_buf, g_secret.sb_len);
		free(g_secret.sb_buf);
		g_secret.sb_buf = NULL;
		g_secret.sb_len = 0;
	}
}

/* Replace a secret string, wiping the previous value first. */
static void
free_secret(char **pp)
{
	if (*pp != NULL) {
		explicit_bzero(*pp, strlen(*pp));
		free(*pp);
		*pp = NULL;
	}
}

void
usage(void)
{
	fprintf(stderr, "usage: awgctl up name [--config path]\n"
	    "       awgctl down name\n"
	    "       awgctl show [name]\n"
	    "       awgctl set name KEY VALUE ...\n"
	    "       awgctl add name peer pubkey [endpoint ADDR:PORT] "
	    "[allowed-ips LIST] [psk KEY] [keepalive N]\n"
	    "       awgctl del name peer pubkey\n");
	exit(EX_USAGE);
}

/* Strip a trailing '#' comment and surrounding blanks, in place. */
static void
strip_line(char *line, char **key, char **value)
{
	char *hash, *nl, *end;

	if ((hash = strchr(line, '#')) != NULL)
		*hash = '\0';
	if ((nl = strpbrk(line, "\r\n")) != NULL)
		*nl = '\0';
	end = line + strlen(line);
	while (end > line && isspace((unsigned char)end[-1]))
		*--end = '\0';
	*key = line;
	while (**key && isspace((unsigned char)**key))
		(*key)++;
	*value = strchr(*key, '=');
	if (*value != NULL) {
		*(*value)++ = '\0';
		end = *key + strlen(*key);
		while (end > *key && isspace((unsigned char)end[-1]))
			*--end = '\0';
		while (**value && isspace((unsigned char)**value))
			(*value)++;
	}
}

/*
 * Config trust chain (R4-07). unveil(2) confines the process to CONF_DIR but
 * gives pathname visibility, not an ownership proof, so every component of
 * the path is verified: CONF_DIR and each intermediate directory are
 * root-owned and not group/other-writable, symlinked components are refused,
 * and the final file is a regular 0600/0400 root-owned file. The walk is
 * descriptor-relative (openat/O_DIRECTORY), so the descriptor returned is
 * the one that was checked component by component — a file swapped in the
 * window between check and read cannot slip past, because there is no window:
 * the same descriptor is verified (fstat) and read.
 */
static int
open_trusted(const char *path)
{
	struct stat st;
	char *copy;
	char **comp = NULL;
	int ncomp = 0, cap = 0;
	int fd, i;

	if (strncmp(path, CONF_DIR "/", sizeof(CONF_DIR)) != 0)
		errx(EX_USAGE, "%s: config must live under %s — unveil(2) "
		    "confines awgctl to that directory", path, CONF_DIR);
	if ((copy = strdup(path)) == NULL)
		err(EX_USAGE, "strdup");
	/* Split the absolute path from the root, so /etc and /etc/amnezia
	 * are validated too; empty pieces (from "//") are dropped. */
	{
		char *p = copy;

		if (*p == '/')
			p++;
		for (; p != NULL && *p != '\0'; ) {
			char *slash = strchr(p, '/');

			if (slash != NULL)
				*slash = '\0';
			if (*p != '\0') {
				if (strcmp(p, ".") == 0 ||
				    strcmp(p, "..") == 0)
					errx(EX_USAGE,
					    "%s: component `%s' is not a "
					    "directory name", path, p);
				if (ncomp == cap) {
					cap = cap ? cap * 2 : 8;
					comp = reallocarray(comp, cap,
					    sizeof(*comp));
					if (comp == NULL)
						err(EX_USAGE,
						    "reallocarray");
				}
				comp[ncomp++] = p;
			}
			p = slash != NULL ? slash + 1 : NULL;
		}
	}
	if (ncomp == 0)
		errx(EX_USAGE, "%s: no file under %s", path, CONF_DIR);

	if ((fd = open("/", O_RDONLY | O_CLOEXEC | O_DIRECTORY)) == -1)
		err(EX_USAGE, "%s", "/");
	for (i = 0; i < ncomp; i++) {
		int next, is_last = (i == ncomp - 1);

		/* One open per component: O_NOFOLLOW refuses a symlinked
		 * component, O_NONBLOCK stops a FIFO from blocking. */
		if ((next = openat(fd, comp[i], O_RDONLY | O_CLOEXEC |
		    O_NOFOLLOW | (is_last ? O_NONBLOCK : O_DIRECTORY))) == -1)
			err(EX_USAGE, "%s", path);
		if (fstat(next, &st) == -1)
			err(EX_USAGE, "%s", path);
		close(fd);
		fd = next;
		if (is_last) {
			if (!S_ISREG(st.st_mode))
				errx(EX_USAGE, "%s: not a regular file", path);
			if (!CONF_OWNER_OK(st.st_uid))
				errx(EX_USAGE, "%s must be root-owned — it "
					    "holds the private key", path);
			if ((st.st_mode & 0777) != 0600 &&
			    (st.st_mode & 0777) != 0400)
				errx(EX_USAGE, "%s is mode %o — it holds the "
					    "private key; chmod 600 it", path,
				    st.st_mode & 0777);
		} else {
			if (!S_ISDIR(st.st_mode))
				errx(EX_USAGE, "%s: component `%s' is not a "
					    "directory", path, comp[i]);
			if (!CONF_OWNER_OK(st.st_uid) ||
			    (st.st_mode & 0022) != 0)
				errx(EX_USAGE, "%s: component `%s' must be "
					    "root-owned and not "
					    "group/other-writable", path,
				    comp[i]);
		}
	}
	free(copy);
	return fd;
}

/*
 * Key material for `set`/`add`: a literal, or "-" to read the line from
 * stdin. argv keeps the process listing free of the secret for the "-"
 * form; the file path and stdin are the two documented sources.
 */
static char *
read_secret(const char *val)
{
	char *line = NULL;
	size_t len = 0;
	ssize_t n;

	/* R4-10: "-" (stdin) is the documented form; a literal stays visible
	 * in the process listing, so say so on stderr. */
	if (strcmp(val, "-") != 0) {
		warnx("secret given as a literal argument — it is visible in "
		    "the process listing; prefer '-' (stdin)");
		return strdup(val);
	}
	if ((n = getline(&line, &len, stdin)) == -1)
		err(EX_USAGE, "reading key from stdin");
	while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r'))
		line[--n] = '\0';
	return line;
}

/* "N" or "N-M", both ends at most max — ifconfig's parseawgrange. */
static void
parse_range(const char *val, long long max, struct awg_range *r,
    const char *name)
{
	const char *errmsg = NULL;
	char *buf, *hi;

	if ((buf = strdup(val)) == NULL)
		err(EX_USAGE, "strdup");
	if ((hi = strchr(buf, '-')) != NULL)
		*hi++ = '\0';
	r->r_lo = (uint32_t)strtonum(buf, 0, max, &errmsg);
	if (errmsg)
		errx(EX_CONFIG, "%s: %s, %s", name, val, errmsg);
	r->r_hi = hi != NULL ? (uint32_t)strtonum(hi, 0, max, &errmsg) :
	    r->r_lo;
	if (errmsg)
		errx(EX_CONFIG, "%s: %s, %s", name, val, errmsg);
	if (r->r_hi < r->r_lo)
		errx(EX_CONFIG, "%s: %s, invalid range", name, val);
	free(buf);
}

/* Base64 key of exactly AWG_KEY_LEN bytes, as ifconfig's AWG_LOAD_KEY. */
static void
load_key(const char *b64, const char *name)
{
	uint8_t tmp[AWG_KEY_LEN];
	int r;

	if (strlen(b64) != AWG_BASE64_KEY_LEN)
		errx(EX_CONFIG, "%s (key): invalid length", name);
	if ((r = b64_pton(b64, tmp, sizeof(tmp))) != (int)sizeof(tmp))
		errx(EX_CONFIG, "%s (key): invalid base64 %d/%zu", name, r,
		    sizeof(tmp));
	explicit_bzero(tmp, sizeof(tmp));
}

static int
parse_bool(const char *val, const char *name)
{
	const char *errmsg = NULL;
	long v;

	if (strcmp(val, "on") == 0)
		return 1;
	if (strcmp(val, "off") == 0)
		return 0;
	v = strtonum(val, 0, 1, &errmsg);
	if (errmsg)
		errx(EX_CONFIG, "%s: boolean value is neither on/off nor "
		    "0/1: `%s'", name, val);
	return v != 0;
}

static void
peer_add_aip(struct peer_cfg *p, const char *list)
{
	char *copy, *tok, *saveptr;

	if ((copy = strdup(list)) == NULL)
		err(EX_USAGE, "strdup");
	for (tok = strtok_r(copy, ",", &saveptr); tok != NULL;
	    tok = strtok_r(NULL, ",", &saveptr)) {
		p->pc_aips = reallocarray(p->pc_aips, p->pc_n_aips + 1,
		    sizeof(*p->pc_aips));
		if (p->pc_aips == NULL)
			err(EX_USAGE, "reallocarray");
		p->pc_aips[p->pc_n_aips++] = strdup(tok);
	}
	free(copy);
}

static struct peer_cfg *
peer_new(struct cfg *c)
{
	struct peer_cfg *p = calloc(1, sizeof(*p));

	if (p == NULL)
		err(EX_USAGE, "calloc");
	c->c_peer = reallocarray(c->c_peer, c->c_peers + 1, sizeof(*c->c_peer));
	if (c->c_peer == NULL)
		err(EX_USAGE, "reallocarray");
	c->c_peer[c->c_peers++] = p;
	return p;
}

/*
 * wg-quick grammar (see docs/ADMIN-INTERFACE.md and sbin/awg-quick):
 * [Interface] and [Peer] sections, "Key = value", '#' comments.
 * An unknown key refuses the whole file (exit 2), like awg-quick.
 */
static void
parse_config(int fd, const char *path, struct cfg *c)
{
	FILE *f;
	char *line = NULL;
	size_t linesize = 0;
	ssize_t nread;
	int in_interface = 0, in_peer = 0;
	struct peer_cfg *p = NULL;
	char *key, *value;

	/* R4-07: the descriptor was opened and verified by open_trusted(). */
	if ((f = fdopen(fd, "r")) == NULL)
		err(EX_USAGE, "%s", path);

	while ((nread = getline(&line, &linesize, f)) != -1) {
		/* R7/N12: a NUL inside the file marks a truncated config —
		 * the bytes after it are never parsed, so refuse the whole
		 * file before any mutation instead of applying the prefix
		 * that happens to parse. */
		if (memchr(line, '\0', (size_t)nread) != NULL)
			errx(EX_CONFIG, "%s: NUL byte in config (truncated)",
			    path);
		strip_line(line, &key, &value);
		if (*key == '\0')
			continue;
		if (strcmp(key, "[Interface]") == 0) {
			in_interface = 1;
			in_peer = 0;
			p = NULL;
			continue;
		}
		if (strcmp(key, "[Peer]") == 0) {
			in_peer = 1;
			in_interface = 0;
			p = peer_new(c);
			continue;
		}
		if (value == NULL)
			errx(EX_CONFIG, "%s: no '=' in `%s'", path, key);

		if (in_interface) {
			if (strcmp(key, "PrivateKey") == 0) {
				load_key(value, "PrivateKey");
				free_secret(&c->c_private);
				c->c_private = strdup(value);
				c->c_update_private = 1;
			} else if (strcmp(key, "ListenPort") == 0) {
				const char *errmsg = NULL;
				c->c_port = strtonum(value, 0, 65535,
				    &errmsg);
				if (errmsg)
					errx(EX_CONFIG, "ListenPort: %s, %s",
					    value, errmsg);
				c->c_has_port = 1;
			} else if (strcmp(key, "Address") == 0) {
				char *copy = strdup(value), *tok, *sp;
				for (tok = strtok_r(copy, ",", &sp); tok;
				    tok = strtok_r(NULL, ",", &sp)) {
					c->c_addrs = reallocarray(c->c_addrs,
					    c->c_n_addrs + 1,
					    sizeof(*c->c_addrs));
					if (c->c_addrs == NULL)
						err(EX_USAGE, "reallocarray");
					c->c_addrs[c->c_n_addrs++] =
					    strdup(tok);
				}
				free(copy);
			} else if (strcmp(key, "MTU") == 0) {
				/* R4-08: the driver's SIOCSIFMTU accepts 1..9000
				 * (src/if_awg.c); the wg-quick floor is 576. */
				const char *errmsg = NULL;
				c->c_mtu_num = strtonum(value, 576, 9000,
				    &errmsg);
				if (errmsg)
					errx(EX_CONFIG, "MTU: %s, %s", value,
					    errmsg);
				free(c->c_mtu);
				c->c_mtu = strdup(value);
				c->c_has_mtu = 1;
			} else if (strcmp(key, "DNS") == 0) {
				char *copy = strdup(value), *tok, *sp;
				for (tok = strtok_r(copy, ",", &sp); tok;
				    tok = strtok_r(NULL, ",", &sp)) {
					c->c_dns = reallocarray(c->c_dns,
					    c->c_n_dns + 1,
					    sizeof(*c->c_dns));
					if (c->c_dns == NULL)
						err(EX_USAGE, "reallocarray");
					c->c_dns[c->c_n_dns++] = strdup(tok);
				}
				free(copy);
			} else if (strcmp(key, "Table") == 0) {
				const char *errmsg = NULL;
				c->c_rtable = strtonum(value, 0,
				    RT_TABLEID_MAX, &errmsg);
				if (errmsg)
					errx(EX_CONFIG, "Table: %s, %s",
					    value, errmsg);
				c->c_has_rtable = 1;
			} else if (strcmp(key, "Version") == 0) {
				if (strcmp(value, "legacy") == 0)
					c->c_version = AWG_VERSION_LEGACY;
				else if (strcmp(value, "3.1") == 0)
					c->c_version = AWG_VERSION_3_1;
				else
					errx(EX_CONFIG,
					    "Version: %s, must be legacy or 3.1",
					    value);
				c->c_has_version = 1;
			} else if (strcmp(key, "Jc") == 0) {
				const char *errmsg = NULL;
				c->c_jc = strtonum(value, 0, AWG_MAX_JUNK,
				    &errmsg);
				if (errmsg)
					errx(EX_CONFIG, "Jc: %s, %s", value,
					    errmsg);
				c->c_has_jc = 1;
			} else if (strcmp(key, "Jmin") == 0) {
				const char *errmsg = NULL;
				c->c_jmin = strtonum(value, 0,
				    AWG_MAX_JUNK_SIZE, &errmsg);
				if (errmsg)
					errx(EX_CONFIG, "Jmin: %s, %s", value,
					    errmsg);
				c->c_has_jc = 1;
			} else if (strcmp(key, "Jmax") == 0) {
				const char *errmsg = NULL;
				c->c_jmax = strtonum(value, 0,
				    AWG_MAX_JUNK_SIZE, &errmsg);
				if (errmsg)
					errx(EX_CONFIG, "Jmax: %s, %s", value,
					    errmsg);
				c->c_has_jc = 1;
			} else if (strcmp(key, "S1") == 0) {
				const char *errmsg = NULL;
				c->c_s1 = strtonum(value, 0, AWG_MAX_PADDING,
				    &errmsg);
				if (errmsg)
					errx(EX_CONFIG, "S1: %s, %s", value,
					    errmsg);
				c->c_has_s12 = 1;
			} else if (strcmp(key, "S2") == 0) {
				const char *errmsg = NULL;
				c->c_s2 = strtonum(value, 0, AWG_MAX_PADDING,
				    &errmsg);
				if (errmsg)
					errx(EX_CONFIG, "S2: %s, %s", value,
					    errmsg);
				c->c_has_s12 = 1;
			} else if (strcmp(key, "S3") == 0) {
				const char *errmsg = NULL;
				c->c_s3 = strtonum(value, 0, AWG_MAX_PADDING,
				    &errmsg);
				if (errmsg)
					errx(EX_CONFIG, "S3: %s, %s", value,
					    errmsg);
				c->c_has_s3 = 1;
			} else if (strcmp(key, "S4") == 0) {
				const char *errmsg = NULL;
				c->c_s4 = strtonum(value, 0, AWG_MAX_PADDING,
				    &errmsg);
				if (errmsg)
					errx(EX_CONFIG, "S4: %s, %s", value,
					    errmsg);
				c->c_has_s4 = 1;
			} else if (strcmp(key, "H1") == 0) {
				parse_range(value, AWG_H_MAX, &c->c_h1, "H1");
				c->c_has_h = 1;
			} else if (strcmp(key, "H2") == 0) {
				parse_range(value, AWG_H_MAX, &c->c_h2, "H2");
				c->c_has_h = 1;
			} else if (strcmp(key, "H3") == 0) {
				parse_range(value, AWG_H_MAX, &c->c_h3, "H3");
				c->c_has_h = 1;
			} else if (strcmp(key, "H4") == 0) {
				parse_range(value, AWG_H_MAX, &c->c_h4, "H4");
				c->c_has_h = 1;
			} else if (strcmp(key, "I1") == 0) {
				if (strlen(value) >= AWG_ISPEC_MAXLEN)
					errx(EX_CONFIG, "I1: too long");
				free(c->c_ispec[0]);
				c->c_ispec[0] = strdup(value);
				c->c_has_i[0] = 1;
			} else if (strcmp(key, "I2") == 0) {
				if (strlen(value) >= AWG_ISPEC_MAXLEN)
					errx(EX_CONFIG, "I2: too long");
				free(c->c_ispec[1]);
				c->c_ispec[1] = strdup(value);
				c->c_has_i[1] = 1;
			} else if (strcmp(key, "I3") == 0) {
				if (strlen(value) >= AWG_ISPEC_MAXLEN)
					errx(EX_CONFIG, "I3: too long");
				free(c->c_ispec[2]);
				c->c_ispec[2] = strdup(value);
				c->c_has_i[2] = 1;
			} else if (strcmp(key, "I4") == 0) {
				if (strlen(value) >= AWG_ISPEC_MAXLEN)
					errx(EX_CONFIG, "I4: too long");
				free(c->c_ispec[3]);
				c->c_ispec[3] = strdup(value);
				c->c_has_i[3] = 1;
			} else if (strcmp(key, "I5") == 0) {
				if (strlen(value) >= AWG_ISPEC_MAXLEN)
					errx(EX_CONFIG, "I5: too long");
				free(c->c_ispec[4]);
				c->c_ispec[4] = strdup(value);
				c->c_has_i[4] = 1;
			} else if (strcmp(key, "HeaderProtectionKey") == 0) {
				load_key(value, "HeaderProtectionKey");
				free_secret(&c->c_hpk);
				c->c_hpk = strdup(value);
				c->c_has_hpk = 1;
				c->c_update_hpk = 1;
			} else if (strcmp(key, "ContentPaddingAddition") == 0) {
				parse_range(value, AWG_RANGE_MAX, &c->c_cpa,
				    "ContentPaddingAddition");
				c->c_has_cpa = 1;
			} else if (strcmp(key, "RekeyAfterTime") == 0) {
				parse_range(value, AWG_RANGE_MAX,
				    &c->c_rekey_after, "RekeyAfterTime");
				c->c_has_rekey_after = 1;
			} else if (strcmp(key, "RekeyTimeout") == 0) {
				parse_range(value, AWG_RANGE_MAX,
				    &c->c_rekey_timeout, "RekeyTimeout");
				c->c_has_rekey_timeout = 1;
			} else if (strcmp(key, "RejectAfterTime") == 0) {
				parse_range(value, AWG_RANGE_MAX,
				    &c->c_reject_after, "RejectAfterTime");
				c->c_has_reject_after = 1;
			} else if (strcmp(key, "KeepaliveTimeout") == 0) {
				parse_range(value, AWG_RANGE_MAX,
				    &c->c_keepalive_timeout,
				    "KeepaliveTimeout");
				c->c_has_keepalive_timeout = 1;
			} else if (strcmp(key, "MaxHandshakeAttempts") == 0) {
				parse_range(value, AWG_RANGE_MAX,
				    &c->c_max_attempts,
				    "MaxHandshakeAttempts");
				c->c_has_max_attempts = 1;
			} else if (strcmp(key, "RandomTrailers") == 0) {
				c->c_random_trailers =
				    parse_bool(value, "RandomTrailers");
				c->c_has_trailers = 1;
			} else if (strcmp(key, "DisableCookies") == 0) {
				c->c_disable_cookies =
				    parse_bool(value, "DisableCookies");
				c->c_has_cookies = 1;
			} else
				errx(EX_CONFIG, "%s: unknown key `%s'", path,
				    key);
		} else if (in_peer) {
			if (strcmp(key, "PublicKey") == 0) {
				load_key(value, "PublicKey");
				free(p->pc_pubkey);
				p->pc_pubkey = strdup(value);
			} else if (strcmp(key, "PresharedKey") == 0) {
				load_key(value, "PresharedKey");
				free_secret(&p->pc_psk);
				p->pc_psk = strdup(value);
			} else if (strcmp(key, "Endpoint") == 0) {
				free(p->pc_endpoint);
				p->pc_endpoint = strdup(value);
			} else if (strcmp(key, "AllowedIPs") == 0) {
				peer_add_aip(p, value);
			} else if (strcmp(key, "PersistentKeepalive") == 0) {
				parse_range(value, AWG_PKA_MAX,
				    &(struct awg_range){0},
				    "PersistentKeepalive");
				free(p->pc_pka);
				p->pc_pka = strdup(value);
			} else
				errx(EX_CONFIG, "%s: unknown key `%s'", path,
				    key);
		} else
			errx(EX_CONFIG, "%s: key `%s' outside a section",
			    path, key);
	}
	/* R4-08: a read error after a valid prefix must not turn a truncated
	 * peer list into an authoritative replacement. */
	if (ferror(f))
		err(EX_USAGE, "%s", path);
	if (fclose(f) != 0)
		err(EX_USAGE, "%s", path);
	free(line);

	if (c->c_private == NULL)
		errx(EX_CONFIG, "PrivateKey not set in %s", path);
}

static int
range_overlap(struct awg_range a, struct awg_range b)
{
	return a.r_lo <= b.r_hi && b.r_lo <= a.r_hi;
}

/*
 * Validate the cross-field constraints the kernel enforces in
 * awg_ioctl_check(), so a bad file is refused before the ioctl:
 * jmin <= jmax, H ranges disjoint (3.1), reject > keepalive + rekey,
 * HPK needs S1-S4 >= 12, 3.1 keys rejected on a legacy interface.
 */
static void
validate_config(struct cfg *c)
{
	int version = c->c_has_version ? c->c_version : AWG_VERSION_LEGACY;
	int has31 = c->c_has_s3 || c->c_has_s4 || c->c_has_hpk ||
	    c->c_has_cpa || c->c_has_trailers || c->c_has_cookies ||
	    c->c_has_rekey_after || c->c_has_rekey_timeout ||
	    c->c_has_reject_after || c->c_has_keepalive_timeout ||
	    c->c_has_max_attempts || c->c_has_i[0] || c->c_has_i[1] ||
	    c->c_has_i[2] || c->c_has_i[3] || c->c_has_i[4];
	int i, j;
	long s1, s2, s3, s4, jc, jmin, jmax;
	struct awg_range h[4], rj, ka, rt;

	if (version == AWG_VERSION_LEGACY && has31)
		errx(EX_CONFIG,
		    "Version = legacy, but AmneziaWG 3.1 parameters are set");

	if (c->c_has_jc) {
		jc = c->c_jc;
		jmin = c->c_has_jc ? c->c_jmin : 0;
		jmax = c->c_has_jc ? c->c_jmax : 0;
		if (jc != 0 && (jmin == 0 || jmin > jmax))
			errx(EX_CONFIG,
			    "Jc: %ld, Jmin %ld, Jmax %ld, invalid junk range",
			    jc, jmin, jmax);
	}
	if (c->c_has_h) {
		h[0] = c->c_h1;
		h[1] = c->c_h2;
		h[2] = c->c_h3;
		h[3] = c->c_h4;
		for (i = 0; i < 4; i++)
			for (j = i + 1; j < 4; j++)
				if (range_overlap(h[i], h[j]))
					errx(EX_CONFIG,
					    "H%d and H%d ranges overlap",
					    i + 1, j + 1);
	}
	/* Kernel merges timing with the stored defaults; mirror them. */
	rj = c->c_has_reject_after ? c->c_reject_after
	    : (struct awg_range){ DEF_REJECT_AFTER_TIME,
		DEF_REJECT_AFTER_TIME };
	ka = c->c_has_keepalive_timeout ? c->c_keepalive_timeout
	    : (struct awg_range){ DEF_KEEPALIVE_TIMEOUT,
		DEF_KEEPALIVE_TIMEOUT };
	rt = c->c_has_rekey_timeout ? c->c_rekey_timeout
	    : (struct awg_range){ DEF_REKEY_TIMEOUT, DEF_REKEY_TIMEOUT };
	if (rj.r_lo != 0 &&
	    rj.r_lo <= ka.r_lo + rt.r_lo)
		errx(EX_CONFIG,
		    "RejectAfterTime %u-%u, KeepaliveTimeout %u, "
		    "RekeyTimeout %u: reject must exceed keepalive + rekey",
		    rj.r_lo, rj.r_hi, ka.r_lo, rt.r_lo);
	s1 = c->c_has_s12 ? c->c_s1 : 0;
	s2 = c->c_has_s12 ? c->c_s2 : 0;
	s3 = c->c_has_s3 ? c->c_s3 : 0;
	s4 = c->c_has_s4 ? c->c_s4 : 0;
	if (c->c_has_hpk && (s1 < AWG_HPK_NONCE_LEN || s2 < AWG_HPK_NONCE_LEN ||
	    s3 < AWG_HPK_NONCE_LEN || s4 < AWG_HPK_NONCE_LEN))
		errx(EX_CONFIG,
		    "HeaderProtectionKey requires S1, S2, S3 and S4 of at "
		    "least %d", AWG_HPK_NONCE_LEN);

	for (i = 0; i < c->c_peers; i++)
		if (c->c_peer[i]->pc_pubkey == NULL)
			errx(EX_CONFIG, "peer %d: PublicKey not set", i + 1);
}

/*
 * R4-10: release the parsed config, wiping the secret-bearing strings first.
 * explicit_bzero(3) clears the bytes; the allocator may still keep them in a
 * freed chunk (documented in docs/ADMIN-INTERFACE.md — the kernel's copyin
 * and the allocator are the two copies this tool cannot erase).
 */
static void
cfg_free(struct cfg *c)
{
	int i, j;

	for (i = 0; i < AWG_ISPEC_COUNT; i++) {
		if (c->c_ispec[i] == NULL)
			continue;
		explicit_bzero(c->c_ispec[i], strlen(c->c_ispec[i]));
		free(c->c_ispec[i]);
		c->c_ispec[i] = NULL;
	}
	if (c->c_private != NULL) {
		explicit_bzero(c->c_private, strlen(c->c_private));
		free(c->c_private);
		c->c_private = NULL;
	}
	if (c->c_hpk != NULL) {
		explicit_bzero(c->c_hpk, strlen(c->c_hpk));
		free(c->c_hpk);
		c->c_hpk = NULL;
	}
	free(c->c_mtu);
	c->c_mtu = NULL;
	for (i = 0; i < c->c_n_addrs; i++)
		free(c->c_addrs[i]);
	free(c->c_addrs);
	c->c_addrs = NULL;
	c->c_n_addrs = 0;
	for (i = 0; i < c->c_n_dns; i++)
		free(c->c_dns[i]);
	free(c->c_dns);
	c->c_dns = NULL;
	c->c_n_dns = 0;
	for (i = 0; i < c->c_peers; i++) {
		struct peer_cfg *p = c->c_peer[i];

		if (p == NULL)
			continue;
		if (p->pc_psk != NULL) {
			explicit_bzero(p->pc_psk, strlen(p->pc_psk));
			free(p->pc_psk);
			p->pc_psk = NULL;
		}
		free(p->pc_pubkey);
		free(p->pc_endpoint);
		free(p->pc_pka);
		for (j = 0; j < p->pc_n_aips; j++)
			free(p->pc_aips[j]);
		free(p->pc_aips);
		free(p);
	}
	free(c->c_peer);
	c->c_peer = NULL;
	c->c_peers = 0;
	if (g_active_cfg == c)
		g_active_cfg = NULL;
}

/*
 * Serialise the interface fields and every peer into the kernel buffer
 * layout ifconfig builds with ensureawginterface()/growawgdata().
 * R7/R4-08: this is the parse/resolve/serialise half — base64 keys,
 * PersistentKeepalive ranges, peer endpoints (getaddrinfo) and AllowedIPs
 * (inet_net_pton) all run here, so `up` completes every field before it
 * mutates the interface.
 */
static void
serialise_config(struct awg_data_io *d, struct cfg *c)
{
	struct awg_interface_io *ip;
	struct awg_peer_io *pp;
	struct awg_aip_io *ap;
	size_t size = sizeof(*ip);
	int i, j;

	for (i = 0; i < c->c_peers; i++)
		size += sizeof(struct awg_peer_io) +
		    (size_t)c->c_peer[i]->pc_n_aips * sizeof(struct awg_aip_io);

	d->awgd_size = size;
	if ((d->awgd_interface = calloc(1, size)) == NULL)
		err(EX_USAGE, "calloc");
	/* R6/R4-10: register the serialised buffer (private key + PSKs)
	 * with the atexit cleanup so a parse failure or SIOCSAWG error
	 * that exits before the wipe below still clears it. */
	secret_register(d->awgd_interface, size);
	ip = d->awgd_interface;

	/*
	 * The kernel walks i_peers_count entries (src/if_awg.c
	 * awg_ioctl_set); a zeroed count means the serialised peers never
	 * reach it. REPLACE_PEERS makes the whole list authoritative for
	 * the full-config path, so `up` with a two-peer file leaves
	 * exactly those two peers behind.
	 */
	ip->i_peers_count = (size_t)c->c_peers;
	if (c->c_replace_peers)
		ip->i_flags |= AWG_INTERFACE_REPLACE_PEERS;

	if (c->c_update_private && c->c_private != NULL) {
		ip->i_flags |= AWG_INTERFACE_HAS_PRIVATE;
		b64_pton(c->c_private, ip->i_private, sizeof(ip->i_private));
	}
	if (c->c_update_hpk && c->c_hpk != NULL) {
		ip->i_flags |= AWG_INTERFACE_HAS_HPK;
		b64_pton(c->c_hpk, ip->i_hpk, sizeof(ip->i_hpk));
	}
	if (c->c_has_port) {
		ip->i_flags |= AWG_INTERFACE_HAS_PORT;
		ip->i_port = c->c_port;
	}
	if (c->c_has_rtable) {
		ip->i_flags |= AWG_INTERFACE_HAS_RTABLE;
		ip->i_rtable = c->c_rtable;
	}
	/*
	 * Send the version only when the input specifies one (a config
	 * file infers 3.1 from any 3.1 key, `set ... Version` names it).
	 * The kernel keeps the stored version when HAS_VERSION is clear;
	 * always setting it resets a 3.1 interface to legacy on every
	 * partial command.
	 */
	if (c->c_has_version) {
		ip->i_flags |= AWG_INTERFACE_HAS_VERSION;
		ip->i_version = c->c_version;
	}
	if (c->c_has_jc) {
		ip->i_flags |= AWG_INTERFACE_HAS_JC;
		ip->i_jc = c->c_jc;
		ip->i_jmin = c->c_jmin;
		ip->i_jmax = c->c_jmax;
	}
	if (c->c_has_s12) {
		ip->i_flags |= AWG_INTERFACE_HAS_S12;
		ip->i_s1 = c->c_s1;
		ip->i_s2 = c->c_s2;
	}
	if (c->c_has_s3) {
		ip->i_flags |= AWG_INTERFACE_HAS_S3;
		ip->i_s3 = c->c_s3;
	}
	if (c->c_has_s4) {
		ip->i_flags |= AWG_INTERFACE_HAS_S4;
		ip->i_s4 = c->c_s4;
	}
	if (c->c_has_h) {
		ip->i_flags |= AWG_INTERFACE_HAS_H;
		ip->i_h1 = c->c_h1;
		ip->i_h2 = c->c_h2;
		ip->i_h3 = c->c_h3;
		ip->i_h4 = c->c_h4;
	}
	if (c->c_has_cpa) {
		ip->i_flags |= AWG_INTERFACE_HAS_CPA;
		ip->i_cpa = c->c_cpa;
	}
	if (c->c_has_rekey_after) {
		ip->i_flags |= AWG_INTERFACE_HAS_REKEY_AFTER_TIME;
		ip->i_rekey_after_time = c->c_rekey_after;
	}
	if (c->c_has_rekey_timeout) {
		ip->i_flags |= AWG_INTERFACE_HAS_REKEY_TIMEOUT;
		ip->i_rekey_timeout = c->c_rekey_timeout;
	}
	if (c->c_has_reject_after) {
		ip->i_flags |= AWG_INTERFACE_HAS_REJECT_AFTER_TIME;
		ip->i_reject_after_time = c->c_reject_after;
	}
	if (c->c_has_keepalive_timeout) {
		ip->i_flags |= AWG_INTERFACE_HAS_KEEPALIVE_TIMEOUT;
		ip->i_keepalive_timeout = c->c_keepalive_timeout;
	}
	if (c->c_has_max_attempts) {
		ip->i_flags |= AWG_INTERFACE_HAS_MAX_HANDSHAKE_ATTEMPTS;
		ip->i_max_handshake_attempts = c->c_max_attempts;
	}
	if (c->c_has_trailers) {
		ip->i_flags |= AWG_INTERFACE_HAS_TRAILERS;
		ip->i_random_trailers = c->c_random_trailers;
	}
	if (c->c_has_cookies) {
		ip->i_flags |= AWG_INTERFACE_HAS_COOKIES;
		ip->i_disable_cookies = c->c_disable_cookies;
	}
	for (i = 0; i < AWG_ISPEC_COUNT; i++) {
		if (!c->c_has_i[i])
			continue;
		ip->i_flags |= AWG_INTERFACE_HAS_I(i);
		ip->i_ispec[i] = c->c_ispec[i];
	}

	pp = &ip->i_peers[0];
	for (i = 0; i < c->c_peers; i++) {
		struct peer_cfg *pc = c->c_peer[i];
		struct addrinfo *ai = NULL;

		pp->p_flags |= AWG_PEER_HAS_PUBLIC;
		if (pc->pc_remove)
			pp->p_flags |= AWG_PEER_REMOVE;
		b64_pton(pc->pc_pubkey, pp->p_public, sizeof(pp->p_public));
		if (pc->pc_psk != NULL) {
			pp->p_flags |= AWG_PEER_HAS_PSK;
			b64_pton(pc->pc_psk, pp->p_psk, sizeof(pp->p_psk));
		}
		if (pc->pc_pka != NULL) {
			struct awg_range r;

			parse_range(pc->pc_pka, AWG_PKA_MAX, &r,
			    "PersistentKeepalive");
			pp->p_flags |= AWG_PEER_HAS_PKA;
			pp->p_pka = r.r_lo;
			pp->p_pka_hi = r.r_hi != r.r_lo ? r.r_hi : 0;
		}
		if (pc->pc_endpoint != NULL) {
			char *host, *service, *br;
			int error;

			if ((host = strdup(pc->pc_endpoint)) == NULL)
				err(EX_USAGE, "strdup");
			if ((br = strchr(host, ']')) != NULL &&
			    br[1] == ':') {
				*br = '\0';
				service = br + 2;
				if (host[0] == '[')
					memmove(host, host + 1,
					    strlen(host + 1));
			} else if ((service = strrchr(host, ':')) != NULL)
				*service++ = '\0';
			else
				errx(EX_CONFIG, "Endpoint: %s, missing port",
				    pc->pc_endpoint);
			if ((error = getaddrinfo(host, service, NULL,
			    &ai)) != 0)
				errx(EX_CONFIG, "Endpoint: %s",
				    gai_strerror(error));
			pp->p_flags |= AWG_PEER_HAS_ENDPOINT;
			memcpy(&pp->p_sa, ai->ai_addr,
			    MIN(ai->ai_addrlen, sizeof(pp->p_endpoint)));
			AWG_SA_LEN(pp->p_sa) = ai->ai_addrlen;
			freeaddrinfo(ai);
			free(host);
		}
		if (pc->pc_n_aips > 0)
			pp->p_flags |= AWG_PEER_REPLACE_AIPS;
		ap = &pp->p_aips[0];
		for (j = 0; j < pc->pc_n_aips; j++) {
			int res;

			if ((res = inet_net_pton(AF_INET, pc->pc_aips[j],
			    &ap->a_ipv4, sizeof(ap->a_ipv4))) != -1)
				ap->a_af = AF_INET;
			else if ((res = inet_net_pton(AF_INET6,
			    pc->pc_aips[j], &ap->a_ipv6,
			    sizeof(ap->a_ipv6))) != -1)
				ap->a_af = AF_INET6;
			else
				errx(EX_CONFIG, "AllowedIPs: %s, bad address",
				    pc->pc_aips[j]);
			ap->a_cidr = res;
			ap++;
		}
		pp->p_aips_count = pc->pc_n_aips;
		pp = (struct awg_peer_io *)ap;
	}
}

/*
 * One SIOCSAWG carrying the serialised interface and peers.  R7/R4-08: the
 * mutation half — serialise_config() has parsed, resolved and built the
 * whole payload first, so an operational failure restores the state captured
 * before the mutations (an interface this run created is destroyed; a
 * pre-existing one gets its MTU and addresses back) and the secret buffer
 * is wiped.  `prev` may be NULL for callers that do not snapshot.
 */
static void
apply_config(int s, const char *name, struct cfg *c, struct if_state *prev)
{
	struct awg_data_io d;

	memset(&d, 0, sizeof(d));
	strlcpy(d.awgd_name, name, sizeof(d.awgd_name));
	serialise_config(&d, c);
	if (ioctl(s, SIOCSAWG, &d) == -1) {
		int e = errno;

		restore_state(s, prev);
		secret_free(d.awgd_interface, d.awgd_size);
		errx(e, "%s: SIOCSAWG", d.awgd_name);
	}
	/* R4-10: the serialised buffer carries the private key and PSKs. */
	secret_free(d.awgd_interface, d.awgd_size);
}

/*
 * Size-probe get, following ifconfig's awg_status (patch lines 673-695):
 * the kernel reports the full size in awgd_size, so grow and re-issue until
 * it stops growing. A fixed buffer truncates peers. The first call must
 * carry a real buffer: awg_ioctl_get copies struct awg_interface_io to
 * awgd_interface before it looks at awgd_size, so a NULL probe faults
 * (EFAULT) instead of returning the size. Seed with the base struct and
 * seed the I1-I5 receive buffers so the get path has somewhere to copy the
 * ispec strings. AWGCTL_DEBUG traces each probe (iteration, requested and
 * returned size, errno) on stderr; the shipped tool stays quiet.
 */
static int
show_interface(int s, const char *name)
{
	static char ispec[AWG_ISPEC_COUNT][AWG_ISPEC_MAXLEN];
	struct awg_data_io d;
	struct awg_interface_io *ip;
	struct awg_peer_io *pp;
	struct awg_aip_io *ap;
	struct timespec now;
	size_t i, j, last_size;
	unsigned int iter = 0;
	int dbg = getenv("AWGCTL_DEBUG") != NULL;
	char hbuf[NI_MAXHOST], sbuf[NI_MAXSERV];
	char key[AWG_BASE64_KEY_LEN + 1];
	struct awg_range *timing[5];
	const char *timing_name[5] = { "awgrekeyaftertime",
	    "awgrekeytimeout", "awgrejectaftertime", "awgkeepalivetimeout",
	    "awgmaxhandshakeattempts" };

	memset(&d, 0, sizeof(d));
	strlcpy(d.awgd_name, name, sizeof(d.awgd_name));
	d.awgd_size = sizeof(struct awg_interface_io);
	if ((d.awgd_interface = calloc(1, d.awgd_size)) == NULL)
		err(EX_USAGE, "calloc");
	for (i = 0; i < AWG_ISPEC_COUNT; i++) {
		d.awgd_interface->i_ispec[i] = ispec[i];
		d.awgd_interface->i_ispec_len[i] = sizeof(ispec[i]);
	}
	last_size = 0;
	for (;;) {
		int saved_errno;

		iter++;
		if (ioctl(s, SIOCGAWG, &d) < 0) {
			saved_errno = errno;
			if (dbg)
				fprintf(stderr,
				    "awgctl: debug: SIOCGAWG iter %u "
				    "req %zu -> size %zu errno %d (%s)\n",
				    iter, last_size, d.awgd_size,
				    saved_errno, strerror(saved_errno));
			if (saved_errno == ENOTTY) {
				/* R4-09: an explicitly named target that the
				 * driver rejects must report failure. */
				explicit_bzero(d.awgd_interface,
				    d.awgd_size);
				free(d.awgd_interface);
				return (1);
			}
			errno = saved_errno;
			err(EX_IOCTL, "%s: SIOCGAWG", d.awgd_name);
		}
		if (dbg)
			fprintf(stderr,
			    "awgctl: debug: SIOCGAWG iter %u req %zu "
			    "-> size %zu ok\n",
			    iter, last_size, d.awgd_size);
		if (last_size >= d.awgd_size)
			break;
		d.awgd_interface = realloc(d.awgd_interface, d.awgd_size);
		if (d.awgd_interface == NULL)
			err(EX_USAGE, "realloc");
		for (i = 0; i < AWG_ISPEC_COUNT; i++) {
			d.awgd_interface->i_ispec[i] = ispec[i];
			d.awgd_interface->i_ispec_len[i] = sizeof(ispec[i]);
		}
		last_size = d.awgd_size;
	}
	ip = d.awgd_interface;

	printf("%s:\n", name);
	if (ip->i_flags & AWG_INTERFACE_HAS_PORT)
		printf("\tawgport %hu\n", ip->i_port);
	if (ip->i_flags & AWG_INTERFACE_HAS_RTABLE)
		printf("\tawgrtable %d\n", ip->i_rtable);
	if (ip->i_flags & AWG_INTERFACE_HAS_PUBLIC) {
		b64_ntop(ip->i_public, AWG_KEY_LEN, key, sizeof(key));
		printf("\tawgpubkey %s\n", key);
	}
	if (ip->i_flags & AWG_INTERFACE_HAS_VERSION)
		printf("\tawgversion %s\n",
		    ip->i_version == AWG_VERSION_3_1 ? "3.1" : "legacy");
	if (ip->i_flags & AWG_INTERFACE_HAS_JC) {
		printf("\tawgjc %u\n", ip->i_jc);
		printf("\tawgjmin %u\n", ip->i_jmin);
		printf("\tawgjmax %u\n", ip->i_jmax);
	}
	if (ip->i_flags & AWG_INTERFACE_HAS_S12) {
		printf("\tawgs1 %u\n", ip->i_s1);
		printf("\tawgs2 %u\n", ip->i_s2);
		if (ip->i_flags & AWG_INTERFACE_HAS_S3)
			printf("\tawgs3 %u\n", ip->i_s3);
		if (ip->i_flags & AWG_INTERFACE_HAS_S4)
			printf("\tawgs4 %u\n", ip->i_s4);
	}
	if (ip->i_flags & AWG_INTERFACE_HAS_H) {
		printf("	awgh1 %u", ip->i_h1.r_lo);
		if (ip->i_h1.r_hi != ip->i_h1.r_lo)
			printf("-%u", ip->i_h1.r_hi);
		printf("\n");
		printf("	awgh2 %u", ip->i_h2.r_lo);
		if (ip->i_h2.r_hi != ip->i_h2.r_lo)
			printf("-%u", ip->i_h2.r_hi);
		printf("\n");
		printf("	awgh3 %u", ip->i_h3.r_lo);
		if (ip->i_h3.r_hi != ip->i_h3.r_lo)
			printf("-%u", ip->i_h3.r_hi);
		printf("\n");
		printf("	awgh4 %u", ip->i_h4.r_lo);
		if (ip->i_h4.r_hi != ip->i_h4.r_lo)
			printf("-%u", ip->i_h4.r_hi);
		printf("\n");
	}
	for (i = 0; i < AWG_ISPEC_COUNT; i++)
		if (ip->i_flags & AWG_INTERFACE_HAS_I(i) &&
		    ip->i_ispec_len[i] <= sizeof(ispec[i]))
			printf("\tawgi%zu %s\n", i + 1, ispec[i]);
	if (ip->i_flags & AWG_INTERFACE_HAS_HPK)
		printf("\tawghpk (present)\n");
	if (ip->i_flags & AWG_INTERFACE_HAS_CPA && ip->i_cpa.r_hi != 0) {
		if (ip->i_cpa.r_lo == ip->i_cpa.r_hi)
			printf("	awgcpa %u\n", ip->i_cpa.r_lo);
		else
			printf("	awgcpa %u-%u\n", ip->i_cpa.r_lo,
			    ip->i_cpa.r_hi);
	}
	timing[0] = &ip->i_rekey_after_time;
	timing[1] = &ip->i_rekey_timeout;
	timing[2] = &ip->i_reject_after_time;
	timing[3] = &ip->i_keepalive_timeout;
	timing[4] = &ip->i_max_handshake_attempts;
	for (i = 0; i < 5; i++) {
		if (!(ip->i_flags & (AWG_INTERFACE_HAS_REKEY_AFTER_TIME << i))
		    || timing[i]->r_hi == 0)
			continue;
		if (timing[i]->r_lo == timing[i]->r_hi)
			printf("\t%s %u\n", timing_name[i], timing[i]->r_lo);
		else
			printf("\t%s %u-%u\n", timing_name[i],
			    timing[i]->r_lo, timing[i]->r_hi);
	}
	if (ip->i_flags & AWG_INTERFACE_HAS_TRAILERS && ip->i_random_trailers)
		printf("\tawgrandomtrailers\n");
	if (ip->i_flags & AWG_INTERFACE_HAS_COOKIES && ip->i_disable_cookies)
		printf("\tawgdisablecookies\n");

	pp = &ip->i_peers[0];
	for (i = 0; i < ip->i_peers_count; i++) {
		b64_ntop(pp->p_public, AWG_KEY_LEN, key, sizeof(key));
		printf("\tawgpeer %s\n", key);
		if (strlen(pp->p_description))
			printf("\tawgdescr: %s\n", pp->p_description);
		if (pp->p_flags & AWG_PEER_HAS_PSK)
			printf("\tawgpsk (present)\n");
		if (pp->p_flags & AWG_PEER_HAS_PKA && pp->p_pka) {
			if (pp->p_pka_hi > pp->p_pka)
				printf("\tawgpka %u-%u (sec)\n", pp->p_pka,
				    pp->p_pka_hi);
			else
				printf("\tawgpka %u (sec)\n", pp->p_pka);
		}
		if (pp->p_flags & AWG_PEER_HAS_ENDPOINT) {
			if (getnameinfo(&pp->p_sa, AWG_SA_LEN(pp->p_sa), hbuf,
			    sizeof(hbuf), sbuf, sizeof(sbuf),
			    NI_NUMERICHOST | NI_NUMERICSERV) == 0)
				printf("\tawgendpoint %s %s\n", hbuf, sbuf);
			else
				printf("\tawgendpoint unable to print\n");
		}
		printf("	tx: %llu, rx: %llu\n",
		    (unsigned long long)pp->p_txbytes,
		    (unsigned long long)pp->p_rxbytes);
		if (pp->p_last_handshake.tv_sec != 0) {
			clock_gettime(CLOCK_REALTIME, &now);
			printf("\tlast handshake: %lld seconds ago\n",
			    (long long)(now.tv_sec -
			    pp->p_last_handshake.tv_sec));
		}
		ap = &pp->p_aips[0];
		for (j = 0; j < pp->p_aips_count; j++) {
			inet_ntop(ap->a_af, &ap->a_addr, hbuf, sizeof(hbuf));
			printf("\tawgaip %s/%d\n", hbuf, ap->a_cidr);
			ap++;
		}
		pp = (struct awg_peer_io *)ap;
	}
	/* R4-10: the get buffer holds the private key and peer PSKs. */
	explicit_bzero(d.awgd_interface, d.awgd_size);
	free(d.awgd_interface);
	return (0);
}

/*
 * Read the interface's current state with SIOCGAWG (same size-probe loop as
 * show_interface) and seed a cfg with it. `set` names a few keys; the kernel
 * applies grouped fields as a unit (Jc/Jmin/Jmax, S1/S2, H1-H4), so the
 * siblings the command leaves out come from here instead of from a zeroed
 * struct.
 */
static int
read_current(int s, const char *name, struct cfg *c)
{
	static char ispec[AWG_ISPEC_COUNT][AWG_ISPEC_MAXLEN];
	struct awg_data_io d;
	struct awg_interface_io *ip;
	size_t i, last_size, alloc;
	char key[AWG_BASE64_KEY_LEN + 1];

	memset(&d, 0, sizeof(d));
	strlcpy(d.awgd_name, name, sizeof(d.awgd_name));
	d.awgd_size = sizeof(struct awg_interface_io);
	if ((d.awgd_interface = calloc(1, d.awgd_size)) == NULL)
		err(EX_USAGE, "calloc");
	/* R6/R4-10: the GET buffer receives the private key and PSKs, so
	 * it is registered with the atexit cleanup for the whole probe.
	 * `alloc` tracks the live allocation so the grow wipes it first. */
	alloc = d.awgd_size;
	secret_register(d.awgd_interface, d.awgd_size);
	for (i = 0; i < AWG_ISPEC_COUNT; i++) {
		d.awgd_interface->i_ispec[i] = ispec[i];
		d.awgd_interface->i_ispec_len[i] = sizeof(ispec[i]);
	}
	last_size = 0;
	for (;;) {
		int saved_errno;

		if (ioctl(s, SIOCGAWG, &d) < 0) {
			saved_errno = errno;
			/* R5/R4-09: a probe failure is a real failure — the
			 * caller gets the errno instead of a zeroed seed. */
			if (saved_errno == ENOTTY) {
				secret_free(d.awgd_interface, d.awgd_size);
				errno = saved_errno;
				return (1);
			}
			errno = saved_errno;
			err(EX_IOCTL, "%s: SIOCGAWG", d.awgd_name);
		}
		if (last_size >= d.awgd_size)
			break;
		/* R6/R4-10: wipe the key-bearing block before realloc frees
		 * it; the re-issued ioctl refills the grown buffer, so the
		 * copy realloc makes is not needed for correctness. */
		explicit_bzero(d.awgd_interface, alloc);
		d.awgd_interface = realloc(d.awgd_interface, d.awgd_size);
		if (d.awgd_interface == NULL)
			err(EX_USAGE, "realloc");
		secret_register(d.awgd_interface, d.awgd_size);
		alloc = d.awgd_size;
		for (i = 0; i < AWG_ISPEC_COUNT; i++) {
			d.awgd_interface->i_ispec[i] = ispec[i];
			d.awgd_interface->i_ispec_len[i] = sizeof(ispec[i]);
		}
		last_size = d.awgd_size;
	}
	ip = d.awgd_interface;

	if (ip->i_flags & AWG_INTERFACE_HAS_PRIVATE) {
		b64_ntop(ip->i_private, AWG_KEY_LEN, key, sizeof(key));
		c->c_private = strdup(key);
	}
	if (ip->i_flags & AWG_INTERFACE_HAS_HPK) {
		b64_ntop(ip->i_hpk, AWG_KEY_LEN, key, sizeof(key));
		c->c_hpk = strdup(key);
	}
	if (ip->i_flags & AWG_INTERFACE_HAS_PORT)
		c->c_port = ip->i_port;
	if (ip->i_flags & AWG_INTERFACE_HAS_RTABLE)
		c->c_rtable = ip->i_rtable;
	if (ip->i_flags & AWG_INTERFACE_HAS_VERSION)
		c->c_version = ip->i_version;
	if (ip->i_flags & AWG_INTERFACE_HAS_JC) {
		c->c_jc = ip->i_jc;
		c->c_jmin = ip->i_jmin;
		c->c_jmax = ip->i_jmax;
	}
	if (ip->i_flags & AWG_INTERFACE_HAS_S12) {
		c->c_s1 = ip->i_s1;
		c->c_s2 = ip->i_s2;
	}
	if (ip->i_flags & AWG_INTERFACE_HAS_S3)
		c->c_s3 = ip->i_s3;
	if (ip->i_flags & AWG_INTERFACE_HAS_S4)
		c->c_s4 = ip->i_s4;
	if (ip->i_flags & AWG_INTERFACE_HAS_H) {
		c->c_h1 = ip->i_h1;
		c->c_h2 = ip->i_h2;
		c->c_h3 = ip->i_h3;
		c->c_h4 = ip->i_h4;
	}
	if (ip->i_flags & AWG_INTERFACE_HAS_CPA)
		c->c_cpa = ip->i_cpa;
	if (ip->i_flags & AWG_INTERFACE_HAS_REKEY_AFTER_TIME)
		c->c_rekey_after = ip->i_rekey_after_time;
	if (ip->i_flags & AWG_INTERFACE_HAS_REKEY_TIMEOUT)
		c->c_rekey_timeout = ip->i_rekey_timeout;
	if (ip->i_flags & AWG_INTERFACE_HAS_REJECT_AFTER_TIME)
		c->c_reject_after = ip->i_reject_after_time;
	if (ip->i_flags & AWG_INTERFACE_HAS_KEEPALIVE_TIMEOUT)
		c->c_keepalive_timeout = ip->i_keepalive_timeout;
	if (ip->i_flags & AWG_INTERFACE_HAS_MAX_HANDSHAKE_ATTEMPTS)
		c->c_max_attempts = ip->i_max_handshake_attempts;
	if (ip->i_flags & AWG_INTERFACE_HAS_TRAILERS)
		c->c_random_trailers = ip->i_random_trailers;
	if (ip->i_flags & AWG_INTERFACE_HAS_COOKIES)
		c->c_disable_cookies = ip->i_disable_cookies;
	for (i = 0; i < AWG_ISPEC_COUNT; i++) {
		if (!(ip->i_flags & AWG_INTERFACE_HAS_I(i)))
			continue;
		if (ip->i_ispec_len[i] > sizeof(ispec[i]))
			continue;
		c->c_ispec[i] = strdup(ispec[i]);
	}
	/* R6/R4-10: the stack key holds the private key (then the HPK)
	 * after the b64_ntop copies — wipe it, not just the heap buffer. */
	explicit_bzero(key, sizeof(key));
	secret_free(d.awgd_interface, d.awgd_size);
	return (0);
}

static int
run_ifconfig_try(const char *const *argv)
{
	pid_t pid;
	int status;

	if (posix_spawnp(&pid, argv[0], NULL, NULL, (char *const *)argv,
	    environ) != 0)
		return (-1);
	while (waitpid(pid, &status, 0) == -1)
		if (errno != EINTR)
			return (-1);
	if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
		return (-1);
	return (0);
}

static void
run_ifconfig(const char *const *argv)
{
	if (run_ifconfig_try(argv) != 0)
		erx(EX_USAGE, "%s failed", argv[0]);
}

/*
 * Same, but tolerates EEXIST: setting an address the interface already
 * has must stay idempotent, so `up` twice must not fail on SIOCAIFADDR.
 * Only EEXIST is tolerated — the child's stderr is captured so any other
 * failure (including a signal) is reported with the child's own message.
 * R8/R4-08: a failure restores the snapshot (and frees it) before the
 * command exits with the child's status, so no path mutates and exits
 * without a restore attempt.
 */
static int
run_ifconfig_eexist(int s, struct if_state *prev, const char *const *argv)
{
	pid_t pid;
	int status;
	int pfd[2];
	posix_spawn_file_actions_t fa;
	char msg[256] = "";

	if (pipe(pfd) != 0)
		err(EX_USAGE, "pipe");
	posix_spawn_file_actions_init(&fa);
	if (posix_spawn_file_actions_adddup2(&fa, pfd[1], 2) != 0)
		err(EX_USAGE, "%s", argv[0]);
	if (posix_spawnp(&pid, argv[0], &fa, NULL, (char *const *)argv,
	    environ) != 0)
		err(EX_USAGE, "%s", argv[0]);
	posix_spawn_file_actions_destroy(&fa);
	close(pfd[1]);
	{
		ssize_t n = 0, r;
		char *p = msg;

		while (n < (ssize_t)sizeof(msg) - 1 &&
		    (r = read(pfd[0], p + n, sizeof(msg) - 1 - n)) > 0)
			n += r;
		while (n > 0 && (p[n - 1] == '\n' || p[n - 1] == '\r'))
			p[--n] = '\0';
	}
	close(pfd[0]);
	while (waitpid(pid, &status, 0) == -1)
		if (errno != EINTR)
			err(EX_USAGE, "waitpid");
	/* R5/R4-09: the child's termination comes first — a process killed
	 * by a signal that printed "File exists" is a failure, not an
	 * idempotent success. */
	if (WIFSIGNALED(status)) {
		restore_state(s, prev);
		state_free(prev);
		erx(EX_USAGE, "%s failed: killed by %s%s%s", argv[0],
		    strsignal(WTERMSIG(status)), *msg ? ": " : "", msg);
	}
	if (WIFEXITED(status) && WEXITSTATUS(status) == 0)
		return (0);
	if (strstr(msg, "File exists") != NULL)
		return (0);
	restore_state(s, prev);
	state_free(prev);
	erx(EX_USAGE, "%s failed: exit %d%s%s", argv[0],
	    WEXITSTATUS(status), *msg ? ": " : "", msg);
}

/*
 * Prefix length of a network-order netmask (4 IPv4 or 16 IPv6 bytes).
 * R6/N7 + R7/N13: a netmask is a contiguous run of ones, so every bit
 * after the first zero must be zero.  The first version counted the
 * leading ones and stopped (0x000000ff counted 8 and matched a requested
 * /8); the second version stopped the byte loop at the first short byte
 * and re-checked the rest with a shift of (8 - bits % 8), which shifts by
 * 8 — a whole byte — whenever the run ends on a byte boundary, so
 * 0xff00ff00 still counted 8 and 0xf0f00000 counted 4.  This walk visits
 * all 8*len bits, so the prefix is computed from the whole mask, and any
 * one after a zero makes the mask not a prefix: -1 ("cannot verify").
 */
static int
mask_bits(const uint8_t *mask, size_t len)
{
	int bits = 0;
	size_t i;
	int seen_zero = 0;

	for (i = 0; i < len; i++) {
		uint8_t m = mask[i];
		int b;

		for (b = 7; b >= 0; b--) {
			if (m & (1u << b)) {
				if (seen_zero)
					return (-1);
				bits++;
			} else
				seen_zero = 1;
		}
	}
	return (bits);
}

/*
 * Parse the presentation form of an address: family (by the ':'), prefix
 * length (0..32 for IPv4, 0..128 for IPv6, -1 when absent) and the address
 * bytes.  R7/R4-08: shared by the pre-mutation validation and the read-back
 * so both use the same grammar; a bad prefix or address refuses the config.
 */
static void
parse_address(const char *addr, uint8_t *bytes, size_t *len, int *family,
    int *bits)
{
	char *want, *slash, *pct;
	const char *errmsg = NULL;

	if ((want = strdup(addr)) == NULL)
		err(EX_USAGE, "strdup");
	*family = strchr(want, ':') != NULL ? AF_INET6 : AF_INET;
	*len = *family == AF_INET ? 4 : 16;
	*bits = -1;
	if ((slash = strchr(want, '/')) != NULL) {
		*slash++ = '\0';
		*bits = (int)strtonum(slash, 0,
		    *family == AF_INET ? 32 : 128, &errmsg);
		if (errmsg != NULL) {
			free(want);
			errx(EX_CONFIG, "%s: bad prefix length", addr);
		}
	}
	/* An IPv6 scope (fe80::1%ixn) is not part of the address bytes. */
	if ((pct = strchr(want, '%')) != NULL)
		*pct = '\0';
	if (inet_pton(*family, want, bytes) != 1) {
		free(want);
		errx(EX_CONFIG, "%s: not a valid %s address", addr,
		    *family == AF_INET ? "IPv4" : "IPv6");
	}
	free(want);
}

/*
 * R4-09/N3: confirm the address is on the target interface by reading the
 * interface back (getifaddrs). The kernel's EEXIST is generic (any
 * in6_ifaddr collision answers "File exists"), so the text says nothing
 * about *this* address on *this* interface. The comparison is on the parsed
 * address bytes, the address family and the prefix length — never on the
 * presentation text, so fd00:0:0:0:0:0:0:1/128 and FD00::1/128 match the
 * fd00::1/128 the kernel stores.
 */
static int
address_present(int s, const char *ifname, const char *addr)
{
	struct ifaddrs *ifa0, *ifa;
	uint8_t wantbytes[16], havebytes[16], maskbytes[16];
	size_t wantlen;
	int wantfamily, wantbits = -1, found = 0;

	(void)s;
	parse_address(addr, wantbytes, &wantlen, &wantfamily, &wantbits);

	if (getifaddrs(&ifa0) == -1) {
		err(EX_USAGE, "getifaddrs");
		return (0);
	}
	for (ifa = ifa0; ifa != NULL && !found; ifa = ifa->ifa_next) {
		/* R5: ifa_name is not guaranteed by every getifaddrs(3)
		 * implementation, so guard the dereference before strcmp. */
		if (ifa->ifa_addr == NULL || ifa->ifa_name == NULL ||
		    strcmp(ifa->ifa_name, ifname) != 0)
			continue;
		if (ifa->ifa_addr->sa_family != wantfamily)
			continue;
		if (wantfamily == AF_INET)
			memcpy(havebytes,
			    &((const struct sockaddr_in *)ifa->ifa_addr)->sin_addr,
			    wantlen);
		else
			memcpy(havebytes,
			    &((const struct sockaddr_in6 *)ifa->ifa_addr)->sin6_addr,
			    wantlen);
		if (memcmp(havebytes, wantbytes, wantlen) != 0)
			continue;
		if (wantbits < 0) {	/* no prefix given: address is enough */
			found = 1;
			break;
		}
		/*
		 * R6/N7: with an explicit prefix the mask is the proof of
		 * that prefix.  An entry without a netmask says nothing
		 * about it, so it is not a match — skip it and keep
		 * looking instead of accepting the address on faith.
		 */
		if (ifa->ifa_netmask == NULL)
			continue;
		if (wantfamily == AF_INET)
			memcpy(maskbytes,
			    &((const struct sockaddr_in *)ifa->ifa_netmask)->sin_addr,
			    wantlen);
		else
			memcpy(maskbytes,
			    &((const struct sockaddr_in6 *)ifa->ifa_netmask)->sin6_addr,
			    wantlen);
		if (mask_bits(maskbytes, wantlen) == wantbits)
			found = 1;
	}
	freeifaddrs(ifa0);
	return (found);
}

/*
 * ifconfig inet|inet6 <addr> alias, then verify the address landed on the
 * named interface. A duplicate address (EEXIST) is idempotent only when the
 * address is already on this interface — the readback is what proves it.
 * R7/R4-08: a read-back failure is an operational failure, so the snapshot
 * is restored (MTU and the addresses already installed are put back) before
 * the command exits.
 */
static void
install_address(int s, const char *ifname, const char *addr,
    struct if_state *prev)
{
	const char *av[6];

	av[0] = (char *)IFCONFIG_PATH;
	av[1] = (char *)ifname;
	av[2] = strchr(addr, ':') != NULL ? "inet6" : "inet";
	av[3] = (char *)addr;
	av[4] = (char *)"alias";
	av[5] = NULL;
	run_ifconfig_eexist(s, prev, av);
	if (!address_present(s, ifname, addr)) {
		restore_state(s, prev);
		state_free(prev);
		errx(EX_USAGE, "%s: %s not installed on %s", IFCONFIG_PATH,
		    addr, ifname);
	}
}

/*
 * R7/R4-08: capture the state a `up` is about to overwrite — existence
 * (SIOCGAWG), the MTU (AF_LINK ifa_data) and the address list with its
 * prefixes (AF_INET/AF_INET6 entries of this interface, rendered from the
 * parsed bytes so a restore re-installs what the kernel had).
 */
static void
state_snapshot(int s, const char *ifname, struct if_state *st)
{
	struct awg_data_io probe;
	struct awg_interface_io base;
	struct ifaddrs *ifa0, *ifa;
	struct addr_ent *tail = NULL;

	(void)s;
	memset(st, 0, sizeof(*st));
	st->is_ifname = ifname;
	memset(&probe, 0, sizeof(probe));
	memset(&base, 0, sizeof(base));
	strlcpy(probe.awgd_name, ifname, sizeof(probe.awgd_name));
	/* awg_ioctl_get copies the base struct before consulting
	 * awgd_size, so the probe needs a real buffer. */
	probe.awgd_size = sizeof(base);
	probe.awgd_interface = &base;
	if (ioctl(s, SIOCGAWG, &probe) == -1) {
		if (errno == ENXIO || errno == ENODEV)
			return;		/* absent: nothing to restore */
		err(EX_IOCTL, "%s: SIOCGAWG", probe.awgd_name);
	}
	st->is_exists = 1;

	if (getifaddrs(&ifa0) == -1)
		err(EX_USAGE, "getifaddrs");
	for (ifa = ifa0; ifa != NULL; ifa = ifa->ifa_next) {
		uint8_t bytes[16], maskbytes[16];
		size_t len = 0;
		char text[INET6_ADDRSTRLEN + 8];

		if (ifa->ifa_name == NULL || ifa->ifa_addr == NULL ||
		    strcmp(ifa->ifa_name, ifname) != 0)
			continue;
		switch (ifa->ifa_addr->sa_family) {
		case AF_LINK: {
			char mtu[32];
			unsigned int m;

			/* R9/R4-08: the up/down flag lives on the AF_LINK
			 * entry; the restore puts it back with the MTU. */
			st->is_up = (ifa->ifa_flags & IFF_UP) != 0;
			if (ifa->ifa_data == NULL)
				break;
			m = ((const struct if_data *)ifa->ifa_data)->ifi_mtu;
			snprintf(mtu, sizeof(mtu), "%u", m);
			free(st->is_mtu);
			if ((st->is_mtu = strdup(mtu)) == NULL)
				err(EX_USAGE, "strdup");
			break;
		}
		case AF_INET:
			len = 4;
			memcpy(bytes,
			    &((const struct sockaddr_in *)ifa->ifa_addr)->sin_addr,
			    len);
			break;
		case AF_INET6:
			len = 16;
			memcpy(bytes,
			    &((const struct sockaddr_in6 *)ifa->ifa_addr)->sin6_addr,
			    len);
			break;
		default:
			continue;
		}
		if (len == 0)
			continue;
		if (inet_ntop(ifa->ifa_addr->sa_family, bytes, text,
		    sizeof(text)) == NULL)
			continue;
		/* The prefix travels with the mask; a non-contiguous mask
		 * (N13) says nothing about it, so the address is restored
		 * without one. */
		if (ifa->ifa_netmask != NULL) {
			int bits;

			memcpy(maskbytes,
			    ifa->ifa_addr->sa_family == AF_INET ?
			    (const void *)&((const struct sockaddr_in *)
				ifa->ifa_netmask)->sin_addr :
			    (const void *)&((const struct sockaddr_in6 *)
				ifa->ifa_netmask)->sin6_addr,
			    len);
			bits = mask_bits(maskbytes, len);
			if (bits >= 0) {
				size_t n = strlen(text);

				if (n + 8 < sizeof(text))
					snprintf(text + n,
					    sizeof(text) - n, "/%d", bits);
			}
		}
		{
			struct addr_ent *ae = calloc(1, sizeof(*ae));

			if (ae == NULL)
				err(EX_USAGE, "calloc");
			if ((ae->ae_addr = strdup(text)) == NULL)
				err(EX_USAGE, "strdup");
			if (tail == NULL)
				st->is_addrs = ae;
			else
				tail->ae_next = ae;
			tail = ae;
		}
	}
	freeifaddrs(ifa0);
}

/*
 * R7/R4-08: put the interface back the way the snapshot found it.  An
 * interface this run created is destroyed (its MTU and addresses go with
 * it); one that already existed gets its MTU and every address re-installed
 * in the original order.  R8: every step is attempted — a failing MTU no
 * longer skips the addresses — and a failed step is reported with the
 * state it leaves behind before the caller exits with the original error.
 * R9/R4-08: the address list is dropped first (-inet, -inet6) and the
 * snapshot re-installed behind it, primary first and the rest as aliases.
 * Re-installing the snapshot alone left every address this run added — a
 * /32 alias, an IPv6 secondary — on the interface, so the pre-state was
 * not byte-identical.  The up/down flag travels with the snapshot too.
 */
static void
restore_state(int s, struct if_state *st)
{
	const char *av[6];
	const char *clear4[] = { IFCONFIG_PATH, NULL, "-inet", NULL };
	const char *clear6[] = { IFCONFIG_PATH, NULL, "-inet6", NULL };
	struct addr_ent *ae;
	const char *first4 = NULL, *first6 = NULL;
	int failed = 0;

	(void)s;
	if (st == NULL)
		return;
	if (st->is_created) {
		const char *destroy[] = { IFCONFIG_PATH, st->is_ifname,
		    "destroy", NULL };

		if (run_ifconfig_try(destroy) != 0)
			warnx("restore: destroy of %s failed; MTU %s and "
			    "address %s left behind", st->is_ifname,
			    st->is_mtu != NULL ? st->is_mtu : "-",
			    st->is_addrs != NULL ? st->is_addrs->ae_addr : "none");
		return;
	}
	if (st->is_mtu != NULL) {
		av[0] = (char *)IFCONFIG_PATH;
		av[1] = (char *)st->is_ifname;
		av[2] = (char *)"mtu";
		av[3] = (char *)st->is_mtu;
		av[4] = NULL;
		if (run_ifconfig_try(av) != 0) {
			failed = 1;
			warnx("restore: mtu %s on %s failed", st->is_mtu,
			    st->is_ifname);
		}
	}
	clear4[1] = st->is_ifname;
	clear6[1] = st->is_ifname;
	if (run_ifconfig_try(clear4) != 0) {
		failed = 1;
		warnx("restore: -inet on %s failed", st->is_ifname);
	}
	if (run_ifconfig_try(clear6) != 0) {
		failed = 1;
		warnx("restore: -inet6 on %s failed", st->is_ifname);
	}
	for (ae = st->is_addrs; ae != NULL; ae = ae->ae_next) {
		int is6 = strchr(ae->ae_addr, ':') != NULL;

		/* The first address of a family is the primary, the rest
		 * are aliases; installing every entry as an alias leaves
		 * the previous primary in front of them. */
		if ((is6 && first6 == NULL) || (!is6 && first4 == NULL)) {
			if (is6)
				first6 = ae->ae_addr;
			else
				first4 = ae->ae_addr;
		}
		av[0] = (char *)IFCONFIG_PATH;
		av[1] = (char *)st->is_ifname;
		av[2] = is6 ? "inet6" : "inet";
		av[3] = (char *)ae->ae_addr;
		av[4] = (is6 && ae->ae_addr == first6) ||
		    (!is6 && ae->ae_addr == first4) ? NULL : (char *)"alias";
		av[5] = NULL;
		if (run_ifconfig_try(av) != 0) {
			failed = 1;
			warnx("restore: address %s on %s failed",
			    ae->ae_addr, st->is_ifname);
		}
	}
	av[0] = (char *)IFCONFIG_PATH;
	av[1] = (char *)st->is_ifname;
	/* R10/R4-08a: the native command is `down`; `-up` is not an OpenBSD
	 * ifconfig operation (it answers "bad value"), so a snapshot taken
	 * while the interface was down could not be restored by one. */
	av[2] = (char *)(st->is_up ? "up" : "down");
	av[3] = NULL;
	if (run_ifconfig_try(av) != 0) {
		failed = 1;
		warnx("restore: %s of %s failed",
		    st->is_up ? "up" : "down", st->is_ifname);
	}
	if (failed)
		warnx("restore of %s incomplete: MTU %s, first address %s "
		    "left behind", st->is_ifname,
		    st->is_mtu != NULL ? st->is_mtu : "-",
		    st->is_addrs != NULL ? st->is_addrs->ae_addr : "none");
}

static void
state_free(struct if_state *st)
{
	struct addr_ent *ae;

	while (st->is_addrs != NULL) {
		ae = st->is_addrs;
		st->is_addrs = ae->ae_next;
		free(ae->ae_addr);
		free(ae);
	}
	free(st->is_mtu);
	st->is_mtu = NULL;
}

/*
 * R8/R4-08: the single mutation path of cmd_up.  Every ifconfig mutation
 * runs through here, so any operational failure first restores the
 * snapshot (MTU, addresses in order, created-vs-existing) and only then
 * exits with the original error and exit status.
 */
static void
mutate(int s, struct if_state *prev, const char *const *av)
{
	if (run_ifconfig_try(av) != 0) {
		int e = errno;

		restore_state(s, prev);
		state_free(prev);
		errno = e;
		erx(EX_USAGE, "%s failed", av[0]);
	}
}

static void
cmd_up(int s, const struct request *r)
{
	const char *ifname = r->r_ifname;
	const char *path = r->r_path;
	struct cfg c;
	struct awg_data_io d;
	struct if_state prev;
	const char *av[6];
	int i;

	memset(&c, 0, sizeof(c));
	g_active_cfg = &c;			/* R4-10: wiped on any exit */
	c.c_replace_peers = 1;	/* the file is the full peer list */

	parse_config(r->r_fd, path, &c);
	validate_config(&c);

	/* R7/R4-08: the addresses are parsed (family, prefix, bytes) before
	 * the first mutation, so an invalid second address does not leave a
	 * half-configured interface behind. */
	for (i = 0; i < c.c_n_addrs; i++) {
		uint8_t bytes[16];
		size_t len;
		int family, bits;

		parse_address(c.c_addrs[i], bytes, &len, &family, &bits);
	}

	/* R7/R4-08: parse, resolve and serialise every field — base64 keys,
	 * PersistentKeepalive ranges, the peer endpoint (getaddrinfo) and
	 * the allowed-IPs (inet_net_pton) — before the first mutation, so a
	 * bad endpoint or allowed-IP leaves the interface untouched. */
	memset(&d, 0, sizeof(d));
	strlcpy(d.awgd_name, ifname, sizeof(d.awgd_name));
	serialise_config(&d, &c);

	/* R7/R4-08: capture the pre-up state (existence, MTU, addresses) so
	 * an operational failure restores it.  The SIOCGAWG probe is the
	 * existence test: only an absence (ENXIO/ENODEV) justifies the
	 * create, any other probe error is propagated before the mutation
	 * (R6/R4-09). */
	state_snapshot(s, ifname, &prev);
	if (!prev.is_exists) {
		const char *create[] = { IFCONFIG_PATH, ifname,
		    "create", NULL };
		run_ifconfig(create);
		prev.is_created = 1;
	}

	/* R4-08: order the mutations cheapest-failure first — MTU and the
	 * addresses are validated before the peer list is replaced, so a
	 * rejected MTU leaves the peers (and their count) untouched. */
	if (c.c_has_mtu) {
		av[0] = (char *)IFCONFIG_PATH;
		av[1] = (char *)ifname;
		av[2] = (char *)"mtu";
		av[3] = c.c_mtu;
		av[4] = NULL;
		mutate(s, &prev, av);
	}
	for (i = 0; i < c.c_n_addrs; i++)
		install_address(s, ifname, c.c_addrs[i], &prev);

	/* R7/R4-08: the single mutation.  On an operational failure the
	 * state captured above is restored — a fresh interface is destroyed,
	 * a pre-existing one gets its MTU and addresses back. */
	if (ioctl(s, SIOCSAWG, &d) == -1) {
		int e = errno;

		restore_state(s, &prev);
		secret_free(d.awgd_interface, d.awgd_size);
		state_free(&prev);
		erx(e, "%s: SIOCSAWG", d.awgd_name);
	}
	/* R4-10: the serialised buffer carries the private key and PSKs. */
	secret_free(d.awgd_interface, d.awgd_size);

	{
		av[0] = (char *)IFCONFIG_PATH;
		av[1] = (char *)ifname;
		av[2] = (char *)"up";
		av[3] = NULL;
		mutate(s, &prev, av);
	}
	state_free(&prev);
	cfg_free(&c);
}

static void
cmd_down(int s, int argc, char **argv)
{
	const char *av[4];

	/* R5/R4-09: exact arity — surplus arguments are an error. */
	if (argc != 1)
		usage();
	/* Remove every peer, then bring down and destroy. */
	{
		struct awg_data_io d;

		memset(&d, 0, sizeof(d));
		strlcpy(d.awgd_name, argv[0], sizeof(d.awgd_name));
		d.awgd_size = sizeof(struct awg_interface_io);
		if ((d.awgd_interface = calloc(1, d.awgd_size)) == NULL)
			err(EX_USAGE, "calloc");
		d.awgd_interface->i_flags = AWG_INTERFACE_REPLACE_PEERS;
		if (ioctl(s, SIOCSAWG, &d) == -1)
			err(EX_IOCTL, "%s: SIOCSAWG", d.awgd_name);
		free(d.awgd_interface);
	}
	av[0] = (char *)IFCONFIG_PATH;
	av[1] = argv[0];
	av[2] = (char *)"destroy";
	av[3] = NULL;
	run_ifconfig(av);
}

static int
cmd_show(int s, int argc, char **argv)
{
	char *names = NULL;
	size_t ncount = 0;
	int rc = 0;

	/* R5/R4-09: show takes the interface or nothing — surplus
	 * arguments are an error, not something to ignore. */
	if (argc > 1)
		usage();
	if (argc == 1) {
		/* R4-09: an explicitly named target reports its ioctl status. */
		return show_interface(s, argv[0]);
	}
	/* No name: every awg interface, from the routing socket. */
	{
		struct ifaddrs *ifa0, *ifa;

		if (getifaddrs(&ifa0) == -1)
			err(EX_USAGE, "getifaddrs");
		for (ifa = ifa0; ifa != NULL; ifa = ifa->ifa_next) {
			size_t i;

			if (ifa->ifa_addr == NULL ||
			    ifa->ifa_addr->sa_family != AF_LINK)
				continue;
			if (ifa->ifa_name == NULL)	/* R5: guard */
				continue;
			if (strncmp(ifa->ifa_name, "awg", 3) != 0)
				continue;
			for (i = 0; i < ncount; i++)
				if (strcmp(names + i * IFNAMSIZ,
				    ifa->ifa_name) == 0)
					break;
			if (i < ncount)
				continue;
			names = realloc(names, (ncount + 1) * IFNAMSIZ);
			if (names == NULL)
				err(EX_USAGE, "realloc");
			strlcpy(names + ncount * IFNAMSIZ, ifa->ifa_name,
			    IFNAMSIZ);
			ncount++;
		}
		freeifaddrs(ifa0);
	}
	for (size_t i = 0; i < ncount; i++) {
		if (i > 0)
			printf("\n");
		if (show_interface(s, names + i * IFNAMSIZ) != 0)
			rc = 1;
	}
	free(names);
	return (rc);
}

/* set name KEY VALUE ... — one SIOCSAWG with the given interface keys. */
static void
cmd_set(int s, int argc, char **argv)
{
	struct cfg c;
	int i;

	memset(&c, 0, sizeof(c));
	g_active_cfg = &c;			/* R4-10: wiped on any exit */
	if (argc < 1)
		usage();
	/* R4-09: every key needs a value — a trailing token is an error, not
	 * something to ignore while the preceding fields apply. */
	if ((argc - 1) % 2 != 0)
		errx(EX_USAGE, "set %s: expected KEY VALUE pairs, one "
		    "argument left over", argv[0]);
	/* Seed with the current state so a partial set keeps the siblings
	 * of every grouped field (Jc/Jmin/Jmax, S1/S2, H1-H4). R5/R4-09:
	 * a probe failure (ENOTTY on a non-AWG target) is reported with the
	 * kernel's errno instead of seeding zeros. */
	if (read_current(s, argv[0], &c) != 0)
		errx(EX_IOCTL, "set %s: SIOCGAWG: %s", argv[0],
		    strerror(errno));
	for (i = 1; i + 1 < argc; i += 2) {
		const char *key = argv[i];
		const char *val = argv[i + 1];

		if (strcmp(key, "PrivateKey") == 0) {
			char *secret = read_secret(val);

			load_key(secret, "PrivateKey");
			free_secret(&c.c_private);
			c.c_private = secret;
			c.c_update_private = 1;
		} else if (strcmp(key, "ListenPort") == 0) {
			const char *errmsg = NULL;
			c.c_port = strtonum(val, 0, 65535, &errmsg);
			if (errmsg)
				errx(EX_CONFIG, "ListenPort: %s, %s", val,
				    errmsg);
			c.c_has_port = 1;
		} else if (strcmp(key, "Table") == 0) {
			const char *errmsg = NULL;
			c.c_rtable = strtonum(val, 0, RT_TABLEID_MAX, &errmsg);
			if (errmsg)
				errx(EX_CONFIG, "Table: %s, %s", val, errmsg);
			c.c_has_rtable = 1;
		} else if (strcmp(key, "Version") == 0) {
			if (strcmp(val, "legacy") == 0)
				c.c_version = AWG_VERSION_LEGACY;
			else if (strcmp(val, "3.1") == 0)
				c.c_version = AWG_VERSION_3_1;
			else
				errx(EX_CONFIG,
				    "Version: %s, must be legacy or 3.1", val);
			c.c_has_version = 1;
		} else if (strcmp(key, "Jc") == 0) {
			const char *errmsg = NULL;
			c.c_jc = strtonum(val, 0, AWG_MAX_JUNK, &errmsg);
			if (errmsg)
				errx(EX_CONFIG, "Jc: %s, %s", val, errmsg);
			c.c_has_jc = 1;
		} else if (strcmp(key, "Jmin") == 0) {
			const char *errmsg = NULL;
			c.c_jmin = strtonum(val, 0, AWG_MAX_JUNK_SIZE,
			    &errmsg);
			if (errmsg)
				errx(EX_CONFIG, "Jmin: %s, %s", val, errmsg);
			c.c_has_jc = 1;
		} else if (strcmp(key, "Jmax") == 0) {
			const char *errmsg = NULL;
			c.c_jmax = strtonum(val, 0, AWG_MAX_JUNK_SIZE,
			    &errmsg);
			if (errmsg)
				errx(EX_CONFIG, "Jmax: %s, %s", val, errmsg);
			c.c_has_jc = 1;
		} else if (strcmp(key, "S1") == 0) {
			const char *errmsg = NULL;
			c.c_s1 = strtonum(val, 0, AWG_MAX_PADDING, &errmsg);
			if (errmsg)
				errx(EX_CONFIG, "S1: %s, %s", val, errmsg);
			c.c_has_s12 = 1;
		} else if (strcmp(key, "S2") == 0) {
			const char *errmsg = NULL;
			c.c_s2 = strtonum(val, 0, AWG_MAX_PADDING, &errmsg);
			if (errmsg)
				errx(EX_CONFIG, "S2: %s, %s", val, errmsg);
			c.c_has_s12 = 1;
		} else if (strcmp(key, "S3") == 0) {
			const char *errmsg = NULL;
			c.c_s3 = strtonum(val, 0, AWG_MAX_PADDING, &errmsg);
			if (errmsg)
				errx(EX_CONFIG, "S3: %s, %s", val, errmsg);
			c.c_has_s3 = 1;
		} else if (strcmp(key, "S4") == 0) {
			const char *errmsg = NULL;
			c.c_s4 = strtonum(val, 0, AWG_MAX_PADDING, &errmsg);
			if (errmsg)
				errx(EX_CONFIG, "S4: %s, %s", val, errmsg);
			c.c_has_s4 = 1;
		} else if (strcmp(key, "H1") == 0) {
			parse_range(val, AWG_H_MAX, &c.c_h1, "H1");
			c.c_has_h = 1;
		} else if (strcmp(key, "H2") == 0) {
			parse_range(val, AWG_H_MAX, &c.c_h2, "H2");
			c.c_has_h = 1;
		} else if (strcmp(key, "H3") == 0) {
			parse_range(val, AWG_H_MAX, &c.c_h3, "H3");
			c.c_has_h = 1;
		} else if (strcmp(key, "H4") == 0) {
			parse_range(val, AWG_H_MAX, &c.c_h4, "H4");
			c.c_has_h = 1;
		} else if (strcmp(key, "I1") == 0) {
			free(c.c_ispec[0]);
			c.c_ispec[0] = strdup(val);
			c.c_has_i[0] = 1;
		} else if (strcmp(key, "I2") == 0) {
			free(c.c_ispec[1]);
			c.c_ispec[1] = strdup(val);
			c.c_has_i[1] = 1;
		} else if (strcmp(key, "I3") == 0) {
			free(c.c_ispec[2]);
			c.c_ispec[2] = strdup(val);
			c.c_has_i[2] = 1;
		} else if (strcmp(key, "I4") == 0) {
			free(c.c_ispec[3]);
			c.c_ispec[3] = strdup(val);
			c.c_has_i[3] = 1;
		} else if (strcmp(key, "I5") == 0) {
			free(c.c_ispec[4]);
			c.c_ispec[4] = strdup(val);
			c.c_has_i[4] = 1;
		} else if (strcmp(key, "HeaderProtectionKey") == 0) {
			char *secret = read_secret(val);

			load_key(secret, "HeaderProtectionKey");
			free_secret(&c.c_hpk);
			c.c_hpk = secret;
			c.c_has_hpk = 1;
			c.c_update_hpk = 1;
		} else if (strcmp(key, "ContentPaddingAddition") == 0) {
			parse_range(val, AWG_RANGE_MAX, &c.c_cpa,
			    "ContentPaddingAddition");
			c.c_has_cpa = 1;
		} else if (strcmp(key, "RekeyAfterTime") == 0) {
			parse_range(val, AWG_RANGE_MAX, &c.c_rekey_after,
			    "RekeyAfterTime");
			c.c_has_rekey_after = 1;
		} else if (strcmp(key, "RekeyTimeout") == 0) {
			parse_range(val, AWG_RANGE_MAX, &c.c_rekey_timeout,
			    "RekeyTimeout");
			c.c_has_rekey_timeout = 1;
		} else if (strcmp(key, "RejectAfterTime") == 0) {
			parse_range(val, AWG_RANGE_MAX, &c.c_reject_after,
			    "RejectAfterTime");
			c.c_has_reject_after = 1;
		} else if (strcmp(key, "KeepaliveTimeout") == 0) {
			parse_range(val, AWG_RANGE_MAX,
			    &c.c_keepalive_timeout, "KeepaliveTimeout");
			c.c_has_keepalive_timeout = 1;
		} else if (strcmp(key, "MaxHandshakeAttempts") == 0) {
			parse_range(val, AWG_RANGE_MAX, &c.c_max_attempts,
			    "MaxHandshakeAttempts");
			c.c_has_max_attempts = 1;
		} else if (strcmp(key, "RandomTrailers") == 0) {
			c.c_random_trailers = parse_bool(val,
			    "RandomTrailers");
			c.c_has_trailers = 1;
		} else if (strcmp(key, "DisableCookies") == 0) {
			c.c_disable_cookies = parse_bool(val,
			    "DisableCookies");
			c.c_has_cookies = 1;
		} else
			errx(EX_CONFIG, "unknown key `%s'", key);
	}
	apply_config(s, argv[0], &c, NULL);
	cfg_free(&c);
}

/* add name peer pubkey [endpoint ADDR:PORT] [allowed-ips LIST]
 *    [psk KEY] [keepalive N] */
static void
cmd_add(int s, int argc, char **argv)
{
	struct cfg c;
	struct peer_cfg *p;
	int i;

	memset(&c, 0, sizeof(c));
	g_active_cfg = &c;			/* R4-10: wiped on any exit */
	if (argc < 3)
		usage();
	if (strcmp(argv[1], "peer") != 0)
		usage();
	p = peer_new(&c);
	p->pc_pubkey = strdup(argv[2]);
	load_key(p->pc_pubkey, "peer");
	for (i = 3; i < argc; ) {
		if (strcmp(argv[i], "endpoint") == 0 && i + 1 < argc) {
			free(p->pc_endpoint);
			p->pc_endpoint = strdup(argv[i + 1]);
			i += 2;
		} else if (strcmp(argv[i], "allowed-ips") == 0 &&
		    i + 1 < argc) {
			peer_add_aip(p, argv[i + 1]);
			i += 2;
		} else if (strcmp(argv[i], "psk") == 0 && i + 1 < argc) {
			char *secret = read_secret(argv[i + 1]);

			load_key(secret, "psk");
			free_secret(&p->pc_psk);
			p->pc_psk = secret;
			i += 2;
		} else if (strcmp(argv[i], "keepalive") == 0 &&
		    i + 1 < argc) {
			free(p->pc_pka);
			p->pc_pka = strdup(argv[i + 1]);
			i += 2;
		} else
			usage();
	}
	apply_config(s, argv[0], &c, NULL);
	cfg_free(&c);
}

/* del name peer pubkey */
static void
cmd_del(int s, int argc, char **argv)
{
	struct cfg c;
	struct peer_cfg *p;

	memset(&c, 0, sizeof(c));
	g_active_cfg = &c;			/* R4-10: wiped on any exit */
	/* R5/R4-09: exact arity — name, "peer" and the key, nothing more. */
	if (argc != 3)
		usage();
	if (strcmp(argv[1], "peer") != 0)
		usage();
	p = peer_new(&c);
	p->pc_pubkey = strdup(argv[2]);
	p->pc_remove = 1;
	load_key(p->pc_pubkey, "peer");
	apply_config(s, argv[0], &c, NULL);
	cfg_free(&c);
}

/*
 * The `up` arguments are parsed here, in main, so the config trust
 * walk runs before unveil(2) pins the filesystem: the whole chain from "/"
 * (etc, amnezia, amneziawg) is validated, not only CONF_DIR.
 */
static void
parse_request(int argc, char **argv, struct request *r)
{
	memset(r, 0, sizeof(*r));
	if (strcmp(argv[1], "up") != 0)
		return;
	if (argc < 3)
		usage();
	r->r_ifname = argv[2];
	if (argc == 3) {
		char *def;

		if (asprintf(&def, CONF_DIR "/%s.conf", r->r_ifname) == -1)
			err(EX_USAGE, "asprintf");
		r->r_path = def;
	} else if (argc == 5 && strcmp(argv[3], "--config") == 0) {
		r->r_path = argv[4];
	} else
		usage();
	r->r_fd = open_trusted(r->r_path);
}

int
main(int argc, char **argv)
{
	int s;
	int rc = EX_OK;
	struct request req;

	if (argc < 2)
		usage();

	/* R4-10: one cleanup path — err()/errx() exit through here too. */
	if (atexit(cleanup_active_cfg) != 0)
		err(EX_USAGE, "atexit");

	parse_request(argc, argv, &req);

	if (unveil(CONF_DIR, "r") == -1)
		err(EX_USAGE, "unveil(%s)", CONF_DIR);
	if (unveil(IFCONFIG_PATH, "x") == -1)
		err(EX_USAGE, "unveil(%s)", IFCONFIG_PATH);
	if (unveil(NULL, NULL) == -1)
		err(EX_USAGE, "unveil");
	/*
	 * rpath: config file. inet: SIOCSAWG/SIOCGAWG on the AF_INET
	 * socket; the kernel charges SIOCGAWG against "tty" (sys_ioctl(2)
	 * falls through to PLEDGE_TTY for requests it does not name), so
	 * both are required. route: getifaddrs opens a routing socket.
	 * dns: endpoint resolution. proc exec: ifconfig create/destroy/up.
	 * wpath/cpath are not needed — awgctl never writes.
	 */
	if (pledge("stdio rpath inet route tty dns proc exec", NULL) == -1)
		err(EX_USAGE, "pledge");

	s = socket(AF_INET, SOCK_DGRAM, 0);
	if (s == -1)
		err(EX_USAGE, "socket");

	if (strcmp(argv[1], "up") == 0)
		cmd_up(s, &req);
	else if (strcmp(argv[1], "down") == 0)
		cmd_down(s, argc - 2, argv + 2);
	else if (strcmp(argv[1], "show") == 0)
		rc = cmd_show(s, argc - 2, argv + 2);
	else if (strcmp(argv[1], "set") == 0)
		cmd_set(s, argc - 2, argv + 2);
	else if (strcmp(argv[1], "add") == 0)
		cmd_add(s, argc - 2, argv + 2);
	else if (strcmp(argv[1], "del") == 0)
		cmd_del(s, argc - 2, argv + 2);
	else
		usage();

	close(s);
	return rc;
}
