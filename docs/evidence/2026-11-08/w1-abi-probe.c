/*
 * abi-probe.c — W1 ABI mismatch probe for the AmneziaWG OpenBSD driver.
 *
 * Issues SIOCSAWG (223) and SIOCGAWG (224) — the ioctls the patched
 * ifconfig(8) uses (see src/if_awg.h) — against a live awg interface with
 *   (a) a truncated buffer (awgd_size smaller than struct awg_interface_io),
 *   (b) a zeroed structure (awgd_size = 0, NULL interface pointer),
 *   (c) garbage lengths (SIZE_MAX, 1, 4095, huge peer counts).
 * Requires EINVAL/ENOTTY-class returns and no kernel panic.
 *
 * Build on the VM: cc -Wall -Wextra -I/usr/include abi-probe.c -o abi-probe
 */
#include <sys/ioctl.h>
#include <net/if.h>
#include <net/if_awg.h>
#include <err.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int
probe(int s, const char *label, unsigned long req, struct awg_data_io *d,
    struct awg_interface_io *io)
{
	int rc;

	(void)io;
	rc = ioctl(s, req, d);
	printf("%-46s req=%s rc=%d errno=%d (%s)\n", label,
	    req == SIOCSAWG ? "SIOCSAWG" : "SIOCGAWG", rc, errno,
	    strerror(errno));
	return rc;
}

int
main(int argc, char *argv[])
{
	const char *ifname = (argc > 1) ? argv[1] : "awg9";
	struct awg_data_io d;
	struct awg_interface_io *io;
	int s;

	if ((s = socket(AF_INET, SOCK_DGRAM, 0)) < 0)
		err(1, "socket");

	/* (b) fully zeroed structure: size 0, NULL interface pointer */
	memset(&d, 0, sizeof(d));
	strlcpy(d.awgd_name, ifname, sizeof(d.awgd_name));
	probe(s, "b1 zeroed struct, size=0, NULL iface", SIOCGAWG, &d, NULL);

	/* (a) truncated buffer: valid name, size < sizeof(interface_io) */
	io = calloc(1, sizeof(*io));
	if (io == NULL)
		err(1, "calloc");
	memset(&d, 0, sizeof(d));
	strlcpy(d.awgd_name, ifname, sizeof(d.awgd_name));
	d.awgd_size = 8;
	d.awgd_interface = io;
	probe(s, "a1 truncated, size=8", SIOCGAWG, &d, io);
	printf("   -> kernel-reported size=%zu\n", d.awgd_size);

	memset(&d, 0, sizeof(d));
	strlcpy(d.awgd_name, ifname, sizeof(d.awgd_name));
	d.awgd_size = sizeof(struct awg_interface_io) - 1;
	d.awgd_interface = io;
	probe(s, "a2 truncated, size=sizeof(io)-1", SIOCGAWG, &d, io);
	printf("   -> kernel-reported size=%zu\n", d.awgd_size);

	memset(&d, 0, sizeof(d));
	strlcpy(d.awgd_name, ifname, sizeof(d.awgd_name));
	d.awgd_size = 1;
	d.awgd_interface = io;
	probe(s, "a3 truncated, size=1", SIOCGAWG, &d, io);

	/* full-size get (baseline: the size ifconfig uses) */
	memset(&d, 0, sizeof(d));
	strlcpy(d.awgd_name, ifname, sizeof(d.awgd_name));
	d.awgd_size = sizeof(struct awg_interface_io);
	d.awgd_interface = io;
	probe(s, "a4 full struct, size=sizeof(io)", SIOCGAWG, &d, io);
	printf("   -> kernel-reported size=%zu\n", d.awgd_size);

	/* (c) garbage lengths */
	memset(&d, 0, sizeof(d));
	strlcpy(d.awgd_name, ifname, sizeof(d.awgd_name));
	d.awgd_size = (size_t)-1;
	d.awgd_interface = io;
	probe(s, "c1 garbage size=SIZE_MAX", SIOCGAWG, &d, io);

	memset(&d, 0, sizeof(d));
	strlcpy(d.awgd_name, ifname, sizeof(d.awgd_name));
	d.awgd_size = 4095;
	d.awgd_interface = io;
	probe(s, "c2 garbage size=4095", SIOCGAWG, &d, io);

	memset(&d, 0, sizeof(d));
	strlcpy(d.awgd_name, ifname, sizeof(d.awgd_name));
	d.awgd_size = sizeof(struct awg_interface_io);
	d.awgd_interface = io;
	io->i_flags = 0xffffffffu;	/* all feature bits, incl. 3.1+legacy */
	io->i_peers_count = (size_t)-1;	/* absurd peer count */
	probe(s, "c3 set: flags=0xffffffff peers=SIZE_MAX", SIOCSAWG, &d, io);

	memset(&d, 0, sizeof(d));
	strlcpy(d.awgd_name, ifname, sizeof(d.awgd_name));
	d.awgd_size = 3;
	d.awgd_interface = io;
	io->i_flags = 0xffffffffu;
	probe(s, "c4 set: size=3 flags=0xffffffff", SIOCSAWG, &d, io);

	/* garbage on a non-existent interface (clone lookup path) */
	memset(&d, 0, sizeof(d));
	strlcpy(d.awgd_name, "awg12", sizeof(d.awgd_name));
	d.awgd_size = (size_t)-1;
	d.awgd_interface = io;
	probe(s, "c5 garbage size on absent awg12", SIOCGAWG, &d, io);

	close(s);
	free(io);
	return 0;
}
