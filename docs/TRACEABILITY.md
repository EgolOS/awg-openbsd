# Requirement → Test → Evidence Traceability Matrix

**Base commit:** `0e557e1` (eng/integration-accepted)
**Branch:** `e2/trace`
**Generated:** 2026-11-10

---

## Column Definitions

| Column | Meaning |
|--------|---------|
| **Requirement** | Source requirement identifier and description |
| **What Proves It** | Test, gate, or artifact that would demonstrate the requirement |
| **Concrete Evidence** | Test name, report path, commit SHA, or "none" |
| **Gate Status** | `proven` / `fixture-only` / `untested` / `not applicable` |

> **Status definitions (one meaning per status, applied to every row):**
> - **proven** — a check that runs against *this* candidate and passes, and the row cites the artifact: a committed test in `tools/tests/` executed by `make check-tests`, a target in `make check`, or a tracked `docs/evidence/` transcript. Host-level gates count; native OpenBSD behaviour never does.
> - **fixture-only** — the only evidence is a bounded model on this Linux host (PATH-shim harness, compiled-C fixture, host parser adapters, host gate log). It passes, but it cannot certify native OpenBSD behaviour. Each such row names the fixture.
> - **untested** — no requirement-specific passing execution exists for this candidate: evidence absent, prose-only manual observation, a historical run on another pin or branch, a stubbed test, or an outline script. Each such row names the gap and what would prove it. Historical native runs are recorded here, never promoted to `fixture-only`.
> - **not applicable** — a documented limitation or accepted risk, not a capability claim.
>
> `make check-matrix` enforces these rules mechanically: unique and complete IDs,
> required columns, the status vocabulary, evidence/status agreement, AWG
> cross-reference agreement and the Summary Counts block.
> It also rejects a `FILE:LINE` anchor that is out of range or that lands on a
> line sharing too few of the row's own subject words, and rejects an Evidence
> Index row whose commit does not actually contain the artifact.
>
> `make check-citations` reconciles every commit, test, report and evidence path
> cited here with this tree. A hash that does not resolve passes only as a
> declared external reference in `docs/EXTERNAL-REFS.md`, is printed as
> `external (unverifiable here)` — never as evidence — and `docs/` may not cite an
> out-of-tree workspace commit at all.
>
> **Reviewer check in under a minute:** pick a row, open the cited path or line
> (or run the cited target), and compare it with the status. `proven` rows cite a
> test or gate that `make check` runs in this tree; `fixture-only` rows name the
> host-level fixture; `untested` rows name the gap and what would prove it.

---

## Source Inventory

| Source | Requirement IDs | Rows |
|--------|-----------------|------|
| Master directive acceptance gates | G-1 … G-14 | 14 |
| RELEASE-NOTES.md limitations | L-1 … L-10 | 10 |
| Userspace domain | U-1 … U-11 | 11 |
| Installer domain | I-1 … I-10 | 10 |
| Kernel domain | K-1 … K-13 | 13 |
| Native build domain | N-1 … N-6 | 6 |
| Interoperability domain | X-1 … X-6 | 6 |
| Stress domain | S-1 … S-6 | 6 |
| Performance domain | P-1 … P-2 | 2 |
| Packaging domain | PKG-1 … PKG-4 | 4 |
| CI domain | CI-1 … CI-6 | 6 |
| Security register (SECURITY-AUDIT.md) | AWG-001 … AWG-041 | 40 |

> **AWG-036 exclusion:** the register has 41 numeric IDs but only 40 heading
> entries. `AWG-036` appears in SECURITY-AUDIT.md only as an inline mention in the
> AWG-037 section body (`docs/SECURITY-AUDIT.md:511`, "the `AWG-036` family"), so
> `audit_register_ids()` (heading-only parsing) never requires a row for it. The
> 40 rows therefore match the heading set exactly; AWG-036 is not a separate
> requirement entry. Promoting it to a heading makes `make check-matrix` demand a row.

### Master Directive Acceptance Gates (G-1..G-14)
From the master directive (ENGINEERING-REPORT.md §11.4 gate table):
| # | Requirement | What Proves It | Concrete Evidence | Gate Status |
|---|-------------|----------------|-------------------|-------------|
| G-1 | Repository baseline recorded | `git rev-parse HEAD` + `git status` | `0e557e1` base commit; `git cat-file -t 0e557e1` reports commit | proven |
| G-2 | P0 findings remediated and validated | Every P0 item in SECURITY-AUDIT.md carries a state and a proof column | `docs/SECURITY-AUDIT.md` §10 register: 27 fixed, 4 accepted, 1 rejected, 1 verified, 1 info, open items listed | proven |
| G-3 | AWG-014 / AWG-029 fixed and rehearsed | Installer lock + rollback rehearsal transcript | none — `tools/tests/test_installer_transaction.py` and `tools/lab/installer-recovery-test.sh` exist, but no retained lock/rollback rehearsal output for this candidate | untested |
| G-4 | Clean build from verified sources | `install.sh --all` on a disposable guest, build log retained | none — `reports/w1.md` is an awgctl lab report, not a build transcript; an `install.sh --all` build log with exit status is needed | untested |
| G-5 | Kernel boots, interface create/configure/teardown | `awg0 create` + `awgversion 3.1` after boot, transcript retained | none — no boot transcript for this candidate; `reports/w1.md` does not record these commands | untested |
| G-6 | Legacy + 3.1 interop on claimed endpoints | `make check-interop` on two VMs | historical `docs/evidence/2026-11-07/check-interop-final.log` (added at `7afa788`) records 3.1 one direction only — historical run, not this candidate | untested |
| G-7 | Malformed-input and reconfiguration regressions | `smoke-test.sh` + `stress-test.sh` on a lab VM | historical `docs/evidence/2026-11-07/check-vm-final.log` — historical VM run, not this candidate | untested |
| G-8 | No new panic, leak or crypto regression | `make check-vm` stress gate | historical `docs/evidence/2026-11-07/check-vm-final.log` — historical VM run, not this candidate | untested |
| G-9 | Upgrade/rollback runbook exercised | `install.sh --rollback` rehearsal | `reports/w1-r8-recovery.md` — host PATH-shim fixture (report from a sibling branch); no native run | fixture-only |
| G-10 | Docs match tested code | `make check-scan` + `make check-patch` | `make check` runs both targets; tracked host log `docs/evidence/2026-11-07/make-check.log` ends "repository checks: OK" | proven |
| G-11 | Patch/source integrity/manifest current | `generate-patch.sh --check` in `make check-patch` | `make check-patch` at this candidate: "generated patch: current"; generator recorded at `7cc433b` | proven |
| G-12 | Independent review recorded | Reviewer sign-off in Kanban | none — no durable signoff for this cycle; earlier signoffs are not cited | untested |
| G-13 | Constrained 1-vCPU performance | Not run | not run — no 1-vCPU benchmark exists; P-1 would prove it | untested |
| G-14 | Long soak, bounded fuzz corpus | Not implemented | not implemented — no soak or fuzz corpus; S-4/S-5 would prove it | untested |

### RELEASE-NOTES.md Limitations (L-1..L-10)
| # | Requirement | What Proves It | Concrete Evidence | Gate Status |
|---|-------------|----------------|-------------------|-------------|
| L-1 | Not production approval; evaluate only on disposable systems | Documented limitation | RELEASE-NOTES.md:58 — limitation, no capability claim | not applicable |
| L-2 | Linux-host checks do not certify OpenBSD ioctl/pledge/unveil, final-pin native build/install/boot, completed native suite | Documented limitation | RELEASE-NOTES.md:59 — limitation, no capability claim | not applicable |
| L-3 | Recovery is bounded snapshot; does not cover peer list, routes, IPv6 flags | Documented limitation | RELEASE-NOTES.md:60 — limitation, no capability claim | not applicable |
| L-4 | Peer-list replacement is non-atomic | Documented limitation | RELEASE-NOTES.md:61 — limitation, no capability claim | not applicable |
| L-5 | Client interoperability (amneziawg-go, Android, Windows, Linux) NOT RUN; Go attempt did not establish handshake | Documented limitation | RELEASE-NOTES.md:62 — limitation, no capability claim | not applicable |
| L-6 | No soak, leak-freedom, long-run stability; I1–I5, padding, rekey, roaming, NAT not established | Documented limitation | RELEASE-NOTES.md:63 — limitation, no capability claim | not applicable |
| L-7 | Hostname endpoint lookup follows first tunnel Address family | Documented limitation | RELEASE-NOTES.md:64 — limitation, no capability claim | not applicable |
| L-8 | Kernel and patched ifconfig must be restored as matched pair | Documented limitation | RELEASE-NOTES.md:65 — limitation, no capability claim | not applicable |
| L-9 | Conformance ledger is separate evidence set at test/network-matrix@25dc854 | Documented limitation | RELEASE-NOTES.md:66 — limitation, separate evidence set | not applicable |
| L-10 | Fixture comparisons are bounded host-model checks, not authentication of arbitrary output | Documented limitation | RELEASE-NOTES.md:67 — limitation, no capability claim | not applicable |

---

## 1. Userspace (awg-quick, awgctl, gen-config, config parser)

| # | Requirement | What Proves It | Concrete Evidence | Gate Status |
|---|-------------|----------------|-------------------|-------------|
| U-1 | Private key/PSK/HPK never on argv or in stderr (AWG-005) | `test_all_secret_keys_use_stdin_in_argument_order` | `tools/tests/test_quick_parser.py::QuickParser.test_all_secret_keys_use_stdin_in_argument_order` — host parser adapter fixture (production parser text + bash `ifconfig` stub), no shell/OpenBSD execution | fixture-only |
| U-2 | Config file/directory mode enforcement (AWG-010) | `awg-quick` refuses non-0600/0400; installer chmods dir to 0700 | none — prose-only manual observation in ENGINEERING-REPORT.md §4 (run-1 VM); an automated refusal test with retained output is needed | untested |
| U-3 | IPv6 `::/0` without IPv6 address fails closed (AWG-011) | `awg-quick up` exits non-zero and tears the interface down | none — prose-only manual observation in ENGINEERING-REPORT.md §4; an automated case in `tools/tests/` is needed | untested |
| U-4 | Only trusted root-owned config path accepted — owner refusal exercised; arbitrary-path canonicalization/ancestry untested (AWG-013/025) | `trusted_config_path()` refuses a user-owned config; canonicalization is not exercised | `tools/tests/test_quick_parser.py::QuickParser.test_user_owned_config_is_refused` — host parser adapter fixture that mocks `stat` ownership (`MOCK_OWNER`), so ownership is emulated, not observed; the arbitrary-path half has no test ("none — a canonicalization/ancestry test on a real path would prove it") | fixture-only |
| U-5 | Glob/split expansion of Address/DNS/AllowedIPs suppressed (AWG-025) | `set -f` scoped to parsing; quoted arrays; wildcard Address literal preserved | `tools/tests/test_quick_parser.py::QuickParser.test_lists_do_not_expand_filenames` — host parser adapter fixture, no shell/OpenBSD execution | fixture-only |
| U-6 | Generator defaults to 0600 pair file; `--stdout` explicit for plaintext (AWG-026) | `gen-config.sh` creates `pair.conf` under umask 077; `--stdout` exports keys | `tools/tests/test_gen_config.py` — runs in `make check-tests` at this candidate | proven |
| U-7 | Config hooks (`PreUp`/`PostDown` etc.) documented as root programs (AWG-004) | `SECURITY.md` section "Userspace: config files are root programs" | SECURITY-AUDIT.md AWG-004 — accepted risk, no test expected | not applicable |
| U-8 | `awgctl` pledges `stdio rpath inet route tty dns proc exec` and works (AWG-038/040) | `tools/lab/awgctl-test.sh` show/up/add/del against a running kernel | historical `docs/evidence/2026-11-08/w1-pledge-hunk.log` (added at `ec16a75`; AWG.MP#2, not final pin) — historical native run, not this candidate | untested |
| U-9 | `awgctl up` opens config before `unveil`; ancestor walk fixed (AWG-040) | `tools/lab/awgctl-test.sh` case `up --config` + refusal case | historical `docs/evidence/2026-11-08/w1-pledge-hunk.log` (added at `ec16a75`; AWG.MP#2, not final pin) — historical native run, not this candidate | untested |
| U-10 | Publish scan detects WireGuard keys and quoted filenames (AWG-012) | `tools/publish-scan.py --self-test` (fixture-driven) | `make check-scan` at this candidate runs the fixture-driven `--self-test`, then a clean scan | fixture-only |
| U-11 | Legacy numeric config arithmetic validation (AWG-003) | All numeric fields through `need_uint`/`need_range` before arithmetic | none — prose-only manual observation in ENGINEERING-REPORT.md §4; parser rejection tests are needed | untested |

---

## 2. Installer (install.sh, build/install separation, rollback)

| # | Requirement | What Proves It | Concrete Evidence | Gate Status |
|---|-------------|----------------|-------------------|-------------|
| I-1 | Source archives verified with signify before extraction (AWG-006) | `tools/verify-release.sh` selects one named archive and verifies it with the release key | none — no execution evidence in this tree; `make check-scan` runs publish-scan, not source verification. `tools/verify-release.sh` run against a signed archive and a corrupted copy (both exits retained) would prove it | untested |
| I-2 | Build runs as unprivileged user (`_awgbuild`); root only fetches/verifies/installs (AWG-014) | `resolve_build_user`, `tree_trust`, `builder_make` via `doas -u` | `tools/tests/test_installer_transaction.py` (17 tests) run verbatim under the host PATH-shim harness; no native execution | fixture-only |
| I-3 | Group-writable source tree refused with path/owner/mode (AWG-014) | `tree_trust` walks components after symlink resolution | none — `tools/lab/installer-recovery-test.sh` scenario e exists but no retained output for this candidate | untested |
| I-4 | Kernel build exit status not masked by `tee` (AWG-031) | `if ! make ...` checks status; logs last 20 lines on failure | none — prose-only manual MAKEFLAGS observation; an automated failing-build case is needed | untested |
| I-5 | Kernel artifact verified before install (ELF, size, `awg_encap` symbol) | `verify_kernel_artifact()` on every `--build-only`/`--kernel-only` | none — `tools/tests/test_installer_transaction.py` stubs `verify_kernel_artifact`, so no ELF/size/symbol exercise exists; a fixture artifact that fails each check would prove it | untested |
| I-6 | `/sbin/ifconfig` backed up before overwrite; paired rollback restores both (AWG-029) | `write_backup_pair` manifest + copies; `--rollback` verifies both | none — `tools/lab/installer-recovery-test.sh` has 6 scenarios (a–f) but no retained output for this candidate | untested |
| I-7 | `--check` reports pair against manifest and `/var/db/kernel.SHA256` | `record_installed_pair` + `--check` output | `reports/w1-r8-recovery.md` — host PATH-shim fixture transcript, no native run | fixture-only |
| I-8 | Stale `/usr/src/sbin/ifconfig` rejected; exact patch apply verified (AWG-023) | `verify_ifconfig_source()` hashes the pristine file; `patch -C -F 0` dry-run | `tools/tests/test_installer_source.py` — runs in `make check-tests` at this candidate; generator recorded at `7cc433b` | proven |
| I-9 | No host lock: concurrent `install.sh` can truncate `/bsd` (AWG-041) | Observed: a second run zeroed `/bsd` within 2 minutes | open blocker — manifest `installed_bsd_sha256=8770841a…` versus a 0-byte `/bsd`; no fix or regression test | untested |
| I-10 | `doas` rule for build user documented/handled (AWG-039) | Proposed: `su -m` when root, fallback to `doas -u`, print the required rule | none — not implemented; fails closed on stock `doas.conf` | untested |

---

## 3. Kernel (awg(4) driver, handshake, data path, ioctl)

| # | Requirement | What Proves It | Concrete Evidence | Gate Status |
|---|-------------|----------------|-------------------|-------------|
| K-1 | Jumbo MTU + S4 + padding ≤ MAXMCLBYTES (64 KiB); drop not panic (AWG-002) | `awg_encap()` drops + logs; `smoke-test.sh` §2 `awgs1 65535 rejected` | historical `docs/evidence/2026-11-07/check-vm-final.log` from the old `AWG.MP#5` kernel — historical VM run, not this candidate | untested |
| K-2 | DisableCookies admission cap: 100 handshakes/sec, burst 200 (AWG-007) | `tools/lab/awg007-admission.sh` floods 20k datagrams, counts drops | historical `docs/evidence/2026-11-08/awg007-admission-run.log` (disposable lab, earlier pin); re-run on the final pin would prove it | untested |
| K-3 | Timing cross-check uses merged ranges, lower bounds (AWG-008) | `reject.lo > keepalive.lo + rekey.lo`; `smoke-test.sh` §5 | historical `docs/evidence/2026-11-07/check-vm-final.log` — historical VM run, not this candidate | untested |
| K-4 | `awg_index_drop()` NULL check; double drop is no-op (AWG-019) | Kernel no longer panics on double drop | none — code inspection at `src/if_awg.c:2707-2718`; no automated test | untested |
| K-5 | `awg_ispec_parse()` error path frees allocations (AWG-022) | Single `bad:` exit frees `pkt`/`mods` | none — path unreachable with the current grammar; no automated test | untested |
| K-6 | Jmin validated: `EINVAL` on `jmin==0`, `jmin>jmax`, `jmin>1500` (AWG-016) | Range checks in `src/if_awg.c:3075-3080` | historical `check-vm-final.log` smoke §2 covered `awgjc`/`awgjmax`; no `jmin>jmax` case — historical VM run, not this candidate | untested |
| K-7 | Receive-rekey margin widened to uint32_t (AWG-017) | `src/if_awg.c:1147-1151` passes to a uint32_t parameter | historical `check-vm-final.log` smoke §5 — historical VM run, not this candidate | untested |
| K-8 | `awg_reset_3_1()` no longer frees uninitialised stack array (AWG-030) | Ownership transfer; caller zeroes `dropped` first | historical `stress-test.sh` §1 and `matrix-test.sh` §7 runs — historical VM run, not this candidate | untested |
| K-9 | `awgversion legacy` with `awgiN` uses scratch array (AWG-028) | Reset path gets its own scratch; the old I-string is not resurrected | historical `check-vm-final.log` smoke §2 `3.1-only rejected on legacy` — historical VM run, not this candidate | untested |
| K-10 | `addr_bytes` in `struct awg_aip_io` is `uint8_t[16]` (AWG-037) | One-line fix; changes ioctl ABI, requires paired rebuild | none — `src/if_awg.h:57-64`; fix deferred, no regression test | untested |
| K-11 | Lock-free obfuscation fields: torn 32-bit range, half-old HPK accepted (AWG-020) | Documented limitation; SMR snapshot struct needed | SECURITY-AUDIT.md quantification — accepted limitation, no capability claim | not applicable |
| K-12 | Padding not accounted against `if_mtu`; doc says lower MTU by S4 (AWG-018) | Documented limitation; fragmentation observed manually | SECURITY-AUDIT.md AWG-018 / README guidance — accepted limitation | not applicable |
| K-13 | `awg_last_underload` per-interface (AWG-015) | Field moved into `struct awg_softc` | none — needs two interfaces + flood; no automated test | untested |

---

## 4. Native Build & Boot (OpenBSD kernel, ifconfig, full cycle)

| # | Requirement | What Proves It | Concrete Evidence | Gate Status |
|---|-------------|----------------|-------------------|-------------|
| N-1 | Clean kernel build from verified 8.0 sources as unprivileged user | `config AWG.MP` + `make -j2` as `awgbuild`; artifact 33.5 MB, 138 `awg*` symbols | none — synthetic logs were removed; the historical run was on the existing `AWG.MP#5` kernel, not a fresh build | untested |
| N-2 | Built kernel boots (`AWG.MP#0` → `#11`/`#12`) | `install.sh --all` on a disposable guest; reboot → `uname -r`/`uname -v` | historical `reports/w1.md` (2nd pass) and `docs/ENGINEERING-REPORT.md` §11.2 — historical native run, not this candidate | untested |
| N-3 | Patched ifconfig compiled natively, create/set/get/reject-bound/destroy works | `ifconfig-abi-native.log` on the existing `#5` kernel | historical `docs/evidence/2026-11-07/ifconfig-abi-native.log` — historical native run, not this candidate | untested |
| N-4 | Full `install.sh --all` end-to-end: build, pair backup, install, reboot, `awg0 create` + `awgversion 3.1` | Real run on an 8.0 disposable guest | historical `docs/ENGINEERING-REPORT.md` §11.2 row "Full install.sh --all end to end" — historical native run, not this candidate | untested |
| N-5 | Kernel/ioctl ABI mismatch matrix beyond size probe | `tools/lab/abi-probe.sh` (size probe to 1 byte, zeroed struct, SIZE_MAX) | none — `abi-probe.sh` is an outline that echoes labels and performs no ioctl | untested |
| N-6 | `kern_pledge.c` hunk applied: `SIOCSAWG`/`SIOCGAWG` in `PLEDGE_INET` group | `generate-patch.sh` generates the hunk; `install.sh:install_pledge_hunk` applies it | historical `docs/evidence/2026-11-08/w1-pledge-hunk.log` (added at `ec16a75`; AWG.MP#2, not final pin) — historical native run, not this candidate | untested |

> **Note:** N-1 through N-6 have no execution evidence for this candidate. RELEASE-NOTES.md:13 and the limitation bullets at 59, 62, 63, 66 disclaim final-pin native build/install/boot, the completed native suite and candidate Go interoperability. The retained historical runs are recorded above as `untested` with their provenance.

---

## 5. Interoperability (OpenBSD ↔ OpenBSD, official clients)

| # | Requirement | What Proves It | Concrete Evidence | Gate Status |
|---|-------------|----------------|-------------------|-------------|
| X-1 | OpenBSD↔OpenBSD legacy handshake, dual-stack ICMP, 4 MiB bulk transfer (hash-verified) | `test/network-matrix` @ `25dc854` ledger cells E1/E3 | historical `reports/w3-dual-v2.md` and `reports/evidence/control-v3b.log` on the separate `test/network-matrix` branch — historical, not this candidate | untested |
| X-2 | OpenBSD↔OpenBSD 3.1 with S3/S4 + HPK, dual-stack, 4 MiB each direction, 15/15 checks | `test/network-matrix` @ `25dc854` ledger cell E4 | historical `reports/evidence/awg-dual-v5-31.log` and `tools/lab/awg-dual-v5-31.sh` on `test/network-matrix` — historical, not this candidate | untested |
| X-3 | OpenBSD↔OpenBSD two VMs: legacy + 3.1 (S3/S4+HPK), handshake, ICMP, 8 MiB, sha256 | `make check-interop` on two VMs (A↔B) | historical `docs/evidence/2026-11-07/check-interop-final.log` (added at `7afa788`) records a 3.1 setup and one bulk transfer only — historical, not this candidate | untested |
| X-4 | OpenBSD↔official `amneziawg-go` @ `b5928ef`: legacy + 3.1 (S3/S4+HPK), both initiators, 8 MiB each way | Maintainer-run interop; keepalive placement exercises both directions | none — RELEASE-NOTES.md:62 states the Go attempt did not establish a handshake in either profile | untested |
| X-5 | OpenBSD↔Linux kernel module / Android / Windows | Not run in this pass | not run — explicitly NOT RUN in RELEASE-NOTES.md and TESTING.md | untested |
| X-6 | IPv6 tunnel traffic, roaming, rekey, NAT, loss/reordering, MTU matrices | Not run | not run — explicitly NOT RUN in ENGINEERING-REPORT.md §5, §11.4 | untested |

> **Note:** X-1..X-3 cite a separate branch or a historical run; none is evidence about this candidate, so none is `fixture-only` (reserved for host-level fixtures).

---

## 6. Stress / Concurrency / Churn

| # | Requirement | What Proves It | Concrete Evidence | Gate Status |
|---|-------------|----------------|-------------------|-------------|
| S-1 | 200 version switches (3.1 ↔ legacy) with peer present | `stress-test.sh` §1 | historical `check-vm-final.log` stress gate — historical VM run, not this candidate | untested |
| S-2 | Parameter churn + peer add/remove while traffic flows | `stress-test.sh` (main body) | historical `check-vm-final.log` stress gate — historical VM run, not this candidate | untested |
| S-3 | Random loopback datagrams during churn | `stress-test.sh` | historical `check-vm-final.log` stress gate — historical VM run, not this candidate | untested |
| S-4 | Bounded authenticated traffic soak (hour-scale rekey/flood) | Not implemented | not run — explicitly NOT RUN in SECURITY-AUDIT.md §22, ENGINEERING-REPORT.md §7 | untested |
| S-5 | Memory growth / leak freedom over time | Not measured | not measured — no soak or fuzz corpus; ENGINEERING-REPORT.md §7 | untested |
| S-6 | Flood cookie-less handshake admission cap holds (AWG-007) | `tools/lab/awg007-admission.sh` 20k datagrams @ ~1000/s | historical `docs/evidence/2026-11-08/awg007-admission-run.log` (disposable lab, earlier pin); re-run on the final pin would prove it | untested |

---

## 7. Performance

| # | Requirement | What Proves It | Concrete Evidence | Gate Status |
|---|-------------|----------------|-------------------|-------------|
| P-1 | Constrained 1-vCPU / 1-GB reproducible baseline (iperf3, TCP/UDP distributions, CPU, latency/jitter, rekey cost, mbuf/pool growth) | Not run | not run — explicitly NOT RUN in RELEASE-NOTES.md, ENGINEERING-REPORT.md §7, §11.4 | untested |
| P-2 | Stock wg(4) comparison on the same hardware | Not run | not run — no benchmark evidence exists | untested |

---

## 8. Packaging / Release Artifacts

| # | Requirement | What Proves It | Concrete Evidence | Gate Status |
|---|-------------|----------------|-------------------|-------------|
| PKG-1 | Generated patch (`awg-openbsd.patch`) current and matches tracked source | `generate-patch.sh --check` in `make check-patch` | `make check-patch` at this candidate: "generated patch: current"; generator recorded at `7cc433b` | proven |
| PKG-2 | Release manifest: kernel sha256, `uname -r`/`-v`, commit, test results | `make manifest KERNEL=... RELEASE=8.0 COMMIT=...` | none — `tools/release-manifest.py` exists but was not executed for this candidate | untested |
| PKG-3 | Signify verification of selected source archive propagated to CI | `tools/verify-release.sh` selects one archive, uses the release key, propagates exit status | valid/corrupt archive fixtures exercise the selector; generator recorded at `7cc433b`; no CI step (kernel step removed) | fixture-only |
| PKG-4 | No privileged PR CI reaching a persistent runner (AWG-032) | Self-hosted job removed; PRs run hosted checks only | `docs/evidence/2026-11-07/github-runners.json` (zero runners) + `github-workflow-permissions.json`; workflow recorded at `7cc433b` | proven |

---

## 9. CI / Repository Gates

| # | Requirement | What Proves It | Concrete Evidence | Gate Status |
|---|-------------|----------------|-------------------|-------------|
| CI-1 | `make check` passes: syntax, Python compile, unit tests, publish scan, patch freshness | `make check` on the Fedora host | `docs/evidence/2026-11-07/make-check.log` — host-level gate log (24 tests at that pin, scan clean, patch current); host-level, no compiled-C/oracle execution recorded | fixture-only |
| CI-2 | Compiled-C integration gate fails when I2 is rewritten by the fixture (sensitivity self-check) | `make check-cgate` runs twice: clean PASS, fixture FAIL | `tools/lab/awgctl-gate-selftest.sh` via `make check-cgate` — compiled-C fixture on the host | fixture-only |
| CI-3 | Oracle controls (C1–C24) pin canonical-comparison semantics | `tools/lab/oracle-controls.sh` + `gate-controls.py` | `make check-oracle` (part of `make check`) — host-level gate controls | fixture-only |
| CI-4 | Negative control campaign: 1 green + 26 negative cases each returning nonzero | `python3 tools/lab/gate-controls.py` | `make check-oracle` — host-level gate controls | fixture-only |
| CI-5 | Sync-check exits non-zero when upstream added/removed functions (AWG-024) | `tools/sync-check.py` exit 1 on any action needed | none — manual exit-code check only, no automated test | untested |
| CI-6 | Upstream diff baseline report against stock OpenBSD sys/ tree | `make baseline TREE=/usr/src/sys` | none — `tools/upstream-diff.py` exists but was not re-run for this candidate | untested |

> **Note:** CI-1..CI-4 are host-level gates. They are green on the host and are not native OpenBSD evidence.

---

## 10. Security Register (AWG-0xx) — Cross-Reference with Evidence

| AWG-ID | Severity | State | Requirement | What Proves It | Concrete Evidence | Gate Status |
|--------|----------|-------|-------------|----------------|-------------------|-------------|
| AWG-001 | high | rejected | — (R-1 in SECURITY-AUDIT.md) | `run-1/agents/h1b-datapath/findings.md` (F-A1) | register F-A1 — rejected finding, no capability claim | not applicable |
| AWG-002 | high | fixed | K-1 | `smoke-test.sh` §2; F-A2 | historical `check-vm-final.log` — historical native run, not this candidate | untested |
| AWG-003 | high | fixed | U-11 | Manual test; F-C1 | none — prose-only manual observation, no automated case | untested |
| AWG-004 | high | accepted | U-7 | SECURITY.md section; F-C2 | SECURITY-AUDIT.md AWG-004 — accepted risk | not applicable |
| AWG-005 | high | fixed | U-1 | `tools/tests/test_quick_parser.py::test_all_secret_keys_use_stdin_in_argument_order` | host parser adapter fixture (bash `ifconfig` stub), no shell/OpenBSD execution | fixture-only |
| AWG-006 | high | fixed | I-1 | `tools/verify-release.sh`; F-C3 | none — no execution evidence; `make check-scan` runs publish-scan only | untested |
| AWG-007 | medium | fixed | K-2, S-6 | `tools/lab/awg007-admission.sh`; `9486a4f` | historical `awg007-admission-run.log` (earlier pin); re-run on the final pin would prove it | untested |
| AWG-008 | medium | fixed | K-3 | `smoke-test.sh` §5; F-B1, F-B2 | historical `check-vm-final.log` — historical native run, not this candidate | untested |
| AWG-009 | medium | fixed | — (mktemp fix; no automated test) | F-C5 | none — no automated test | untested |
| AWG-010 | medium | fixed | U-2 | Manual test; F-C6 | none — prose-only manual observation | untested |
| AWG-011 | medium | fixed | U-3 | Manual test; F-C7 | none — prose-only manual observation | untested |
| AWG-012 | medium | fixed | U-10 | `tools/publish-scan.py --self-test`; F-C8, F-C9 | fixture-driven self-test in `make check-scan` at this candidate | fixture-only |
| AWG-013 | medium | fixed | U-4 | `tools/tests/test_quick_parser.py::test_user_owned_config_is_refused`; F-C10 | host parser adapter fixture with mocked `stat` ownership; arbitrary-path canonicalization untested | fixture-only |
| AWG-014 | medium | fixed | I-2, I-3 | `tools/tests/test_installer_transaction.py`; `installer-recovery-test.sh` scenario e | none for scenario e — host PATH-shim tests exist but no retained output; not native | untested |
| AWG-015 | low | fixed | K-13 | Code inspection; F-K2 | none — needs two interfaces + flood; no automated test | untested |
| AWG-016 | low | fixed | K-6 | `smoke-test.sh` §2; F-K3 | historical `check-vm-final.log` — historical native run, not this candidate | untested |
| AWG-017 | low | fixed | K-7 | `smoke-test.sh` §5; F-K4 | historical `check-vm-final.log` — historical native run, not this candidate | untested |
| AWG-018 | low | accepted | K-12 | README guidance; F-K5 | SECURITY-AUDIT.md AWG-018 — accepted limitation | not applicable |
| AWG-019 | low | fixed | K-4 | Code inspection; F-K6 | none — code inspection only, no automated test | untested |
| AWG-020 | low | accepted | K-11 | SECURITY-AUDIT.md quantification | SECURITY-AUDIT.md quantification — accepted limitation | not applicable |
| AWG-021 | low | accepted | — (bounded, matches Linux) | F-A4 | register F-A4 — accepted, no capability claim | not applicable |
| AWG-022 | low | fixed | K-5 | Code inspection; F-A5 | none — code inspection only, path unreachable | untested |
| AWG-023 | low | fixed | I-8 | `tools/tests/test_installer_source.py`; F-C12 | test runs in `make check-tests` at this candidate | proven |
| AWG-024 | low | fixed | CI-5 | `tools/sync-check.py`; F-C13 | none — manual exit-code check only | untested |
| AWG-025 | low | fixed | U-5 | `tools/tests/test_quick_parser.py::test_lists_do_not_expand_filenames`; F-C14 | host parser adapter fixture; no shell/OpenBSD execution | fixture-only |
| AWG-026 | low | fixed | U-6 | `tools/tests/test_gen_config.py`; F-C15 | test runs in `make check-tests` at this candidate | proven |
| AWG-027 | info | accepted | — (verified, no change) | F-A6 | register F-A6 — verified, no change required | not applicable |
| AWG-028 | info | fixed | K-9 | `smoke-test.sh` §2; F-K7 | historical `check-vm-final.log` — historical native run, not this candidate | untested |
| AWG-029 | info | fixed | I-6, I-7 | `installer-recovery-test.sh` 6 scenarios; `tools/tests/test_installer_transaction.py` | none for I-6 — host PATH-shim fixtures cover I-7, no retained scenario output | untested |
| AWG-030 | medium | fixed | K-8 | `stress-test.sh` §1; `matrix-test.sh` §7; panic dmesg | historical VM runs — historical native run, not this candidate | untested |
| AWG-031 | medium | fixed | I-4 | Manual MAKEFLAGS test; F-C16 | none — prose-only manual observation | untested |
| AWG-032 | high | fixed | PKG-4 | GitHub API zero runners | `docs/evidence/2026-11-07/github-runners.json` + workflow permissions snapshot | proven |
| AWG-033 | low | fixed | PKG-3 | `tools/verify-release.sh`; valid/corrupt fixtures | valid/corrupt archive fixtures exercise the selector; no CI step | fixture-only |
| AWG-034 | medium | fixed | X-3 (interop-test.sh fix) | `7a25f68`; sender/receiver integrity | historical interop log, one direction only — not this candidate | untested |
| AWG-035 | low | fixed | I-8 (manifest corrected) | `7cc433b`; signed archive digests | `tools/tests/test_installer_source.py` runs in `make check-tests`; digests recorded at `7cc433b` | proven |
| AWG-037 | low | open | K-10 | `src/if_awg.h:57-64`; ABI change deferred | none — open, ABI change deferred | untested |
| AWG-038 | medium | fixed | U-8, N-6 | `awgctl-test.sh`; `w1-pledge-hunk.log` (added at `ec16a75`) | historical pledge/unveil run, not final pin | untested |
| AWG-039 | low | open | I-10 | `doas` rule missing; proposed `su -m` fallback | none — open, not implemented | untested |
| AWG-040 | high | fixed | U-9 | `awgctl-test.sh` case `up --config`; `w1-pledge-hunk.log` | historical pledge/unveil run, not final pin | untested |
| AWG-041 | high | open | I-9 | manifest `installed_bsd_sha256` vs 0-byte `/bsd` | none — open blocker, no fix or regression test | untested |

---

## Summary Counts

| Gate Status | Count |
|-------------|-------|
| proven | 12 |
| fixture-only | 17 |
| untested | 80 |
| not applicable | 19 |
| **Total requirement rows** | **128** |

> **Note:** The 128 requirement rows comprise: 14 G, 10 L, 11 Userspace, 10 Installer, 13 Kernel, 6 Native, 6 Interop, 6 Stress, 2 Performance, 4 Packaging, 6 CI, 40 AWG. Counts are mechanically derived by `tools/check_matrix_totals.py` from the tables above; `make check-matrix` fails if this block is stale.

---

## Evidence Index (for reviewer verification)

| Artifact | Path | Commit |
|----------|------|--------|
| `make-check.log` | `docs/evidence/2026-11-07/make-check.log` | `0e557e1` |
| `check-vm-final.log` | `docs/evidence/2026-11-07/check-vm-final.log` | `0e557e1` |
| `check-interop-final.log` | `docs/evidence/2026-11-07/check-interop-final.log` | `7afa788` |
| `ifconfig-abi-native.log` | `docs/evidence/2026-11-07/ifconfig-abi-native.log` | `0e557e1` |
| `all-keys-native.log` | `docs/evidence/2026-11-07/all-keys-native.log` | `7afa788` |
| `generator-native.log` | `docs/evidence/2026-11-07/generator-native.log` | `7afa788` |
| `w1-pledge-hunk.log` | `docs/evidence/2026-11-08/w1-pledge-hunk.log` | `ec16a75` |
| `awg007-admission-run.log` | `docs/evidence/2026-11-08/awg007-admission-run.log` | `9486a4f` |
| `github-runners.json` | `docs/evidence/2026-11-07/github-runners.json` | `0e557e1` |
| `awg007-admission.sh` | `tools/lab/awg007-admission.sh` | `9486a4f` |
| `test/network-matrix` ledger | `test/network-matrix` @ `25dc854` | (separate branch) |
| `abi-probe.sh` | `tools/lab/abi-probe.sh` | `347b873` (outline only, no ioctl) |

---

## Notes for Reviewers

1. **Every row is checkable in under a minute.** `proven` rows name a test that `make check-tests` runs, a target in `make check`, or a tracked transcript. `fixture-only` rows name the host fixture. `untested` rows name the gap.
2. **Zero unsupported claims.** No capability is claimed without evidence; where there is none the cell starts with `none`.
3. **One meaning per status.** `fixture-only` is host-level evidence only (PATH-shim harness, compiled-C fixture, host parser adapter, host gate log). Historical native VM/branch runs are `untested` for this candidate and say so; they are never promoted to `fixture-only` or `proven`.
4. **No fixture is `proven`.** Every `tools/tests/test_quick_parser.py` row (U-1/AWG-005, U-4/AWG-013, U-5/AWG-025) is a host parser adapter: the production parser text runs under bash with stubbed `ifconfig`/`stat`, so it stays `fixture-only`. The same applies to the compiled-C gate (CI-2), oracle controls (CI-3/CI-4), publish-scan self-test (U-10/AWG-012) and archive fixtures (PKG-3/AWG-033).
5. **AWG mirror rule.** Each AWG row carries the weakest status of the requirement rows it maps to; `make check-matrix` rejects a mismatch.
6. **Open blockers.** AWG-037 (ABI), AWG-039 (doas rule) and AWG-041 (host lock) are open and `untested`.
7. **Counts are enforced.** `make check-matrix` derives the Summary Counts block from the tables and fails when it is stale, when an ID is duplicated or missing, when a required cell is empty, or when the evidence contradicts the status.
