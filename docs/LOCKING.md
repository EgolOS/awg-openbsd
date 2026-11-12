# Locking, and the two open concurrency questions

This document exists because `SECURITY.md` carried a one-line row —
"obfuscation fields are read without a lock" — that is true but not useful. What
follows is the precise version: which fields, read where, what can actually go
wrong, and why the fix is not in this round.

## 1. What is protected today

| Object | Lock | Held by |
| --- | --- | --- |
| `sc_awg_ispec[5]` (I1–I5) | `sc_ispec_lock` (rwlock) | `awg_send_ispecs()` read, `awg_ioctl_set()`/`awg_reset_3_1()` write |
| softc configuration as a whole | `sc_lock` (rwlock) | `awg_ioctl_set()` write, `awg_ioctl_get()` read |
| peer list, allowed IPs | `sc_lock` | as above, plus peer lookups |
| peer endpoint | `p_endpoint_mtx` (mutex) | `awg_peer_set_sockaddr()` |
| keypair ring, index table | noise remote mutex, `sc_index_mtx` | handshake paths |
| timers | `t_mtx` per timer | `awg_timers_*()` |
| UDP sockets | `sc_so_lock` | bind/unbind |

## 2. What the packet path reads without a lock

`awg_classify()` (`src/if_awg.c:2748`) and `awg_encap()` (`:2176`) copy the
obfuscation fields into locals at entry and never take a lock:

```c
struct awg_range h[4] = { sc->sc_awg_h1, sc->sc_awg_h2, sc->sc_awg_h3, sc->sc_awg_h4 };
size_t          s[4] = { sc->sc_awg_s1, sc->sc_awg_s2, sc->sc_awg_s3, sc->sc_awg_s4 };
int             trailers = sc->sc_awg_random_trailers;
```

`awg_encap()` additionally reads `sc_awg_cpa`, `sc_awg_random_trailers` and the
32-byte `sc_awg_hpk` through `awg_hp_init()`. `awg_send_initiation()` and
`awg_send_response()` read `sc_awg_h1`/`h2` and `sc_awg_s1`/`s2`/`s3`, plus the
`Jc`/`Jmin`/`Jmax` triple.

`awg_ioctl_set()` writes all of them under `sc_lock` in write mode
(`:3213`), so a concurrent write can land between two of the reads above.

## 3. What a torn read can actually do

A `struct awg_range` is two `uint32_t`s; a 32-bit field cannot tear on amd64,
but the *pair* can be seen half-updated, and a set of fields can be seen from
two different configurations. Concretely:

1. **Range half-updated (`r_lo` new, `r_hi` old).** `awg_range_contains()` tests
   `v >= lo && v <= hi`. A torn range either admits a value outside the intended
   window or rejects one inside it. Both outcomes are a *classification miss*:
   the packet is dropped or handed to the handshake path with a header the peer
   did not produce, which fails authentication.
2. **Prefix length changed between the length test and the `memcpy`.** The length
   test and the copy both use the same local `s[i]`, and the copy is guarded by
   `len == s[i] + hs[i].size` (or `len >` with trailers), so `buf + s[i]` is
   inside the datagram by construction. No out-of-bounds read.
3. **Half-old header protection key.** `awg_hp_init()` copies the 32 bytes it
   finds; a half-updated key makes the keystream wrong, so the packet fails to
   decrypt. The peer drops it.

Nothing here reaches memory the datagram does not own, and nothing frees
anything. The observable effect is **packet loss during the window in which a
configuration is being replaced** — which is also the window in which the
interface is being reprogrammed, i.e. a moment an operator already expects
traffic to be disturbed.

The `stress-test.sh` §2 case (parameter churn under traffic, 200 iterations)
is the regression test for "this must not do worse than drop packets": it
asserts the interface survives and still works afterwards, not that no packet is
lost.

## 4. Why the snapshot refactor is not in this round

The fix is to publish the whole obfuscation configuration as one immutable
structure and swap a pointer under a lock, so a reader sees either the old set
or the new one. That is a small-looking change with a large proof obligation:

- `awg_encap()`/`awg_classify()` run from taskqueues and from the UDP input
  path. Some callers already hold `NET_LOCK`, some hold `sc_lock`, some hold
  neither; taking a *sleeping* rwlock in the wrong one is a deadlock, not a
  slowdown.
- `rw_enter_read()` on `sc_lock` while the same thread is inside a path that
  already holds it in write mode self-deadlocks; proving that needs an audit of
  every caller of `awg_encap()`/`awg_classify()` and of the ioctl path.
- A deadlock in the receive path stalls every packet on the machine, which is
  strictly worse than the dropped packet it is meant to prevent.

So the order of work is: write the lock-order table for these two functions,
prove there is no writer-held recursion, then land the snapshot with a
two-VM test that reconfigures while traffic flows (`make check-interop` plus a
variant that runs `awgversion` in a loop). Until then, `SECURITY.md` states the
limitation in the same words as §3 and the operators' rule is: change AWG
parameters while the interface is down (`awg-quick down`, edit, `awg-quick up`).

## 5. H1–H4 collisions on legacy interfaces: decision

`SECURITY.md` listed overlapping legacy `H1`–`H4` values as an open question.
The decision is **to keep accepting them and to keep documenting it**, for these
reasons:

1. **Classification is length-first, but prefixes matter.** Distinct base
   handshake sizes are not a universal disambiguator: S1/S2/S3 can make total
   datagram lengths equal (for example 148+0 and 92+56). With colliding H values,
   the first matching candidate wins, and authentication can then fail. This is
   an availability/compatibility trade-off, not proof collisions always work.
2. **Data packets are separated by size and by index.** A transport packet is
   `len >= s4 + AWG_MIN_DATA_SIZE`; a handshake-length datagram that also
   matched `H4` would still fail the AEAD tag because `r_idx` is random.
3. **Keep the existing compatibility acceptance.** The pinned official Go
   receive classifier uses the same candidate ordering as C. This supports the
   classification comparison, not a claim that every overlapping configuration
   is accepted by every client's configuration API. Official-client collision
   interoperability remains NOT RUN; do not introduce a new rejection without
   protocol and runtime evidence.
4. A collision that does matter (two *peers* configured with the same interface
   parameters) is not a driver problem but a configuration one, and the server
   generator (`tools/gen-config.sh`) now rejects duplicate draws (bounded retry),
   avoiding this ambiguity for newly generated configurations.

This is recorded as a decision, not a gap: `SECURITY.md` row updated to say
"accepted, by design, see docs/LOCKING.md §5".

## 6. Installer run lock: generation-token CAS covers the finish/publish window

The installer (`install.sh`) uses a generation-token lock in `/var/db/awg-install/lock` to serialise concurrent runs. The lock is a single file carrying `gen=<N> pid=<P> start=<S> nonce=<X>`. A new generation is published through an exclusive-create CAS slot named after the **next** generation (`lock.claim-<N+1>` at `install.sh:236`). The canonical `$LOCKFILE` is written **only when it does not exist** (`install.sh:213-219`, `:239-247`); later generations publish to immutable `lock.claim-<N>` slots and the canonical root keeps pointing at generation 1 — the repo's own `test_lock_reclaimed_from_dead_pid` asserts `lock.claim-5` holds gen 5 while `lock` still holds gen 4.

**Acquisition (`acquire_lock`)**: The contender reads the current slot, extracts the generation `N`, and attempts to create `lock.claim-<N+1>`. Before creating it, the contender checks whether the current owner is still alive (`lock_owner_live`, `install.sh:154-162`) and whether a `.released` marker for the current token exists. If the owner is alive and no `.released` marker exists, the contender immediately refuses with "another install.sh run holds lock" — it never creates the next claim slot. Only a dead owner or an already-released lock allows the contender to proceed.

**Release (`release_lock`)**: The owner verifies the lock slot still contains its own token (`read_lock_owner "$LOCK_SLOT"` == `LOCK_TOKEN`, `install.sh:286`), then publishes a `.released` marker with that token. This is the final, authoritative statement that the generation is complete.

**The finish/publish window**: After `publish_pair()` completes (the kernel, ifconfig, header, and awgctl are all installed and recorded) and before the EXIT trap runs `finish()` → `release_lock()`, the lock is still held (`LOCK_OWNED=yes`). A second run starting in this window will:
1. Read the current lock slot (generation `N`)
2. Check if `lock.claim-<N+1>` exists — it does not
3. Check if the owner is alive — yes, the first run is still running
4. Check if `.released` exists for the owner's token — no, not yet published
5. **Refuse immediately** at step 4, before ever creating a claim slot

The `verify_lock_held()` helper previously checked for the absence of `lock.claim-<N+1>` at arbitrary points in the run. This check is **redundant** because the acquisition logic *already prevents* a contender from creating that claim slot while the owner is alive. The generation-token CAS (the exclusive-create of `lock.claim-<N+1>`) combined with the liveness check in `acquire_lock` provides the guarantee for the normal case: no other run can enter the critical section while the lock is held by a live process whose token `ps` start rendering matches.

**What the helper really detected**: a **liveness misjudgement** — `lock_owner_live` returns 1 for an R7 token whose `ps` start-time rendering differs (`install.sh:154-162`), which would let a contender claim the successor while the owner still runs. However, the owner is nonetheless **not displaced** because a contender publishes only into its own `claim-<N+1>` slot, never over the owner's root — both runs' publish namespaces stay disjoint. The canonical `$LOCKFILE` remains the owner's generation 1.

**Accepted residual risk**: the liveness misjudgement branch is reachable only for legacy tokens or tokens whose `ps` start rendering differs from `lock_owner_live`'s parser (e.g., an R7 token with a timezone or locale difference in the start field). This code writes tokens with `new_lock_token` which records the same rendering `lock_owner_live` expects, so the branch is **unreachable for tokens generated by this installer**. Legacy or externally planted tokens could trigger it; that scenario is accepted as residual risk. Disjoint publish namespaces protect the lock metadata (the claim chain), not the shared install targets (`/bsd`, `/etc/amnezia/...`, staged header); the guarantee for those targets rests on the liveness check correctly identifying live owners, which holds for installer-generated tokens.

**Removed**: `verify_lock_held()` was dead code (never called in the main flow; `git show 6a17fbb:install.sh | grep -n verify_lock_held` returns only the definition at line 291). Its **first** assertion (lock still owned) is enforced by `acquire_lock` (contender refusal). Its **second** assertion (no successor claim exists) is **not** enforced by `release_lock` — `release_lock` only compares `read_lock_owner "$LOCK_SLOT"` with `LOCK_TOKEN`, and a contender's `claim-<N+1>` never touches the owner's `LOCK_SLOT` root, so `release_lock` returns success while a successor claim exists. (Proven: successor claim planted mid-run → base fixture exits 1 `lock replaced by another run`, patched fixture exits 0.) A regression test in `test_installer_r7_lock.py` (`test_publish_window_exclusivity`) verifies that a contender started while the holder is in the publish-to-release window is refused. A second regression test (`test_release_lock_ownership_verified`) verifies that `release_lock` fails when the owner's `LOCK_SLOT` no longer names its token.
