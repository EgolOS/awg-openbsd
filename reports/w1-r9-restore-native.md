# W1-r9 — the restore's `ifconfig` incantations, verified on real OpenBSD 8.0

Date: 2026-11-09 (Europe/Istanbul). Host: `lab@<lab-host>`, OpenBSD 8.0 `AWG.MP#0`, disposable test box.
Why this file exists: the R4-08a restore is exercised by host fixtures, and a fixture accepts whatever argv
it is given. The commands the restore issues must be valid on the real `ifconfig(8)`, so they were run
natively against a scratch `vether(4)` interface (no tunnel, no traffic, no kernel change).

## What the restore issues

| step | command |
|---|---|
| clear IPv4 | `ifconfig <ifn> -inet` |
| clear IPv6 | `ifconfig <ifn> -inet6` |
| primary of a family | `ifconfig <ifn> inet <addr>/<prefix>` (no `alias`) |
| additional address | `ifconfig <ifn> inet <addr>/<prefix> alias` |
| up/down | `ifconfig <ifn> up` / `ifconfig <ifn> down` (R10: `-up` is not an OpenBSD command) |

## Native transcript

```
$ ifconfig vether0 create
$ ifconfig vether0 inet 10.99.0.1/24  <!-- scan:allow-fixture[sha1:fbe6c467057357c911c0b02316574b270ac599f7,sha1:95779448c013deb29200ac50569229c7ec611b46,sha1:4bf4ce739ee24c597f05060aba35ca130d6c8ce8]: RFC1918 address on a scratch vether(4), not a deployment -->
$ ifconfig vether0 inet 10.99.0.2/24 alias  <!-- scan:allow-fixture[sha1:fbe6c467057357c911c0b02316574b270ac599f7,sha1:95779448c013deb29200ac50569229c7ec611b46,sha1:4bf4ce739ee24c597f05060aba35ca130d6c8ce8]: RFC1918 address on a scratch vether(4), not a deployment -->
$ ifconfig vether0 inet6 fd00:99::1/64
--- before:
	inet 10.99.0.1 netmask 0xffffff00 broadcast 10.99.0.255  <!-- scan:allow-fixture[sha1:fbe6c467057357c911c0b02316574b270ac599f7,sha1:95779448c013deb29200ac50569229c7ec611b46,sha1:4bf4ce739ee24c597f05060aba35ca130d6c8ce8]: RFC1918 address on a scratch vether(4), not a deployment -->
	inet 10.99.0.2 netmask 0xffffff00 broadcast 10.99.0.255  <!-- scan:allow-fixture[sha1:fbe6c467057357c911c0b02316574b270ac599f7,sha1:95779448c013deb29200ac50569229c7ec611b46,sha1:4bf4ce739ee24c597f05060aba35ca130d6c8ce8]: RFC1918 address on a scratch vether(4), not a deployment -->
	inet6 fe80::fce1:baff:fed0:c2a5%vether0 prefixlen 64 tentative scopeid 0xc
	inet6 fd00:99::1 prefixlen 64 tentative
--- clear with -inet:
$ ifconfig vether0 -inet      rc=0
	inet6 fe80::fce1:baff:fed0:c2a5%vether0 prefixlen 64 tentative scopeid 0xc
	inet6 fd00:99::1 prefixlen 64 tentative
--- clear with -inet6:
$ ifconfig vether0 -inet6     rc=0
(no addresses left)
$ ifconfig vether0 destroy
```

## Link-state commands (R10, after the Round-10 review)

The R9 restore emitted `ifconfig <ifn> -up` for a snapshot taken while the interface was down. That command
does not exist: OpenBSD 8.0 `ifconfig(8)` answers `bad value`, so a DOWN interface could not be restored and
came back UP. Both the code and the host fixture are corrected (the fixture now rejects `-up` the same way),
and the native behaviour was measured on the same scratch `vether(4)`:

```
before:  vether0: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> mtu 1500
$ ifconfig vether0 down      rc=0
after:   vether0: flags=8802<BROADCAST,SIMPLEX,MULTICAST> mtu 1500
$ ifconfig vether0 up        rc=0
final:   vether0: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> mtu 1500
$ ifconfig vether0 -up
ifconfig: -up: bad value
rc=1
```

Regression proof (host fixture, real `awgctl.c`): a DOWN pre-state + a `up` that fails on SIOCSAWG now
produces `awg9 down` in the recorded calls and leaves the address list and routing table unchanged; the
pre-fix C produces `awg9 -up` and fails the same test (RED), the fixed C passes (GREEN) —
`tools/tests/test_awgctl_r9_restore.py::test_failed_up_keeps_a_down_interface_down`.

## What this establishes, and what it does not

- `-inet` / `-inet6` **without an address** are accepted by OpenBSD 8.0 `ifconfig(8)` and drop every address
  of that family, primary and aliases alike — so clearing before re-installing the snapshot is a real
  operation, not a fixture artifact.
- `alias` adds an additional address; the primary of each family is installed without it.
- `-alias` is documented as a synonym for `delete` and is discouraged (man ifconfig), so the restore's
  rebuild-from-snapshot approach does not depend on it.
- Not established here: behaviour on a real `awg(4)` interface (this host runs a stock kernel), or the
  link-local IPv6 regeneration the driver does on its own. The restore re-installs whatever the snapshot
  captured, so a driver-regenerated `fe80::` entry shows up as an extra address, not a missing one.
