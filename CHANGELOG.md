# Changelog

Notable changes to `awg(4)` and its tooling. Format follows
[Keep a Changelog](https://keepachangelog.com/); the project does not use
semantic versioning in a strict sense — a tag marks a tree that was built and
tested as a unit.

Findings are numbered (`AWG-0xx`) and tracked with state and regression test in
[`docs/SECURITY-AUDIT.md`](docs/SECURITY-AUDIT.md).

## [Unreleased]

### Security

- `AWG-007` (cookie-less handshake flood): an interface with `DisableCookies`
  set now admits at most `AWG_HS_ADMIT_RATE` handshake datagrams per second
  (burst `AWG_HS_ADMIT_BURST`) before the X25519, counting the rest as input
  errors. Wire format unchanged; interfaces with cookies enabled are untouched.
  Rationale and measurements are in `docs/SECURITY-AUDIT.md` (AWG-007).

## [0.2.0] — 2026-11-07

Security hardening, a real test gate, install rollback, and the documentation
that says what is and is not verified. Baseline: OpenBSD 8.0 amd64,
`tools/upstream/8.0.sha256`.

### Security

Fixed, with the surface each one needed to reach:

- kernel panics from configuration: `S1`–`S4` were accepted up to 65535 and the
  next handshake died in `m_clget` (`AWG-002`); `Jmin > Jmax` produced junk that
  the peer never expected (`AWG-016`); a jumbo MTU plus `S4` crossed
  `MAXMCLBYTES` on transmit (`AWG-002`).
- kernel panic from a plain `ifconfig` command: `awgversion legacy` on a 3.1
  interface freed an uninitialised stack array (`AWG-030`, `panic: free:
  non-malloced addr ... type devbuf`).
- root command execution from a provider-supplied config: numeric fields reached
  a bash arithmetic context (`AWG-003`).
- private key disclosure: the key travelled on the `ifconfig` argument vector
  and into the log (`AWG-005`); config file modes were assumed, not enforced
  (`AWG-010`).
- supply chain: `install.sh` built root-owned kernel sources and a base
  `ifconfig` from unverified downloads (`AWG-006`); fixed `/tmp` paths were
  followed as root (`AWG-009`).
- silent VPN leak: `AllowedIPs = ::/0` with an IPv4-only `Address` skipped the
  IPv6 routes and exited 0 (`AWG-011`). Now fails closed;
  `AWG_ALLOW_IPV6_BYPASS=1` restores the old behaviour.
- installer could report success for a kernel it never built, because
  `make | tee` returns tee's status (`AWG-031`).
- publish gate: the WireGuard-key rule could never match, so the leak scanner
  said "clean" unconditionally (`AWG-012`).
- kernel correctness: 16-bit receive-rekey margin (`AWG-017`), NULL iterator in
  `awg_index_drop()` (`AWG-019`), timings not cross-checked across separate
  ioctls (`AWG-008`), `awgversion legacy` resurrecting the previous I-packet
  (`AWG-028`), one static underload timer shared by every interface
  (`AWG-015`), ispec leak on an error path (`AWG-022`), and `sync-check.py`
  exiting 0 when upstream added or removed a function (`AWG-024`).

Accepted with reasoning rather than patched: `PreUp`/`PostUp` hooks are a root
program by design (`AWG-004`), `DisableCookies` removes the pre-handshake gate
(`AWG-007`), padding is not charged against `if_mtu` (`AWG-018`), the `Jc` burst
is bounded at 96 KB per handshake (`AWG-021`), obfuscation fields are read
lock-free on the packet path (`AWG-020`), and legacy `H1`–`H4` collisions are
not rejected (`docs/LOCKING.md` §5, because classification is length-first).

### Added

- `tools/lab/` gates behind `make check-vm`: `smoke-test` (lifecycle,
  handshake, malformed input), `wire-test` (zero-length to 65 000-byte
  datagrams), `matrix-test` (every parameter at its bound and the
  interdependent rules), `stress-test` (200 version switches and peer churn
  under traffic).
- `tools/lab/interop-test.sh` and `make check-interop`: two VMs, real link,
  3.1 handshake, ICMP through the tunnel, 8 MiB transfer, counters.
- `Makefile`: `check`, `check-vm`, `check-interop`, `baseline`, `manifest`.
- `tools/upstream-diff.py` + `tools/upstream/8.0.sha256`: function-level
  divergence against a recorded stock tree (40 identical / 37 modified /
  24 AWG-only in `if_awg.c`; 36 / 5 / 4 in `awg_noise.c`).
- `tools/release-manifest.py` (kernel, patch and tool hashes with test results)
  and `.github/workflows/ci.yml` (host checks on every push; kernel build on a
  self-hosted OpenBSD runner when `OPENBSD_RUNNER=true`).
- Documentation: `ARCHITECTURE.md`, `UPSTREAM-TRACKING.md`, `SECURITY-AUDIT.md`,
  `LOCKING.md`, `RUNBOOK-security-updates.md`, `ADMIN-INTERFACE.md`.

### Changed

- `install.sh`: `--check` (report, change nothing), `--build-only` (verified
  artifact, `/bsd` untouched), `--kernel-only` (staged swap, the replaced kernel
  kept as `/bsd.awg-prev` and `/obsd`), `--rollback` (restores the pair, parks
  the failed kernel as `/bsd.failed.<timestamp>`).
- `awg-quick`: numeric validation for every parameter, `awgkey -` so the
  private key never reaches `argv`, redacting `cmd()`, `0600` config
  requirement, IPv6 fail-closed.
- `conf/files` registration attaches `wg_cookie.c` and the crypto helpers to the
  `awg` attribute, so a kernel with `awg` but without `wg` links.
- README and test docs distinguish what this fork verified from what the
  upstream fork reported.

### Known limitations

- Lock-free obfuscation reads: a reconfiguration can drop packets mid-update;
  no memory-safety consequence (`docs/LOCKING.md` §2-§4, snapshot refactor
  planned).
- No long-run soak, no fuzzing, no throughput measurement with `iperf3` in this
  repository; the two-VM lab has no third machine, so client interoperability
  (Android/Windows, `amneziawg-go`, Linux module) is inherited, not re-run.
- 7.9 compiles but has no kernel build or runtime test.

### Verification

- OpenBSD 8.0 amd64 KVM: `install.sh --all` from scratch, kernel `AWG.MP#5`,
  `make check-vm` all four gates pass, `make check-interop` passes against a
  second VM, `--rollback` booted the previous kernel and was verified by
  `uname -v`.
- `make check` on a clean checkout: shell syntax, Python compile, publish scan,
  generated patch current.

## Older releases

`v0.1.0` … `v0.1.5` predate the changelog; see the git tags. `v0.1.5` was the
three-way sync check.
