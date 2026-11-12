/* BLAKE2s-256 (RFC 7693) and HMAC-BLAKE2s (RFC 2104) from scratch.
 * Only used as the crypto provider for the verbatim production code the E2
 * harness compiles; answers are known-answer checked against the RFCs by
 * tools/tests/test_e2_vectors.py. */
#include <string.h>

#include "blake2s.h"

static const uint32_t blake2s_iv[8] = {
    0x6A09E667UL, 0xBB67AE85UL, 0x3C6EF372UL, 0xA54FF53AUL,
    0x510E527FUL, 0x9B05688CUL, 0x1F83D9ABUL, 0x5BE0CD19UL,
};

static const uint8_t blake2s_sigma[10][16] = {
    { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 },
    { 14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3 },
    { 11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4 },
    { 7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8 },
    { 9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13 },
    { 2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9 },
    { 12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11 },
    { 13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10 },
    { 6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5 },
    { 10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0 },
};

static uint32_t
rotr32(uint32_t x, int n)
{
    return (x >> n) | (x << (32 - n));
}

static void
blake2s_compress(uint32_t h[8], const uint8_t block[BLAKE2S_BLOCK_SIZE],
    uint64_t t, int last)
{
    uint32_t v[16], m[16];
    int i, r;

    for (i = 0; i < 16; i++)
        m[i] = (uint32_t)block[4 * i] | ((uint32_t)block[4 * i + 1] << 8) |
            ((uint32_t)block[4 * i + 2] << 16) |
            ((uint32_t)block[4 * i + 3] << 24);
    for (i = 0; i < 8; i++)
        v[i] = h[i];
    for (i = 0; i < 8; i++)
        v[8 + i] = blake2s_iv[i];
    v[12] ^= (uint32_t)t;
    v[13] ^= (uint32_t)(t >> 32);
    if (last)
        v[14] = ~v[14];

#define G(a, b, c, d, x, y) do {                    \
    v[a] = v[a] + v[b] + (x); v[d] = rotr32(v[d] ^ v[a], 16); \
    v[c] = v[c] + v[d];       v[b] = rotr32(v[b] ^ v[c], 12); \
    v[a] = v[a] + v[b] + (y); v[d] = rotr32(v[d] ^ v[a], 8);  \
    v[c] = v[c] + v[d];       v[b] = rotr32(v[b] ^ v[c], 7);  \
} while (0)

    for (r = 0; r < 10; r++) {
        const uint8_t *s = blake2s_sigma[r];

        G(0, 4, 8, 12, m[s[0]], m[s[1]]);
        G(1, 5, 9, 13, m[s[2]], m[s[3]]);
        G(2, 6, 10, 14, m[s[4]], m[s[5]]);
        G(3, 7, 11, 15, m[s[6]], m[s[7]]);
        G(0, 5, 10, 15, m[s[8]], m[s[9]]);
        G(1, 6, 11, 12, m[s[10]], m[s[11]]);
        G(2, 7, 8, 13, m[s[12]], m[s[13]]);
        G(3, 4, 9, 14, m[s[14]], m[s[15]]);
    }
#undef G
    for (i = 0; i < 8; i++)
        h[i] ^= v[i] ^ v[i + 8];
}

void
blake2s(uint8_t *digest, size_t digest_len, const void *key, size_t key_len,
    const void *data, size_t data_len)
{
    uint32_t h[8];
    uint8_t block[BLAKE2S_BLOCK_SIZE];
    const uint8_t *p = data;
    uint8_t param[32];
    uint64_t t = 0;
    size_t outlen = digest_len ? digest_len : BLAKE2S_HASH_SIZE;
    size_t i;

    /* parameter block: digest_length | key_length << 8 | fanout 1 << 16 */
    memset(param, 0, sizeof(param));
    param[0] = (uint8_t)outlen;
    param[1] = (uint8_t)key_len;
    param[2] = 1;
    param[3] = 1;
    for (i = 0; i < 8; i++)
        h[i] = blake2s_iv[i] ^ ((uint32_t)param[4 * i] |
            ((uint32_t)param[4 * i + 1] << 8) |
            ((uint32_t)param[4 * i + 2] << 16) |
            ((uint32_t)param[4 * i + 3] << 24));

    if (key_len > 0) {
        memset(block, 0, sizeof(block));
        memcpy(block, key, key_len);
        t = BLAKE2S_BLOCK_SIZE;
        if (data_len == 0) {
            blake2s_compress(h, block, t, 1);
            for (i = 0; i < outlen; i++)
                digest[i] = (uint8_t)(h[i / 4] >> (8 * (i % 4)));
            memset(block, 0, sizeof(block));
            memset(h, 0, sizeof(h));
            return;
        }
        blake2s_compress(h, block, t, 0);
        memset(block, 0, sizeof(block));
    }

    while (data_len > BLAKE2S_BLOCK_SIZE) {
        memcpy(block, p, BLAKE2S_BLOCK_SIZE);
        t += BLAKE2S_BLOCK_SIZE;
        blake2s_compress(h, block, t, 0);
        p += BLAKE2S_BLOCK_SIZE;
        data_len -= BLAKE2S_BLOCK_SIZE;
    }
    memset(block, 0, sizeof(block));
    memcpy(block, p, data_len);
    t += data_len;
    blake2s_compress(h, block, t, 1);

    for (i = 0; i < outlen; i++)
        digest[i] = (uint8_t)(h[i / 4] >> (8 * (i % 4)));
    memset(block, 0, sizeof(block));
    memset(h, 0, sizeof(h));
}

void
blake2s_hmac(uint8_t *digest, const void *data, const void *key,
    size_t digest_len, size_t data_len, size_t key_len)
{
    uint8_t k[BLAKE2S_BLOCK_SIZE], inner_msg[BLAKE2S_BLOCK_SIZE + 64];
    uint8_t inner[32], outer_msg[BLAKE2S_BLOCK_SIZE + 32], keyhash[32];
    size_t i;

    if (data_len > 64) {
        /* not reachable from the Noise KDF (max 33 bytes); fail loudly */
        digest[0] = 0;
        return;
    }

    memset(k, 0, sizeof(k));
    if (key_len > BLAKE2S_BLOCK_SIZE) {
        blake2s(keyhash, 32, NULL, 0, key, key_len);
        memcpy(k, keyhash, 32);
    } else if (key_len > 0)
        memcpy(k, key, key_len);

    for (i = 0; i < BLAKE2S_BLOCK_SIZE; i++)
        inner_msg[i] = k[i] ^ 0x36;
    memcpy(inner_msg + BLAKE2S_BLOCK_SIZE, data, data_len);
    blake2s(inner, 32, NULL, 0, inner_msg, BLAKE2S_BLOCK_SIZE + data_len);

    for (i = 0; i < BLAKE2S_BLOCK_SIZE; i++)
        outer_msg[i] = k[i] ^ 0x5c;
    memcpy(outer_msg + BLAKE2S_BLOCK_SIZE, inner, 32);
    blake2s(digest, digest_len, NULL, 0, outer_msg,
        BLAKE2S_BLOCK_SIZE + 32);

    memset(k, 0, sizeof(k));
    memset(keyhash, 0, sizeof(keyhash));
    memset(inner, 0, sizeof(inner));
}
