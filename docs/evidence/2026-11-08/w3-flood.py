#!/usr/bin/env python3
"""W3 malformed-datagram suite against the AWG kernel driver.

Sends hostile UDP datagrams from C (2203) to D (2204) port 51830.
Classes:
  - fixed lengths 1,2,3,4,15,16,31,32,63,64,147,148,149,150,151,65535
  - all-zero, all-0xff, random payloads
  - WireGuard-type byte (1..4) + truncated body
  - messages advertising a length larger than the datagram (u32 BE length
    prefix larger than the real size, for WG message types 1-4)
  - rapid repeat bursts (100 in a row) to exercise the cookie path
Total datagrams: 2000.
"""
import os
import socket
import sys
import time

HOST = "10.9.0.1"
PORT = 51830
TOTAL = 2000

def gen():
    # fixed lengths, cycled over three payload flavours
    lengths = [1, 2, 3, 4, 15, 16, 31, 32, 63, 64, 147, 148, 149, 150, 151, 65535]
    for n in lengths:
        yield bytes(n)                                   # all-zero
        yield b"\xff" * min(n, 4096) if n > 4096 else b"\xff" * n  # all-0xff (cap 4096 for the 64 KiB class)
        yield os.urandom(n if n <= 4096 else 4096)       # random
    # WG type byte + truncated bodies
    for t in (1, 2, 3, 4):
        hdr = t.to_bytes(4, "little")
        for extra in (0, 10, 32):
            yield hdr + os.urandom(extra)
    # advertise a length larger than the datagram (WG header carries
    # total message length in the 4-byte type word for types 1-4:
    # type word = (len << 8) | type in the real encoding)
    for t in (1, 2, 3, 4):
        for over in (1, 64, 1024):
            body = os.urandom(16)
            # 3.1 header-protection style: first 4 bytes = (len<<8)|type
            yield (((16 + over) << 8) | t).to_bytes(4, "big") + body
    # rapid burst: 100 repeats of a cookie-shaped message
    burst = (3).to_bytes(4, "little") + os.urandom(40)
    for _ in range(100):
        yield burst

def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sent = 0
    t0 = time.time()
    # cycle the generator until TOTAL datagrams are sent
    while sent < TOTAL:
        for pkt in gen():
            try:
                s.sendto(pkt, (HOST, PORT))
            except OSError as e:
                print(f"sendto failed at {sent}: {e}", file=sys.stderr)
                # keep going; a failed send is data, not a test abort
            sent += 1
            if sent >= TOTAL:
                break
    dt = time.time() - t0
    print(f"sent={sent} datagrams in {dt:.2f}s to {HOST}:{PORT}")
    s.close()

if __name__ == "__main__":
    main()
