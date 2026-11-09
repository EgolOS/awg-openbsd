/*	$OpenBSD: fixture-stubs.c,v 1.0 2026/11/08 00:00:00 Exp $ */

/*
 * Host fixture layer for awgctl (R5).
 *
 * Links with sbin/awgctl/awgctl.c in place of libc's ioctl(2)/getifaddrs(3)
 * so the production source can be driven on a machine with no awg(4) kernel.
 * The model follows src/if_awg.c: SIOCGAWG copies struct awg_interface_io
 * out of the kernel's state, echoes the caller's I1-I5 receive pointers
 * (awg_ioctl_get copies ubuf[i], lines 3676-3684) and reports the live peer
 * counters and last-handshake (lines 3606-3612); SIOCSAWG stores the fields
 * named by i_flags. State lives in $AWGFIX_STATE (default
 * /tmp/awgctl-fixture.state) so consecutive awgctl invocations see the same
 * interface, exactly like a kernel would.
 *
 * Fixture knobs (environment):
 *   AWGFIX_STATE    state file (key=value)
 *   AWGFIX_TRAFFIC  1 = peer counters and last-handshake advance on every GET
 *   AWGFIX_LOG      append one line per ioctl/ifconfig interaction
 */

#include <sys/types.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#ifndef __OpenBSD__
#define	IFDESCRSIZE	32	/* net/if.h on non-OpenBSD hosts */
#define	AF_LINK		17	/* link-layer family on Linux hosts */
struct if_data {
	unsigned int	ifi_mtu;
};
#endif
#include <net/if.h>
#include <net/if_awg.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <ifaddrs.h>
#include <errno.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

struct fix_state {
	unsigned int	 flags;
	int		 port;
	int		 version;
	int		 peers;
	unsigned long long tx;
	unsigned long long rx;
	long long	 handshake;
	char		 ispec[AWG_ISPEC_COUNT][AWG_ISPEC_MAXLEN];
	char		 addr[INET6_ADDRSTRLEN];
	int		 prefix;
	int		 family;
	unsigned int	 mtu;
	char		aliases[512];	/* legacy "fam:addr/prefix" list */
	int		 has_aliases;	/* the aliases key is present */
	char		addrs[512];	/* canonical "fam:addr/prefix" list, R9/R4-08 */
	int		 has_addrs;	/* the addrs key is present */
	int		 up;		/* AF_LINK IFF_UP, R9/R4-08 */
};

static void
fix_log(const char *fmt, ...)
{
	const char *path = getenv("AWGFIX_LOG");
	FILE *f;
	va_list ap;

	if (path == NULL)
		return;
	if ((f = fopen(path, "a")) == NULL)
		return;
	va_start(ap, fmt);
	vfprintf(f, fmt, ap);
	va_end(ap);
	fputc('\n', f);
	fclose(f);
}

static void
state_read(struct fix_state *st)
{
	char path[PATH_MAX];
	FILE *f;
	char line[AWG_ISPEC_MAXLEN + 64];

	snprintf(path, sizeof(path), "%s",
	    getenv("AWGFIX_STATE") ? getenv("AWGFIX_STATE") :
	    "/tmp/awgctl-fixture.state");
	memset(st, 0, sizeof(*st));
	st->prefix = 128;
	st->family = AF_INET6;
	st->mtu = 1420;
	st->up = 1;
	if ((f = fopen(path, "r")) == NULL) {
		/* Defaults: a 3.1 interface with one peer. */
		st->flags = AWG_INTERFACE_HAS_PRIVATE | AWG_INTERFACE_HAS_PORT |
		    AWG_INTERFACE_HAS_VERSION | AWG_INTERFACE_HAS_JC |
		    AWG_INTERFACE_HAS_S12 | AWG_INTERFACE_HAS_S3 |
		    AWG_INTERFACE_HAS_S4 | AWG_INTERFACE_HAS_H |
		    AWG_INTERFACE_HAS_HPK | AWG_INTERFACE_HAS_CPA |
		    AWG_INTERFACE_HAS_REKEY_AFTER_TIME |
		    AWG_INTERFACE_HAS_REKEY_TIMEOUT |
		    AWG_INTERFACE_HAS_REJECT_AFTER_TIME |
		    AWG_INTERFACE_HAS_KEEPALIVE_TIMEOUT |
		    AWG_INTERFACE_HAS_MAX_HANDSHAKE_ATTEMPTS |
		    AWG_INTERFACE_HAS_TRAILERS | AWG_INTERFACE_HAS_COOKIES |
		    AWG_INTERFACE_HAS_I1;
		st->port = 51820;
		st->version = AWG_VERSION_3_1;
		st->peers = 1;
		st->prefix = 128;
		st->family = AF_INET6;
		snprintf(st->addr, sizeof(st->addr), "fd00::1");
		snprintf(st->ispec[0], sizeof(st->ispec[0]),
		    "<b 0xc700000001><r 16><t>");
		return;
	}
	while (fgets(line, sizeof(line), f) != NULL) {
		char *eq = strchr(line, '=');
		char *nl;
		char *key, *val;

		if (eq == NULL)
			continue;
		*eq = '\0';
		key = line;
		val = eq + 1;
		if ((nl = strpbrk(val, "\r\n")) != NULL)
			*nl = '\0';
		if (strcmp(key, "flags") == 0)
			st->flags = (unsigned int)strtoul(val, NULL, 0);
		else if (strcmp(key, "port") == 0)
			st->port = atoi(val);
		else if (strcmp(key, "version") == 0)
			st->version = atoi(val);
		else if (strcmp(key, "peers") == 0)
			st->peers = atoi(val);
		else if (strcmp(key, "tx") == 0)
			st->tx = strtoull(val, NULL, 0);
		else if (strcmp(key, "rx") == 0)
			st->rx = strtoull(val, NULL, 0);
		else if (strcmp(key, "handshake") == 0)
			st->handshake = strtoll(val, NULL, 0);
		else if (strcmp(key, "addr") == 0)
			snprintf(st->addr, sizeof(st->addr), "%s", val);
		else if (strcmp(key, "prefix") == 0)
			st->prefix = atoi(val);
		else if (strcmp(key, "family") == 0)
			st->family = atoi(val);
		else if (strcmp(key, "mtu") == 0)
			st->mtu = (unsigned int)strtoul(val, NULL, 0);
		else if (strcmp(key, "addrs") == 0) {
			snprintf(st->addrs, sizeof(st->addrs), "%s", val);
			st->has_addrs = 1;
		}
		else if (strcmp(key, "aliases") == 0) {
			snprintf(st->aliases, sizeof(st->aliases), "%s", val);
			st->has_aliases = 1;
		}
		else if (strcmp(key, "up") == 0)
			st->up = atoi(val);
		else if (strncmp(key, "ispec", 5) == 0) {
			int idx = atoi(key + 5) - 1;
			if (idx >= 0 && idx < AWG_ISPEC_COUNT)
				snprintf(st->ispec[idx],
				    sizeof(st->ispec[idx]), "%s", val);
		}
	}
	fclose(f);
}

static void
state_write(const struct fix_state *st)
{
	char path[PATH_MAX], tmp[PATH_MAX + 8];
	FILE *f;

	snprintf(path, sizeof(path), "%s",
	    getenv("AWGFIX_STATE") ? getenv("AWGFIX_STATE") :
	    "/tmp/awgctl-fixture.state");
	snprintf(tmp, sizeof(tmp), "%s.tmp", path);
	if ((f = fopen(tmp, "w")) == NULL)
		return;
	fprintf(f, "flags=%u\nport=%d\nversion=%d\npeers=%d\n",
	    st->flags, st->port, st->version, st->peers);
	fprintf(f, "tx=%llu\nrx=%llu\nhandshake=%lld\n",
	    st->tx, st->rx, st->handshake);
	fprintf(f, "addr=%s\nprefix=%d\nfamily=%d\nmtu=%u\n",
	    st->addr, st->prefix, st->family, st->mtu);
	/* R9: the address list and the up flag are kernel state too — a
	 * SIOCSAWG that stores the AWG config must not drop them.
	 * R10: `addrs` is the canonical list (fixture-ifconfig.py writes
	 * it); the legacy `aliases` member is only the fallback, so a
	 * successful SET keeps the modelled list instead of a stale copy. */
	if (st->has_addrs)
		fprintf(f, "addrs=%s\n", st->addrs);
	else if (st->has_aliases)
		fprintf(f, "addrs=%s\n", st->aliases);
	fprintf(f, "up=%d\n", st->up);
	for (int i = 0; i < AWG_ISPEC_COUNT; i++)
		if (st->ispec[i][0] != '\0')
			fprintf(f, "ispec%d=%s\n", i + 1, st->ispec[i]);
	fclose(f);
	rename(tmp, path);
}

/*
 * ioctl(2): SIOCSAWG stores, SIOCGAWG returns the stored interface. The
 * caller's I1-I5 receive buffers are echoed back exactly like the driver
 * does (iface_o.i_ispec[i] = ubuf[i]) so a caller that hashes the whole
 * serialised payload sees the buffer addresses, not only the configuration.
 */
int
ioctl(int fd, unsigned long request, ...)
{
	struct fix_state st;
	void *arg;
	va_list ap;

	(void)fd;
	va_start(ap, request);
	arg = va_arg(ap, void *);
	va_end(ap);
	if (arg == NULL)
		return (-1);
	if (getenv("AWGFIX_ENOTTY") != NULL && request == SIOCGAWG) {
		errno = ENOTTY;
		fix_log("GET enotty");
		return (-1);
	}
	/* R6/R4-09: the first SIOCGAWG (the cmd_up create probe) answers
	 * EPERM, so the non-absence probe error must be propagated. */
	if (getenv("AWGFIX_PROBE_ERR") != NULL && request == SIOCGAWG) {
		static int probed = 0;
		if (!probed) {
			probed = 1;
			errno = EPERM;
			fix_log("GET probe eperm");
			return (-1);
		}
	}
	/* R7/R4-08: the first SIOCGAWG answers ENXIO (interface absent), so
	 * the create runs and the restore path destroys what it created. */
	if (getenv("AWGFIX_CREATE") != NULL && request == SIOCGAWG) {
		static int created = 0;
		if (!created) {
			created = 1;
			errno = ENXIO;
			fix_log("GET probe enxio");
			return (-1);
		}
	}

	state_read(&st);
	if (getenv("AWGFIX_TRAFFIC") != NULL &&
	    strcmp(getenv("AWGFIX_TRAFFIC"), "0") != 0) {
		st.tx += 1420;
		st.rx += 1040;
		st.handshake += 1;
		state_write(&st);
		state_read(&st);
	}

	if (request == SIOCSAWG) {
		struct awg_data_io *d = arg;
		struct awg_interface_io *ip = d->awgd_interface;

		/* R7/R4-08: an operational failure of the config mutation,
		 * so the restore path runs after the create/MTU/addresses. */
		if (getenv("AWGFIX_SET_ERR") != NULL) {
			errno = EIO;
			fix_log("SET eio");
			return (-1);
		}
		if (ip->i_flags & AWG_INTERFACE_HAS_PORT)
			st.port = ip->i_port;
		if (ip->i_flags & AWG_INTERFACE_HAS_VERSION)
			st.version = ip->i_version;
		if (ip->i_flags & AWG_INTERFACE_HAS_PRIVATE)
			st.flags |= AWG_INTERFACE_HAS_PRIVATE;
		else
			st.flags &= ~AWG_INTERFACE_HAS_PRIVATE;
		if (ip->i_flags & AWG_INTERFACE_HAS_HPK)
			st.flags |= AWG_INTERFACE_HAS_HPK;
		else
			st.flags &= ~AWG_INTERFACE_HAS_HPK;
		for (int i = 0; i < AWG_ISPEC_COUNT; i++)
			if ((ip->i_flags & AWG_INTERFACE_HAS_I(i)) &&
			    ip->i_ispec[i] != NULL)
				snprintf(st.ispec[i], sizeof(st.ispec[i]),
				    "%s", ip->i_ispec[i]);
		st.peers = (int)ip->i_peers_count;
		state_write(&st);
		fix_log("SET flags=%u port=%u version=%u peers=%zu",
		    ip->i_flags, ip->i_port, ip->i_version,
		    ip->i_peers_count);
		return (0);
	}

	if (request == SIOCGAWG) {
		struct awg_data_io *d = arg;
		struct awg_interface_io *ip = d->awgd_interface;
		char *ubuf[AWG_ISPEC_COUNT];
		size_t ulen[AWG_ISPEC_COUNT];
		size_t size = sizeof(struct awg_interface_io);

		/* The driver reads the caller's I1-I5 receive buffers with
		 * copyin before it builds the reply (if_awg.c:3676-3684), so
		 * capture them before the destination is cleared. */
		for (int i = 0; i < AWG_ISPEC_COUNT; i++) {
			ubuf[i] = ip->i_ispec[i];
			ulen[i] = ip->i_ispec_len[i];
		}
		if (d->awgd_size < size) {
			d->awgd_size = size;
			return (0);
		}
		/* Kernel-side copy of the interface, then the peer array. */
		memset(ip, 0, size);
		ip->i_flags = st.flags;
		ip->i_port = st.port;
		ip->i_version = st.version;
		memset(ip->i_private, 0x11, AWG_KEY_LEN);
		memset(ip->i_hpk, 0x22, AWG_HPK_LEN);
		ip->i_peers_count = st.peers;
		size += (size_t)st.peers * sizeof(struct awg_peer_io);
		if (d->awgd_size < size) {
			d->awgd_size = size;
			return (0);
		}
		if (st.peers > 0) {
			struct awg_peer_io *pp =
			    (struct awg_peer_io *)&ip->i_peers[0];
			memset(pp, 0, sizeof(*pp));
			pp->p_flags = AWG_PEER_HAS_PUBLIC;
			pp->p_txbytes = st.tx;
			pp->p_rxbytes = st.rx;
			pp->p_last_handshake.tv_sec =
			    (time_t)(1700000000 + st.handshake);
			pp->p_aips_count = 0;
		}
		/* Echo the caller's ispec pointers and fill their buffers,
		 * as awg_ioctl_get does with ubuf/ulen. */
		for (int i = 0; i < AWG_ISPEC_COUNT; i++) {
			ip->i_ispec[i] = ubuf[i];
			ip->i_ispec_len[i] = strlen(st.ispec[i]) + 1;
			if (ubuf[i] != NULL &&
			    ulen[i] >= strlen(st.ispec[i]) + 1)
				strcpy(ubuf[i], st.ispec[i]);
		}
		fix_log("GET flags=%u port=%u version=%u peers=%d "
		    "tx=%llu rx=%llu hs=%lld ispec0=%p", ip->i_flags,
		    ip->i_port, ip->i_version, st.peers, st.tx, st.rx,
		    st.handshake, (void *)ip->i_ispec[0]);
		return (0);
	}
	return (0);
}

/*
 * getifaddrs(3): one AF_LINK entry for the interface, then the primary
 * address and every alias from the state file, each with a netmask built from
 * its own prefix length. R6/N7 knobs: AWGFIX_MASK replaces the netmask with a
 * raw value (hex, network order — 0x000000ff for IPv4, 32 hex digits for
 * IPv6) so a non-contiguous mask can be served; AWGFIX_NOMASK leaves
 * ifa_netmask NULL. R9/R4-08: the alias list travels too, so a restore that
 * re-installs the snapshot is checked against a list, not one address slot.
 */
#define FIX_MAX_ADDR	8
static struct ifaddrs fix_ifa[1 + FIX_MAX_ADDR];
static struct sockaddr_storage fix_addr[1 + FIX_MAX_ADDR];
static struct sockaddr_storage fix_mask[1 + FIX_MAX_ADDR];
static struct if_data fix_linkdata;

static void
fix_mask_set(const char *hex, int family)
{
	if (family == AF_INET) {
		struct sockaddr_in *smask =
		    (struct sockaddr_in *)&fix_mask[0];

		smask->sin_family = AF_INET;
		smask->sin_addr.s_addr =
		    htonl((uint32_t)strtoul(hex, NULL, 0));
	} else {
		struct sockaddr_in6 *smask =
		    (struct sockaddr_in6 *)&fix_mask[0];

		smask->sin6_family = AF_INET6;
		inet_pton(AF_INET6, hex, &smask->sin6_addr);
	}
}

/*
 * R13: duplicate key of an address entry — the family the observer serves
 * (2 = inet, 10 = inet6, the same rule as the token decode below) plus the
 * address bytes without the %scope suffix.  The prefix is deliberately not
 * part of the key: the kernel's EEXIST ignores it, and the Python model
 * deduplicates on the same pair, so the observer, the writer and the oracle
 * all keep the first entry of a (family, address) pair.
 */
static void
fix_key(char *dst, size_t n, int family, const char *addr)
{
	char base[INET6_ADDRSTRLEN];
	char *pct;

	snprintf(base, sizeof(base), "%s", addr);
	if ((pct = strchr(base, '%')) != NULL)
		*pct = '\0';
	snprintf(dst, n, "%d|%s", family == AF_INET ? 2 : 10, base);
}

static void
fix_addr_set(struct sockaddr_storage *ss, struct sockaddr_storage *mask,
    int family, const char *addr, int prefix)
{
	/* R10: the %scope suffix (fe80::1%ixn) is kept in the stored text
	 * form the interface reports, but it is not part of the address
	 * bytes — only the part before it goes to inet_pton(3). */
	char base[INET6_ADDRSTRLEN];
	char *pct;

	snprintf(base, sizeof(base), "%s", addr);
	if ((pct = strchr(base, '%')) != NULL)
		*pct = '\0';
	if (family == AF_INET) {
		struct sockaddr_in *sin = (struct sockaddr_in *)ss;
		struct sockaddr_in *smask = (struct sockaddr_in *)mask;

		sin->sin_family = AF_INET;
		inet_pton(AF_INET, base, &sin->sin_addr);
		smask->sin_family = AF_INET;
		smask->sin_addr.s_addr = prefix <= 0 ? 0 :
		    htonl(~(0xffffffffu >> (prefix > 32 ? 32 : prefix)));
	} else {
		struct sockaddr_in6 *sin6 = (struct sockaddr_in6 *)ss;
		struct sockaddr_in6 *smask = (struct sockaddr_in6 *)mask;
		int bits = prefix;

		sin6->sin6_family = AF_INET6;
		inet_pton(AF_INET6, base, &sin6->sin6_addr);
		smask->sin6_family = AF_INET6;
		for (int i = 0; i < 16 && bits > 0; i++) {
			int n = bits >= 8 ? 8 : bits;
			smask->sin6_addr.s6_addr[i] =
			    (uint8_t)(0xff << (8 - n));
			bits -= n;
		}
	}
}

int
getifaddrs(struct ifaddrs **ifap)
{
	struct fix_state st;
	const char *maskenv;
	int n = 0;		/* AF_LINK entry, then the addresses */

	state_read(&st);
	memset(fix_ifa, 0, sizeof(fix_ifa));
	memset(&fix_linkdata, 0, sizeof(fix_linkdata));

	fix_ifa[n].ifa_name = (char *)"awg9";
	if (getenv("AWGFIX_NONAME") != NULL)
		fix_ifa[n].ifa_name = NULL;	/* R5: exercise the guard */
	fix_linkdata.ifi_mtu = st.mtu;
	fix_ifa[n].ifa_addr = (struct sockaddr *)&fix_addr[n];
	fix_addr[n].ss_family = AF_LINK;
	fix_ifa[n].ifa_flags = st.up ? IFF_UP : 0;	/* R9 */
	fix_ifa[n].ifa_data = &fix_linkdata;
	n++;

	/* Primary address, then each alias ("fam:addr/prefix" tokens).
	 * R9: the whole list travels, so a restore that re-installs the
	 * snapshot is checked against the list, not one address slot.
	 * A state file with only addr/prefix/family (the older cases)
	 * falls back to that single entry. */
	{
		char buf[sizeof(st.aliases)], *tok, *save;
		char seen[1 + FIX_MAX_ADDR][INET6_ADDRSTRLEN + 8];
		int nseen = 0;

		/* R9/R4-08: `addrs` is the canonical list and both fixture
		 * models must read the same one — fixture-ifconfig.py already
		 * preferred it, so a state written by an ifconfig call used to
		 * arrive here as "primary + stale aliases" and a snapshot saw a
		 * different interface than the one being configured. When the
		 * key is absent, fall back to the legacy addr/prefix + aliases
		 * pair, serving the primary even if aliases exist (dropping it
		 * made a restore delete the real primary). */
		snprintf(buf, sizeof(buf), "%s",
		    st.has_addrs ? st.addrs : st.aliases);
		if (!st.has_addrs && st.addr[0] != '\0' &&
		    n < 1 + FIX_MAX_ADDR) {
			fix_addr_set(&fix_addr[n], &fix_mask[n], st.family,
			    st.addr, st.prefix);
			fix_ifa[n].ifa_addr = (struct sockaddr *)&fix_addr[n];
			fix_ifa[n].ifa_name = (char *)"awg9";
			fix_key(seen[nseen], sizeof(seen[0]), st.family,
			    st.addr);
			nseen++;
			n++;
		}
		for (tok = strtok_r(buf, " 	", &save); tok != NULL;
		    tok = strtok_r(NULL, " 	", &save)) {
			char fam[8], addr[INET6_ADDRSTRLEN];
			char key[INET6_ADDRSTRLEN + 8];
			int prefix = -1;
			char *colon = strchr(tok, ':');
			char *slash;
			int dup = 0;

			if (colon == NULL || n >= 1 + FIX_MAX_ADDR)
				continue;
			*colon = '\0';
			slash = strchr(colon + 1, '/');
			if (slash != NULL) {
				*slash = '\0';
				prefix = atoi(slash + 1);
			}
			snprintf(fam, sizeof(fam), "%s", tok);
			snprintf(addr, sizeof(addr), "%s", colon + 1);
			/* R13: one duplicate rule for every model — keep the
			 * first entry of a (family, address) pair, ignore the
			 * prefix of a repeat.  Comparing the alias against the
			 * primary's "addr/prefix" text made this observer emit
			 * a duplicate the canonical text did not show. */
			fix_key(key, sizeof(key), fam[0] == '2' ? AF_INET : AF_INET6,
			    addr);
			for (int k = 0; k < nseen; k++)
				if (strcmp(seen[k], key) == 0) {
					dup = 1;
					break;
				}
			if (dup)
				continue;
			snprintf(seen[nseen], sizeof(seen[0]), "%s", key);
			nseen++;
			fix_addr_set(&fix_addr[n], &fix_mask[n],
			    fam[0] == '2' ? AF_INET : AF_INET6, addr,
			    prefix);
			fix_ifa[n].ifa_addr = (struct sockaddr *)&fix_addr[n];
			fix_ifa[n].ifa_name = (char *)"awg9";
			n++;
		}
	}
	for (int i = 0; i < n; i++) {
		fix_ifa[i].ifa_next = (i + 1 < n) ? &fix_ifa[i + 1] : NULL;
		if (i > 0 && (maskenv = getenv("AWGFIX_MASK")) != NULL) {
			fix_mask_set(maskenv, fix_addr[i].ss_family);
			fix_ifa[i].ifa_netmask =
			    (struct sockaddr *)&fix_mask[0];
		} else if (i > 0 && getenv("AWGFIX_NOMASK") != NULL) {
			fix_ifa[i].ifa_netmask = NULL;	/* R6/N7 */
		} else if (i > 0) {
			fix_ifa[i].ifa_netmask =
			    (struct sockaddr *)&fix_mask[i];
		}
	}
	*ifap = &fix_ifa[0];
	return (0);
}

void
freeifaddrs(struct ifaddrs *ifap)
{
	(void)ifap;
}
