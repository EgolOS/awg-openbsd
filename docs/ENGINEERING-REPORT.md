# AWG-OpenBSD engineering report — 2026-11-07

## Executive verdict: READY FOR A RELEASE CANDIDATE (claims narrowed, see §11)

Second pass, 2026-11-08. The two blockers this report named — AWG-014 (root builds
inside a writable tree) and AWG-029 (no paired rollback) — are fixed and were
rehearsed on disposable OpenBSD 8.0 guests. A clean, fully unprivileged kernel
build from signature-verified 8.0 sources boots and passes the repository gates,
and the tunnel was exercised against the **official amneziawg-go implementation**
in both directions, for the legacy profile and for 3.1 with S3/S4 and header
protection, with hash-verified 8 MiB payloads. Details and the exact evidence are
in §11; the sections above describe the state at `7afa788` and are kept as the
record of that pass.

What is still not verified, and is therefore **not claimed**: Android and Windows
clients, the Linux kernel module, IPv6 tunnel traffic, roaming/rekey-timing/NAT/MTU
matrices, long soak, a bounded fuzz corpus, old/new ioctl ABI matrices beyond a
size probe, and constrained 1-vCPU performance figures. A release taken from this
tree must carry those limitations, not a blanket "tested" statement.

## 1. Baseline inventory

| Item | Verified value |
|---|---|
| Repository | https://github.com/EgolOS/awg-openbsd |
| Initial checkout | main; clean working tree |
| Initial HEAD / latest tag | `2d71d8cefce55680fba4535a13c88aadbb00bf53` / `v0.2.0` (same commit) |
| Remote state | git ls-remote confirmed initial HEAD/tag; no remote write this pass |
| Working branch | `audit/maintenance-assurance` |
| Kernel source delta | **None** to src/ or awg-openbsd.patch in this series |
| OpenBSD baseline | 8.0 amd64, base sys/src archives verified with stock release key |
| Host | Linux 7.2.15-200.fc44.x86_64; AMD Ryzen AI Max+ 395 |
| Tools | Git 2.56.0; Python 3.14.9; Bash 5.3.9; GCC 16.2.1; QEMU 11.0.3 |
| Live lab | two disposable local KVM VMs; OpenBSD 8.0 AWG.MP#5; **2 vCPU / 1 GB each** |
| SSH | loopback 2201/2202, lab user + actual `permit nopass :wheel`; dedicated known_hosts |
| PF | already disabled in both labs; left unchanged |
| Console | A has serial file; B was launched with TCP serial, not continuous file capture |
| GitHub controls observed | zero repository self-hosted runners; default token read; PR review approval false |

Initial relevant commits: `2d71d8c` release metadata, `e5537c2` runner variable gate,
`864a1b4` CI syntax/actions, `7cc9b85` build exit propagation, `7081e82` two-VM lab.
Earlier kernel fixes were not reimplemented. The reset ownership transfer and
zeroed caller array remain at if_awg.c:3152–3192 and :3380–3395; the existing
AWG-030 regression was rerun on #5. VM source digests matched the unchanged driver
files; this does **not** substitute for a new full clean kernel build manifest.

Verified archive SHA256 (release signature checked):

```
sys.tar.gz  964b1aed7839b86f4e010a0fdd03dcd409e6f569e69fe85f95e5fceb740c2aaf
src.tar.gz  9984c74ff875b5ff041e4676a7f3e4e1ead2cc47a284d705eb9f37cbec4fe795
```

The corrected manifest has 11 matching files. Function-level baseline comparison:
if_awg.c = 40 identical / 37 modified / 24 AWG-only; awg_noise.c = 36 / 5 / 4.
No candidate-release delta was run. See retained `upstream-baseline.json`.

## 2. Security register and workstream status

Primary register: [SECURITY-AUDIT.md](SECURITY-AUDIT.md). It preserves AWG-001–031,
adds AWG-032–035, and explicitly distinguishes historical and current evidence.
Current register totals: **35: 26 fixed locally/historically, 2 open, 6 accepted,
1 rejected**. “Fixed locally” does not mean deployed to main or installed remotely.

| WORKSTREAM | OWNER | COMMIT | STATUS | TEST EVIDENCE | REMAINING RISK | NEXT ACTION |
|---|---|---|---|---|---|---|
| CI / signatures AWG-032/033 | Parent | 7cc433b | PASS locally | Real signify fixtures; runner API | Workflow unpushed; no new kernel CI | Review/publish only with authorization |
| Generator AWG-026 | Parent; Qwen-2 draft rejected | 81e1bb8 | PASS | Generator unit/native logs | Shell memory/root tracing outside threat model; IPv4 tunnel generator | Preserve explicit export boundary |
| Config trust/globbing AWG-013/025 | Parent | 6ec75cf | PASS bounded scope | Actual doas/ancestry + parser tests | Hooks intentional root code; full narrow-policy matrix incomplete | Broaden policy/routing tests |
| All secret-key stdin AWG-005 | Parent | be08d4a | PASS | Argument-order unit + native production function | Manual argv forms still expose keys | Prefer automated stdin path |
| Source identity AWG-023/035 | Parent | 7cc433b | PASS gate/build | Stale rejection; patch -C -F 0; unprivileged compile; baseline regression | Writable-tree trust is separate | Resolve AWG-014 |
| Kernel/reset/length review | Qwen-1; parent bounded checks | unchanged 2d71d8c | PASS existing regressions, limited review | Four VM gates; reset source checked | Not an exhaustive lifetime/lock proof | Clean build and stronger authenticated traffic gates |
| Interop integrity AWG-034 | Parent; Qwen-3 source review | 7a25f68 | PASS corrected run | Receiver 8388608 bytes + SHA256 | Only OpenBSD pair, no HPK in profile | Official-client and full-feature matrix |
| Root build trust AWG-014 | Parent | unchanged | BLOCKED release gate | /usr/src root:wsrc 0775; lab-owned source tree | Root make can execute writable source | Isolate verified source; unprivileged build/install separation |
| Paired recovery AWG-029 | Parent | unchanged | BLOCKED release gate | Source inspection; proposed runbook only | ifconfig unbacked; rollback kernel-only | Implement transaction and rehearse rollback/rescue |
| Official protocol clients | Parent / DeepSeek source review | refs below | NOT RUN runtime | Pinned source comparison only | No Go/Linux/mobile/Windows execution | Acquire/build controlled endpoints |
| Soak / coverage fuzz / performance | Qwen-3 analysis, parent | no implementation claimed | NOT RUN | Existing bounded churn only | No long-run resource or benchmark evidence | Implement after preceding gates |

## 3. Implemented patch series

1. `7cc433b` — remove privileged PR CI; propagate signify status; require recorded
   ifconfig identity, exact staged patch; correct userland baseline digest and
   sibling path; optional production HP keystream KAT.
2. `81e1bb8` — exclusive 0600 generator output, explicit --stdout, stdin private
   keys, tracing disabled, malformed endpoint/CIDR rejection, safe ownership of
   helper interface/output, bounded distinct random H values.
3. `6ec75cf` — canonical root-owned non-writable config ancestry, scoped no-glob
   parsing/argv, quoted route enumeration, decimal range comparison.
4. `7a25f68` — integrate offline regressions; explicit disposable-lab guard,
   pinned SSH defaults, native listener syntax, sender-status and receiver
   byte/hash assertions, own-interface cleanup, no PF changes.
5. `be08d4a` — HPK and PSK join private key on ordered stdin; disable awg-quick
   xtrace before reading configuration. Existing ifconfig stdin macro reused.

Subsequent documentation/evidence commit records this report. No source/kernel
patch regeneration was necessary: the generated patch is unchanged and checked.
These commits can be inspected with `git show`; the delivered patch bundle covers
the full series from the initial HEAD. No force-push/rebase or release was done.

## 4. Executed test report

Exported logs are under [evidence/2026-11-07](evidence/2026-11-07/); see its README
for explicit scanner annotations and path redactions. Unannotated originals and
raw independent reviews are retained privately in the adjacent audit directory.
The first full make check rejected harmless `PASS:` fixture messages/home paths;
only reviewed evidence exports were annotated/redacted, not scanner rules changed.

| Command / operation | Environment | Result | Evidence / limit |
|---|---|---|---|
| Initial make check | Linux starting HEAD | PASS | Existing baseline checks, not new tests |
| python3 -m unittest discover -s tools/tests -v, OPENBSD_SYS_TREE set | Linux offline bwrap sandbox | **24/24 PASS** | unit-tests-final.log; includes HP KAT |
| make check (OPENBSD_SYS_TREE set) | Final source tree, sandbox copy byte-checked against tracked files | **PASS** | make-check.log: 24 tests, scan clean, generated patch current |
| AWG_LAB_DISPOSABLE=1 make check-vm LAB_KNOWN_HOSTS=... | OpenBSD A, existing #5 | PASS | check-vm-final.log: smoke, wire, matrix, stress |
| AWG_LAB_DISPOSABLE=1 make check-interop LAB_KNOWN_HOSTS=... | OpenBSD A↔B | PASS corrected run | check-interop-final.log: handshake, ICMP, exact bytes+SHA256, counters |
| Original/new-listener diagnostic runs | Same lab | FAIL / timed out | check-interop.log and check-interop-rerun.log retained, not erased as green |
| Real doas + production parser ancestry checks | OpenBSD A | PASS | native-checks.log; exact executed lab script retained |
| Stale source / exact native patch / unprivileged ifconfig build | OpenBSD A | PASS | native-checks.log; no base-tool replacement |
| Newly compiled ifconfig create/set/get/reject-bound/destroy | Existing #5 kernel | PASS bounded ABI smoke | ifconfig-abi-native.log; not old/new ABI matrix |
| sh -x generator, permissions/logs/IPv6 endpoint/cleanup | OpenBSD A | PASS | generator-native.log; ephemeral generated profile removed |
| Production set_config private/HPK/PSK stdin order under bash -x | OpenBSD A | PASS | all-keys-native.log; no key material retained |
| Full modified installer / clean full kernel build / new boot | Disposable lab | **NOT RUN** | Existing #5 is not a new build |
| Recovery, official clients, long soak/fuzz, performance | — | **NOT RUN** | No inference of green status |

Host unit execution used no-network bwrap namespaces, read-only source/toolchain,
an empty allowlisted environment, scratch HOME/TMPDIR, hidden host home/run/tmp
and an outer wall-clock timeout. The HP harness is suitable userspace C only;
no kernel sanitizer/fuzzer claim. Destructive tests ran only in the identified
throwaway guests. Existing wire/churn tests have limited send/error assertions;
passing them establishes bounded liveness, not memory safety or leak freedom.

Native compiled ifconfig SHA256:
`50885675f643895cab83d449ccfed3de7663723bba68bbd597b6dc6b85fcd2fa`.
It remains a test artifact in the lab, not a distributed/release binary.

## 5. Protocol compatibility matrix

Reference source revisions actually retrieved:

- Official Go: `b5928efb6ca19f0153958460c3d141f04abc5c2e` — amnezia-vpn/amneziawg-go.
- Official Linux: `4569c4c67f3a57414969260cafbbd04694fbaae0` — amneziawg-linux-kernel-module.
- OpenBSD implementation: unchanged driver source at starting `2d71d8c`.

Source presence/semantic comparison is not runtime compatibility. Equal settings
are the conservative matching recommendation; directional acceptance can be broader.

| Feature | Endpoint relationship / evidence | Runtime status this pass |
|---|---|---|
| Plain WireGuard mode | No junk/prefixes; H=1/2/3/4; no 3.1 transforms | Stock wg peer NOT RUN |
| Legacy | S1/S2 and transmitted H types accepted by receiver | Same-host legacy handshake PASS in smoke |
| 3.1 | Extended per-message prefixes/ranges | OpenBSD↔OpenBSD selected profile PASS |
| Junk Jc/Jmin/Jmax | Sender-local; no need to match | Obfuscated handshake profile exercised; burst characterization NOT RUN |
| S1–S4 | Receiver needs sender's per-type prefix lengths | Two-VM S1=26,S2=33,S3=18,S4=24 PASS |
| H1–H4 fixed/ranges | Receiver range must contain transmitted values; equal ranges safest | Bounds/overlap rejection cases PASS; range interop NOT RUN |
| Legacy collisions | First length/type candidate wins; prefixes may equalize lengths | Acceptance preserved; collision/overlap client interop NOT RUN |
| I1–I5 | Sender-local signatures, not matching credentials | Grammar/bounds cases PASS; full wire signature comparison NOT RUN |
| HeaderProtectionKey | Same key and adequate prefixes; nonce first 12 datagram bytes | Round-trip/config + userspace KAT PASS; real HPK tunnel NOT RUN |
| ContentPaddingAddition | Local transmit policy, receiver framing must accept it | Parameter cases only; differential wire behavior NOT RUN |
| RandomTrailers | Receiver must accept sender trailers; both on is conservative | Parameter/config checks only; full trailer interop NOT RUN |
| DisableCookies | Local responder/load policy; DoS trade-off | Source review/parameter coverage; load/cookie exchange NOT RUN |
| Session timing ranges | Local timing with validated ordering, not shared keys | Bounds/merged-config tests; timed rekey behavior NOT RUN |
| PersistentKeepalive ranges | Local peer transmit timing | Parameter handling only; timer distribution NOT RUN |
| Rekey / retries | Keep WireGuard crypto semantics; local timing affects behavior | Sustained/time-driven cases NOT RUN |

| Endpoint pair | Legacy | 3.1 | Exact scope |
|---|---|---|---|
| OpenBSD↔OpenBSD same host | PASS bounded handshake/counters | Config cases only | Local ping is not tunnel data evidence |
| OpenBSD↔OpenBSD two VMs | NOT RUN separate legacy profile | PASS selected non-HPK profile | IPv4 handshake/ICMP/8 MiB receiver integrity; initiated from A only |
| OpenBSD↔official Go | NOT RUN | NOT RUN | Source obtained, executable tunnel not exercised |
| OpenBSD↔official Linux module | NOT RUN | NOT RUN | No Linux module/client VM built |
| OpenBSD↔Android | NOT RUN | NOT RUN | No actual client/version available in this pass |
| OpenBSD↔Windows | NOT RUN | NOT RUN | No actual client/version available in this pass |

IPv6 tunnel traffic, both initiator directions, rekey, roaming, NAT interruption,
loss/reordering/duplication, reduced MTU and full routing-domain/PF matrices remain
NOT RUN. A generator's IPv6 endpoint formatting test is not IPv6 tunnel validation.

## 6. OpenBSD support matrix

| Platform | Compile/build | Boot | ABI/runtime | Support classification |
|---|---|---|---|---|
| 8.0 amd64 base release | Historical full #5; new ifconfig compiled this pass; new full kernel NOT RUN | Existing #5 observed | Four gates + selected two-VM test and new-ifconfig smoke PASS | Current lab baseline; not RC-qualified |
| 7.9 amd64 | Historical driver-only -Werror claim, not re-executed | NOT RUN | NOT RUN | Compile-only historical; no installer manifest/runtime support |
| Errata-modified 8.0 sources | No new baseline reviewed | NOT RUN | NOT RUN | Must review provenance/patch before changing manifest |
| Candidate later release | NOT RUN | NOT RUN | NOT RUN | Unsupported until independent release gates |
| Other architectures | NOT RUN | NOT RUN | NOT RUN | Unsupported |

A common header does not prove all old/new structure layouts are compatible.
The ioctl mismatch matrix remains outstanding; do not assert reliable ABI error
messages or cross-release rollback solely from a successful current-pair smoke.

## 7. Performance and resource report

**No optimization implemented and no reproducible performance baseline measured.**
The successful receiver-integrity run took 25 wall-clock seconds including harness
behavior. It must not be converted into a bandwidth claim. Both VMs have 2 vCPU /
1 GB, not the required constrained 1-vCPU profile. No TCP/UDP distributions, CPU,
latency/jitter, rekey cost, sustained mbuf/pool growth or stock-wg comparison was
collected. Unsupported benchmark/general bottleneck claims were withdrawn from
README. Future benchmarks must pin topology/revisions/resources, separate plain
L2 ceiling and tunnel directions, repeat measurements, and retain raw logs.

## 8. Installation/recovery runbook status

Updated existing `RUNBOOK-sysupgrade.md`, `RUNBOOK-security-updates.md`,
`DEPLOY-vps.md` and README. Removed nonexistent `install.sh build`, unsupported
future-release recipes, unsigned illustrative errata application, and claims that
kernel-only rollback restores the management pair.

The manual same-release backup/paired restore procedure is explicit but
**NOT REHEARSED**. Root build isolation, atomic paired installation and all partial
failure/full-disk/upgrade/rescue cases remain release blockers. No-console or
VPN-only SSH access is a stop condition, not a reason to risk a remote reboot.

## 9. Maintenance, dependencies and independent review

Preserve two upstream obligations: OpenBSD kernel/network/crypto/security deltas
and official AmneziaWG wire semantics/client versions. The existing manifest,
function comparison, sync-check and generated patch remain the small maintenance
architecture; no daemon, dashboard, framework or runtime dependency was added.

Runtime/build inventory: native OpenBSD kernel/base toolchain, patched ifconfig,
Bash for awg-quick; native signify/ftp/tar; existing OpenSSL/randomness for key
creation. Host checks require Python stdlib and Bash; the signature fixture uses
signify (or Ubuntu's signify-openbsd). Optional KAT also uses a C compiler/OpenSSL.
Kernel/LICENSE carry ISC-style notices; awg-quick retains GPL-2.0 SPDX; OpenBSD
ChaCha header retains its public-domain notice. No license homogenization or
unverified SBOM claim. Hosted actions still use major tags and allowed_actions=all
was observed: immutable reviewed action pins remain supply-chain follow-up work.

Workers were isolated in separate worktrees, at most three Halogen requests;
DeepSeek official API calls were serial (one slot). Qwen-1 returned a bounded
kernel review; Qwen-2's generator draft was **not integrated** (parent found unsafe
existing-interface handling and incorrect baseline claims); Qwen-3 provided lab
findings, not an implemented soak/fuzzer. Parent implemented/retested the series.

Official API catalog exposed DeepSeek-V4.1-Flash as `deepseek-flash`; snapshot
`1017` was not exposed, so exact snapshot pinning is **not verified**. First review
exhausted its reasoning budget with empty content and was not counted as a review.
A no-thinking retry and final patch review returned content. Parent rejected the
unsupported nonce/ChaCha/path/stdin allegations against full source and executed
KAT/native tests. Review is recorded, not an independent release certification.
Private raw review material is retained outside the public tree; no secrets sent.

Next milestones, in dependency order:

1. Resolve AWG-014 and AWG-029 without executing root make from writable inputs;
   implement/rehearse clean staging, paired backup/restore and failure recovery.
2. Clean verified kernel+ifconfig build, boot, ABI mismatch and teardown/concurrency
   gates in disposable release-matched VMs, with serial crash capture on both.
3. Execute pinned official Go/Linux endpoints; obtain real mobile/desktop clients.
4. Add narrowly scoped real-parser fuzz harnesses and an optional authenticated
   traffic/reconfiguration soak with resource/crash bounds; no production fuzzing.
5. Run the 1-vCPU/1-GB and larger reproducible baseline distributions.
6. Independent exact-head review; reconcile all release gates, then request human
   publication/deployment approval. Missing gates remain NOT VERIFIED.

## 10. Final engineering verdict

**NOT READY FOR RELEASE CANDIDATE.** The verified local fixes materially improve
secret handling, CI/source gates and test honesty. They do not eliminate the
privileged-build/paired-recovery hazards or supply the unexecuted runtime matrix.
No P1 exception has been silently accepted and no unbuilt/unverified kernel is
included in the deliverables. Remaining work is explicit, reviewable and ordered.

## 11. Second pass — 2026-11-08 (what changed, and the evidence)

### 11.1 Baseline for this pass

Branch `audit/maintenance-assurance`, starting tree `7afa788`. Kernel, crypto and
wire code were **not modified** in either pass. Lab: five disposable KVM guests on
OpenBSD 8.0 amd64; the constraints from §1 still held (no production host, no
push, no reboot of anything outside the guests).

### 11.2 Workstreams and their outcome

| WORKSTREAM | OWNER | STATUS | EVIDENCE | REMAINING RISK |
|---|---|---|---|---|
| Clean build + boot (AWG-014 proof) | delegated worker, re-verified by maintainer | **PASS** | `8.0` `sys`/`src` verified, unpacked; `config AWG.MP` + `make -j2` as `awgbuild`; artifact `7f454c46`, 33 518 410 B, sha256 `c970ab83…`, 138 `awg*` symbols; installed and booted as `AWG.MP#0` then `#11` | builder still compiles what root installs |
| Repository gates on a freshly built kernel | delegated worker | **PASS** | `check-vm`: smoke, wire, matrix, stress — 4/4 gates, `RESULT: all cases passed` | not a lifetime/lock proof |
| Installer: build/install separation | delegated worker + maintainer fixes | **PASS** | `tree_trust` (symlinks resolved), `resolve_build_user`, `builder_make`; group-writable tree refused with path/owner/mode | third-party-trusted builder |
| Installer: paired backup/rollback | delegated worker + maintainer fixes | **PASS** | six rehearsal scenarios (a–f) on a disposable guest; `--check` reports pair, trust, build user | KARL relink handled by design, not by test-only hack |
| Full `install.sh --all` end to end | maintainer | **PASS** | real run on 8.0: unprivileged kernel build, pair backup + manifest (`release_tag=rc-e2e`), install, reboot → `AWG.MP#12`, `awg0 create` + `awgversion 3.1` work, `--check` → `result: ready` | one release/one arch only |
| Official client interop | maintainer (worker ran out of budget) | **PASS, narrow** | `amneziawg-go` @ `b5928ef` ↔ kernel: legacy and 3.1 (S3/S4+HPK), ICMP both ways, 8 MiB each direction with matching sha256 | no Android/Windows/Linux-module run |
| ABI probe | delegated worker | **partly** | ioctl size-probe down to 1 byte, zeroed struct, `SIZE_MAX` — `EINVAL`/no panic | no old/new binary matrix |
| New defect found | delegated worker, verified by maintainer | **open, low** | AWG-037: `addr_bytes` is `uint8_t`, not `uint8_t[16]` | ABI change needs a paired rebuild |

Two claims from the delegated worker did **not** survive checking and are not
repeated here: the `awgpeer` pool "leak" (10 create/destroy cycles leave the count
unchanged, and the 6 items it saw were live interfaces) and the "HAS_3_1 bit range
collision" (`AWG_INTERFACE_HAS_I1` is bit 20 and the group runs to bit 24, so the
mask is consistent).

### 11.3 Interoperability matrix (actual, not inferred)

| Pair | Legacy | 3.1 (S3/S4+HPK) | Notes |
|---|---|---|---|
| OpenBSD ↔ OpenBSD (two guests) | PASS | PASS | handshake, ICMP, 8 MiB with sha256 |
| OpenBSD ↔ official `amneziawg-go` | PASS | PASS | both initiators exercised by keepalive placement; 8 MiB each direction, hashes matched |
| OpenBSD ↔ official Linux module | NOT RUN | NOT RUN | no Linux module guest built |
| OpenBSD ↔ Android / Windows | NOT RUN | NOT RUN | no client available in this pass |

I1–I5 signatures, content-padding addition, random trailers and disable-cookies
were configured in neither direction, so they carry no interop claim.

### 11.4 Revised gate table

| Gate | Status |
|---|---|
| Repository baseline recorded | PASS |
| P0 findings remediated and validated | PASS |
| AWG-014 / AWG-029 | PASS (fixed, rehearsed) |
| Clean build from verified sources | PASS (unprivileged, booted) |
| Kernel boots, interface create/configure/teardown | PASS (`check-vm` on the new kernel) |
| Legacy + 3.1 interop on claimed endpoints | PASS for OpenBSD and official Go; other clients NOT RUN |
| Malformed-input and reconfiguration regressions | PASS (repository wire/matrix/stress suites) |
| No new panic, leak or crypto regression | PASS in the runs above |
| Upgrade/rollback runbook exercised | PASS at file level (six scenarios); rescue mode NOT RUN |
| Docs match tested code | PASS after this pass |
| Patch/source integrity/manifest current | PASS (`make check`, generated patch current) |
| Independent review recorded | PASS (official API review, adjudicated; its nonce/ChaCha/path claims were refuted with source and a keystream KAT) |
| Constrained 1-vCPU performance | NOT RUN |
| Long soak, bounded fuzz corpus | NOT RUN (a 2000-datagram flood script is staged, not executed) |

### 11.5 Verdict

**READY FOR A RELEASE CANDIDATE**, conditioned on the release notes carrying
§11.3 and §11.4 verbatim: OpenBSD 8.0 amd64, OpenBSD↔OpenBSD and
OpenBSD↔`amneziawg-go`, legacy and 3.1 obfuscation, paired rollback rehearsed.
Everything outside that list is unverified and must not be advertised. No agent
in this pass rebooted, reconfigured or deployed anything outside the disposable
guests.
