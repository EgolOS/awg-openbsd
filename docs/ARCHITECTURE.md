# awg-openbsd architecture

Reference document for the `awg(4)` AmneziaWG kernel driver as carried in this
repository (OpenBSD 8.0 amd64 baseline, `src/if_awg.c` v1.2). All `path:line`
citations refer to the files in this repository as of commit `16288ee` — an
edit that adds or removes lines above a citation invalidates it, so re-check
the lines you touched; `src/if_awg.c` is 3953 lines,
`src/awg_noise.c` 1405, `src/if_awg.h` 174, `src/awg_noise.h` 215.

## 1. Overview and scope

`awg(4)` is a pseudo-network interface implementing AmneziaWG: the WireGuard
protocol (`Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s`, `src/awg_noise.h:45`) with
an obfuscation layer on top of the UDP framing. It is a clone of OpenBSD's
`wg(4)` driver with the obfuscation parameters of AmneziaWG 2.x (legacy) and
3.1 added. What it is not: it is not a userspace daemon (no `awg` helper
binary; configuration goes straight through `ifconfig(8)`), it does not
implement the WireGuard "cookie reply" semantics beyond the standard
`wg_cookie.c` maker/checker pair, and it does not implement any protocol
version above 3.1 (peer `p_protocol_version > 1` is rejected with
`EPFNOSUPPORT`, `src/if_awg.c:3301`).

A custom kernel is required because OpenBSD has no loadable network drivers:
`pseudo-device` entries in a kernel configuration select which drivers are
linked (`src/AWG.MP` is `include "arch/amd64/conf/GENERIC.MP"` +
`pseudo-device awg`), so using `awg(4)` means building and booting
`AWG.MP` (the installed kernel reports `AWG.MP#<n>` in `uname -v`). The
repository ships the result as a single patch, `awg-openbsd.patch`, generated
from `src/` by `generate-patch.sh`; a stale patch ships an older driver than
`src/`, which is why the generator has a `--check` mode.

## 2. Upstream base: `wg(4)` in OpenBSD 8.0

`if_wg.c` in OpenBSD 8.0 (the file this driver is copied from; its checksum is
pinned in `tools/upstream/8.0.sha256`) is organised as:

- **softc** — one `struct wg_softc` per interface: `ifnet`, siphash secret,
  rwlock, noise local identity, cookie checker, UDP socket pair, allowed-IP
  radix (ART) tree, peer hashtable, index hashtable, handshake queue,
  encap/decap rings, crypt/handshake taskqueues.
- **peers** — `struct wg_peer` keyed by Curve25519 public key in a siphash
  hashtable, plus a TAILQ for ordered iteration; each carries the noise
  remote, cookie maker, timers, endpoint, counters, stage/encap/decap queues
  and three index slots.
- **keypairs** — `struct wg_noise_keypair` in a three-slot ring (next,
  current, previous) inside the noise remote.
- **index table** — maps the 32-bit keypair index to the noise remote.
- **handshake queue** — serialises handshake messages onto a taskqueue.
- **encap/decap** — a parallel ring (crypt taskqueue, ncpus workers) feeding
  per-peer serial queues drained by the per-interface net taskqueue.
- **timers** — five timeouts driven by an event model
  (`wg_timers_event_*`).

The copy into `src/if_awg.c` renames every symbol `wg_*` → `awg_*`
(`tools/sync-check.py:26` `RENAMES` is the canonical mapping used for the
three-way diff) and keeps the same layout. What is **copied verbatim**:
`awg_peer_create` (`src/if_awg.c:510`), `awg_peer_lookup` (`:578`),
`awg_peer_destroy` (`:600`), the endpoint accessors (`:661`–`:713`),
`awg_aip_*` (`:725`–`:826`), `awg_socket_open/close/bind/unbind`
(`:829`–`:947`), `awg_tag_get` (`:1052`), the ring/queue primitives
`awg_ring_dequeue`/`awg_queue_dequeue` (`:2622`, `:2635`),
`awg_remote_get` (`:2648`) and the timers event/run functions
(`:1386`–`:1712`). What is **replaced**: `awg_send_buf` (`:999`) adds a
`MAXMCLBYTES` guard before `m_clget`/`m_align`; `awg_encap` (`:2176`) and
`awg_decap` (`:2302`) add prefix/padding/trailer handling; `awg_classify`
(`:2748`) replaces the fixed 4-byte type read with prefix+range
classification; `awg_ioctl_check` (`:3033`) extends the wg validation with
AWG bounds. What is **added**: the `awg_range` helpers (`:1072`–`:1097`),
the timing accessors (`:1100`–`:1140`), `awg_hp_init` (`:1164`),
`awg_random_trailer`/`awg_content_padding` (`:1179`, `:1187`),
`awg_peer_update_udp_window` (`:1198`), the I1–I5 parser
`awg_ispec_parse`/`awg_ispec_free` (`:1230`, `:1345`), `awg_send_ispecs`
(`:1789`), `awg_send_junk` (`:1763`), `awg_reset_3_1` (`:3152`).
`src/awg_noise.c` is a copy of `sys/net/wg_noise.c` with the fixed
`REKEY_AFTER_TIME`/`REJECT_AFTER_TIME` constants turned into per-interface
ranges; `sys/net/wg_cookie.c` is *not* copied — `src/if_awg.c:49` includes
`<net/wg_cookie.h>` and links the stock `wg_cookie.c` (see §11).

## 3. AmneziaWG differences (obfuscation layer)

Two protocol profiles, selected per interface by `awgversion`
(`AWG_VERSION_LEGACY`=0, `AWG_VERSION_3_1`=1, `src/if_awg.h:43-44`; default
legacy, `src/if_awg.c:3805`). Legacy requires `r_lo == r_hi` for the H ranges
(`:3054`) and rejects all 3.1-only flags (`AWG_INTERFACE_HAS_3_1`, bits 9–24,
`:3046`).

| Parameter | Where it is applied on the wire | Where in the source |
|---|---|---|
| `Jc`/`Jmin`/`Jmax` | `Jc` random junk datagrams of `[Jmin,Jmax]` bytes sent before the initiation packet | `awg_send_junk()` `src/if_awg.c:1763`, called from `awg_send_initiation()` `:1881`; bounds `AWG_MAX_JUNK`=64, `AWG_MAX_JUNK_SIZE`=1500 `:85-86`, validated `:3094-3107` |
| `S1`–`S4` | random prefix prepended to initiation/response/cookie/transport messages | `awg_send_hs(..., sc_awg_s1/s2/s3)` `:1884`,`:1910`,`:1928`; `awg_encap` `s4` `:2187`,`:2240-2242`; stripped in `awg_input` via `awg_classify` padding `:2865`; bound `AWG_MAX_PADDING`=1280 `:84` |
| `H1`–`H4` | 32-bit little-endian message-type field; legacy = single values 1–4, 3.1 = inclusive ranges | `pkt.t = htole32(awg_range_pick(sc_awg_hN))` `:1875`,`:1906`,`:1922`,`:2245`; matched by `awg_range_contains` in `awg_classify` `:2786`,`:2796`; defaults 1/2/3/4 `:3802-3805`; non-overlap enforced `:3061-3065` |
| `I1`–`I5` | up to five signature datagrams sent before the junk burst, built from a tag grammar `<b 0xHEX><c><t><r N><rc N><rd N>` | `awg_ispec_parse()` `:1230` (two-pass, `AWG_ISPEC_MAXLEN`=8192), `awg_send_ispecs()` `:1789` (3.1 only, `:1799`), storage `sc_awg_ispec[]` under `sc_ispec_lock` `:354-355` |
| `HeaderProtectionKey` | ChaCha20 (IETF 96-bit nonce, counter 0) keystream keyed with HPK; the nonce is the first 12 bytes of the S1–S4 prefix; encrypts the message header | `awg_hp_init()` `:1164` (64-bit-nonce `chacha_private.h` quirk: first nonce word goes into the upper counter half `:1171-1173`); applied to the first 4 keystream bytes in `awg_classify` `:2771-2778`, the whole header in `awg_input` `:2869-2873`, the transport header in `awg_encap` `:2261-2265`; all-zero key disables HP `:3410-3416`; requires `S* >= 12` `:3142-3145` |
| `ContentPaddingAddition` | extra plaintext padding inside the transport message, drawn from a range and clamped to the peer's UDP window | `awg_content_padding()` `:1187`, chosen in `awg_encap` `:2200-2206`; validated `:3077-3079` |
| `RandomTrailers` | random bytes appended after the message (handshakes and transport), keeping the datagram inside the UDP window | `awg_random_trailer()` `:1179`; handshake path `awg_send_hs` `:1740-1748`, transport fallback `awg_encap` `:2202-2203`; keepalive-with-padding detection `awg_decap` `:2356-2357` |
| `DisableCookies` | the responder never emits a cookie-request packet, so the initiator's messages are accepted without the ratelimiting challenge | `awg_handshake` `:1984-1986` forces `underload = 0`, so `cookie_checker_validate_macs` never returns `EAGAIN`; consequence: every queued initiation costs an X25519 (`SECURITY.md` threat model) |
| Configurable timings (`RekeyAfterTime`, `RekeyTimeout`, `RejectAfterTime`, `KeepaliveTimeout`, `MaxHandshakeAttempts`, `PersistentKeepalive` range) | rekey/retry/keepalive pacing of handshake and transport traffic | accessors `awg_rekey_timeout`…`awg_max_handshake_attempts` `:1100-1140`; pushed into noise via `awg_update_noise_timings` `:1143` → `awg_noise_local_set_timings` `src/awg_noise.c:104`; consumed by `awg_noise_rekey_after_time[_recv]`/`reject_after_time` `src/awg_noise.c:996-1027`; 3.1 `p_pka_hi` range `src/if_awg.h:89`, applied `:3340-3342` |

## 4. Receive path

`awg_bind` (`src/if_awg.c:900`) creates an AF_INET and an AF_INET6
`SOCK_DGRAM` socket (`awg_socket_open` `:829`, `socreate` `:868`) and installs
`awg_input` as the UDP pcb upcall (`inp_upcall` `:872`). The kernel's
`udp_input` (in `sys/netinet/udp_usrreq.c`) calls that upcall with the
datagram, its IP header and UDP header.

1. `awg_input` (`:2806`, NET_LOCK asserted `:2820`) attaches an `awg_tag`
   (`PACKET_TAG_WIREGUARD` mbuf tag, `awg_tag_get` `:1052`) carrying the
   remote/local endpoint built from the IP+UDP headers (`:2827-2844`), strips
   the IP header (`m_adj(m, hlen)` `:2847`) and makes the datagram contiguous
   (`m_pullup` `:2855`).
2. `awg_classify` (`:2748`) determines the message type from the datagram
   length, the S1–S4 prefix sizes and the H1–H4 ranges; with HP it first
   copies the 12-byte nonce and decrypts the 4-byte type with the keystream
   (`:2771-2786`). Handshake types match exactly (`len == s[i] + hs[i].size`,
   `len >` also allowed with trailers, `:2781-2782`); data matches
   `len >= s[3] + AWG_MIN_DATA_SIZE` (`:2793`). Type 0 → `ifc_ierrors` and
   free (`:2858-2862`).
3. The prefix is stripped (`m_adj(m, padding)` `:2865`), a handshake's random
   trailer is trimmed (`:2866-2867`), and the header is HP-decrypted with the
   same nonce (`:2869-2873`).
4. Handshake messages (types 1–3) go on the serial `sc_handshake_queue`
   (`mq_enqueue` `:2877`, drop counted in `ifc_ierrors` `:2878`) and
   `sc_handshake` is queued on `awg_handshake_taskq` (`:2882`);
   `awg_handshake_worker` (`:2144`) drains it into `awg_handshake` (`:1973`).
5. Data messages (type 4) resolve the peer via `awg_index_get(sc, data->r_idx)`
   (`:2886`); the tag gets `t_peer/t_mbuf/t_done/t_type` (`:2887-2890`) and
   `awg_queue_in` (`:2545`) inserts the mbuf into the peer's serial
   `p_decap_queue` and the shared `sc_decap_ring` (both bounded by
   `MAX_QUEUED_PKT`=1024; overflow frees the mbuf and counts `ifc_iqdrops`
   `:2893`, ring-full marks `t_done` `:2568-2570`), then `sc_decap` is queued
   on `awg_crypt_taskq` (`:2895`).
6. `awg_decap_worker` (`:2447`) → `awg_decap` (`:2302`): decrypts in place
   with `awg_noise_remote_decrypt` (`:2329`), trims header and auth tag
   (`m_adj` `:2346-2347`), records the peer's UDP window (`:2349`), treats a
   leading zero byte as a padded keepalive (`:2356`), counts
   `ifc_ipackets/ifc_ibytes` (`:2359`), checks the source address against the
   allowed-IP tree (`awg_aip_lookup` `:2392`/`:2402`, mismatch counted as
   warning + drop `:2412-2418`), clears `csum_flags` and sets `ph_family`,
   `ph_ifidx`, `ph_rtableid` (`:2421-2425`), then marks `t_done` and queues
   `p_deliver_in` on `net_tq(if_index)` (`:2431-2434`).
7. `awg_deliver_in` (`:2495`) drains the serial queue in order (packets whose
   `t_done` is not set stay at the head, `awg_queue_dequeue` `:2635-2645`),
   counts `ifc_ierrors` for failed decryptions (`:2505`), taps bpf
   (`bpf_mtap_af` `:2525`), and calls `ipv4_input`/`ipv6_input` under
   NET_LOCK (`:2529-2538`); `awg_timers_event_data_received` (`:2540`)
   schedules the follow-up keepalive.

## 5. Transmit path

1. `awg_output` (`:2950`, NET_LOCK asserted) attaches the tag, looks the
   destination up in the ART trees (`awg_aip_lookup` `:2967`/`:2971`),
   requires a configured/discovered endpoint (`:2984-2990`), bounds
   `ph_loopcnt` (`:2992`) and rejects pf-delayed packets (`:3005`), then
   `if_enqueue(ifp, m)` (`:3021`) puts the packet on `if_snd`.
2. `awg_qstart` (`:2906`, serialised) drains `if_snd`, taps bpf OUT
   (`:2927-2929`), pushes each packet onto the peer's `p_stage_queue`
   (overflow → `ifc_oqdrops` `:2933`) and builds a temporary `start_list` so
   each peer is serviced once; peers with a ready keypair get
   `awg_queue_out` (`:2941`), others get `awg_timers_event_want_initiation`
   (`:2943`); finally `sc_encap` is queued on `awg_crypt_taskq` (`:2946`).
3. `awg_queue_out` (`:2577`) `mq_delist`s the whole stage queue and moves the
   packets into `p_encap_queue` + `sc_encap_ring` (both `MAX_QUEUED_PKT`
   bounded; excess counted in `ifc_oqdrops` `:2617-2618`). It can race with
   `awg_qstart` from `awg_send_keepalive` (`:2586-2590`); that is safe
   because `awg_qstart` is serialised.
4. `awg_encap_worker` (`:2438`) → `awg_encap` (`:2176`): computes the
   transport padding (CPA, else RandomTrailers, else WireGuard 16-byte
   rounding, `:2198-2206`), refuses datagrams larger than `MAXMCLBYTES`
   (`:2217-2223`), allocates a fresh cluster (`m_clget` `:2235`), writes the
   S4 prefix, copies the plaintext, sets the H4 type (`:2240-2245`), copies
   the flowid/prio (`:2252-2254`), encrypts with
   `awg_noise_remote_encrypt` (`:2256`, `ESTALE` triggers
   `awg_timers_event_want_initiation` `:2271`), HP-encrypts the transport
   header (`:2261-2265`), counts peer tx bytes (`:2293`) and stores the
   encrypted mbuf in `t->t_mbuf` (`:2295`).
5. `awg_deliver_out` (`:2456`, on `net_tq`) drains `p_encap_queue` in order,
   frees failed encryptions (`ifc_oerrors` `:2470`), and hands `t_mbuf` to
   `awg_send` (`:2475`) → `sosend` on `sc_so4`/`sc_so6` under `sc_so_lock`
   (`:981-993`), with `IP_SENDSRCADDR`/`IPV6_PKTINFO` control to pin the
   source address (`:956-967`); `EADDRNOTAVAIL` clears the cached source and
   re-reads the endpoint (`:2485-2488`). Successful sends fire the
   any-authenticated-packet/data-sent timer events (`:2478-2484`).

## 6. Handshake

- **Initiation** (`awg_send_initiation` `:1857`): `awg_noise_create_initiation`
  (`src/awg_noise.c:232`) runs the Noise IKpsk2 exchange (ephemeral X25519,
  `awg_noise_kdf` `:811`, `awg_noise_mix_dh` `:859`), the H1 type is picked
  from the range (`:1875`), the cookie maker MACs the message (`:1876`), then
  I1–I5 (`:1879`) and junk (`:1881`) precede the S1-prefixed packet
  (`:1884`). The local index is published in the index table via the noise
  upcall `awg_index_set` (`src/if_awg.c:2658`), which takes `sc_index_mtx`
  and re-draws `arc4random()` keys on collision (`:2677-2684`).
- **Response** (`awg_send_response` `:1889`): `awg_noise_create_response`
  (`src/awg_noise.c:361`) + `awg_noise_remote_begin_session` (`:467`) derive
  the keypair; the H2 type and S2 prefix wrap it (`src/if_awg.c:1906-1910`).
- **Cookie** (`awg_send_cookie` `:1914`): `cookie_checker_create_payload`
  (`:1925`, stock `wg_cookie.c`) builds the encrypted cookie; H3/S3 wrap it
  (`:1922`,`:1928`). The responder's underload hysteresis
  (`sc_awg_last_underload`, `MAX_QUEUED_HANDSHAKES/8` watermark,
  `ratecheck` with `awg_underload_interval`) is at `:1987-1996`;
  `DisableCookies` pins `underload` to 0 (`:1984-1986`), i.e. it disables the
  *issuing* of cookie requests (and with it the per-address ratelimiting),
  not the cookie machinery itself — a peer that still sends a cookie is
  answered via `cookie_maker_consume_payload` (`:2119`).
- **Consuming** (`awg_handshake` `:1973`): initiation →
  `cookie_checker_validate_macs` (`:2004`) then
  `awg_noise_consume_initiation` (`src/awg_noise.c:284`, looks the peer up by
  `s_idx` through the `u_remote_get` upcall → `awg_remote_get`
  `src/if_awg.c:2648`); response → `awg_index_get` (`:276`) then
  `awg_noise_consume_response` (`src/awg_noise.c:408`) +
  `awg_noise_remote_begin_session` (`:467`), which installs the derived
  keypair into the ring and drops the handshake index
  (`awg_noise_remote_keypair_free`/`allocate` `:723`/`:714`, ring of 3
  `r_keypair[]` `src/awg_noise.h:114`).
- **Index table**: `HASHTABLE_INDEX_SIZE` = 1<<13 buckets
  (`src/if_awg.c:73`), entries are the per-peer `p_index[3]` slots
  (`:271-272`) taken from `p_unused_index`; `awg_index_drop` (`:2711`)
  tolerates a double drop (`:2727-2733`).
- **Timers** (`awg_timers_*` `:1386-1712`): five timeouts
  (`t_new_handshake`, `t_send_keepalive`, `t_retry_handshake`,
  `t_zero_key_material`, `t_persistent_keepalive` `:211-215`). The
  arithmetic with ranges: new-handshake = `awg_new_handshake_timeout`
  (KeepaliveTimeout.hi + RekeyTimeout pick) + `REKEY_TIMEOUT_JITTER`
  (`:1484-1489`); retry = RekeyTimeout pick + jitter (`:1540-1545`), giving
  up after `awg_max_handshake_attempts` picks (`:1630`, `:1652-1656`);
  key-material zeroing = `awg_keychain_expire_time` × 3
  (`:1583-1584`, `:1655-1656`); the noise layer draws the actual
  RekeyAfterTime/RejectAfterTime per keypair from the ranges
  (`src/awg_noise.c:996-1027`), with `REKEY_AFTER_TIME_RECV =
  RejectAfterTime − (KeepaliveTimeout + RekeyTimeout)` (`:1008-1020`,
  `src/if_awg.c:1148-1154`; `l_recv_margin` is 32-bit so the sum of two
  16-bit ranges cannot truncate, `src/awg_noise.h:136-140`).

## 7. Configuration path

`SIOCSAWG` (223) / `SIOCGAWG` (224) are `_IOWR('i', …)` on
`struct awg_data_io` (`src/if_awg.h:39-40`). The numbers are 223/224 rather
than wg's 212/213 so a stale `ifconfig(8)` gets `ENOTTY` when the structure
layout changes (`src/if_awg.h:34-38`).

- **Layout**: `awg_data_io` (name + size + pointer) → `awg_interface_io`
  (flags bitmap `AWG_INTERFACE_HAS_*` bits 0–20, port, rtable, keypair, and
  the AWG parameter fields, with a flexible `i_peers[]`) → `awg_peer_io`
  (flags `AWG_PEER_*`, public, psk, pka range, endpoint, counters,
  description, flexible `p_aips[]`) → `awg_aip_io` (af, cidr, address union)
  (`src/if_awg.h:60-172`). I1–I5 are userland *pointers* (`i_ispec[]` +
  `i_ispec_len[]`, `src/if_awg.h:158-163`) — set uses `copyinstr`, get uses
  `copyout` into caller buffers.
- **Kernel**: `awg_ioctl` (`src/if_awg.c:3632`) drops NET_LOCK around
  `awg_ioctl_set`/`awg_ioctl_get` (`:3640-3647`). `awg_ioctl_set` (`:3191`)
  requires `suser` (`:3209`), takes `sc_lock` in write mode (`:3213`),
  `copyin`s the interface struct (`:3216`), validates everything up front via
  `awg_ioctl_check` (`:3219`; range sanity, H non-overlap, S ≤
  `AWG_MAX_PADDING`, Jc/Jmin/Jmax, timing margin `rj.lo > ka.lo + rt.lo`
  `:3116-3134`, HPK nonce fit `:3142-3145`), parses I1–I5 (`:3225-3235`),
  then applies peers (`:3283-3370`) and obfuscation parameters by flag bits
  (`:3372-3445`), and `explicit_bzero`s the staging copies (`:3453-3457`).
  `awg_ioctl_get` (`:3462`) uses the size-negotiation protocol: caller passes
  `awgd_size`, kernel returns the needed size (`:3478-3479`, `:3507-3508`,
  `:3626-3627`); non-root gets only port/rtable (`:3496-3497`).
- **Userspace**: `sbin/ifconfig/ifconfig_awg.patch` (826 lines against
  OpenBSD 8.0 `ifconfig.c`) adds `A_AMNEZIAWG` command-classification
  (`#define A_AMNEZIAWG 0x0080`), the `awgpeer/awgendpoint/awgaip/awgpsk/
  awgpka/awgport/awgkey/awgrtable/awgjc/…/awgdisablecookies` keywords,
  `process_awg_commands()` which issues one `SIOCSAWG` per ifconfig run, and
  `awg_status()` which issues the `SIOCGAWG` size-probe loop. Keys accept `-`
  to read base64 from stdin (`AWG_LOAD_KEY` macro) so the private key need
  not sit in the argument vector.
- **Pledge group** (`sys/kern/kern_pledge.c`): stock OpenBSD names neither
  `SIOCSAWG` (223) nor `SIOCGAWG` (224) in the `PLEDGE_INET` ioctl switch, so
  `sys_ioctl(2)` charges both against `PLEDGE_TTY` and a pledged binary is
  SIGABRTed on the first ioctl. The hunk adds both cases (plus
  `#include <net/if_awg.h>` for the constants) and is part of the kernel side:
  `generate-patch.sh` emits it from `src/kern_pledge.c.orig` →
  `src/kern_pledge.c` so `awg-openbsd.patch` carries it, and
  `install.sh:install_pledge_hunk` applies it before `config AWG.MP`
  (idempotent, baseline-verified against `tools/upstream/<ver>.sha256`).
  Without it `awgctl` cannot pledge (docs/SECURITY-AUDIT.md AWG-038).
- **`sbin/awgctl`** (standalone management tool, Option B): one
  `awg_data_io` per command. `up` serialises the whole `[Peer]` list with
  `i_peers_count` set and `AWG_INTERFACE_REPLACE_PEERS`, so the file is
  authoritative for the peer set; `set` reads the current state with
  `SIOCGAWG` first so a partial command keeps the siblings of the grouped
  fields (Jc/Jmin/Jmax, S1/S2, H1–H4); `add`/`del` carry one peer. Key
  material comes from the config file, from argv, or from stdin with `-`.
  Interface create/destroy shells out to stock `ifconfig(8)` (SIOCIFCREATE2).
- **`sbin/awg-quick`** (832 lines, bash; installed to
  `/usr/local/sbin/awg-quick` by `install.sh`) parses an AmneziaWG `.conf`
  (`[Interface]` incl. all AWG/3.1 keys, `[Peer]` incl. multiple peers),
  creates the interface (`ifconfig awgN create`, `awg-quick:307-324`),
  assembles one `ifconfig` invocation with `awgversion/awgkey -/awgport/
  awgjc…/awgi1…/awghpk/awgpeer/awgendpoint/awgaip` (`:341-407`), feeds the
  private key on stdin (`:407`), sets address/MTU (`:419-425`, MTU = underlay
  MTU − 80, `:454`), installs routes and runs pre/post up/down hooks, and can
  `get` an existing config back from the kernel by parsing `ifconfig` output
  (`:718-767`).

## 8. Memory ownership

- **softc** (`src/if_awg.c:282`): `malloc(M_DEVBUF)` in `awg_clone_create`
  (`:3774`), freed in `awg_clone_destroy` (`:3927`) after `explicit_bzero` of
  the HPK (`:3926`). Owns the ART trees (`art_alloc` `:3816`/`:3819`, freed
  `:3920-3922`), the peer hashtable (`hashinit` `:3825`, `hashfree` `:3918`),
  the index hashtable (`:3830`, `:3917`), the two taskqueues (created on first
  clone `:3756-3771`, destroyed when the last interface dies `:3906-3913`),
  and the UDP sockets (`awg_bind`/`awg_unbind`, `soclose` `:894`).
- **peer** (`:243`): `awg_peer_pool` (`pool_init` `:3941`, `pool_get` `:520`,
  `pool_put` after `explicit_bzero` `:656-657`). `awg_peer_destroy` (`:600`)
  removes it from the hashtable/seq lists, disables timers, then barriers
  `awg_handshake_taskq`, drains `if_snd`, barriers `awg_crypt_taskq` and
  `net_tq` (`:620-650`) so no queued task references it before the free.
- **keypair**: statically embedded `r_keypair[3]` in the noise remote
  (`src/awg_noise.h:114`); the ring (`r_next/r_current/r_previous`) is
  maintained through `r_unused_keypairs` — no per-keypair allocation.
- **index**: embedded `p_index[3]` in the peer (`src/if_awg.c:272`), linked
  into the global hashtable; no separate allocation.
- **ispec** (`:187`): `is_desc`/`is_pkt`/`is_mods` `malloc(M_DEVBUF)` in
  `awg_ispec_parse` (`:1248-1251`, `:1327`), freed by `awg_ispec_free`
  (`:1345`) — called on replacement (`:3444`), on version reset (`:3176`),
  and at destroy (`:3924-3925`).
- **mbuf tag** (`:159`): `awg_tag_get` reuses an existing
  `PACKET_TAG_WIREGUARD` tag or prepends one (`:1056-1062`); it lives as long
  as the mbuf. The encap path allocates a *new* mbuf (`m_clget` `:2235`) and
  the original is freed by `awg_deliver_out` (`:2490`); decap decrypts in
  place (`t_mbuf == m`, `:2431`, `KASSERT` `:2511`).
- **Allocation bounds**: `AWG_MAX_PADDING`=1280 (S1–S4),
  `AWG_MAX_JUNK`=64 (Jc), `AWG_MAX_JUNK_SIZE`=1500 (junk buffer, `:1766`),
  `AWG_MAX_UDP_PAYLOAD`=65507 (I-packet parser `:1305`,`:1312`),
  `MAXMCLBYTES` guards at `:1012` (send_buf) and `:2217` (encap),
  `AWG_MAX_UDP_WINDOW`=1500 clamps the peer padding window (`:1205-1209`),
  `MAX_QUEUED_PKT`=1024 / `MAX_STAGED_PKT`=128 /
  `MAX_QUEUED_HANDSHAKES`=4096 bound the queues, `MAX_PEERS_PER_IFACE`=1<<20
  bounds peers (`:517`).

## 9. Locking and concurrency

- `sc_lock` (rwlock, `:286`): coarse lock for the config surface —
  `awg_ioctl_set` takes it in write (`:3213`), `awg_ioctl_get`/`awg_up`/
  `awg_down` in read (`:3484`, `:3693`, `:3730`).
- `sc_so_lock` (rwlock, `:292`): guards `sc_so4`/`sc_so6`; taken read around
  `sosend` (`:981`) and write around rebind (`:924`).
- `sc_aip_lock` (rwlock, `:298`) + SMR: the ART trees are mutated under the
  write lock (`:753`, `:807`); `awg_aip_lookup` reads with
  `smr_read_enter/leave` (`:779-781`) — lock-free on the packet path.
- `sc_peer_lock` (rwlock, `:305`): guards the peer hashtable and seq TAILQ
  (`:567`, `:612`).
- `sc_index_mtx` (mutex, `:311`): guards the index hashtable. `awg_index_set`
  mutates `p_unused_index` without a lock because set/drop are serialised per
  remote (`:2665-2672`); `awg_index_get` is lock-free for the keypair index
  read path (`:2700-2706`).
- Per-peer: `p_counters_mtx` (`:253`), `p_endpoint_mtx` (`:257`),
  `p_encap_queue.q_mtx`/`p_decap_queue.q_mtx` (`:232`), `p_stage_queue`
  mutex; noise: `r_handshake_lock` (rwlock) and `r_keypair_mtx`
  (`src/awg_noise.h:105`,`:111`); timers: `t_mtx` (serialises
  `t_disabled`/timeout adds) and `t_handshake_mtx` (`:205`,`:217`).
- `sc_ispec_lock` (rwlock, `:354`): guards the I1–I5 strings; read by
  `awg_send_ispecs` (`:1805`), written on config (`:3440`) and version reset
  (`:3174`).
- NET_LOCK is dropped around the blocking socket work (`awg_up` `:3691`,
  `awg_ioctl` `:3640`) and taken around `ipv4_input`/`ipv6_input`
  (`:2529`). Taskqueues: `awg_handshake_taskq` (2 workers),
  `awg_crypt_taskq` (`ncpus`), `net_tq(if_index)` (`:3757-3760`).
- **Unlocked reads on the packet path**: the whole `sc_awg_*` obfuscation
  block (`:326-352`) and the noise timings (`src/awg_noise.c:108`) are plain
  scalars read without `sc_lock` by `awg_classify`, `awg_encap`,
  `awg_send_hs`, and the timers. This is accepted because each field is
  word-sized and independently meaningful: a packet that races a
  reconfiguration may be built with the old prefix and dropped by the new
  classifier, which is the same cost the upstream wg(4) accepts for its
  scalar config (`src/if_awg.c:326-329` comment).
- **Where a config change can race a packet**: (a) `awg_ioctl_set` holds
  `sc_lock` in write, so a packet in `awg_classify`/`awg_encap` that started
  earlier reads a torn *set* of (S, H) values — tolerated, the datagram is
  simply dropped; (b) `awg_bind` replaces the sockets under `sc_so_lock`
  while a queued `p_deliver_out` reads them under the same lock — safe, but
  a packet encrypted before the rebind is sent on the *new* socket; (c) peer
  removal (`awg_peer_destroy`) must outlive mbufs referencing it via
  `t_peer`, which is why the destroy path barriers the handshake and crypt
  taskqueues and the net taskqueue (`:620-650`); pf-delayed packets are
  therefore unsupported (`:2998-3009`).

## 10. Lifecycle

- **Create**: `awgattach` (`:3932`) runs at boot — `if_clone_attach(&awg_cloner)`
  (`:3937`) and the three `pool_init`s (`:3939-3944`). `awg_clone_create`
  (`:3748`, KERNEL_LOCK) creates the shared taskqueues on the first interface
  (`awg_counter` refcount `:3756-3772`), allocates and initialises the softc
  (siphash secret `:3785`, noise local `:3788`, cookie checker `:3789`,
  AWG defaults `:3798-3806`, ART trees, hashtables, queues/rings), then
  attaches the `ifnet` (`if_mtu` 1420 `:3851`, `IFF_BROADCAST|IFF_MULTICAST|
  IFF_NOARP` `:3852`, `if_txmit` 64 `:3854`, `if_ioctl`/`if_qstart`/
  `if_output` `:3856-3858`, `IFT_WIREGUARD` `:3860`, `if_attach` `:3864`,
  `if_alloc_sadl` `:3865`, bpf `:3868`).
- **Up/down**: `awg_up` (`:3677`) sets `IFF_RUNNING`, drops NET_LOCK, takes
  `sc_lock`, binds the UDP sockets and enables every peer's timers (which
  flushes staged packets and sends a keepalive) (`:3689-3711`). `awg_down`
  (`:3716`) purges stage queues, disables timers, barriers the handshake
  taskqueue, clears noise sessions and resets the last-handshake stamp, then
  unbinds (`:3723-3744`).
- **Destroy**: `awg_clone_destroy` (`:3890`) destroys all peers under
  `sc_lock`, unbinds, `if_detach`s, drops the taskqueue refcount, frees the
  hashtables/ART/ispecs/HPK and the softc (`:3898-3928`).
- **Version switch (legacy ↔ 3.1)**: setting `awgversion` to a different
  value runs `awg_reset_3_1` (`:3152`) before storing the new version
  (`:3373-3390`): S3/S4, the H range high ends, HPK, CPA, all five timing
  ranges, RandomTrailers and DisableCookies are reset to WireGuard defaults,
  the I1–I5 set is replaced (the *stored* set is freed, not the freshly
  parsed one, `:3378-3388`), and every peer's PersistentKeepalive high end is
  collapsed to the low end (`:3182-3187`). What *survives* the switch: the
  private key, port/rtable, peers, allowed IPs, descriptions, counters, and
  the legacy parameters `Jc/Jmin/Jmax/S1/S2/H1–H4` (H collapses to its low
  end). Existing keypairs are not flushed by the switch itself; the peer
  keeps its current session until it rekeys.

## 11. Component dependency diagram

```mermaid
graph TD
    subgraph kernel["kernel (built from AWG.MP)"]
        IFW["if_awg.c<br/>src/if_awg.c"]
        NOI["awg_noise.c<br/>src/awg_noise.c"]
        COO["wg_cookie.c (stock, not copied)"]
        B2["blake2s.c · curve25519.c · chachapoly.c (stock)"]
        CFG["conf/files<br/>awg-openbsd.patch"]
        AWGMP["src/AWG.MP<br/>include GENERIC.MP + pseudo-device awg"]
    end
    subgraph user["userspace"]
        IFC["sbin/ifconfig<br/>+ ifconfig_awg.patch"]
        Q["sbin/awg-quick (bash)"]
        INST["install.sh"]
        GEN["generate-patch.sh"]
        SYNC["tools/sync-check.py<br/>tools/upstream/8.0.sha256"]
        LAB["tools/lab/{smoke,wire}-test.sh<br/>start_vm.sh, install_vm.py, build-in-vm.sh"]
    end
    IFW -->|#include net/awg_noise.h| NOI
    IFW -->|#include net/wg_cookie.h| COO
    IFW -->|SIPHASH_KEY| B2
    NOI -->|blake2s/chachapoly/curve25519| B2
    CFG -->|pseudo-device awg: ifnet| IFW
    CFG -->|file net/if_awg.c, net/awg_noise.c| IFW
    CFG -->|file net/awg_noise.c| NOI
    CFG -.->|wg \| awg attribute: shared sources also compiled for awg| COO
    AWGMP -->|include| CFG
    IFC -->|SIOCSAWG/SIOCGAWG 223/224 via if_awg.h| IFW
    Q -->|execs ifconfig with awg* keywords| IFC
    INST -->|applies patch, builds kernel + ifconfig, installs awg-quick| IFC
    GEN -->|regenerates awg-openbsd.patch from src/| CFG
    SYNC -->|3-way diff stock if_wg.c vs if_awg.c| IFW
    LAB -->|runs on VM booted with AWG.MP| AWGMP
```

Direction notes: the dependency is one-way — userspace (`ifconfig`,
`awg-quick`) knows the kernel ABI (`if_awg.h`) but the kernel knows nothing
about the tools; `install.sh` is the only component that touches both the
kernel build and the userspace install; `tools/sync-check.py` reads the
*stock* `if_wg.c` (checksum-pinned in `tools/upstream/8.0.sha256`) against
this repo's `if_awg.c`, so it depends on the source tree, not the build.

## 12. Maintenance risks

| # | Risk | Where | Why it hurts at the next release |
|---|---|---|---|
| 1 | Copied functions drift upstream | `awg_peer_create/lookup/destroy` (`src/if_awg.c:510,578,600`), socket layer (`:829-947`), ring/queue (`:2545-2645`), timers (`:1386-1712`) — listed as "straight copy" in `docs/MAINTENANCE.md` §2 | Every OpenBSD release (May/Nov) rewrites parts of `if_wg.c`; `tools/sync-check.py` classifies the delta, and any function we also changed (`!`) needs a hand merge. The AWG block (`:326-355`) has no upstream counterpart, so upstream field additions must be re-added by hand. |
| 2 | `conf/files` `wg \| awg` attribute trick | `awg-openbsd.patch` (`sys/conf/files` hunk): `file net/if_awg.c awg`, `file net/awg_noise.c awg`, and `net/wg_cookie.c`, `crypto/blake2s.c`, `crypto/curve25519.c`, `crypto/chachapoly.c` marked `wg \| awg` | The pipe means "compile for wg *or* awg". A kernel selecting both pseudo-devices links `wg_cookie.c` and the crypto files once for both; a future release that changes the attribute grammar, splits `wg_cookie.c`, or renames a crypto file breaks the single-file patch and the `AWG.MP` build in lockstep with `if_wg.c`. |
| 3 | ifconfig patch context | `sbin/ifconfig/ifconfig_awg.patch` (826 lines against 8.0 `ifconfig.c`, header timestamped Oct 2026) | The patch anchors on `if_wg.h` includes, the `wg*` keyword table and `wg_status`; upstream additions to any of those (new wg keywords, `A_WIREGUARD` handling) shift the context and the patch stops applying, forcing a re-roll against each release's sources (`install.sh` fetches sources matching `uname -r`). |
| 4 | ioctl ABI | `SIOCSAWG`/`SIOCGAWG` 223/224, `src/if_awg.h:39-40`; flexible-array layout `awg_interface_io → awg_peer_io → awg_aip_io` | The 3.1 fields changed the structure layout, so kernel and ifconfig must be rebuilt together; the number bump to 223/224 converts a mismatch into `ENOTTY` (README: old ifconfig + new kernel = "Inappropriate ioctl for device"). Any future field addition requires another number bump, invalidating cached configs on disk. |
| 5 | No userspace test for the ioctl contract | `tools/lab/smoke-test.sh` (VM, loopback handshake + bounds), `tools/lab/wire-test.sh` (malformed datagrams) | Both tests exercise the *running* kernel with the *matching* ifconfig; nothing pins the byte layout of `awg_data_io`/`awg_peer_io`/`awg_aip_io` (e.g. a compile-time `static_assert` on `sizeof`/`offsetof`, or a golden `SIOCGAWG` round-trip). A layout regression that keeps `awg(4)` working can silently change what `ifconfig awg0` prints and what `awg-quick get` parses (`sbin/awg-quick:718-767`). |
| 6 | Single generated patch | `awg-openbsd.patch` (5770 lines) regenerated by `generate-patch.sh` | The patch embeds a timestamped header and the whole `src/`; forgetting `generate-patch.sh` (or its `--check` gate) ships a driver older than `src/`. |
| 7 | Shared taskqueues refcount | `awg_counter` (`src/if_awg.c:502`, `:3756-3772`, `:3906-3913`) | The two taskqueues are process-global keyed by a plain counter; changes to `if_clone` semantics or new destroy paths must keep create/destroy balanced (`KASSERT` at `:3908`). |
