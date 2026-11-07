# Interoperability run — OpenBSD kernel driver ↔ official amneziawg-go (parent-run)

Performed by the principal maintainer after W3's worker run ended without a
completed matrix (see `reports/w3.md`). Guest VMs are disposable lab clones;
nothing here touched a production host.

- Kernel side: VM 2204 (`obsd-d`, OpenBSD 8.0, `AWG.MP#5`), driver unchanged at `7afa788`.
- Userspace side: VM 2203 (`obsd-c`), `amneziawg-go` built from
  `/home/aigeus/.hermes/cache/scratch/awg-assurance/reference-go` at commit
  `b5928efb6ca19f0153958460c3d141f04abc5c2e` (`go1.27.1 openbsd/amd64`).
- Underlay: private qemu socket L2, `vio1` 10.9.0.1/24 (kernel) ↔ 10.9.0.2/24 (Go).
  `pf` disabled on both, left disabled. Tunnel addresses 10.77.0.1/24 ↔ 10.77.0.2.

## Client invocation (verified against the source, not guessed)

`awg-go` on OpenBSD is `awg-go [-f] tunN` — the interface must be named `tun[0-9]*`,
and configuration is a `key=value` payload on the UAPI socket
`/var/run/amneziawg/<ifn>.sock`. The payload must carry the device section and the
peer section in one `set=1` with a blank line between them; a second invocation
hits `device busy`. Reads and writes both need root (`doas`).

Keys: the kernel takes base64, the UAPI takes hex. Public keys were cross-checked
two ways — derived with an independent X25519 implementation and confirmed
identical to what the kernel prints (`awgpubkey`) for the same private key.

## Legacy profile (AWG 2.x framing)

Both ends: `Jc=3 Jmin=10 Jmax=25 S1=26 S2=33 H1..H4=100/200/300/400`,
kernel `awgversion legacy`, `awgpka 5` on the kernel side only.

| Check | Result |
|---|---|
| handshake (kernel sees `last handshake`) | PASS — `last handshake: 6 seconds ago` |
| ICMP kernel→Go | PASS — 3/3, 1.44/1.49/1.60 ms |
| ICMP Go→kernel | PASS — 3/3, 1.15/1.29/1.48 ms |
| 8 MiB kernel→Go, sha256 | PASS — 8388608 bytes, `efc65873f2d0e6ddbed23f28e1ae6a94dbae2afa3c056dfaa8749a79f45b1910` both ends |
| 8 MiB Go→kernel, sha256 | PASS — 8388608 bytes, `efc65873…b1910` both ends |
| counters | kernel `tx: 9304285 rx: 9292612`; Go `tx_bytes=28615163 rx_bytes=18943800` |

## 3.1 profile (S3/S4 + header protection)

Kernel: `ifconfig awg0 awgversion 3.1 awgs3 18 awgs4 24 awghpk <b64>` (peer survived
the version switch; `last handshake` continued and a rekey completed during the run).
Go: `s3=18 s4=24 header_protection_key=<hex>` on the device section. The Go UAPI
only accepts `protocol_version=1`; 3.1 on this implementation is the obfuscation
layer (S3/S4/HPK/I*), which is what both sides were configured with.

| Check | Result |
|---|---|
| ICMP after switching both ends | PASS — 2/3 (one packet lost during the transition) |
| 8 MiB kernel→Go, sha256 | PASS — `15f1bfbec59e6a43f41e0f4186b99efb0f670ec126836cd1fe7812d004376ba2` both ends |
| 8 MiB Go→kernel, sha256 | PASS — `15f1bfbe…76ba2` both ends |
| new handshake after traffic | PASS — `last handshake: 3 seconds ago`, counters advanced |

## What this does and does not establish

Established: the OpenBSD kernel driver and the official Go implementation agree on
the wire for the legacy profile and for the 3.1 obfuscation parameters
(S3/S4 + header protection), in both directions, with hash-verified payloads.

Not established here: I1–I5 signature exchange (configured on neither side in this
run), content padding addition, random trailers, disable-cookies, the Android and
Windows clients, rekey timing distributions, roaming, NAT rebinding, MTU edge cases.

## Lab gotchas hit while doing this (recorded so the next run is faster)

1. `ifconfig awgN` **as a non-root user prints no keys and no peers** — the driver
   hides that state. Reading it unprivileged looks exactly like "the configuration
   vanished"; use `doas ifconfig`. This cost a false bug report in this session.
2. After a guest reboot the private-L2 NIC comes back without its address; the
   tunnel then shows counters on one side only. `doas ifconfig vio1 inet 10.9.0.1/24 up`.
3. `awgpka` installs no route; the peer's `awgaip` did not add one either. Give both
   ends a subnet address (`inet 10.77.0.1/24`) or add the route by hand — with `/32`
   on both ends the handshake succeeds and ICMP silently dies.
4. `pkill -f awg-go` also matches the shell running it and kills the ssh session;
   kill by pid from `ps`.
