# Ship list — v0.2.1-rc1 (definitive)

E2-HYGIENE: this file is the single authority for **which files ship, at which
install path, with which owner/group and mode**. The release card must use
this list verbatim. It is the intersection of the four packaging inputs —
the `awgctl.8` man page, the `Makefile` build/install targets, the
`install.sh` publish steps (the repo's packaging mechanism; there is no
`pkg/` directory) and the release manifest `tools/release-manifest.py` —
after the E2 consistency pass.

## Installed artefacts (`install.sh --all`, the release mode)

| # | File | Install path | Owner:group | Mode | Source |
|---|------|--------------|-------------|------|--------|
| 1 | bsd (AWG kernel) | `/bsd` | root:wheel | 700 | built from `src/AWG.MP` + `src/*.c` |
| 2 | ifconfig (patched) | `/sbin/ifconfig` | root:bin | 555 | built from `/usr/src/sbin/ifconfig` + `sbin/ifconfig/ifconfig_awg.patch` |
| 3 | ifconfig.8 (man) | `/usr/share/man/man8/ifconfig.8` | root:bin | 444 | ifconfig source tree (patched) |
| 4 | if_awg.h (header) | `/usr/include/net/if_awg.h` | root:bin | 444 | staged from `src/if_awg.h` |
| 5 | awgctl (tool) | `/usr/local/sbin/awgctl` | root:wheel | 555 | built from `sbin/awgctl/awgctl.c` via `sbin/awgctl/Makefile` (bsd.prog.mk) |
| 6 | awgctl.8 (man) | `/usr/share/man/man8/awgctl.8` | root:bin | 444 | `sbin/awgctl/awgctl.8` |
| 7 | awg-quick (script) | `/usr/local/sbin/awg-quick` | root:wheel | 755 | `sbin/awg-quick` |

Mode sources in `install.sh`: kernel `install -F -m 700` (line 807),
ifconfig/man `install -c -s … -m 555` / `-m 444` (572–575), header
`install -c … -m 444` (586), awg-quick `install -m 755` (598), awgctl/man
`install -c -s … -m 555` / `-m 444` (1112–1117). The root `Makefile` has an
explicit `install` target (`make install` → `sh install.sh $(AWG_INSTALL_MODE)`,
default `--all`; `--tools-only`/`--kernel-only` for the subsets) so it cannot
fall back to the implicit `%.sh → %` rule that generated an untracked
executable `./install`. The standalone `make -C sbin/awgctl install`
(bsd.prog.mk, `PROG`/`MAN`, `BINDIR = /usr/local/sbin`, `BINGRP = wheel`,
`MANDIR` default `/usr/share/man`, modes 555/444) installs the same pair at
the same destinations, owner:group and modes as the release (rows 5–6).
`tools/tests/test_ship_list.py` verifies this agreement repo-only: a staged
`install.sh --all` (recording `install` shim) and the bsd.prog.mk install
rules (simulated on this Linux host, which has no `/usr/share/mk/bsd.prog.mk`)
both reproduce the table's name/path/owner:group/mode.

Install-mode subsets (docs/ADMIN-INTERFACE.md, matches `install.sh`):
`--kernel-only` ships rows 1+4; `--tools-only` ships rows 2–4 and 7;
`--all` ships all seven.

## Release manifest (`make manifest` → `tools/release-manifest.py`)

The manifest pins sha256 of the artefact set (schema v1): the kernel,
`awg-openbsd.patch`, and the tool list `TOOLS_REL` =
`sbin/awg-quick`, `sbin/ifconfig/ifconfig_awg.patch`, `sbin/awgctl/awgctl.c`.
E2 fix: `sbin/awgctl/awgctl.c` was missing from `TOOLS_REL` even though
`awgctl` ships with the release (rows 5–6); the manifest now pins it. The
`awgctl.8` man page travels with the binary (row 6) and is not separately
hashed. The `sbin/awgctl/obj/` build directory is build output, not a
shipped file.

## Consistency pass findings (E2)

1. **Fixed** — `tools/release-manifest.py` `TOOLS_REL` omitted `awgctl`
   while `install.sh --all` and `docs/RELEASE-NOTES.md` ship it.
2. **Fixed** — README "Repo layout" omitted `sbin/awgctl/` (Makefile,
   awgctl.c, awgctl.8) although the Component table and installer ship it.
3. **Fixed** — `make awgctl` (bsd.prog.mk path) leaves `sbin/awgctl/obj/`
   untracked in the tree; `.gitignore` now ignores it and `make
   check-gitclean` fails when `git status --porcelain` is dirty.
4. **Checked, consistent** — `awgctl.8` documents exactly the binary of row 5
   (NAME/SYNOPSIS/`awgctl`), cross-references `awg(4)`, `ifconfig(8)` and
   `awg-quick(8)`; the man page installed by the release is the repo copy
   (row 6), the same file `sbin/awgctl/Makefile` installs via `MAN`.
5. **Checked, consistent** — `generate-patch.sh` emits the patch rows
   (if_awg.h, if_awg.c, awg_noise.h/.c, kern_pledge hunk, files entries);
   `awg-openbsd.patch` is the documented application vehicle for the kernel
   and matches the staged sources.
6. **Fixed (review R1-2)** — the root `Makefile` had no `install` target, so
   `make install` used GNU make's implicit `%.sh → %` rule: it created an
   untracked executable `./install` (a copy of `install.sh`), installed none
   of the seven files and left the tree dirty. The target is now explicit and
   runs `install.sh` with the mode selectable (`AWG_INSTALL_MODE`).
7. **Fixed (review R1-2)** — `sbin/awgctl/Makefile` set no `BINDIR`, so the
   standalone bsd.prog.mk install used `/usr/sbin` with group `bin` while the
   release publishes `/usr/local/sbin` with group `wheel` (row 5). `BINDIR`
   and `BINGRP` are now pinned to the release values; the staged install test
   proves name/path/owner:group/mode agreement for both install paths.