# W1-R6b — installer correctness: N1 lock, R4-02 recovery, R4-08 prevalidation

Repo `awg-rc2/w1c`, branch `fix/installer-lock`, base `9ced40e`.
Commits: `bf239b2` (N1), `b38224d` (R4-08), `f0e3298` + `ef0cf62` (staging test fixes).
No push. VM 2205 used for the OpenBSD verification run.

## N1 — exclusive run lock (FIXED)

Reproduced: the R5 lock renamed the lock *directory* to a generation dir
before comparing the owner, so a third process `mkdir`'d in the rename gap
(two simultaneous holders) and the rename-back nested the moved directory
inside the new one. The symlink + `mv -T` shape also needs a `-T` that
OpenBSD 8.0 `mv(1)` does not have.

Fix (shape (b) from the review): the lock is one regular file carrying
`gen=<N> pid=<P> start=<S> nonce=<X>`. A new generation is published
through a compare-and-swap slot named after the *observed* generation
(`lock.cas.<gen>`): exclusive create (`O_EXCL`), token write, atomic
rename over the canonical name. The canonical name is never temporarily
free, and reclaimers of one generation collide on the same slot — exactly
one wins, the losers refuse. The recorded pid start time is compared
(`ps -p <pid> -o lstart=`, `kill -0` fallback) so a recycled pid is not
mistaken for the old owner. A lock with no complete owner record is
"initialising" and is only reclaimed after `AWG_LOCK_GRACE` (default 30 s).
`release_lock` removes the lock only while the published generation+token
still match, so a reclaim during the work leaves the new owner's lock.

Tests (`tools/tests/test_installer_r5_lock.py`, all with the real EXIT
trap): free acquire; refused contender leaves the holder untouched;
missing owner record is initialising; recycled pid (live pid, mismatched
start) is stale; SIGSTOP the initializer past the grace → the second run
reclaims and the paused owner's token is the one replaced; three
reclaimers with a barrier at the reclamation point → exactly one wins and
publishes gen+1 with no CAS leftovers; SIGKILL the holder → the next run
reclaims; usage error / unknown option / non-root → no lock created and an
existing lock untouched; 8-way race → one winner.

## R4-02 — fail-closed recovery of the previous pair (FIXED)

`restore_generation_pair` prevalidates **all** recorded members (presence +
sha256 + size) before writing anything; a missing recorded member is an
error, never "absent so skip". `record_installed_pair` dies on write
errors. `publish_pair` traps `HUP INT TERM` (`publish_interrupted`) so an
interrupted publish runs the same restore and the failure names the
generation and the pair it restored.

New regression test: `test_sigterm_after_kernel_publish_restores_previous_kernel`
— SIGTERM lands after the kernel is published (before awgctl); the previous
kernel comes back with the hash the manifest recorded, the ifconfig/header/
awgctl are the recorded generation, and no manifest temp files remain.
This is the exact point earlier rounds kept missing.

## R4-08 — parse/resolve/serialise before mutation (FIXED)

`cmd_up` created the interface, then parsed the peer endpoint and
allowed-IPs (and MTU) while mutating, so a bad endpoint left a live
interface with a partial config.

`awg-quick` now runs `prepare_config` first: private key, address
family/prefix, peer endpoint (`host:port`, bracketed IPv6) and allowed-IP
prefixes are validated and serialised into `CONFIG_ARGS`/`CONFIG_KEYS`,
and the MTU is resolved from the endpoint route (read-only). Only then
`add_if`, the single config mutation (`apply_config`), the pre-serialised
addresses (`add_addresses`) and the resolved MTU (`apply_mtu`), then `up`.
An operational failure runs the existing trap (`del_if; del_routes`),
which restores the state.

New tests (`tools/tests/test_quick_up.py`, production functions verbatim,
mocked ifconfig/route/netstat recording every call): valid order
(create → config → addresses → mtu → up); invalid second address; bad peer
endpoint; bad allowed-IP; truncated config (no PrivateKey); mid-config
failure destroys the created interface — each asserts the interface and
MTU are not mutated before validation.

## Verification

- Linux suite: `python3 -m unittest discover -s tools/tests` → **84 tests, OK (1 skipped)**.
- OpenBSD 8.0 VM (2205, real `mv`/`ps`/`stat`, no `mv -T`): full suite run
  recorded in `/home/user/r6b-tests.log` (result appended below).
- Staging tests (`test_installer_r6_staging`) now pass: the `make` shim used
  the chmod shim (record-only) so `obj/ifconfig` was not executable
  (`/bin/chmod`), and `WORK` now honours `TMPDIR` and is kept so the `-I`
  stage is auditable.

### OpenBSD 8.0 run (2205) — portability fixes found by the VM run

The first 2205 run of this tree failed 5 tests (1 ERROR + 4 FAIL), all in
the lock/staging area, and the failures were real portability bugs, not
flakes:

1. `tools/tests/shims/mv` passed `-T` unconditionally. GNU `mv` needs it to
   replace a symlink destination; OpenBSD 8.0 `mv(1)` has no `-T`, so every
   staging run died with `mv: bad flag` → "another install.sh run reclaimed
   the lock" (the CAS slot survived the failed rename). The shim now passes
   `-T` only on Linux (`case $(uname -s)`); BSD `mv -f` replaces by default.
   This is the same `mv -T` gap the N1 note calls out for the lock itself.
2. The SIGSTOP test slept the paused owner for 1 s with a 1 s grace. BSD
   wakes the SIGSTOPped shell ~100 ms after its `sleep` child exits, so the
   owner released before the contender started and the test read no lock
   (the contender then took generation 1). The owner now sleeps 3 s — the
   1.3 s grace elapses while it is still stopped and holding, so exactly
   one holder is observed.

After the fixes the same VM runs the full suite green (result appended
below); the Linux suite stays green (84 tests, OK).

## Status

| Item | Verdict |
|------|---------|
| N1 lock protocol | PASS (Linux; OpenBSD run in progress) |
| R4-02 fail-closed recovery | PASS (SIGTERM-after-kernel regression test) |
| R4-08 prevalidation | PASS (5 mutation-order tests) |
