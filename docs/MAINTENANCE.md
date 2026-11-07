# Maintenance

The driver is a copy of `net/if_wg.c` with an obfuscation layer on top, so it
has to be re-synced whenever OpenBSD changes that file. In practice that means
the two release windows (May and November) plus any errata that touches `wg(4)`.

**Current baseline: OpenBSD 8.0**, recorded as the file digests in
`tools/upstream/8.0.sha256` (see `docs/UPSTREAM-TRACKING.md` for the full
record). Release tags (`v0.1.x`) mark the points that were published; the tree
may be ahead of the newest tag, so the manifest — not the tag — is what says
which upstream tree the driver was compared against.

## 1. Get the new tree and report the delta

```sh
cd /tmp
curl -O https://cdn.openbsd.org/pub/OpenBSD/8.1/sys.tar.gz
curl -O https://cdn.openbsd.org/pub/OpenBSD/8.1/SHA256.sig
signify -C -p /etc/signify/openbsd-81-base.pub -x SHA256.sig   # verify first
mkdir -p sys81 && tar xzf sys.tar.gz -C sys81

cd /path/to/awg-openbsd
python3 tools/sync-check.py --base /tmp/sys80/sys/net/if_wg.c \
                            --new  /tmp/sys81/sys/net/if_wg.c
```

`sync-check.py` exits non-zero when anything needs action — a conflicting
function, or one added or removed upstream — so it can gate a release script.
`tools/upstream-diff.py --tree <sys>` answers the other question: how far the
driver as a whole diverges from the recorded baseline (identical / modified /
AWG-only per function).

The report sorts upstream changes into three piles:

| Mark | Meaning | What to do |
|---|---|---|
| `+` / `-` | function added or removed upstream | copy it in, check the callers |
| `=` | upstream changed a function we never touched | take the upstream body verbatim |
| `!` | we changed that function too | merge by hand — this is the only real work |

Exit status is 1 when there is at least one `!`, or when a function was added
or removed upstream. Run with `--verbose` to get the hunks for the `!` rows.

## 2. The functions we own

Anything in this list needs a human look when it shows up as `!`:

- `awg_ioctl_check` — configuration bounds (S1–S4, Jc, Jmax, timings)
- `awg_send_buf` — cluster allocation and size guards
- `awg_send_hs`, `awg_send_junk`, `awg_send_ispecs` — padding, junk, I1–I5
- `awg_peer_update_udp_window` — padding window clamp
- `awg_ispec_parse`, `awg_classify` — 3.1 parsing and packet classification
- `awg_encap`, `awg_decap`, `awg_input`, `awg_output` — carry the obfuscation

Everything else should be a straight copy.

## 3. Verify before you publish

```sh
doas ./install.sh --all        # must stay -Werror clean
doas reboot
uname -v                       # new ident
```

Then the negative tests from `docs/TESTING.md` — in particular
`ifconfig awg0 awgs1 65535` must return `EINVAL`. If a merge accidentally drops
the bounds, that command panics the machine and the test matrix is what catches
it.

Finally a real tunnel: `awg-quick up`, handshake, `ping 3/3`, both legacy and
3.1 with `HeaderProtectionKey`.

## 4. Publish

```sh
git commit -am "sync: adopt OpenBSD 8.1 net/if_wg.c"
git tag -a v0.2.0 -m "OpenBSD 8.1 sync"
git push --tags
gh release create v0.2.0 --notes "Synced to OpenBSD 8.1 ..."
```

Update the "Current baseline" line above and the Tested table in `README.md`.

## AmneziaWG protocol updates

When AmneziaWG itself changes (a new obfuscation parameter, a new
`HeaderProtectionKey` scheme), the reference implementations are:

- `amnezia-vpn/amneziawg-linux-kernel-module` — the kernel semantics,
- `amnezia-vpn/amneziawg-go` — the userspace reference,

and the wire behaviour is validated by a tunnel between `awg(4)` and
`amneziawg-go` (see `docs/TESTING.md`). Never adopt a parameter without a
matching client that can talk to it; the driver silently drops what it cannot
classify, which reads as "the tunnel does not work" and costs hours.

## If this repository goes quiet

The patches are upstreamed as they are written; if they land in
`TrueBad0ur/awg-openbsd-kernel`, that tree is the better one to follow. The
build is also reproducible from the stock release: `src/if_awg.c` plus the
`conf/files` change is the whole kernel side, so nothing here is a dead end.
