# Release notes — experimental release candidate

Baseline: OpenBSD 8.0 amd64, source archives verified against
`tools/upstream/8.0.sha256`.

## Signoff

- **Tag:** `v0.2.1-rc1` (experimental source release candidate).
- **Reviewed source:** `integ/installer-r6` at `9a8310a`.
- **Separate evidence set:** `test/network-matrix` at `25dc854` — not files
  implicitly present in this tree.

> v0.2.1-rc1 is an experimental source release candidate of native AmneziaWG for OpenBSD 8.0 amd64: the awg(4) driver, patched ifconfig(8), awgctl, awg-quick and configuration generator, reviewed at 9a8310a. It includes early endpoint preparation, corrected DOWN-state recovery and generation-based installer recovery. Independent Linux-host checks reproduce a 74/0 compiled-C fixture, correct I2 sensitivity and wrong-cause rejection, 26 fail-closed negative controls, C1–C24 oracle controls and a passing full repository gate. Bounded host lock/race and recovery campaigns remain accepted. The separate test/network-matrix evidence set at 25dc854 retains the corrected conformance ledger: OpenBSD↔OpenBSD legacy and 3.1 observations, including S3/S4 plus header protection, dual-stack traffic and hash-verified 4 MiB transfers both ways in the retained 3.1 run. Historical ident-only observations are not per-run image hashes or final-pin native certification. This is not production approval; native final-pin ioctl/pledge/unveil, native build/install/boot, complete native-suite execution, third-party client interoperability and soak stability remain unestablished, and peer replacement is non-atomic.

## What this is

Native AmneziaWG for OpenBSD 8.0: the `awg(4)` kernel driver — a pseudo-device
parallel to `wg(4)` — plus the userland tools that drive it (the patched
`ifconfig(8)`, `awgctl`, `awg-quick` and the config generator). Both protocol
versions are supported per interface: legacy (AmneziaWG < 3.1) and 3.1 with
junk packets, prefixes, header protection and the other obfuscation
parameters. This is shipped as an **experimental release candidate**, not a
production release: the supported platform is OpenBSD 8.0 amd64, and the
limitations below travel with the release.

## What is verified, and where the evidence is

Executed evidence comes from a two-VM OpenBSD↔OpenBSD lab pair (server and
client guests on a private L2 segment, kernel `OpenBSD 8.0 AWG.MP#11`) and,
for the recovery row, from a host-fixture test. The conformance ledger and
the run logs ship as a separate evidence set: they live on branch
`test/network-matrix` at the corrected head `25dc854` — the commit whose ledger
separates per-run image identity from quoted historical identity — and are not
part of this release tree. Read the ledgers *there*; the same files at `e774efb`
still carry provenance claims this candidate does not stand behind. The ledger's cell ids (E1, E3, E4, E8) are the citation keys:

| Verified behaviour | Evidence artefact |
|---|---|
| Legacy 2.0 handshake, dual-stack (IPv4 + IPv6) ICMP in both directions, and a 4 MiB bulk transfer whose receiver byte count (4 194 304) and sha256 match the sender's payload, with counters advancing | ledger cells E1/E3 — `reports/w3-dual-v2.md` (ping + underlay capture) and `reports/evidence/control-v3b.log` (transfer), on `test/network-matrix` @ `25dc854` |
| 3.1 with `S3`/`S4` and a shared header-protection key, dual stack, 4 MiB in **each** direction, 15/15 checks pass, handshake observed on both ends | `reports/evidence/awg-dual-v5-31.log` on `test/network-matrix` @ `25dc854` (harness `tools/lab/awg-dual-v5-31.sh` in the same branch, ledger cell E4) |
| Recovery after a failed `up`: the interface is put back to the snapshot taken before the run — the address list (in order), the MTU and the link up/down flag — and the addresses the failed run added are removed. The snapshot does not cover broadcast/destination metadata, routes beyond the host routes the tool installs, IPv6 address flags/lifetimes, or the peer list | `tools/tests/test_awgctl_r9_restore.py` (in this tree; drives the real `awgctl` against host fixtures; R4-08a) |

`make check` (shell syntax, Python compile, offline unit tests, the compiled-C
integration gate plus its sensitivity self-check, publish scan, generated-patch
freshness) passes on the Fedora (Linux) host this candidate was prepared on.
The integration gate is only counted because it can fail: `make check-cgate`
runs it twice, once clean and once with I2 rewritten by the fixture, and
requires the second run to fail with the changed value in the message. The four `make check-vm` negative-test gates (smoke, wire,
matrix, stress) were last captured on the `AWG.MP#5` lab build under
`docs/evidence/2026-11-07/` and were **not** re-run on the `AWG.MP#11` build
in this pass — treat those captures as the behaviour of that build, not as a
fresh gate for this candidate.

## What is NOT verified

The signoff review requires this list verbatim; do not shorten it.

- This is an experimental source release candidate for OpenBSD 8.0 amd64, not a production release or production approval. Evaluate it only on disposable experimental systems with a trusted full configuration backup, boot/recovery media and out-of-band access.
- Linux-host make check and -DAWGCTL_LINUX fixture builds do not certify actual OpenBSD ioctl ABI, pledge or unveil behavior, a final-pin native kernel build/install/boot, or a completed native test suite. Native suite execution was attempted but did not complete. The requested 1-vCPU performance baseline remains unestablished.
- Recovery is not a complete interface or kernel transaction. The bounded snapshot concerns the captured address/prefix list and its per-family order, MTU and link up/down flag; it does not cover the peer list, arbitrary route metadata, broadcast/destination metadata, IPv6 flags or lifetimes, family-enabled state, address regeneration or arbitrary scope conventions. Inspect interfaces, routes and peers after failures.
- Peer-list replacement is non-atomic and can destroy the previous peers before a replacement fails. Keep and reapply the full trusted previous configuration. awg-quick up is not an in-place refresh of an already active interface.
- Client interoperability with amneziawg-go, Android, Windows and the Linux kernel module is NOT RUN for this candidate. The retained current Go attempt did not establish a handshake in either profile; its cause is not adjudicated. Older unresolved-build observations are historical records, not candidate verification.
- No soak, leak-freedom or long-run stability claim is made. The evidence does not establish complete I1–I5 signature exchange, padding/random-trailer behavior, rekey timing, roaming or NAT-rebinding coverage. Bounded lock and crash/retry campaigns are not sudden-power-loss or exhaustive concurrency certification.
- Hostname endpoint lookup follows the first tunnel Address's family; an otherwise reachable opposite-family hostname may require a numeric endpoint. Tunnel and underlay address families are not inherently required to match.
- Restore the kernel and patched ifconfig as a matched pair. Installer kernel rollback does not perform the runbook's separate manual ifconfig restoration.
- The conformance ledger and historical run logs are a separate evidence set at test/network-matrix commit 25dc854, not files implicitly present in the release tree. Its 7 VERIFIED feature rows out of 10 are backed by E1, E3, E4 and E8; E4 records its own kernel image hashes, while E1/E3/E8 retain ident-only provenance where no per-run hash was captured. None certifies the final source pin natively.
- Fixture comparisons and structured records are bounded host-model checks, not authentication of arbitrary shell output or proof of arbitrary fixture-state equivalence. Do not infer scope-ID, full raw-netmask, missing-family, prefixless-token or alternate-address-spelling guarantees from the C probe's formatted strings. Release evidence requires actual ordinary make execution with the intended trusted suite and controls, not dry-run/touch/ignore/success-shell overrides or skipped controls; the control campaign alone does not enforce an exact nonzero make exit code.

## Upgrade and rollback

`docs/RUNBOOK-sysupgrade.md` is the operator procedure: keep and restore the
kernel and the patched `ifconfig` as a pair, and verify `uname -r` /
`uname -v` after the authorized reboot. The installer's `--rollback` restores
the previously installed kernel (the replaced kernel is kept as
`/bsd.awg-prev`); the runbook notes that restoring `/sbin/ifconfig` is a
separate manual step (AWG-029).

## How to report a problem

Open a private security advisory on the repository
(<https://github.com/EgolOS/awg-openbsd/security/advisories/new>) or mail the
maintainer listed in `README.md`; kernel panics, memory-safety bugs and
remote DoS should not be opened as public issues (`SECURITY.md`). Quote the
relevant limitation from the list above so the receiver knows what is
expected behaviour, and include `uname -r`/`uname -v`, the kernel `/bsd`
sha256 where recorded, and the command and console output of the failing run.
