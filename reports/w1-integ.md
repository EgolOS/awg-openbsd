# W1-integ — Round 6d: merged installer tree (feat/awgctl × fix/installer-lock)

Tree: `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6`, base
`9b31ab2` (merge of `feat/awgctl` N5 staging + docs and `fix/installer-lock`
R6c exclusive lock). No push. Evidence host: Fedora (Linux) — the installer runs
through the sandboxed suite (`tools/tests/shims`, BSD `make`/`install`/`stat`/
`mkdir` shims) plus the host `cc` for the include-path proof.

Incoming state: `make check-tests` → `Ran 85 … FAILED (failures=5, skipped=1)`,
all five failures in `tools/tests/test_installer_r6_staging.py`, each
`AssertionError: 1 != 0` with **empty stderr** (the script logs to `$LOGFILE`).

| Item | Verdict | Command |
| --- | --- | --- |
| Repro + script log captured | **PASS** | `python3 -m unittest tools.tests.test_installer_r6_staging -v` + `sh -x install.sh --tools-only` with the test's own `env()`/`run_install()` env, sandbox kept on disk |
| Failing command named | **PASS** | `ln -s <sandbox>/gen-1 <sandbox>/lock.cas.1` → `ln: failed to create symbolic link './gen-1': File exists` (rc 1) |
| Cause named (no test weakened) | **PASS** | merged `install.sh` lock publication × the `feat/awgctl` `ln` shim operand order; tests untouched |
| Fix | **PASS** | `tools/tests/shims/ln` passes flags + operands through; `gen-1` untracked + gitignored; `reports/w1-r6a-repro.py` ROOT from `__file__` |
| `python3 -m unittest tools.tests.test_installer_r6_staging -v` | **PASS** | `Ran 5 tests … OK` |
| `python3 -m unittest tools.tests.test_installer_r5_lock -v` | **PASS** | `Ran 10 tests … OK` |
| `make check-tests` | **PASS** | `Ran 85 tests … OK (skipped=1)` |
| `make check` | **PASS** | exit 0 — `SELF-TEST: ok`, `files scanned: 139`, `RESULT: clean`, `publish scan: clean`, `awg-openbsd.patch is up to date`, `repository checks: OK` |

## 1. Repro and the script log

The test helper deletes its temp dir, so the sandbox was rebuilt in a persistent
directory with the module's own `sandbox()`/`tree()`/`env()`/`run_install()`
helpers (`repro_r6d.py`, same as `reports/w1-r6a-repro.py`), and the run was
repeated under `sh -x` (`repro_x.py`) to get the command trace. All five tests
fail at the same point, before any staging work:

```
$ python3 -m unittest tools.tests.test_installer_r6_staging -v
…
AssertionError: 1 != 0 :            # empty stderr
$ cat /tmp/awg-install-log.p2pUcX
ERROR: another install.sh run reclaimed /…/test_…/lock — wait for it to finish, then rerun
```

## 2. The exact command that fails, and why

The trace shows the lock reaching the reclaim path and dying inside
`publish_lock_owner` — the `ln` that creates the CAS slot, not the staging code:

```
+ gdir=/…/x1/gen-1
+ mkdir /…/x1/gen-1
+ rec=
+ '[' 0 = 0 ']'
+ publish_lock_owner /…/x1/gen-1 'gen=1 pid=1816659 start=2026-11-08T10:00:00 nonce=…'
+ mv_t=
+ cas=/…/x1/lock.cas.1
+ ln -s /…/x1/gen-1 /…/x1/lock.cas.1
+ return 1
+ rm -rf /…/x1/gen-1
+ die 'another install.sh run reclaimed /…/x1/lock — wait for it to finish, then rerun'
```

`ln` exits 1 with `ln: failed to create symbolic link './gen-1': File exists`
(reproduced standalone against the shim). The sandbox `ln` is a shim, and the
merge kept the `feat/awgctl` copy:

```sh
exec /bin/ln -s "$2" "$1"          # tools/tests/shims/ln (feat/awgctl copy)
exec /bin/ln "$@"                  # after the fix
```

With `ln -s TARGET LINK`, `$1` is `-s` and `$2` is the TARGET, so the shim passed
`-s TARGET -s` to `/bin/ln`: the second `-s` is parsed as a *flag*, leaving one
operand, and `ln` creates the link under the **target's basename in the cwd**
(`./gen-1`) and ignores the real link name (`lock.cas.1`).

That is harmless on the `feat/awgctl` lock, which publishes a generation named
`${name}.g$$` — a sibling of the lock in the same directory the test uses as cwd
— and moves it with `mv "$LOCK_GEN" "$name"`, so the basename link and the
following rename agree by accident. The `fix/installer-lock` design publishes
`gen-<N>` through a *separately named* CAS slot (`${LOCKFILE}.cas.${LOCK_GEN}`)
that it then renames over the canonical name, so the basename link collides with
the generation directory `mkdir` just created: EEXIST → `publish_lock_owner`
returns 1 → `acquire_lock` dies "reclaimed" before `stage_header()` runs. That
is why the five staging tests are red while the lock lane's own ten tests stay
green: `test_installer_r5_lock.py` writes its own `mkdir`/`mv`/`ps`/`stat`/`id`
shims into its temp dir and takes `ln` from the real `PATH`, so it never uses the
swapped shim.

Checked leads, both confirmed not the cause: the staging tests export
`AWG_LOCK_FILE=$WORKDIR/lock` and `acquire_lock`'s `mkdir -p "$(dirname …)"`
finds the parent present (the trace shows the `gen-1` directory being created
next to it), and every command the lock lane added (`ps -p … -o lstart=`,
`od -An -N8 -tx1 /dev/urandom`, `mv -T`, `mkdir -p`, `ln -s`, `readlink`)
resolves in the sandbox — `ps`/`mv`/`mkdir`/`stat` shims exist in both lanes
identically except `chmod`/`make`/`mkdir`/`mv`, whose merged copies are the
`fix/installer-lock` versions and work.

## 3. Fix

1. `tools/tests/shims/ln` — pass the flags and both operands through
   (`exec /bin/ln "$@"`), which is the `ln -s TARGET LINK` order the merged
   script uses (and keeps `ln -f "$BSD_BACKUP" /obsd` correct). No test changed.
2. `gen-1` — a lock generation directory committed in `01098cd` as a symlink into
   a scratch temp dir that no longer exists; the scan reported it as
   `NOT SCANNED: gen-1: [Errno 2]` (which makes `publish-scan.py` exit 2).
   Untracked (`git rm --cached`) and gitignored with the CAS slot pattern
   (`gen-*`, `*.cas.*`) — both are runtime state next to `$LOCKFILE`.
3. `reports/w1-r6a-repro.py` — `ROOT` was hard-coded to the `awg-rc2/w1`
   worktree (the scan's one `absolute home path` finding, and stale for this
   tree); it now resolves the repo root from `__file__`, so the repro follows the
   checkout it lives in.

## 4. After-state

`make check-tests` → `Ran 85 tests … OK (skipped=1)`; the two installer suites
`Ran 5 … OK` and `Ran 10 … OK`; `make check` exits 0 with a clean scan (139 files,
no `NOT SCANNED` line) and a current `awg-openbsd.patch`. The published lock is
the canonical shape the lock lane specifies — `lock` → `gen-1` with the
`gen=/pid=/start=/nonce=` owner inside, and the superseded generation removed at
release — and the staging run publishes `$WORK/stage/net/if_awg.h` with the
`-I` build resolving it (compiled marker `32`).
