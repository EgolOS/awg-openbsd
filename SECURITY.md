# Security policy

## Reporting

Open a private security advisory on this repository
(<https://github.com/EgolOS/awg-openbsd/security/advisories/new>) or mail the
maintainer listed in `README.md`. Please do not open a public issue for a
kernel panic, memory-safety bug or remote DoS. We aim to answer within seven
days.

## Threat model

`awg(4)` is a kernel driver. Anyone who can send UDP packets to the interface
port reaches the entire code path that parses packets, so the parsing code is
written to the same rules as `if_wg.c`: every field is bounds-checked before
use, and only packets that authenticate are allowed to touch per-peer state.

What the driver does **not** protect against:

- **A peer with a valid session key** can influence padding sizes (see below).
  Treat peer keys as trusted input.
- **Anyone who can write the interface configuration** (`doas ifconfig`,
  root-owned `/etc/amnezia/*.conf`) can influence allocation sizes. Every
  config value is now validated, but a hostile root is out of scope.
- **Traffic analysis.** Obfuscation changes the wire format, not the timing
  or volume of traffic.

## Known limitations

| Limitation | Impact | State |
|---|---|---|
| S1–S4 were accepted up to 65535 | `panic: m_clget` on the next handshake — kernel DoS from a config value | **Fixed**: capped at 1280 (`AWG_MAX_PADDING`), `EINVAL` above |
| `awg_send_buf` ignored a failed cluster allocation | `m_align` assertion panic under memory pressure | **Fixed**: `M_EXT` check + `MAXMCLBYTES` guard |
| Peer-inflated UDP window | Padding up to 64 KB, one datagram per packet | **Fixed**: clamped to 1500 |
| `I1`–`I5` were sent regardless of MTU | Fragmented signature packets | **Fixed**: packets above `if_mtu` are skipped |
| `conf/files` attached shared crypto to `wg` only | `awg`-without-`wg` config links with undefined symbols | **Fixed**: entries are `wg \| awg` |
| H1–H4 collisions in legacy mode are not rejected | A peer's response can be classified as an initiation | Open, matches the Linux module |
| Obfuscation fields are read without a lock | A reconfiguration can drop packets mid-update | Open, documented in the source |

## Hardening checklist for deployments

1. `sysctl kern.version` after `sysupgrade`: a new kernel replaces `bsd`, so
   the patched kernel must be reinstalled (see `docs/RUNBOOK-sysupgrade.md`).
2. Keep `/etc/amnezia/*.conf` mode `0600` — it holds private keys.
3. Prefer `Jc` ≤ 12 and `S1`–`S4` ≤ 1280; the driver now rejects anything
   above, but the values are also capped by the client implementation.
4. Run `pf(4)` with a default-deny policy and allow only the AWG UDP port.
5. Enable `ddb.panic=0` on production hosts so a panic reboots instead of
   dropping to the debugger, and make sure the console is reachable.
