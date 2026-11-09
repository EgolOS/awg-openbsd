#!/usr/bin/env python3
"""X25519 keypair generator for the awgctl lab fixture (stdlib only).

Prints one keypair as "<private-b64> <public-b64>" in the base64 form
awg(4)/wg(4) configs use (32 raw bytes, little-endian, RFC 7748). The
loopback handshake needs a real public key for the private key the
interface carries; a base64 string that merely decodes to 32 bytes is not
the same key, so the peer never completes a handshake with it.
"""
import base64
import os
import sys

P = (1 << 255) - 19
A24 = 121665


def x25519(scalar: int, u: int = 9) -> int:
    x1, x2, z2, x3, z3 = u, 1, 0, u, 1
    swap = 0
    for t in range(255, -1, -1):
        kt = (scalar >> t) & 1
        if kt == swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt
        a = (x2 + z2) % P
        aa = a * a % P
        b = (x2 - z2) % P
        bb = b * b % P
        e = (aa - bb) % P
        c = (x3 + z3) % P
        d = (x3 - z3) % P
        da = d * a % P
        cb = c * b % P
        x3 = (da + cb) * (da + cb) % P
        z3 = (da - cb) * (da - cb) % P
        x2 = aa * bb % P
        z2 = e * (aa + A24 * e) % P
    return x2 * pow(z2, P - 2, P) % P


def keypair(seed: bytes | None = None) -> tuple[str, str]:
    raw = bytearray(seed if seed is not None else os.urandom(32))
    raw[0] &= 248
    raw[1] &= 127
    raw[31] |= 64
    scalar = int.from_bytes(raw, "little")
    pub = x25519(scalar)
    priv_b64 = base64.b64encode(bytes(raw)).decode()
    pub_b64 = base64.b64encode(pub.to_bytes(32, "little")).decode()
    return priv_b64, pub_b64


if __name__ == "__main__":
    seed = None
    if len(sys.argv) > 1:
        seed = (sys.argv[1].encode() + b"\0" * 32)[:32]
    priv, pub = keypair(seed)
    print(f"{priv} {pub}")
