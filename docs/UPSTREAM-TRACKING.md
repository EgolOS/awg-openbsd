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
2. Record the new manifest: copy `tools/upstream/8.0.sha256` to
   `<new>.sha256`, update the header, and recompute the digests of the files that
   still exist. A file that disappeared upstream is a finding, not a nuisance.
3. `python3 tools/upstream-diff.py --tree <new tree> --release <new>`.
   A `MISMATCH` on the *old* manifest is expected here — it tells you the tree
   changed; the function report is what matters.
4. Read the upstream diff for the functions listed as `modified`. For each:
   - identical copy upstream and downstream → take the upstream body verbatim;
   - small AWG delta → re-apply the delta on top of the new body;
   - large delta → re-implement the AWG part against the new structure. Do not
     paste the old body over a changed upstream function.
5. `python3 tools/sync-check.py` for the same comparison across all files
   registered in `conf/files`.
6. Rebuild in a lab VM (`install.sh --all`), run `make check-vm`, and only then
   record the new baseline as supported in README/docs.
7. Update this document: new row in §1, new divergence table in §3.

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
cycle to see the breakage early, but it never becomes a baseline.

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
