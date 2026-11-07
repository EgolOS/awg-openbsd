# Security audit — findings register

**Audit date:** 2026-11-07 · **Source ref:** `f43535e` (OpenBSD 8.0 amd64 baseline; the audit
started at `abca36c` and the fixes landed in `f43535e`, same tree for `src/if_awg.c`).
**Method:** four independent agent passes — k1 (kernel packet path, handshake, ioctl), h1b (data
path), h2b (noise/crypto + config surface), h3 (userspace, installer, supply chain), run by three
models — plus a parent pass that re-checked every claim against the source and, where the answer
depended on kernel internals, against the OpenBSD 8.0 kernel tree. Test bed: one OpenBSD 8.0
amd64 VM (KVM, custom `AWG.MP` kernel), kernel built with `install.sh --all`, incremental
rebuilds `AWG.MP#1..#4`, `tools/lab/smoke-test.sh` as the PASS/FAIL gate.

**Second pass (same day, after `f43535e`):** the lab grew three more gates —
`tools/lab/wire-test.sh` (receive path against malformed datagrams), `matrix-test.sh`
(every parameter at its bound) and `stress-test.sh` (200 version switches with traffic).
The first run of `wire-test.sh` panicked the kernel in `awg_reset_3_1()` and produced
`AWG-030`; the fix is in the working tree after `f43535e`.

**Not covered:** two-VM interoperability with `amneziawg-go`/Android/Linux clients (only the
loopback handshake in `smoke-test.sh` and one client bring-up were exercised); throughput
measurement at 1 vCPU; long-run stress (hour-scale rekey/flood soak, memory growth over time);
fuzzing of the packet parser beyond the fixed negative cases. Nothing in this register claims
coverage of those four.

Evidence paths are relative to `~/projects/awg-openbsd-audit/` unless they name a repo file.

## Current revalidation (local series, 2026-11-07)

Starting HEAD `2d71d8cefce55680fba4535a13c88aadbb00bf53`; details and exact gates in
`docs/ENGINEERING-REPORT.md`. New fixes are **local commits, not deployed/pushed**.
The historical audit below is retained; its line numbers and “not covered” list
are not current coverage claims. AWG-014 and AWG-029 remain explicit release
blockers, not agent-waived risks. Existing AWG.MP#5 VM gates were re-executed;
no new kernel build/boot, official-client interop, full recovery rehearsal, long
soak, coverage-guided fuzzing or performance result is claimed.

## Summary

| Severity | fixed | open | accepted | rejected | total |
|---|---|---|---|---|---|
| critical | 0 | 0 | 0 | 0 | 0 |
| high | 5 | 0 | 1 | 1 | 7 |
| medium | 9 | 1 | 1 | 0 | 11 |
| low | 11 | 0 | 3 | 0 | 14 |
| info | 1 | 1 | 1 | 0 | 3 |
| **total** | **26** | **2** | **6** | **1** | **35** |

## Register

### AWG-001 — Transport decrypt reads past the mbuf cluster for datagrams above one cluster
- Severity: high · State: rejected · Confidence: confirmed by the hunter, refuted by parent verification
- Surface: remote, unauthenticated (any UDP datagram to the port)
- Affected: all commits before `f43535e` (behaviour unchanged by the audit)
- Root cause (claimed): `src/if_awg.c:2822` `m_pullup(m, m->m_pkthdr.len)` was assumed to
  guarantee only one cluster (2048 B), so `awg_decap` (`:2300-2305`) would size the
  ChaCha20-Poly1305 AEAD from `m_pkthdr.len` and read ~63 KB out of a 2 KB cluster.
- Remediation: not fixed — nothing to fix. See "Rejected claims" R-1; the datagram is contiguous.
- Regression test: `sent truncated and oversized datagrams, kernel alive` (32 KB datagrams to the
  port, `smoke-test.sh` §4) — passes in the VM with the kernel healthy.
- Evidence: `run-1/agents/h1b-datapath/findings.md` (F-A1), `run-1/REPORT.md`

### AWG-002 — Jumbo MTU + `S4` + padding exceeds `MAXMCLBYTES` → `m_clget` panic
- Severity: high · State: fixed · Confidence: confirmed (threshold corrected to 64 KiB)
- Surface: local root config (`MTU` 1..9000, `S4` ≤ 1280); any peer datagram triggers it
- Affected: all commits before `f43535e`
- Root cause: `src/if_awg.c:2197-2210` computes `out_len = s4 + 48 + payload + authtag` with no
  `if_mtu` term; `m_clget` panics above `MAXMCLBYTES`, and the `S1`–`S4` cap is checked in
  isolation (`:3072-3074`), never against the MTU.
- Remediation: `awg_encap()` drops the packet and logs instead of panicking; the check uses the
  real `MAXMCLBYTES` (64 KiB in 8.0), not 9216.
- Regression test: `awgs1 65535 rejected` and `awgs1 1281 rejected (AWG_MAX_PADDING)`
  (`smoke-test.sh` §2); the jumbo-MTU reproduction recipe in `docs/TESTING.md` is manual.
- Evidence: `run-1/agents/h1b-datapath/findings.md` (F-A2), `run-1/REPORT.md`

### AWG-003 — Config value reaches a bash arithmetic context (root command execution)
- Severity: high · State: fixed · Confidence: confirmed (reproduced in bash)
- Surface: userspace root (provider-supplied `[Interface]` config)
- Affected: all commits before `f43535e`
- Root cause: `sbin/awg-quick:181` — `(( ${S1:-0} < 12 || ... ))` re-parses the expanded config
  text, and an array subscript inside arithmetic undergoes full expansion, so
  `S1 = x[$(cmd)]` executes `cmd` as root during `parse_options`, before any `ifconfig` call.
- Remediation: every numeric field goes through `need_uint`/`need_range` validation first, and
  the arithmetic uses the validated locals.
- Regression test: manual — `awg-quick up` with `S1 = x[$(touch ...)]` is refused before
  `ifconfig` (run-1 VM run). No automated test in `smoke-test.sh`.
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C1), `run-1/REPORT.md`

### AWG-004 — `PreUp`/`PostUp`/`PreDown`/`PostDown` are `eval`ed as root
- Severity: high · State: accepted · Confidence: confirmed
- Surface: userspace root (config content, normally supplied by a VPN provider)
- Affected: all commits through `f43535e`
- Root cause: `sbin/awg-quick:557-565` runs the hook with `eval` (call sites 575, 592, 598, 602);
  `%i`/`%I` substitution happens first and `$VAR` inside the hook expands from root's environment.
- Remediation: not fixed — documented `wg-quick` behaviour. `SECURITY.md` now states plainly that
  a config file is a root program (new "Userspace: config files are root programs" section).
- Regression test: no automated test.
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C2), `run-1/REPORT.md`

### AWG-005 — Private key (and PSK/HPK) on the `ifconfig` argument vector and in stderr
- Severity: high · State: fixed · Confidence: confirmed
- Surface: local (any local user via `ps(1)`, core dumps, ktrace, stderr log sinks)
- Affected: all commits before `f43535e`
- Root cause: `sbin/awg-quick:268-270` builds `args+=( awgkey "$PRIVATE_KEY" )` and `cmd()`
  (`:61`) echoes the whole argv to fd 3 before running it, so every `up` writes the base64 key
  twice. Upstream `wg-quick` uses setconf-on-stdin; the removal of the helper reintroduced it.
- Remediation: the ifconfig patch gains `awgkey -` (key read from stdin) and `cmd()` redacts key
  arguments (`awgkey <redacted>` in the echoed command).
- Regression test: `awg0 key from stdin (awgkey -)` (`smoke-test.sh` §1); the redaction in the
  `awg-quick` log is manual.
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C3), `run-1/REPORT.md`
- Current revalidation (`6ec75cf` plus follow-up `be08d4a` secret queue): private-key stdin was
  already fixed, but PSK/HPK still appeared in `set_config()` argv at the starting HEAD.
  All three now use `AWG_LOAD_KEY` stdin in argument order; shell xtrace is disabled
  before reading configuration. `test_all_secret_keys_use_stdin_in_argument_order`
  and native `all-keys-native.log` pass. Manual ifconfig argv forms still exist;
  root tracing/core/ktrace and deliberately secret-bearing hooks remain out of scope.

### AWG-006 — Kernel and userland sources built from unverified downloads
- Severity: high · State: fixed · Confidence: confirmed
- Surface: network (MITM / compromised CDN path) plus local root
- Affected: all commits before `f43535e`
- Root cause: `install.sh:47-53,79-83` fetch `sys.tar.gz`/`src.tar.gz` with `ftp … 2>/dev/null`
  and extract into `/usr/src` with no `SHA256.sig`/signify check, so the download decides the
  content of `/bsd` and `/sbin/ifconfig`. `docs/MAINTENANCE.md` does verify by hand.
- Remediation: `SHA256.sig` is fetched and verified with `signify(1)` before extraction; the
  installer refuses to build when verification is impossible.
- Regression test: no automated test — manual `install.sh --all` in a fresh OpenBSD 8.0 VM.
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C4), `run-1/REPORT.md`

### AWG-007 — `DisableCookies = on` removes the only pre-handshake gate
- Severity: medium · State: accepted · Confidence: confirmed against `sys/net/wg_cookie.c`
- Surface: remote, unauthenticated (any host that knows the public key)
- Affected: all commits through `f43535e`
- Root cause: `src/if_awg.c:1974-1985` forces `underload = 0` when the flag is set, so
  `cookie_checker_validate_macs()` returns after `mac1` — no cookie, no per-address ratelimit —
  and every queued initiation costs one X25519 (`awg_noise.c:305`) before anything rejects it.
- Remediation: not fixed — inherent to the AmneziaWG parameter. `SECURITY.md` threat model now
  spells out the CPU-exhaustion exposure next to the `S1`–`S4` row.
- Regression test: no automated test (a cookie-off interface under a flood is manual).
- Evidence: `run-1/agents/k1-kernel/findings.md` (F-K1), `run-1/REPORT.md`

### AWG-008 — Timing cross-check uses `r_hi` and is skipped across separate ioctls
- Severity: medium · State: fixed · Confidence: confirmed
- Surface: local root config; every peer data packet then triggers a handshake
- Affected: all commits before `f43535e`
- Root cause: `src/if_awg.c:3076-3081` compares `reject.r_hi` against `keepalive.r_hi +
  rekey.r_hi` while `awg_noise_rekey_after_time_recv()` (`awg_noise.c:1008-1020`) draws in
  `[lo,hi]` and subtracts the margin, so a reject near `lo` yields 0; the gate at `:3072-3075`
  also requires both `HAS_*` bits in the same `SIOCSAWG`, so a later single-field update merges
  unvalidated (`:3354-3365`).
- Remediation: the ranges are merged first and the check requires
  `reject.lo > keepalive.lo + rekey.lo`.
- Regression test: `reject-after <= keepalive + rekey rejected` (`smoke-test.sh` §5).
- Evidence: `run-1/agents/h2b-handshake/findings.md` (F-B1, F-B2), `run-1/REPORT.md`

### AWG-009 — Fixed `/tmp` paths written as root follow planted symlinks
- Severity: medium · State: fixed · Confidence: confirmed
- Surface: local (any local user, sticky `/tmp`)
- Affected: all commits before `f43535e`
- Root cause: `install.sh:16,18,50,53,285` — `LOGFILE=/tmp/awg-install.log` is truncated then
  appended, and `ftp -o /tmp/sys.tar.gz` writes through the same class of fixed name, so a
  pre-created symlink (`/etc/pf.conf`) is truncated/corrupted by root.
- Remediation: `mktemp` for the log and the work directory; the work dir prefers `/usr/src`.
- Regression test: no automated test.
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C5), `run-1/REPORT.md`

### AWG-010 — Config file and directory modes assumed, not enforced
- Severity: medium · State: fixed · Confidence: confirmed
- Surface: local userspace (a `0644` config in a `0755` directory is readable by all users)
- Affected: all commits before `f43535e`
- Root cause: `sbin/awg-quick:75-82` stats nothing before reading the config; `install.sh:198-204`
  chmods the config directory only when it creates it, so a pre-existing `0755` stays `0755`.
  `README.md` states mode `0600` as an assurance the code never establishes.
- Remediation: `awg-quick` refuses a config that is not `0600`/`0400` and warns on a non-`0700`
  directory; the installer chmods the directory unconditionally.
- Regression test: manual — a `0644` config is refused, a `0600` one comes up (run-1 VM).
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C6), `run-1/REPORT.md`

### AWG-011 — IPv6 `::/0` with an IPv4-only `Address` bypasses the tunnel
- Severity: medium · State: fixed · Confidence: confirmed
- Surface: local root config (the common "full tunnel" provider config)
- Affected: all commits before `f43535e`
- Root cause: `sbin/awg-quick:443-449` prints a warning and returns success for an IPv6 AIP when
  no IPv6 address is configured, so IPv6 keeps the physical default route and real source address
  while `up` exits 0.
- Remediation: fails closed; `AWG_ALLOW_IPV6_BYPASS=1` restores the lenient behaviour.
- Regression test: manual — `::/0` without an IPv6 address fails closed and tears the interface
  down (run-1 VM). No automated test in `smoke-test.sh`.
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C7), `run-1/REPORT.md`

### AWG-012 — Publish gate's WireGuard-key rule never matches; quoted filenames skipped
- Severity: medium · State: fixed · Confidence: confirmed (mechanism test run in a scratch repo)
- Surface: local (the publish gate that guards a public repository)
- Affected: all commits before `f43535e`
- Root cause: `tools/publish-scan.py:13` ended the 44-char base64 pattern with `=\b`, and `=` is a
  non-word character, so a key followed by whitespace/EOL never matched; `:43,46-48` split
  `git ls-files` output, which C-quotes non-ASCII names, so those files were reported as scanned
  but never opened (`except OSError: continue`).
- Remediation: lookahead `(?![A-Za-z0-9+/=])` replaces `\b`; `git ls-files -z` with
  `.split("\0")`; unreadable files are reported as findings.
- Regression test: `tools/publish-scan.py --self-test` (fixture-driven guard, plus
  `scan:allow-fixture` markers for the lab keys).
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C8, F-C9), `run-1/REPORT.md`

### AWG-013 — Arbitrary config path accepted, then loaded as root through `doas`
- Severity: medium · State: fixed · Confidence: confirmed in disposable OpenBSD lab
- Surface: local unprivileged user (when `doas` permits `awg-quick` without argument restrictions)
- Affected: all commits through `f43535e`
- Root cause: `sbin/awg-quick:75-82` rewrites only bare names into `/etc/amnezia/amneziawg/`; any
  other argument is used as-is provided it ends in `NAME.conf`, and `auto_su` (`:568`) re-execs
  with the original `ARGS`, so the path survives the privilege transition.
- Original proposed remediation (superseded below): restrict to `/etc/amnezia/amneziawg/*.conf` after `readlink -f`.
- Regression test: no automated test.
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C10), `run-1/REPORT.md`
- Current revalidation: fixed locally in `6ec75cf`. `trusted_config_path()` checks
  the canonical regular file and every ancestor for UID 0 and no group/other write
  before parsing; 0600/0400 file-mode checks remain. Arbitrary **trusted root-owned**
  paths are preserved, not globally banned. Native OpenBSD tests used the actual
  `permit nopass :wheel` lab policy: user-owned 0600, root file under a user-owned
  parent, and symlink to an untrusted target were refused; root-private config passed.
  Evidence: `docs/evidence/2026-11-07/native-checks.log`, `test_quick_parser.py`.
  Residual: hooks are intentional root programs; no claim that untrusted provider
  content becomes safe. Narrow doas policy matrices have not all been rehearsed.

### AWG-014 — Build and install run as root inside a group-writable source tree
- Severity: medium · State: open · Confidence: needs-validation (deployment fact: `wsrc` membership)
- Surface: local (a member of the `wsrc` group)
- Affected: all commits through `f43535e`
- Root cause: `install.sh:111-114,185-191` run `make` in `/usr/src/sbin/ifconfig` and
  `/usr/src/sys/arch/amd64/compile/AWG.MP` as root; stock `/usr/src` is `root:wsrc 0775`, so
  whatever is in those makefiles executes as root at the next `--all`.
- Remediation: not fixed — check `find … -group wsrc -perm -g+w` is empty, or build unprivileged
  and install as root.
- Regression test: no automated test.
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C11), `run-1/REPORT.md`

### AWG-015 — `awg_last_underload` is one static shared by every `awg` interface
- Severity: low · State: fixed · Confidence: confirmed
- Surface: remote, unauthenticated (a flood against interface A)
- Affected: all commits before `f43535e`
- Root cause: `src/if_awg.c:1971` declares the ratecheck timestamp at file scope, so interface B's
  handshakes are judged against A's load for up to `UNDERLOAD_TIMEOUT` (1 s) and drop otherwise
  valid initiations.
- Remediation: the field moved into `struct awg_softc` (`sc_awg_last_underload`).
- Regression test: no automated test (needs two interfaces plus a flood).
- Evidence: `run-1/agents/k1-kernel/findings.md` (F-K2), `run-1/REPORT.md`

### AWG-016 — `Jmin` unvalidated; `Jmin > Jmax` accepted and junk packets silently disabled
- Severity: low · State: fixed · Confidence: confirmed
- Surface: local root config
- Affected: all commits before `f43535e`
- Root cause: `src/if_awg.c:3075-3080` range-checks `i_jc`/`i_jmax` only, so
  `awgjc 8 awgjmin 2000 awgjmax 1500` is accepted and `awg_send_junk` (`:1759-1762`) returns
  early — no junk at all, the fingerprint divergence the `Jmax` row claims to fix.
- Remediation: `EINVAL` on `jmin == 0`, `jmin > jmax`, `jmin > 1500` while junk is enabled.
- Regression test: no automated test for the `jmin > jmax` pair; `smoke-test.sh` §2 covers
  `awgjc 65535`, `awgjc 65` and `awgjmax 1501`.
- Evidence: `run-1/agents/k1-kernel/findings.md` (F-K3), `run-1/agents/h2b-handshake/findings.md`
  (F-B3), `run-1/REPORT.md`

### AWG-017 — Receive-rekey margin truncated to 16 bits
- Severity: low · State: fixed · Confidence: confirmed
- Surface: local root config (`KeepaliveTimeout` + `RekeyTimeout` summing above 65535)
- Affected: all commits before `f43535e`
- Root cause: `src/if_awg.c:1147-1151` passes `(ka.r_lo) + (rt.r_lo)` to a `uint16_t` parameter
  (`src/awg_noise.h:137`, setter `awg_noise.c:104-113`), so 40000+40000 wraps to 14464 and the
  initiator keeps transmitting on a keypair past the configured receiving rekey point.
- Remediation: the margin widened to `uint32_t`.
- Regression test: no automated test for a sum above 65535; `awg1 timing ranges`
  (`smoke-test.sh` §5) covers the ordinary range pair.
- Evidence: `run-1/agents/k1-kernel/findings.md` (F-K4), `run-1/REPORT.md`

### AWG-018 — Padding is not accounted against `if_mtu`
- Severity: low · State: accepted · Confidence: confirmed
- Surface: remote authenticated peer (datagram size) plus local root config (`S4`, CPA, trailers)
- Affected: all commits through `f43535e`
- Root cause: `src/if_awg.c:2197-2212` sizes the transmit packet as `s4 + 16 + payload + padding +
  16` with no `if_mtu` term (`t_mtu` at `:2995` is written, never read), so with `S4` up to 1280
  a 1420-byte transport packet leaves the host fragmented; a 148-byte initiation is answered with
  up to `s2 + 92 + trailer`.
- Remediation: not fixed — README now says to lower `MTU` by `S4`.
- Regression test: manual (fragmentation observed on the wire); `awg1 S1-S4 + header protection
  key` (`smoke-test.sh` §5) exercises the parameters.
- Evidence: `run-1/agents/k1-kernel/findings.md` (F-K5), `run-1/REPORT.md`

### AWG-019 — `awg_index_drop()` dereferences a NULL iterator
- Severity: low · State: fixed · Confidence: confirmed (upstream parity)
- Surface: remote authenticated peer (handshake/cookie packets selecting a keypair index)
- Affected: all commits before `f43535e`
- Root cause: `src/if_awg.c:2707-2718` — when `LIST_FOREACH` finds no entry, `iter` is NULL and
  `CONTAINER_OF(iter->i_value, …)` faults before the `KASSERT` can fire; a double drop (or a
  failed `awg_index_set`) panics.
- Remediation: NULL check added — a double drop is a no-op.
- Regression test: no automated test.
- Evidence: `run-1/agents/k1-kernel/findings.md` (F-K6), `run-1/agents/h2b-handshake/findings.md`
  (F-B4), `run-1/REPORT.md`

### AWG-020 — Obfuscation fields read lock-free: torn 32-bit range, half-old HPK
- Severity: low · State: accepted · Confidence: confirmed (worst case quantified)
- Surface: remote, unauthenticated datagram racing a `SIOCSAWG`
- Affected: all commits through `f43535e`
- Root cause: `src/if_awg.c:2726-2739` copies `h1..h4` as two 32-bit words each and the 32-byte
  HPK without a lock, so a reconfiguration can yield `r_lo` from the old and `r_hi` from the new
  range (`awg_range_contains`, `:1079`) or a half-old ChaCha key. `sc_awg_s4` is a single 16-bit
  store, so it never splits.
- Remediation: not fixed — an SMR-published snapshot struct is the fix; see "Open items".
- Regression test: no automated test.
- Evidence: `run-1/agents/h1b-datapath/findings.md` (F-A3), `run-1/agents/k1-kernel/findings.md`
  (F-K6/A3), `run-1/REPORT.md`

### AWG-021 — `Jc` junk burst: up to 64 datagrams (≤96 KB) per handshake
- Severity: low · State: accepted · Confidence: confirmed
- Surface: remote authenticated peer (a rekey); `Jc` comes from config
- Affected: all commits through `f43535e`
- Root cause: `src/if_awg.c:1765` loops `sc_awg_jc` times, each `awg_send_buf` allocating a
  cluster (`len + max_hdr` ≤ 1560) and calling `sosend` on `awg_crypt_taskq`; the cost is queue
  pressure, not overflow.
- Remediation: not fixed — bounded and matches the Linux module.
- Regression test: `awg0/awg1 obfuscation parameters` (`smoke-test.sh` §3, `awgjc 4`); the burst
  itself has no automated test.
- Evidence: `run-1/agents/h1b-datapath/findings.md` (F-A4), `run-1/REPORT.md`

### AWG-022 — `awg_ispec_parse()` leaks pass-1 allocations on an error path
- Severity: low · State: fixed · Confidence: confirmed
- Surface: local root config (`I1`–`I5` via `SIOCSAWG`)
- Affected: all commits before `f43535e`
- Root cause: `src/if_awg.c:1245-1310` `malloc`s `pkt`/`mods` then `return EINVAL` at
  1253/1274/1277/1297-1310 without freeing; the grammar is deterministic, so the returns are
  unreachable today, each hit leaking up to `AWG_MAX_UDP_PAYLOAD` bytes.
- Remediation: a single `bad:` exit frees them.
- Regression test: no automated test (the path is unreachable with the current grammar);
  `I1 above the UDP payload bound rejected` (`smoke-test.sh` §5) covers the ioctl bound.
- Evidence: `run-1/agents/h1b-datapath/findings.md` (F-A5), `run-1/REPORT.md`

### AWG-023 — `install.sh` reuses `ifconfig` sources from another release
- Severity: low · State: fixed · Confidence: confirmed
- Surface: local root (installer), a stale `/usr/src/sbin/ifconfig` left by `sysupgrade`
- Affected: all commits through `f43535e`
- Root cause: `install.sh:60-64` checks `conf/newvers.sh` for `$SRCDIR` (sys) only; `:76-86`
  reuses `/usr/src/sbin/ifconfig` on the strength of `[ -f ifconfig.c ]`, so an old release's
  `ifconfig.c` is patched (with offsets) and built against new headers — contrary to `README.md`.
- Original proposed remediation (superseded below): apply the same `newvers`/`$OpenBSD:` ident check and `patch --dry-run`.
  Related: `--tools-only` no longer downloads the kernel tree.
- Regression test: no automated test.
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C12), `run-1/REPORT.md`
- Current revalidation: fixed locally in `7cc433b`. `verify_ifconfig_source()` hashes
  pristine `ifconfig.c` (or its retained original) against the release manifest;
  staging applies the current patch with `patch -C -F 0` then `patch -F 0`, and an
  existing patched file must match exactly. Mixed kernel release is fatal, not a
  warning. Native stale-file rejection and unprivileged patched-ifconfig compilation
  passed; unit tests cover pristine/patched/stale identities. Manifest corrected
  against signed 8.0 archives (AWG-035). This does not fix writable-tree races AWG-014,
  full installer transaction safety, or establish ABI support for another release.

### AWG-024 — `sync-check.py` exits 0 when upstream added or removed functions
- Severity: low · State: fixed · Confidence: confirmed
- Surface: local (maintainer-facing gate)
- Affected: all commits before `f43535e`
- Root cause: `tools/sync-check.py:75-97,124-126` compute `added`/`removed` but only put
  `conflicts` into the exit status, so a run with unmerged upstream functions reports "all
  mechanical" and exit 0 — `docs/MAINTENANCE.md` treats exit 1 as "there is work to do".
- Remediation: exit 1 whenever anything needs action.
- Regression test: no automated test (exit code of `tools/sync-check.py`).
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C13), `run-1/REPORT.md`

### AWG-025 — Glob/split expansion of `Address`, `DNS` and `AllowedIPs`
- Severity: low · State: fixed · Confidence: confirmed
- Surface: local root config
- Affected: all commits through `f43535e`
- Root cause: `sbin/awg-quick:112,114-116,158,323,585,587` expand `${value//,/ }` unquoted, so an
  address/AIP of `*` becomes a directory listing of the directory root ran the script from.
- Original proposed remediation (superseded below): use `IFS=, read -r -a` and quote every use (`set -f` around the parser).
- Regression test: no automated test.
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C14), `run-1/REPORT.md`
- Current revalidation: fixed locally in `6ec75cf`. Bash `local -; set -f` scopes
  pathname-expansion suppression to parsing and ifconfig argv construction; route
  enumeration uses `read -r -a` and quoted arrays. Hook glob semantics are preserved.
  `test_lists_do_not_expand_filenames` passes with wildcard Address/AllowedIPs and
  a populated cwd. Intentional whitespace/comma list separators remain supported;
  invalid literal network values are left for native ifconfig/route validation.

### AWG-026 — `gen-config.sh` prints private keys to stdout by default
- Severity: low · State: fixed · Confidence: confirmed
- Surface: local operator (terminal scrollback, `script(1)` logs, screen shares)
- Affected: all commits through `f43535e`
- Root cause: `tools/gen-config.sh:33,37-48,50-91` emit two private keys and the PSK on stdout and
  pass the server key through `ifconfig awg9 awgkey "$SPRIV"` argv; the script sets no umask and
  creates no file, so the `umask 077` redirect is documented only in its own header.
- Original proposed remediation (superseded below): default to a `umask 077` / `install -m 600` pair file, `--stdout` for
  the current behaviour.
- Regression test: no automated test.
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C15), `run-1/REPORT.md`
- Current revalidation: fixed locally in `81e1bb8`. Default `pair.conf` is created
  exclusively with umask 077, mode 0600; `--output` selects a new file and `--stdout`
  explicitly exports plaintext. Private keys go through `awgkey -`; tracing is off
  before generation. Failed generation removes its incomplete output and its own
  helper interface; pre-existing awg9 is never modified. Endpoint/CIDR checks reject
  config injection, IPv6 endpoints retain brackets, H values are distinct with a
  bounded collision retry. Generation still uses OpenSSL and /dev/random CSPRNGs.
  Unit contract tests and native OpenBSD `sh -x` generation passed. The small legacy
  generator intentionally supports IPv4 tunnel addresses, with IPv4/IPv6 endpoints.
  Evidence: `docs/evidence/2026-11-07/generator-native.log`, `test_gen_config.py`.

### AWG-027 — `awg_send_hs` trailer arithmetic is overflow-free; `MCLGETL` failure handled
- Severity: info · State: accepted · Confidence: confirmed
- Surface: n/a (verification result, no attacker input)
- Affected: all commits through `f43535e`
- Root cause: `src/if_awg.c:1729-1737` computes `total = padding + len + trailer` in `size_t`
  (≤ window + len), and `awg_send_buf` (`:1009-1028`) guards `len + max_hdr > MAXMCLBYTES` and
  checks `M_EXT` after `MCLGETL` before `m_align`.
- Remediation: no change needed.
- Regression test: `awg0/awg1 obfuscation parameters` and the handshake case (`smoke-test.sh` §3).
- Evidence: `run-1/agents/h1b-datapath/findings.md` (F-A6)

### AWG-028 — `awgversion legacy` with `awgiN` resurrects the previous I-packet
- Severity: info · State: fixed · Confidence: confirmed
- Surface: local root config
- Affected: all commits before `f43535e`
- Root cause: `src/if_awg.c:3135-3141` (`awg_reset_3_1`) uses the caller's array as scratch — it
  frees the freshly parsed `ispec[i]` and moves the device's old struct into it, so the install
  loop at `:3384-3394` stores the *old* I-string back and the new value is discarded.
- Remediation: the reset path gets its own scratch array.
- Regression test: manual (set `awgversion legacy` with `awgi1` on an interface that had an `I1`);
  `smoke-test.sh` §2 covers `3.1-only parameter rejected on a legacy interface` and
  `I1 rejected on a legacy interface`.
- Evidence: `run-1/agents/k1-kernel/findings.md` (F-K7), `run-1/REPORT.md`

### AWG-029 — `/sbin/ifconfig` is overwritten with no backup
- Severity: info · State: open · Confidence: confirmed
- Surface: local root (installer)
- Affected: all commits through `f43535e`
- Root cause: `install.sh:114` installs over `/sbin/ifconfig` with no stock copy, so a bad build
  replaces the tool used to bring up any interface; `docs/DEPLOY-vps.md` backs up `/bsd` only.
- Remediation: not fixed — `cp -p /sbin/ifconfig /root/ifconfig-stock-$(uname -r)` first.
- Regression test: no automated test.
- Evidence: `run-1/agents/h3-userspace/report.md` (F-C16), `run-1/REPORT.md`

### AWG-030 — `awgversion legacy` frees an uninitialised stack array (kernel panic)
- Severity: medium · State: fixed · Confidence: confirmed (panicked `AWG.MP#4`, clean on `AWG.MP#5`)
- Surface: local root config. Reachable from `awg-quick` and from a plain
  `ifconfig awg0 awgversion legacy` on any 3.1 interface, no traffic required.
- Affected: every commit from the 3.1 support up to `f43535e` (introduced with the
  ispec-ownership fix in that commit's parent work).
- Root cause: `awg_reset_3_1()` did `awg_ispec_free(&old[i])` before assigning, while the only
  caller hands it an uninitialised `struct awg_ispec dropped[AWG_ISPEC_COUNT]` from the stack
  (`awg_ioctl_set`, `src/if_awg.c:3380`). `free(9)` on whatever the stack held panics:
  `panic: free: non-malloced addr 0xffff80001c8e81b0 type devbuf`, trace
  `free -> awg_reset_3_1 -> awg_ioctl_set -> awg_ioctl -> ifioctl -> sys_ioctl`.
- Remediation: `awg_reset_3_1()` no longer frees the array it fills — it just moves ownership —
  and the caller zeroes `dropped` first, so the ownership transfer has one owner at each step.
- Regression test: `stress-test.sh` §1 (200 `3.1 -> legacy -> 3.1` switches with a peer present)
  and `matrix-test.sh` §7 (three consecutive round trips). Pre-fix, `wire-test.sh`'s version
  switch alone reached it.
- Evidence: `dmesg` capture of the panic in the session log; `show panic` / `trace` at the `ddb>`
  prompt. `run-2/agents/a3-tests/PROPOSAL.md` proposed the concurrency/version-switch layer that
  found it.

### AWG-031 — The kernel build's exit status is masked, so the installer can report success with no kernel
- Severity: medium · State: fixed · Confidence: confirmed (reproduced on the guest)
- Surface: local root install. Not remotely reachable; the cost is an operator who believes a
  security rebuild happened when it did not.
- Affected: every installer up to `7081e82`.
- Root cause: `make -j"$(sysctl -n hw.ncpu)" 2>&1 | tee -a "$LOGFILE"` reports **tee's** exit
  status. A build that dies (out of disk, compiler error, a tree that does not match the release)
  therefore looks successful, and under `set -e` the script continues to the install step.
  Observed on the second lab VM: `INSTALL_OK seconds=455` was printed with no `bsd` anywhere in
  the compile directory.
- Evidence: `MAKEFLAGS=-bogus make ... | tee -a log; echo $?` prints `0`; the same command
  without the pipe returns non-zero.
- Remediation: the build logs to `$LOGFILE` and its status is checked with `if ! make ...`;
  a failure prints the last 20 lines and stops. The artifact is additionally verified before
  it can reach `/bsd` (ELF, size, `awg_encap` symbol — `--build-only`/`--kernel-only`).
- Regression test: `verify_kernel_artifact()` runs on every `--build-only`/`--kernel-only`;
  the failure branch was checked by hand with a bogus `MAKEFLAGS` (no automated case: forcing a
  real compile failure would need a broken tree).

### AWG-032 — Untrusted PR workflow can reach a persistent privileged runner
- Severity: high · State: fixed · Surface: supply chain, external PR author if a
  self-hosted runner is registered and OPENBSD_RUNNER enabled.
- Evidence at `2d71d8c`: `.github/workflows/ci.yml` pull_request + kernel job gated
  only by a variable, root-required installer. Read-only GITHUB_TOKEN does not
  contain arbitrary code execution on the runner or protect its persistent state.
- Fix: `7cc433b` removes the self-hosted job, not merely its PR condition; PRs run
  hosted checks with read-only contents and checkout credentials not persisted.
- Validation: `test_ci_never_schedules_persistent_self_hosted_runner`; GitHub API
  observed zero repository runners and read-only default workflow permissions.
- Residual: local patch not pushed in this session. Do not register a privileged
  runner without out-of-workflow branch/runner-group isolation and disposable VMs;
  a PR can edit its own workflow condition. No production runner is authorized.

### AWG-033 — Broken CI signify output plumbing and masked verifier status
- Severity: low · State: fixed · Surface: release/operational verification gate.
- Evidence at `2d71d8c`: ci.yml ran signify then a grep with no input; install.sh
  masked signify status with `|| true` and parsed human-readable aggregate output.
  This is a gate-integrity finding, not a demonstrated signature bypass.
- Fix: `7cc433b`, shared `tools/verify-release.sh`: select exactly one named source
  archive, use stock trusted release key, propagate signify exit status directly.
  Removed CI kernel step is not represented as a successful kernel build.
- Validation: real generated signify signatures: valid selected archive passes
  even with absent unrelated manifest entries; bad/missing signature, wrong key
  and modified archive fail. Native OpenBSD valid/corrupt fixture also exercised.
- Residual: existing source directories are a separate trust boundary (AWG-014).

### AWG-034 — Interop transfer PASS without successful delivery
- Severity: medium · State: fixed · Surface: operational release/test evidence.
- Evidence at `2d71d8c`: interop-test.sh ignored sender status and discarded receiver
  bytes, reporting PASS from elapsed seconds. Native OpenBSD rejects its `nc -l -p`
  listener syntax; parent reproduced sender failure before correcting the listener.
- Fix: `7a25f68`: require sender success, exactly 8388608 receiver bytes and the
  known SHA256 of the non-secret zero fixture. Use native nc listen syntax and
  track its actual PID, not pkill -f. Opt-in disposable-lab guard, refuse existing
  awg0, leave PF unchanged, pinned known_hosts default.
- Validation: sender failure and receiver corruption regressions; corrected
  two-VM run passed handshake, ICMP, bytes+digest and counters. Earlier failed and
  timed-out attempts are retained; no throughput inference from this test.
- Residual: trusted A_SSH/B_SSH overrides are shell commands. Fixed-port readiness,
  abrupt SSH loss and all path/metacharacter overrides need broader lab tests.

### AWG-035 — Incorrect userland baseline digest and manifest path resolution
- Severity: low · State: fixed · Surface: supply-chain/provenance tooling.
- Evidence: starting manifest ifconfig hash `98badbbbb05c706fd1fc65e0b9b66fe5bdcd0272de974c0fd7dc8b00533d052d`
  did not match `ifconfig.c` from the release-key-verified 8.0 src.tar.gz.
  upstream-diff.py incorrectly resolved sbin/ below sys/ rather than beside it.
- Fix: `7cc433b`: use verified archive hash
  `6266edbd533c0789383678a50fa22e859693f1f64b37cb42e656e1f02c6e4e34`,
  resolve userland against tree.parent, and record chacha_private.h for HP KAT.
- Validation: signed sys/src archive digests checked, all baseline entries match;
  test_userland_resolves_beside_sys_tree checks both presence and missing failure.
- Residual: base-release identity intentionally rejects unreviewed errata-modified
  ifconfig; update provenance and patch after review, never silently bypass hash.

## Rejected claims

- **R-1 (from AWG-001) — `m_pullup` reads past one cluster.** Counter-evidence: `m_pullup()` in
  OpenBSD 8.0 `sys/kern/uipc_mbuf.c` allocates `space = adj + len` via `MCLGETL` (up to
  `MAXMCLBYTES`) and memcpy's the whole length into that cluster, so the returned mbuf is
  contiguous for the entire packet; when `space > MAXMCLBYTES` it returns NULL and `awg_input()`
  drops the datagram. Confirmed in the VM: 32 KB datagrams to the port leave the kernel healthy.
- **R-2 (from AWG-002) — the 9216-byte threshold.** `MAXMCLBYTES` is 64 KiB in 8.0
  (`sys/sys/mbuf.h`) and the cluster pools go up to 64 KiB, so the panic is real only above
  ~65 KB of aggregate packet+padding. The fix uses the real constant.
- **R-3 (from AWG-003) — "no eval needed" applied to all numeric fields.** Only `S1`–`S4` were
  interpolated into `(( ))`; the other numeric fields went to `ifconfig`, which validates them.
  All are validated now, so the distinction is moot.

## Threat model deltas (`SECURITY.md` at `f43535e`)

- New threat-model bullet: **`DisableCookies = on` removes the pre-handshake gate** — with cookies
  on, a sender answers a cookie challenge and is rate limited per address *before* the X25519 in
  `consume_initiation`; with it off, every queued initiation costs one X25519 and only
  `MAX_QUEUED_HANDSHAKES` bounds the work (AWG-007).
- Eight new known-limitation rows: jumbo MTU + `S4` over `MAXMCLBYTES` (AWG-002), `awg-quick` key
  on the command line (AWG-005), numeric config values in an arithmetic context (AWG-003), config
  file/directory modes (AWG-010), IPv6 `::/0` bypass (AWG-011), unverified source downloads
  (AWG-006), fixed `/tmp` paths (AWG-009), publish-gate key rule (AWG-012).
- New section **"Userspace: config files are root programs"** stating that the `eval`ed hooks make
  a config a root program, and that the key/arithmetic/permission fixes close the accidental
  paths, not the deliberate one (AWG-004).
- Pre-existing rows re-checked against the code and confirmed accurate: `S1`–`S4` ≤ 1280,
  `awg_send_buf` `M_EXT`/`MAXMCLBYTES`, UDP window clamp 1500, `I1`–`I5` above `if_mtu`,
  `conf/files` `wg | awg`, `Jmax` > 1500, H1–H4 legacy overlap, lock-free obfuscation fields.
  The "Timings were not cross-checked" row is now qualified: fixed on merged ranges with the lower
  bounds (AWG-008).

## Open items — owner-facing decisions

- **AWG-014 — release blocker:** root builds still use writable source trees.
  Native lab facts: /usr/src root:wsrc 0775, sys symlink to lab-owned /home/build;
  no wsrc members are required when the source owner itself is untrusted. Do not
  run root make in this layout on a shared/production machine. Isolate verified
  sources and compile unprivileged; validate install-only privilege separately.
- **AWG-029 — release blocker:** kernel rollback exists, but ifconfig is still
  replaced without an automatic paired backup/restore. A manual same-release
  pair procedure is documented but NOT REHEARSED in this pass.
- **AWG-020 — existing accepted limitation:** no snapshot/locking refactor here.
  Keep live parameter changes controlled; broader authenticated-traffic soak and
  lifetime/lock-order proof remain outstanding, not silently waived.
- **Legacy H collisions — existing compatibility decision:** retained. Prefixes
  can equalize lengths; do not promise collision configurations always handshake.
  Collision/overlap interop against official clients remains NOT RUN.
- **AWG-013/023/025/026:** local fixes and revalidation are recorded above.
- **Outstanding acceptance gates:** clean verified full kernel build/boot; exact
  ABI/old-new mismatch matrix; official Go/Linux/Android/Windows tunnels; IPv6,
  roaming, rekey/loss/NAT/MTU; fault injection; long soak/fuzz; full install,
  upgrade/rollback/rescue rehearsal; reproducible constrained-resource benchmarks.
  These are NOT RUN, not PASS and not human-accepted exceptions.

## Process

- **Adding a finding:** append a section to this register with the next free `AWG-` id, ordered by
  severity, with all seven fields filled. Missing fields say "n/a", not blank.
- **Closing a finding:** a finding moves from `open` to `fixed` only with a named regression test —
  a case in `tools/lab/smoke-test.sh` (the PASS/FAIL gate `docs/TESTING.md` refers to),
  `tools/publish-scan.py --self-test` for the publish gate, or an explicitly named manual
  procedure. A finding with no test keeps "no automated test" in the register.
- **A compiling patch is not a closed finding.** `f43535e` is the proof of the rule: the kernel
  built and `AWG.MP#1..#4` booted, and each fix still needed a run in the OpenBSD 8.0 VM
  (`ifconfig awg0 create`, handshake, counters, malformed datagrams) before the finding closed.
- **Verification notes** (not findings, recorded so they are not re-litigated): k1's timing
  arithmetic check — every `arc4random_uniform()` argument is guarded, all range endpoints
  ≤ `UINT16_MAX`, retry work bounded by `t_max_handshake_retries`; h2b's ABI check — the ifconfig
  patch includes `<net/if_awg.h>`, so `awg_data_io`/`awg_interface_io`/`awg_peer_io` layouts are
  shared by construction, `SIOCSAWG`/`SIOCGAWG` are `_IOWR` 223/224.
- **Repo consistency items found while testing** (fixed in `f43535e`, tracked here for the
  record): `awg-openbsd.patch` was stale by 67 lines and `generate-patch.sh --check` now gates
  drift; `tools/lab/install.conf.example` used autoinstall keywords that do not exist, so the
  documented lab install produced no user and no ssh.
