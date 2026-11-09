# AmneziaWG for OpenBSD

Native kernel driver (`awg(4)`) for [AmneziaWG](https://github.com/amnezia-vpn/amneziawg-linux-kernel-module) — an obfuscated WireGuard fork — targeting OpenBSD 8.0 amd64 (7.9 has historical compile-only evidence).

OpenBSD ships WireGuard (`wg(4)`) built into GENERIC. This project adds a parallel `awg(4)` pseudo-device to a custom kernel, providing the AmneziaWG obfuscation that is used on Linux and Windows, without any userspace daemon.

Two protocol versions are supported, selected per interface (see [Protocol version](#protocol-version)):

- **legacy** (AmneziaWG < 3.1, the default): the 9 classic parameters `Jc`, `Jmin`, `Jmax`, `S1`, `S2`, `H1`–`H4`.
- **3.1** (AmneziaWG ≥ 3.1): additionally `S3`, `S4`, `H1`–`H4` ranges, `I1`–`I5`, `HeaderProtectionKey`, `ContentPaddingAddition`, session timings, `RandomTrailers`, `DisableCookies` and `PersistentKeepalive` ranges.

AWG parameters are configured directly through `ifconfig(8)` — the same tool used for standard WireGuard on OpenBSD — with no external helper binary required.

## Release status

**READY FOR A RELEASE CANDIDATE**, with the claims narrowed to what was actually
executed. Verified on disposable OpenBSD 8.0 amd64 guests: an unprivileged kernel
build from signature-verified sources boots and passes the repository gates; the
tunnel interoperates with the official `amneziawg-go` in both directions for the
legacy profile and for 3.1 (S3/S4 + header protection) with hash-verified 8 MiB
payloads; the installer separates build from install (AWG-014) and restores
`/bsd` + `/sbin/ifconfig` as a pair (AWG-029), rehearsed across six failure
scenarios. Not verified and not claimed: Android/Windows clients, the Linux
kernel module, IPv6 tunnel traffic, roaming/NAT/MTU matrices, long soak and fuzz,
and 1-vCPU throughput figures. Evidence and the full gate table:
[the engineering report](docs/ENGINEERING-REPORT.md).
The following install commands are for a disposable lab, not a remote-only server.

## Quick start

On a disposable OpenBSD 8.0 amd64 lab, after reviewing the recovery runbook:

```sh
doas pkg_add git bash
git clone https://github.com/EgolOS/awg-openbsd
cd awg-openbsd
doas sh install.sh --all     # kernel + patched ifconfig + header + awgctl (+ awgctl.8) + awg-quick, 35-50 min on 1 vCPU
doas reboot
uname -v                     # AWG.MP#<n>
doas sh tools/gen-config.sh --endpoint vpn.example.com:51820
```

The generator writes an exclusive **0600 `pair.conf`**, not keys to the terminal.
Use `--output /root/pair.conf` for a different new file; `--stdout` is deliberate
plaintext export (protect any redirection with `umask 077`). It generates legacy
IPv4 tunnel addresses, accepts bracketed IPv6 endpoints, and refuses a busy awg9.
Put the server half of that file in `/etc/amnezia/amneziawg/vpn.conf`
(mode 0600), `doas awg-quick up vpn`, and import the client half into the
AmneziaWG app. Full walkthrough including the firewall: [`docs/DEPLOY-vps.md`](docs/DEPLOY-vps.md).

This fork carries security fixes that are not in the upstream tree yet — see
[`SECURITY.md`](SECURITY.md) for the list and [`docs/TESTING.md`](docs/TESTING.md)
for how they are tested. The upstream pull request is
[#2](https://github.com/TrueBad0ur/awg-openbsd-kernel/pull/2).

## How it works

| Component | Role |
|-----------|------|
| `src/if_awg.c` | Kernel driver — clone of `if_wg.c` extended with AWG obfuscation |
| `src/if_awg.h` | Public ioctl interface (`SIOCSAWG`/`SIOCGAWG`, numbers 223/224) |
| `src/awg_noise.c`, `src/awg_noise.h` | Copy of `wg_noise` with configurable `RekeyAfterTime`/`RejectAfterTime` |
| `src/AWG.MP` | Kernel config (`include GENERIC.MP` + `pseudo-device awg`) |
| `sbin/ifconfig/ifconfig_awg.patch` | Patch for `/sbin/ifconfig` to add `awg*` flags |
| `sbin/awgctl/awgctl.c` | Management tool built and installed with the pair (`SIOCSAWG`/`SIOCGAWG`) |
| `sbin/awg-quick` | Shell script to bring tunnels up/down from a config file |
| `install.sh` | Build and install script |

Interfaces are named `awg0`, `awg1`, … Configs live in `/etc/amnezia/amneziawg/`.

## Requirements

- OpenBSD 8.0 amd64 baseline; 7.9 is compile-only historical, not installer/runtime support
- Root access
- Packages: `bash` — `install.sh` installs it automatically
- Kernel sources and ifconfig sources — downloaded from the OpenBSD CDN automatically if missing

## Installation

```sh
git clone https://github.com/EgolOS/awg-openbsd
cd awg-openbsd

# 1. Patch ifconfig + install awg-quick (fast, no reboot; no awgctl)
doas sh install.sh --tools-only

# 2. Build and install the AWG kernel + header (15 min on a fast machine,
#    35-50 min on a 1 vCPU VPS; requires reboot; no awgctl)
doas sh install.sh --kernel-only

# Or do everything in one shot (kernel + patched ifconfig + header +
# awgctl with its awgctl.8 manpage + awg-quick):
doas sh install.sh --all
```

After `--kernel-only` or `--all`, reboot. The bootloader loads `/bsd` automatically.

> **After `sysupgrade`:** the upgrade installs a stock `GENERIC.MP` kernel and stock `ifconfig`, so `awg(4)` is gone until you reinstall. Move the old release's sources aside (`/usr/src/sys`, `/usr/src/sbin/ifconfig`), run `install.sh --all` (it fetches sources matching `uname -r`) and reboot. `install.sh` refuses to build from sources of another release.

> **Upgrading from a release without AWG 3.1 support:** the ioctl interface between the kernel and `ifconfig(8)` changed, so rebuild both (`install.sh --all`) and reboot. Until the reboot, the new `ifconfig` cannot configure the running old kernel (and an old `ifconfig` cannot configure the new kernel): `SIOCSAWG` fails with `Inappropriate ioctl for device`.

```sh
reboot
```

Verify the kernel loaded and the device is available:

```sh
uname -v              # should show AWG.MP
ifconfig awg0 create  # should succeed
ifconfig awg0 destroy
```

## Usage

### Place your config

Config files go in `/etc/amnezia/amneziawg/`. The filename (without `.conf`) is the tunnel name.

```sh
doas mkdir -p /etc/amnezia/amneziawg
doas chmod 700 /etc/amnezia/amneziawg
doas cp myvpn.conf /etc/amnezia/amneziawg/
doas chown root:wheel /etc/amnezia/amneziawg/myvpn.conf
doas chmod 600 /etc/amnezia/amneziawg/myvpn.conf
```

Config format is standard WireGuard + AWG obfuscation fields:

```ini
[Interface]
PrivateKey = <base64>
Address = 10.8.0.21/24
# AWG obfuscation parameters (provided by your server admin)
Jc = 4
Jmin = 10
Jmax = 50
S1 = 26
S2 = 33
H1 = 1737146616
H2 = 705704376
H3 = 391058102
H4 = 2103979647

[Peer]
PublicKey = <base64>
PresharedKey = <base64>
Endpoint = vpn.example.com:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
```

Plain-WireGuard wire format is intended with junk/prefixes disabled and H1–H4
set to **1, 2, 3, 4**, not all zero. Stock wg(4) interoperability has not been
re-run in this engineering pass.

#### Protocol version

Configs generated by AmneziaWG 3.1 servers use parameters that older clients do not understand (and the other way round), so the protocol version is chosen per tunnel with `Version` in `[Interface]`:

| `Version` | Protocol | Accepted parameters |
|-----------|----------|---------------------|
| `legacy` | AmneziaWG < 3.1 | `Jc`, `Jmin`, `Jmax`, `S1`, `S2`, `H1`–`H4` (single values) |
| `3.1` | AmneziaWG ≥ 3.1 | all of the above plus the 3.1 parameters below, `H1`–`H4` as ranges |

Without `Version`, `awg-quick` picks `3.1` if the config uses any 3.1-only parameter and `legacy` otherwise, so existing configs keep working unchanged. `Version = legacy` together with 3.1 parameters is an error.

An AmneziaWG 3.1 config looks like this:

```ini
[Interface]
PrivateKey = <base64>
Address = 10.8.0.21/24
Jc = 4
Jmin = 10
Jmax = 50
S1 = 26
S2 = 33
S3 = 18
S4 = 24
H1 = 1737146616-1737146716
H2 = 705704376-705704476
H3 = 391058102-391058202
H4 = 2103979647-2103979747
I1 = <b 0xc700000001><r 16><t>
HeaderProtectionKey = <base64, generated with awg genkey>
ContentPaddingAddition = 0-32
RandomTrailers = on

[Peer]
PublicKey = <base64>
PresharedKey = <base64>
Endpoint = vpn.example.com:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 20-30
```

### Bring the tunnel up / down

```sh
doas awg-quick up myvpn
doas awg-quick down myvpn
```

If `AllowedIPs` contains IPv6 networks (e.g. `::/0`) but `Address` has no IPv6 address, `awg-quick` refuses to bring the tunnel up — see below.

`awg-quick` configures the tunnel entirely via `ifconfig`. If the config filename matches an `awgN` pattern (e.g. `awg0.conf`), that interface is used directly; otherwise the next free `awg0`, `awg1`, … is allocated.

The private key is never put on a command line: `awg-quick` feeds it to
`ifconfig awgN awgkey -` on stdin, because the argument vector of a root
process is readable with `ps(1)` and lands in core dumps. Pre-/post-up hooks
(`%i`/`%I` substitution) still run as root shell — treat a config file as a
root program: only copy configs you trust. The file must be root-owned,
`0600`/`0400`, and every canonical ancestor must be root-owned with no group/other
write permission. Prefer `/etc/amnezia/amneziawg/` mode `0700`. Private keys, HPKs
and PSKs all use stdin in automated configuration; manual argv examples below
remain available but can disclose secrets through process listings.

If `AllowedIPs` contains IPv6 networks (e.g. `::/0`) but `Address` has no IPv6
address, `awg-quick` refuses to bring the tunnel up: the route cannot be
installed and the traffic it was meant to cover would leave through the
physical interface. Add an IPv6 address, drop the `::/0` entry, or export
`AWG_ALLOW_IPV6_BYPASS=1` to accept the leak explicitly.

### Show tunnel status

```sh
doas awg-quick show          # all active awg interfaces
doas awg-quick show myvpn    # a specific tunnel
```

Output is `wg show`-style: listening port, public key, all AWG obfuscation parameters, and per-peer endpoint, allowed IPs, latest handshake, transfer counters and keepalive.

### Inspect the tunnel

```sh
ifconfig awg0
```

Output includes all AWG parameters:

```
awg0: flags=80c3<UP,BROADCAST,RUNNING,NOARP,MULTICAST> mtu 1420
    awgport 51820
    awgpubkey <base64>
    awgversion legacy
    awgjc 4 awgjmin 10 awgjmax 50
    awgs1 26 awgs2 33
    awgh1 1737146616 awgh2 705704376 awgh3 391058102 awgh4 2103979647
    awgpeer <base64>
        awgpsk (present)
        awgpka 25 (sec)
        awgendpoint 1.2.3.4 51820
        tx: 232408, rx: 67712
        last handshake: 3 seconds ago
        awgaip 0.0.0.0/0
    inet 10.8.0.21 netmask 0xffffff00 broadcast 10.8.0.255
```

### Manual configuration (without awg-quick)

Because AWG is fully integrated into `ifconfig`, you can configure interfaces manually:

```sh
ifconfig awg0 create
ifconfig awg0 awgkey -            # reads the base64 key from stdin (or: awgkey <base64>)
ifconfig awg0 awgport 51820
ifconfig awg0 awgjc 4 awgjmin 10 awgjmax 50
ifconfig awg0 awgs1 26 awgs2 33
ifconfig awg0 awgh1 1737146616 awgh2 705704376 awgh3 391058102 awgh4 2103979647
ifconfig awg0 awgpeer <peer-pubkey> awgpsk <psk> awgpka 25 \
         awgendpoint vpn.example.com 51820 awgaip 0.0.0.0/0
ifconfig awg0 inet 10.8.0.21/24
ifconfig awg0 up
```

AmneziaWG 3.1 parameters require `awgversion 3.1` (in the same or an earlier command):

```sh
ifconfig awg0 awgversion 3.1
ifconfig awg0 awgs3 18 awgs4 24
ifconfig awg0 awgh1 1737146616-1737146716 awgh2 705704376-705704476 \
         awgh3 391058102-391058202 awgh4 2103979647-2103979747
ifconfig awg0 awgi1 '<b 0xc700000001><r 16><t>'  # awgi1 .. awgi5, '' clears
ifconfig awg0 awghpk <base64>                     # -awghpk clears
ifconfig awg0 awgcpa 0-32
ifconfig awg0 awgrandomtrailers                   # -awgrandomtrailers
ifconfig awg0 awgdisablecookies                   # -awgdisablecookies
ifconfig awg0 awgrekeyaftertime 110-130 awgrekeytimeout 4-6 \
         awgrejectaftertime 170-190 awgkeepalivetimeout 9-11 \
         awgmaxhandshakeattempts 15-20
ifconfig awg0 awgpeer <peer-pubkey> awgpka 20-30
```

In legacy mode the kernel rejects all of these with `EINVAL`. Switching an interface back with `awgversion legacy` drops every 3.1 parameter (ranges collapse to their lower bound).

This also means `/etc/hostname.awg0` works natively.

### Auto-start on boot

Via `/etc/hostname.awg0`:

```
awgkey <private-key-base64>
awgport 51820
awgjc 4 awgjmin 10 awgjmax 50
awgs1 26 awgs2 33
awgh1 1737146616 awgh2 705704376 awgh3 391058102 awgh4 2103979647
awgpeer <pubkey> awgpsk <psk> awgpka 25 awgendpoint vpn.example.com 51820 awgaip 0.0.0.0/0
inet 10.8.0.21/24
up
```

Or via `/etc/rc.local`:

```sh
awg-quick up myvpn
```

### TCP MSS clamping

`awg-quick` does not configure TCP MSS clamping automatically. Without it, TCP packets may exceed the tunnel MTU and get fragmented.

`install.sh` prints the rule but does **not** touch your firewall unless you pass `--pf`:

```
match on awg scrub (max-mss 1380)
```

The value 1380 = tunnel MTU (1420) − IP header (20) − TCP header (20).

Add it yourself (recommended), or let the installer do it against an existing `/etc/pf.conf`:

```sh
echo 'match on awg scrub (max-mss 1380)' >> /etc/pf.conf
pfctl -f /etc/pf.conf
# or:
doas ./install.sh --tools-only --pf
```

## AWG obfuscation parameters

| Param | Meaning | Default (= plain WireGuard) |
|-------|---------|---------------------------|
| `Jc` | Number of junk packets sent before handshake | 0 |
| `Jmin` | Min size of each junk packet (bytes) | 0 |
| `Jmax` | Max size of each junk packet (bytes) | 0 |
| `S1` | Random prefix bytes prepended to Initiation packet | 0 |
| `S2` | Random prefix bytes prepended to Response packet | 0 |
| `H1` | Magic type header replacing packet type 1 (Initiation) | 1 |
| `H2` | Magic type header replacing packet type 2 (Response) | 2 |
| `H3` | Magic type header replacing packet type 3 (Cookie) | 3 |
| `H4` | Magic type header replacing packet type 4 (Data) | 4 |

`S1`, `S2` and `H1`–`H4` must match on both endpoints; junk packets (`Jc`, `Jmin`, `Jmax`) are dropped by the receiver and can differ.

AmneziaWG 3.1 only (`Version = 3.1`):

| Param | Meaning | Default |
|-------|---------|---------|
| `S3` | Random prefix bytes prepended to Cookie reply packet | 0 |
| `S4` | Random prefix bytes prepended to every Data packet | 0 |
| `H1`–`H4` | May be ranges `lo-hi`; each packet uses a random value from the range. Ranges must not overlap | — |
| `I1`–`I5` | Signature packets sent before the junk packets of each handshake. Tags: `<b 0xHEX>` bytes, `<r N>` random bytes, `<rc N>` random letters, `<rd N>` random digits, `<t>` unix time, `<c>` packet counter | none |
| `HeaderProtectionKey` | Encrypts the message headers with ChaCha20, nonce taken from the `S1`–`S4` prefix; requires `S1`–`S4` ≥ 12 | none |
| `ContentPaddingAddition` | Random zero padding (range) inside encrypted Data packets | none |
| `RandomTrailers` | Random tail after handshake packets, random padding in Data packets | off |
| `DisableCookies` | Never send cookie replies and never rate limit handshakes | off |
| `RekeyAfterTime`, `RekeyTimeout`, `RejectAfterTime`, `KeepaliveTimeout`, `MaxHandshakeAttempts` | WireGuard session timings (seconds / count), ranges | WireGuard values |
| `PersistentKeepalive` (peer) | May be a range | — |

`HeaderProtectionKey`, `S1`–`S4`, `H1`–`H4` and `RandomTrailers` must match on both endpoints; `I1`–`I5`, junk packets, `ContentPaddingAddition` and the timings are local.

`S4` (and any content padding) is added *outside* the tunnel MTU, so the
datagram that leaves the host is `mtu + S4 + padding` bytes. With a large `S4`
lower the interface MTU by the same amount (`MTU = 1420 - S4`, or set `MTU`
explicitly in `[Interface]`) — otherwise every transport packet is fragmented,
which is exactly the fingerprint the `I1`–`I5` MTU check exists to avoid.

The 3.1 mode also talks to AmneziaWG 2.x peers (`S3`, `S4`, `H1`–`H4` ranges, `I1`–`I5`) as long as the 3.x-only parameters are not set.

## Security

This driver parses packets from the network, so it is held to the same rules
as `if_wg.c`: nothing is read before it is bounds-checked, and only
authenticated packets touch peer state.

Read [`SECURITY.md`](SECURITY.md) before deploying — it lists the threat model,
the bounds that configuration values are now held to, the remaining known
limitations, and how to report a problem privately.

Two findings from the first audit are worth knowing about:

- `S1`–`S4` were accepted up to 65535, which made the next handshake panic the
  kernel (`panic: m_clget: request for 65807 byte cluster`). Values are now
  capped at 1280 and rejected with `EINVAL` above that.
- The installer used to rewrite `/etc/pf.conf`; it now only prints the MSS rule
  unless you pass `--pf`.

## Tested

| Component | Version | Result |
|---|---|---|
| OpenBSD | 8.0 amd64 (GENERIC.MP) | builds with `-Werror`, kernel boots, `make check-vm` passes in the lab VM |
| OpenBSD | 7.9 amd64 | `if_awg.c` and `awg_noise.c` compile clean against the 7.9 sources (no kernel build, no runtime test) |
| AmneziaWG legacy | Android/Windows client | **NOT RUN here**; inherited upstream report only |
| AmneziaWG 3.1 | official Go/Linux clients | **NOT RUN here**; source revisions pinned in the engineering report |
| Throughput, 1 vCPU / 1 GB server | TCP/UDP baseline | **NOT RUN here**; inherited figures are not release evidence |

Inherited client/throughput reports above are **not project verification**.
Two OpenBSD 8.0 AWG.MP#5 VMs were re-tested here: 3.1 handshake, ICMP and an
8 MiB transfer whose receiver length and SHA256 were verified. Both currently
have 2 vCPU / 1 GB; this is not the constrained 1-vCPU benchmark profile.
See the engineering report for the exact support/compatibility matrix.

Deploying on a VPS (kernel build, pf policy, tunnel, sysupgrade survival):
[`docs/DEPLOY-vps.md`](docs/DEPLOY-vps.md). `tools/gen-config.sh` writes a
private server/client pair file with fresh keys.

See [`docs/TESTING.md`](docs/TESTING.md) for the matrix, the negative tests that
must not panic, and how to reproduce them. Upgrading the OS replaces `/bsd`;
see [`docs/RUNBOOK-sysupgrade.md`](docs/RUNBOOK-sysupgrade.md).

## Development checks

The repository carries its own gates. `make check` runs anywhere;
the others need the lab VMs described in `tools/lab/README.md`. Destructive lab
targets additionally require `AWG_LAB_DISPOSABLE=1` and pinned SSH host keys.
Optional HP keystream KAT: `OPENBSD_SYS_TREE=/verified/sys make check-tests`.

| Command | What it proves |
| --- | --- |
| `make check` | syntax, Python compile, offline security regressions, the compiled-C integration gate plus its fail-closed sensitivity campaign (`make check-cgate` → `tools/lab/gate-controls.py`, 1 green + 26 negative controls, and `tools/lab/oracle-controls.sh` C1–C24), publish scan, generated patch freshness; signature fixture skips explicitly if signify is absent |
| `make check-vm` | four gates in one VM: `smoke-test` (lifecycle, handshake, malformed input), `wire-test`, `matrix-test` (every parameter at its bound), `stress-test` (200 version switches under traffic) |
| `make check-interop` | two VMs, real link: 3.1 handshake, ICMP through the tunnel, 8 MiB transfer, counters |
| `make baseline TREE=/usr/src/sys` | function-level divergence against a stock OpenBSD tree, checked against the recorded baseline |
| `make manifest KERNEL=…` | release metadata: kernel/patch/tool hashes, versions, test results |

Design and maintenance documents:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — receive and transmit paths, locking, peer state, ioctl contract
- [`docs/UPSTREAM-TRACKING.md`](docs/UPSTREAM-TRACKING.md) — the 8.0 baseline, which functions diverge, how to move the baseline to a new release
- [`docs/SECURITY-AUDIT.md`](docs/SECURITY-AUDIT.md) — findings register: what was found, what is fixed, which test proves it
- [`docs/LOCKING.md`](docs/LOCKING.md) — lock inventory, what a torn obfuscation read can do, the H1–H4 decision
- [`docs/RUNBOOK-security-updates.md`](docs/RUNBOOK-security-updates.md) — what `syspatch` does to a custom kernel and how to rebuild
- [`docs/ADMIN-INTERFACE.md`](docs/ADMIN-INTERFACE.md) — why the `ifconfig` patch stays primary and `awgctl` ships alongside it

## Upstream

This repository is a maintained fork of
[`TrueBad0ur/awg-openbsd-kernel`](https://github.com/TrueBad0ur/awg-openbsd-kernel),
kept in step with OpenBSD releases. Fixes are sent upstream as pull requests;
the fork carries them until they land.

## Performance

No reproducible project-owned throughput baseline is established by this pass.
Previously advertised loopback figures and universal “driver is not the bottleneck”
claims are withdrawn pending logs, exact revisions and repeatable resource profiles.
The 8 MiB nc integrity check is a functional test, not a TCP/UDP benchmark.
Required next measurements: stock wg(4), plain/legacy/complete 3.1 AWG; repeated
TCP/UDP throughput, latency, loss, CPU and pool/mbuf pressure, including 1 vCPU/1 GB.

## awg-quick internals

### No awg/wg tool dependency

The original `wg-quick` for OpenBSD (from amneziawg-tools) calls `wg setconf` to configure the interface and `wg show endpoints` to read back resolved peer endpoint IPs (the `wg` binary from amneziawg-tools is effectively `awg` added to PATH). This project eliminates that dependency entirely: `awg-quick` configures the interface via `ifconfig` and reads resolved endpoint IPs back from `ifconfig awg0` output (the kernel resolves hostnames at configuration time and exposes them as `awgendpoint IP PORT`).

### Peer endpoint host route

When `AllowedIPs` routes cover the peer endpoint IP, a routing loop forms: sending a WireGuard handshake packet to the peer requires going through the tunnel, but the tunnel cannot start without the handshake.

On Linux, WireGuard avoids this by marking its own UDP packets with `fwmark` and routing them through a separate routing table that bypasses `AllowedIPs` entirely. **OpenBSD has no equivalent `fwmark` mechanism.**

The standard `wg-quick` for OpenBSD addresses this only for the `AllowedIPs = 0.0.0.0/0` case (full routing): it adds a `/32` host route for each peer endpoint via the physical gateway before installing the tunnel routes. Because `/32` is more specific than any AllowedIPs prefix, it takes priority in the routing table and the tunnel's UDP packets reach the peer via the physical interface.

This project extends the same mechanism to **all routing configurations** — including split routing. After installing all AllowedIPs routes, `awg-quick` checks each peer endpoint IP with `route get`. If the result shows the tunnel interface, it installs a `/32` host route for that endpoint via the physical gateway. This correctly handles configs that route large public IP ranges through the tunnel while the peer endpoint IP happens to fall in one of those ranges.

## Why not just use the original AmneziaWG driver?

On **FreeBSD**, you can — there is an official `amnezia-kmod` package that installs a pre-built loadable kernel module (`if_amn.ko`), loaded at runtime via `kldload` without any reboot. See [freebsd-amneziawg-setup](https://github.com/HugoFiermein/freebsd-amneziawg-setup) for a ready-made installer.

On **OpenBSD**, this is not possible. OpenBSD does not support loadable kernel modules for network drivers — the kernel is monolithic and all pseudo-devices must be compiled in. The upstream `wg(4)` driver is compiled directly into `GENERIC`, not shipped as a `.ko`. There is no AmneziaWG package for OpenBSD and no shortcut: the only way is to fork `if_wg.c`, extend it with AWG obfuscation, and build a custom kernel. That is exactly what this project does.

This is a native kernel-level AmneziaWG implementation for OpenBSD; no exclusivity claim is made.

## Repo layout

```
src/                              kernel driver files
  if_awg.c                        AWG pseudo-device driver
  if_awg.h                        public ioctl interface
  awg_noise.c, awg_noise.h        Noise protocol (wg_noise copy, configurable timings)
  AWG.MP                          kernel configuration
sbin/
  awg-quick                       tunnel up/down script (uses ifconfig only)
  ifconfig/
    ifconfig_awg.patch            patch for /sbin/ifconfig to add awg* flags
awg-openbsd.patch                 unified diff of all kernel changes
generate-patch.sh                 regenerates awg-openbsd.patch from src/
install.sh                        staged build + install (--check/--build-only/--rollback)
Makefile                          make check, check-vm, check-interop, baseline, manifest
docs/                             architecture, upstream tracking, security register,
                                  locking, testing, runbooks
tools/                            publish scan, upstream diff, sync check, release
                                  manifest, lab VM scripts
.github/workflows/ci.yml          GitHub-hosted checks only; no privileged runner
```
