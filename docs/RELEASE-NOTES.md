# Release notes — v0.3.0

Baseline: OpenBSD 8.0 amd64, source archives verified against
`tools/upstream/8.0.sha256`.

## Signoff

- **Tag:** `v0.3.0` (experimental release).
- **Reviewed source:** integrated tree at `a1393fc` (eng/integration-e1) plus accepted
  E2 branches merged for this release.
- **Separate evidence set:** `test/network-matrix` at `25dc854` — an external
  evidence set, not published in the repository (neither the cycle repo nor
  the public origin lists that ref), so its ledgers cannot be opened from this
  checkout.
- **Integrated tree:** `eng/integration-e1` at `a1393fc` merges the accepted
  E1 parents (native fixture `a54fec6`, installer baseline `3777792`, kernel
  baseline `ec16a75`, installer transaction `b4dc032`, source completeness
  `91359bb`) and, on top of them, the accepted E2 VM-lab scripting,
  upstream-delta, CI-hardening and AWG-041 commits (`b16ab8d`..`a1393fc`,
  18 commits) plus the kernel junk sweep `083e0d9`. The rest of the E2 wave —
  CLI contract (`e2/cli-contract-r2` `ec9469d`), status JSON
  (`e2/status-json-v2` `93ca14c`), wire vectors (`e2/vectors-r8` `7715bc2`),
  config fuzz (`e2/config-fuzz-r8` `8452256`), uninstall v3
  (`e2/uninstall-v3` `53d4e2b`), hygiene r5 (`e2/hygiene-r5` `89ec8c9`),
  trace (`e2/trace` `561c441`), reproducible release archive
  (`e2/repro-final` `c903478`, base `0e557e1`), privilege audit
  (`e2/userspace-priv-audit` `a17e27f`) with its remediation
  (`eng/e2-priv-remediation-r26` `66fffd8`), installer fixes
  (`e2/install-fixes` `283fcda`), and harness collision-freedom
  (`e2/harness-par` `ecba79a`) — are accepted on their own branches and
  merged into this release. The E1 integration re-ran `make check` at the
  integrated pin (rc=0, 138 tests, 1 skipped,
  `evidence/e2-docs/e2docs-base-make-check.log`); the native gates below keep
  their last-captured status.

> v0.3.0 is an experimental release of native AmneziaWG for OpenBSD 8.0 amd64:
> the awg(4) driver, patched ifconfig(8), awgctl, awg-quick and configuration
> generator, reviewed at the integrated pin `a1393fc`. It includes early
> endpoint preparation, corrected DOWN-state recovery and generation-based
> installer recovery. Independent Linux-host checks reproduce a 74/0 compiled-C
> fixture, correct I2 sensitivity and wrong-cause rejection, 26 fail-closed
> negative controls, C1–C24 oracle controls and a passing full repository gate.
> Bounded host lock/race and recovery campaigns remain accepted. The separate
> test/network-matrix evidence set at 25dc854 retains the corrected conformance
> ledger: OpenBSD↔OpenBSD legacy and 3.1 observations, including S3/S4 plus
> header protection, dual-stack traffic and hash-verified 4 MiB transfers both
> ways in the retained 3.1 run. Historical ident-only observations are not
> per-run image hashes or final-pin native certification. This is not production
> approval; native final-pin ioctl/pledge/unveil, native build/install/boot,
> complete native-suite execution, third-party client interoperability and soak
> stability remain unestablished, and peer replacement is non-atomic.

## What this is

Native AmneziaWG for OpenBSD 8.0: the `awg(4)` kernel driver — a pseudo-device
parallel to `wg(4)` — plus the userland tools that drive it (the patched
`ifconfig(8)`, `awgctl`, `awg-quick` and the config generator). Both protocol
versions are supported per interface: legacy (AmneziaWG < 3.1) and 3.1 with
junk packets, prefixes, header protection and the other obfuscation
parameters. This is shipped as an **experimental release**, not a
production release: the supported platform is OpenBSD 8.0 amd64, and the
limitations below travel with the release.

## How it differs from the upstream fork

This tree carries the following changes on top of a stock OpenBSD 8.0 source
tree (`src/sys/net/if_awg.c`, `src/sys/net/if_awg.h`,
`src/sbin/ifconfig/ifconfig.c`, plus new userland tools):

- **Kernel:** awg(4) pseudo-device supporting both legacy and 3.1 protocol
  versions per interface. Junk packet emission (Jc/Jmin/Jmax), prefix
  parameters (S1–S4), header protection (HPK), and cookie/handshake
  obfuscation. KARL (kernel address randomized link) relinking at boot with
  signed kernel.SHA256 manifest.
- **ifconfig(8) patches:** awg-quick integration, awgctl ioctl plumbing,
  interface creation/configuration/teardown, peer management, endpoint
  preparation, and the 3.1 parameter ioctls.
- **Userland tools:** awgctl (privileged management daemon/client), awg-quick
  (configuration parser and interface bring-up/down), gen-config.sh (key
  generation), install.sh (kernel build, paired backup/rollback, KARL relink
  object publishing).
- **Installer:** generation-based paired backup/rollback, unprivileged builder
  (`_awgbuild` user), kernel.SHA256 manifest, relink object publishing with
  size verification, `--check` readiness gate, `--uninstall` to stock baseline.

## What is verified, and where the evidence is

Executed evidence comes from a two-VM OpenBSD↔OpenBSD lab pair (server and
client guests on a private L2 segment, kernel `OpenBSD 8.0 AWG.MP#11`) and,
for the recovery row, from a host-fixture test. The conformance ledger and
the run logs are an external evidence set on branch
`test/network-matrix` at the corrected head `25dc854` — the commit whose ledger
separates per-run image identity from quoted historical identity. That branch
is not published in the repository (the cycle repo and the public origin list
no such ref), so the ledgers cannot be opened from this checkout; the
observations are quoted here from the retained lab capture. The same files at
`e774efb` carry provenance claims this candidate does not stand behind. The
ledger's cell ids (E1, E3, E4, E8) are the citation keys:

| Verified behaviour | Evidence artefact |
|---|---|
| Legacy 2.0 handshake, dual-stack (IPv4 + IPv6) ICMP in both directions, and a 4 MiB bulk transfer whose receiver byte count (4 194 304) and sha256 match the sender's payload, with counters advancing | ledger cells E1/E3 — `reports/w3-dual-v2.md` (ping + underlay capture) and `reports/evidence/control-v3b.log` (transfer), on `test/network-matrix` @ `25dc854` |
| 3.1 with `S3`/`S4` and a shared header-protection key, dual stack, 4 MiB in **each** direction, 15/15 checks pass, handshake observed on both ends | `reports/evidence/awg-dual-v5-31.log` on `test/network-matrix` @ `25dc854` (harness `tools/lab/awg-dual-v5-31.sh` in the same branch, ledger cell E4) |
| Recovery after a failed `up`: the interface is put back to the snapshot taken before the run — the address list (in order), the MTU and the link up/down flag — and the addresses the failed run added are removed. The snapshot does not cover broadcast/destination metadata, routes beyond the host routes the tool installs, IPv6 address flags/lifetimes, or the peer list | `tools/tests/test_awgctl_r9_restore.py` (in this tree; drives the real `awgctl` against host fixtures; R4-08a) |

`make check` (shell syntax, Python compile, offline unit tests, the compiled-C
integration gate plus its fail-closed sensitivity self-check, the C1–C24 oracle
controls and their 26 negative controls, the publish scan, generated-patch
freshness, and the upstream digest-drift gate) passes on the Fedora (Linux)
host this candidate was prepared on, and was re-run at the integrated
`eng/integration-e1` pin `a1393fc` (rc=0, 138 tests, 1 skipped,
`evidence/e2-docs/e2docs-base-make-check.log`). The integration
gate is only counted because it can fail: `make check-cgate` runs it twice,
once clean and once with I2 rewritten by the fixture, and requires the second
run to fail with the changed value in the message. The four `make check-vm`
negative-test gates (smoke, wire, matrix, stress) were last captured on the
`AWG.MP#5` lab build under `docs/evidence/2026-11-07/` and were **not**
re-run on the `AWG.MP#11` build in this pass — treat those captures as the
behaviour of that build, not as a fresh gate for this candidate.

## Known risks and untested areas

This section is specific and unsentimental. Each item states what was actually
run and what was not.

| Area | Status | Evidence | What would prove it |
|---|---|---|---|
| **Native kernel build/install/boot on OpenBSD 8.0** | untested — blocked behind VM-lab chain | E1 integration `a1393fc` lists as not tested; prior clean build booted `AWG.MP#0`/`#11` in lab, not this pin | A native install of the final pin in the disposable VM lab, booting and running the suite |
| **Native ioctl ABI, pledge and unveil behaviour of the final pin** | untested | Linux `-DAWGCTL_LINUX` fixture builds only; `docs/SECURITY-AUDIT.md` AWG-038 records the pledge fix on an earlier kernel | Running `awgctl`/`ifconfig` against the installed final-pin kernel (ABI probe, pledge set, unveil walk) |
| **`make check-vm` negative-test gates (smoke, wire, matrix, stress)** | untested on this candidate — last captured on the `AWG.MP#5` build | `docs/evidence/2026-11-07/` | Re-running the four gates on the final-pin kernel |
| **Legacy + 3.1 interoperability (amneziawg-go)** | untested for this candidate (a historical narrow maintainer-run Go pass is on record; not re-run at this pin) | `docs/ENGINEERING-REPORT.md` §11.3/§11.4 narrow PASS — `amneziawg-go` @ `b5928ef`, legacy and 3.1 (S3/S4+HPK), ICMP both ways, 8 MiB each direction with matching sha256; not final-pin certification. The `test/network-matrix` @ `25dc854` ledgers record the OpenBSD↔OpenBSD lab pair at 4 MiB per direction — not Go evidence | Re-running the pinned Go endpoint against the final pin |
| **Hostname endpoint lookup follows first tunnel Address family** | untested | Base `v0.2.1-rc1` signoff limitation list in `a1393fc:docs/RELEASE-NOTES.md` lines 64/67; `awgctl` reads the kernel-resolved `awgendpoint` back from `ifconfig` (README awg-quick internals) | A lab run pairing a hostname endpoint with an opposite-family tunnel Address, verifying the resolved endpoint and traffic |
| **Fixture-bounded checks (compiled-C integration gate, C1–C24 oracle controls with 26 negative controls, publish scan) are bounded host-model checks** | tested (host fixture, bounded) | `tools/lab/gate-controls.py` and `tools/lab/oracle-controls.sh` in this tree; `evidence/e2-docs/e2docs-base-make-check.log` (138 tests, 1 skipped); the control campaign alone does not enforce an exact nonzero `make` exit code | A coverage-guided corpus and native-suite run beyond the bounded fixture campaign |
| **Recovery after a failed `up` (bounded snapshot: address list, MTU, link flag)** | tested (host fixture) | `tools/tests/test_awgctl_r9_restore.py` (R4-08a) | A native run exercising the same restore against a live kernel |
| **Peer-list replacement is non-atomic (can destroy previous peers on a failed replacement)** | tested | `docs/SECURITY-AUDIT.md` AWG-020 family (accepted limitation); E2 kernel-junk sweep `083e0d9` | An atomic snapshot/SMR publish of the obfuscation fields, proven on native runs |
| **Kernel + patched `ifconfig` installed and rolled back as a pair** | tested (disposable guest, six scenarios) | `tools/lab/installer-recovery-test.sh`, `tools/tests/test_installer_transaction.py`; E1 merge of installer-baseline `3777792` + installer-txn `b4dc032` | A native install of the final pin with `--rollback` exercised end to end |
| **IPv6 tunnel traffic, roaming, NAT rebinding, rekey timing, loss/reordering, reduced MTU** | untested | `docs/TESTING.md` matrix rows | A lab matrix covering each dimension on the final pin |
| **Soak, leak-freedom, long-run stability** | untested | Bounded host lock/race and recovery campaigns only | A long soak with resource and crash bounds on the native kernel |
| **Bounded config-parser fuzz (awg-quick, awgctl)** | tested (host, bounded) — on branch `e2/config-fuzz-r8` (`8452256`), merged | E2 config-fuzz r8: 5 493 records, 0 crashes, 0 hangs — `reports/t_53072532-e2-config-fuzz-r8.md` | A coverage-guided corpus beyond the bounded campaign |
| **Wire-format conformance vectors (handshake layout, cookie MACs, junk bounds, KDF)** | tested (kernel-free harness) — on branch `e2/vectors-r8` (`7715bc2`), merged | E2 vectors r8: focused suite 21/21 pass — `reports/t_860ebf3f-e2-vectors-r8.md` | The same vectors replayed against a live kernel |
| **`awgctl` CLI contract (whole-chain config trust, interface-name validation)** | tested (host fixture) — on branch `e2/cli-contract-r2` (`ec9469d`), merged | E2 CLI contract r2 — `reports/t_5183ea9a-cli-contract.md` | The contract exercised against the native kernel and `doas` policy matrix |
| **`awgctl show` truthful counters + JSON mode** | tested (host fixture) — on branch `e2/status-json-v2` (`93ca14c`), merged | E2 status-json v2 — `reports/t_61db8be5-status-json-v2.md` | The JSON emitter driven against a live kernel's peer table |
| **`--uninstall` back to stock (`/obsd`, `/bsd`, `/sbin/ifconfig`, pf backup)** | tested (fixture round trip) — on branch `e2/uninstall-v3` (`53d4e2b`), merged | E2 uninstall v3 — `reports/t_637cbb4d-uninstall-v3.md` | A native install→upgrade→uninstall round trip on the final pin |
| **Build-artifact hygiene and ship-list consistency** | tested (repo gate) — the ship-list check is on branch `e2/hygiene-r5` (`89ec8c9`), merged | `make check-scan` publish scan is the in-tree gate (`a1393fc` Makefile); the ship-list consistency check runs `docs/SHIP-LIST.md` with `tools/tests/test_ship_list.py`, which exist only on `e2/hygiene-r5`; the clean-tree proof is the CTO gate `make check-gitclean` 0/2/0 run (`reports/t_e82c4bb9-cto-gate.md`; `check-gitclean` is likewise not a target in `a1393fc`); the r5 final pass (`evidence/e2hygiene/e2hyg-final-pass.log`) records the negative control — deliberate untracked marker ⇒ `check-gitclean` rc=2, tree left dirty (`?? e2hyg-deliberate-dirty`) | Merging `e2/hygiene-r5` and re-running `make check-scan` with the ship-list test at the release pin |
| **Reproducible release archive** | tested (host, byte-identical rebuilds) — on branch `e2/repro-final` (`c903478`, base `0e557e1`), merged | E2 repro final — `reports/t_409cc17f-repro-final.md` (two independent builds, identical sha256 `fb2e612a…`, byte-identical across timezones); the tooling — `tools/create-release-archive.sh`, `tools/verify-release-archive.sh`, `tools/test-reproducible-archive.sh`, `docs/REPRODUCIBLE-RELEASE.md` and the `check-repro` Makefile target — exists on that branch (`a1393fc` Makefile has no `check-repro`) | Merging `e2/repro-final` and re-running `make check-repro` at the release pin, then publishing the archive with its recorded digest |
| **CI workflow guards (pinned actions, timeouts, proven guards)** | tested (host) | E2-CI `a1393fc` — `reports/t_21b86c94-deepseek-r6.md` CI review lane | A CI run on the final pin |
| **Privilege/config-trust gaps G1/G3 (resolv.conf backup order, show GET buffer)** | tested (host fixture); native runtime not tested | E2 priv-remediation `66fffd8` on branch `eng/e2-priv-remediation-r26` (merged; base `a1393fc`) — `evidence/e2-userspace-privilege-config-trust-audit/remediation-report.md` | The remediated paths exercised on a native install |
| **1-vCPU/1 GB throughput baseline (TCP/UDP, latency, loss, CPU, pool/mbuf pressure)** | untested — blocked behind VM-lab chain | Lab pair currently 2 vCPU / 1 GB; 8 MiB nc integrity check is functional, not a benchmark | Repeated measurements on the constrained profile, raw logs retained |
| **Android / Windows / Linux-module clients** | untested (no endpoints in this pass) | `docs/TESTING.md` interoperability table | Acquiring and running real clients against the final pin |
| **OpenBSD 7.9** | not applicable (compile-only historical) | `README.md` requirements row | A 7.9 kernel build + runtime pass, if support is ever claimed |

### CTO-required verbatim risk lines (from userspace audit `a17e27f`)

1. **Native pledge/unveil enforcement and kernel copyinstr/ENOTTY not verified here.**
2. **getline realloc and stdio can retain secret copies despite wiping the live buffer.**
3. **awg-quick shell secret zeroisation deferred.**
4. **Config hooks intentionally execute privileged shell code.**
5. **Kernel i_peers_count versus awgd_size validation deferred.**

### CTO-required verbatim risk lines (from installer evidence `t_0f8ef970`)

6. **`/bsd` is rewritten by KARL on every boot**, so kernel hashes are only comparable within a single boot. Three boots of the same preserved image gave `/bsd` `5824d25e…`, `be3522…`, `031576…`; each time `/var/db/kernel.SHA256` matched `/bsd` and `install.sh --check` returned `result: ready`. Any transcript that compares `/bsd` hashes across boots is comparing different relinked kernels, not a corruption.

7. **A boot-time `reorder_kernel` failure was observed once with the relink set present** (548 M free under `/usr` against a 677–709 M relink set), while `/bsd` + `kernel.SHA256` stayed a consistent pair and a later boot's `relink.log` shows a successful relink. Cause not established; space pressure is the plausible one. `--skip-relink-objects` exists for hosts that cannot meet the documented ~2.2 GB, and the installer refuses to publish a relink set without its size + 100 MB.

8. Interrupted installer runs leave `/usr/src/.awg-install.*` work directories behind (~697 MB observed) — a cleanup follow-up, not a data-integrity issue.

### Public limitations issue body

The following limitations are carried forward from the v0.2.1-rc1 limitations table and updated for this release. They are the authoritative list for this release.

1. **Not production approval; evaluate only on disposable systems.**
2. **Linux-host checks do not certify OpenBSD ioctl/pledge/unveil, final-pin native build/install/boot, completed native suite.**
3. **Recovery is bounded snapshot; does not cover peer list, routes, IPv6 flags.**
4. **Peer-list replacement is non-atomic.**
5. **Client interoperability (amneziawg-go, Android, Windows, Linux) NOT RUN; Go attempt did not establish handshake.**
6. **No soak, leak-freedom, long-run stability; I1–I5, padding, rekey, roaming, NAT not established.**
7. **Hostname endpoint lookup follows first tunnel Address family.**
8. **Kernel and patched ifconfig must be restored as matched pair.**
9. **Conformance ledger is separate evidence set at test/network-matrix@25dc854.**
10. **Fixture comparisons are bounded host-model checks, not authentication of arbitrary output.**

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
relevant limitation from the table above so the receiver knows what is
expected behaviour, and include `uname -r`/`uname -v`, the kernel `/bsd`
sha256 where recorded, and the command and console output of the failing run.