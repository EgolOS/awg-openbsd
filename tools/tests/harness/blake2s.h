/* From-scratch BLAKE2s (RFC 7693) used as the crypto provider for the
 * verbatim production code compiled by the E2 harness.  It is not part of
 * the implementation under test: every vector that depends on it is
 * known-answer checked against the RFC in tools/tests/test_e2_vectors.py. */
#ifndef E2_BLAKE2S_H
#define E2_BLAKE2S_H

#include <stddef.h>
#include <stdint.h>

#define BLAKE2S_HASH_SIZE 32
#define BLAKE2S_BLOCK_SIZE 64

void blake2s(uint8_t *digest, size_t digest_len, const void *key,
    size_t key_len, const void *data, size_t data_len);

/* OpenBSD <crypto/blake2s.h> signature used by src/awg_noise.c:
 * blake2s_hmac(digest, data, key, digest_len, data_len, key_len) */
void blake2s_hmac(uint8_t *digest, const void *data, const void *key,
    size_t digest_len, size_t data_len, size_t key_len);

#endif
