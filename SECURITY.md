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
- **`DisableCookies = on` removes the pre-handshake gate.** With cookies
  enabled, a sender must answer a cookie challenge and is rate limited per
  address *before* the X25519 in `consume_initiation`; with it off, every
  queued initiation costs one X25519 and only the bounded handshake queue
  (`MAX_QUEUED_HANDSHAKES`) limits the work. Expect remote CPU load and
  starvation of legitimate handshakes while a flood runs; leave cookies on
  unless the peer implementation requires otherwise.
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
| H1–H4 collisions in legacy mode are not rejected | A peer's response can be classified as an initiation | Accepted by design: classification is length-first, the handshake structs differ in size, and the Linux module behaves the same — `docs/LOCKING.md` §5 |
| `Jmax` above 1500 was silently ignored | The driver sent no junk packets at all, so the fingerprint differed from the Linux peers | **Fixed**: rejected with `EINVAL` |
| Timings were not cross-checked | `RejectAfterTime` ≤ keepalive + rekey made every packet rekey | **Fixed**: rejected when both arrive in one call |
| Obfuscation fields are read without a lock | A reconfiguration can drop packets mid-update; no memory-safety consequence | Accepted, analysed in `docs/LOCKING.md` §2-§3; change parameters with the interface down |
| A jumbo MTU plus `S4` plus the padding window could exceed `MAXMCLBYTES` | `panic: m_clget` on transmit (and a NULL pool without DIAGNOSTIC) | **Fixed**: `awg_encap()` drops the packet instead |
| `awg-quick` put the private key on the `ifconfig` command line | Key readable via `ps(1)`, in core dumps and in stderr logs | **Fixed**: `awgkey -` reads it from stdin; `cmd()` redacts key arguments |
| Numeric config values reached a shell arithmetic context | `S1 = x[$(cmd)]` executed as root while parsing the config | **Fixed**: every numeric field is validated before use |
| Config file and directory modes were assumed, not enforced | A `0644` config exposes the long-term private key | **Fixed**: `awg-quick` refuses a config that is not `0600`/`0400` |
| IPv6 `::/0` with an IPv4-only `Address` silently bypassed the tunnel | IPv6 traffic left unencrypted while `up` exited 0 | **Fixed**: fails closed; `AWG_ALLOW_IPV6_BYPASS=1` opts in to the old behaviour |
| `install.sh` built root-owned kernel sources and a base `ifconfig` from unverified downloads | Supply chain: whoever controls the download controls `/bsd` | **Fixed**: `SHA256.sig` verified with `signify(1)` before extraction |
| `install.sh` wrote fixed paths under `/tmp` as root | A planted symlink could truncate or overwrite a root-owned file | **Fixed**: `mktemp` for the log and work directory |
| `tools/publish-scan.py` WireGuard-key rule could never match | The publish gate reported "clean" for a leaked 44-character key | **Fixed**: lookahead instead of `\b`, `git ls-files -z`, unscanned files reported |

## Userspace: config files are root programs

`awg-quick` executes `PreUp`/`PostUp`/`PreDown`/`PostDown` hooks with `eval` as
root, which is the documented `wg-quick` behaviour. Combined with the fact that
config content usually arrives from a VPN provider, this means: **a config file
is a root program.** Only copy configs you trust; the private-key, arithmetic
and permission fixes above close the accidental paths, not the deliberate one.

## Hardening checklist for deployments

1. `sysctl kern.version` after `sysupgrade`: a new kernel replaces `bsd`, so
   the patched kernel must be reinstalled (see `docs/RUNBOOK-sysupgrade.md`).
2. Keep `/etc/amnezia/*.conf` mode `0600` — it holds private keys.
3. Prefer `Jc` ≤ 12 and `S1`–`S4` ≤ 1280; the driver now rejects anything
   above, but the values are also capped by the client implementation.
4. Run `pf(4)` with a default-deny policy and allow only the AWG UDP port.
5. Enable `ddb.panic=0` on production hosts so a panic reboots instead of
   dropping to the debugger, and make sure the console is reachable.
