/*	$OpenBSD: fixture-probe.c,v 1.0 2026-11-09 00:00:00 Exp $ */

/*
 * R13 read-only observer for the host fixture (no awgctl involved).
 *
 * Links with tools/lab/fixture-stubs.c and prints the address list that
 * getifaddrs(3) serves — one "fam:addr/prefix" token per line, AF_LINK
 * skipped, the prefix taken from the served netmask. A control uses it to
 * compare the C observer's list against the Python writer's `addrs` key and
 * against the canonical render of the same state file, which is how the
 * duplicate and untagged-token rules are proven on all three models.
 */

#include <sys/types.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef __OpenBSD__
#define	IFDESCRSIZE	32	/* net/if.h on non-OpenBSD hosts */
#define	AF_LINK		17	/* link-layer family on Linux hosts */
struct if_data {
	unsigned int	ifi_mtu;
};
#endif
#include <net/if.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <ifaddrs.h>

/* Leading run of ones in the netmask, i.e. the prefix length it proves. */
static int
mask_bits(const struct sockaddr *mask, size_t len)
{
	const unsigned char *b;
	size_t i;
	int bits = 0;

	if (mask == NULL)
		return (-1);
	if (mask->sa_family == AF_INET)
		b = (const unsigned char *)
		    &((const struct sockaddr_in *)mask)->sin_addr;
	else
		b = (const unsigned char *)
		    &((const struct sockaddr_in6 *)mask)->sin6_addr;
	for (i = 0; i < len * 8; i++) {
		if ((b[i / 8] & (1 << (7 - (i % 8)))) == 0)
			break;
		bits++;
	}
	return (bits);
}

int
main(void)
{
	struct ifaddrs *ifa0, *ifa;
	char buf[INET6_ADDRSTRLEN];

	if (getifaddrs(&ifa0) != 0) {
		perror("getifaddrs");
		return (1);
	}
	for (ifa = ifa0; ifa != NULL; ifa = ifa->ifa_next) {
		int fam = (ifa->ifa_addr != NULL) ? ifa->ifa_addr->sa_family : 0;
		int bits;

		if (fam != AF_INET && fam != AF_INET6)
			continue;
		if (inet_ntop(fam,
			(fam == AF_INET) ? (const void *)
			    &((const struct sockaddr_in *)ifa->ifa_addr)->sin_addr
			    : (const void *)
			    &((const struct sockaddr_in6 *)ifa->ifa_addr)->sin6_addr,
			buf, sizeof(buf)) == NULL)
			continue;
		bits = mask_bits(ifa->ifa_netmask, (fam == AF_INET) ? 4 : 16);
		if (bits >= 0)
			printf("%d:%s/%d\n", fam, buf, bits);
		else
			printf("%d:%s\n", fam, buf);
	}
	freeifaddrs(ifa0);
	return (0);
}
