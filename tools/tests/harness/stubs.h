/* Kernel scaffolding for the E2 conformance harness.
 *
 * These are *only* the kernel-side types and symbols the verbatim
 * production code compiled by e2_harness.c needs in order to link on a
 * userspace host.  They contain no AWG protocol logic: message structs,
 * constants and the junk/counter/KDF bodies all come from src/ (see
 * extract.py).  Where a value is a protocol constant that also lives in an
 * OpenBSD base header (the crypto headers), the value is asserted independently by
 * the Python vectors.
 */
#ifndef E2_STUBS_H
#define E2_STUBS_H

#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

/* <crypto/blake2s.h>, <crypto/curve25519.h>, <crypto/chachapoly.h>:
 * sizes fixed by the WireGuard/Noise protocol, not by this tree. */
#define CURVE25519_KEY_SIZE 32
#define CHACHA20POLY1305_KEY_SIZE 32
#define CHACHA20POLY1305_AUTHTAG_SIZE 16
#ifndef BLAKE2S_HASH_SIZE
#define BLAKE2S_HASH_SIZE 32
#endif

/* <net/wg_cookie.h> (OpenBSD base, not in this repository): two 16-byte MACs */
#define COOKIE_MAC_SIZE 16
#define COOKIE_NONCE_SIZE 24
#define COOKIE_ENCRYPTED_SIZE (COOKIE_MAC_SIZE + COOKIE_MAC_SIZE)
struct cookie_macs {
	uint8_t mac1[COOKIE_MAC_SIZE];
	uint8_t mac2[COOKIE_MAC_SIZE];
};

/* <sys/mutex.h> / <sys/rwlock.h> minimal stand-ins */
struct mutex { int m_unused; };
struct rwlock { int rw_unused; };
#define mtx_enter(m) ((void)(m))
#define mtx_leave(m) ((void)(m))
#define mtx_init_flags(m, ipl, name, flags) do { (void)(m); } while (0)

/* <sys/param.h> MIN */
#ifndef MIN
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#endif

/* <sys/systm.h> */
#define KASSERT(x) ((void)0)
static inline void
explicit_bzero(void *p, size_t n)
{
	volatile uint8_t *v = (volatile uint8_t *)p;

	while (n-- > 0)
		*v++ = 0;
}

/* <sys/systm.h> random source: deterministic for vector runs */
void arc4random_buf(void *, size_t);
uint32_t arc4random_uniform(uint32_t);
uint32_t arc4random(void);

/* Production prototypes the extracted junk sender calls (src/if_awg.c:386,
 * :400).  Implemented in e2_harness.c as recorders. */
struct awg_softc;
struct awg_peer;
struct awg_endpoint;
void awg_peer_get_endpoint(struct awg_peer *, struct awg_endpoint *);
void awg_send_buf(struct awg_softc *, struct awg_endpoint *, uint8_t *,
    size_t);

/* Minimal stand-ins for the two production types the junk sender touches.
 * Field names are production field names: a rename in src/if_awg.c breaks
 * compilation of the extracted function. */
struct awg_softc {
	uint16_t sc_awg_jc;
	uint16_t sc_awg_jmin;
	uint16_t sc_awg_jmax;
};
struct awg_peer { int p_unused; };
struct awg_endpoint { int e_unused; };

#endif
