# Runbook: security updates (`syspatch`) on an `AWG.MP` kernel

`syspatch(8)` installs prebuilt binary sets. A patch that carries a kernel
replaces `/bsd` with the **stock** `GENERIC.MP` build at that errata revision,
and `awg(4)` is not in it. The rule this document exists to make impossible to
get wrong:

> A `syspatch` that ships a kernel does not put the fix in your `AWG.MP` kernel.
> The fix lands in `AWG.MP` only when you rebuild from the source tree at that
> patch level.

OpenBSD 8.0 amd64 uses KARL, so a kernel swap recompiles the module cache on the
first boot — a slower boot, not a broken one; verify the cost on a test VM.

## 1. Decision table

| Symptom / situation | What it means | Action | § |
| --- | --- | --- | --- |
| `syspatch -c` lists an errata whose set contains `bsd` | kernel patch: stock `bsd` replaces `AWG.MP` | apply, then rebuild from the patched tree | 2, 3 |
| `syspatch -c` lists a userspace-only patch (`ifconfig`, `ssh`) | no kernel change, but the patched `ifconfig` lost the `awg*` keywords | apply, then `install.sh --tools-only`, no reboot | 2.3 |
| `syspatch` applied and the box rebooted into stock `bsd` | expected; `awg(4)` is gone until the rebuild | rebuild, reboot | 3 |
| `awg0` gone after an upgrade | running kernel is stock, `ifconfig` is the AWG build (or the reverse) | rebuild the missing half | 2.3, 3 |
| `ifconfig: SIOCSAWG: Inappropriate ioctl for device` | kernel and `ifconfig` disagree on the `awg_data_io` layout (223/224) | rebuild both with `install.sh --all` | 2.3 |
| Kernel built, boots, handshake never completes | `ifconfig`/kernel pairing, `awgversion` mismatch, or a merge that dropped a bound | check pairing, then the negative test | 3 |
| New kernel panics or does not reach multi-user | tree does not match the release, or a merge regression | boot `/obsd` (or `/bsd.awg-prev`), then `--rollback` | 4, 6 |
| `sysupgrade` 8.0 → 8.1 | stock kernel and `ifconfig` return, sources are stale | refetch sources, re-roll the `ifconfig` patch | 5 |
| Kernel is months behind, no new errata out | acceptable with compensating controls | record the age, tighten exposure | 7 |

## 2. Does the patch touch our kernel?

### 2.1 Ask `syspatch`

`syspatch -c` lists patches available for the running kernel, `syspatch -l` the
installed ones. The sets stay under `/usr/local/share/syspatch/<release>/`, one
tarball per patch, and the member list says which components it replaces: a list
with `bsd` (usually `obsd` too) is a kernel patch, a list of `usr/lib/lib*.so`
and man pages is not. Some patches carry a library *and* the kernel — treat any
set with `bsd` as a kernel patch. Patch names below are examples; your release
prints its own set.

```sh
syspatch -c                       # available, e.g. "014-wg"
syspatch -l                       # installed: header "8.0/amd64" + one line per patch
tar -tzf /usr/local/share/syspatch/$(uname -r)/014-wg.tgz
```

### 2.2 Read the errata diff and map it to our files

The source diff sits on the CDN in
`https://cdn.openbsd.org/pub/OpenBSD/patches/8.0/common/` as `NNN_<name>.patch`
(syspatch set `014-wg` ↔ `014_wg.patch`; confirm the name against the listing).
Read the file headers before the hunks:

```sh
cd /tmp
ftp -o 014_wg.patch https://cdn.openbsd.org/pub/OpenBSD/patches/8.0/common/014_wg.patch
grep -E '^(---|\+\+\+)' 014_wg.patch     # which source files it edits
less 014_wg.patch                         # the change itself (grep '^@@' for hunks)
```

| Errata touches | Our file | Merge cost |
| --- | --- | --- |
| `sys/net/if_wg.c` | `src/if_awg.c` (`wg_*` → `awg_*`) | hand-merge: 37 of 101 functions differ, 20 by ≤3 lines |
| `sys/net/wg_noise.c` | `src/awg_noise.c` | hand-merge: 5 of 45 modified |
| `sys/net/wg_cookie.c` | stock file, attached to `awg` in `conf/files` | free — rebuild picks it up |
| `sys/crypto/{blake2s,curve25519,chachapoly}.c` | stock files, attached to `awg` | free — rebuild picks it up |
| `sys/conf/files` | patched by `install.sh` with `awk` on line patterns | check the `pseudo-device wg:` and `file net/if_wg.c` lines survive |
| `sbin/ifconfig/ifconfig.c` | `sbin/ifconfig/ifconfig_awg.patch` | re-roll the patch if context moved |
| anything else (`net/if.c`, `usr.sbin/*`) | not in our build | nothing |

`tools/upstream-diff.py` turns that mapping into a per-function report and checks
the tree against `tools/upstream/8.0.sha256` first — a patched tree reports
`MISMATCH`, which is the signal that the tree moved. `identical copies` is a
straight overwrite; `modified` is the hand-merge surface.

```sh
cd /root/awg-openbsd
python3 tools/upstream-diff.py --tree /usr/src/sys            # text
python3 tools/upstream-diff.py --tree /usr/src/sys --json > /tmp/awg-8.0-014-wg.json
```

### 2.3 Userspace-only patch: `ifconfig` still needs a rebuild

`/sbin/ifconfig` here is a stock build with `ifconfig_awg.patch` applied, so a
syspatch that replaces it drops the `awg*` keywords; kernel and `ifconfig` must
agree on the `SIOCSAWG`/`SIOCGAWG` (223/224) layout. No reboot needed: the running
`AWG.MP` already carries the matching ioctl.

```sh
syspatch                                  # patched stock ifconfig
cd /root/awg-openbsd
doas sh install.sh --tools-only           # re-patch + rebuild
strings /sbin/ifconfig | grep -c awgkey   # 3: AWG keywords are back
ifconfig awg0                             # ioctl round-trip against the running kernel
```

## 3. Rebuild procedure (kernel patch)

Order: `syspatch` first, source second — `syspatch` replaces binaries only, and
the source tree is what the kernel builds from.

**1. Apply the patch.** No arguments = fetch and install everything available; the
new kernel goes to `/bsd`, the previous one to `/obsd`.

```sh
doas syspatch
syspatch -l                       # the new patch is listed
uname -v                          # GENERIC.MP#n — stock kernel is running
```

**2. Source at the patched revision.** `sys.tar.gz` is the *base* release, so apply
the errata diffs in ascending order (a later patch can supersede an earlier one for
the same file). Fetch only the patches you do not have; `install.sh` reuses a
`/usr/src/sys` that matches `uname -r`. With `cvs`, check out `OPENBSD_8_0` and
apply the same files on top.

```sh
doas mkdir -p /usr/src && cd /usr/src
doas ftp -o sys.tar.gz https://cdn.openbsd.org/pub/OpenBSD/8.0/sys.tar.gz
doas ftp -o SHA256.sig https://cdn.openbsd.org/pub/OpenBSD/8.0/SHA256.sig
doas signify -C -p /etc/signify/openbsd-80-base.pub -x SHA256.sig sys.tar.gz
doas tar xzf sys.tar.gz
cd /usr/src/sys
for p in 012_dhcp 013_ssl 014_wg; do
    doas ftp -o /tmp/$p.patch https://cdn.openbsd.org/pub/OpenBSD/patches/8.0/common/$p.patch
    doas patch -p0 < /tmp/$p.patch
done
```

**3. Merge surface, then pre-flight.** `--check` reports signify key, source
version, disk and running kernel without changing anything.

```sh
cd /root/awg-openbsd && doas git pull
python3 tools/upstream-diff.py --tree /usr/src/sys
doas sh install.sh --check        # "result: ready" before continuing
```

**4. Build and install both halves.** `--all` rebuilds `ifconfig` against the
patched `ifconfig.c` and the kernel, stages to `/bsd.awg-staging`, verifies it
(ELF, size, `awg_encap` symbol), keeps the kernel it replaces as `/bsd.awg-prev`
and refreshes `/obsd`. 35–50 min on 1 vCPU, ~2.5 GB under `/usr`.

```sh
doas sh install.sh --all
doas reboot
```

**5. Verify the errata is in the running kernel.** The patch level is not in the
`uname -v` string, so `uname -v` (AWG build) and `syspatch -l` (patch installed)
must agree.

```sh
uname -v                          # AWG.MP#<n>, not GENERIC.MP#<n>
sysctl kern.version               # full build string with the ident
syspatch -l
dmesg | head -3
```

**6. Prove the tunnel.** On a production box run the cheap checks, not the four lab
scripts: `smoke-test.sh`, `wire-test.sh`, `matrix-test.sh` and `stress-test.sh`
destroy and recreate `awg0`/`awg1` and churn every parameter, so they belong in the
lab VM (`make check-vm`). `make check` runs anywhere.

```sh
make check                        # repo gates, no VM needed
ifconfig awg0                     # SIOCGAWG round-trip, parameters round-trip
doas awg-quick show               # "last handshake: N seconds ago" is fresh
ping -c 3 -W 2 10.8.0.1           # one address through the tunnel
ifconfig awg0 awgs1 65535         # EINVAL, device stays up (S1–S4 bound)
```

## 4. Keep a known-good kernel

`/bsd` is what the amd64 loader loads. `/obsd` is the previous kernel kept by
`install(8)`; `install.sh` hard-links `/obsd` to `/bsd`, so they are the same inode
and `/obsd` tracks the last *installed* kernel, not the stock one.
`--all`/`--kernel-only` also keep the replaced kernel as `/bsd.awg-prev`, and
`--rollback` parks the kernel it replaces as `/bsd.failed.<timestamp>`. The loader
takes a path after `boot`; `reboot -a` drops it to the `boot>` prompt on the next
boot (verify `-a` for your platform — `man 8 reboot`, `man 7 boot_config`). If the
new kernel does not reach multi-user, boot the previous one, let it stay up, then
`install.sh --rollback`: a kernel that panics before `rc` leaves `/etc/pf.conf` and
`sshd` untouched, so restoring the kernel is the whole rollback.

```sh
ls -li /bsd /obsd /bsd.awg-prev 2>/dev/null   # same inode = same content
sha256 /bsd
doas reboot -a                    # next boot stops at the loader prompt
#   boot> boot /obsd
#   boot> boot /bsd.awg-prev
vmctl start -c -b /root/kernels/bsd-8.0 myvm   # vmm takes the kernel directly
```

## 5. `sysupgrade` (8.0 → 8.1)

`sysupgrade` installs the new release's kernel and userland sets, then reboots:
stock `GENERIC.MP`, stock `ifconfig`, no `awg(4)`, and the old `/usr/src` is stale —
`install.sh` refuses a tree whose `conf/newvers.sh` does not match `uname -r`.

```sh
doas sysupgrade                   # installs 8.1 sets, reboots
doas mv /usr/src/sys           /usr/src/sys-8.0
doas mv /usr/src/sbin/ifconfig /usr/src/sbin/ifconfig-8.0
cd /root/awg-openbsd
doas git pull                     # tree for the new release
doas sh install.sh --all          # fetches+verifies 8.1 sys.tar.gz and src.tar.gz
doas reboot
```

Two cheap checks on a new release: the AWG keywords survived the patch against the
new `ifconfig.c` (`ifconfig_awg.patch` is a context diff against the 8.0 file, so
context moves and hunks can land in `.rej` — inspect `ifconfig.c.rej`, apply by
hand, keep the result as the new patch), and the ioctl numbers did not collide (a
collision shows up as a wrong-ioctl failure, not a build error —
`docs/UPSTREAM-TRACKING.md` §7).

```sh
strings /sbin/ifconfig | grep -c awgkey          # expect 3
grep -rn "'i', 22[34]" /usr/src/sys/sys/ioctl.h /usr/src/sys/net/*.h
```

## 6. Rollback

Kernel first (needs a reboot), then the tools; `pf` and `sshd` are userspace, so
`/etc/pf.conf` and `/etc/hostname.awg0` survive. Roll back the *pair*: an 8.1 kernel
with an 8.0 `ifconfig` (or the reverse) fails with `Inappropriate ioctl for device`
because the `awg_data_io` layout changed.

```sh
doas sh install.sh --rollback     # restores /bsd.awg-prev, parks /bsd.failed.<ts>
# no /bsd.awg-prev: boot /obsd at the boot> prompt, then
doas cp /obsd /bsd && doas chown root:wheel /bsd && doas chmod 700 /bsd
doas reboot
doas sh install.sh --tools-only   # ifconfig rebuilt against the restored tree
doas ifconfig awg0                # ioctl works, no ENOTTY
doas awg-quick up myvpn           # tunnel up, handshake fresh
pfctl -sr | head                  # pf policy intact
```

## 7. Monthly maintenance

```text
1. syspatch -c            — any new patch? which set carries a kernel?
2. install.sh --check     — signify key, source version, disk free
3. install.sh --all       — rebuild if a kernel patch landed; --tools-only if not
4. reboot                 — only when the kernel changed
5. uname -v + syspatch -l — AWG.MP ident and patch level agree
6. ifconfig awg0          — parameters round-trip, no ENOTTY
7. awg-quick show         — "last handshake" seconds old, tx/rx counters move
8. make check             — repo gates pass on the tree you built from
9. upstream-diff.py       — record the merge surface for the next release
10. record the evidence   — §8, dated, in the ops log
```

A box running an `AWG.MP` from an older patch level is missing every `wg(4)` fix in
that window. Compensating controls, cheapest first: keep cookies on
(`-awgdisablecookies`, so the cookie challenge and per-address ratelimit run before
the X25519 — AWG-007); narrow the UDP listener in `/etc/pf.conf` to the served port
and the peer subnets; leave `RekeyAfterTime`/`RejectAfterTime` at defaults so stale
sessions age out; and schedule the rebuild in a window rather than reacting to a
panic.

## 8. Verification evidence to record

One block per update, appended to the ops log and kept next to the kernel you can
boot (`/root/kernels/bsd-<release>`, see `RUNBOOK-sysupgrade.md`).

```text
date            2026-11-07
release         8.0
syspatch        014-wg (kernel) applied
uname -v        AWG.MP#5
kern.version    OpenBSD 8.0 (AWG.MP) #5 Wed Nov  7 ...
sha256 /bsd     3f2a…   (/var/db/kernel.SHA256 updated)
ifconfig        /sbin/ifconfig rebuilt, awgkey present (strings: 3)
handshake       awg-quick show myvpn: last handshake 3s ago, tx/rx increased
negative        ifconfig awg0 awgs1 65535 -> EINVAL, device up
make check      OK
operator        ege
notes           merged wg_index_drop() fix into awg_index_drop()
```
