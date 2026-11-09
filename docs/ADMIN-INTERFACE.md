# Admin interface: keep the `ifconfig` patch, or add `awgctl`?

Decision document for the awg-openbsd maintainer. Scope: the userspace management
path for `awg(4)` over the next five years (≈10 OpenBSD releases, two per year).
Every number below is counted from the tree at `85c5ba8`, not estimated.
Repository is read-only for this audit; nothing was modified.

## 1. What the arrangement is today, measured

Two artefacts carry the management path:

| Artefact | Size | Shape |
| --- | --- | --- |
| `sbin/ifconfig/ifconfig_awg.patch` | 826 lines, 768 added, 0 deleted | 8 hunks, one file (`ifconfig.c`), 6 context lines per hunk |
| `sbin/awg-quick` | 832 lines, bash | 19 `ifconfig` invocations (25 mentions, 6 of them comments) |

The `sys/conf/files` hunk is **not** in the ifconfig patch — it lives in
`awg-openbsd.patch:5764-5774` (8 added lines, 4 anchors) alongside the four new
kernel files (`awg-openbsd.patch` is 5 778 lines: `if_awg.h`, `if_awg.c`,
`awg_noise.h`, `awg_noise.c` + `conf/files`). `generate-patch.sh` regenerates it
and `make check-patch` gates drift.

### 1.1 The 768 added lines, by section

| # | Patch line | Anchor in 8.0 `ifconfig.c` | Added | Content |
| --- | --- | --- | --- | --- |
| 1 | 3 | 82, include block | 1 | `#include <net/if_awg.h>` |
| 2 | 11 | 365, `wg_*` prototype block | 46 | 45 `setawg*/unsetawg*/awg_status` prototypes |
| 3 | 64 | 393, `A_*` action bitmask | 1 | `#define A_AMNEZIAWG 0x0080` |
| 4 | 72 | 656, `wg*` keyword table | 45 | 44 keyword entries |
| 5 | 124 | 729, `process_wg_commands` prototype | 1 | `void process_awg_commands(void);` |
| 6 | 132 | 979, `main()` | 1 | call to `process_awg_commands()` |
| 7 | 140 | 3409, `status()` | 1 | call to `awg_status(ifaliases)` |
| 8 | 148 | 6056, after `wg_status()` body | 672 | implementation |

Hunk 8 (672 lines) decomposes as: 47 function definitions, of which 41 are
`setawg*`/`unsetawg*` setters (358 lines), 5 are helpers
(`ensureawginterface` 10, `growawgdata` 27, `parseawgrange` 21,
`process_awg_commands` 10, `awg_print_range` 8 = 76 lines), plus `awg_status`
(167 lines) and the `AWG_LOAD_KEY` base64/stdin macro (18 lines, patch 155-172).
So 434 of 768 lines (57%) are mechanical "set one field in `awg_interface_io`"
code, and the irreducible ioctl plumbing is 10 lines (`process_awg_commands`,
patch 639-648) plus the `SIOCGAWG` size-probe loop inside `awg_status`
(patch 673-695).

### 1.2 The collision points at each release

All 8 anchors are the collision surface, but they are not equally risky. The
672-line tail (hunk 8) is *appended* — it is anchored by the last three lines of
`wg_status()` and the first three of `umb_regstate[]` (patch 148-152, 822-826),
so it only breaks if upstream rewrites the tail of `wg_status` or the umb
descriptor arrays. The 96 lines in hunks 1-7 (1+46+1+45+1+1+1) are the ones that
move: the include block, the `wg_*` prototype block, the `A_*` bitmask, the `wg*`
keyword table, and the two call sites in `main()` and `status()`.
`docs/UPSTREAM-TRACKING.md:123-132` lists `sbin/ifconfig/ifconfig.c` as the
sixth watched file and `docs/ARCHITECTURE.md:449` says the same thing: upstream
additions to the wg keyword table shift the context and the patch stops applying.

### 1.3 `awg-quick`'s dependency on `ifconfig` output

Of the 19 invocation sites, 10 set state (`create` 308/322, `description` 323,
`destroy` 334, `up` 338, the keyword argv 407, `inet alias` 419, `mtu` 425/454)
and 9 read the kernel back. Seven of those nine parse the printed text:
`ifconfig` 300 (matches `description: wg-quick:`), `ifconfig -a` 318 and 774
(`grep -Eo '^awg[0-9]+'`), `ifconfig $iface` 444 and 451 (`=~ mtu ([0-9]+)`),
and the two that consume AWG-specific keys: 480 (`populate_endpoint_ips`,
regex on `awgendpoint IP PORT`, `awg-quick:476`) and 767 (`show_iface`).
`show_iface` (712-769, 58 lines, 19 regex match arms) plus `_show_flush_peer`
(698-710) and `format_bytes` (689-696) — 79 lines of parser — consumes the 29
key tokens `awg_status` prints (patch 697-818: `awgport`, `awgrtable`,
`awgpubkey`, `awgversion`, `awgjc`/`awgjmin`/`awgjmax`, `awgs1`-`awgs4`,
`awgh1`-`awgh4`, `awgi1`-`awgi5`, `awghpk`, `awgcpa`, the 5 timing names,
`awgrandomtrailers`, `awgdisablecookies`, `awgpeer`, `awgdescr`, `awgpsk`,
`awgpka`, `awgendpoint`, `awgaip`, `tx`/`rx`, `last handshake`).
`docs/ARCHITECTURE.md:451` flags the real risk: nothing pins the byte layout of
`awg_data_io`/`awg_peer_io`/`awg_aip_io`, so a layout change silently changes
what `ifconfig awg0` prints and what this parser reads.

## 2. Option A — keep the patch, shrink it

The 44 keyword entries split cleanly: 15 are the WireGuard-shaped subset
(`awgpeer`, `awgendpoint`, `awgaip`, `awgpsk`, `awgpka`, `awgport`, `awgkey`,
`awgrtable`, `awgdescription`/`awgdescr`, and the 5 `-awg*` unsetters), 10 are
legacy obfuscation (`awgjc/jmin/jmax/s1/s2/h1-h4`, `awgversion`), 19 are 3.1-only
(`awgs3`, `awgs4`, `awgi1-5`, `awghpk`/`-awghpk`, `awgcpa`, 5 timings,
`awgrandomtrailers`/`-`, `awgdisablecookies`/`-`).

The shrink proposal: drop the 19 3.1 keywords (and optionally the 10 legacy
ones) from the keyword table and accept them through one `awgconf /path` keyword
that hands the config text to the driver. `hostname.awg0` survives — `/etc/rc`
passes the whole line to `ifconfig`, so `awgconf /etc/amnezia/amneziawg/vpn.conf`
is one argument, and README:269-281 keeps working.

Costs and risks: the kernel needs a text parser for the `Key = value` grammar,
which is new code in `if_awg.c` next to `awg_ioctl_check` (`src/if_awg.c:3219`)
and replaces the flag-bitmap application path (`:3372-3445`). The `awg_data_io`
layout changes, so `SIOCSAWG`/`SIOCGAWG` get a number bump (223/224 → 225/226,
`src/if_awg.h:34-40`) and kernel + `ifconfig` must be rebuilt as a pair
(README:79, `docs/ARCHITECTURE.md:450`). The `.conf` grammar then exists in two
parsers — `awg-quick:105-264` in bash and the kernel — which must agree on
`Version` inference (`awg-quick:243-252`) and the range syntax
(`need_range`, `awg-quick:94-103`). Net saving: the keyword table shrinks 45 → 2
lines and 358 setter lines collapse to one, so the patch drops from 768 to
roughly 250, at the price of ~150 new kernel lines and an ABI bump.

## 3. Option B — `awgctl`

Shape: one C binary, `awgctl up|down|show|set|add|del [name]`, reading the same
`/etc/amnezia/amneziawg/<name>.conf`, issuing `SIOCSAWG` (set) and `SIOCGAWG`
(get) on `struct awg_data_io` from `src/if_awg.h` — no new ABI, no number bump —
and shelling to `ifconfig awgN create`/`destroy` for interface creation only
(`ifconfig awgN create` is `SIOCIFCREATE2`, so the create path stays stock).
It installs `hostname.awg0` support by being the tool `/etc/rc` runs after
`ifconfig awg0`, or by `ifconfig awg0; awgctl up` in `rc.local`.

What it buys: no patched base system (the 826-line patch and the
`ifconfig.c.orig`/`ifconfig.c` dance in `install.sh:135-150` disappear), so a
release upgrade replaces `/sbin/ifconfig` with stock and nothing is lost;
`awgctl` is one self-contained port with its own `Makefile`, `Pledge`
(`"stdio rpath wpath cpath getaddrinfo inet"` — `awgendpoint` needs DNS,
`ifconfig` resolves it today at `ifconfig_awg.patch:299`) and `Unveil`
(`/etc/amnezia/amneziawg`, `/sbin/ifconfig`). It also removes the `ps(1)`
exposure of the key: `awgctl` reads `PrivateKey` from the file, so the
`awgkey -` stdin trick (AWG-005, `docs/SECURITY-AUDIT.md:88-95`) is unnecessary.

What it costs: a second config path alongside `ifconfig awg0` — every
`awg(4)` parameter now has two userspace readers (the `awgctl` binary and
`ifconfig`'s `awg_status`) that must agree, and the man page for the driver
(`ifconfig.8`, installed by `install.sh:159`) documents only the ifconfig
spelling. `doas` policy grows from `doas ifconfig` / `doas awg-quick`
(SECURITY.md:22) to a third rule, and AWG-013 (arbitrary config path,
`docs/SECURITY-AUDIT.md:188`) has to be re-decided for the new binary.
And the biggest one: `awg-quick` already parses `ifconfig` output at 7 sites;
`awgctl show` replaces 2 of them (480, 767) and leaves 5, so the shell script
ends up calling both tools.

## 4. Option C — hybrid

`ifconfig` keeps the 15 WireGuard-shaped keys (peer, endpoint, aips, psk, pka,
port, key, rtable, description + unsetters) and `awgversion`; the AWG
obfuscation parameters (10 legacy + 19 3.1) move to one `awgconf` ioctl taking
the config text. The keyword table drops 44 → 16 entries, the setter block drops
41 → 16 functions, and `awg_status` prints the WireGuard subset plus one
`awgconf` blob.

| Axis | A: keep patch | B: `awgctl` | C: hybrid |
| --- | --- | --- | --- |
| Patched base surface | 768 lines, 8 hunks, 1 file | 0 lines (stock `ifconfig`) | ~250 lines, 8 hunks, 1 file |
| Upgrade work per release | re-roll 8 hunks; 96 lines move, 672-line tail is append-only | none for ifconfig; port rebuild only | re-roll 8 hunks, 96 lines move; smaller table to re-verify |
| ABI churn | none (223/224) | none (223/224) | one bump (225/226) + paired kernel/ifconfig rebuild |
| Attack surface, setuid-free tool | `ifconfig` is not setuid; 47 functions, 2 ioctls, base64 + `getaddrinfo` | one binary, `Pledge`/`Unveil` declarable, 2 ioctls | one `ifconfig` with 16 keywords + 1 text ioctl |
| Testability | `make check-vm` gates (`docs/TESTING.md:31-41`) exercise it today; no layout pin | needs the port built in the lab VM, new gate | needs the new `awgconf` parser tested; existing `matrix-test.sh` bounds cases must be re-expressed as text |
| `hostname.awg0` | native | needs `awgctl` in `rc.local` after `ifconfig … create` | native |

## 5. Recommendation

**Keep the `ifconfig` patch (Option A) as the primary management path, and ship
`awgctl` alongside it.** The numbers
say the patch is cheap: 768 added lines is 8 hunks in one file, and 672 of them
(88%) sit in an append-only tail that cannot collide; only 96 lines across 6
anchors are release-sensitive, and `patch(1)` with its 6 lines of context absorbs
most upstream edits. The dominant per-release cost is the driver sync
(`docs/UPSTREAM-TRACKING.md:64-69`: 37 modified + 24 AWG-only functions in
`if_awg.c`), not the management path, and `awgctl` does not reduce that cost —
it adds a third binary that reads the same two ioctls, keeps 5 of the 7
`ifconfig` output-parsing sites in `awg-quick`, and gives up `ifconfig awg0` as
the status view. The shipped snapshot builds `awgctl` with `--all`, installs it
next to `ifconfig` (with its `awgctl.8` manpage), records it in the backup manifest and rolls it back with the
pair, so the tool is delivered without displacing the patch. The publishing
modes and the artifacts each one installs are fixed by `install.sh`:

| Mode | Installs | Omits |
| --- | --- | --- |
| `--all` (default) | `/bsd`, patched `/sbin/ifconfig` + `ifconfig.8`, `/usr/include/net/if_awg.h`, `/usr/local/sbin/awgctl` + `awgctl.8`, `/usr/local/sbin/awg-quick` | — |
| `--kernel-only` | `/bsd`, `/usr/include/net/if_awg.h` | `awgctl` + `awgctl.8`, `awg-quick`, patched `ifconfig` — the management tool matches the running kernel, not the rebuilt one |
| `--tools-only` | patched `/sbin/ifconfig` + `ifconfig.8`, `/usr/include/net/if_awg.h`, `/usr/local/sbin/awg-quick` | `awgctl` + `awgctl.8` (by design), `/bsd` |
| `--build-only` | the built kernel in its object directory | every publish (`/bsd` untouched) |
| `--rollback` | the recorded previous pair (`/bsd` + `/sbin/ifconfig` + header + `awgctl`) | a rebuild |

Concretely, ship
the patch, `awg-quick` and `awgctl`, and spend the effort on the two cheap gaps
the audit
already lists — record `sbin/ifconfig/ifconfig.c` in the baseline manifest
(`tools/upstream/8.0.sha256` now carries the `sbin/ifconfig/ifconfig.c` digest
next to the `net/` and `crypto/` entries, so the file the patch is applied to
has a recorded digest) and add a
`static_assert` on `sizeof`/`offsetof` of `awg_data_io`/`awg_peer_io`/`awg_aip_io`
(`docs/ARCHITECTURE.md:451`). Re-evaluate at the 8.1 re-roll: if the 6 small
hunks need more than about an hour of manual merge, take Option C then, because
it keeps `hostname.awg0` and the status view while halving the keyword table.
Option B is the right answer only if a future release rewrites `ifconfig.c`
wholesale; until then it is a second config path for no measured gain.

## 6. Evidence to gather before committing

- **`ifconfig.c` churn between releases.** Record `sha256 sbin/ifconfig/ifconfig.c`
  per release (the 8.0 baseline already carries it, alongside the other 12
  entries in `tools/upstream/8.0.sha256`), then
  diff 7.9 → 8.0 (and -current snapshots) restricted to the 8 anchor sites, so
  "the patch is cheap" becomes a measured line count per release rather than a
  claim. `tools/upstream-diff.py` already does this for `if_wg.c`; extend it to
  the keyword table.
- **Would OpenBSD take an `awgconf` keyword.** File it on `openbsd-misc` and in
  upstream PR #2 (`README.md:36-38`); a keyword accepted upstream removes the
  patch entirely, which dominates every other option.
- **Size of the `hostname.if` parser.** Count the lines in `/etc/rc` that split
  `hostname.awgN` into `ifconfig` arguments, to confirm a single-path keyword
  (`awgconf /path`) and a multi-token keyword list both survive; this is the
  constraint that keeps Option C cheap.
- **Re-roll cost on a real 8.1 tree.** Build `ifconfig` from an 8.1 snapshot with
  the current patch and record `patch(1)` fuzz/reject output per hunk; that is
  the number that decides A vs C, and it also closes AWG-023
  (`docs/SECURITY-AUDIT.md:309`, `install.sh:118` accepts any release's sources).
- **Layout pin as a test.** Add the `static_assert` and confirm it fires on a
  deliberately reordered `awg_interface_io`; today the four lab gates
  (`docs/TESTING.md:31-41`) only catch a layout regression that breaks the
  handshake, not one that only changes `ifconfig awg0` output.
