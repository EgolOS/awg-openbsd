# Upstream tracking

`awg(4)` is a copy of `sys/net/if_wg.c` plus `sys/net/wg_noise.c` with an
obfuscation layer added. That copy is the project's main long-term liability:
every WireGuard fix that lands in OpenBSD after the baseline has to be carried
across by hand, and a security fix that is missed is a security fix that is
missing from this driver too.

This document records the baseline we ported from, what we changed, and the
procedure for moving the baseline forward.

## 1. Baseline

| Item | Value |
| --- | --- |
| Release | OpenBSD 8.0 (amd64) |
| Source | `https://cdn.openbsd.org/pub/OpenBSD/8.0/sys.tar.gz` |
| Recorded | 2026-11-07 |
| Manifest | `tools/upstream/8.0.sha256` (SHA-256 of every file the port depends on, including `sbin/ifconfig/ifconfig.c`, the file the patch is applied to) |
| Driver commit | `f43535e` |

The manifest is the baseline. It exists so that a divergence report can state
which tree it was produced against: a report computed against a different tree
describes a different upstream and must not be used for the merge.

Verify the tarball the way OpenBSD does, not with HTTPS alone:

```sh
ftp -o SHA256.sig https://cdn.openbsd.org/pub/OpenBSD/8.0/SHA256.sig
signify -C -p /etc/signify/openbsd-80-base.pub -x SHA256.sig sys.tar.gz
```

`install.sh` performs the same check before it copies anything into `/usr/src`
and refuses to continue if the signature or digest does not match.

## 2. How the port is built

`install.sh` copies four files into `$SRCDIR/net/`
(`if_awg.c`, `if_awg.h`, `awg_noise.c`, `awg_noise.h`), copies `AWG.MP` into
`arch/amd64/conf/`, and patches `conf/files`:

```
pseudo-device awg: ifnet
file net/if_awg.c                       awg
file net/awg_noise.c                    awg
file net/wg_cookie.c                    wg | awg
file crypto/chachapoly.c                ipsec | crypto | awg
file crypto/blake2s.c                   wg | awg
file crypto/curve25519.c                wg | awg
```

The `| awg` attachments matter: without them a kernel configured with `awg` but
without `wg` links with undefined symbols, because `wg_cookie.c` and the
`crypto/` helpers were only reachable through the `wg` attribute. The patching
is idempotent and text-matched; if a future release reformats those lines the
installer stops with `conf/files patch failed` instead of building a kernel that
cannot link. That failure mode is intentional.

Renaming: the copy uses `awg_*` for everything the stock file calls `wg_*` (and
`AWG_*` for `WG_*`). `tools/upstream-diff.py` applies the same rename map to the
stock text before comparing, so an untouched function compares equal even though
every identifier differs.

## 3. Divergence inventory (measured)

`python3 tools/upstream-diff.py --tree <stock sys/>` compares the two files
function by function. At `f43535e` against the 8.0 baseline:

| File | Identical | Modified | AWG-only |
| --- | --- | --- | --- |
| `src/if_awg.c` ← `net/if_wg.c` | 40 | 37 | 24 |
| `src/awg_noise.c` ← `net/wg_noise.c` | 36 | 5 | 4 |

Of the 42 modified functions, 20 differ by three lines or fewer — a timing field,
an extra initialiser, a changed panic string. Those stay cheap to re-apply. The
expensive ones, by number of changed lines:

| Function | Changed lines | Why |
| --- | --- | --- |
| `awg_ioctl_set` | 111 | every AWG parameter, validation, version switch |
| `awg_ioctl_get` | 73 | status readout for all parameters |
| `awg_encap` | 46 | padding, junk, trailers, header protection |
| `awg_input` | 33 | prefix strip, classification, magic headers |
| `awg_handshake` | 28 | 3.1 handshake variants, signature packets |
| `awg_send_buf` | 21 | S1–S4 prefixes, per-type padding |
| `awg_clone_create` | 18 | AWG defaults, softc fields |
| `awg_decap` | 13 | padding removal, bounds |

AWG-only functions (24 in the driver, 4 in noise) are the obfuscation layer
itself: `awg_classify`, `awg_send_junk`, `awg_send_ispecs`, `awg_content_padding`,
`awg_random_trailer`, `awg_hp_init`, `awg_ispec_parse`/`awg_ispec_free`,
`awg_range_*`, the timing helpers, and `awg_reset_3_1`.

Regenerate this table whenever the driver changes:

```sh
python3 tools/upstream-diff.py --tree /usr/src/sys      # on an OpenBSD host
python3 tools/upstream-diff.py --tree ./upstream-8.0    # from a fetched sys tree
```

## 4. Moving the baseline to a new release

Do this in a scratch copy, never in the tree a running server builds from.

1. Fetch and verify the new `sys.tar.gz` (§1). Unpack it somewhere writable.
2. Re-vendor the stock `kern_pledge.c`: copy the new tree's
   `sys/kern/kern_pledge.c` over `src/kern_pledge.c.orig`, then re-apply the
   AWG edit to that stock file to produce `src/kern_pledge.c` (the
   `net/if_awg.h` include plus the `SIOCSAWG`/`SIOCGAWG` cases in the
   `PLEDGE_INET` ioctl group, AWG-038). The two hunks come from the current
   `awg-openbsd.patch`; apply them with `patch -p0 -F 0` and fix the context
   by hand where the release moved lines.
3. Record the new manifest: copy `tools/upstream/8.0.sha256` to
   `<new>.sha256`, update the header, and recompute the digests of the files
   that still exist — including **both** `kern/kern_pledge.c` (the new stock
   `.orig`) and `kern/kern_pledge.c.awg` (the patched copy). A file that
   disappeared upstream is a finding, not a nuisance.
4. Regenerate the patch artifact: `sh generate-patch.sh`. The
   `sys/kern/kern_pledge.c` section is a `diff -u` of
   `src/kern_pledge.c.orig` against `src/kern_pledge.c`, so it only picks up
   the re-vendored stock once steps 2 and 3 are done.
5. `make check-upstream` (part of `make check`) must be green in the same
   commit: it compares the recorded digest, the vendored pair and the patch
   hunk (§9). Steps 2–5 are one commit; a baseline move that refreshes only
   some of them is exactly what the gate rejects.
6. `python3 tools/upstream-diff.py --tree <new tree> --release <new>`.
   A `MISMATCH` on the *old* manifest is expected here — it tells you the tree
   changed; the function report is what matters.
7. Read the upstream diff for the functions listed as `modified`. For each:
   - identical copy upstream and downstream → take the upstream body verbatim;
   - small AWG delta → re-apply the delta on top of the new body;
   - large delta → re-implement the AWG part against the new structure. Do not
     paste the old body over a changed upstream function.
8. `python3 tools/sync-check.py` for the same comparison across all files
   registered in `conf/files`.
9. Rebuild in a lab VM (`install.sh --all`), run `make check-vm`, and only then
   record the new baseline as supported in README/docs.
10. Update this document: new row in §1, new divergence table in §3.

Only `kern_pledge.c` is vendored as a stock/patched pair. `sbin/ifconfig/ifconfig.c`
and `conf/files` refresh differently: the installer patches the release's own
`ifconfig.c` with `sbin/ifconfig/ifconfig_awg.patch` and edits `conf/files`
with `awk` text-matching, so moving the baseline changes only the recorded
digests for those two — no vendored copy to re-vendor, and the existing
`ifconfig_awg.patch` is reused as long as its context still applies.

## 5. What to watch upstream

| Upstream file | Why it matters |
| --- | --- |
| `net/if_wg.c` | the driver body; every change is a candidate merge |
| `net/wg_noise.c` | handshake and key schedule; AWG 3.1 timings hook into it |
| `net/wg_cookie.c` | cookie validation; `DisableCookies` bypasses this path |
| `crypto/blake2s.c`, `crypto/curve25519.c`, `crypto/chachapoly.c` | used directly by the handshake |
| `conf/files` | attribute wiring; the `| awg` patch depends on the exact lines |
| `sbin/ifconfig/ifconfig.c` | the AWG keywords live in a patch against this file; its digest is in the manifest, so a re-rolled patch can be checked against the tree it expects |

A change to any of the first five requires a kernel rebuild. A change to
`ifconfig.c` requires rebuilding and reinstalling `ifconfig` as well — the
kernel and `ifconfig` speak the same ioctl, and a half-upgraded pair fails with
`Inappropriate ioctl for device` (documented in README).

## 6. Compatibility policy

State only what has been built and tested:

| Release | Kernel built | Runtime tested |
| --- | --- | --- |
| 8.0 amd64 | historical AWG.MP#5; clean rebuild NOT RUN this pass | existing #5 re-tested with four VM gates and two-VM integrity test |
| 7.9 amd64 | compiles (`-Werror` clean, per project history) | no |

`-current` is not tracked automatically. It is worth a compile per release
cycle to see the breakage early, but it never becomes a baseline. The
2026-11-10 probe (§8) measured the current `-current` against 8.0: zero
delta on every file the port touches, so a `-current` kernel build today
needs the same patch, with no hunk re-flow.

## 7. Known weaknesses in this process

- Function-level comparison is textual. A semantic change that keeps the same
  shape (for example a changed constant inside an otherwise identical body) is
  reported as a small delta, and a reviewer who skims by line count will miss it.
  Read the diff, do not trust the count.
- `conf/files` patching is `awk` on line patterns. It fails loudly, but it fails
  at install time rather than at review time.
- The ioctl ABI (`SIOCSAWG`/`SIOCGAWG`, numbers 223/224) is ours. Nothing checks
  that a future OpenBSD release does not allocate the same number; a collision
  would appear as a wrong-ioctl failure, not as a build error.
- There is no automated test for the userspace half of the ioctl contract. A
  struct-layout mismatch between kernel and `ifconfig` is caught only by the
  handshake test after an install.

## 8. `-current` probe, measured 2026-11-10 (E2-UPSTREAM)

Sources fetched read-only for this probe:

| Tree | Source | Revision evidence |
| --- | --- | --- |
| 8.0-release | `https://cdn.openbsd.org/pub/OpenBSD/8.0/sys.tar.gz` + `src.tar.gz` | CVS Ids below |
| `-current` | `https://raw.githubusercontent.com/openbsd/src/master/…` (GitHub mirror of anoncvs) | master @ `6689907bdb0b0941e9e0ae76ba0f035b647f3f78`, 2026-11-09T20:41:35Z |

The fetch was re-run in the review round on 2026-11-10 (same day): the GitHub
`master` API returned the same `6689907b…` commit, all 13 files fetched with
rc 0, and every `sha256sum` and `$OpenBSD:` Id line below matched — the
per-file command output is `evidence/e2-upstream/upstream-fetch.log`.

Every one of the 13 files the installer touches is **byte-identical**
between the two trees (SHA-256 equal, not just the recorded digest), so
there is no context shift to carry:

| File | CVS revision (both trees) | 8.0 vs -current |
| --- | --- | --- |
| `net/if_wg.c` | 1.50, 2026-09-20, mvs | identical |
| `net/if_wg.h` | 1.7, 2025-07-10, dlg | identical |
| `net/wg_noise.c` | 1.8, 2025-10-27, mvs | identical |
| `net/wg_noise.h` | 1.3, 2024-03-05, mvs | identical |
| `net/wg_cookie.c` | 1.5, 2023-08-18, jsg | identical |
| `net/wg_cookie.h` | 1.2, 2020-12-09, tb | identical |
| `crypto/blake2s.c` | 1.3, 2023-02-03, miod | identical |
| `crypto/curve25519.c` | 1.2, 2020-07-22, tobhe | identical |
| `crypto/chachapoly.c` | 1.6, 2020-07-22, tobhe | identical |
| `crypto/chacha_private.h` | 1.4, 2020-07-22, tobhe | identical |
| `conf/files` | 1.752, 2026-03-16, claudio | identical |
| `kern/kern_pledge.c` | 1.369, 2026-09-21, jan | identical |
| `sbin/ifconfig/ifconfig.c` | 1.482, 2026-07-13, dlg | identical |

Patch application against both trees (GNU `patch`, fuzz 0, dry run):

- `awg-openbsd.patch` — the five real sections apply cleanly on 8.0 and on
  `-current` (rc 0): the four new `net/` files and the two
  `sys/kern/kern_pledge.c` hunks (PLEDGE_INET ioctl group + `net/if_awg.h`
  include, AWG-038). The `sys/conf/files` block is not a unified-diff hunk:
  it is a text-match block (`@@ pseudo-device wg: ifnet`, no line numbers)
  that GNU `patch` skips while exiting 0, which is why the dry run prints
  exactly five `checking file` lines. `install.sh` applies it with `awk`
  text-matching, and its anchors are present in both trees because the file
  is byte-identical — the digest equality is the evidence, not `patch`.
- `sbin/ifconfig/ifconfig_awg.patch` — 8/8 hunks apply cleanly on both
  (rc 0). `install.sh` uses `patch -C -F 0` on OpenBSD, the strictest mode;
  the recorded `sbin/ifconfig/ifconfig.c` digest in
  `tools/upstream/8.0.sha256` matches the fetched 8.0 file.

Consequence: no upstream change touches the patched functions
(`wg_ioctl_set`/`get`, `awg_encap`/`input`/`handshake`, `pledge_inet`,
the ifconfig `wg` keyword block) between 8.0 and today's `-current`; the
divergence table in §3 is current for both trees. Raw command output:
`evidence/e2-upstream/` (`upstream-fetch.log` — the per-file `curl` with
`sha256sum` and the `$OpenBSD:` Id line behind the table above,
`patch-8.0.out`, `patch-current.out`, `ifcfg-8.0.out`, `ifcfg-current.out`,
`conffiles-8.0.awged` / `conffiles-current.awged` — the `awk` result on the
fetched `conf/files`, `upstream-probe.log`).

## 9. Automated digest-drift gate

`make check-upstream` runs `tools/upstream-digest-check.py`, which fails
(rc 1) when the recorded digest and the vendored stock stop agreeing:

1. `sha256(src/kern_pledge.c.orig)` must equal the manifest's
   `kern/kern_pledge.c` entry.
2. `sha256(src/kern_pledge.c)` must equal the manifest's
   `kern/kern_pledge.c.awg` entry.
3. Applying the `sys/kern/kern_pledge.c` hunk of `awg-openbsd.patch` to
   the stock file must reproduce `src/kern_pledge.c` byte-for-byte, so the
   patch artifact, the stock file and the recorded digest describe one
   edit.

It is repo-only (no network), part of `make check` alongside
`check-patch`, and covered by `tools/tests/test_upstream_digest_check.py`
(passes at HEAD; fails on simulated drift of the stock, the patched copy,
the manifest, or the patch artifact). The gate compares only the
`kern_pledge.c` pair; the other manifest entries (`if_wg.c`, the `wg_*`
helpers, `sbin/ifconfig/ifconfig.c`, `conf/files`) have no vendored
comparison target, so it detects a half-done baseline move, not an upstream
change by itself. When §4 moves the baseline, the re-vendored
`src/kern_pledge.c.orig` + `src/kern_pledge.c` pair, the new
`<release>.sha256` and a `sh generate-patch.sh` regeneration are the same
commit: the gate fails until all three are refreshed.
