/*
 * Send N initiation-shaped datagrams at an awg(4) interface and report the
 * rate. Used to check the cookie-less admission cap (AWG-007) on a
 * disposable lab guest: the first byte is the legacy H1 value, so the driver
 * classifies each datagram as a handshake initiation before any MAC check.
 *
 *   cc -O2 -o hsflood hsflood.c
 *   ./hsflood 127.0.0.1 51820 20000 148 [delay_us] [type_offset] [type_byte]
 */
#include <arpa/inet.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/time.h>
#include <unistd.h>

int
main(int argc, char **argv)
{
	struct sockaddr_in dst;
	struct timeval t0, t1;
	unsigned char buf[1500];
	long n, i, delay_us, blocked = 0, acc = 0, off = 0;
	int type_byte = 0x01;
	size_t len;
	int s;
	double secs;

	if (argc < 4) {
		fprintf(stderr, "usage: %s host port count [len] [delay_us]\n", argv[0]);
		return 1;
	}
	n = atol(argv[3]);
	len = (argc > 4) ? (size_t)atol(argv[4]) : 148;
	if (len < 8 || len > sizeof(buf))
		len = 148;
	delay_us = (argc > 5) ? atol(argv[5]) : 0;
	if (argc > 6)
		off = atol(argv[6]);
	if (argc > 7)
		type_byte = (int)strtol(argv[7], NULL, 0);
	if (off < 0 || (size_t)off >= len)
		off = 0;

	memset(&dst, 0, sizeof(dst));
	dst.sin_family = AF_INET;
	dst.sin_port = htons((unsigned short)atoi(argv[2]));
	if (inet_pton(AF_INET, argv[1], &dst.sin_addr) != 1) {
		fprintf(stderr, "bad address\n");
		return 1;
	}
	s = socket(AF_INET, SOCK_DGRAM, 0);
	if (s < 0) {
		perror("socket");
		return 1;
	}
	/* Non-blocking: a full receive buffer on loopback must count as a lost
	 * datagram, not stall the sender. */
	if (fcntl(s, F_SETFL, O_NONBLOCK) < 0) {
		perror("fcntl");
		return 1;
	}

	memset(buf, 0x42, sizeof(buf));
	/* The type is a little-endian uint32 right after the S<i> prefix. */
	buf[off] = (unsigned char)(type_byte & 0xff);
	buf[off + 1] = (unsigned char)((type_byte >> 8) & 0xff);
	buf[off + 2] = 0x00;
	buf[off + 3] = 0x00;

	gettimeofday(&t0, NULL);
	for (i = 0; i < n; i++) {
		buf[4] = (unsigned char)(i & 0xff);
		/* usleep() rounds up to a tick, so pace by accumulating: a
		 * 500 us budget sleeps 10 ms once every 20 packets. */
		if (delay_us > 0) {
			acc += delay_us;
			if (acc >= 10000) {
				usleep((useconds_t)acc);
				acc = 0;
			}
		}
		if (sendto(s, buf, len, 0, (struct sockaddr *)&dst,
		    sizeof(dst)) < 0) {
			if (errno == EWOULDBLOCK || errno == ENOBUFS ||
			    errno == EAGAIN)
				blocked++;
			else {
				perror("sendto");
				break;
			}
		}
	}
	gettimeofday(&t1, NULL);
	close(s);

	secs = (t1.tv_sec - t0.tv_sec) + (t1.tv_usec - t0.tv_usec) / 1e6;
	printf("offered=%ld len=%zu secs=%.3f pps=%.0f busy_drops=%ld\n", i,
	    len, secs, secs > 0 ? i / secs : 0, blocked);
	return 0;
}
