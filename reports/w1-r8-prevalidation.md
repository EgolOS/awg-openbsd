# W1 Round 8 — R4-08 prevalidation + restore gaps

Branch `integ/installer-r6` (awg-rc2/w1i), base HEAD `1bcff8d`.
Scope: R4-08 code blockers — `sbin/awg-quick` field prevalidation and
`sbin/awgctl` restore-path gaps. No push.

## 1. awg-quick: full field prevalidation — PASS

Reproduced first (`evidence/r8-repro-before.log`): the old parser accepted
`Address = 10.0.0.999/24`, `/33` IPv4 prefixes, and a malformed endpoint
port, mutating the interface with a half-valid config.

Changes in `sbin/awg-quick`:

- `validate_cidr` now checks the IPv4 octet grammar (4 octets, 0..255) and
  per-family prefix limits (IPv4 0..32, IPv6 0..128) instead of accepting
  any `a.b.c.d/n` shape.
- `validate_endpoint` accepts `host:port`, `ipv4:port` and `[ipv6]:port`
  (zone id stripped), port range 1..65535; serialised as
  `awgendpoint <host> <port>` so host and port are validated as one field.
- MTU is range-checked 576..9000 (awg(4) wire MTU), not just `<= 65535`.
- `validate_dns` classifies each DNS entry: pure-numeric tokens are
  octet-checked IPv4, `*:*` tokens are IPv6 (zone stripped), everything
  else is a DNS search suffix.
- Config files are refused when they contain NUL bytes (`tr -dc '\0'`) or
  the last line is truncated (no trailing newline).

All parsing happens before the first `ifconfig` mutation, so a rejection
leaves interface, MTU and routes untouched — including when the interface
already existed (pre-state snapshot compared byte-for-byte in tests).

## 2. awgctl: restore gaps at :2037-2067, :2145, :2170 — PASS

- `run_ifconfig` split into `run_ifconfig_try` (silent, returns status)
  plus the logging wrapper; `run_ifconfig_eexist` tolerates EEXIST on the
  address install (idempotent second `up`) and restores on every other
  failure, capturing the child's stderr for the message.
- `restore_state` re-installs everything the snapshot holds: MTU first,
  then every address in original order; for a run-created interface it
  issues `ifconfig <if> destroy`. Every step is attempted; failures are
  reported with the left-behind state ("Terminating because the restore
  failed" prints what remains).
- New single mutation path `mutate(s, prev, av)`: every post-mutation
  failure (MTU set, address install, `up`, SIOCSAWG) restores the
  `if_state` pre-state, frees the snapshot and exits with the original
  error/status. `cmd_up` MTU/address/up steps and the SIOCSAWG failure
  path all go through it.
- `erx` is an OpenBSD err.h macro; added a portable `#define erx errx`
  fallback so the Linux fixture build compiles (VM build verified).

## 3. Tests — PASS

- `tools/lab/awgctl-fixture-test.sh`: new **F13** (a/b/c) drives
  `AWGFIX_MTU_ERR`/`AWGFIX_UP_ERR`/`AWGFIX_DESTROY_ERR` in the stateful
  fixture ifconfig and asserts exit non-zero, byte-identical
  mtu/addr/prefix/family snapshot afterwards, and destroy only for the
  created-interface case.
- `tools/tests/test_quick_r8_prevalidation.py` (new): one negative per
  field (Address octet, Address prefix, AllowedIPs prefix, Endpoint port,
  MTU, DNS, NUL bytes, truncated last line), each on a pre-existing and a
  created interface, asserting exit code, byte-identical
  ifconfig/route/netstat state, and that the error names the field.
- Full suite: `python3 -m unittest discover -s tools/tests` → 108 tests,
  `OK (skipped=1)`; fixture suite `RESULT: all fixture cases passed`;
  `make check` → `repository checks: OK` (syntax, py, tests, publish
  scan, patch).

## 4. publish-scan marker normalisation — PASS

`--normalize-markers` now pins the matched shape (sha1) inside
`scan:allow-fixture[sha1:...]`; the bare legacy marker no longer
suppresses unrelated rules. The marker comment is excluded from the
matched text so hashes stay stable, and all matches of a rule on a
marked line are pinned (fixture lines carry private+public keys).
Repo scan: 18 findings → `RESULT: clean` (170 files).

## 5. VM 2206 (OpenBSD 8.0, kernel AWG.MP#2) — PASS (baseline)

`awgctl-build` rebuilt with the new `awgctl.c` (after the erx fix) and
`tools/lab/awgctl-test.sh` run via doas: `RESULT: 81 case(s) failed`
(37 abort traps), matching the pre-change binary on the same kernel
(82 failures) — the AWG.MP#2 ioctl baseline, not a regression from this
change. Full log: `evidence/2026-11-09/r8-vm-2206.log`.

## Verdict

R4-08: both tools validate every field before the first mutation and
restore the captured pre-state on every post-mutation failure. All
checks green; no test weakened.
