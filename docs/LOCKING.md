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

1. **Classification is length-first.** `awg_classify()` only compares the type
   field after `len == s[i] + hs[i].size` matches, and the three handshake
   structs have different layouts and therefore different sizes
   (`awg_pkt_initiation` carries `ue`+`es`+`ets`, `awg_pkt_response` only
   `ue`+`en`, `awg_pkt_cookie` a nonce and an encrypted cookie,
   `src/if_awg.c:110-132`). Two identical `H` values cannot make an initiation
   be read as a response or a cookie, because the length test fails first.
2. **Data packets are separated by size and by index.** A transport packet is
   `len >= s4 + AWG_MIN_DATA_SIZE`; a handshake-length datagram that also
   matched `H4` would still fail the AEAD tag because `r_idx` is random.
3. **The Linux module behaves the same way**, so rejecting collisions would
   break configs that work on the reference implementation — the exact
   interoperability cost the protocol exists to avoid.
4. A collision that does matter (two *peers* configured with the same interface
   parameters) is not a driver problem but a configuration one, and the server
   generator (`tools/gen-config.sh`) emits distinct values per interface.

This is recorded as a decision, not a gap: `SECURITY.md` row updated to say
"accepted, by design, see docs/LOCKING.md §5".
