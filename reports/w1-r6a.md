# W1 — Round 6a: installer N5 staging + R4-14 packaging docs

Tree: `~/.hermes/cache/scratch/awg-rc2/w1` (out-of-tree workspace, not retained),
branch `feat/awgctl`, base: the maintainer's WIP on top of the worker's change. No push.
Those commit identities resolve in no ref of this repository, so none is cited.
Evidence host: Fedora (Linux) — the installer is exercised through the sandboxed
suite (`tools/tests/shims`, BSD `make`/`install`/`stat`/`mkdir` shims) plus the
real host `cc` for the include-path proof.
Commits (local): see `git log` — `install.sh` staging, shims+tests, docs.
Unit suite: `python3 -m unittest discover -s tools/tests` → **Ran 76 tests …
FAILED (failures=1, skipped=1)**; the single failure is the pre-existing
sibling lock-lane test in another worktree (that run's test name was withdrawn
in review: it exists in no ref of this repository, so it is not cited here).
`make check-syntax`/`check-py`/`check-patch` clean;
`check-scan` reports the 2 pre-existing `manifest.kv` home-path findings.

| Item | Verdict | Command |
| --- | --- | --- |
| N5 staging path + private include | **PASS** | `python3 -m unittest tools.tests.test_installer_r6_staging -v` (5 tests OK) |
| N5 clean env → stage in `$WORK`, no `/if_awg.h` | **PASS** | `python3 repro_n5.py --tools-only` → `EXIT 0`, `Staged driver header in /tmp/awg-install-work.xQMeuI/stage/net/if_awg.h`, `[ ! -e /if_awg.h ]` true |
| N5 absent system header → build uses staged header | **PASS** | `python3 repro_n5.py --tools-only no` → `EXIT 0`, `cc … -I/tmp/awg-install-work.3deIFd/stage -S obj/probe.c` |
| N5 incompatible old header → not silently used | **PASS** | `python3 repro_n5.py --tools-only yes stale` → compiled marker `.string "32"` (staged), not `"16"` (installed) |
| N5 stale `/if_awg.h` → ignored | **PASS** | same run: `sandbox/rootfs/if_awg.h` keeps `/* STALE ROOT STAGE */`, published header is the repo header |
| N5 `--kernel-only` stages before publish | **PASS** | `python3 repro_n5.py --kernel-only yes stale` → `EXIT 0` |
| N5 `--all` stages + publishes | **PASS** | `python3 repro_n5.py --all` → `EXIT 0` |
| R4-14 `awgctl.8` installed | **PASS** | `--all` run: `awgctl installed to …/awgctl (with awgctl.8)`; test asserts `usr/share/man/man8/awgctl.8` |
| R4-14 stale baseline claims + mode table | **PASS** | `docs/ADMIN-INTERFACE.md` §5 table + §6 entry count |
| Lock / recovery / prevalidation (sibling lane) | **NOT RUN** (out of scope) | — |

## N5 — header staging path expanded before `$WORK` exists

Reproduced with the **real script startup** first (not extracted functions):
`sh install.sh --tools-only` against a sandbox root with PATH shims. The WIP's
`IFAWG_STAGED=$WORK/if_awg.h` + `builder_make … CPPFLAGS=-I$WORK` put the header
flat in `$WORK`, while the patched source includes `<net/if_awg.h>`
(`sbin/ifconfig/ifconfig_awg.patch:7`), so the compiler resolves
`$WORK/net/if_awg.h`. Recorded build log (WIP in the out-of-tree workspace; its
commit identity is not cited):

```
make CPPFLAGS=-I/tmp/awg-install-work.dXeU3M
cc -DIFDESCRSIZE=256 -I/tmp/awg-install-work.dXeU3M -S obj/probe.c -o obj/probe.s
obj/probe.c:3:10: fatal error: net/if_awg.h: No such file or directory
make: compiler failed for -I/tmp/awg-install-work.dXeU3M
```

`--tools-only` and `--all` failed the build; `--kernel-only` "succeeded" by
publishing the flat stage. So the WIP's `-I` did **not** fix the defect.

Fix (`install.sh`):
- `STAGE_DIR=$WORK/stage`, `IFAWG_STAGED=$STAGE_DIR/net/if_awg.h`, created with
  `mkdir -m 700 -p` right after `mktemp` (private dir, header mode 0600).
- The ifconfig build gets `CPPFLAGS=-I"$STAGE_DIR"`, so `-I` and the
  `<net/if_awg.h>` include agree on the `net/` layout.
- One `stage_header()` helper is used by every publishing mode: `--all`
  (`ifconfig_build` + `kernel_build`), `--kernel-only` (`kernel_build`),
  `--tools-only` (`ifconfig_build`). `publish_header` fails closed with
  `cannot publish <target>: <stage> was not staged` when the stage is missing,
  and no mode creates `/if_awg.h`.
- Test-facing overrides added next to the existing ones: `AWG_CONF_DIR`,
  `AWGCTL_MAN_DIR` (the sandbox previously touched the host `/etc` and
  `/usr/share/man`).

Proof harness: `reports/w1-r6a-repro.py` (runs the real `install.sh` against a
sandbox root and dumps the exit code, log, recorded command line and tree).
The `make` shim now compiles a probe with the real `cc` using the
`CPPFLAGS=-I` the installer passes; the probe stringifies `AWG_KEY_LEN`
(`AWG_XSTR`), so the emitted object names which header was opened — `32`
(shipped) vs `16` (the deliberately incompatible installed one). The `chmod` and
`mkdir` shims apply their mode for real (the exec bit and `0700` were previously
recorded but not applied), and `mkdir` records its flags.

Regression test: `tools/tests/test_installer_r6_staging.py` (5 tests) runs the
real `install.sh` — clean environment, absent system header, incompatible old
header, stale root stage, `--kernel-only`, `--all` — asserting the stage is
`$WORK/stage/net/if_awg.h` with mode 0600, the `-I` matches it, the compiled
marker is the staged header, and no root-level `/if_awg.h` appears.

## R4-14 — packaging and docs

- `publish_awgctl` installs `sbin/ifconfig`-side `ifconfig.8` and now installs
  `sbin/awgctl/awgctl.8` next to the binary (`$AWGCTL_MAN_DIR`, default
  `/usr/share/man/man8`); the `--all` test asserts the manpage lands.
- `docs/ADMIN-INTERFACE.md` §5: the nine-entry baseline claim is replaced by the
  mode → artifact table (`--all`, `--kernel-only`, `--tools-only`,
  `--build-only`, `--rollback`, with the `awgctl` omission called out per mode);
  §6 no longer says the `ifconfig.c` digest is missing (the 8.0 baseline carries
  it alongside the other 12 entries).
- `README.md` install snippets and the `--all` comment now list the artifacts
  (`kernel + patched ifconfig + header + awgctl (+ awgctl.8) + awg-quick`),
  consistent with `README.md:445`; `install.sh --help` states the same per mode.

## Notes for the next round

- The sibling lock-lane test in another worktree (name withdrawn in review — it
  exists in no ref) fails  # observation, not current evidence
  deterministically in that out-of-tree workspace (both runs acquire; the WIP's `mv -T` → `mv`
  change). Lock lane, sibling worktree — untouched here.
- `tools/publish-scan.py` reports 2 `manifest.kv` home-path findings at HEAD
  (the `stat -f %z` shim's multi-line answer); the N6 per-rule allow table lives
  in w3/w1b, not in this worktree.
