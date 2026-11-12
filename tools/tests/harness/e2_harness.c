/* E2 wire-format conformance harness.
 *
 * Runs without a kernel.  Every line of AWG logic it executes is copied
 * verbatim out of src/if_awg.c and src/awg_noise.c at build time by
 * extract.py: the packet structs and constants, awg_send_junk(),
 * awg_noise_counter_recv() and awg_noise_kdf().  The expected values live
 * in tools/tests/test_e2_vectors.py with a cited origin; this program only
 * prints what the production code computed.
 *
 * Output is one "key=value" line per vector on stdout.  Counter cases are
 * read from stdin, one per line ("reset" or a decimal nonce).
 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "blake2s.h"
#include "stubs.h"

/* ---- verbatim production text, generated at build time ---- */
#include "gen/defines.inc"
#include "gen/structs.inc"
#include "gen/awg_noise_kdf.inc"
#include "gen/awg_noise_counter_recv.inc"
#include "gen/awg_send_junk.inc"

/* ------------------------------------------------------------------ */
/* deterministic kernel stand-ins                                      */
/* ------------------------------------------------------------------ */

static uint32_t rng_state = 0x12345678u;

uint32_t
arc4random(void)
{
	rng_state ^= rng_state << 13;
	rng_state ^= rng_state >> 17;
	rng_state ^= rng_state << 5;
	return rng_state;
}

void
arc4random_buf(void *buf, size_t len)
{
	uint8_t *p = buf;
	size_t i;

	for (i = 0; i < len; i++)
		p[i] = (uint8_t)(arc4random() >> 24);
}

uint32_t
arc4random_uniform(uint32_t upper)
{
	if (upper == 0)
		return 0;
	return arc4random() % upper;
}

/* ------------------------------------------------------------------ */
/* recorders for the production send path                             */
/* ------------------------------------------------------------------ */

#define MAX_SENT 64
static int g_sent;
static size_t g_len[MAX_SENT];
static int g_null[MAX_SENT];
static int g_nonzero[MAX_SENT];

void
awg_peer_get_endpoint(struct awg_peer *peer, struct awg_endpoint *e)
{
	memset(e, 0, sizeof(*e));
}

void
awg_send_buf(struct awg_softc *sc, struct awg_endpoint *e, uint8_t *buf,
    size_t len)
{
	size_t i, probe = len < 16 ? len : 16;
	int nz = 0;

	if (g_sent < MAX_SENT) {
		g_null[g_sent] = (buf == NULL);
		g_len[g_sent] = len;
		for (i = 0; buf != NULL && i < probe; i++)
			if (buf[i] != 0)
				nz = 1;
		g_nonzero[g_sent] = nz;
	}
	g_sent++;
}

static void
sent_reset(void)
{
	memset(g_len, 0, sizeof(g_len));
	memset(g_null, 0, sizeof(g_null));
	memset(g_nonzero, 0, sizeof(g_nonzero));
	g_sent = 0;
}

/* ------------------------------------------------------------------ */
/* helpers                                                            */
/* ------------------------------------------------------------------ */

static void
hex(const char *key, const uint8_t *p, size_t n)
{
	size_t i;

	printf("%s=", key);
	for (i = 0; i < n; i++)
		printf("%02x", p[i]);
	printf("\n");
}

static void
junk_case(const char *name, uint16_t jc, uint16_t jmin, uint16_t jmax)
{
	struct awg_softc sc;
	struct awg_peer peer;
	char key[64];
	int i;

	memset(&sc, 0, sizeof(sc));
	memset(&peer, 0, sizeof(peer));
	sc.sc_awg_jc = jc;
	sc.sc_awg_jmin = jmin;
	sc.sc_awg_jmax = jmax;

	sent_reset();
	awg_send_junk(&sc, &peer);

	printf("junk.%s.n=%d\n", name, g_sent);
	for (i = 0; i < g_sent && i < MAX_SENT; i++) {
		snprintf(key, sizeof(key), "junk.%s.len.%d", name, i);
		printf("%s=%zu\n", key, g_len[i]);
		snprintf(key, sizeof(key), "junk.%s.null.%d", name, i);
		printf("%s=%d\n", key, g_null[i]);
		snprintf(key, sizeof(key), "junk.%s.nz.%d", name, i);
		printf("%s=%d\n", key, g_nonzero[i]);
	}
}

static void
mac_vectors(void)
{
	struct awg_pkt_initiation pkt;
	uint8_t mac1_key[BLAKE2S_HASH_SIZE];
	static const uint8_t label_mac1[8] = {
		'm', 'a', 'c', '1', '-', '-', '-', '-'
	};
	uint8_t label_input[8 + 32];
	size_t i;
	size_t mac1_len = sizeof(pkt) - sizeof(pkt.m);

	/* deterministic packet content: byte i == i */
	for (i = 0; i < sizeof(pkt); i++)
		((uint8_t *)&pkt)[i] = (uint8_t)i;

	printf("mac.init.size=%zu\n", sizeof(pkt));
	printf("mac.init.mac1_offset=%zu\n", offsetof(struct awg_pkt_initiation, m));
	printf("mac.init.mac2_offset=%zu\n",
	    offsetof(struct awg_pkt_initiation, m) + COOKIE_MAC_SIZE);
	printf("mac.init.mac1_len=%zu\n", mac1_len);
	printf("mac.init.mac2_len=%zu\n", mac1_len + COOKIE_MAC_SIZE);

	/* wg_cookie.c key derivation: UNKEYED BLAKE2s("mac1----" || peer_pub) */
	memcpy(label_input, label_mac1, sizeof(label_mac1));
	for (i = 0; i < 32; i++)
		label_input[sizeof(label_mac1) + i] = (uint8_t)i;
	blake2s(mac1_key, BLAKE2S_HASH_SIZE, NULL, 0, label_input,
	    sizeof(label_input));
	hex("mac.mac1_key", mac1_key, sizeof(mac1_key));

	/* mac1 = KEYED BLAKE2s(mac1_key, message[0 .. mac1_offset)) */
	blake2s(pkt.m.mac1, COOKIE_MAC_SIZE, mac1_key, sizeof(mac1_key), &pkt,
	    mac1_len);
	hex("mac.mac1", pkt.m.mac1, COOKIE_MAC_SIZE);

	/*
	 * mac2 = KEYED BLAKE2s(cookie, msg[0 .. mac2_offset)), 16 B.
	 *
	 * wg_cookie.c cookie_macs_mac2() hashes the message with the mac1
	 * FIELD SUBSTITUTED: the input is msg[0..116) || mac1, i.e. the
	 * 16 bytes at offset 116 are the mac1 value computed just above,
	 * not the "byte i == i" filler.  The buffer below is built
	 * explicitly so the shape is visible: dropping the substitution
	 * and hashing the raw i==i packet[0..132) yields
	 * 1f9027d2095fc29ee4301d2a2c574f53, which is NOT the vector.
	 */
	{
		uint8_t cookie[16];
		uint8_t msg2[sizeof(pkt) - sizeof(pkt.m) + COOKIE_MAC_SIZE];

		for (i = 0; i < sizeof(cookie); i++)
			cookie[i] = (uint8_t)(0xa0 + i);
		hex("mac.cookie", cookie, sizeof(cookie));
		memcpy(msg2, &pkt, mac1_len);
		memcpy(msg2 + mac1_len, pkt.m.mac1, COOKIE_MAC_SIZE);
		blake2s(pkt.m.mac2, COOKIE_MAC_SIZE, cookie, sizeof(cookie),
		    msg2, sizeof(msg2));
		hex("mac.mac2", pkt.m.mac2, COOKIE_MAC_SIZE);
	}
	hex("mac.pkt", (const uint8_t *)&pkt, sizeof(pkt));
}

static void
kdf_vectors(void)
{
	uint8_t ck[NOISE_HASH_LEN], x[NOISE_HASH_LEN];
	uint8_t out1[NOISE_HASH_LEN], out2[NOISE_HASH_LEN];
	uint8_t out3[NOISE_HASH_LEN];
	size_t i;

	for (i = 0; i < sizeof(ck); i++) {
		ck[i] = 0x10;
		x[i] = 0x20;
	}
	memset(out1, 0, sizeof(out1));
	memset(out2, 0, sizeof(out2));
	memset(out3, 0, sizeof(out3));
	/* Production signature: (a, b, c, x, a_len, b_len, c_len, x_len, ck) */
	awg_noise_kdf(out1, out2, out3, x,
	    sizeof(out1), sizeof(out2), sizeof(out3), sizeof(x), ck);
	hex("kdf.out1", out1, sizeof(out1));
	hex("kdf.out2", out2, sizeof(out2));
	hex("kdf.out3", out3, sizeof(out3));
}

static void
counter_replay(void)
{
	struct awg_noise_counter ctr;
	char line[128];

	memset(&ctr, 0, sizeof(ctr));
	while (fgets(line, sizeof(line), stdin) != NULL) {
		char *end;
		unsigned long long v;

		if (strncmp(line, "reset", 5) == 0) {
			memset(&ctr, 0, sizeof(ctr));
			printf("counter.reset=ok\n");
			continue;
		}
		v = strtoull(line, &end, 10);
		if (end == line)
			continue;
		printf("counter.rc=%d\n", awg_noise_counter_recv(&ctr, v));
	}
}

int
main(void)
{
	size_t i;

	/* 1. message layout and discriminator values */
	printf("layout.init.size=%zu\n", sizeof(struct awg_pkt_initiation));
	printf("layout.init.t=%zu\n", offsetof(struct awg_pkt_initiation, t));
	printf("layout.init.s_idx=%zu\n", offsetof(struct awg_pkt_initiation, s_idx));
	printf("layout.init.ue=%zu\n", offsetof(struct awg_pkt_initiation, ue));
	printf("layout.init.es=%zu\n", offsetof(struct awg_pkt_initiation, es));
	printf("layout.init.ets=%zu\n", offsetof(struct awg_pkt_initiation, ets));
	printf("layout.init.mac1=%zu\n", offsetof(struct awg_pkt_initiation, m));
	printf("layout.init.mac2=%zu\n",
	    offsetof(struct awg_pkt_initiation, m) + COOKIE_MAC_SIZE);
	printf("layout.init.ue_len=%zu\n", sizeof(((struct awg_pkt_initiation *)0)->ue));
	printf("layout.init.es_len=%zu\n", sizeof(((struct awg_pkt_initiation *)0)->es));
	printf("layout.init.ets_len=%zu\n", sizeof(((struct awg_pkt_initiation *)0)->ets));

	printf("layout.resp.size=%zu\n", sizeof(struct awg_pkt_response));
	printf("layout.resp.ue=%zu\n", offsetof(struct awg_pkt_response, ue));
	printf("layout.resp.en=%zu\n", offsetof(struct awg_pkt_response, en));
	printf("layout.resp.mac1=%zu\n", offsetof(struct awg_pkt_response, m));
	printf("layout.resp.en_len=%zu\n", sizeof(((struct awg_pkt_response *)0)->en));

	printf("layout.cookie.size=%zu\n", sizeof(struct awg_pkt_cookie));
	printf("layout.cookie.r_idx=%zu\n", offsetof(struct awg_pkt_cookie, r_idx));
	printf("layout.cookie.nonce=%zu\n", offsetof(struct awg_pkt_cookie, nonce));
	printf("layout.cookie.ec=%zu\n", offsetof(struct awg_pkt_cookie, ec));
	printf("layout.cookie.nonce_len=%zu\n",
	    sizeof(((struct awg_pkt_cookie *)0)->nonce));
	printf("layout.cookie.ec_len=%zu\n", sizeof(((struct awg_pkt_cookie *)0)->ec));

	printf("layout.data.header=%zu\n", offsetof(struct awg_pkt_data, buf));
	printf("layout.data.nonce_len=%zu\n",
	    sizeof(((struct awg_pkt_data *)0)->nonce));

	printf("layout.type.init=%d\n", AWG_TYPE_INITIATION);
	printf("layout.type.resp=%d\n", AWG_TYPE_RESPONSE);
	printf("layout.type.cookie=%d\n", AWG_TYPE_COOKIE);
	printf("layout.type.data=%d\n", AWG_TYPE_DATA);

	printf("limits.junk.max=%d\n", AWG_MAX_JUNK);
	printf("limits.junk.max_size=%d\n", AWG_MAX_JUNK_SIZE);
	printf("limits.data.min_size=%zu\n", (size_t)AWG_MIN_DATA_SIZE);

	/* 2. cookie MAC derivation over the production-derived message */
	mac_vectors();

	/* 3. junk emission at and around the configured bound */
	junk_case("at_bound", 3, 1500, 1500);
	junk_case("over_bound", 3, 1500, 1501);
	junk_case("jc_zero", 0, 100, 200);
	junk_case("jmin_zero", 3, 0, 200);
	junk_case("inverted", 3, 200, 100);
	junk_case("range", 4, 100, 200);

	/* 4. Noise KDF (HMAC-BLAKE2s extract/expand) */
	kdf_vectors();

	/* 5. counter window / replay handling, driven from stdin */
	counter_replay();

	/* blake2s self check: BLAKE2s-256("") RFC 7693 appendix A */
	{
		uint8_t d[BLAKE2S_HASH_SIZE];

		blake2s(d, sizeof(d), NULL, 0, "", 0);
		hex("blake2s.empty", d, sizeof(d));
		for (i = 0; i < 32; i++) {
			/* nothing: keeps -Wunused quiet on minimal builds */
		}
	}
	return 0;
}
