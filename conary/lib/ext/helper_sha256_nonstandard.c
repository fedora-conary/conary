/*
 NOTE: DO NOT USE unless you need an implementation of sha256 which has
 an implementation error when the length of the string being
 hashed is 55 % 64.  See http://pycrypto.cvs.sourceforge.net/viewvc/pycrypto/crypto/src/SHA256.c?r1=1.3&r2=1.4 for fix to this bug.  In this case,
 we want to keep the bw compatible bug for creating older signatures.
 
 LICENSE:
 ===================================================================
 Distribute and use freely; there are no restrictions on further
 dissemination and usage except those imposed by the laws of your
 country of residence.  This software is provided "as is" without
 warranty of fitness for use or suitability for any purpose, express
 or implied. Use at your own risk or not at all.
 ===================================================================

 Incorporating the code into commercial products is permitted; you do
 not have to make source available or contribute your changes back
 (though that would be nice).

 --amk                                                             (www.amk.ca)
 END LICENSE
 *
 * An implementation of the SHA-256 hash function, this is endian neutral
 * so should work just about anywhere.
 *
 * This code works much like the MD5 code provided by RSA.  You sha_init()
 * a "sha_state" then sha_process() the bytes you want and sha_done() to get
 * the output.
 *
 * Revised Code:  Complies to SHA-256 standard now.
 *
 * Tom St Denis -- http://tomstdenis.home.dhs.org
 * */

#include <string.h>
#include  "helper_sha256_nonstandard.h"

/* the K array */
static const uint32_t K[64] = {
    0x428a2f98UL, 0x71374491UL, 0xb5c0fbcfUL, 0xe9b5dba5UL, 0x3956c25bUL,
    0x59f111f1UL, 0x923f82a4UL, 0xab1c5ed5UL, 0xd807aa98UL, 0x12835b01UL,
    0x243185beUL, 0x550c7dc3UL, 0x72be5d74UL, 0x80deb1feUL, 0x9bdc06a7UL,
    0xc19bf174UL, 0xe49b69c1UL, 0xefbe4786UL, 0x0fc19dc6UL, 0x240ca1ccUL,
    0x2de92c6fUL, 0x4a7484aaUL, 0x5cb0a9dcUL, 0x76f988daUL, 0x983e5152UL,
    0xa831c66dUL, 0xb00327c8UL, 0xbf597fc7UL, 0xc6e00bf3UL, 0xd5a79147UL,
    0x06ca6351UL, 0x14292967UL, 0x27b70a85UL, 0x2e1b2138UL, 0x4d2c6dfcUL,
    0x53380d13UL, 0x650a7354UL, 0x766a0abbUL, 0x81c2c92eUL, 0x92722c85UL,
    0xa2bfe8a1UL, 0xa81a664bUL, 0xc24b8b70UL, 0xc76c51a3UL, 0xd192e819UL,
    0xd6990624UL, 0xf40e3585UL, 0x106aa070UL, 0x19a4c116UL, 0x1e376c08UL,
    0x2748774cUL, 0x34b0bcb5UL, 0x391c0cb3UL, 0x4ed8aa4aUL, 0x5b9cca4fUL,
    0x682e6ff3UL, 0x748f82eeUL, 0x78a5636fUL, 0x84c87814UL, 0x8cc70208UL,
    0x90befffaUL, 0xa4506cebUL, 0xbef9a3f7UL, 0xc67178f2UL
};

/* Various logical functions */
#define Ch(x,y,z)    ((x & y) ^ (~x & z))
#define Maj(x,y,z)  ((x & y) ^ (x & z) ^ (y & z))
#define S(x, n)        (((x)>>((n)&31))|((x)<<(32-((n)&31))))
#define R(x, n)        ((x)>>(n))
#define Sigma0(x)    (S(x, 2) ^ S(x, 13) ^ S(x, 22))
#define Sigma1(x)    (S(x, 6) ^ S(x, 11) ^ S(x, 25))
#define Gamma0(x)    (S(x, 7) ^ S(x, 18) ^ R(x, 3))
#define Gamma1(x)    (S(x, 17) ^ S(x, 19) ^ R(x, 10))

/* compress 512-bits */
static void
sha_compress(sha256ns_hash_state * md)
{
    uint32_t S[8], W[64], t0, t1;
    int i;

    /* copy state into S */
    for (i = 0; i < 8; i++)
        S[i] = md->state[i];

    /* copy the state into 512-bits into W[0..15] */
    for (i = 0; i < 16; i++)
        W[i] = (((uint32_t) md->buf[(4 * i) + 0]) << 24) |
            (((uint32_t) md->buf[(4 * i) + 1]) << 16) |
            (((uint32_t) md->buf[(4 * i) + 2]) << 8) |
            (((uint32_t) md->buf[(4 * i) + 3]));

    /* fill W[16..63] */
    for (i = 16; i < 64; i++)
        W[i] = Gamma1(W[i - 2]) + W[i - 7] + Gamma0(W[i - 15]) + W[i - 16];

    /* Compress */
    for (i = 0; i < 64; i++) {
        t0 = S[7] + Sigma1(S[4]) + Ch(S[4], S[5], S[6]) + K[i] + W[i];
        t1 = Sigma0(S[0]) + Maj(S[0], S[1], S[2]);
        S[7] = S[6];
        S[6] = S[5];
        S[5] = S[4];
        S[4] = S[3] + t0;
        S[3] = S[2];
        S[2] = S[1];
        S[1] = S[0];
        S[0] = t0 + t1;
    }

    /* feedback */
    for (i = 0; i < 8; i++)
        md->state[i] += S[i];
}

/* init the SHA state */
void
sha256ns_init(sha256ns_hash_state * md)
{
    md->curlen = md->length = 0;
    md->state[0] = 0x6A09E667UL;
    md->state[1] = 0xBB67AE85UL;
    md->state[2] = 0x3C6EF372UL;
    md->state[3] = 0xA54FF53AUL;
    md->state[4] = 0x510E527FUL;
    md->state[5] = 0x9B05688CUL;
    md->state[6] = 0x1F83D9ABUL;
    md->state[7] = 0x5BE0CD19UL;
}

void
sha256ns_copy(const sha256ns_hash_state *source, sha256ns_hash_state *dest)
{
    memcpy(dest, source, sizeof(sha256ns_hash_state));
}

void
sha256ns_update(sha256ns_hash_state * md, const unsigned char *buf, int len)
{
    while (len--) {
        /* copy byte */
        md->buf[md->curlen++] = *buf++;

        /* is 64 bytes full? */
        if (md->curlen == 64) {
            sha_compress(md);
            md->length += 512;
            md->curlen = 0;
        }
    }
}

void
sha256ns_final(sha256ns_hash_state * md, unsigned char *hash)
{
    int i;

    /* increase the length of the message */
    md->length += md->curlen * 8;

    /* append the '1' bit */
    md->buf[md->curlen++] = 0x80;

    /* if the length is currenlly above 56 bytes we append zeros
                               * then compress.  Then we can fall back to padding zeros and length
                               * encoding like normal.
                             */
    if (md->curlen >= 56) {
        for (; md->curlen < 64;)
            md->buf[md->curlen++] = 0;
        sha_compress(md);
        md->curlen = 0;
    }

    /* pad upto 56 bytes of zeroes */
    for (; md->curlen < 56;)
        md->buf[md->curlen++] = 0;

    /* since all messages are under 2^32 bits we mark the top bits zero */
    for (i = 56; i < 60; i++)
        md->buf[i] = 0;

    /* append length */
    for (i = 60; i < 64; i++)
        md->buf[i] = (md->length >> ((63 - i) * 8)) & 255;
    sha_compress(md);

    /* copy output */
    for (i = 0; i < 32; i++)
        hash[i] = (md->state[i >> 2] >> (((3 - i) & 3) << 3)) & 255;
}


void
sha256ns_digest(const unsigned char *data, int len, unsigned char *digest)
{
    sha256ns_hash_state state;
    sha256ns_init(&state);
    sha256ns_update(&state, data, len);
    sha256ns_final(&state, digest);
}

/* vim: set sts=4 sw=4 expandtab : */
